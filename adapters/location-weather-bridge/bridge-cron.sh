#!/usr/bin/env bash
# Run the bridge once. Point cron at this, e.g. every 5 minutes:
#   */5 * * * * /path/to/adapters/location-weather-bridge/bridge-cron.sh >> /tmp/rr-bridge.log 2>&1
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# Location/AMap calls should go direct (home broadband), not through any proxy.
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY 2>/dev/null || true
exec python3 "$DIR/bridge.py"
