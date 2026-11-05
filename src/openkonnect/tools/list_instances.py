"""SES-02 ``list_instances``: every reachable KiCad instance on this machine.

An empty list is a valid answer, not an error. A socket that exists but does
not answer is listed with ``answered: false`` and an ``ipc_unreachable`` reason.
When the platform cannot list sockets (Windows) the result says so with
``listable: false`` instead of returning an empty list.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

from .. import errors as E
from ..session import Session, socket_pid

DESCRIPTION = (
    "List every KiCad instance whose IPC API socket can be found: endpoint, process id "
    "(when the socket name has one), whether it answered, its KiCad version and open board "
    "path, and why not if it did not answer. An empty list means no sockets were found. "
    "'listable: false' means this platform cannot list sockets. Sockets are probed one "
    "after another, each bounded by the call timeout, so the worst case is the number of "
    "sockets times that timeout."
)


def _probe_one(session: Session, endpoint: str) -> dict[str, Any]:
    """Ping one socket with a throwaway backend, bounded by ``timeout_s``."""
    record: dict[str, Any] = {
        "endpoint": endpoint,
        "pid": socket_pid(endpoint),
        "answered": False,
        "kicad_version": None,
        "board_path": None,
        "reason": None,
    }
    cfg = session.config
    box: dict[str, Any] = {}

    def work() -> None:
        backend = None
        try:
            ms = max(1, int(cfg.request_timeout_s * 1000))
            backend = session._backend_factory(endpoint, ms)
            box["backend"] = backend
            backend.ping()
            box["version"] = backend.kicad_version()
            try:
                boards = backend.open_boards()
                box["board_path"] = boards[0].path if boards else None
            except Exception:  # the board path is best effort
                box["board_path"] = None
            box["ok"] = True
        except Exception as e:
            box["error"] = e
        finally:
            if backend is not None:
                with contextlib.suppress(Exception):
                    backend.close()

    t = threading.Thread(target=work, name="openkonnect-list-instances", daemon=True)
    t.start()
    t.join(cfg.timeout_s)
    if t.is_alive():
        record["reason"] = E.ipc_unreachable(
            "The socket exists but did not answer in time.",
            socket=endpoint,
            timeout_s=cfg.timeout_s,
        ).to_dict()
    elif box.get("ok"):
        record.update(
            answered=True,
            kicad_version=box["version"],
            board_path=box["board_path"],
        )
    else:
        err = box.get("error")
        record["reason"] = E.ipc_unreachable(
            socket=endpoint,
            client_message=str(err) if err else "no answer",
            exception=type(err).__name__ if err else None,
        ).to_dict()
    return record


def list_instances(session: Session, socket: str | None = None) -> dict[str, Any]:
    """Returns a complete result envelope (not wrapped in ``run_tool``).

    ``run_tool`` bounds a whole call by ``timeout_s``; this tool bounds each
    socket by it instead, so it builds its own envelope.
    """
    started = time.monotonic()
    sockets = session._socket_finder(socket or session.config.socket)
    out: dict[str, Any] = {"ok": True}
    if sockets is None:
        out["listable"] = False
        out["instances"] = []
        out["listable_note"] = (
            "This platform cannot list KiCad API sockets. Pass an explicit socket, or use "
            "session_status, which tries the default."
        )
    else:
        out["listable"] = True
        out["instances"] = [_probe_one(session, s) for s in sockets]
    out["meta"] = {
        **session.config.as_meta(),
        "busy_retries": 0,
        "requests": len(out["instances"]),
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }
    return out
