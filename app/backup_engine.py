"""
Core backup logic. Produces a self-contained, portable backup of a single
container (image + volumes + networks + full config) so it can be restored
on a completely different host/OS as long as Docker is available there.

Layout of a container backup version folder:

  <container_name>/<timestamp>/
      meta.json          -> backup metadata (app version, docker version, sizes...)
      container.json      -> full `docker inspect` output for the container
      image.tar            -> `docker save` of the container's image
      networks.json        -> attrs of every custom network the container was attached to
      volumes/<vol>.tar.gz  -> tar of each named volume's data
      binds/<dest>.tar.gz    -> tar of each bind-mounted host directory's data
                                (e.g. a Nextcloud data dir living on a separate
                                disk) - skipped for a denylist of Docker/system
                                internals like the docker.sock (see
                                _should_backup_bind_mount)
"""
from __future__ import annotations

import datetime
import json
import logging
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from app import encryption
from app.config import BACKUPS_DIR, DOCKER_HELPER_IMAGE
from app.docker_client import get_client

logger = logging.getLogger("dbm.backup_engine")

# (target_type, config_json, target_id) - passed down from routers/scheduler,
# which already have DB access to resolve a StorageTarget. When set, volume
# archives are streamed straight to this target instead of ever touching
# local disk (see iter_volume_tar_chunks / stream_volume_to_target below).
StreamTarget = tuple[str, str, int]

ProgressCallback = Callable[[int, str, Optional[int]], None]
ShouldCancel = Callable[[], bool]
# Called with the number of new bytes read/written (not cumulative) so the
# job tracker can derive a live transfer speed - see job_tracker.update_bytes.
BytesCallback = Optional[Callable[[int], None]]


def _noop_progress(step: int, name: str, total: Optional[int] = None) -> None:
    pass


def _never_cancel() -> bool:
    return False


class BackupCancelled(Exception):
    """Raised (cooperatively - nothing preemptively interrupts a thread) when
    should_cancel() reports the user asked to stop. Caught by backup_container
    so it can clean up the partial directory exactly like any other failure,
    but tag the result as cancelled rather than failed."""


def _check_cancel(should_cancel: ShouldCancel, what: str) -> None:
    if should_cancel():
        raise BackupCancelled(f"Backup abgebrochen ({what})")


APP_BACKUP_FORMAT_VERSION = 1


@dataclass
class BackupResult:
    ok: bool
    name: str
    path: Path
    size_bytes: int = 0
    error: Optional[str] = None
    containers: list[str] = field(default_factory=list)
    # For landscape backups: each member container's own BackupResult, so the
    # caller can record them individually (they're real container backups
    # living in their own directory - without a BackupRecord each, they're
    # invisible in the UI, never deletable, and never subject to retention,
    # even though they still consume real disk space).
    member_results: list["BackupResult"] = field(default_factory=list)
    # Set when volumes were streamed directly to a storage target instead of
    # being written locally - restore/delete need to know to fetch/remove them
    # from there instead of expecting a local volumes/*.tar.gz file.
    streamed_target_id: Optional[int] = None
    # Distinguishes a user-requested stop from an actual error - the job
    # tracker shows a neutral "abgebrochen" status instead of a red "failed"
    # one for these.
    cancelled: bool = False


def _timestamp() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def sanitize_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)


# Bind mounts that reference Docker/system internals rather than actual
# application data - archiving these would be meaningless (sockets aren't
# regular files) or pointlessly huge (/proc, /sys, /dev). Everything else a
# container bind-mounts (e.g. a data directory on a separate disk) is
# real data and gets backed up like a named volume.
_SKIP_BIND_MOUNT_SOURCES = {"/var/run/docker.sock", "/proc", "/sys", "/dev"}


def _should_backup_bind_mount(source: str) -> bool:
    if source in _SKIP_BIND_MOUNT_SOURCES:
        return False
    if source.endswith(".sock"):
        return False
    if source.startswith(("/proc/", "/sys/", "/dev/")):
        return False
    return True


def iter_volume_tar_chunks(volume_name: str, should_cancel: ShouldCancel = _never_cancel,
                            on_bytes: BytesCallback = None) -> Iterator[bytes]:
    """Streams a tar.gz of a named Docker volume's contents straight from a
    disposable helper container's stdout - no bind mount involved at all.

    This app talks to the Docker daemon over the host's docker.sock rather
    than running its own nested daemon ("Docker outside of Docker"), so a
    bind-mount path handed to containers.run() is resolved by the daemon
    against the *host* filesystem, not this container's own - a local
    tempfile path would silently resolve to an unrelated, auto-created host
    directory instead of the one this process can see. Reading the archive
    off the container's stdout instead sidesteps that entirely, and as a
    bonus lets the data be streamed directly to a storage target without ever
    touching local disk (see stream_volume_to_target).

    The stream is read via a raw attach socket with the container's logging
    driver disabled (log_config Type "none"), not via container.logs(). Logs
    go through the json-file driver by default, which JSON-escapes every
    byte onto host disk and is then re-read over the HTTP /logs endpoint -
    for a multi-GB binary tar stream that both balloons disk usage (escaped
    binary data runs ~3-5x larger than the source) and collapses throughput
    to roughly 1MB/s.

    The container is created (not run/started) first, then attached to, and
    only started after the attach connection is hijacked - not
    create-and-start-then-attach. With the log driver disabled there is no
    buffered output to fall back on, so attaching after starting races a
    fast-finishing container (a small volume can tar up in well under a
    second): if it has already exited by the time attach() connects, the
    live-only stream has nothing left to deliver and the read blocks
    forever, immune to should_cancel() since it never yields control back to
    the loop below.

    should_cancel() is polled between chunks so even a single huge volume
    (e.g. hundreds of GB) can be interrupted promptly rather than only
    between whole volumes/containers.
    """
    client = get_client()
    container = client.containers.create(
        DOCKER_HELPER_IMAGE,
        command=["tar", "czf", "-", "-C", "/data", "."],
        volumes={volume_name: {"bind": "/data", "mode": "ro"}},
        tty=False,
        log_config={"Type": "none"},
    )
    try:
        stream = client.api.attach(container.id, stdout=True, stderr=False, stream=True, logs=False)
        container.start()
        for chunk in stream:
            if should_cancel():
                raise BackupCancelled(f"Archiving of '{volume_name}' was cancelled")
            if chunk:
                if on_bytes:
                    on_bytes(len(chunk))
                yield chunk
        result = container.wait()
        status = result.get("StatusCode", 0) if isinstance(result, dict) else result
        if status != 0:
            raise RuntimeError(f"Volume archive helper container for '{volume_name}' exited with status {status}")
    finally:
        try:
            container.remove(force=True)
        except Exception:  # noqa: BLE001
            pass


def backup_volume_to_file(volume_name: str, dest_tar_gz: Path, should_cancel: ShouldCancel = _never_cancel,
                           on_bytes: BytesCallback = None) -> None:
    """Tar up a named Docker volume's contents into a local file."""
    dest_tar_gz.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_tar_gz, "wb") as f:
        for chunk in iter_volume_tar_chunks(volume_name, should_cancel=should_cancel, on_bytes=on_bytes):
            f.write(chunk)


def stream_volume_to_target(volume_name: str, target_type: str, config_json: str, relative_path: str,
                             should_cancel: ShouldCancel = _never_cancel, on_bytes: BytesCallback = None) -> None:
    """Tar up a named Docker volume's contents and upload it straight to a
    storage target, without ever writing the archive to local disk. Note:
    this bypasses DBM_ENCRYPTION_KEY at-rest encryption entirely, since that
    only ever applies to files that get written locally first - only use this
    for targets you trust on their own (private LAN NAS, server-side
    encryption, etc.)."""
    from app import storage_sync
    storage_sync.stream_upload_to_target(target_type, config_json, relative_path,
                                          iter_volume_tar_chunks(volume_name, should_cancel=should_cancel,
                                                                  on_bytes=on_bytes))


def restore_volume_from_file(volume_name: str, src_tar_gz: Path) -> None:
    client = get_client()
    src_dir = src_tar_gz.parent.resolve()
    client.containers.run(
        DOCKER_HELPER_IMAGE,
        command=["tar", "xzf", f"/backup/{src_tar_gz.name}", "-C", "/data"],
        volumes={
            volume_name: {"bind": "/data", "mode": "rw"},
            str(src_dir): {"bind": "/backup", "mode": "ro"},
        },
        remove=True,
    )


def backup_container(container_id_or_name: str, dest_root: Path = BACKUPS_DIR,
                      on_progress: ProgressCallback = _noop_progress,
                      stream_target: Optional[StreamTarget] = None,
                      should_cancel: ShouldCancel = _never_cancel,
                      stop_container: bool = False,
                      on_bytes: BytesCallback = None) -> BackupResult:
    client = get_client()
    container = client.containers.get(container_id_or_name)
    attrs = container.attrs
    name = container.name
    ts = _timestamp()
    backup_dir = dest_root / sanitize_name(name) / ts

    volume_mounts = [m for m in attrs.get("Mounts", []) if m.get("Type") == "volume"]
    bind_mounts = [
        m for m in attrs.get("Mounts", [])
        if m.get("Type") == "bind" and _should_backup_bind_mount(m.get("Source", ""))
    ]
    encrypt = encryption.is_enabled()
    # Only actually stop/restart if it's running to begin with - stopping an
    # already-stopped container would just be a no-op that restarts it
    # unexpectedly (the user may have stopped it deliberately).
    should_stop = stop_container and attrs.get("State", {}).get("Status") == "running"
    container_stopped = False
    # inspect+networks, image, finalize, one per volume, one per bind mount, optional encrypt, optional stop+restart
    total_steps = 3 + len(volume_mounts) + len(bind_mounts) + (1 if encrypt else 0) + (2 if should_stop else 0)

    try:
        _check_cancel(should_cancel, "before start")
        backup_dir.mkdir(parents=True, exist_ok=False)

        step = 1
        on_progress(step, f"Reading configuration for {name}", total_steps)
        (backup_dir / "container.json").write_text(json.dumps(attrs, indent=2, default=str))

        network_settings = attrs.get("NetworkSettings", {}).get("Networks", {})
        networks_info = {}
        for net_name in network_settings.keys():
            if net_name in ("bridge", "host", "none"):
                continue
            try:
                net = client.networks.get(net_name)
                networks_info[net_name] = net.attrs
            except Exception:  # noqa: BLE001
                pass
        (backup_dir / "networks.json").write_text(json.dumps(networks_info, indent=2, default=str))

        step += 1
        on_progress(step, f"Saving image for {name}", total_steps)
        image_tag = None
        if attrs.get("Config", {}).get("Image"):
            image_tag = attrs["Config"]["Image"]
        image_tar = backup_dir / "image.tar"
        with open(image_tar, "wb") as f:
            for chunk in container.image.save(named=True):
                _check_cancel(should_cancel, "saving image")
                if on_bytes:
                    on_bytes(len(chunk))
                f.write(chunk)

        if should_stop:
            step += 1
            _check_cancel(should_cancel, f"before stopping {name}")
            on_progress(step, f"Stopping {name} for a consistent backup", total_steps)
            container.stop()
            container_stopped = True

        volumes_dir = backup_dir / "volumes"
        volume_names = []
        for mount in volume_mounts:
            vol_name = mount["Name"]
            step += 1
            _check_cancel(should_cancel, f"before volume {vol_name}")
            volume_names.append(vol_name)
            vol_filename = f"{sanitize_name(vol_name)}.tar.gz"
            if stream_target:
                target_type, target_config_json, _target_id = stream_target
                on_progress(step, f"Streaming volume {vol_name} to storage target", total_steps)
                relative_path = f"{sanitize_name(name)}/{ts}/volumes/{vol_filename}"
                stream_volume_to_target(vol_name, target_type, target_config_json, relative_path,
                                        should_cancel=should_cancel, on_bytes=on_bytes)
            else:
                on_progress(step, f"Archiving volume {vol_name}", total_steps)
                backup_volume_to_file(vol_name, volumes_dir / vol_filename, should_cancel=should_cancel,
                                       on_bytes=on_bytes)

        binds_dir = backup_dir / "binds"
        bind_mounts_meta = []
        for mount in bind_mounts:
            source = mount["Source"]
            destination = mount["Destination"]
            step += 1
            _check_cancel(should_cancel, f"before bind mount {destination}")
            bind_filename = f"{sanitize_name(destination)}.tar.gz"
            bind_mounts_meta.append({
                "source": source, "destination": destination, "filename": bind_filename,
                "rw": mount.get("RW", True),
            })
            if stream_target:
                target_type, target_config_json, _target_id = stream_target
                on_progress(step, f"Streaming bind mount {destination} to storage target", total_steps)
                relative_path = f"{sanitize_name(name)}/{ts}/binds/{bind_filename}"
                stream_volume_to_target(source, target_type, target_config_json, relative_path,
                                        should_cancel=should_cancel, on_bytes=on_bytes)
            else:
                on_progress(step, f"Archiving bind mount {destination}", total_steps)
                backup_volume_to_file(source, binds_dir / bind_filename, should_cancel=should_cancel,
                                       on_bytes=on_bytes)

        if container_stopped:
            step += 1
            on_progress(step, f"Starting {name} again", total_steps)
            container.start()
            container_stopped = False

        step += 1
        on_progress(step, "Finalizing", total_steps)
        meta = {
            "format_version": APP_BACKUP_FORMAT_VERSION,
            "backup_type": "container",
            "container_name": name,
            "image": image_tag,
            "volumes": volume_names,
            "bind_mounts": bind_mounts_meta,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "docker_api_version": client.version().get("ApiVersion"),
            "streamed_target_id": stream_target[2] if stream_target else None,
        }
        (backup_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        if encrypt:
            step += 1
            on_progress(step, "Encrypting backup", total_steps)

            def encrypt_progress(label, idx, total):
                on_progress(step, label, total_steps)

            encryption.encrypt_directory_in_place(backup_dir, on_progress=encrypt_progress)

        size = dir_size_bytes(backup_dir)
        return BackupResult(ok=True, name=name, path=backup_dir, size_bytes=size, containers=[name],
                             streamed_target_id=stream_target[2] if stream_target else None)
    except BackupCancelled as exc:
        shutil.rmtree(backup_dir, ignore_errors=True)
        return BackupResult(ok=False, name=name, path=backup_dir, error=str(exc), cancelled=True)
    except Exception as exc:  # noqa: BLE001
        # Don't leave a half-written backup directory (partial image.tar, etc.) behind -
        # it would be unusable but still count toward disk usage forever, since a
        # failed BackupResult has no size_bytes and isn't retention-eligible either.
        shutil.rmtree(backup_dir, ignore_errors=True)
        return BackupResult(ok=False, name=name, path=backup_dir, error=str(exc))
    finally:
        # No matter how we leave this function (cancelled mid-volume, a volume
        # archive raising, or anything else) - never leave the user's
        # container down because a backup failed partway through.
        if container_stopped:
            try:
                container.start()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to restart container %s after backup", name)


def list_landscape_containers(project_filter: Optional[str] = None,
                               name_contains: Optional[str] = None) -> list:
    client = get_client()
    containers = client.containers.list(all=True)
    if project_filter:
        containers = [
            c for c in containers
            if c.labels.get("com.docker.compose.project") == project_filter
        ]
    if name_contains:
        # For multi-container apps that don't set the Compose project label -
        # e.g. Nextcloud All-in-One creates its sibling containers directly via
        # the Docker API, not docker-compose, so they never show up as a
        # selectable "project" and each gets backed up as its own separate
        # top-level entry otherwise.
        needle = name_contains.lower()
        containers = [c for c in containers if needle in c.name.lower()]
    return containers


def backup_landscape(dest_root: Path = BACKUPS_DIR, project_filter: Optional[str] = None,
                      name_contains: Optional[str] = None,
                      label: Optional[str] = None,
                      on_progress: ProgressCallback = _noop_progress,
                      stream_target: Optional[StreamTarget] = None,
                      should_cancel: ShouldCancel = _never_cancel,
                      stop_containers: bool = False,
                      on_bytes: BytesCallback = None) -> BackupResult:
    containers = list_landscape_containers(project_filter, name_contains)
    ts = _timestamp()
    landscape_name = label or project_filter or name_contains or "landscape"
    landscape_dir = dest_root / "_landscapes" / sanitize_name(landscape_name) / ts
    landscape_dir.mkdir(parents=True, exist_ok=True)

    member_names = []
    member_results = []
    errors = []
    cancelled = False
    total = max(len(containers), 1)
    for idx, c in enumerate(containers, start=1):
        if should_cancel():
            cancelled = True
            break
        on_progress(idx, f"Backing up {c.name} ({idx}/{total})", total)
        result = backup_container(c.name, dest_root, stream_target=stream_target, should_cancel=should_cancel,
                                   stop_container=stop_containers, on_bytes=on_bytes)
        member_names.append(result.name)
        member_results.append(result)
        if result.cancelled:
            cancelled = True
            break
        if not result.ok:
            errors.append(f"{result.name}: {result.error}")
        else:
            (landscape_dir / (result.name + ".json")).write_text(json.dumps({
                "container_name": result.name,
                "backup_path": str(result.path),
            }))

    meta = {
        "format_version": APP_BACKUP_FORMAT_VERSION,
        "backup_type": "landscape",
        "label": landscape_name,
        "members": member_names,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "errors": errors,
    }
    (landscape_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    if not cancelled and encryption.is_enabled():
        encryption.encrypt_directory_in_place(landscape_dir)

    size = dir_size_bytes(landscape_dir)
    ok = len(errors) == 0 and not cancelled
    return BackupResult(
        ok=ok, name=landscape_name, path=landscape_dir, size_bytes=size,
        error="Backup abgebrochen" if cancelled else ("; ".join(errors) if errors else None),
        containers=member_names, member_results=member_results,
        streamed_target_id=stream_target[2] if stream_target else None,
        cancelled=cancelled,
    )


def delete_backup(path: Path) -> None:
    p = Path(path)
    if not p.exists():
        return
    if BACKUPS_DIR.resolve() not in p.resolve().parents and p.resolve() != BACKUPS_DIR.resolve():
        raise ValueError("Refusing to delete path outside of backups directory")
    shutil.rmtree(p)
