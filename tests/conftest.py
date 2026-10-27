"""Shared fixtures: a fake IPC backend and the ``live_kicad`` marker.

The fake stands in for :class:`openkonnect.backend.KipyBackend` and raises the
real ``kipy`` exception types with real ``ApiStatusCode`` values, so the
session layer's error mapping is exercised as it would be against KiCad.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import pytest
from kipy.errors import ApiError
from kipy.errors import ConnectionError as KipyConnectionError
from kipy.proto.common import ApiStatusCode

from openkonnect.backend import BoardRef
from openkonnect.session import Session, SessionConfig

SOCKET = "ipc:///tmp/kicad/api.sock"


def busy() -> ApiError:
    return ApiError("KiCad is busy", code=ApiStatusCode.AS_BUSY)


def token_mismatch() -> ApiError:
    return ApiError("token mismatch", code=ApiStatusCode.AS_TOKEN_MISMATCH)


def refused() -> KipyConnectionError:
    return KipyConnectionError("Failed to connect to KiCad: Connection refused")


def timed_out() -> KipyConnectionError:
    return KipyConnectionError("Error receiving reply from KiCad: Timed out")


CANNED_BOARD = {
    "title_block": {"title": "Fixture", "date": "2026-10-27", "revision": "A", "company": ""},
    "copper_layers": 2,
    "enabled_layers": 9,
    "footprints": 3,
    "nets": 4,
    "track_segments": 5,
    "track_arcs": 1,
    "vias": 2,
    "zones": 2,
    "rule_areas": 1,
    "edge_cuts_shapes": 4,
    "outline_nm": {
        "x0_nm": 100_000_000,
        "y0_nm": 50_000_000,
        "x1_nm": 130_000_000,
        "y1_nm": 70_000_000,
    },
}


class FakeBackend:
    """Scriptable fake. ``script`` maps an operation name to a list of steps.

    Each step is a value to return, an exception to raise, or a callable to run.
    The last step repeats once the list is exhausted.
    """

    def __init__(self, socket: str = SOCKET, token: str | None = "tok-1", **script: Any) -> None:
        self.socket = socket
        self.token = token
        self.calls: list[str] = []
        self.closed = False
        self.script: dict[str, list[Any]] = {
            "ping": [None],
            "kicad_version": ["10.0.7"],
            "open_boards": [[]],
            "board_data": [CANNED_BOARD],
        }
        for k, v in script.items():
            self.script[k] = v if isinstance(v, list) else [v]

    def _step(self, name: str, *args: Any) -> Any:
        self.calls.append(name)
        steps = self.script[name]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(*args)
        return step

    def ping(self) -> None:
        self._step("ping")

    def kicad_version(self) -> str:
        return self._step("kicad_version")

    def instance_token(self) -> str | None:
        return self.token

    def open_boards(self) -> list[BoardRef]:
        return self._step("open_boards")

    def board_data(self, ref: BoardRef) -> dict[str, Any]:
        return self._step("board_data", ref)

    def close(self) -> None:
        self.closed = True


def board_ref(project_dir: str = "/proj", name: str = "demo") -> BoardRef:
    return BoardRef(
        path=os.path.join(project_dir, f"{name}.kicad_pcb"),
        file_name=f"{name}.kicad_pcb",
        project_dir=project_dir,
        project_name=name,
    )


def make_session(
    backend: FakeBackend | Callable[[str, int], FakeBackend] | None = None,
    *,
    sockets: list[str] | None | Callable[[], list[str] | None] = (SOCKET,),
    running: bool | None = True,
    api_cfg: bool | None = True,
    sleeps: list[float] | None = None,
    **config: Any,
) -> Session:
    backend = backend if backend is not None else FakeBackend()
    factory = (
        backend
        if callable(backend) and not isinstance(backend, FakeBackend)
        else (lambda sock, ms: backend)
    )

    def finder(_explicit: str | None) -> list[str] | None:
        s = sockets() if callable(sockets) else sockets
        return None if s is None else list(s)

    def sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)
        time.sleep(min(s, 0.001))

    config.setdefault("busy_backoff_s", 0.01)
    return Session(
        SessionConfig(**config),
        backend_factory=factory,
        socket_finder=finder,
        process_probe=lambda: running,
        config_probe=lambda: api_cfg,
        sleep=sleep,
    )


FIXTURE_BOARD = os.path.join(os.path.dirname(__file__), "fixtures", "board.kicad_pcb")


@pytest.fixture
def live_session() -> Session:
    """A real session. Skips unless KiCad 10 is reachable with a board open.

    Used only by ``live_kicad`` tests, which never run in CI (no KiCad there).
    """
    from openkonnect.tools.session_status import session_status

    session = Session(SessionConfig.from_env())
    status = session.run_tool(session_status)
    if not status["ok"] or not status["ipc_reachable"]:
        reason = status.get("reason") or status.get("error") or {}
        pytest.skip(f"live_kicad: KiCad not reachable ({reason.get('code', 'unknown')})")
    return session
