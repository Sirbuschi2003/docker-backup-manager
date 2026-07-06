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
from pathlib import Path
from typing import Callable, Optional

from app.backup_engine import ProgressCallback, _noop_progress, restore_volume_from_file
from app.docker_client import get_client


def _build_create_kwargs(container_json: dict, new_name: Optional[str], image_ref: str) -> dict:
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
            volumes[mount["Name"]] = {"bind": mount["Destination"], "mode": "rw" if mount.get("RW", True) else "ro"}
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
                       on_progress: ProgressCallback = _noop_progress):
    client = get_client()
    backup_dir = Path(backup_dir)

    container_json = json.loads((backup_dir / "container.json").read_text())
    networks_json = {}
    networks_path = backup_dir / "networks.json"
    if networks_path.exists():
        networks_json = json.loads(networks_path.read_text())

    volume_files = sorted((backup_dir / "volumes").glob("*.tar.gz")) if (backup_dir / "volumes").exists() else []
    total_steps = 3 + len(volume_files)
    step = 1

    on_progress(step, "Loading image", total_steps)
    with open(backup_dir / "image.tar", "rb") as f:
        loaded = client.images.load(f.read())
    image_ref = loaded[0].tags[0] if loaded and loaded[0].tags else loaded[0].id

    step += 1
    on_progress(step, "Recreating networks", total_steps)
    existing_networks = {n.name for n in client.networks.list()}
    for net_name, net_attrs in networks_json.items():
        if net_name in existing_networks:
            continue
        driver = net_attrs.get("Driver", "bridge")
        client.networks.create(net_name, driver=driver)

    for vol_file in volume_files:
        vol_name = vol_file.name[: -len(".tar.gz")]
        step += 1
        on_progress(step, f"Restoring volume {vol_name}", total_steps)
        existing_volumes = {v.name for v in client.volumes.list()}
        if vol_name not in existing_volumes:
            client.volumes.create(name=vol_name)
        restore_volume_from_file(vol_name, vol_file)

    step += 1
    on_progress(step, "Creating container", total_steps)
    create_kwargs = _build_create_kwargs(container_json, new_name, image_ref)
    container = client.containers.create(**create_kwargs)

    for net_name in networks_json.keys():
        try:
            client.networks.get(net_name).connect(container)
        except Exception:  # noqa: BLE001
            pass

    if start:
        container.start()

    return container
