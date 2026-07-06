from app.restore_engine import _build_create_kwargs


def test_build_create_kwargs_basic_fields():
    container_json = {
        "Name": "/my-app",
        "Config": {
            "Cmd": ["python", "app.py"],
            "Entrypoint": None,
            "Env": ["FOO=bar"],
            "Labels": {"com.example": "1"},
            "WorkingDir": "/app",
            "Hostname": "myhost",
            "User": "",
        },
        "HostConfig": {
            "PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "8080"}]},
            "RestartPolicy": {"Name": "unless-stopped"},
            "Privileged": False,
            "CapAdd": None,
            "CapDrop": None,
            "NetworkMode": "bridge",
        },
        "Mounts": [
            {"Type": "volume", "Name": "data-vol", "Destination": "/data", "RW": True},
            {"Type": "bind", "Source": "/host/path", "Destination": "/bind", "RW": True},
        ],
    }
    kwargs = _build_create_kwargs(container_json, new_name=None, image_ref="myimage:latest")

    assert kwargs["image"] == "myimage:latest"
    assert kwargs["name"] == "my-app"
    assert kwargs["command"] == ["python", "app.py"]
    assert kwargs["environment"] == ["FOO=bar"]
    assert kwargs["ports"] == {"8000/tcp": "8080"}
    assert kwargs["volumes"]["data-vol"] == {"bind": "/data", "mode": "rw"}
    assert kwargs["volumes"]["/host/path"] == {"bind": "/bind", "mode": "rw"}
    assert kwargs["restart_policy"] == {"Name": "unless-stopped"}


def test_build_create_kwargs_new_name_overrides():
    container_json = {"Name": "/old-name", "Config": {}, "HostConfig": {}, "Mounts": []}
    kwargs = _build_create_kwargs(container_json, new_name="new-name", image_ref="img")
    assert kwargs["name"] == "new-name"


def test_build_create_kwargs_no_restart_policy_when_empty():
    container_json = {
        "Name": "/x", "Config": {}, "Mounts": [],
        "HostConfig": {"RestartPolicy": {"Name": ""}},
    }
    kwargs = _build_create_kwargs(container_json, None, "img")
    assert "restart_policy" not in kwargs
