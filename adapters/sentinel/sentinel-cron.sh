#!/bin/bash
# 情绪哨兵 cron 包装：cron 不读 .bashrc，环境变量需在这里显式注入；
# flock 防重入。哨兵自己有条件检查（离开≥10分钟、同批对话只判一次），
# 空跑成本≈0。
#
# 如果你的网络访问模型 API 需要走代理，取消下面几行注释并按需修改：
# export HTTP_PROXY=http://127.0.0.1:7890 HTTPS_PROXY=http://127.0.0.1:7890
# export http_proxy=http://127.0.0.1:7890 https_proxy=http://127.0.0.1:7890
# export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
#
# RR_HOOK_SKIP=1 让本次哨兵调用跳过你自己接的 Claude Code hook（如有）；
# 如果你接了其他 Mind Provider，也可能需要类似的"跳过心跳"环境变量，
# 按你自己的 adapter 文档配置。
export RR_HOOK_SKIP=1
LOG="$HOME/.claude/logs/sentinel.log"
mkdir -p "$(dirname "$LOG")"
exec flock -n /tmp/rr-sentinel.lock \
  python3 "$(cd "$(dirname "$0")" && pwd)/sentinel.py" --compose --push \
  >> "$LOG" 2>&1
