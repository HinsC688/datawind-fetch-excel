#!/bin/bash
# 启动独立的 Chrome（CDP 调试端口 9222），用于抓取 DataWind 网络请求。
# 用法：bash scripts/open-chrome.sh [目标URL]

set -u

PORT=9222
PROFILE_DIR="/tmp/chrome-cdp-profile"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TARGET_URL="${1:-https://datawind.xiaoxiame.com/bi/pages/dashboard/41204?appId=2&sheetId=15473}"

if ! [ -x "$CHROME" ]; then
  echo "❌ 未找到 Google Chrome：$CHROME" >&2
  exit 1
fi

echo "启动 CDP Chrome（端口 $PORT）..."
"$CHROME" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins='*' \
  --user-data-dir="$PROFILE_DIR" \
  --new-window \
  "$TARGET_URL" \
  > /tmp/chrome-cdp.log 2>&1 &

for _ in {1..15}; do
  if curl -fsS "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
    echo "✅ Chrome CDP 已就绪：http://127.0.0.1:$PORT"
    echo "已打开：$TARGET_URL"
    exit 0
  fi
  sleep 1
done

echo "❌ Chrome 未在 15 秒内启动；请查看 /tmp/chrome-cdp.log" >&2
exit 1
