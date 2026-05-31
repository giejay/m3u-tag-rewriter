#!/bin/bash
set -e

# ── Make all container env vars available to cron jobs ──────────────────────
# cron does not inherit the Docker environment, so we dump it to
# /etc/environment which the cron daemon reads before each job.
printenv | grep -v '^_=' > /etc/environment

# ── Write crontab from CRON_SCHEDULE env var ─────────────────────────────────
SCHEDULE="${CRON_SCHEDULE:-0 1 * * *}"
echo "[entrypoint] Installing crontab with schedule: ${SCHEDULE}"
cat > /etc/cron.d/m3u-modifier <<EOF
# Generated at container startup from CRON_SCHEDULE env var
${SCHEDULE} root /usr/local/bin/python3 /app/m3u_dropbox_modifier.py >> /var/log/cron.log 2>&1
EOF
chmod 0644 /etc/cron.d/m3u-modifier
# /etc/cron.d/ files are picked up by cron automatically – no `crontab` call needed

# ── Warn if no token is present yet ──────────────────────────────────────────
TOKEN_FILE="${DROPBOX_TOKEN_FILE:-/data/dropbox_token.json}"
if [ ! -f "$TOKEN_FILE" ]; then
    echo "============================================================"
    echo "  No Dropbox token found – complete auth before cron runs."
    echo "  Run: docker compose exec m3u-modifier python3 /app/m3u_dropbox_modifier.py"
    echo "============================================================"
fi

echo "[entrypoint] Starting cron daemon …"
cron

echo "[entrypoint] Tailing cron log (docker logs will show cron output) …"
tail -F /var/log/cron.log
