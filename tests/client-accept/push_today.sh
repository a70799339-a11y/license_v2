#!/bin/bash
# 一次性补推脚本：force 触发今天的日报推送并打印路由结果
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
echo "== token: ${TOKEN:0:20}..."
for T in 08:20 08:30 09:00; do
  echo "== push/run time=$T force"
  curl -s -X POST http://127.0.0.1:8000/api/push/run \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"time\":\"$T\",\"force\":true}"
  echo
done
echo "== wecom_last_push 记录 =="
sudo -u postgres psql -d license -t -c "select value from settings where key='wecom_last_push'"
