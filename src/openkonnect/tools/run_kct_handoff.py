"""SES-06 ``run_kct_handoff``: run one ``kct`` write next to an open KiCad, safely.

The seven steps of the handoff protocol (``docs/handoff-protocol.md``):

1. ``session_status`` for the board;
2. ``save_before_external_write``; stop if it refuses; record the file hash
   and the editor's content hash;
3. run the ``kct`` command on the saved file (allow-listed, never a shell);
4. if the command failed or timed out, stop without reloading;
5. if the file did not change, skip the reload;
6. otherwise ``sync_from_disk`` with both hashes and the handoff's own
   snapshot: the hash of the editor content step 2 saved (or proved equal to
   the file), taken inside this call and never from a caller. The reload goes
   ahead without ``discard_unsaved`` only if the editor, read fresh at step 6,
   still hashes to that snapshot, so nothing changed in KiCad while the
   command ran. An edit made meanwhile is refused with ``document_dirty``,
   alongside the command's result; ``discard_unsaved=True`` overrides it and
   the result says the edit was discarded. Without a snapshot (the editor text
   could not be read, or did not match the saved file) the reload is refused
   with ``dirty_state_unknown`` (issue #15);
7. report.

Each IPC step runs under the normal per-call bound (C-4). The command has its
own ``timeout_s``, and the session lock is not held while it runs.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .. import errors as E
from ..errors import ErrorCode, OpenKonnectError
from ..session import Session, file_state
from .save_before_external_write import (
    open_boards_or_fail,
    parse_documents,
    save_before_external_write,
)
from .sync_from_disk import UNDO_NOTE, sync_from_disk

DESCRIPTION = (
    "Run one allow-listed kicad-tools write command (kct) on the board open in KiCad, "
    "following the handoff protocol: check the session, save the editor "
    "(allow_save=true is needed when dirty state is unknown, as on KiCad 10), run the "
    "command with a timeout, then reload the editor from the file only if the command "
    "succeeded and changed it. An edit made in KiCad while the command ran makes the "
    "reload refuse with document_dirty, and nothing is lost; discard_unsaved=true reloads "
    "anyway and discards that edit. command is an argv list without 'kct', and must "
    "contain the token {board}, which is replaced by the board's path, e.g. "
    '["route", "{board}", "-o", "{board}"]. Board only on KiCad 10.'
)

#: kct write commands the handoff may run, by leading argv tokens. Anything else
#: is ``invalid_argument``. Each of these rewrites a ``.kicad_pcb``.
KCT_ALLOW_LIST: tuple[tuple[str, ...], ...] = (
    ("route",),
    ("route-auto",),
    ("optimize-traces",),
    ("optimize-placement",),
    ("stitch",),
    ("zones", "add"),
    ("zones", "batch"),
    ("zones", "fill"),
    ("fix-vias",),
    ("fix-silkscreen",),
    ("fix-footprints",),
    ("fix-drc",),
    ("repair-clearance",),
    ("place-silk-refs",),
)

BOARD_TOKEN = "{board}"
DEFAULT_COMMAND_TIMEOUT_S = 300.0
MAX_COMMAND_TIMEOUT_S = 3600.0
_OUTPUT_TAIL = 4000


@dataclass
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


Runner = Callable[[list[str], float, "str | None"], CommandResult]


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def default_runner(argv: list[str], timeout_s: float, cwd: str | None) -> CommandResult:
    """Run ``argv`` without a shell; on timeout, kill it and its children."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        start_new_session=os.name != "nt",
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, err = proc.communicate()
        return CommandResult(None, out or "", err or "", timed_out=True)
    return CommandResult(proc.returncode, out or "", err or "")


def find_kct() -> str | None:
    found = shutil.which("kct")
    if found:
        return found
    beside = os.path.join(os.path.dirname(sys.executable), "kct")
    return beside if os.path.isfile(beside) and os.access(beside, os.X_OK) else None


def validate_command(command: Any) -> list[str]:
    """Check ``command`` against the allow-list. Returns argv without ``kct``."""
    if not isinstance(command, (list, tuple)) or not all(isinstance(a, str) for a in command):
        raise E.invalid_argument(
            "command must be a list of strings (an argv list), not a shell string.",
            got=command,
        )
    argv = list(command)
    if argv and argv[0] in ("kct", "kicad-tools"):
        argv = argv[1:]
    if not argv:
        raise E.invalid_argument("command is empty.", allowed=_allowed())
    if any("\x00" in a for a in argv):
        raise E.invalid_argument("command arguments must not contain NUL bytes.")
    if not any(tuple(argv[: len(p)]) == p for p in KCT_ALLOW_LIST):
        raise E.invalid_argument(
            f"'{' '.join(argv[:2])}' is not an allow-listed kct write command.",
            allowed=_allowed(),
        )
    if not any(BOARD_TOKEN in a for a in argv):
        raise E.invalid_argument(
            f"command must contain the token {BOARD_TOKEN}, which openkonnect replaces with "
            "the open board's path, so the command writes the file KiCad has open.",
            got=argv,
        )
    _check_output_flags(argv)
    return argv


#: Flags that make kct write somewhere other than the open board (issue #59):
#: ``--auto-layers`` can write ``<stem>_4layer.kicad_pcb`` and still exit 0, and
#: ``--checkpoint`` writes extra state files. Refused outright.
REDIRECT_FLAGS: tuple[str, ...] = ("--auto-layers", "--checkpoint")
#: Real kct flags that are also prefixes of a refused flag, so they are not
#: mistaken for argparse abbreviations of it.
_EXACT_FLAGS = frozenset({"--auto", "--check"})
_OUTPUT_LONG = "--output"
_OUTPUT_SHORT = "-o"


def _abbreviates(opt: str, flag: str) -> bool:
    """True if ``opt`` is ``flag`` or an argparse-style abbreviation of it."""
    return len(opt) > 2 and flag.startswith(opt) and opt not in _EXACT_FLAGS


def _check_output_flags(argv: list[str]) -> None:
    """Refuse flags that send kct's output anywhere but ``{board}`` (issue #59).

    ``-o``/``--output`` is allowed only with the value exactly ``{board}``, in any
    spelling (``-o {board}``, ``-o{board}``, ``--output {board}``,
    ``--output={board}``, or an abbreviation of ``--output``).
    """
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            return
        if arg.startswith("--"):
            opt, eq, inline = arg.partition("=")
            for flag in REDIRECT_FLAGS:
                if _abbreviates(opt, flag):
                    raise E.invalid_argument(
                        f"{flag} makes kct write somewhere other than the open board, "
                        "so the handoff would report success while KiCad's board is "
                        "unchanged. It is not allowed in run_kct_handoff.",
                        got=arg,
                    )
            if _abbreviates(opt, _OUTPUT_LONG):
                value = inline if eq else (argv[i + 1] if i + 1 < len(argv) else None)
                _check_output_value(arg, value)
                i += 1 if eq else 2
                continue
        elif arg.startswith(_OUTPUT_SHORT):
            inline = arg[len(_OUTPUT_SHORT) :]
            value = inline if inline else (argv[i + 1] if i + 1 < len(argv) else None)
            _check_output_value(arg, value)
            i += 1 if inline else 2
            continue
        i += 1


def _check_output_value(arg: str, value: str | None) -> None:
    if value != BOARD_TOKEN:
        raise E.invalid_argument(
            f"{arg.partition('=')[0]} must be exactly {BOARD_TOKEN}: the handoff only "
            "reloads the board KiCad has open, so kct must write its result there.",
            got=value,
        )


def _allowed() -> list[str]:
    return [" ".join(p) for p in KCT_ALLOW_LIST]


def _parse_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _tail(s: str) -> str:
    return s if len(s) <= _OUTPUT_TAIL else "..." + s[-_OUTPUT_TAIL:]


def _status_body(session: Session) -> dict[str, Any]:
    ref = open_boards_or_fail(session)[0]
    return {"board": {"path": ref.path, "project_dir": ref.project_dir}}


def run_kct_handoff(
    session: Session,
    command: Sequence[str],
    documents: Any = None,
    allow_save: bool = False,
    discard_unsaved: bool = False,
    timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
    *,
    runner: Runner | None = None,
    kct: str | None = None,
) -> dict[str, Any]:
    """Run the protocol. Returns a complete result envelope (``ok``, ``steps``, ...)."""
    started = time.monotonic()
    steps: list[dict[str, Any]] = []
    out: dict[str, Any] = {"steps": steps, "undo_note": UNDO_NOTE}

    def meta() -> dict[str, Any]:
        return {
            **session.config.as_meta(),
            "command_timeout_s": timeout_s,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }

    def fail(step: str, error: dict[str, Any] | OpenKonnectError) -> dict[str, Any]:
        err = error.to_dict() if isinstance(error, OpenKonnectError) else error
        return {"ok": False, "failed_step": step, "error": err, **out, "meta": meta()}

    # Input checks, before touching KiCad.
    try:
        argv = validate_command(command)
        want_board, want_sch = parse_documents(documents)
        if want_sch or not want_board:
            raise E.unsupported(
                "On KiCad 10 the handoff covers the board only: the schematic cannot be "
                "saved or reloaded over IPC.",
                min_kicad="11",
                flag="documents",
            )
        if not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= MAX_COMMAND_TIMEOUT_S:
            raise E.invalid_argument(
                f"timeout_s must be a number of seconds in (0, {MAX_COMMAND_TIMEOUT_S:g}].",
                got=timeout_s,
            )
        kct_path = kct or find_kct()
        if kct_path is None:
            raise OpenKonnectError(
                ErrorCode.KICAD_TOOLS_FAILED,
                "The kct executable (kicad-tools) was not found on PATH. Install kicad-tools, "
                "then retry.",
            )
    except OpenKonnectError as err:
        return fail("validate", err)

    # Step 1: session status for the target document.
    r = session.run_tool(_status_body)
    steps.append({"step": 1, "name": "session_status", "outcome": "ok" if r["ok"] else "error"})
    if not r["ok"]:
        return fail("session_status", r["error"])
    board_path = r["board"]["path"]
    out["board"] = r["board"]

    # Step 2: save before the external write.
    r = session.run_tool(lambda s: save_before_external_write(s, "board", allow_save=allow_save))
    if not r["ok"]:
        steps.append({"step": 2, "name": "save_before_external_write", "outcome": "refused"})
        return fail("save_before_external_write", r["error"])
    saved = r["documents"][0]
    steps.append(
        {
            "step": 2,
            "name": "save_before_external_write",
            "outcome": saved["action"],
            "state_before": saved["state_before"],
            "sha256": saved["sha256"],
            "editor_sha256": saved["editor_sha256"],
            "editor_matches_file": saved.get("editor_matches_file"),
            "match_basis": saved.get("match_basis"),
        }
    )

    # Step 3: the file-level command, on the saved file.
    full = [kct_path, *(a.replace(BOARD_TOKEN, board_path) for a in argv)]
    out["command"] = ["kct", *full[1:]]
    t0 = time.monotonic()
    try:
        res = (runner or default_runner)(full, float(timeout_s), _cwd(out["board"]))
    except OpenKonnectError as e:
        # A runner that already speaks openkonnect's error model: pass its error
        # through unchanged, inside the handoff envelope.
        steps.append({"step": 3, "name": "kct", "outcome": "runner_error"})
        return fail("kct", e)
    except OSError as e:
        steps.append({"step": 3, "name": "kct", "outcome": "failed_to_start"})
        return fail(
            "kct",
            OpenKonnectError(
                ErrorCode.KICAD_TOOLS_FAILED,
                "kct could not be started. See details.",
                details={"exception": str(e)},
            ),
        )
    except Exception as e:  # noqa: BLE001 - any runner failure must stop the handoff
        steps.append({"step": 3, "name": "kct", "outcome": "runner_error"})
        return fail(
            "kct",
            OpenKonnectError(
                ErrorCode.KICAD_TOOLS_FAILED,
                "Running kct failed with an unexpected error, so the editor was not "
                "reloaded. See details.",
                details={"exception": type(e).__name__, "message": str(e)},
            ),
        )
    after = file_state(board_path)
    file_changed = after["sha256"] != saved["sha256"]
    out["exit_code"] = res.returncode
    out["output_json"] = _parse_json(res.stdout)
    step3 = {
        "step": 3,
        "name": "kct",
        "outcome": "timeout" if res.timed_out else ("ok" if res.returncode == 0 else "failed"),
        "exit_code": res.returncode,
        "duration_s": round(time.monotonic() - t0, 3),
        "file_changed": file_changed,
        "sha256_after": after["sha256"],
        "stdout_tail": _tail(res.stdout),
        "stderr_tail": _tail(res.stderr),
    }
    steps.append(step3)

    # Step 4: stop on failure or timeout, without reloading.
    if res.timed_out or res.returncode != 0:
        steps.append({"step": 4, "name": "check_command", "outcome": "stopped"})
        note = "The editor was not reloaded. " + (
            "The file on disk was changed by the stopped command and may be partial; "
            "restore it or re-run the command before syncing."
            if file_changed
            else "The file on disk is unchanged."
        )
        if res.timed_out:
            err = OpenKonnectError(
                ErrorCode.TIMEOUT,
                f"kct ran past its {timeout_s:g} s timeout and was stopped. {note}",
                details={"timeout_s": timeout_s, "step": "kct", "file_changed": file_changed},
            )
        else:
            err = OpenKonnectError(
                ErrorCode.KICAD_TOOLS_FAILED,
                f"kct exited with code {res.returncode}. {note}",
                details={"exit_code": res.returncode, "file_changed": file_changed},
            )
        return fail("kct", err)
    steps.append({"step": 4, "name": "check_command", "outcome": "ok"})

    # Step 5: nothing to reload if the file did not change.
    if not file_changed:
        steps.append(
            {
                "step": 5,
                "name": "check_file_changed",
                "outcome": "skipped_reload",
                "reason": "The command did not change the file.",
            }
        )
        steps.append({"step": 7, "name": "report", "outcome": "ok"})
        out["final_state"] = {"reloaded": False, "file_changed": False, "sha256": after["sha256"]}
        return {"ok": True, **out, "meta": meta()}
    steps.append({"step": 5, "name": "check_file_changed", "outcome": "changed"})

    # Step 6: reload, protected by the editor content this call saved at step 2.
    # The snapshot is the handoff's own: the hash of the editor content step 2
    # saved (or proved equal to the file), never a caller value. It counts only if
    # step 2 read the editor text and the file fresh and found them equal, by
    # bytes or by the strict token comparison on a measured KiCad version (issue
    # #37). With a canonical match the editor hash differs from the file hash, so
    # the two hashes are not compared here.
    snapshot = (
        saved["editor_sha256"]
        if saved.get("editor_matches_file") is True and saved["editor_sha256"] is not None
        else None
    )
    r = session.run_tool(
        lambda s: sync_from_disk(
            s,
            "board",
            discard_unsaved=discard_unsaved,
            expected_hash=after["sha256"],
            editor_hash=saved["editor_sha256"],
            handoff_snapshot_sha256=snapshot,
        )
    )
    if not r["ok"]:
        steps.append({"step": 6, "name": "sync_from_disk", "outcome": "refused"})
        err = r["error"]
        if err["code"] == ErrorCode.DOCUMENT_DIRTY.value:
            err["details"]["handoff_state"] = (
                "The editor changed in KiCad while the command ran, so the reload was "
                "refused. The file on disk holds the command's output, and the editor holds "
                "its previous state plus the edits made in KiCad meanwhile. Nothing was lost "
                "yet: either save from KiCad (keeping your edits and dropping the "
                "command's), or re-run sync_from_disk with discard_unsaved=true (keeping the "
                "command's output and dropping the edits)."
            )
        elif err["code"] == ErrorCode.DIRTY_STATE_UNKNOWN.value:
            err["details"]["handoff_state"] = (
                "The file on disk holds the command's output. openkonnect could not take a "
                "snapshot of the editor at the save (KiCad did not serve its text, or it "
                "did not match the saved file), so it cannot prove the editor holds nothing "
                "the new file lacks (KiCad 10 has no dirty query). Nothing was lost yet: "
                "if you made no edits in KiCad you need, re-run sync_from_disk with "
                "discard_unsaved=true (keeping the command's output), or save from KiCad "
                "(keeping the editor's state and dropping the command's)."
            )
        return fail("sync_from_disk", err)
    synced = r["documents"][0]
    keep = ("discarded_unsaved", "view_kept", "verified", "editor_changed")
    steps.append(
        {
            "step": 6,
            "name": "sync_from_disk",
            "outcome": "reloaded",
            "snapshot_matched": synced.get("reload_basis") == "handoff_snapshot",
            **{k: synced[k] for k in keep},
        }
    )

    # Step 7: report.
    steps.append({"step": 7, "name": "report", "outcome": "ok"})
    out["final_state"] = {
        "reloaded": True,
        "file_changed": True,
        "sha256": after["sha256"],
        "editor_sha256": synced["editor_sha256"],
        "verified": synced["verified"],
    }
    return {"ok": True, **out, "meta": meta()}


def _cwd(board: dict[str, Any]) -> str | None:
    d = board.get("project_dir")
    return d if d and os.path.isdir(d) else None
