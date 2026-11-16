#!/usr/bin/env bash
# Daily PostgreSQL + uploads backup of the pilot (cron: 0 2 * * * /srv/exam/deploy/backup.sh).
# Keeps 14 days. Copy /srv/backups off the server too (rclone/rsync): a backup on the same disk is not a backup.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${BACKUP_DIR:-/srv/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
COMPOSE=(docker compose --env-file .env.pilot -f docker-compose.pilot.yml)
mkdir -p "$DEST"
umask 077
"${COMPOSE[@]}" exec -T db pg_dump -U exam -Fc exam > "$DEST/exam-$STAMP.dump"
"${COMPOSE[@]}" exec -T app tar -C /app/data -czf - . > "$DEST/data-$STAMP.tar.gz"
find "$DEST" -type f \( -name 'exam-*.dump' -o -name 'data-*.tar.gz' \) -mtime +14 -delete
echo "backup $STAMP done: $(du -h "$DEST/exam-$STAMP.dump" | cut -f1) database, $(du -h "$DEST/data-$STAMP.tar.gz" | cut -f1) files"
