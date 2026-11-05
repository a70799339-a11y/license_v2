#!/usr/bin/env bash
# ============================================================
# License 授权管理系统 — Ubuntu 24.04 (WSL) 一键部署脚本
#
# 前置（Windows 侧，只需一次）：
#   wsl --install -d Ubuntu-24.04
#
# 在 WSL 内运行本脚本：
#   bash /mnt/c/songxiankun/projects/license/deploy/wsl-setup.sh
# ============================================================
set -euo pipefail

# 引导：root 且系统无 sudo 时，先直接用 apt 装上 sudo（后续统一 sudo 语义）
if [ "$(id -u)" = "0" ] && ! command -v sudo >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sudo
fi
SUDO="sudo"

APP_DIR=/opt/license
SRC_DIR=${SRC_DIR:-/mnt/c/songxiankun/projects/license}
DB_NAME=license
DB_USER=postgres
DB_PASS=license123
RUN_USER=${SUDO_USER:-$(id -un)}

echo "==> [1/6] 安装系统依赖（PostgreSQL / Python / Nginx）"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq sudo postgresql python3-venv python3-pip nginx rsync locales curl

echo "==> [2/6] 配置 PostgreSQL（本地实例，数据完全自持）"
$SUDO service postgresql start || true
# 统一监听 5432（部分镜像默认 5433）
$SUDO sed -i 's/^port = 5433/port = 5432/' /etc/postgresql/*/main/postgresql.conf || true
$SUDO service postgresql restart || true
if ! $SUDO -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  $SUDO -u postgres createdb "$DB_NAME"
fi
$SUDO -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
# 注册 systemd 自启，保证 WSL 重启后数据库可用
$SUDO systemctl enable --now postgresql || true

echo "==> [3/6] 部署应用代码（后端 + 已构建前端）"
$SUDO mkdir -p "$APP_DIR"
$SUDO rsync -a --exclude node_modules --exclude tools --exclude .git --exclude '.*' \
  "$SRC_DIR/backend/" "$APP_DIR/backend/"
mkdir -p "$APP_DIR/backend/static"
rsync -a "$SRC_DIR/dist/" "$APP_DIR/backend/static/"
$SUDO chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

echo "==> [4/6] 安装 Python 依赖"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q -i https://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com \
  -r "$APP_DIR/backend/requirements.txt"

echo "==> [5/6] 注册 systemd 服务 + Nginx 反向代理"
sed "s/__USER__/$RUN_USER/g" "$SRC_DIR/deploy/license-backend.service" \
  | $SUDO tee /etc/systemd/system/license-backend.service > /dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now license-backend
$SUDO cp "$SRC_DIR/deploy/nginx.conf" /etc/nginx/sites-available/license
$SUDO ln -sf /etc/nginx/sites-available/license /etc/nginx/sites-enabled/license
$SUDO rm -f /etc/nginx/sites-enabled/default
$SUDO nginx -t && $SUDO systemctl restart nginx

echo "==> [6/6] 部署完成，等待服务就绪..."
for i in $(seq 1 15); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/; then break; fi
  sleep 1
done

echo ""
echo "============================================================"
echo " 部署成功！"
echo "   应用地址（Nginx）:  http://localhost"
echo "   直连 uvicorn:       http://localhost:8000"
echo "   登录账号:           admin / admin123"
echo "   数据库:             PostgreSQL 127.0.0.1:5432/license"
echo "   服务管理:           sudo systemctl {start|stop|restart} license-backend"
echo "============================================================"
