"""
Thin wrapper around docker-py. Talks only to the Docker Engine API (unix socket,
npipe, or TCP via DOCKER_HOST) - never shells out to the `docker` CLI. This makes
the tool independent of how Docker itself was installed (Docker Desktop, Docker
Engine on Linux, Synology Container Manager, QNAP Container Station, UGREEN
Docker app, ...) as long as the API socket/pipe is reachable.
"""
import docker
from docker.errors import DockerException

_client = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def is_available() -> tuple[bool, str]:
    try:
        get_client().ping()
        return True, ""
    except DockerException as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def reset_client():
    """Used mainly by tests to drop a cached client."""
    global _client
    _client = None
