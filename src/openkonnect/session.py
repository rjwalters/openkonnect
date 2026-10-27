"""The shared session/connection layer: spec C-3, C-4 and SES-07.

One :class:`Session` per server holds at most one IPC connection to one KiCad
instance (KiCad answers requests on its UI thread and asks clients to keep a
single connection). It provides:

* :meth:`Session.probe`, which classifies the connection state as *data*:
  not running, API disabled, unreachable, or reachable (used by SES-01);
* :meth:`Session.require_reachable`, the SES-07 fail-fast check every other
  tool runs first;
* :meth:`Session.call`, which runs one backend operation with bounded
  ``kicad_busy`` retries and maps client exceptions onto C-3 codes;
* :meth:`Session.run_tool`, which runs a whole tool body on a worker thread
  under a hard wall-clock bound (C-4) and wraps the result in the common
  envelope (``ok``, ``error``, ``meta``).
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from . import errors as E
from .backend import IpcBackend, KipyBackend
from .errors import ErrorCode, OpenKonnectError

T = TypeVar("T")

DEFAULT_TIMEOUT_S = 5.0
DEFAULT_REQUEST_TIMEOUT_S = 2.0
DEFAULT_BUSY_RETRIES = 3
DEFAULT_BUSY_BACKOFF_S = 0.25

#: Executable names of KiCad GUI processes that can host the API server.
KICAD_PROCESS_NAMES = ("kicad", "pcbnew", "eeschema")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be a number, got {raw!r}") from e


@dataclass
class SessionConfig:
    """Connection settings. All of them are reported in every result's ``meta``."""

    #: Hard bound on one tool call, in seconds (C-4).
    timeout_s: float = DEFAULT_TIMEOUT_S
    #: Bound on a single IPC request, in seconds. Never above ``timeout_s``.
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    #: How many times a ``busy`` answer is retried before ``kicad_busy``.
    busy_retries: int = DEFAULT_BUSY_RETRIES
    #: Pause between busy retries, in seconds.
    busy_backoff_s: float = DEFAULT_BUSY_BACKOFF_S
    #: Explicit socket URL (``ipc:///tmp/kicad/api.sock``). None means discover.
    socket: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.busy_retries < 0:
            raise ValueError("busy_retries must be >= 0")
        self.request_timeout_s = min(self.request_timeout_s, self.timeout_s)

    @classmethod
    def from_env(cls, **overrides: Any) -> SessionConfig:
        values: dict[str, Any] = {
            "timeout_s": _env_float("OPENKONNECT_TIMEOUT", DEFAULT_TIMEOUT_S),
            "request_timeout_s": _env_float(
                "OPENKONNECT_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT_S
            ),
            "busy_retries": int(_env_float("OPENKONNECT_BUSY_RETRIES", DEFAULT_BUSY_RETRIES)),
            "socket": os.environ.get("OPENKONNECT_SOCKET") or None,
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def as_meta(self) -> dict[str, Any]:
        return {
            "timeout_s": self.timeout_s,
            "request_timeout_s": self.request_timeout_s,
            "busy_retries_max": self.busy_retries,
        }


# -- discovery helpers (black-box, no IPC) -----------------------------------


def find_sockets(explicit: str | None = None) -> list[str] | None:
    """Candidate KiCad API socket URLs, or None when they cannot be listed.

    KiCad 10 listens on ``api.sock`` in its temp directory, and appends the
    process ID (``api-<pid>.sock``) when that name is taken. On Windows the
    socket cannot be checked for on the filesystem, so None is returned and
    the caller has to try the default.
    """
    if explicit:
        return [explicit]
    env = os.environ.get("KICAD_API_SOCKET")
    if env:
        return [env]
    if platform.system() == "Windows":
        return None
    found: list[str] = []
    home = os.environ.get("HOME")
    dirs = ["/tmp/kicad"]
    if home:
        dirs.insert(0, f"{home}/.var/app/org.kicad.KiCad/cache/tmp/kicad")
    for d in dirs:
        found.extend(sorted(glob.glob(os.path.join(d, "api*.sock"))))
    return [f"ipc://{p}" for p in found]


def default_socket() -> str:
    if platform.system() == "Windows":
        import tempfile

        return f"ipc://{tempfile.gettempdir()}\\kicad\\api.sock"
    return "ipc:///tmp/kicad/api.sock"


def socket_pid(socket: str) -> int | None:
    m = re.search(r"api-(\d+)\.sock$", socket)
    return int(m.group(1)) if m else None


def choose_socket(sockets: list[str]) -> str:
    """Prefer the default ``api.sock``; otherwise the first one found."""
    for s in sockets:
        if s.endswith("/api.sock"):
            return s
    return sockets[0]


def kicad_process_running() -> bool | None:
    """True/False if a KiCad GUI process is (not) running; None if unknown."""
    try:
        if platform.system() == "Windows":
            if not shutil.which("tasklist"):
                return None
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.lower()
            return any(f'"{n}.exe"' in out for n in KICAD_PROCESS_NAMES)
        if not shutil.which("pgrep"):
            return None
        rc = subprocess.run(
            ["pgrep", "-x", "|".join(KICAD_PROCESS_NAMES)],
            capture_output=True,
            timeout=2,
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return None
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def _kicad_config_roots() -> list[str]:
    env = os.environ.get("KICAD_CONFIG_HOME")
    if env:
        return [env]
    system = platform.system()
    home = os.path.expanduser("~")
    if system == "Darwin":
        return [os.path.join(home, "Library", "Preferences", "kicad")]
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        return [os.path.join(appdata, "kicad")] if appdata else []
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [
        os.path.join(xdg, "kicad"),
        os.path.join(home, ".var", "app", "org.kicad.KiCad", "config", "kicad"),
    ]


def api_enabled_in_config() -> bool | None:
    """The saved state of KiCad 10's "Enable KiCad API" preference, if readable.

    Observed black-box: KiCad 10 stores it as ``api.enable_server`` in
    ``<config>/10.0/kicad_common.json``. It is a hint only: the running KiCad
    may not have applied a change yet.
    """
    for root in _kicad_config_roots():
        for vdir in sorted(glob.glob(os.path.join(root, "10.*")), reverse=True):
            path = os.path.join(vdir, "kicad_common.json")
            try:
                with open(path, encoding="utf-8") as f:
                    value = json.load(f).get("api", {}).get("enable_server")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(value, bool):
                return value
    return None


# -- probe result -------------------------------------------------------------


@dataclass
class Probe:
    """The connection state, as data (SES-01 never raises for these)."""

    kicad_running: bool | str  # True, False or "unknown"
    ipc_reachable: bool
    state: str  # "reachable" or an SES-07 connection code
    reason: OpenKonnectError | None = None
    socket: str | None = None
    sockets_found: int = 0
    api_enabled_in_config: bool | None = None


@dataclass
class CallMeta:
    busy_retries: int = 0
    requests: int = 0
    started: float = field(default_factory=time.monotonic)


# -- the session ---------------------------------------------------------------


def _is_timeout(exc: BaseException) -> bool:
    ctx = exc.__context__ or exc.__cause__
    if ctx is not None and type(ctx).__name__ == "Timeout":
        return True
    return "timed out" in str(exc).lower()


class Session:
    """Holds the single IPC connection and enforces C-3/C-4/SES-07."""

    def __init__(
        self,
        config: SessionConfig | None = None,
        *,
        backend_factory: Callable[[str, int], IpcBackend] | None = None,
        socket_finder: Callable[[str | None], list[str] | None] = find_sockets,
        process_probe: Callable[[], bool | None] = kicad_process_running,
        config_probe: Callable[[], bool | None] = api_enabled_in_config,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or SessionConfig()
        self._backend_factory = backend_factory or (
            lambda sock, ms: KipyBackend(sock, timeout_ms=ms)
        )
        self._socket_finder = socket_finder
        self._process_probe = process_probe
        self._config_probe = config_probe
        self._sleep = sleep
        self._backend: IpcBackend | None = None
        self._identity: tuple[str, str | None] | None = None
        self._lock = threading.Lock()
        self._local = threading.local()

    # -- per-call bookkeeping ------------------------------------------------

    @property
    def meta(self) -> CallMeta:
        m = getattr(self._local, "meta", None)
        if m is None:
            m = self._local.meta = CallMeta()
        return m

    def _remaining(self) -> float:
        return self.config.timeout_s - (time.monotonic() - self.meta.started)

    # -- connection ----------------------------------------------------------

    def _connect(self, socket: str) -> IpcBackend:
        if self._backend is not None and self._backend.socket == socket:
            return self._backend
        self.reset()
        ms = max(1, int(self.config.request_timeout_s * 1000))
        self._backend = self._backend_factory(socket, ms)
        return self._backend

    def reset(self) -> None:
        """Drop the connection; the next call reconnects."""
        backend, self._backend = self._backend, None
        if backend is not None:
            with contextlib.suppress(Exception):
                backend.close()

    @property
    def identity(self) -> dict[str, Any] | None:
        if self._identity is None:
            return None
        socket, token = self._identity
        return {"socket": socket, "pid": socket_pid(socket), "token": token}

    def _check_identity(self, backend: IpcBackend) -> None:
        current = (backend.socket, backend.instance_token())
        previous = self._identity
        self._identity = current
        if previous is None:
            return
        if previous[0] != current[0] or (previous[1] and current[1] and previous[1] != current[1]):
            raise E.instance_changed(
                previous={"socket": previous[0], "token": previous[1]},
                current={"socket": current[0], "token": current[1]},
            )

    # -- one operation, bounded retries ----------------------------------------

    def call(self, op: Callable[[IpcBackend], T], *, backend: IpcBackend | None = None) -> T:
        """Run ``op`` on the backend, retrying ``busy`` a bounded number of times."""
        from kipy.errors import ApiError
        from kipy.errors import ConnectionError as KipyConnectionError
        from kipy.proto.common import ApiStatusCode

        backend = backend or self._backend
        if backend is None:
            raise E.ipc_unreachable("Not connected to KiCad. Call session_status first.")
        attempts = 0
        while True:
            attempts += 1
            self.meta.requests += 1
            try:
                return op(backend)
            except ApiError as e:
                if e.code in (ApiStatusCode.AS_BUSY, ApiStatusCode.AS_NOT_READY):
                    pause = self.config.busy_backoff_s
                    if attempts > self.config.busy_retries or self._remaining() <= pause:
                        raise E.kicad_busy(
                            attempts, retries=self.meta.busy_retries, kicad_message=str(e)
                        ) from None
                    self.meta.busy_retries += 1
                    self._sleep(pause)
                    continue
                if e.code == ApiStatusCode.AS_TOKEN_MISMATCH:
                    old = self._identity
                    self._identity = None
                    self.reset()
                    raise E.instance_changed(
                        previous={"socket": old[0], "token": old[1]} if old else None,
                        kicad_message=str(e),
                    ) from None
                if e.code == ApiStatusCode.AS_TIMEOUT:
                    raise E.timeout(self.config.request_timeout_s, kicad_message=str(e)) from None
                if e.code == ApiStatusCode.AS_UNIMPLEMENTED:
                    raise OpenKonnectError(
                        ErrorCode.UNSUPPORTED_ON_THIS_KICAD,
                        "This KiCad does not implement the API request needed here.",
                        details={"kicad_message": str(e)},
                    ) from None
                raise OpenKonnectError(
                    ErrorCode.INTERNAL,
                    "KiCad rejected the request. See details.kicad_message.",
                    details={"kicad_status": int(e.code), "kicad_message": str(e)},
                ) from None
            except KipyConnectionError as e:
                self.reset()
                if _is_timeout(e):
                    raise E.timeout(
                        self.config.request_timeout_s, socket=backend.socket, client_message=str(e)
                    ) from None
                raise E.ipc_unreachable(socket=backend.socket, client_message=str(e)) from None

    # -- classification ----------------------------------------------------------

    def probe(self) -> Probe:
        """Classify the connection. Not-running / disabled / unreachable are data.

        Raises only ``kicad_busy``, ``timeout`` and ``instance_changed``.
        """
        sockets = self._socket_finder(self.config.socket)
        running = self._process_probe()
        running_field: bool | str = "unknown" if running is None else running
        cfg = self._config_probe()

        def down(reason: OpenKonnectError, socket: str | None = None) -> Probe:
            self.reset()
            return Probe(
                kicad_running=running_field,
                ipc_reachable=False,
                state=reason.code.value,
                reason=reason,
                socket=socket,
                sockets_found=len(sockets or []),
                api_enabled_in_config=cfg,
            )

        if sockets is not None and not sockets:
            if running is False:
                return down(E.kicad_not_running(process_found=False, socket_found=False))
            if running is True:
                if cfg is True:
                    return down(
                        E.ipc_unreachable(
                            "KiCad is running and its API preference is saved as on, but no "
                            "API socket exists. Restart KiCad so the setting takes effect.",
                            process_found=True,
                            socket_found=False,
                            api_enabled_in_config=True,
                        )
                    )
                return down(
                    E.ipc_disabled(
                        process_found=True, socket_found=False, api_enabled_in_config=cfg
                    )
                )
            return down(
                E.ipc_unreachable(
                    "No KiCad API socket was found, and openkonnect could not tell whether "
                    "KiCad is not running or is running with its API disabled. Start KiCad "
                    f"and turn on '{E.IPC_PREFERENCE}'.",
                    process_found="unknown",
                    socket_found=False,
                    ambiguous_between=["kicad_not_running", "ipc_disabled"],
                )
            )

        socket = choose_socket(sockets) if sockets else (self.config.socket or default_socket())
        backend = self._connect(socket)
        try:
            self.call(lambda b: b.ping(), backend=backend)
        except OpenKonnectError as err:
            if err.code is not ErrorCode.IPC_UNREACHABLE:
                raise
            if running is False:
                return down(E.kicad_not_running(process_found=False, stale_socket=socket), socket)
            if sockets is None and running is True and cfg is False:
                return down(E.ipc_disabled(process_found=True, api_enabled_in_config=False))
            err.details.setdefault("process_found", running_field)
            return down(err, socket)

        self._check_identity(backend)
        return Probe(
            kicad_running=True,
            ipc_reachable=True,
            state="reachable",
            socket=socket,
            sockets_found=len(sockets) if sockets is not None else 1,
            api_enabled_in_config=cfg,
        )

    def require_reachable(self) -> Probe:
        """SES-07: fail fast with a connection code unless KiCad answers."""
        p = self.probe()
        if not p.ipc_reachable:
            assert p.reason is not None
            raise p.reason
        return p

    # -- tool envelope, hard time bound ------------------------------------------

    def run_tool(self, body: Callable[[Session], dict[str, Any]]) -> dict[str, Any]:
        """Run a tool body under the C-4 bound and wrap it in the result envelope."""
        started = time.monotonic()
        box: dict[str, Any] = {}

        def worker() -> None:
            self._local.meta = CallMeta(started=started)
            got = self._lock.acquire(timeout=max(0.0, self.config.timeout_s))
            if not got:
                box["error"] = E.timeout(
                    self.config.timeout_s, reason="a previous request to KiCad is still pending"
                )
                box["meta"] = self.meta
                return
            try:
                box["data"] = body(self)
            except OpenKonnectError as err:
                box["error"] = err
            except Exception as exc:  # never leak a raw exception to the client
                box["error"] = OpenKonnectError(
                    ErrorCode.INTERNAL,
                    "openkonnect hit an unexpected error. Retry; if it persists, report it "
                    "with these details.",
                    details={"exception": type(exc).__name__, "exception_message": str(exc)},
                )
            finally:
                box["meta"] = self.meta
                self._lock.release()

        t = threading.Thread(target=worker, name="openkonnect-call", daemon=True)
        t.start()
        t.join(self.config.timeout_s)
        if t.is_alive():
            # The worker is stuck in a request. Abandon it and its connection.
            self._backend = None
            box = {"error": E.timeout(self.config.timeout_s, reason="tool call exceeded bound")}

        meta: CallMeta | None = box.get("meta")
        envelope_meta = {
            **self.config.as_meta(),
            "busy_retries": meta.busy_retries if meta else 0,
            "requests": meta.requests if meta else 0,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
        if "error" in box:
            return {"ok": False, "error": box["error"].to_dict(), "meta": envelope_meta}
        return {"ok": True, **box["data"], "meta": envelope_meta}
