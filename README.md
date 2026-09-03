# usagemon

Hermes Agent 的 **用量 / 会话健康监控 CLI**。纯 Python 标准库、零第三方依赖、无常驻进程，按需跑一条命令就行。

它只读 `HERMES_HOME`（默认 `.qbot/`）下的两样东西，**不动任何文件**：

- `.qbot/sessions/session_*.json` —— 会话内容（消息数 / 模型 / 时间 / 上下文近似）
- `.qbot/logs/agent.log` —— 上下文压缩事件、API 调用失败（日志里带 token 数）

## 为什么要做这个

LLM Agent 跑久了，谁也不知道它到底烧了多少 token、上下文是不是快爆了、API 有没有在悄悄失败。
usagemon 把这些**从会话文件和日志里翻出来算个趋势**——token 全是字符启发式近似值（中日韩字符 ≈0.7 token，
其他 ≈1/4 token），用来**看趋势、找异常**，不做精确账单。

## 用法

```bash
python3 usagemon.py              # 近 7 天汇总
python3 usagemon.py --days 30    # 近 30 天
python3 usagemon.py --today      # 今天
python3 usagemon.py --json       # 机器可读（JSON）
python3 usagemon.py --health     # 看门狗模式
```

输出长这样：

```
usagemon · 近7天 (自 2026-08-27 10:20:00)
  会话 12 个 · 累计 token 约 4.2M（近似值）
  上下文压缩 3 次 · API 失败 1 次
  峰值会话 a1b2c3d4  · 终态上下文 ~180k · gpt-x
    sess_20260830_...  msg=  42  终态~  180k  累计~   3.7M  gpt-x
  ✗ 2026-08-30 03:11:09 API 失败 model=gpt-x 上下文~152k tokens
```

## 看门狗模式（`--health`）

给 cron / 定时任务用的**静默看门狗**：只看最近 1 小时，
- 这 1 小时内**没有** API 失败、**没有**上下文压缩 → 直接 `exit 0`、**零输出**；
- 一旦有 → 打印告警。

配合「无输出即安静」的调度（比如 no_agent 类型的 cron），就能做到「没事不打扰、出事才吭声」。

## 配置

`config.yaml` 里可调：

| 键 | 默认 | 说明 |
|---|---|---|
| `data.sessions_dir` | `.qbot/sessions` | 会话目录（相对项目根） |
| `data.agent_log` | `.qbot/logs/agent.log` | Agent 日志路径 |
| `estimation.cjk_token_per_char` | 0.7 | 中日韩字符 → token 系数 |
| `estimation.other_token_per_4char` | 1 | 其他字符 4 个 ≈ 1 token |
| `health.lookback_hours` | 1 | `--health` 回看窗口 |
| `health.silent_when_clean` | true | 无事时零输出 |

> 注意：`usagemon.py` 目前把数据源路径按「项目根的 `.qbot/`」相对定位（见脚本头部 `ROOT`）。
> 把它放进你自己的 Hermes 项目根下、`.qbot/` 与项目根同级即可直接跑。

## 依赖

无。Python 3.8+ 标准库即可。

## 边界 / 已知

- token 是**近似**，别拿去做精确计费。
- `--health` 依赖 `agent.log` 里上下文压缩 / API 失败的固定格式；Hermes 改日志格式这里要跟着调
  （正则 `COMPRESS_RE` / `FAIL_RE` 在脚本头部）。

—— 幻日出品
