"""
Restores a container backup produced by backup_engine.backup_container onto
any Docker host (same machine or a different OS entirely), by:
  1. loading the saved image,
  2. recreating any missing custom networks,
  3. recreating any missing named volumes and restoring their data,
  4. recreating the container from the saved inspect config.

Note: this covers the common subset of container configuration (env, command,
entrypoint, labels, ports, binds/volumes, restart policy, network attachments,
capabilities, privileged mode). Highly exotic configurations may need manual
adjustment after restore.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable, Optional

from app import encryption, restic_engine, storage_sync
from app.backup_engine import ProgressCallback, StreamTarget, _noop_progress, restore_volume_from_file, restore_volume_from_tar, sanitize_name
from app.config import BACKUPS_DIR
from app.docker_client import get_client


def _build_create_kwargs(container_json: dict, new_name: Optional[str], image_ref: str,
                         volume_name_map: Optional[dict] = None) -> dict:
    config = container_json.get("Config", {})
    host_config = container_json.get("HostConfig", {})

    ports = {}
    for cport, bindings in (host_config.get("PortBindings") or {}).items():
        if not bindings:
            ports[cport] = None
            continue
        mapped = []
        for b in bindings:
            host_ip = b.get("HostIp") or ""
            host_port = b.get("HostPort")
            if host_ip:
                mapped.append((host_ip, host_port))
            else:
                mapped.append(host_port)
        ports[cport] = mapped if len(mapped) > 1 else mapped[0]

    volumes = {}
    for mount in container_json.get("Mounts", []):
        if mount.get("Type") == "volume":
            src = (volume_name_map or {}).get(mount["Name"], mount["Name"])
            volumes[src] = {"bind": mount["Destination"], "mode": "rw" if mount.get("RW", True) else "ro"}
        elif mount.get("Type") == "bind":
            volumes[mount["Source"]] = {"bind": mount["Destination"], "mode": "rw" if mount.get("RW", True) else "ro"}

    restart_policy = host_config.get("RestartPolicy")
    if restart_policy and not restart_policy.get("Name"):
        restart_policy = None

    kwargs = dict(
        image=image_ref,
        name=new_name or container_json.get("Name", "").lstrip("/"),
        command=config.get("Cmd"),
        entrypoint=config.get("Entrypoint"),
        environment=config.get("Env") or [],
        labels=config.get("Labels") or {},
        working_dir=config.get("WorkingDir") or None,
        hostname=config.get("Hostname") or None,
        user=config.get("User") or None,
        ports=ports or None,
        volumes=volumes or None,
        restart_policy=restart_policy,
        privileged=host_config.get("Privileged", False),
        cap_add=host_config.get("CapAdd") or None,
        cap_drop=host_config.get("CapDrop") or None,
        detach=True,
    )
    if host_config.get("NetworkMode") not in (None, "default"):
        kwargs["network_mode"] = host_config.get("NetworkMode")

    return {k: v for k, v in kwargs.items() if v is not None}


def restore_container(backup_dir: Path, new_name: Optional[str] = None, start: bool = True,
                       on_progress: ProgressCallback = _noop_progress,
                       stream_target: Optional[StreamTarget] = None,
                       rename_volumes: bool = True,
                       volume_base_dir: Optional[str] = None):
    backup_dir = Path(backup_dir)
    relative_key = storage_sync._relative_key(backup_dir)

    if not backup_dir.exists():
        if not stream_target:
            raise RuntimeError(
                "Dieses Backup existiert lokal nicht (z. B. nach einem Katalog-Import von einem "
                "Speicherziel) und es wurde kein Speicherziel zum Nachladen angegeben - "
                "Wiederherstellung nicht möglich."
            )
        on_progress(0, "Lade Backup vom Speicherziel herunter", 1)
        target_type, target_config_json, _target_id = stream_target
        storage_sync.download_full_backup_from_target(target_type, target_config_json, relative_key, backup_dir)

    if encryption.is_backup_encrypted(backup_dir):
        on_progress(0, "Decrypting backup", 1)
        with encryption.decrypt_directory_to_temp(backup_dir) as tmp_dir:
            return _restore_from_plaintext_dir(Path(tmp_dir), new_name, start, on_progress,
                                                stream_target, relative_key, rename_volumes, volume_base_dir)
    return _restore_from_plaintext_dir(backup_dir, new_name, start, on_progress, stream_target, relative_key,
                                        rename_volumes, volume_base_dir)


def _restore_from_plaintext_dir(backup_dir: Path, new_name: Optional[str], start: bool,
                                 on_progress: ProgressCallback,
                                 stream_target: Optional[StreamTarget], relative_key: str,
                                 rename_volumes: bool = True, volume_base_dir: Optional[str] = None):
    client = get_client()

    container_json = json.loads((backup_dir / "container.json").read_text())
    networks_json = {}
    networks_path = backup_dir / "networks.json"
    if networks_path.exists():
        networks_json = json.loads(networks_path.read_text())

    meta = json.loads((backup_dir / "meta.json").read_text()) if (backup_dir / "meta.json").exists() else {}
    streamed_target_id = meta.get("streamed_target_id")
    bind_mounts_meta = meta.get("bind_mounts", [])

    # Build volume name map: original name → restored name (or host path for custom dir).
    # Used both when restoring data into volumes and when wiring up the container config.
    original_container_name = meta.get("container_name") or container_json.get("Name", "").lstrip("/")
    effective_name = new_name or original_container_name

    def _map_vol(vol_name: str) -> str:
        mapped = vol_name
        if rename_volumes and new_name and original_container_name and vol_name.startswith(original_container_name):
            mapped = effective_name + vol_name[len(original_container_name):]
        if volume_base_dir:
            return str(Path(volume_base_dir) / mapped)
        return mapped

    # Pre-build map from all named volumes so _build_create_kwargs can patch the container config.
    all_named_vols = [m["Name"] for m in container_json.get("Mounts", []) if m.get("Type") == "volume"]
    volume_name_map = {v: _map_vol(v) for v in all_named_vols}

    if streamed_target_id is not None:
        # Volumes/binds were never written locally - each one has to be
        # fetched from the target it was streamed to before it can be restored.
        if not stream_target:
            raise RuntimeError(
                "Dieses Backup wurde direkt zu einem Speicherziel gestreamt, aber das Ziel ist nicht "
                "mehr verfügbar (gelöscht oder deaktiviert) - Wiederherstellung nicht möglich."
            )
        volume_names = meta.get("volumes", [])
        staging_root = BACKUPS_DIR / ".tmp"
        staging_root.mkdir(parents=True, exist_ok=True)
        stage_dir_ctx = tempfile.TemporaryDirectory(dir=staging_root)
        stage_dir = Path(stage_dir_ctx.name)

        if meta.get("backup_engine") == "restic":
            # Restic-Restore: Snapshots per restic dump → unkomprimiertes Tar → Docker-Volume
            target_type, target_config_json, _target_id = stream_target
            target_config = json.loads(target_config_json)
            container_name = meta.get("container_name", "")
            repo_url = meta.get("restic_repo_url", "")
            snapshot_ids: dict = meta.get("restic_snapshot_ids", {})
            password = restic_engine.get_password()
            _, r_env, smb_conf_path = restic_engine.repo_url_and_env(
                target_type, target_config, container_name
            )
            try:
                volume_files = []
                for vol_name in volume_names:
                    sid = snapshot_ids.get(vol_name)
                    if not sid:
                        raise RuntimeError(f"Kein Restic-Snapshot für Volume '{vol_name}' gefunden")
                    dest = stage_dir / f"{sanitize_name(vol_name)}.tar"
                    restic_engine.dump_snapshot_to_file(repo_url, r_env, password, sid, dest)
                    volume_files.append((vol_name, dest, "tar"))
                bind_files_restic = []
                for bind in bind_mounts_meta:
                    bind_key = f"bind_{sanitize_name(bind['destination'])}"
                    sid = snapshot_ids.get(bind_key)
                    if not sid:
                        raise RuntimeError(f"Kein Restic-Snapshot für Bind-Mount '{bind['destination']}' gefunden")
                    dest = stage_dir / f"{sanitize_name(bind['destination'])}.tar"
                    restic_engine.dump_snapshot_to_file(repo_url, r_env, password, sid, dest)
                    bind_files_restic.append((bind["source"], dest))
            finally:
                if smb_conf_path:
                    try:
                        Path(smb_conf_path).unlink(missing_ok=True)
                    except Exception:
                        pass
            # Reuse the common restore path below, marking files as uncompressed tar
            _restic_restore = True
        else:
            # Altes Tar-Stream-Verfahren
            target_type, target_config_json, _target_id = stream_target
            volume_files = []
            for vol_name in volume_names:
                dest = stage_dir / f"{sanitize_name(vol_name)}.tar.gz"
                storage_sync.download_from_target(
                    target_type, target_config_json, f"{relative_key}/volumes/{sanitize_name(vol_name)}.tar.gz", dest,
                )
                volume_files.append(dest)
            bind_files = []
            for bind in bind_mounts_meta:
                dest = stage_dir / bind["filename"]
                storage_sync.download_from_target(
                    target_type, target_config_json, f"{relative_key}/binds/{bind['filename']}", dest,
                )
                bind_files.append((bind["source"], dest))
            _restic_restore = False
    else:
        stage_dir_ctx = None
        _restic_restore = False
        volume_files = sorted((backup_dir / "volumes").glob("*.tar.gz")) if (backup_dir / "volumes").exists() else []
        binds_dir = backup_dir / "binds"
        bind_files = [(bind["source"], binds_dir / bind["filename"]) for bind in bind_mounts_meta
                      if (binds_dir / bind["filename"]).exists()]

    try:
        if _restic_restore:
            all_vol_entries = volume_files        # list of (vol_name, path, "tar")
            all_bind_entries = bind_files_restic  # list of (source, path)
        else:
            all_vol_entries = [(f.name[:-len(".tar.gz")], f, "tar.gz") for f in volume_files]
            all_bind_entries = bind_files

        total_steps = 3 + len(all_vol_entries) + len(all_bind_entries)
        step = 1

        on_progress(step, "Loading image", total_steps)
        local_image_tar = backup_dir / "image.tar"
        if not local_image_tar.exists() and streamed_target_id is not None and stream_target:
            # Image was streamed directly to the storage target — download it first
            target_type, target_config_json, _target_id = stream_target
            dl_dest = stage_dir / "image.tar"
            storage_sync.download_from_target(
                target_type, target_config_json, f"{relative_key}/image.tar", dl_dest,
            )
            local_image_tar = dl_dest
        with open(local_image_tar, "rb") as f:
            loaded = client.images.load(f)
        image_ref = loaded[0].tags[0] if loaded and loaded[0].tags else loaded[0].id

        step += 1
        on_progress(step, "Recreating networks", total_steps)
        existing_networks = {n.name for n in client.networks.list()}
        for net_name, net_attrs in networks_json.items():
            if net_name in existing_networks:
                continue
            driver = net_attrs.get("Driver", "bridge")
            client.networks.create(net_name, driver=driver)

        for vol_name, vol_file, fmt in all_vol_entries:
            step += 1
            mapped = _map_vol(vol_name)
            on_progress(step, f"Restoring volume {mapped}", total_steps)
            if volume_base_dir:
                # Restore into a host directory (bind mount) — Docker creates it automatically
                Path(mapped).mkdir(parents=True, exist_ok=True)
            else:
                existing_volumes = {v.name for v in client.volumes.list()}
                if mapped not in existing_volumes:
                    client.volumes.create(name=mapped)
            if fmt == "tar":
                restore_volume_from_tar(mapped, vol_file)
            else:
                restore_volume_from_file(mapped, vol_file)

        for source, bind_file in all_bind_entries:
            step += 1
            on_progress(step, f"Restoring bind mount {source}", total_steps)
            # Extracting into `source` (a host path, not a Docker volume name)
            # works the same way restore_volume_from_file already bind-mounts
            # a host path for named volumes - Docker auto-creates the host
            # directory if it doesn't exist yet.
            if _restic_restore:
                restore_volume_from_tar(source, bind_file)
            else:
                restore_volume_from_file(source, bind_file)
    finally:
        if stage_dir_ctx is not None:
            stage_dir_ctx.cleanup()

    step += 1
    on_progress(step, "Creating container", total_steps)
    create_kwargs = _build_create_kwargs(container_json, new_name, image_ref, volume_name_map)
    container = client.containers.create(**create_kwargs)

    for net_name in networks_json.keys():
        try:
            client.networks.get(net_name).connect(container)
        except Exception:  # noqa: BLE001
            pass

    if start:
        container.start()

    return container
