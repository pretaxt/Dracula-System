#!/usr/bin/env bash
# 确保 nginx vhost 在 location ^~ / { ... } 里有 proxy_cache off;
# 幂等可重入:已存在则跳过。
#
# 用法:
#   bash scripts/ensure-nginx-no-cache.sh [vhost-path]
#
# 默认 vhost 路径: /www/server/panel/vhost/nginx/dracula.bot.conf
#
# 根因:
#   宝塔默认 proxy.conf 在所有 location 全局开启 proxy_cache cache_one,
#   导致 Next.js HTML 被缓存 1h,deploy 后老 chunk hash 仍被发出。
#   修复:在 vhost 反代到 Next.js 的 location 上覆盖关闭。

set -euo pipefail

VHOST="${1:-/www/server/panel/vhost/nginx/dracula.bot.conf}"

if [ ! -f "$VHOST" ]; then
  echo "vhost not found: $VHOST" >&2
  exit 1
fi

if grep -qE '^[[:space:]]*proxy_cache[[:space:]]+off' "$VHOST"; then
  echo "proxy_cache off; already present in $VHOST"
  exit 0
fi

BACKUP="/tmp/$(basename "$VHOST").bak.$(date +%s)"
cp "$VHOST" "$BACKUP"
echo "backed up to $BACKUP"

sed -i '/proxy_pass http:\/\/127\.0\.0\.1:8000;/a\        proxy_cache off;' "$VHOST"

if ! nginx -t 2>&1; then
  echo "nginx syntax check failed; restoring backup"
  cp "$BACKUP" "$VHOST"
  exit 2
fi

nginx -s reload
echo "proxy_cache off; injected into $VHOST and nginx reloaded"
