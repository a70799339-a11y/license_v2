#!/usr/bin/env bash
# T7 CI 流水线同构脚本（Gitee Go / 本地 WSL 共用同一入口，防「流水线绿≠本地绿」）
#
# 步骤：① utcnow 门禁 ② Rust 单测 ③ 临时 postgres（容器优先，否则临时实例）
#      ④ 起 uvicorn + CI 桩模型 ⑤ tests/run_tests.py 黑盒回归（直连后端端口）
# 约束：零 SECRET 进仓——JWT_SECRET 每次临时生成；签名私钥写临时目录随清理销毁；
#      数据库用临时容器/临时实例，绝不触碰本机 dev 库。
#
# 用法：bash ci/run_ci.sh
# 环境变量：SKIP_RUST=1 跳过 Rust 段；CI_FULL_RUST=1 时 Linux 也跑 tauri crate（需 webkit2gtk）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PG_PORT="${CI_PG_PORT:-55432}"
API_PORT="${CI_API_PORT:-18000}"
STUB_PORT="${CI_LLM_STUB_PORT:-11435}"
DB_NAME="license"
PY_BIN="${PY_BIN:-/opt/license/.venv/bin/python3}"
[ -x "$PY_BIN" ] || PY_BIN="$(command -v python3 || echo python3)"

RUNDIR="$(mktemp -d /tmp/license-ci.XXXXXX)"
PGBIN=""
PG_STARTED=0
DOCKER_PG=0
UVICORN_PID=""
STUB_PID=""
KEYS_DIR="$RUNDIR/keys"

pg_as() {
  # initdb 拒绝 root：root 时降权到 postgres 用户；其他用户直跑
  if [ "$(id -u)" = "0" ]; then sudo -u postgres "$@"; else "$@"; fi
}

pg_stop() {
  if [ "$DOCKER_PG" = "1" ]; then
    docker rm -f license-ci-pg >/dev/null 2>&1 || true
  elif [ "$PG_STARTED" = "1" ]; then
    pg_as "$PGBIN/pg_ctl" -D "$RUNDIR/pgdata" -m immediate stop >/dev/null 2>&1 || true
  fi
}

cleanup() {
  set +e
  [ -n "$UVICORN_PID" ] && kill "$UVICORN_PID" 2>/dev/null
  [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
  pg_stop
  rm -rf "$RUNDIR"
}
trap cleanup EXIT

echo "==== [1/5] utcnow 门禁（L4：新增禁用）===="
python3 ci/check_utcnow.py

echo "==== [2/5] Rust 单测 ===="
if [ "${SKIP_RUST:-}" = "1" ]; then
  echo "SKIP_RUST=1，跳过"
elif command -v cargo >/dev/null 2>&1; then
  cargo test --manifest-path client/src-tauri/license-core/Cargo.toml
  if [ "${CI_FULL_RUST:-}" = "1" ]; then
    (cd client/src-tauri && cargo test)
  else
    echo "（tauri crate 测试依赖 webkit2gtk；Windows 侧请跑 client/build.bat alltest，或设 CI_FULL_RUST=1）"
  fi
elif command -v cmd.exe >/dev/null 2>&1; then
  echo "本机无 cargo，经 Windows 跑 build.bat alltest（license-core + main.rs 全量单测）"
  (cd client && cmd.exe /c build.bat alltest)
else
  echo "✗ cargo 与 cmd.exe 均不可用，无法跑 Rust 单测" >&2
  exit 1
fi

echo "==== [3/5] 临时 postgres ===="
if command -v docker >/dev/null 2>&1; then
  DOCKER_PG=1
  docker rm -f license-ci-pg >/dev/null 2>&1 || true
  docker run -d --rm --name license-ci-pg \
    -e POSTGRES_PASSWORD=ci -e POSTGRES_DB="$DB_NAME" \
    -p "127.0.0.1:${PG_PORT}:5432" postgres:16-alpine >/dev/null
  DATABASE_URL="postgresql://postgres:ci@127.0.0.1:${PG_PORT}/${DB_NAME}"
  for _ in $(seq 1 30); do
    docker exec license-ci-pg pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
else
  echo "（无 docker，用 postgres 临时实例代替临时容器）"
  PGBIN="$(pg_config --bindir 2>/dev/null || echo /usr/lib/postgresql/16/bin)"
  if [ "$(id -u)" = "0" ]; then chown postgres "$RUNDIR"; fi
  pg_as "$PGBIN/initdb" -D "$RUNDIR/pgdata" -U postgres --auth=trust >/dev/null
  pg_as "$PGBIN/pg_ctl" -D "$RUNDIR/pgdata" -l "$RUNDIR/pg.log" \
    -o "-p ${PG_PORT} -k ${RUNDIR} -h 127.0.0.1" start >/dev/null
  PG_STARTED=1
  pg_as "$PGBIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U postgres "$DB_NAME"
  DATABASE_URL="postgresql://postgres@127.0.0.1:${PG_PORT}/${DB_NAME}"
fi
echo "临时库就绪: $DATABASE_URL"

echo "==== [4/5] 启动 uvicorn + CI 桩模型 ===="
python3 ci/llm_stub.py --port "$STUB_PORT" &
STUB_PID=$!

export DATABASE_URL
export JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
export LICENSE_KEY_DIR="$KEYS_DIR"
export SEED_DEMO_DATA=1   # 演示数据（HT-0001/h001 等）是套件基线依赖

(cd backend && exec "$PY_BIN" -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$API_PORT" --proxy-headers --forwarded-allow-ips=127.0.0.1) &
UVICORN_PID=$!

for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${API_PORT}/api/client/version" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -sf "http://127.0.0.1:${API_PORT}/api/client/version" >/dev/null \
  || { echo "✗ uvicorn 未能就绪" >&2; exit 1; }

# T18 走真实 HTTP 回路：把本地槽位指向桩模型（provider=ollama → POST /api/chat）
TOKEN="$(curl -s -X POST "http://127.0.0.1:${API_PORT}/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')"
[ -n "$TOKEN" ] && [ "$TOKEN" != "None" ] || { echo "✗ CI 登录失败（种子用户未就绪？）" >&2; exit 1; }
CFG_CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:${API_PORT}/api/ai/config" \
  -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
  -d "{\"active\":\"local\",\"fallback\":false,\"local\":{\"provider\":\"ollama\",\"endpoint\":\"http://127.0.0.1:${STUB_PORT}\",\"model\":\"ci-stub\"},\"cloud\":{\"endpoint\":\"\",\"model\":\"\"}}")"
[ "$CFG_CODE" = "200" ] || { echo "✗ 桩模型配置失败（HTTP ${CFG_CODE}）" >&2; exit 1; }
echo "桩模型已接线: http://127.0.0.1:${STUB_PORT}"

echo "==== [5/5] 后端黑盒回归（直连端口，H1 探针不走 nginx）===="
BASE_URL="http://127.0.0.1:${API_PORT}" TEST_DATABASE_URL="$DATABASE_URL" \
  python3 tests/run_tests.py

echo "==== CI 全绿 ===="
