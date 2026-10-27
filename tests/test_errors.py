"""SES-07 / C-3 / C-4: every connection code, against a fake IPC backend."""

from __future__ import annotations

import time

import pytest

from openkonnect.errors import CONNECTION_CODES, IPC_PREFERENCE, ErrorCode, OpenKonnectError
from openkonnect.session import SessionConfig
from openkonnect.tools.board_summary import board_summary

from .conftest import (
    SOCKET,
    FakeBackend,
    board_ref,
    busy,
    make_session,
    refused,
    timed_out,
    token_mismatch,
)

SLACK_S = 0.5


def run(session):
    started = time.monotonic()
    result = session.run_tool(board_summary)
    elapsed = time.monotonic() - started
    assert elapsed < session.config.timeout_s + SLACK_S, "call exceeded its visible bound"
    return result


def assert_error(result, code: str) -> dict:
    assert result["ok"] is False
    err = result["error"]
    assert set(err) == {"code", "message", "retryable", "details"}
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"].endswith(".")
    assert isinstance(err["retryable"], bool)
    assert isinstance(err["details"], dict)
    assert result["meta"]["timeout_s"] == pytest.approx(result["meta"]["timeout_s"])
    return err


def test_connection_codes_cover_ses07():
    assert {c.value for c in CONNECTION_CODES} == {
        "kicad_not_running",
        "ipc_disabled",
        "ipc_unreachable",
        "kicad_busy",
        "no_document",
    }


def test_kicad_not_running():
    err = assert_error(run(make_session(sockets=[], running=False)), "kicad_not_running")
    assert err["retryable"] is False


def test_kicad_not_running_with_stale_socket():
    s = make_session(FakeBackend(ping=refused()), running=False)
    err = assert_error(run(s), "kicad_not_running")
    assert err["details"]["stale_socket"] == SOCKET


def test_ipc_disabled_names_the_preference():
    err = assert_error(run(make_session(sockets=[], running=True, api_cfg=False)), "ipc_disabled")
    assert err["details"]["preference"] == IPC_PREFERENCE
    assert IPC_PREFERENCE in err["message"]


def test_ipc_disabled_when_socket_cannot_be_listed():
    # Windows: sockets are not enumerable, so the default is tried directly.
    s = make_session(FakeBackend(ping=refused()), sockets=None, running=True, api_cfg=False)
    assert_error(run(s), "ipc_disabled")


def test_ipc_unreachable_socket_does_not_answer():
    s = make_session(FakeBackend(ping=refused()), running=True)
    err = assert_error(run(s), "ipc_unreachable")
    assert err["retryable"] is True
    assert err["details"]["socket"] == SOCKET


def test_not_running_vs_disabled_ambiguity_is_stated_not_guessed():
    err = assert_error(run(make_session(sockets=[], running=None)), "ipc_unreachable")
    assert err["details"]["ambiguous_between"] == ["kicad_not_running", "ipc_disabled"]


def test_api_enabled_but_no_socket_asks_for_restart():
    err = assert_error(run(make_session(sockets=[], running=True, api_cfg=True)), "ipc_unreachable")
    assert "Restart KiCad" in err["message"]


def test_kicad_busy_retries_are_bounded_and_counted():
    backend = FakeBackend(ping=busy())
    sleeps: list[float] = []
    s = make_session(backend, busy_retries=3, sleeps=sleeps)
    result = run(s)
    err = assert_error(result, "kicad_busy")
    assert err["details"]["attempts"] == 4
    assert err["details"]["retries"] == 3
    assert result["meta"]["busy_retries"] == 3
    assert backend.calls.count("ping") == 4
    assert len(sleeps) == 3


def test_kicad_busy_then_success_reports_retries():
    backend = FakeBackend(ping=[busy(), busy(), None], open_boards=[[board_ref()]])
    result = run(make_session(backend, busy_retries=3))
    assert result["ok"] is True
    assert result["meta"]["busy_retries"] == 2


def test_busy_retries_stop_at_the_deadline():
    backend = FakeBackend(ping=busy())
    s = make_session(backend, busy_retries=1000, timeout_s=0.3, busy_backoff_s=0.05)
    s._sleep = time.sleep  # real backoff, so the deadline is what stops it
    result = run(s)
    assert_error(result, "kicad_busy")
    assert result["meta"]["busy_retries"] < 1000


def test_timeout_from_client():
    err = assert_error(run(make_session(FakeBackend(ping=timed_out()))), "timeout")
    assert err["retryable"] is True


def test_timeout_when_kicad_hangs_is_bounded():
    backend = FakeBackend(ping=lambda: time.sleep(3))
    s = make_session(backend, timeout_s=0.3)
    err = assert_error(run(s), "timeout")
    assert err["details"]["timeout_s"] == 0.3
    # A second call while the first is still stuck is bounded too.
    assert_error(run(s), "timeout")


def test_instance_changed_on_token_mismatch():
    s = make_session(FakeBackend(ping=token_mismatch()))
    err = assert_error(run(s), "instance_changed")
    assert err["retryable"] is True


def test_instance_changed_when_another_instance_answers():
    backends = {
        SOCKET: FakeBackend(open_boards=[[board_ref()]]),
        "ipc:///tmp/kicad/api-42.sock": FakeBackend(
            socket="ipc:///tmp/kicad/api-42.sock", open_boards=[[board_ref()]]
        ),
    }
    current = [SOCKET]
    s = make_session(lambda sock, ms: backends[sock], sockets=lambda: list(current))
    assert run(s)["ok"] is True
    current[:] = ["ipc:///tmp/kicad/api-42.sock"]
    err = assert_error(run(s), "instance_changed")
    assert err["details"]["current"]["socket"].endswith("api-42.sock")
    assert run(s)["ok"] is True  # the new instance is adopted after reporting


def test_instance_changed_when_token_changes():
    backend = FakeBackend(open_boards=[[board_ref()]])
    s = make_session(backend)
    assert run(s)["ok"] is True
    backend.token = "tok-2"
    assert_error(run(s), "instance_changed")


def test_no_document():
    err = assert_error(run(make_session(FakeBackend(open_boards=[[]]))), "no_document")
    assert err["details"]["kind"] == "board"


def test_unexpected_exception_is_structured():
    backend = FakeBackend(open_boards=[[board_ref()]], board_data=RuntimeError("boom"))
    err = assert_error(run(make_session(backend)), "internal_error")
    assert err["details"]["exception_message"] == "boom"


def test_error_type_defaults():
    e = OpenKonnectError(ErrorCode.KICAD_BUSY, "Busy.")
    assert e.retryable is True
    assert e.to_dict() == {
        "code": "kicad_busy",
        "message": "Busy.",
        "retryable": True,
        "details": {},
    }


def test_config_validation_and_visible_timeout(monkeypatch):
    monkeypatch.setenv("OPENKONNECT_TIMEOUT", "1.5")
    cfg = SessionConfig.from_env()
    assert cfg.timeout_s == 1.5
    assert cfg.request_timeout_s == 1.5  # clamped to the call bound
    with pytest.raises(ValueError):
        SessionConfig(timeout_s=0)
    result = make_session(sockets=[], running=False, timeout_s=1.5).run_tool(board_summary)
    assert result["meta"]["timeout_s"] == 1.5
