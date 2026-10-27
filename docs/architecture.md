# Architecture: the boundary with kicad-tools

**Decision (issue #6, 2026-10-27).** openkonnect is built on
[kicad-tools](https://github.com/rjwalters/kicad-tools). Each capability has one
implementation, in one repository. This page sets out where the line falls,
how a board is signed off, and what openkonnect has asked kicad-tools to add.

## The rule

**File-level capability comes from kicad-tools.** If something can be done
offline on a `.kicad_*` file, it belongs in kicad-tools and openkonnect calls
it. openkonnect does not keep a second copy.

| kicad-tools owns (file-level) | openkonnect owns (live-editor) |
|---|---|
| Parsing `.kicad_pcb`, `.kicad_sch` and project files | The IPC session with a running KiCad 10 ([ipc-client.md](ipc-client.md)) |
| Analysis: nets, net status, board metrics, mistake detection | File sync between the open editor and file-based tools (#4): dirty state, save before an external write, reload after it |
| Design checks: `kct check --mfr`, including DRC and LVS (netlist sync, copper LVS) | Selection and highlight (#5): what the user has selected, and pointing the user at findings |
| Routing: `kct route`, `kct route-auto` | Interactive edits in the open document, through IPC transactions, with undo |
| Placement: `kct placement`, `kct optimize-placement` | |
| Exports: `kct export` (Gerbers, drill, BOM, CPL), `kct render` | |

The per-capability classification lives in
[`spec/capabilities.md`](../spec/capabilities.md). Every entry there carries a
**Class** (file-level or live-editor) and a **kicad-tools** field, and each
classification follows this rule.

When a live-editor feature needs file-level logic (for example, a check on
the board that is open), openkonnect gets the board to a file and runs
kicad-tools on it. There are two ways to do that:

- a snapshot of the live board;
- the handoff protocol from #4: save, let `kct` write, then reload.

openkonnect then moves the result back into the editor.

## Gaps go upstream

When openkonnect needs a file-level capability that kicad-tools lacks, the
gap is **filed on rjwalters/kicad-tools**. openkonnect does not implement it
here. Until the gap is closed, the capability that depends on it either waits
(deferred in the spec) or runs in a reduced form that says what it could not
do. It never runs a local reimplementation.

### Upstream requests

These come from the spec's
[Upstream gaps](../spec/capabilities.md#upstream-gaps-for-kicad-tools) table.

| ID | Gap | Needed by | Upstream |
|----|-----|-----------|----------|
| G1 | A stable, versioned finding format shared by the commands that emit findings | SES-09, SES-10 | [rjwalters/kicad-tools#8015](https://github.com/rjwalters/kicad-tools/issues/8015) (open) |
| G2 | A check limited to a region or a set of references (`kct check --only` limits *which* checks run, not *where*) | REV-07 | [rjwalters/kicad-tools#8016](https://github.com/rjwalters/kicad-tools/issues/8016) (open) |
| G3 | A semantic diff between two board files | SES-12 | [rjwalters/kicad-tools#6417](https://github.com/rjwalters/kicad-tools/issues/6417) (completed: `kct pcb diff OLD NEW`, shipped in 0.24) |
| G4 | Plot, 3D and alternative fabrication exports (PDF and SVG plots, STEP, IPC-2581, ODB++) | MFG-03 | [rjwalters/kicad-tools#8017](https://github.com/rjwalters/kicad-tools/issues/8017) (open) |

G3 was not filed again. kicad-tools already ships it as `kct pcb diff`, which
pairs items by reference, by normalised content and by uuid, and has JSON
output. SES-12 should be built on it. The spec still lists G3 as a gap, and
that entry should be updated in a later spec revision.

**The Freerouting runner is not a gap.** An earlier draft of the spec listed
one. That request was already filed upstream as
[rjwalters/kicad-tools#7991](https://github.com/rjwalters/kicad-tools/issues/7991)
and was closed as not planned on 2026-10-27. kct will not call an external
router; it improves its own router instead. If kct cannot fully route a board,
that is reported upstream as a router gap. RTE-09 (an external autorouter
round trip) is out of scope.

Add new requests to this table when they are filed.

## Sign-off is two-engine, on the saved file

openkonnect never reports a board as done on the strength of KiCad's
in-editor DRC alone. In-editor DRC is a quick check for use while editing.
Manufacturing sign-off needs **both** of these, run on the **saved file**:

1. `kct check --mfr` at 0 errors, and
2. `kicad-cli pcb drc --refill-zones` at 0 errors.

Both must run and both must pass. Following the spec's C-5 rule, a check that
could not run (for example, `kicad-cli` is missing, or the board could not be
saved) is reported as *not run*. A verdict that includes a not-run check is
*incomplete*, **never a pass**.

## IPC edits must leave a board kicad-tools accepts

Interactive edits made through IPC must leave the board in a state that the
kicad-tools checks accept. An edit that is fine in the editor but that kct
rejects, cannot parse or reports as a new violation counts as a broken edit.

This is verified on files, never on editor state:

- The board is saved (or snapshotted) and kicad-tools runs on that file. The
  in-editor view of the board is not evidence.
- Edit-scoped re-checks (REV-07) compare findings before and after an edit,
  but always report `signoff: false`. Only the two-engine run above on the
  saved file signs a board off.

## Version pin and the kicad-tools-main job

`pyproject.toml` pins `kicad-tools>=0.24,<0.25`, the minor series resolved in
`uv.lock`. The regular `lint` and `test` CI jobs run against that locked
release.

The `kicad-tools-main` job in `.github/workflows/ci.yml` installs kicad-tools
from `git+https://github.com/rjwalters/kicad-tools@main` over the locked
environment and runs `pytest -rs`. It runs on every push and pull request,
and also weekly on a schedule. It is `continue-on-error` and is not a required
check, so it never blocks a merge.

If that job fails, do one of the following:

- if kicad-tools main broke an API or behaviour that openkonnect relies on,
  **file an issue on rjwalters/kicad-tools** with the failing test and the
  commit it ran against (the job log prints the git source and its version);
- if the change is intended, **adjust openkonnect and the pin** in
  `pyproject.toml` (then `uv lock`) when the new release ships.
