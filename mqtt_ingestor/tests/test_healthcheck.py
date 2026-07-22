import os
import time
from pathlib import Path

from mqtt_ingestor.healthcheck import clear_connected, is_healthy, mark_connected


def test_connection_state_file_tracks_broker_connection(tmp_path: Path) -> None:
    state_file = tmp_path / "connected"

    assert is_healthy(str(state_file), 5) is False
    mark_connected(str(state_file))
    assert is_healthy(str(state_file), 5) is True
    clear_connected(str(state_file))
    assert is_healthy(str(state_file), 5) is False


def test_clear_connection_state_is_idempotent(tmp_path: Path) -> None:
    clear_connected(str(tmp_path / "missing"))


def test_stale_connection_state_is_unhealthy(tmp_path: Path) -> None:
    state_file = tmp_path / "stale"
    mark_connected(str(state_file))
    stale = time.time() - 30
    os.utime(state_file, (stale, stale))

    assert is_healthy(str(state_file), 5) is False
