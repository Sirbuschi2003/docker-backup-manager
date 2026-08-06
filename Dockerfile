FROM python:3.12-slim

# rclone is bundled so "external storage target" sync (Google Drive, OneDrive,
# SFTP, WebDAV, extra S3-compatible backends, ...) works out of the box.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip bzip2 ca-certificates \
    && ARCH=$(uname -m) \
    && case "$ARCH" in \
         x86_64)  RCLONE_ARCH=amd64  ; RESTIC_ARCH=amd64  ;; \
         aarch64) RCLONE_ARCH=arm64  ; RESTIC_ARCH=arm64  ;; \
         armv7l)  RCLONE_ARCH=arm-v7 ; RESTIC_ARCH=arm    ;; \
         *)       RCLONE_ARCH=amd64  ; RESTIC_ARCH=amd64  ;; \
       esac \
    && curl -fsSL "https://downloads.rclone.org/rclone-current-linux-${RCLONE_ARCH}.zip" -o /tmp/rclone.zip \
    && unzip /tmp/rclone.zip -d /tmp \
    && install -m 755 /tmp/rclone-*-linux-${RCLONE_ARCH}/rclone /usr/local/bin/rclone \
    && rm -rf /tmp/rclone* \
    && RESTIC_VERSION=0.17.3 \
    && curl -fsSL "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${RESTIC_ARCH}.bz2" \
       | bunzip2 > /usr/local/bin/restic \
    && chmod 755 /usr/local/bin/restic \
    && apt-get purge -y unzip bzip2 \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV DBM_BASE_DIR=/data
VOLUME ["/data"]

EXPOSE 8420

CMD ["/docker-entrypoint.sh"]
