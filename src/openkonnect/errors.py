"""Structured errors (spec C-3) and the fail-fast connection codes (SES-07).

Every failure a tool reports carries a stable ``code``, a sentence saying what
to do next, a ``retryable`` flag and a ``details`` mapping. Tools return these
as data; they never surface a bare exception to the MCP client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Where the user turns on KiCad's API server. Observed in the KiCad 10.0.7
#: preferences dialog (black-box), where the checkbox sits on the Plugins page.
#: KiCad stores the choice as ``api.enable_server`` in ``kicad_common.json``.
IPC_PREFERENCE = "Preferences > Plugins > Enable KiCad API"


class ErrorCode(str, Enum):
    """Stable error codes from spec C-3 that this server can return."""

    KICAD_NOT_RUNNING = "kicad_not_running"
    IPC_DISABLED = "ipc_disabled"
    IPC_UNREACHABLE = "ipc_unreachable"
    KICAD_BUSY = "kicad_busy"
    TIMEOUT = "timeout"
    INSTANCE_CHANGED = "instance_changed"
    NO_DOCUMENT = "no_document"
    UNSUPPORTED_ON_THIS_KICAD = "unsupported_on_this_kicad"
    INVALID_ARGUMENT = "invalid_argument"
    INTERNAL = "internal_error"


#: The codes SES-07 requires every tool except ``session_status`` to check
#: before doing any work.
CONNECTION_CODES = frozenset(
    {
        ErrorCode.KICAD_NOT_RUNNING,
        ErrorCode.IPC_DISABLED,
        ErrorCode.IPC_UNREACHABLE,
        ErrorCode.KICAD_BUSY,
        ErrorCode.NO_DOCUMENT,
    }
)

_RETRYABLE = {
    ErrorCode.KICAD_NOT_RUNNING: False,
    ErrorCode.IPC_DISABLED: False,
    ErrorCode.IPC_UNREACHABLE: True,
    ErrorCode.KICAD_BUSY: True,
    ErrorCode.TIMEOUT: True,
    ErrorCode.INSTANCE_CHANGED: True,
    ErrorCode.NO_DOCUMENT: False,
    ErrorCode.UNSUPPORTED_ON_THIS_KICAD: False,
    ErrorCode.INVALID_ARGUMENT: False,
    ErrorCode.INTERNAL: False,
}


@dataclass
class OpenKonnectError(Exception):
    """A C-3 structured error. Raised internally, returned to clients as data."""

    code: ErrorCode
    message: str
    retryable: bool | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.retryable is None:
            self.retryable = _RETRYABLE.get(self.code, False)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


def kicad_not_running(**details: Any) -> OpenKonnectError:
    return OpenKonnectError(
        ErrorCode.KICAD_NOT_RUNNING,
        "KiCad is not running. Start KiCad 10 and open the board, then call this tool again.",
        details=details,
    )


def ipc_disabled(**details: Any) -> OpenKonnectError:
    return OpenKonnectError(
        ErrorCode.IPC_DISABLED,
        "KiCad is running but its API server is off. Turn on "
        f"'{IPC_PREFERENCE}' in KiCad, then call this tool again.",
        details={"preference": IPC_PREFERENCE, **details},
    )


def ipc_unreachable(message: str | None = None, **details: Any) -> OpenKonnectError:
    return OpenKonnectError(
        ErrorCode.IPC_UNREACHABLE,
        message
        or "KiCad's API socket exists but did not answer. Check that KiCad is responsive, "
        "then call this tool again.",
        details={"preference": IPC_PREFERENCE, **details},
    )


def kicad_busy(attempts: int, **details: Any) -> OpenKonnectError:
    return OpenKonnectError(
        ErrorCode.KICAD_BUSY,
        f"KiCad stayed busy through {attempts} attempt(s), probably in a modal dialog, an "
        "interactive move or a zone refill. Finish that operation in KiCad, then retry.",
        details={"attempts": attempts, **details},
    )


def timeout(timeout_s: float, **details: Any) -> OpenKonnectError:
    return OpenKonnectError(
        ErrorCode.TIMEOUT,
        f"KiCad did not answer within {timeout_s:g} s. It may be busy; retry, or raise "
        "the timeout with --timeout / OPENKONNECT_TIMEOUT.",
        details={"timeout_s": timeout_s, **details},
    )


def instance_changed(**details: Any) -> OpenKonnectError:
    return OpenKonnectError(
        ErrorCode.INSTANCE_CHANGED,
        "The KiCad instance changed (it restarted, or another instance answered). Call "
        "session_status to see the new instance, then retry.",
        details=details,
    )


def no_document(kind: str = "board", **details: Any) -> OpenKonnectError:
    return OpenKonnectError(
        ErrorCode.NO_DOCUMENT,
        f"No {kind} is open in KiCad. Open the {kind} in KiCad's editor, then call this "
        "tool again.",
        details={"kind": kind, **details},
    )
