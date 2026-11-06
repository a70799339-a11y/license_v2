#!/bin/bash
# License 服务自监控（cron: */5 * * * *）——状态变化时告警，稳定不刷屏
CONF=/opt/license/monitor/monitor.conf
STATE=/opt/license/monitor/last_state
LOG=/opt/license/monitor/monitor.log
[ -f "$CONF" ] && . "$CONF"

FAILS=()
for s in postgresql nginx license-backend; do
  systemctl is-active --quiet "$s" || FAILS+=("$s 服务")
done
HEALTH=$(curl -fsS -m 8 http://127.0.0.1:8000/api/health 2>/dev/null)
if [ -n "$HEALTH" ]; then
  echo "$HEALTH" | grep -q '"running":true' || FAILS+=("企微长连接")
else
  FAILS+=("后端 API")
fi

CUR=$([ ${#FAILS[@]} -eq 0 ] && echo OK || echo "FAIL:${FAILS[*]}")
LAST=$(cat "$STATE" 2>/dev/null || echo INIT)
if [ "$CUR" != "$LAST" ]; then
  if [ "$CUR" = "OK" ]; then
    MSG="**✅ License 服务已恢复**\n所有组件运行正常。"
  else
    MSG="**🚨 License 服务异常**\n> 异常组件：${FAILS[*]}\n> 时间：$(date '+%F %T')\n> 请尽快处理！"
  fi
  if [ -n "${WEBHOOK_URL:-}" ]; then
    curl -fsS -m 8 -X POST "$WEBHOOK_URL" -H 'Content-Type: application/json' \
      -d "{\"msgtype\":\"markdown\",\"markdown\":{\"content\":\"$MSG\"}}" >/dev/null 2>&1 || true
  fi
  echo "$(date '+%F %T') $CUR" >> "$LOG"
  echo "$CUR" > "$STATE"
fi
