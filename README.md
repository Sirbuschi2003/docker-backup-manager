# Docker Backup Manager

Ein webbasiertes, betriebssystem- und Docker-Installations-unabhängiges Tool zum
Sichern und Wiederherstellen von Docker-Umgebungen — einzelne Container oder die
gesamte Docker-Landschaft, inklusive Images, Volumes, Netzwerken und
Konfiguration, sodass ein Backup auf einem völlig anderen Host/OS
wiederhergestellt werden kann.

Es spricht ausschließlich mit der **Docker Engine API** (über den Docker-Socket),
nie mit der `docker`-CLI. Dadurch läuft es identisch auf Docker Desktop,
Synology Container Manager, QNAP Container Station, UGREEN Docker-App oder
purem Docker Engine auf Linux.

## Features

- **Backup einzelner Container** oder der **gesamten Docker-Landschaft** (alle
  Container oder gefiltert nach Compose-Projekt)
- Backup enthält: Image (`docker save`), alle benannten Volumes, angehängte
  Custom-Netzwerke und die vollständige Container-Konfiguration
- **Wiederherstellung** auf demselben oder einem anderen Host/OS
- **Zeitbasierte Versionierung**: jedes Backup ist eine eigene Zeitstempel-Version,
  nichts wird überschrieben
- **Zeitpläne** (Cron) pro Container oder für die gesamte Landschaft, inkl.
  automatischer **Aufbewahrungsrichtlinie** (Anzahl Versionen und/oder Alter in Tagen)
- **Löschen** einzelner Backup-Versionen
- **Fortschrittsanzeige** (Ladebalken + geschätzte Restzeit) bei laufenden
  Backup-/Restore-Jobs
- **Externe Speicherziele** für Offsite-Kopien: SMB/NFS (als gemounteter Pfad),
  S3-kompatibel (AWS S3, MinIO, Wasabi, ...) nativ, sowie über das mitgelieferte
  `rclone` **Google Drive, OneDrive** und viele weitere Backends
- Modernes, responsives Web-UI (hell/dunkel), Login-geschützt

## Architektur

- Backend: Python/FastAPI, SQLite (Metadaten), APScheduler (Cron-Jobs)
- Docker-Zugriff: `docker-py` gegen die Engine-API (Socket/Named Pipe/TCP)
- Frontend: reines HTML/CSS/JS ohne Build-Schritt (Vite/Node nicht nötig)
- Alles läuft in einem einzigen Container; Backups liegen unter `/data`

## Backup-Format (portabel)

```
<container_name>/<timestamp>/
    meta.json          Metadaten (Format-Version, Docker-Version, ...)
    container.json      vollständiges `docker inspect`
    image.tar             `docker save` des Images
    networks.json         Konfiguration angehängter Custom-Netzwerke
    volumes/<name>.tar.gz  Inhalt jedes benannten Volumes
```

Ein Landschafts-Backup ist einfach eine Sammlung solcher Container-Backups plus
`_landscapes/<label>/<timestamp>/meta.json` als Verknüpfung.

## Installation

Voraussetzung überall: ein laufender Docker-Host mit Zugriff auf den
Docker-Socket (`/var/run/docker.sock`). Das Tool selbst läuft am einfachsten
ebenfalls als Container.

### 1. Ubuntu / Debian / beliebiger Linux-Host mit Docker

```bash
git clone https://github.com/sirbuschi2003/docker-backup-manager.git
cd docker-backup-manager
cp docker-compose.yml docker-compose.override.yml   # optional, für eigene Anpassungen
docker compose up -d --build
```

Danach: `http://<server-ip>:8420` öffnen und Admin-Konto anlegen.

### 2. Synology NAS (DSM 7, Container Manager)

1. Über **Container Manager → Projekt → Erstellen** ein neues Projekt anlegen,
   Pfad wählen (z. B. `/docker/docker-backup-manager`), Repository dieses
   Projekts dorthin klonen oder `docker-compose.yml` + `Dockerfile` + `app/`
   + `requirements.txt` per File Station hochladen.
2. Als Quelle „docker-compose.yml erstellen/importieren" wählen und den
   Inhalt dieser Datei einfügen.
3. Volume-Pfad `./data` zeigt auf einen Ordner innerhalb des Shared Folder
   (z. B. `/volume1/docker/docker-backup-manager/data`).
4. Der Docker-Socket liegt bei Synology unter `/var/run/docker.sock` — das
   Compose-File mountet ihn bereits korrekt.
5. Projekt starten, Port 8420 in der Fritzbox/Router-Firewall bei Bedarf
   freigeben, `http://<nas-ip>:8420` aufrufen.

### 3. QNAP NAS (Container Station)

1. Container Station → **Anwendungen erstellen** → „Docker-Compose YAML
   erstellen" wählen.
2. `docker-compose.yml` Inhalt einfügen; Pfade unter `volumes:` auf einen
   Ordner im QNAP-Freigabeordner anpassen (z. B. `/share/Container/dbm/data`).
3. QNAP exponiert den Docker-Socket automatisch über Container Station —
   der Standardmount `/var/run/docker.sock:/var/run/docker.sock` funktioniert.
4. Erstellen & starten, danach `http://<nas-ip>:8420` öffnen.

### 4. UGREEN NAS (UGOS / Docker-App)

1. In der UGREEN Docker-App **Compose-Projekt** anlegen (Funktion analog zu
   Synology/QNAP, basiert ebenfalls auf Container Manager/Portainer-artigem UI).
2. `docker-compose.yml` einfügen, `./data` auf einen Pfad im UGREEN-Datenpool
   umbiegen.
3. Docker-Socket-Mount beibehalten (Standard bei allen genannten NAS-Systemen).
4. Projekt starten, Port 8420 aufrufen.

### 5. Portainer (Stacks) — funktioniert auf jedem Docker-Host inkl. NAS

Portainer läuft selbst oft auf genau den NAS-Systemen oben (oder auf einem
separaten Docker-Host) und bietet eine eigene Oberfläche für Compose-Stacks.
Zwei Wege, das Tool darüber zu deployen:

**a) Über Git-Repository (empfohlen, ermöglicht spätere „Pull & Redeploy"):**

1. **Stacks → Add stack**.
2. Name vergeben, z. B. `docker-backup-manager`.
3. Build method: **Repository** wählen.
4. Repository-URL: `https://github.com/Sirbuschi2003/docker-backup-manager`
   (bei privatem Repo zusätzlich einen GitHub Personal Access Token unter
   „Authentication" hinterlegen).
5. Compose path: `docker-compose.yml` (Standard).
6. Unter **Environment variables** optional `DBM_SECRET_KEY` setzen.
7. **Deploy the stack** klicken.

**b) Per Copy-Paste (Web-Editor), ohne Repository-Zugriff:**

Der Web-Editor kann keinen lokalen Build-Kontext (`build: .`) hochladen —
dafür wird bei jedem Push auf `master` automatisch ein fertiges Image per
GitHub Actions nach GHCR gebaut (`.github/workflows/docker-publish.yml`),
das hier direkt referenziert werden kann.

1. **Stacks → Add stack**, Build method: **Web editor**.
2. Folgenden Inhalt einfügen (nicht in eine Markdown-Liste eingerückt kopieren,
   sonst können führende Leerzeichen die YAML-Einrückung durcheinanderbringen):

```yaml
services:
  docker-backup-manager:
    image: ghcr.io/sirbuschi2003/docker-backup-manager:latest
    container_name: docker-backup-manager
    restart: unless-stopped
    ports:
      - "8420:8420"
    environment:
      DBM_SECRET_KEY: "please-change-this-to-a-long-random-string"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - dbm_data:/data
volumes:
  dbm_data:
```

3. Da das Repository **privat** ist, ist das gebaute Package auf GHCR
   standardmäßig ebenfalls privat. Entweder:
   - auf GitHub unter **Packages → docker-backup-manager → Package settings →
     Change visibility → Public** stellen, oder
   - in Portainer unter **Registries** eine GHCR-Registry mit einem GitHub
     Personal Access Token (Scope `read:packages`) hinterlegen und beim
     Stack-Deploy diese Registry auswählen.
4. **Deploy the stack** klicken.

Falls du stattdessen selbst bauen möchtest (z. B. eigener Image-Name/Tag):

```bash
docker build -t ghcr.io/<dein-user>/docker-backup-manager:latest .
docker push ghcr.io/<dein-user>/docker-backup-manager:latest
```

In beiden Fällen: Docker-Socket-Mount und persistentes `/data`-Volume nicht
vergessen, sonst gehen Backups/Zeitpläne bei einem Container-Neustart verloren.
Danach `http://<host-ip>:8420` öffnen.

### 6. Windows (Docker Desktop) — z. B. zum Testen

```powershell
git clone https://github.com/sirbuschi2003/docker-backup-manager.git
cd docker-backup-manager
docker compose up -d --build
```

Docker Desktop muss laufen (WSL2-Backend empfohlen). Danach
`http://localhost:8420` öffnen.

### Ohne Docker starten (lokale Entwicklung/Test)

```bash
python -m venv .venv
./.venv/Scripts/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
DBM_BASE_DIR=./data uvicorn app.main:app --host 0.0.0.0 --port 8420
```

Achtung: In diesem Modus muss der Rechner selbst Zugriff auf einen
Docker-Daemon haben (z. B. lokal installierter Docker Engine/Docker Desktop),
da die Backup/Restore-Funktionen die Docker-API benötigen.

## Erste Schritte nach der Installation

1. Beim ersten Aufruf wird ein Admin-Konto angelegt (Benutzername + Passwort,
   min. 8 Zeichen).
2. Unter **Container** siehst du alle laufenden/gestoppten Container des Hosts
   und kannst pro Container ein sofortiges Backup starten, oder mit
   „Gesamte Landschaft sichern" alles auf einmal sichern.
3. Unter **Backups** siehst du alle Versionen je Container/Landschaft,
   kannst wiederherstellen, herunterladen (Dateisystem) oder löschen.
4. Unter **Zeitpläne** legst du Cron-Zeitpläne mit Aufbewahrungsrichtlinie an
   (z. B. täglich 03:00 Uhr, letzte 7 Versionen behalten).
5. Unter **Einstellungen** kannst du zusätzliche Speicherziele für Offsite-Kopien
   konfigurieren:
   - **SMB/NFS**: Freigabe auf dem NAS/Host mounten und den Mount-Pfad im
     Container per `docker-compose.yml`-Volume verfügbar machen (siehe
     auskommentierte Zeile in `docker-compose.yml`), dann diesen Pfad als
     „SMB/NFS-Pfad" Ziel eintragen.
   - **S3**: Bucket, Endpoint (leer lassen für AWS S3), Access/Secret Key
     eintragen.
   - **Google Drive / OneDrive / sonstige**: einmalig per `rclone config`
     (z. B. lokal `rclone config` ausführen, die erzeugte `rclone.conf` als
     Volume `./rclone.conf:/data/rclone.conf:ro` einbinden), danach im UI
     den Remote-Namen + Zielpfad eintragen.

Nach jedem erfolgreichen Backup werden alle aktivierten Speicherziele
automatisch synchronisiert — der Fortschritt erscheint mit im Dashboard.

## Wiederherstellung auf einem anderen System

1. Backup-Ordner (bzw. den entsprechenden Zeitstempel-Unterordner) auf den
   Zielhost übertragen, z. B. per SMB/NFS-Ziel, S3-Download oder `rclone copy`.
2. Docker Backup Manager auf dem Zielhost installieren/starten (siehe oben),
   `DBM_BACKUPS_DIR`/`./data/backups` auf den übertragenen Ordner zeigen lassen
   (oder Backup-Ordner in das bestehende `data/backups`-Verzeichnis kopieren).
3. In der UI unter **Backups** die passende Version auswählen und
   „Wiederherstellen" klicken. Container-Name kann dabei angepasst werden,
   z. B. um Namenskonflikte zu vermeiden.

Hinweis: Die Wiederherstellung deckt die gängigen Container-Einstellungen ab
(Umgebungsvariablen, Ports, Volumes/Binds, Restart-Policy, Netzwerke,
Capabilities, Privileged-Mode). Sehr exotische Host-Konfigurationen (z. B.
komplexe Device-Mappings) müssen ggf. nach der Wiederherstellung manuell
nachjustiert werden.

## Sicherheit

- Zugriff auf die Web-UI ist durchgehend loginpflichtig (Session-Cookie,
  bcrypt-gehashte Passwörter)
- `DBM_SECRET_KEY` in der `docker-compose.yml` **unbedingt** vor dem
  Produktivbetrieb auf einen langen, zufälligen Wert ändern
- Der Container benötigt Zugriff auf den Docker-Socket — das entspricht
  faktisch Root-Rechten auf dem Host. Nur auf vertrauenswürdigen Hosts
  betreiben und die Web-UI nicht ungeschützt ins Internet stellen (ggf.
  hinter einen Reverse-Proxy mit TLS, z. B. Traefik/Caddy/nginx, oder per VPN).

## Entwicklung & Tests

```bash
pip install -r requirements.txt pytest httpx
pytest -q
```

Die Test-Suite deckt die reine Logik ab (Retention-Regeln, Namens-Sanitizing,
Restore-Config-Mapping, Job-Fortschritt, Storage-Sync, sowie einen kompletten
App-Boot-/Login-Smoketest über `TestClient`). Docker-abhängige Funktionen
(Backup/Restore realer Container) benötigen einen laufenden Docker-Daemon und
werden über die App selbst manuell getestet.
