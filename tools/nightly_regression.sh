#!/bin/bash
# 夜间自动回归（cron: 30 1 * * *）——结果摘要推企微 webhook（复用 monitor.conf）
CONF=/opt/license/monitor/monitor.conf
LOG=/opt/license/monitor/regression.log
[ -f "$CONF" ] && . "$CONF"

OUT=$(cd /mnt/c/songxiankun/projects/license && BASE_URL=http://127.0.0.1:8000 python3 tests/run_tests.py 2>&1)
SUMMARY=$(echo "$OUT" | grep -E '测试汇总|✗' | head -8)
PASS_ALL=$(echo "$OUT" | grep -c '115/115 通过\|112/112 通过\|11[0-9]/11[0-9] 通过' || true)
OK=$(echo "$SUMMARY" | grep -q '/.*通过（100.0%）' && echo yes || echo no)

echo "$(date '+%F %T') $SUMMARY" >> "$LOG"

if [ -n "${WEBHOOK_URL:-}" ]; then
  if [ "$OK" = yes ]; then
    MSG="**✅ 夜间回归全部通过**\n> $SUMMARY\n> $(date '+%F %T')"
  else
    MSG="**🚨 夜间回归存在失败**\n> $SUMMARY\n> 请查看 tests/last_results.json"
  fi
  curl -fsS -m 8 -X POST "$WEBHOOK_URL" -H 'Content-Type: application/json' \
    -d "{\"msgtype\":\"markdown\",\"markdown\":{\"content\":\"$MSG\"}}" >/dev/null 2>&1 || true
fi
