#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
usagemon —— 幻日的用量/会话健康监控 CLI（参考 damejan80/tokentab 思路，适配 Hermes）

数据源（只读，不动 .qbot 任何文件）:
  1. .qbot/sessions/session_*.json   会话内容（消息数/模型/时间/上下文近似）
  2. .qbot/logs/agent.log            上下文压缩事件、API 失败（带 token 数）

用法:
  python3 subprojects/usagemon/usagemon.py                # 近 7 天汇总
  python3 subprojects/usagemon/usagemon.py --days 30      # 近 30 天
  python3 subprojects/usagemon/usagemon.py --today        # 今天
  python3 subprojects/usagemon/usagemon.py --json         # 机器可读
  python3 subprojects/usagemon/usagemon.py --health       # 看门狗: 只有 1 小时内有
                                                         # API 失败/压缩才输出, 否则静默
注意: token 全是近似值（字符启发式），用于看趋势/找异常，不做精确账单。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SESSIONS_DIR = os.path.join(ROOT, ".qbot", "sessions")
AGENT_LOG = os.path.join(ROOT, ".qbot", "logs", "agent.log")

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff]")
COMPRESS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \w+ \[[\w.]*\] "
    r"agent\.conversation_compression: context compression (started|done): "
    r"session=(\S+) messages=(\d+)(?:->(\d+))? tokens=~([\d,]+)"
)
FAIL_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ ERROR \[[\w.]*\] root: API call failed "
    r"after \d+ retries\..*?model=(\S+) msgs=(\d+) tokens=~([\d,]+)"
)


def est_tokens(text: str) -> int:
    """字符启发式: CJK 字 ≈0.7 token, 其他 ≈1/4 token。"""
    if not isinstance(text, str):
        return 0
    cjk = len(CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk * 0.7 + other / 4)


def parse_session(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    msgs = d.get("messages") or []
    total = 0
    assistant = 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):  # 多段
            c = " ".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in c)
        total += est_tokens(c or "")
        if m.get("role") == "assistant":
            assistant += 1
    # 累计消耗近似: 上下文线性增长, 每轮重发 → Σ ≈ 终态 * 轮数/2
    cumulative = total * max(assistant, 1) // 2
    return {
        "id": d.get("session_id", os.path.basename(path)),
        "model": d.get("model", "?"),
        "start": d.get("session_start", ""),
        "end": d.get("last_updated", ""),
        "messages": d.get("message_count", len(msgs)),
        "context_est": total,        # 终态上下文近似
        "total_est": cumulative,     # 全会话累计近似
        "size_kb": round(os.path.getsize(path) / 1024, 1),
    }


def parse_agent_log() -> dict:
    compressions, failures = [], []
    if not os.path.exists(AGENT_LOG):
        return {"compressions": compressions, "failures": failures}
    with open(AGENT_LOG, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = COMPRESS_RE.search(line)
            if m:
                compressions.append({
                    "time": m.group(1), "phase": m.group(2), "session": m.group(3),
                    "msgs_before": int(m.group(4)),
                    "msgs_after": int(m.group(5)) if m.group(5) else None,
                    "tokens": int(m.group(6).replace(",", "")),
                })
                continue
            m = FAIL_RE.search(line)
            if m:
                failures.append({
                    "time": m.group(1), "model": m.group(2),
                    "msgs": int(m.group(3)), "tokens": int(m.group(4).replace(",", "")),
                })
    return {"compressions": compressions, "failures": failures}


def fmt_h(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.0f}k"
    return str(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--today", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--health", action="store_true")
    args = ap.parse_args()

    now = datetime.now()
    if args.today:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        since = now - timedelta(days=args.days)
    since_s = since.strftime("%Y-%m-%d %H:%M:%S")

    sessions = []
    if os.path.isdir(SESSIONS_DIR):
        for fn in os.listdir(SESSIONS_DIR):
            if not (fn.startswith("session_") and fn.endswith(".json")):
                continue
            s = parse_session(os.path.join(SESSIONS_DIR, fn))
            if s and (not s["end"] or s["end"] >= since_s[:19]):
                sessions.append(s)
    logdata = parse_agent_log()
    comp = [c for c in logdata["compressions"] if c["time"] >= since_s]
    fails = [f for f in logdata["failures"] if f["time"] >= since_s]

    if args.health:
        # 看门狗: 只看最近 1 小时; 无事则静默退出 0（no_agent cron 可直接用）
        h1 = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        recent_f = [f for f in logdata["failures"] if f["time"] >= h1]
        recent_c = [c for c in logdata["compressions"] if c["time"] >= h1 and c["phase"] == "started"]
        if not recent_f and not recent_c:
            return
        print("[usagemon] 近 1 小时会话健康告警")
        for f in recent_f:
            print(f"  API 失败 {f['time']} model={f['model']} 上下文~{fmt_h(f['tokens'])} tokens")
        for c in recent_c:
            print(f"  上下文压缩 {c['time']} session={c['session'][-8:]} ~{fmt_h(c['tokens'])} tokens")
        return

    total_tok = sum(s["total_est"] for s in sessions)
    peak = max(sessions, key=lambda s: s["context_est"], default=None)
    out = {
        "window": f"近{'' if args.today else args.days}天",
        "since": since_s,
        "sessions": len(sessions),
        "total_tokens_est": total_tok,
        "compressions": len([c for c in comp if c["phase"] == "started"]),
        "api_failures": len(fails),
        "peak_session": (
            {"id": peak["id"][-12:], "context_est": peak["context_est"], "model": peak["model"]}
            if peak else None
        ),
        "failures": fails[:10],
        "top_sessions": sorted(sessions, key=lambda s: -s["total_est"])[:5],
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"usagemon · {out['window']} (自 {since_s})")
    print(f"  会话 {out['sessions']} 个 · 累计 token 约 {fmt_h(total_tok)}（近似值）")
    print(f"  上下文压缩 {out['compressions']} 次 · API 失败 {out['api_failures']} 次")
    if out["peak_session"]:
        p = out["peak_session"]
        print(f"  峰值会话 {p['id']} · 终态上下文 ~{fmt_h(p['context_est'])} · {p['model']}")
    for s in out["top_sessions"]:
        print(f"    {s['id'][-16:]}  msg={s['messages']:>4}  终态~{fmt_h(s['context_est']):>6}  累计~{fmt_h(s['total_est']):>8}  {s['model']}")
    for f in out["failures"][:5]:
        print(f"  ✗ {f['time']} API 失败 model={f['model']} 上下文~{fmt_h(f['tokens'])}")


if __name__ == "__main__":
    main()
