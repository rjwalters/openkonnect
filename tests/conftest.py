"""Shared fixtures: a fake IPC backend and the ``live_kicad`` marker.

The fake stands in for :class:`openkonnect.backend.KipyBackend` and raises the
real ``kipy`` exception types with real ``ApiStatusCode`` values, so the
session layer's error mapping is exercised as it would be against KiCad.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
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


def remove_kct_artifacts(board: Path) -> None:
    """Delete the ``<stem>.route.json`` record ``kct route`` writes beside ``board``.

    Only the route record: KiCad's own ``.kicad_pro``/``.kicad_prl``/``.lck``
    files belong to the open editor and are ignored by ``.gitignore`` instead.
    """
    board.with_suffix(".route.json").unlink(missing_ok=True)


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


# -- a fake PCB editor with a selection (#5) --------------------------------------

SELECTION_BOARD = os.path.join(os.path.dirname(__file__), "fixtures", "selection.kicad_pcb")
MM = 1_000_000


def _box(x0: float, y0: float, x1: float, y1: float) -> dict[str, int]:
    return {
        "x0_nm": round(x0 * MM),
        "y0_nm": round(y0 * MM),
        "x1_nm": round(x1 * MM),
        "y1_nm": round(y1 * MM),
    }


def _pt(x: float, y: float) -> dict[str, int]:
    return {"x": round(x * MM), "y": round(y * MM)}


def selection_board_index() -> dict[str, Any]:
    """What KiCad reports for ``tests/fixtures/selection.kicad_pcb``, in board_index form.

    Written by hand from the fixture file: three footprints of two 1 x 1.2 mm pads,
    nets VIN (R1.1, R2.1, routed by three F.Cu segments), GND (R1.2, R3.2) and SIG
    (R2.2, R3.1, unrouted); Edge.Cuts 100,50 to 140,75 (mm, file frame).
    """
    fps = []
    for n, (ref, x, y, nets) in enumerate(
        [
            ("R1", 110, 60, ("VIN", "GND")),
            ("R2", 120, 60, ("VIN", "SIG")),
            ("R3", 130, 65, ("SIG", "GND")),
        ],
        start=1,
    ):
        pads = []
        for k, (num, dx) in enumerate((("1", -1), ("2", 1))):
            px = x + dx
            pads.append(
                {
                    "id": f"pad-{ref}-{num}",
                    "kind": "pad",
                    "ref": ref,
                    "pad": num,
                    "net": nets[k],
                    "layer": None,
                    "position_nm": _pt(px, y),
                    "bbox_nm": _box(px - 0.5, y - 0.6, px + 0.5, y + 0.6),
                }
            )
        fps.append(
            {
                "id": f"fp-{n}",
                "kind": "footprint",
                "ref": ref,
                "net": None,
                "layer": "F.Cu",
                "position_nm": _pt(x, y),
                "bbox_nm": _box(x - 1.5, y - 0.6, x + 1.5, y + 0.6),
                "pads": pads,
            }
        )
    w = 0.125
    copper = [
        {
            "id": f"trk-{i}",
            "kind": "track",
            "net": "VIN",
            "layer": "F.Cu",
            "bbox_nm": _box(min(a, c) - w, min(b, d) - w, max(a, c) + w, max(b, d) + w),
        }
        for i, (a, b, c, d) in enumerate(
            [(109, 60, 109, 57), (109, 57, 119, 57), (119, 57, 119, 60)], start=1
        )
    ]
    return {
        "footprints": fps,
        "copper": copper,
        "nets": ["VIN", "GND", "SIG"],
        "outline_nm": _box(100, 50, 140, 75),
    }


class EditorFake(FakeBackend):
    """A fake PCB editor holding ``index`` (board_index form) and a selection of IDs."""

    def __init__(
        self,
        index: dict[str, Any] | None = None,
        *,
        selected: list[str] | None = None,
        undescribed: dict[str, int] | None = None,
        text: str | None = None,
        **script: Any,
    ) -> None:
        script.setdefault(
            "open_boards", [[board_ref(os.path.dirname(SELECTION_BOARD), "selection")]]
        )
        super().__init__(**script)
        self.index = index if index is not None else selection_board_index()
        self.selected: list[str] = list(selected or [])
        self.undescribed = dict(undescribed or {})
        self.text = text
        self.set_calls: list[list[str]] = []

    def items_by_id(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for fp in self.index["footprints"]:
            out[fp["id"]] = fp
            for p in fp["pads"]:
                out[p["id"]] = p
        for c in self.index["copper"]:
            out[c["id"]] = c
        return out

    def id_of(self, name: str) -> str:
        """Session ID of a footprint ref or ``REF.PAD`` (test helper)."""
        for item in self.items_by_id().values():
            if item["kind"] == "footprint" and item["ref"] == name:
                return item["id"]
            if item["kind"] == "pad" and f"{item['ref']}.{item['pad']}" == name:
                return item["id"]
        raise KeyError(name)

    def selection(self, ref: BoardRef, kinds: list[str] | None = None) -> dict[str, Any]:
        self.calls.append("selection")
        by_id = self.items_by_id()
        items = [by_id[i] for i in self.selected if i in by_id]
        if kinds:
            items = [i for i in items if i["kind"] in kinds]
        return {
            "items": items,
            "undescribed": {} if kinds else dict(self.undescribed),
            "outline_nm": self.index["outline_nm"],
        }

    def board_index(self, ref: BoardRef) -> dict[str, Any]:
        self.calls.append("board_index")
        return self.index

    def set_selection(self, ref: BoardRef, ids: list[str]) -> None:
        self.calls.append("set_selection")
        self.set_calls.append(list(ids))
        known = self.items_by_id()
        self.selected = [i for i in ids if i in known]

    def board_text(self, ref: BoardRef) -> str:
        self.calls.append("board_text")
        if self.text is None:
            raise ApiError("not implemented", code=ApiStatusCode.AS_UNIMPLEMENTED)
        return self.text
