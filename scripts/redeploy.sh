#!/usr/bin/env bash
# Dracula 生产端一键 redeploy。
#
# 用法(在生产服务器 root 用户下):
#   bash /opt/dracula/scripts/redeploy.sh           # api + frontend 全量重建
#   bash /opt/dracula/scripts/redeploy.sh frontend  # 仅 frontend
#   bash /opt/dracula/scripts/redeploy.sh api       # 仅 api
#
# 步骤:
#   1) git pull origin main
#   2) docker compose build <服务>
#   3) docker compose up -d
#   4) 确保 nginx 不缓存 / 路由(idempotent)
#   5) flush nginx proxy_cache + reload
#   6) docker compose ps 检查健康度
#
# 关键根因修复:
#   宝塔默认 /www/server/nginx/conf/proxy.conf 全局开启 proxy_cache cache_one,
#   导致 Next.js HTML 老版本被缓存 1h,deploy 后浏览器拿到过期 chunk hash。
#   本脚本会确保 dracula.bot.conf 的 location ^~ / 块里有 proxy_cache off;。

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/dracula}"
VHOST="${VHOST:-/www/server/panel/vhost/nginx/dracula.bot.conf}"
SERVICE="${1:-}"

cd "$REPO_DIR"

echo "[1/6] git pull"
git pull origin main

echo "[2/6] docker compose build ${SERVICE:-api frontend}"
if [ -z "$SERVICE" ]; then
  docker compose build api frontend
else
  docker compose build "$SERVICE"
fi

echo "[3/6] docker compose up -d"
docker compose up -d

echo "[4/6] ensure nginx proxy_cache off in vhost"
bash "$REPO_DIR/scripts/ensure-nginx-no-cache.sh" "$VHOST"

echo "[5/6] flush nginx caches + reload"
find /var/cache/nginx \
     /www/server/nginx/proxy_cache_dir \
     /www/wwwroot/dracula.bot/proxy_cache_dir \
     -type f -delete 2>/dev/null || true
nginx -s reload

echo "[6/6] container status"
docker compose ps

echo
echo "redeploy complete"
