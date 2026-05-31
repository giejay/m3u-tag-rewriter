FROM python:3.12-slim

# Install cron
RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY m3u_dropbox_modifier.py .
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Crontab is written dynamically at startup by docker-entrypoint.sh
# using the CRON_SCHEDULE env var from .env / docker-compose.yml.

# Token and .env are provided at runtime via volume / env vars.
# Cron log goes to stdout so docker logs works.
RUN touch /var/log/cron.log

ENTRYPOINT ["/entrypoint.sh"]
