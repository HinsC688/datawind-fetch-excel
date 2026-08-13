#!/bin/bash
# 启动带 CDP 调试端口的 Chrome，独立 profile，不影响日常使用的 Chrome 窗口
# 用法: bash scripts/open-chrome.sh

PORT=9222
PROFILE_DIR="/tmp/chrome-cdp-profile"

echo "启动 Chrome (调试端口 $PORT)..."
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=$PORT \
  --remote-allow-origins=* \
  --user-data-dir="$PROFILE_DIR" \
  > /tmp/chrome-cdp.log 2>&1 &

sleep 2

if curl -s "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
  echo "✅ Chrome CDP 已就绪: http://127.0.0.1:$PORT"
  echo "请在这个新打开的 Chrome 窗口中登录 DataWind，并打开目标看板页面。"
else
  echo "❌ 未检测到 CDP 端口，请检查 /tmp/chrome-cdp.log"
fi
