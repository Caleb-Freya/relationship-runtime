#!/usr/bin/env bash
# Claude Code hook adapter：把每轮对话作为统一 Event POST 给 Runtime。
# 用法（配到 settings.json hooks）: post_event.sh <actor> <type>
# stdin 接收 Claude Code hook 的 JSON（UserPromptSubmit 含 .prompt）。
# token 自动从项目 config.yaml 读取，不写进 settings.json。
set -uo pipefail
[ "${RR_HOOK_SKIP:-}" = "1" ] && exit 0
ACTOR="${1:-user}"
TYPE="${2:-user_message}"
# 事件实时入库（GPT评审#11：节流丢语义——"滚/算了/对不起"一条都不能丢）。
# 分析的节流由哨兵自己做，上报不节流。event_id 幂等由服务端保证。
MIN_INTERVAL="${RR_HOOK_MIN_INTERVAL_SECONDS:-0}"
STAMP_FILE="/tmp/.rr-hook-last-${TYPE}"
if [ "$MIN_INTERVAL" -gt 0 ] 2>/dev/null; then
  now=$(date +%s)
  last=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  if [ $((now - last)) -lt "$MIN_INTERVAL" ]; then
    exit 0
  fi
  echo "$now" > "$STAMP_FILE"
fi
RR_DIR="${RR_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
RR_URL="${RR_URL:-http://127.0.0.1:18200}"
TOKEN="${RR_TOKEN:-$(grep 'token:' "$RR_DIR/config.yaml" | sed 's/.*token: "\(.*\)"/\1/')}"
export HOOK_JSON=$(cat 2>/dev/null || true)
python3 - "$ACTOR" "$TYPE" "$RR_URL" "$TOKEN" <<'PY' >/dev/null 2>&1
import json, os, sys, time, urllib.request
actor, type_, url, token = sys.argv[1:5]
try:
    hook = json.loads(os.environ.get("HOOK_JSON") or "{}")
except json.JSONDecodeError:
    hook = {}
text = (hook.get("prompt") or "")[:300]
ev = {
    "event_id": f"hook-{int(time.time()*1000)}-{type_}",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
    "source": "claude-code", "actor": actor, "type": type_,
    "context_window_id": hook.get("session_id", "claude-code"),
    "payload": {"text": text},
}
req = urllib.request.Request(
    url + "/api/event", data=json.dumps(ev).encode(),
    headers={"Authorization": f"Bearer {token}",
             "Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass  # Runtime 不在线时静默跳过，绝不阻塞对话
PY
exit 0
