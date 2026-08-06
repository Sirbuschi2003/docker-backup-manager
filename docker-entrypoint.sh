#!/bin/sh
set -e

UPDATE_STAGE=/data/.app_update

if [ -d "$UPDATE_STAGE" ]; then
    echo "[DBM] Update gefunden in $UPDATE_STAGE – wird eingespielt …"
    if [ -d "$UPDATE_STAGE/app" ]; then
        cp -r "$UPDATE_STAGE/app/." /app/app/
        echo "[DBM] app/ aktualisiert."
    fi
    if [ -f "$UPDATE_STAGE/requirements.txt" ]; then
        if ! diff -q "$UPDATE_STAGE/requirements.txt" /app/requirements.txt > /dev/null 2>&1; then
            echo "[DBM] Neue Abhängigkeiten erkannt – installiere …"
            pip install --no-cache-dir -r "$UPDATE_STAGE/requirements.txt" -q
        fi
    fi
    rm -rf "$UPDATE_STAGE"
    echo "[DBM] Update abgeschlossen."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8420
