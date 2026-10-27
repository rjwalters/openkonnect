"""The real kicad-python client against local sockets, no KiCad needed.

These check that kipy's own failures map onto the right C-3 codes: a missing
socket is ``ipc_unreachable``/``kicad_not_running``, and a socket that accepts
but never answers is ``timeout`` within the bound.
"""

from __future__ import annotations

import platform
import shutil
import tempfile
import time

import pynng
import pytest

from openkonnect.session import Session, SessionConfig
from openkonnect.tools.board_summary import board_summary

pytestmark = pytest.mark.skipif(platform.system() == "Windows", reason="ipc:// paths are POSIX")


@pytest.fixture
def short_dir():
    # Unix socket paths are limited to ~104 bytes, too short for pytest's tmp_path on macOS.
    d = tempfile.mkdtemp(prefix="okt-", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _session(sock: str, running: bool | None, **cfg) -> Session:
    return Session(
        SessionConfig(socket=sock, **cfg),
        process_probe=lambda: running,
        config_probe=lambda: None,
    )


def test_missing_socket_is_unreachable(short_dir):
    sock = f"ipc://{short_dir}/api.sock"
    r = _session(sock, running=True).run_tool(board_summary)
    assert r["error"]["code"] == "ipc_unreachable"
    r = _session(sock, running=False).run_tool(board_summary)
    assert r["error"]["code"] == "kicad_not_running"


def test_silent_socket_times_out_within_bound(short_dir):
    sock = f"ipc://{short_dir}/api.sock"
    with pynng.Rep0(listen=sock):  # accepts, never replies
        s = _session(sock, running=True, timeout_s=1.0, request_timeout_s=0.3)
        started = time.monotonic()
        r = s.run_tool(board_summary)
        elapsed = time.monotonic() - started
    assert r["ok"] is False
    assert r["error"]["code"] == "timeout"
    assert elapsed < 1.5
