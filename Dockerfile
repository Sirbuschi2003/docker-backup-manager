FROM python:3.12-slim

# rclone is bundled so "external storage target" sync (Google Drive, OneDrive,
# SFTP, WebDAV, extra S3-compatible backends, ...) works out of the box.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip \
    && unzip /tmp/rclone.zip -d /tmp \
    && install -m 755 /tmp/rclone-*-linux-amd64/rclone /usr/local/bin/rclone \
    && rm -rf /tmp/rclone* \
    && apt-get purge -y unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DBM_BASE_DIR=/data
VOLUME ["/data"]

EXPOSE 8420

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8420"]
