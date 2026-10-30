# openkonnect capability spec v0

**Issue:** #1 · **Status:** v0, draft for implementation · **Written:** 2026-10-27
**Role:** written in the *specification author* role of [CLEANROOM.md](../CLEANROOM.md).
The author wrote this spec only and implemented none of it (see
[Provenance](#provenance)).

This document says *what* each openkonnect tool must do: its purpose, inputs,
outputs and failure modes. It does not say *how*. Implementers build from this
text, KiCad's public API documentation, and permissively licensed code, under
the rules in CLEANROOM.md. Tool names are ours. Where a name came from a request
in this repo (#2, #4, #5), we keep that name.

## Contents

- [How to read an entry](#how-to-read-an-entry)
- [What KiCad 10's IPC API allows](#what-kicad-10s-ipc-api-allows)
- [Contracts shared by every tool](#contracts-shared-by-every-tool)
- [Session](#session) (SES-01 to SES-17)
- [Schematic](#schematic) (SCH-01 to SCH-07)
- [Layout](#layout) (LAY-01 to LAY-18)
- [Placement](#placement) (PLC-01 to PLC-04)
- [Routing](#routing) (RTE-01 to RTE-09)
- [Review](#review) (REV-01 to REV-08)
- [Manufacturing](#manufacturing) (MFG-01 to MFG-05)
- [Considered and not specified](#considered-and-not-specified)
- [Upstream gaps for kicad-tools](#upstream-gaps-for-kicad-tools)
- [Coverage of #4 and #5](#coverage-of-4-and-5)
- [Provenance](#provenance)

## How to read an entry

Each entry has an ID, a tool name and these fields:

- **Class.** One of:
  - **file-level**: the capability works offline on `.kicad_*` files. Under the
    #6 boundary it comes from kicad-tools, and openkonnect does not
    reimplement it. openkonnect may *call* it, usually on a snapshot of the live
    board ([SES-11](#ses-11-snapshot_board)) or on the saved file after a
    handoff ([SES-06](#ses-06-run_kct_handoff-and-the-handoff-protocol)).
  - **live-editor**: the capability needs a running KiCad over IPC. This covers
    the session, file sync, selection and highlighting, and edits to the open
    document with undo. openkonnect owns these.
- **kicad-tools.** One of:
  - **already in kicad-tools**, with the `kct` command or MCP tool cited;
  - **partial**, meaning some of it exists there;
  - **upstream gap**, meaning it is missing there and is listed in
    [Upstream gaps](#upstream-gaps-for-kicad-tools) so that #6 can file it on
    rjwalters/kicad-tools;
  - **n/a**, for a purely live-editor capability that has no file-level
    counterpart.
- **KiCad.** The lowest KiCad version whose documented IPC API supports the
  live part, as far as the public docs say ([P-D3]–[P-D6]). "10.0" means any
  KiCad 10 release. "11" means the docs mark it as KiCad 11 only, so on KiCad
  10 the live part is **deferred** and the entry says what happens instead.
- **From.** The request or roadmap item that asked for it.
- **Purpose / Inputs / Outputs / Failure modes.** What the tool does. All of
  this text is ours.
- **Sources.** IDs from the [Provenance](#provenance) table.

The input and output lists name fields informally. They are *not* a schema.
Implementers choose the exact JSON shapes, and #2 fixes the conventions.

## What KiCad 10's IPC API allows

Several entries depend on these facts. Each comes from KiCad's public docs and
the official Python bindings' docs ([P-D1]–[P-D6]), as read on 2026-10-27.

1. **Only a running GUI.** In KiCad 10 the IPC API talks only to a running KiCad
   GUI. Headless use through `kicad-cli` arrives in KiCad 11.
2. **No plotting or export.** KiCad 10's API cannot plot or export. All
   manufacturing output therefore goes through `kicad-cli` or kicad-tools, on a
   saved file.
3. **PCB editor only.** KiCad 10's API serves the PCB editor. The schematic
   document API is marked KiCad 11. So on KiCad 10 openkonnect **cannot read or
   edit the open schematic live**, cannot read its selection, and cannot save or
   reload it over IPC.
4. **No dirty-state query on KiCad 10.** The docs mark a "modified since last
   save" query as KiCad 11. Saving, reverting to the saved file, reading the
   whole document as native-format text, item create/update/delete, grouped
   commits (one undo step) and selection read/add/remove/clear are documented
   without a KiCad 11 restriction. On KiCad 10, dirty state has to come from
   another documented signal or be reported as `unknown` (see
   [SES-01](#ses-01-session_status)).
5. **Version-gated features.** Several board features need a recent 10.0.x
   point release. Flipping items to the other side and setting layer visibility
   need 10.0.6. Finding items by net needs 10.0.1. Items carry stable internal
   IDs from 10.0.0.
6. **KiCad 11 only.** Reading and writing board design rules, refilling a
   chosen subset of zones (KiCad 10 refills all of them), and placing a new
   footprint from a library.
7. **No viewport, zoom, net highlight or DRC request** is documented. A generic
   "run a named editor action" request exists, but the docs mark it unstable
   and not for general use.
8. **Synchronous, and sometimes busy.** KiCad handles API requests on its UI
   thread. A request may time out or come back "busy" while the user is in a
   modal operation, during a zone refill, or during an interactive move. Clients
   should retry, and should hold one connection per KiCad instance.
9. **Several instances.** Each KiCad instance has its own socket. The socket
   name gets the process ID appended when the default name is taken. KiCad also
   gives each instance a token that changes on restart. The API must be enabled
   in KiCad's preferences, and is off unless the user enables it ([P-K1], [P-C1]).

The version notes in [P-D3]–[P-D6] come from the development edition of the
bindings' docs. Implementers must confirm them against the KiCad 10.0.x release
they test with, and should correct this spec when the two disagree.

## Contracts shared by every tool

**C-1 Units and frames.** Lengths are in millimetres and angles in degrees.
Every coordinate output names its frame:
- `file`: absolute KiCad page coordinates, as stored in the `.kicad_pcb`, with
  Y pointing down;
- `board`: relative to the top-left corner of the board-level Edge.Cuts
  graphics' geometry, stroke width excluded. Footprint-owned Edge.Cuts
  graphics do not count, matching `kct` (kicad-tools `board_outline_bounds`),
  so the outline size in `board_summary` omits them too.

kicad-tools commands do not all use the same default frame. For example,
`kct pcb move-footprint` takes board-relative coordinates unless `--absolute`
is passed. So every tool that returns coordinates also returns the offset
between the two frames. Inputs must say which frame they use; there is no
silent default.

**C-2 References.** Items are named by file-level references wherever one
exists:
- reference designators (`U3`);
- net names (`/USB_D+`);
- pads as `REF.PAD` (`U3.14`);
- canonical layer names (`F.Cu`).

KiCad's internal item IDs may also be returned for items that have no file-level
name, such as a single track segment. They are marked session-scoped. A
reference that matches nothing gives `not_found`, and one that matches more than
one item gives `ambiguous_reference`. Neither is ever guessed.

**C-3 Errors.** A failure is structured. It carries a stable `code`, a sentence
saying what to do, a `retryable` flag, and details. Codes used in this spec are
listed below. The first table holds the codes the server emits today (the
`ErrorCode` values in `src/openkonnect/errors.py`; a test keeps the two in
step). The second holds codes reserved for tools this spec plans but that are
not yet emitted.

| Code | Meaning |
|------|---------|
| `kicad_not_running` | No KiCad process is found |
| `ipc_disabled` | KiCad is running, but its API server is not enabled |
| `ipc_unreachable` | A socket exists but does not answer, or could not be told apart from `ipc_disabled` |
| `kicad_busy` | KiCad answered "busy" after the bounded retries |
| `timeout` | No answer in time |
| `instance_changed` | KiCad restarted, or another instance answered |
| `no_document` | The needed board or schematic is not open, or the board is open but has never been saved, so it has no file (`details.saved: false`) |
| `document_dirty` | Refused to protect unsaved editor changes |
| `dirty_state_unknown` | Refused because unsaved changes could not be ruled out |
| `unsupported_on_this_kicad` | The documented API lacks the feature on this version; carries the minimum version when known |
| `not_found`, `invalid_argument` | Bad input |
| `stale_input` | The input was computed from a board state that has since changed |
| `save_failed` | KiCad could not save the document, or reported success but the file on disk is unchanged ([SES-04](#ses-04-save_before_external_write)) |
| `reload_failed` | KiCad rejected the file on reload (for example it does not parse), so the board was not reloaded; `details.editor_state` says whether the editor still holds its previous state ([SES-05](#ses-05-sync_from_disk)) |
| `kicad_tools_failed` | A delegated kicad-tools command failed, could not be started, or is absent |
| `internal_error` | An unexpected failure inside openkonnect, or a KiCad reply it cannot map to another code; returned as data, never as a raw exception |

A board that has never been saved is reported as `no_document` rather than
under a code of its own. The agent's remedy is the same kind of action (put a
document that has a file in front of KiCad; here, save the board with
File > Save As), and `details.saved: false` tells the two cases apart. A new
code would change the `ErrorCode` set and the wire contract for no gain. A
plain "not open" `no_document` carries `details.kind` and no `saved` field.

Reserved, not yet emitted:

| Code | Meaning |
|------|---------|
| `ambiguous_reference` | Bad input: a reference matches more than one item |
| `locked_item` | The item is locked and the caller did not allow editing locked items |
| `kicad_cli_missing`, `kicad_cli_failed` | `kicad-cli` failed or is absent |

**C-4 Bounded time.** Every call finishes within a timeout the caller can see.
No tool waits forever on KiCad. Retries on `kicad_busy` are bounded, and the
result reports how many were made.

**C-5 Report what was observed.** A result reports what openkonnect observed
after the action, by reading it back, and not what the caller asked for. A check
that could not run is reported as *not run*. A summary verdict that includes a
not-run check is *incomplete*, never a pass. This matches kicad-tools' own
`NOT RUN` / `INCOMPLETE` vocabulary ([P-T5]).

**C-6 Mutations.**
- Every tool that changes the open document accepts `dry_run`. A dry run
  reports exactly what would change and changes nothing.
- One tool call is **one undo step** in KiCad, unless the caller has opened a
  group ([SES-17](#ses-17-begin_change-commit_change-abort_change)).
- If any part of a call fails, none of it lands.
- Locked items are refused unless the call passes `allow_locked`.
- The result lists each changed item with its before and after values.
- A reload after an external file write is *not* an undo step, and tools that
  reload say so.

**C-7 Never edit a file underneath the editor.** If a live edit is unsupported
on the running KiCad, the tool returns `unsupported_on_this_kicad`. It does not
quietly fall back to editing the `.kicad_*` file while that file is open in
KiCad. The only route to a file write next to an open editor is the handoff
protocol ([SES-06](#ses-06-run_kct_handoff-and-the-handoff-protocol)).

**C-8 Where a result came from.** A result built from file-level analysis says
which input it read:
- `snapshot`: the live board including unsaved changes, with the snapshot ID;
- `saved_file`: the file on disk, with its hash.

Sign-off results ([REV-02](#rev-02-signoff_check), [MFG-01](#mfg-01-prefab_check))
only ever come from `saved_file`.

---

## Session

Connection, documents, file sync (#4), selection and findings (#5), view
control, and grouping edits into transactions. Every entry here is live-editor.

### SES-01 `session_status`
- **Class:** live-editor · **kicad-tools:** partial. `kct ipc status` finds
  instances and IPC reachability, but has no per-document or dirty state ·
  **KiCad:** 10.0 (dirty state: see below) · **From:** #4, #2
- **Purpose:** tell the agent, in one cheap call, whether KiCad is reachable and
  what it has open, so that it can choose between live tools and file tools.
- **Inputs:** optional instance (from [SES-02](#ses-02-list_instances)).
  Without it, the selected or only instance is used.
- **Outputs:**
  - `kicad_running` and `ipc_reachable`, as separate fields;
  - the KiCad version and API version;
  - the instance identity;
  - the project directory, or null when no board is open or the board has
    never been saved;
  - a list of documents. Each has `kind` (board or schematic), `path`,
    `path_basis`, `open` (true/false/unknown), `dirty` (true/false/unknown)
    and `dirty_basis`. `path_basis` says where `path` came from:
    - `kicad_ipc`: the board path KiCad reports;
    - `inferred_from_project`: the schematic path, derived from the project
      directory and name because KiCad 10 does not serve the schematic. The
      record also has `exists_on_disk`;
    - `unsaved`: a board KiCad reports with no file name and no project, so
      it is new and has never been saved. `path` is `null`, and a `path_note`
      says why. This is the only case where `path` is `null`. No schematic
      is listed for such a board, as there is no project to infer it from.

    `dirty_basis` says how dirty was decided: KiCad's own flag (KiCad 11+),
    a documented content comparison against the saved file, or `none`;
  - a table of capability flags saying which tools in this spec the instance
    supports, with the reason for each one it does not. For a board that has
    never been saved, the tools that need the board's file
    ([SES-04](#ses-04-save_before_external_write),
    [SES-05](#ses-05-sync_from_disk),
    [SES-06](#ses-06-run_kct_handoff-and-the-handoff-protocol)) are marked
    `supported: false` with `error_code: "no_document"`.
- **KiCad 10 notes:**
  - There is no documented dirty query on KiCad 10. If the implementer cannot
    decide dirty state reliably from documented signals, it is reported as
    `unknown`.
  - The schematic is not served on KiCad 10. Its path may be reported as
    *inferred* from the project, with `open` and `dirty` set to `unknown`.
  - Every tool that protects unsaved work treats `unknown` as dirty.
  - A PCB Editor started with no file reports an empty board file name, an
    empty project path and an empty project name. This is a steady state, not
    a startup race: KiCad keeps answering this way until the board is saved,
    so the board is listed with `path_basis: "unsaved"` rather than retried
    (black-box, KiCad 10.0.7 on macOS, #34).
- **Failure modes:** this tool reports "not running" or "IPC disabled" as
  *data*, not as an error, so that it can always answer. It returns `kicad_busy`
  or `timeout` after bounded retries. It returns `instance_changed` if the
  selected instance has restarted.
- **Sources:** P-R2, P-D2, P-D4, P-D5, P-T7, P-T8.

### SES-02 `list_instances`
- **Class:** live-editor · **kicad-tools:** partial (`kct ipc status`
  discovers running instances) · **KiCad:** 10.0 · **From:** ROADMAP Phase 1
- **Purpose:** find every reachable KiCad instance on this machine.
- **Inputs:** an optional explicit socket path.
- **Outputs:** one record per instance: its endpoint, KiCad version, the open
  board path (when it can be read), whether it answered, and why not if it
  did not.
- **Failure modes:** an empty list is a valid answer, not an error. A socket
  that exists but does not answer is listed as `ipc_unreachable`.
- **Sources:** P-D1, P-D2, P-T7.

### SES-03 `select_instance`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 ·
  **From:** ROADMAP Phase 1
- **Purpose:** choose which KiCad instance later calls talk to, when more than
  one is running.
- **Inputs:** an endpoint, or the path of a board that the wanted instance has
  open.
- **Outputs:** the selected instance's identity, version and open documents.
- **Failure modes:**
  - `not_found` when nothing matches, and `ambiguous_reference` when two
    instances match;
  - after selection, any call that finds a different instance answering (for
    example after a KiCad restart) fails with `instance_changed` rather than
    acting on the wrong editor.
- **Sources:** P-D1, P-D2.

### SES-04 `save_before_external_write`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 (board);
  11 (schematic) · **From:** #4
- **Purpose:** make the saved file match what the user sees, before a
  file-based tool reads or writes it.
- **Inputs:**
  - `documents`: board, schematic or all; the default is all open documents;
  - `allow_save`: default false;
  - `schematic_unverified_ok`: default false. See the KiCad 10 notes below.
- **Behaviour:**
  - A clean document is left alone.
  - A dirty document with `allow_save` is saved. The tool then checks that the
    file on disk changed.
  - A dirty document without `allow_save` is refused, and no document is saved.
  - Dirty state `unknown` counts as dirty.
- **Outputs:** per document: its state before the call, the action taken
  (none / saved / refused), and the file hash afterwards. The hash lets the
  handoff detect the external write later.
- **KiCad 10 notes:**
  - The schematic cannot be saved or inspected over IPC.
  - If a schematic is part of the request, the tool refuses with
    `unsupported_on_this_kicad`, unless the caller passes
    `schematic_unverified_ok`. That flag states that the user has saved or
    closed the schematic, and the result records that the caller asserted it.
- **Failure modes:**
  - `document_dirty` or `dirty_state_unknown`, naming the documents;
  - `save_failed` when the save fails inside KiCad (for example a read-only
    file, or a dialog blocking it): the error says which;
  - `save_failed` when the save reports success but the file on disk is
    unchanged: reported as a failure, not as success;
  - the C-3 connection errors.
- **Sources:** P-R2, P-D2, P-D4.

### SES-05 `sync_from_disk`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 (board);
  11 (schematic) · **From:** #4
- **Purpose:** after an external tool has written a file, make the open editor
  show what is on disk.
- **Inputs:**
  - `documents`;
  - `discard_unsaved`: default false;
  - `expected_hash`: optional, normally from SES-04.
- **Behaviour:**
  - If the editor is dirty or `unknown` and `discard_unsaved` is not set,
    refuse and change nothing. The editor is clean only when its content,
    read during this call, equals the file, also read during this call:
    byte-identical, or, on a KiCad version where the serializer difference
    was measured (10.0.7 only), equal by the strict token comparison in
    `docs/handoff-protocol.md` (only whitespace between tokens, and the
    `version`/`generator`/`generator_version` lists directly inside a
    footprint in the editor text, are ignored; the same lists in the file
    stay a difference). A caller-supplied hash (`editor_hash`, the editor
    content hash from SES-04, or `expected_hash`) can only show the editor
    changed or leave the state `unknown`; it never proves the editor clean.
  - Inside [SES-06](#ses-06-run_kct_handoff-and-the-handoff-protocol) only, an
    `unknown` editor may be reloaded without `discard_unsaved` when its
    content, read during this call, hashes to the snapshot the handoff took of
    its own save in the same handoff call (the snapshot rule). The snapshot is
    never a caller input, and it never relaxes a changed (dirty) editor.
  - With `discard_unsaved`, nothing is refused or reported first: unsaved
    work, including an edit detected through `editor_hash`, is discarded, and
    the result says so.
  - Otherwise reload the document from disk, keeping the view (zoom, pan,
    layers) where KiCad allows.
  - Afterwards, check that the editor content matches the file.
- **Outputs:** per document: whether it was reloaded, whether unsaved changes
  were discarded, whether the view was kept (true/false/unknown), and a note
  that the reload is not an undo step.
- **KiCad 10 notes:** the schematic cannot be reloaded over IPC. The result
  says `manual_reload_required` and tells the user what to do.
- **Failure modes:**
  - `document_dirty` or `dirty_state_unknown`;
  - the file is missing, or does not match `expected_hash`: refuse and leave the
    editor as it is, since an unexpected write could be a third party;
  - `reload_failed` when KiCad rejects the file (for example it does not
    parse): report that the editor still holds the previous state;
  - the C-3 connection errors.
- **Sources:** P-R2, P-D4.

### SES-06 `run_kct_handoff` and the handoff protocol
- **Class:** live-editor (it orchestrates a file-level tool) · **kicad-tools:**
  the file-level step is any `kct` write command, already in kicad-tools ·
  **KiCad:** 10.0 (board only) · **From:** #4, ROADMAP principle 3
- **Purpose:** let a file-based kicad-tools command change a board that is open
  in KiCad, without losing either side's changes.
- **Protocol.** The protocol is mandatory. The wrapper tool is a convenience for
  it. Its steps, in order:
  1. Read [SES-01](#ses-01-session_status) for the target document.
  2. Run [SES-04](#ses-04-save_before_external_write). Stop if it refuses.
     Record the file hash.
  3. Run the file-level command on the saved file.
  4. If the command failed, stop. Do not reload. Report whether the file
     changed.
  5. If the file did not change, skip the reload.
  6. Otherwise, run [SES-05](#ses-05-sync_from_disk) with the hash and the
     snapshot rule: at step 2 the tool records the hash of the exact editor
     content it saved (or proved equal to the file by the SES-05 rule); after
     the command, it reloads without `discard_unsaved` only if a fresh read of
     the editor hashes to that snapshot. An editor that changed meanwhile is
     refused with `document_dirty`. Without a snapshot the reload is refused as
     `unknown`. `discard_unsaved` overrides both. One window remains: a human
     save plus an exact undo during step 3 matches the snapshot, so the reload
     loads the human's saved file (which replaced the command's output).
  7. Report the result.
- **Inputs:**
  - `command`: a `kct` subcommand and its arguments, from an allow-list of
    known write commands. This is never an arbitrary shell command;
  - `documents`;
  - `allow_save`;
  - `timeout`.
- **Outputs:** one record per step, with its outcome; the command's exit code
  and its JSON output, when it has one; the final document state; and a note
  that the change cannot be undone with Ctrl+Z (reverting means restoring the
  earlier file).
- **Failure modes:**
  - a refusal or failure at any step stops the sequence and names the step;
  - a command missing from the allow-list gives `invalid_argument`;
  - a flag that makes the command write somewhere other than the open board
    gives `invalid_argument` before KiCad is contacted (#59): `--auto-layers`,
    `--checkpoint`, and `-o`/`--output` with any value other than exactly
    `{board}`;
  - a command that runs past the timeout is stopped. The file state is then
    reported as found, and the editor is not reloaded;
  - the user edits during step 3: step 6 refuses with `document_dirty`,
    reports the command's result too, and says that the file holds the
    command's output while the editor holds the user's edits. With
    `discard_unsaved` the edit is discarded and the result says so.
- **Test requirement (#4):** an end-to-end test wraps one real `kct` write,
  such as `kct route`, in this protocol against a fixture board open in
  KiCad.
- **Sources:** P-R2, P-T7.

### SES-07 Fail-fast connection errors (contract)
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 ·
  **From:** #4, ROADMAP principle 5
- **Purpose:** no tool hangs, or fails vaguely, when KiCad is missing.
- **Rule:** every tool except SES-01 and SES-02 checks reachability first, and
  returns one of the following within the C-4 bound:
  - `kicad_not_running`;
  - `ipc_disabled`, which names the KiCad preference that enables the API;
  - `ipc_unreachable`;
  - `kicad_busy`;
  - `no_document`.

  When "not running" and "API disabled" cannot be told apart, the error says
  so. It does not pick one.
- **Never:** wait without a bound; fall back to the file on disk (C-7); or
  report success with nothing done.
- **Test requirement:** each code is covered by a test against a mocked IPC
  endpoint (#2).
- **Sources:** P-R2, P-D1, P-D2.

### SES-08 `get_selection`
- **Class:** live-editor · **kicad-tools:** n/a (its consumers, `kct`
  commands, take these references) · **KiCad:** 10.0 (board); 11 (schematic) ·
  **From:** #5
- **Purpose:** turn what the user has selected in the PCB editor into stable,
  file-level references that can be passed straight to `kct`, with no
  coordinate translation by hand.
- **Inputs:**
  - an optional filter by item kind;
  - `include_geometry`: default false.
- **Outputs:**
  - `footprints`: reference designators;
  - `pads`: `REF.PAD`;
  - `nets`: net names of the selected copper, pads and zones;
  - `nets_of_footprints`: optional; nets reached through the pads of selected
    footprints;
  - `other`: items with no file-level name (tracks, vias, zones, graphics),
    each with its kind, layer, net, a session-scoped ID and the flag
    `no_file_reference`;
  - `bbox`: the selection's bounding box in mm, in the `file` frame, together
    with the board-frame offset (C-1).

  An empty selection is an ordinary empty result, not an error.
- **Failure modes:**
  - `no_document`;
  - selected items of a kind the API does not describe are counted and
    reported as `undescribed`. They are never dropped silently;
  - a request for the schematic selection on KiCad 10 gives
    `unsupported_on_this_kicad` (11).
- **Sources:** P-R3, P-D4, P-T7.

### SES-09 `show_findings`
- **Class:** live-editor · **kicad-tools:** partial. The findings come from
  `kct check`, `kct drc`, `kct detect-mistakes`, `kct net-status` and the
  `measure_clearance` MCP tool with `--format json`. A single stable finding
  schema is an upstream gap (G1) · **KiCad:** 10.0 for selecting; zoom and
  highlight are not documented (see below) · **From:** #5
- **Purpose:** take kicad-tools' JSON findings and point the user at the items
  they concern in the live editor.
- **Inputs:**
  - `findings`: a list as emitted by a kicad-tools command;
  - `mode`: select, highlight or both;
  - `focus`: all, one index, or next/previous through the list;
  - `zoom`: true or false.
- **Interpretation:** the tool reads kicad-tools' own output models to find
  each finding's locators: refs, `REF.PAD`, net names, a point or box
  (mm, file frame), and a layer. This spec does not define or copy a finding
  schema. If kicad-tools publishes a stable one (G1), the tool uses it.
- **Outputs:** for each finding:
  - the items it resolved to;
  - the locators that did not resolve, each with a reason (reference not on
    the board, net renamed, empty area);
  - the action taken.

  Plus totals.
- **Optional location (#5):** a finding's point is optional in kicad-tools'
  models, and `kct check` on kicad-tools `main` emits `location: null`. Such
  a finding is resolved from its refs, pads and nets alone and reported with
  `has_point: false` and a `point_note`; no point is invented. A finding with
  neither a point nor a name is `invalid_argument`.
- **KiCad 10 notes:**
  - Selecting items is documented.
  - Zooming to items and highlighting a net are not documented. Those parts
    return `unsupported_on_this_kicad`, while selection still happens.
  - An implementation may offer zoom through the docs' unstable action
    request, but only behind an explicit opt-in. The result then marks it
    best-effort.
  - **As shipped (#5):** checked against kicad-python 0.8.0, which has no
    zoom, view or net-highlight request; the unstable action request is not
    used and there is no opt-in. `mode` `highlight` or `both` and
    `zoom: true` each return `unsupported_on_this_kicad` for that part, while
    the items are still selected (the editor draws the selection
    highlighted). The view is not moved.
- **Failure modes:**
  - an unrecognised finding gives `invalid_argument`, naming its index;
  - findings that carry an input hash which no longer matches the board are
    still resolved where possible, and flagged `possibly_stale`;
  - `no_document`.
- **Sources:** P-R3, P-R4, P-D3, P-D4, P-D5, P-T7.

### SES-10 Selection to `kct` and back (round-trip contract)
- **Class:** live-editor · **kicad-tools:** the middle step is already in
  kicad-tools (any finding-producing `kct` command) · **KiCad:** 10.0 ·
  **From:** #5
- **Purpose:** prove that references survive the trip from editor to file
  tool and back.
- **Contract:**
  1. Select items in a fixture board.
  2. Pass the [SES-08](#ses-08-get_selection) output to a `kct` command that
     reports findings on those items.
  3. Feed its findings to [SES-09](#ses-09-show_findings).
  4. The selection that results contains the same refdes, `REF.PAD` and net
     set as the original selection.
  5. Locations agree within 1 µm in the file frame, for the findings that
     carry one.
- **Failure modes:** a mismatch is a test failure, reported with the
  difference between the two sets.
- **Test requirement:** a live-KiCad test using a fixture written for this
  repo.
- **Sources:** P-R3.

### SES-11 `snapshot_board`
- **Class:** live-editor · **kicad-tools:** n/a (its consumers are file-level)
  · **KiCad:** 10.0 · **From:** ROADMAP Phase 1 (hand-off to kicad-tools)
- **Purpose:** capture the open board, including unsaved changes, as a
  temporary `.kicad_pcb` that kicad-tools can analyse. The saved file and the
  editor's dirty state are not touched.
- **Inputs:** an optional output directory.
- **Outputs:**
  - the snapshot path, ID, time and content hash;
  - the saved file's hash, and whether the two differ;
  - the project files copied beside the snapshot, so that kicad-tools sees the
    same net classes and rules.
- **Failure modes:** `no_document`; `timeout` on very large boards;
  `kicad_tools_failed` if kicad-tools cannot load the snapshot. The snapshot is
  then kept for diagnosis, and the error points to it.
- **Sources:** P-D4, P-R1.

### SES-12 `compare_with_disk`
- **Class:** live-editor · **kicad-tools:** already in kicad-tools (`kct pcb
  diff`, kicad-tools 0.24.0, kicad-tools #6417; formerly gap G3, now resolved
  upstream) · **KiCad:** 10.0 · **From:** #4, ROADMAP Phase 1
- **Purpose:** say whether, and how, the board in the editor differs from the
  saved file.
- **Inputs:** none, or a list of item kinds to compare.
- **Outputs:** `differs` (true/false/unknown), and when known a summary by
  kind: footprints moved, added or removed; copper added or removed; zones
  changed. Each with references.
- **Implementation note:** `kct pcb diff` compares two files, so the editor
  side must be a file first.
  - Take a snapshot of the open board, unsaved changes included, as a
    temporary `.kicad_pcb`. Use the capture that
    [SES-11](#ses-11-snapshot_board) describes; this entry adds no second
    mechanism. Then run the diff between the saved file and that snapshot.
  - The snapshot, and any project files copied beside it, go in a new
    temporary directory created for this call, outside the user's project.
    The tool deletes that directory before it returns, whether the diff
    succeeded or failed. SES-11 keeps its snapshot when kicad-tools fails, for
    diagnosis; SES-12 returns only `differs` and its basis, so it keeps
    nothing.
  - The snapshot is never written to the board's own path or over its saved
    copy. This tool does not save the document and does not touch the saved
    file.
  - The comparison is semantic, item by item, so formatting differences
    between the editor's text and a KiCad save do not matter. SES-12 does not
    depend on that text matching a real KiCad save byte for byte (the open
    question in #23).
- **Failure modes:** `no_document` and the SES-07 errors; `timeout` from the
  SES-11 capture on very large boards; `invalid_argument` for a malformed list
  of item kinds. If kicad-tools is not installed, if the snapshot cannot be
  taken or written, or if the board diff fails to load either side, the tool
  reports only `differs` (as `unknown` when it could not compare) and gives the
  basis it used. These snapshot and diff failures add no new error codes.
- **Sources:** P-R1, P-R2, P-D4, P-T10.

### SES-13 `select_items`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 ·
  **From:** ROADMAP Phase 2
- **Purpose:** set the editor selection from file-level references, so that
  the agent can show the user what it means or prepare an interactive action.
- **Inputs:**
  - an operation: replace, add, remove or clear;
  - refdes, `REF.PAD`, net names (meaning every item on the net), or
    session IDs.
- **Outputs:** the selection read back after the change, in SES-08's form,
  and any references that did not resolve.
- **Failure modes:** `not_found` and `ambiguous_reference` per reference. The
  items that did resolve are still applied, and the result is marked partial.
- **Sources:** P-D4.

### SES-14 `focus_view`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** not documented
  for 10 or 11 · **From:** ROADMAP Phase 2, #5
- **Purpose:** zoom and pan the editor to the given items, or fit the whole
  board.
- **Inputs:** references (as for SES-13), or `fit_board`; an optional margin.
- **Outputs:** whether the view changed, and the box it targeted.
- **Status:** **deferred.** Until a documented API exists, the tool returns
  `unsupported_on_this_kicad`. The unstable action request may be used only as
  an explicit opt-in, as in SES-09.
- **Sources:** P-D3, P-D5.

### SES-15 `highlight_net` / `clear_highlight`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** not documented ·
  **From:** ROADMAP Phase 2, #5
- **Purpose:** turn on, or clear, the editor's net highlight for one or more
  nets.
- **Inputs:** net names.
- **Outputs:** the nets highlighted, read back where possible.
- **Status:** **deferred**, on the same terms as SES-14. Until then,
  selecting every item on the net (SES-13) is the fallback, and the result says
  it used that fallback.
- **Sources:** P-D3, P-D5.

### SES-16 `view_state` / `set_view_state`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 for reading
  the active and visible layers; 10.0.6 for setting visible layers ·
  **From:** ROADMAP Phase 1–2
- **Purpose:** read, and change, which layers the user is viewing and working
  on.
- **Inputs:** none to read. To set: the visible layers, and optionally the
  active layer.
- **Outputs:** the active layer and the visible layers, read back after any
  change. The viewport is not reported, because no API for it is documented.
- **Failure modes:** an unknown layer name gives `invalid_argument`. Setting
  visible layers below 10.0.6 gives `unsupported_on_this_kicad`. If the active
  layer does not read back as set, that is reported as a failure.
- **Sources:** P-D3.

### SES-17 `begin_change`, `commit_change`, `abort_change`
- **Class:** live-editor · **kicad-tools:** n/a (kicad-tools' MCP
  `start_session` / `commit_session` / `rollback_session` are file-level
  sessions, a different mechanism) · **KiCad:** 10.0 · **From:** ROADMAP
  principle 4, Phase 2
- **Purpose:** let several mutating tool calls land as **one** undo step.
  Without a group, each call is its own step (C-6).
- **Inputs:**
  - `begin_change`: an optional description, which becomes the undo label,
    and an idle timeout;
  - `commit_change` and `abort_change`: the change ID.
- **Behaviour:**
  - While a group is open, its edits are not visible in the editor until
    commit. This follows KiCad's documented commit model, and the result of
    every grouped call says so.
  - Reads made inside the group must say whether they include the pending
    edits.
  - Only one group is open at a time.
  - If the idle timeout passes, the session ends, or KiCad restarts, the group
    is aborted and nothing lands.
- **Outputs:** `begin_change` returns the change ID. `commit_change` returns
  the combined change list (C-6). `abort_change` confirms that nothing landed.
- **Failure modes:**
  - beginning a second group gives `invalid_argument`;
  - an expired or unknown ID gives `not_found`;
  - `instance_changed`.
- **Open question (ROADMAP):** how to behave when the user edits the board
  while a group is open. This is resolved in Phase 2.
- **Sources:** P-D4, P-T2.

---

## Schematic

On KiCad 10 the IPC API does not serve the schematic editor (fact 3). So for
now, every schematic capability is **file-level**, through kicad-tools. Writes
go through the handoff protocol, with the KiCad 10 limits in SES-04 and SES-05:
the schematic cannot be saved, reloaded or checked for dirty state over IPC.
The caller must confirm that the user has saved it, and a write is not
undoable in the editor. The live versions (SCH-06, SCH-07) wait for KiCad 11.

### SCH-01 `read_schematic`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct sch
  summary`, `kct sch hierarchy`, `kct symbols`, `kct nets`, `kct sch labels`,
  `kct sch pin-map`) · **KiCad:** live read 11, deferred · **From:** ROADMAP
  Phase 5
- **Purpose:** give the agent the sheets, symbols (with their fields), labels
  and nets of the project's schematic.
- **Inputs:** the project, or the root schematic path; an optional sheet; what
  to include.
- **Outputs:** the delegated command's data, plus a C-8 note saying it reflects
  the **saved** file, and that on KiCad 10 the editor may hold unsaved changes
  that this tool cannot see.
- **Failure modes:** no schematic found gives `no_document`. Parse or command
  failures give `kicad_tools_failed`, with kicad-tools' message attached.
- **Sources:** P-T7, P-D2.

### SCH-02 `schematic_connectivity`
- **Class:** file-level · **kicad-tools:** already in kicad-tools. Its
  connectivity audit found five of the six ideas covered: `kct erc`,
  `kct sch unconnected`, `kct sch connections`, `kct nets`, and the
  single-pin-net and orphan-label checks in `kct check` and `kct sch validate`
  · **KiCad:** n/a · **From:** P-C2 tool catalogue (schematic analysis)
- **Purpose:** answer connectivity questions about the schematic: which pins a
  net reaches, which nets have only one pin, which items are dangling, which
  net names are merged, and what is connected at a given point.
- **Inputs:** a query kind, plus a net, a pin (`REF.PIN`) or a point (mm,
  schematic page frame).
- **Outputs:** the delegated result, with the C-8 provenance.
- **Failure modes:** as SCH-01. A query kind that kicad-tools lacks is reported
  as an upstream gap, not built here.
- **Sources:** P-T1, P-T7.

### SCH-03 `edit_schematic`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct sch`:
  `add-component`, `remove-component`, `move-component`, `set-value`,
  `set-reference`, `set-footprint`, `set-symbol-property`, `add-wire`,
  `remove-wire`, `add-label`, `add-junction`, `add-no-connect`,
  `re-annotate`, `fix-wire-stubs`) · **KiCad:** live edit 11, deferred ·
  **From:** ROADMAP Phase 5 (fallback path)
- **Purpose:** make schematic edits while KiCad is open, by running the
  matching `kct sch` command through the handoff protocol.
- **Inputs:** the edit (one of the commands above, with its arguments) and
  `schematic_unverified_ok`, which is required on KiCad 10.
- **Outputs:** the [SES-06](#ses-06-run_kct_handoff-and-the-handoff-protocol)
  record. It states that the edit is **not** an undo step in KiCad, and that the
  user must reload the schematic by hand on KiCad 10.
- **Failure modes:** SES-06's failures, plus `unsupported_on_this_kicad` when
  the caller has not confirmed the schematic is saved.
- **Sources:** P-T7, P-R1, P-D2.

### SCH-04 `update_board_from_schematic`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct sync`,
  `kct pcb sync-netlist`, MCP `create_pcb_from_schematic`) · **KiCad:** a live
  netlist import into the open board appears in the bindings' docs without a
  clear version note. Treat it as unverified for 10 · **From:** P-C2 tool
  catalogue (schematic export)
- **Purpose:** bring the board's footprints and nets in line with the saved
  schematic.
- **Inputs:** a dry-run flag (default true); match options as kicad-tools
  offers them.
- **Outputs:** the planned or applied changes (added, removed and changed
  footprints; net changes), recorded through SES-06.
- **Failure modes:** SES-06's failures; kicad-tools' reported mismatches are
  passed through unchanged.
- **Sources:** P-T7, P-D3.

### SCH-05 `render_schematic`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  screenshot`, MCP `screenshot_schematic`; `kct render --baseline-save` and
  `--baseline-compare` detect drift) · **KiCad:** n/a · **From:** P-T4
- **Purpose:** produce an image of a sheet, and optionally say whether it has
  changed since a stored baseline.
- **Inputs:** a sheet, a format, an optional baseline directory.
- **Outputs:** the image path; and, when a baseline is used, the drift result,
  including kicad-tools' renderer-identity check.
- **Failure modes:** `kicad_cli_missing`; no stored baseline is an explicit
  result, not a pass.
- **Sources:** P-T4, P-T7.

### SCH-06 `get_schematic_selection` and cross-probe
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 11, **deferred**
  · **From:** ROADMAP Phase 5
- **Purpose:** read the schematic editor's selection as refdes, pins and nets.
  Move between a symbol and its footprint in either direction.
- **Status on KiCad 10:** returns `unsupported_on_this_kicad`. The board half
  of cross-probing, selecting footprints given refdes, is already covered by
  [SES-13](#ses-13-select_items).
- **Sources:** P-D2, P-D6.

### SCH-07 Live schematic edits with undo
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 11, **deferred**
  · **From:** ROADMAP Phase 5
- **Purpose:** the SCH-03 edits, made through IPC as undoable transactions.
- **Status:** specified once KiCad 11's schematic API is checked. Until then,
  SCH-03 is the path, and its limits are stated.
- **Sources:** P-D2, P-D6.

---

## Layout

Reads and edits of the board open in the PCB editor. Reads return the **live**
state, including unsaved changes. Every edit follows C-6.

### LAY-01 `board_summary`
- **Class:** live-editor · **kicad-tools:** the file-level equivalent is
  already in kicad-tools (MCP `board_summary`, `kct pcb summary`) · **KiCad:**
  10.0 · **From:** #2, ROADMAP Phase 0
- **Purpose:** give a one-call overview of the open board.
- **Inputs:** none.
- **Outputs:**
  - the board path and title block;
  - copper and total layer counts;
  - counts of footprints, nets, tracks, vias and zones;
  - the outline size;
  - the dirty state, as in SES-01.
- **Failure modes:** `no_document`, and the SES-07 errors.
- **Sources:** P-R1, P-D3, P-T7, P-T8.

### LAY-02 `list_footprints`
- **Class:** live-editor · **kicad-tools:** file-level equivalent already in
  kicad-tools (`kct pcb footprints`, MCP `board_inspect`) · **KiCad:** 10.0 ·
  **From:** ROADMAP Phase 1
- **Purpose:** list the footprints on the board, with what an agent needs in
  order to reason about placement.
- **Inputs:** optional filters: refs, side, inside a box, selected only.
- **Outputs:** per footprint:
  - refdes, value and library ID;
  - position (C-1 frames), rotation, side and locked state;
  - attributes (for example do-not-populate, or excluded from the BOM);
  - fields.
- **Failure modes:** `no_document`. A filter that matches nothing returns an
  empty list.
- **Sources:** P-D3, P-T7.

### LAY-03 `footprint_pads`
- **Class:** live-editor · **kicad-tools:** file-level equivalent already in
  kicad-tools (`kct pcb padmap`) · **KiCad:** 10.0 · **From:** #5 (`REF.PAD`)
- **Purpose:** resolve pads: list a footprint's pads, or look up one pad by
  `REF.PAD`.
- **Inputs:** refs or `REF.PAD` values.
- **Outputs:** per pad: `REF.PAD`, number, net, position, the layers it is on,
  shape and size.
- **Failure modes:** `not_found`; a pad whose net cannot be read reports its
  net as unknown, not as unconnected.
- **Sources:** P-D3, P-T7.

### LAY-04 `list_nets`
- **Class:** live-editor · **kicad-tools:** file-level equivalent already in
  kicad-tools (`kct pcb nets`) · **KiCad:** 10.0; items by net 10.0.1 ·
  **From:** ROADMAP Phase 1
- **Purpose:** list the nets and their net classes, and optionally the items
  on a net.
- **Inputs:** an optional net-name pattern or net class; `include_items`.
- **Outputs:** per net: its name, its net class, and its pad count. When asked,
  the items on it, by reference.
- **Failure modes:** `no_document`. `include_items` below 10.0.1 gives
  `unsupported_on_this_kicad`.
- **Sources:** P-D3, P-T7.

### LAY-05 `list_copper`
- **Class:** live-editor · **kicad-tools:** partial (`kct pcb traces` gives
  statistics; MCP `board_inspect`) · **KiCad:** 10.0 · **From:** ROADMAP
  Phase 1
- **Purpose:** list the tracks, arcs and vias, filtered by net, layer or area.
- **Inputs:** optional net, layer, box (with its frame), and `locked_only`.
- **Outputs:** per item: kind, a session ID, net, layer or layers, width,
  endpoints or centre, the via's size and drill, and locked state. Plus a total
  length per net.
- **Failure modes:** `no_document`; `invalid_argument` for an unknown layer.
- **Sources:** P-D3, P-T7.

### LAY-06 `list_zones`
- **Class:** live-editor · **kicad-tools:** file-level equivalent already in
  kicad-tools (`kct pcb zones`) · **KiCad:** 10.0 · **From:** ROADMAP Phase 1
- **Purpose:** list the copper zones and rule areas (keepouts).
- **Inputs:** an optional net or layer.
- **Outputs:** per zone: a session ID, an optional name, net, layers,
  priority, pad-connection style, whether it is filled, and for a rule area
  what it keeps out.
- **Failure modes:** `no_document`.
- **Sources:** P-D3, P-T7.

### LAY-07 `layers_and_stackup`
- **Class:** live-editor · **kicad-tools:** file-level equivalent already in
  kicad-tools (`kct pcb stackup`) · **KiCad:** 10.0 · **From:** ROADMAP
  Phase 1
- **Purpose:** describe the board's layers and physical build-up, as currently
  set in the editor.
- **Inputs:** none.
- **Outputs:**
  - the enabled layers, with canonical names and the user's names;
  - the copper count;
  - the stackup, listed from top to bottom: for every layer what kind it is,
    how thick it is and what it is made of, the dielectric properties where
    KiCad gives them, and the total thickness.
- **Failure modes:** `no_document`.
- **Sources:** P-D3, P-T7.

### LAY-08 `board_outline`
- **Class:** live-editor · **kicad-tools:** partial (board extents in MCP
  `board_summary`) · **KiCad:** 10.0 · **From:** ROADMAP Phase 1
- **Purpose:** give the board shape and the reference points that coordinate
  frames depend on.
- **Inputs:** none.
- **Outputs:**
  - the outline as one or more closed polygons (file frame);
  - the extents;
  - the board-frame offset (C-1);
  - KiCad's grid and drill origins.
- **Failure modes:** `no_document`. A missing or open outline is reported as
  such, and is never treated as a rectangle.
- **Sources:** P-D3.

### LAY-09 `design_rules`
- **Class:** file-level on KiCad 10 · **kicad-tools:** already in kicad-tools
  (`kct mfr`, `kct constraints`, `kct explain`, which read the project rules
  and custom rules) · **KiCad:** live read and write 11, deferred · **From:**
  ROADMAP Phase 1
- **Purpose:** report the clearance, width, via and other constraints that
  apply to the board.
- **Inputs:** optional net class or rule name.
- **Outputs:** the rules from the saved project files, with the C-8 note that
  unsaved edits made in KiCad's rules dialog are not visible on KiCad 10.
- **Failure modes:** `kicad_tools_failed`; missing project files give
  `no_document`.
- **Sources:** P-D3, P-T3, P-T7.

### LAY-10 `move_footprints`
- **Class:** live-editor · **kicad-tools:** the file-level equivalent is
  already in kicad-tools (`kct pcb move-footprint`, MCP `apply_move`), but it
  must not be used on an open board except through SES-06 · **KiCad:** 10.0 ·
  **From:** ROADMAP Phase 2
- **Purpose:** move or rotate one or more footprints in the open board, as one
  undo step.
- **Inputs:** a list of {ref, position and frame, or delta; rotation, absolute
  or delta}; `allow_locked`; `dry_run`.
- **Outputs:** per footprint, the before and after position and rotation, read
  back. Also a list of copper that no longer meets the moved pads, since moving
  a footprint does not drag its tracks.
- **Failure modes:** `not_found`; `locked_item`; a move that leaves the
  footprint off the board is allowed but flagged. Any failure leaves every
  footprint unmoved (C-6).
- **Sources:** P-D3, P-D4, P-T7.

### LAY-11 `flip_footprints`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0.6 ·
  **From:** ROADMAP Phase 2
- **Purpose:** move footprints to the other side of the board, or to a stated
  side, using KiCad's own flip so that pads, graphics and 3D models follow.
- **Inputs:** refs; a target side, or toggle; `allow_locked`; `dry_run`.
- **Outputs:** per footprint, the side and position before and after.
- **Failure modes:** below 10.0.6, `unsupported_on_this_kicad`. This never
  falls back to a file edit (C-7). Also `locked_item` and `not_found`.
- **Sources:** P-D3, P-C1 (that a file edit must not stand in for a native
  flip is our own rule, C-7).

### LAY-12 `set_footprint_lock`
- **Class:** live-editor · **kicad-tools:** the file-level equivalent is
  already in kicad-tools (`kct pcb lock-footprints`, `unlock-footprints`) ·
  **KiCad:** 10.0 · **From:** ROADMAP Phase 2
- **Purpose:** lock or unlock footprints, so that placement and routing pushes
  leave them alone.
- **Inputs:** refs, locked (true/false), `dry_run`.
- **Outputs:** per footprint, the locked state before and after.
- **Failure modes:** `not_found`.
- **Sources:** P-D3, P-T7.

### LAY-13 `set_footprint_fields`
- **Class:** live-editor · **kicad-tools:** partial (`kct pcb reannotate` for
  file-level renames) · **KiCad:** 10.0 · **From:** ROADMAP Phase 2
- **Purpose:** change a placed footprint's value, its fields, the visibility
  and position of field text, and its attributes.
- **Inputs:** per ref, the fields to set; `dry_run`.
- **Outputs:** per field, the before and after values.
- **Failure modes:**
  - `not_found`;
  - changing a reference designator is allowed only with `allow_rename`. The
    result warns that the schematic link now differs, and points to
    kicad-tools' file-level rename for keeping both sides in step.
- **Sources:** P-D3, P-T7.

### LAY-14 `remove_footprints`
- **Class:** live-editor · **kicad-tools:** the file-level equivalent is
  already in kicad-tools (`kct pcb remove-footprint`) · **KiCad:** 10.0 (removal);
  adding a footprint from a library is 11 · **From:** ROADMAP Phase 2
- **Purpose:** delete footprints from the open board.
- **Inputs:** refs, `allow_locked`, `dry_run`.
- **Outputs:** the removed footprints, and the copper left unconnected.
- **Failure modes:** `not_found`; `locked_item`.
- **Adding footprints:** on KiCad 10, this goes through SCH-04 or a
  `kct pcb sync-netlist` handoff. It is not specified as a live tool until
  KiCad 11.
- **Sources:** P-D3, P-T7.

### LAY-15 `create_zone`
- **Class:** live-editor · **kicad-tools:** the file-level equivalent is
  already in kicad-tools (`kct zones`, `kct pcb add-zone`) · **KiCad:** 10.0 ·
  **From:** ROADMAP Phase 2
- **Purpose:** add a copper zone or a rule area (keepout) to the open board.
- **Inputs:** an outline polygon (with its frame), the layer or layers, a net
  (for copper), priority, pad-connection style, clearances, the keepout flags
  (for a rule area), and `dry_run`.
- **Outputs:** the new zone as LAY-06 describes it. It is not filled until
  LAY-17 runs, and the result says so.
- **Failure modes:** an unknown net or layer, or an invalid polygon (too few
  points, self-intersecting), gives `invalid_argument`.
- **Sources:** P-D3, P-T7.

### LAY-16 `edit_zone`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 · **From:**
  ROADMAP Phase 2
- **Purpose:** change an existing zone's outline, net, priority, connection
  style or keepout flags, or delete it.
- **Inputs:** the zone ID or name, the changes, and `dry_run`.
- **Outputs:** the zone's settings before and after.
- **Failure modes:** `not_found`, `invalid_argument`, `locked_item`.
- **Sources:** P-D3.

### LAY-17 `fill_zones`
- **Class:** live-editor · **kicad-tools:** partial (kicad-tools' own fill is
  file-level; sign-off uses `kicad-cli pcb drc --refill-zones` on the saved
  file, see REV-02) · **KiCad:** 10.0 refills every zone; a chosen subset is
  11 · **From:** ROADMAP Phase 2
- **Purpose:** recompute the copper fill in the open board.
- **Inputs:** an optional list of zones, which is honoured only on KiCad 11+,
  and a timeout.
- **Outputs:** which zones were refilled, the time taken, and a warning that
  KiCad is busy while it fills.
- **Failure modes:**
  - asking for a subset on KiCad 10 gives `unsupported_on_this_kicad`, unless
    the caller passes `fill_all_instead`;
  - `timeout` (KiCad may still be filling; SES-01 then reports busy).
- **Sources:** P-D3, P-R5.

### LAY-18 `render_board`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  screenshot`, `kct board-view`, MCP `screenshot_board`, `board_view`) ·
  **KiCad:** the KiCad 10 API cannot plot (fact 2) · **From:** ROADMAP Phase 1
- **Purpose:** give an image of the current board, live state included.
- **Inputs:** layers, the nets to highlight, an area, and a format.
- **Outputs:** an image of a [SES-11](#ses-11-snapshot_board) snapshot,
  labelled with the snapshot ID.
- **Failure modes:** `kicad_cli_missing`, `kicad_tools_failed`.
- **Sources:** P-D2, P-T7.

---

## Placement

Compute happens in kicad-tools, on a snapshot. Applying the result is a live
transaction.

### PLC-01 `placement_report`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (MCP
  `evaluate_placement`, `placement_analyze`, `kct placement`) · **KiCad:** n/a
  · **From:** ROADMAP Phase 3
- **Purpose:** score the current placement and list its problems: overlaps,
  parts off the board, decoupling distance and similar, with each problem
  named.
- **Inputs:** a scope (the whole board, the selection, or refs).
- **Outputs:** kicad-tools' scored result for a SES-11 snapshot, with C-8
  provenance.
- **Failure modes:** `kicad_tools_failed`. A missing outline means the report
  cannot pass, and it says so (C-5).
- **Sources:** P-T4, P-T7, P-T8.

### PLC-02 `propose_placement`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  optimize-placement`, MCP `optimize_placement`, `placement_place_unplaced`,
  `placement_suggestions`, `resolve_placement_overlaps`) · **KiCad:** n/a ·
  **From:** ROADMAP Phase 3
- **Purpose:** compute new positions for the whole board, the selection or the
  unplaced parts, without touching the editor.
- **Inputs:** a scope; a strategy as kicad-tools offers it (optimise, place
  unplaced, resolve overlaps); options.
- **Outputs:** a proposal ID; the proposed moves per ref; the score before and
  after; and the snapshot ID it was computed from. Locked parts never appear in
  the moves.
- **Failure modes:** `kicad_tools_failed`. A proposal with no improvement is
  reported as such, not as success.
- **Sources:** P-T4, P-T7, P-T8.

### PLC-03 `apply_placement`
- **Class:** live-editor · **kicad-tools:** n/a (it consumes PLC-02's output)
  · **KiCad:** 10.0 · **From:** ROADMAP Phase 3
- **Purpose:** push a PLC-02 proposal into the open board as one undo step.
- **Inputs:** the proposal ID, or an explicit list of moves; `dry_run`
  (default **true**); `skip_changed`.
- **Behaviour:**
  - Before applying, compare each affected footprint against the snapshot.
  - If the user has moved any of them since, refuse with `stale_input`, or
    with `skip_changed` leave those out.
  - Locked footprints are never moved.
- **Outputs:** a before-and-after summary: the parts moved, the parts skipped
  and why, and the score after.
- **Failure modes:** `stale_input`, `locked_item` (reported, not applied), and
  the C-6 all-or-nothing rule.
- **Sources:** P-D4, P-R1.

### PLC-04 `start_user_move`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 · **From:**
  this spec (an interactive move is documented in the IPC API)
- **Purpose:** hand chosen items to the user, to place with the mouse in
  KiCad.
- **Inputs:** refs.
- **Outputs:** returns at once with the status `user_moving`. SES-01 reports
  KiCad as busy until the user finishes or cancels, and a later LAY-02 shows
  the outcome.
- **Failure modes:** `not_found`, `locked_item`. Other tools return
  `kicad_busy` while the move is in progress, and their message says why.
- **Sources:** P-D3.

---

## Routing

Bulk routing is computed in kicad-tools and pushed live. Small copper edits are
made live.

### RTE-01 `unrouted_report`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  net-status`, MCP `get_unrouted_nets`) · **KiCad:** n/a · **From:** ROADMAP
  Phase 3
- **Purpose:** list the nets that are not fully connected, with the number of
  islands and the estimated length still to route.
- **Inputs:** an optional list of nets, or the selection.
- **Outputs:** kicad-tools' result for a snapshot, with C-8 provenance.
- **Failure modes:** `kicad_tools_failed`.
- **Sources:** P-T5, P-T7.

### RTE-02 `propose_routes`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  route`, `kct route-auto`, `kct retune-lengths`, MCP `route_net`,
  `route_net_auto`) · **KiCad:** n/a · **From:** ROADMAP Phase 3
- **Purpose:** route the nets the caller names, the selection's nets, or the
  whole board, on a snapshot. Existing copper, locked items and the user's
  keepouts are kept.
- **Inputs:** a scope; options as kicad-tools offers them (manufacturer
  profile, differential pairs, length matching).
- **Outputs:** a proposal ID; the result board file; per-net status (routed,
  partly routed, failed); and the snapshot ID.
- **Failure modes:** `kicad_tools_failed`. kicad-tools refuses success when it
  produced no copper, and that refusal is passed through ([P-T5]).
- **Sources:** P-T5, P-T7, P-T8.

### RTE-03 `push_routes`
- **Class:** live-editor · **kicad-tools:** **a live-editor capability already
  in kicad-tools:** `kct ipc push-routes` reads the tracks and vias from a
  routed file and creates them in a running KiCad in one undo transaction.
  Whether openkonnect wraps it, reuses its code, or replaces it is for #2 and
  #6 to decide · **KiCad:** 10.0 · **From:** ROADMAP Phase 3, #4
- **Purpose:** move the new copper from an RTE-02 proposal, or from any routed
  file, into the open board as one undo step.
- **Inputs:** the proposal ID or a file; an optional net filter; `dry_run`
  (default **true**).
- **Behaviour:** add only the copper that is new relative to the snapshot. If
  the live copper in the affected area has changed since the snapshot, refuse
  with `stale_input`.
- **Outputs:** before and after: the tracks and vias added per net, the nets
  completed, and the nets still unrouted.
- **Failure modes:** `stale_input`, C-6 all-or-nothing, and `not_found` for
  nets missing from the live board.
- **Sources:** P-T7, P-D4.

### RTE-04 `add_tracks`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 · **From:**
  ROADMAP Phase 2
- **Purpose:** draw straight or arc track segments.
- **Inputs:** segments, each with a start and end (and for an arc, a midpoint),
  a frame, a layer, a width and a net; `dry_run`.
- **Outputs:** the created items, read back, with their session IDs.
- **Failure modes:** unknown net or layer, zero-length segment, or non-copper
  layer gives `invalid_argument`. A segment that DRC would reject is still
  created, and the result recommends REV-01.
- **Sources:** P-D3.

### RTE-05 `add_vias`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 · **From:**
  ROADMAP Phase 2
- **Purpose:** place vias.
- **Inputs:** positions (with their frame), the net, size and drill, the via
  type where KiCad supports it, and `dry_run`.
- **Outputs:** the created vias, read back.
- **Failure modes:** `invalid_argument` for a drill not smaller than the via
  size, or for an unknown net.
- **Sources:** P-D3.

### RTE-06 `delete_copper`
- **Class:** live-editor · **kicad-tools:** partial (`kct pcb strip` removes
  traces in a file) · **KiCad:** 10.0 · **From:** ROADMAP Phase 2
- **Purpose:** remove tracks and vias, chosen by ID, by net, or by area.
- **Inputs:** a selector, `allow_locked`, `dry_run`.
- **Outputs:** what was removed. The result is read back to confirm the items
  are gone.
- **Failure modes:** a stale ID gives `not_found`. This never deletes items
  other than the ones asked for. `locked_item`.
- **Sources:** P-D3, P-T7.

### RTE-07 `edit_copper`
- **Class:** live-editor · **kicad-tools:** n/a · **KiCad:** 10.0 · **From:**
  ROADMAP Phase 2
- **Purpose:** change the width, net or layer of existing tracks, or the size,
  drill or net of vias.
- **Inputs:** a selector, the changes, `dry_run`.
- **Outputs:** per item, the values before and after.
- **Failure modes:** `not_found`, `invalid_argument`, `locked_item`.
- **Sources:** P-D3.

### RTE-08 `add_stitching_vias`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  stitch`) · **KiCad:** n/a for the compute; the push is through RTE-03 ·
  **From:** P-C2 tool catalogue (routing)
- **Purpose:** add stitching vias that tie plane zones together, computed by
  kicad-tools on a snapshot and pushed with RTE-03.
- **Inputs:** the nets, the spacing, and options as kicad-tools offers them;
  `dry_run`.
- **Outputs:** as RTE-02 and RTE-03.
- **Failure modes:** as RTE-02 and RTE-03.
- **Sources:** P-T7.

### RTE-09 External autorouter round trip
- **Class:** file-level · **kicad-tools:** declined upstream. The request for
  a Freerouting runner, rjwalters/kicad-tools#7991, was closed as not planned
  on 2026-10-27 · **KiCad:** n/a · **From:** P-T4, P-T6
- **Purpose:** route with an external autorouter, and bring back only a
  result made for the same board revision.
- **Status:** **not specified; out of scope.** The kicad-tools operator ruled
  that kct will not run Freerouting or any other external router. Its plan is
  to improve its own router (`kct route` / `route-auto`) instead, and to keep
  working on the board file so that LVS and the fab gates check the board that
  ships. openkonnect follows that ruling and offers no external-router tool.
  If an external router can finish a board that kct cannot, that is reported
  upstream as a gap in kct's router, with the board and the nets that failed.
  It is not a reason to add this tool. See
  [Considered and not specified](#considered-and-not-specified).
- **Sources:** P-T4, P-T6, P-T9.

---

## Review

> **Two-engine sign-off (#6).** A board is *done* only when **both**
> `kct check --mfr <manufacturer>` **and** `kicad-cli pcb drc --refill-zones`
> report **0 errors** on the **saved** file. In-editor checks and snapshot
> checks are quick feedback only. openkonnect never reports a board as done
> from them, from KiCad's in-editor DRC alone, or from a single engine.

### REV-01 `quick_check`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct check`,
  `kct drc`, MCP `get_drc_violations`) · **KiCad:** the IPC docs list no DRC
  request (fact 7) · **From:** ROADMAP Phase 4
- **Purpose:** fast design-rule feedback on the live board, unsaved edits
  included.
- **Inputs:** an optional manufacturer profile; an optional `--only` subset of
  checks, as kicad-tools offers.
- **Outputs:** kicad-tools' findings for a SES-11 snapshot, ready for
  SES-09. Every result carries `signoff: false` and the snapshot ID.
- **Failure modes:** `kicad_tools_failed`. Sub-checks that did not run are
  reported as not run (C-5).
- **Sources:** P-R4, P-T5, P-T7.

### REV-02 `signoff_check`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct check
  --mfr`, `kct readiness`); the second engine is KiCad's own `kicad-cli` ·
  **KiCad:** n/a (runs `kicad-cli`) · **From:** #6
- **Purpose:** the two-engine sign-off on the saved file.
- **Inputs:** the manufacturer profile and `allow_save`.
- **Behaviour:**
  1. Run [SES-04](#ses-04-save_before_external_write) first. If the editor is
     dirty or unknown and saving is not allowed, refuse. Never check a file
     that differs from what the user sees without saying so.
  2. Run both engines on the saved file. The check must not change the saved
     file.
- **Outputs:**
  - per engine: whether it ran, its error and warning counts, and its findings;
  - the file hash checked;
  - the verdict: `pass` only if both engines ran and both report 0 errors;
    `fail` if either reports errors; otherwise `incomplete`.
- **Failure modes:** `kicad_cli_missing` gives `incomplete`, never `pass`;
  `kicad_tools_failed`; SES-04's refusals.
- **Sources:** P-R4, P-T5, P-T7.

### REV-03 `schematic_check`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct erc`,
  `kct erc explain`, which parse and explain `kicad-cli sch erc` output) ·
  **KiCad:** n/a · **From:** P-C2 tool catalogue (ERC)
- **Purpose:** electrical rules check of the saved schematic.
- **Inputs:** the schematic and a severity filter.
- **Outputs:** the violations, with explanations where kicad-tools gives them,
  and C-8 provenance.
- **Failure modes:** `kicad_cli_missing`. A report that cannot be parsed is an
  error, not zero violations.
- **Sources:** P-T1, P-T7.

### REV-04 `mistake_scan`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  detect-mistakes`, MCP `detect_mistakes`, `list_mistake_categories`) ·
  **KiCad:** n/a · **From:** ROADMAP Phase 4
- **Purpose:** find common design mistakes, with explanations, on a snapshot.
- **Inputs:** optional categories.
- **Outputs:** findings, ready for SES-09, plus kicad-tools' coverage report
  of which checks ran.
- **Failure modes:** incomplete coverage is reported, never hidden.
- **Sources:** P-T5, P-T7.

### REV-05 `clearance_probe`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (MCP
  `measure_clearance`) · **KiCad:** n/a · **From:** #5 (for example "check
  clearance around these parts")
- **Purpose:** measure the clearance between given items, typically the
  current selection.
- **Inputs:** two sets of refs, `REF.PAD` or nets; usually SES-08's output.
- **Outputs:** the smallest distances and where they occur, as findings for
  SES-09.
- **Failure modes:** `not_found`; `kicad_tools_failed`.
- **Sources:** P-R3, P-T7.

### REV-06 `explain_finding`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  explain`, `kct erc explain`, MCP `get_design_intent`) · **KiCad:** n/a ·
  **From:** ROADMAP Phase 4
- **Purpose:** explain a finding in context: the rule behind it, why it
  matters, and any design intent recorded for the items involved.
- **Inputs:** one finding.
- **Outputs:** kicad-tools' explanation.
- **Failure modes:** an unknown finding type is reported as such.
- **Sources:** P-T7.

### REV-07 `recheck_touched`
- **Class:** live-editor (it knows what an edit touched) · **kicad-tools:**
  upstream gap (G2: a check limited to a region or a set of references; `kct
  check --only` limits *which* checks run, not *where*) · **KiCad:** 10.0 ·
  **From:** ROADMAP Phase 4
- **Purpose:** after an agent edit, check only the area that the edit changed,
  and report the findings that are new or resolved compared with before the
  edit.
- **Inputs:** a change ID (C-6 or SES-17), or a list of items.
- **Outputs:** new findings, resolved findings, and the unchanged count.
  Always `signoff: false`.
- **Failure modes:** until G2 lands, the tool checks the whole board and says
  so.
- **Sources:** P-T7.

### REV-08 `board_parity`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct check`
  LVS, `--netlist-sync`, copper LVS) · **KiCad:** n/a · **From:** P-T1
- **Purpose:** check that the board's copper and footprints match the
  schematic netlist: shorts, opens and missing or extra parts.
- **Inputs:** the board (a snapshot or the saved file) and the schematic
  (saved).
- **Outputs:** the mismatches, as findings for SES-09.
- **Failure modes:** no schematic means the check is *not run*. It does not
  pass (C-5).
- **Sources:** P-T1, P-T5, P-T7.

---

## Manufacturing

> **Two-engine sign-off (#6)** applies here too. Outputs are made from the
> **saved** file, after [SES-04](#ses-04-save_before_external_write). The
> pre-fab check needs `kct check --mfr` and `kicad-cli pcb drc --refill-zones`
> both at **0 errors**. openkonnect never treats in-editor DRC as sign-off.
> KiCad 10's API cannot plot (fact 2), so all output is delegated.

### MFG-01 `prefab_check`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  readiness`, `kct audit`, MCP `validate_assembly_bom`) · **KiCad:** n/a ·
  **From:** ROADMAP Phase 6, #6
- **Purpose:** decide whether the saved board is ready to order.
- **Inputs:** the manufacturer profile, and the assembly options.
- **Outputs:** REV-02's two-engine verdict; the count of unrouted nets, which
  must be 0; missing required fields (for example a part number, for
  assembly); BOM validation; and an overall verdict of pass, fail or
  incomplete.
- **Failure modes:** as REV-02. An empty BOM, or one that is all
  do-not-populate, is not "assembly ready" ([P-T5]).
- **Sources:** P-R4, P-T5, P-T7.

### MFG-02 `export_fab_package`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  export`, MCP `export_gerbers`, `export_bom`, `export_assembly`) ·
  **KiCad:** n/a · **From:** ROADMAP Phase 6
- **Purpose:** produce Gerbers, drill files, the BOM, the placement (CPL) file,
  a project archive and a manifest from the board that is open.
- **Inputs:** the manufacturer profile, the output directory, `allow_save`, and
  `allow_unchecked`.
- **Behaviour:** run SES-04, then MFG-01. If MFG-01 does not pass, refuse,
  unless `allow_unchecked` is set; the manifest then records the package as
  unchecked.
- **Outputs:** only the files this call wrote, never stale files already in
  the directory ([P-T5]); the source file hash; and the MFG-01 verdict.
- **Failure modes:** no copper Gerbers produced is a failure; SES-04's
  refusals; `kicad_tools_failed`.
- **Sources:** P-T5, P-T7.

### MFG-03 Other output formats
- **Class:** file-level · **kicad-tools:** upstream gap (G4: PDF, SVG plots,
  STEP and other 3D, IPC-2581, ODB++ are not wrapped by kicad-tools; `kct
  render` covers previews only) · **KiCad:** n/a · **From:** P-C2 and P-K1 tool
  catalogues (export)
- **Purpose:** documentation, mechanical and alternative fabrication outputs.
- **Status:** **deferred** until kicad-tools covers it, per #6. This spec does
  not wrap `kicad-cli` directly for these.
- **Sources:** P-C2, P-K1, P-T6.

### MFG-04 `fab_cost_estimate`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  estimate`) · **KiCad:** n/a · **From:** P-C2 tool catalogue (manufacturing)
- **Purpose:** estimate the cost of fabrication and assembly.
- **Inputs:** the quantity, the manufacturer, and the assembly options.
- **Outputs:** kicad-tools' itemised estimate, with its stated assumptions.
- **Failure modes:** `kicad_tools_failed`.
- **Sources:** P-T7.

### MFG-05 `panelize`
- **Class:** file-level · **kicad-tools:** already in kicad-tools (`kct
  panel`) · **KiCad:** n/a · **From:** this spec
- **Purpose:** make a manufacturing panel from the saved board.
- **Inputs:** the layout options, as kicad-tools offers them.
- **Outputs:** the path of the panel file. The panel is never opened in place
  of the user's board.
- **Failure modes:** `kicad_tools_failed`.
- **Sources:** P-T7.

---

## Considered and not specified

Each of these was weighed, and left out of v0 for the reason given.

| Idea | Decision |
|------|----------|
| Loading tool groups on demand, to save context | Not in v0. kicad-tools' audit ([P-T2]) declined it for a surface of about 50 tools. Revisit if the implemented tool list grows large enough for listing it to cost noticeable context. |
| A free-text store of design rules that the agent must follow | Not specified. kicad-tools' audit ([P-T3]) found that its existing configuration and intent mechanisms cover it. |
| GUI automation (accessibility trees, clicking, screen capture) | Out of scope. openkonnect is IPC-only (ROADMAP principle 6, #7). |
| Parts search, datasheets, reference-circuit templates, symbol and footprint library authoring, project creation | File-level, and kicad-tools' job (`kct parts`, `kct datasheet`, MCP `adapt_pattern`, `kct lib`, `kct footprint`, `kct init`). openkonnect does not reimplement them. |
| Server call logs for the agent's own debugging | Not in v0. kicad-tools' MCP has `get_recent_calls`. A later spec can add one if it is needed. |
| Live schematic reading and editing | Waits for KiCad 11 (SCH-06, SCH-07). |
| An external autorouter round trip, such as Freerouting (RTE-09) | Out of scope. kicad-tools declined a runner for it ([P-T9]): kct will not call any external router, and improves its own router instead. Boards that kct cannot route fully are reported upstream as router gaps, not handled by an external router. |

## Upstream gaps for kicad-tools

These are file-level capabilities that this spec needs and kicad-tools lacked
when the spec was written. Under #6, the open ones are filed on
rjwalters/kicad-tools and listed in `docs/architecture.md`. That work belongs
to #6, not to this PR. A row marked resolved upstream is kept so that the IDs
stay stable; it is not to be filed.

| ID | Gap | Needed by |
|----|-----|-----------|
| G1 | A stable, versioned finding format shared by the commands that emit findings | SES-09, SES-10 |
| G2 | A check limited to a region or a set of references | REV-07 |
| G3 | A semantic diff between two board files. **Resolved upstream:** `kct pcb diff` (kicad-tools 0.24.0, kicad-tools #6417) | SES-12 |
| G4 | Plot, 3D and alternative fabrication exports | MFG-03 |

An earlier draft listed a fifth gap, a Freerouting runner. That request was
already filed as rjwalters/kicad-tools#7991, and it was closed as not planned on
2026-10-27 ([P-T9]). It is not an open gap. #6 should not file it again or list
it in `docs/architecture.md`. RTE-09 is out of scope as a result.

G3 is not an open gap either. kicad-tools 0.24.0 already has `kct pcb diff`
(kicad-tools #6417, [P-T10]), which compares two board files item by item and
can emit JSON. #6 should not file it; SES-12 builds on it.

## Coverage of #4 and #5

| Request | Entry |
|---------|-------|
| #4 `session_status`: running, documents, version, per-document dirty state | [SES-01](#ses-01-session_status) |
| #4 `sync_from_disk`, refusing when dirty unless discard is explicit | [SES-05](#ses-05-sync_from_disk) |
| #4 `save_before_external_write`, refusing when dirty unless save is allowed | [SES-04](#ses-04-save_before_external_write) |
| #4 the handoff protocol, plus an end-to-end test around one `kct` command | [SES-06](#ses-06-run_kct_handoff-and-the-handoff-protocol) |
| #4 fail fast when KiCad is not running or IPC is disabled | [SES-07](#ses-07-fail-fast-connection-errors-contract), C-3, C-4 |
| #5 `get_selection` returning refdes, nets, `REF.PAD`, and a bbox in mm board coordinates | [SES-08](#ses-08-get_selection), C-1 |
| #5 `show_findings` from kicad-tools JSON, selecting, highlighting and zooming, without copying a schema | [SES-09](#ses-09-show_findings) (zoom and highlight: [SES-14](#ses-14-focus_view), [SES-15](#ses-15-highlight_net--clear_highlight)) |
| #5 the round-trip test | [SES-10](#ses-10-selection-to-kct-and-back-round-trip-contract) |

## Provenance

Every source except P-T10 was accessed on **2026-10-27**; P-T10 was accessed
on **2026-10-28**. No black-box runs of Konnect or of any other project's binaries
were made. The only commands run were kicad-tools' own CLI help (`kct`,
version 0.24.0, MIT), for the "already in kicad-tools" column, and on
2026-10-28 `kct --version` and `kct pcb diff --help`, to confirm G3 resolved
upstream and fill SES-12's kicad-tools column.

**Attestation.** The author of this spec did not open, clone, search or recall
the source code of mixelpixx/Konnect, its forks, or any AGPL or closed-source
successor, including Konnect-EDA. The author also did not read KiCad's GPL
source or KiCAD-MCP-Server's source. The author did not implement any
capability in this spec, and wrote no code for this repo. Every description is
in the author's own words; no tool description, schema or prompt text was
copied.

| ID | Source | URL | Used for |
|----|--------|-----|----------|
| P-R1 | openkonnect ROADMAP | https://github.com/rjwalters/openkonnect/blob/main/ROADMAP.md | Areas, phases, principles |
| P-R2 | openkonnect #4 (session and file sync) | https://github.com/rjwalters/openkonnect/issues/4 | SES-01, SES-04 to SES-07 |
| P-R3 | openkonnect #5 (selection and findings) | https://github.com/rjwalters/openkonnect/issues/5 | SES-08 to SES-10 |
| P-R4 | openkonnect #6 (kicad-tools boundary) | https://github.com/rjwalters/openkonnect/issues/6 | Classification rule, two-engine sign-off |
| P-R5 | openkonnect #7 (KiCAD-MCP-Server, IPC-only) | https://github.com/rjwalters/openkonnect/issues/7 | IPC-only scope, use of its docs |
| P-C1 | Konnect public README (repo archived 2026-10-26) | https://github.com/mixelpixx/Konnect/blob/main/README.md | Overall capability areas; KiCad 10 setup notes (enabling the API); that some board edits need a recent 10.0.x |
| P-C2 | Konnect published tool catalogue, `tool-directory.md` | https://github.com/mixelpixx/Konnect/blob/main/tool-directory.md | Tool names and one-line purposes, read as a checklist of capability areas. Only the tables of names and purposes were read. The page links to source files; those links were not followed, and its "Implementation notes" appendix was not read |
| P-C3 | Konnect repository metadata (archived, AGPL-3.0, description) | https://github.com/mixelpixx/Konnect | Licence and archive status |
| P-K1 | KiCAD-MCP-Server public README (MIT) | https://github.com/mixelpixx/KiCAD-MCP-Server/blob/main/README.md | Its lifecycle tools (save, reload, dirty check), the IPC preference name, and its export list |
| P-D1 | KiCad IPC API overview | https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/ | Synchronous model, busy and retry, one connection, multiple instances |
| P-D2 | KiCad IPC API for add-on developers | https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/ | KiCad 10 limits: GUI only, no export, PCB editor only; socket naming; instance token |
| P-D3 | kicad-python docs: Board | https://docs.kicad.org/kicad-python-main/board.html | Board reads and edits and their version notes (flip 10.0.6, visible layers 10.0.6, items by net 10.0.1, design rules and subset refill in 11, interactive move) |
| P-D4 | kicad-python docs: Project and document management | https://docs.kicad.org/kicad-python-main/project.html | Commits as single undo steps, selection, save, revert, document as text, modified flag (KiCad 11) |
| P-D5 | kicad-python docs: KiCad | https://docs.kicad.org/kicad-python-main/kicad.html | Version, open documents, the unstable "run action" request |
| P-D6 | kicad-python docs: Schematic | https://docs.kicad.org/kicad-python-main/schematic.html | The schematic API is marked KiCad 11 |
| P-D7 | kicad-python release history | https://pypi.org/project/kicad-python/ | The latest release is 0.8.0 (2026-08-30). The docs above are the development edition |
| P-T1 | kicad-tools: Konnect connectivity audit | https://github.com/rjwalters/kicad-tools/blob/6a844af6d/docs/research/konnect-connectivity-audit.md | SCH-02, REV-03, REV-08. Carries an explicit no-source statement |
| P-T2 | kicad-tools: toolset-economy audit (item 1) | https://github.com/rjwalters/kicad-tools/blob/6a844af6d/docs/konnect-item1-toolset-economy-audit.md | On-demand toolsets declined. States that it worked from public descriptions, and that no Konnect source, prompts or schema text was reproduced |
| P-T3 | kicad-tools: design-rules audit (item 8) | https://github.com/rjwalters/kicad-tools/blob/6a844af6d/docs/konnect-item8-design-rules-audit.md | Free-text rule store not specified. States that it uses the mechanism as a reference only and reproduces no code, prompts or schema text. It does **not** explicitly say the source was unread, so it was used only for kicad-tools' own facts |
| P-T4 | kicad-tools: Konnect v0.8 to v0.13 delta | https://github.com/rjwalters/kicad-tools/blob/6a844af6d/docs/research/konnect-v013-delta.md | Placement, visual baselines, DSN/SES provenance. Explicit statement: nothing copied **or read** from Konnect's source; its sources were the public release notes and ROADMAP |
| P-T5 | kicad-tools: truth-contract audit | https://github.com/rjwalters/kicad-tools/blob/6a844af6d/docs/research/konnect-truth-contract-audit.md | C-5 and kicad-tools' observed-result fixes. Its statement says "ideas only… nothing here is copied" and points to P-T1. It does **not** explicitly say the source was unread, so it was used only for facts about kicad-tools' own outputs |
| P-T6 | kicad-tools: KiCAD-MCP-Server evaluation, upstream `628d320f` | https://github.com/rjwalters/kicad-tools/blob/6a844af6d/docs/research/kicad-mcp-server-evaluation.md | KiCAD-MCP-Server tool inventory and the kicad-tools gaps it identified. One of them, the Freerouting runner (#7991), was later declined; see P-T9. Its author read that MIT project's source; this spec uses only its capability-level tables |
| P-T7 | kicad-tools CLI help, `kct` 0.24.0 (local run of `kct <command> --help`) | https://github.com/rjwalters/kicad-tools (main at `6a844af6d`) | Every "already in kicad-tools" citation |
| P-T8 | kicad-tools MCP tool registry (names only) | https://github.com/rjwalters/kicad-tools/tree/6a844af6d/src/kicad_tools/mcp/tools | MCP tool names cited in the kicad-tools column |
| P-T9 | kicad-tools #7991 (Freerouting runner), closed as not planned, with the operator's ruling of 2026-10-27 | https://github.com/rjwalters/kicad-tools/issues/7991 | RTE-09 out of scope, and G5 withdrawn: kct runs no external router, and boards it cannot route become router-gap reports |
| P-T10 | kicad-tools `kct pcb diff --help` and package metadata, kicad-tools 0.24.0, plus kicad-tools #6417 (accessed 2026-10-28) | https://github.com/rjwalters/kicad-tools/issues/6417 | G3 resolved upstream; SES-12 kicad-tools column |

**Unreachable or not used.**
- `https://docs.kicad.org/kicad-python/` (2026-10-27) returned a page with no
  navigation links. `https://docs.kicad.org/kicad-python/main/board.html` and
  `.../main/kicad.html` returned 404. The development edition at
  `https://docs.kicad.org/kicad-python-main/` was used instead (P-D3–P-D6).
  There is no edition matching the KiCad 10.0.x release, so the version notes
  need checking against a real KiCad 10 (see
  [What KiCad 10's IPC API allows](#what-kicad-10s-ipc-api-allows)).
- Konnect's `ROADMAP.md` was downloaded but not read, and is not relied on.
- No page that this spec needed was otherwise unreachable.
