# Handoff protocol: file-based writes next to an open KiCad

kicad-tools (`kct`) reads and writes `.kicad_pcb` files on disk. When the same
board is open in KiCad, a file write risks two things:

1. **Stale editor:** `kct` rewrites the file, but the editor still shows the old
   board, and will save it over `kct`'s output later.
2. **Clobbered edits:** the editor has unsaved changes, and the file write
   ignores them, or reloading the editor throws them away.

The handoff protocol closes both. It is spec entry SES-06
(`spec/capabilities.md`), built on SES-01, SES-04 and SES-05. Use it whenever a
file-based tool writes a board that is open in KiCad. `run_kct_handoff` runs
it for you. Any other tool can follow the same steps by hand with
`save_before_external_write` and `sync_from_disk`.

## The steps

1. **Status.** `session_status`: is KiCad reachable, and which board is open?
2. **Save.** `save_before_external_write`. Stop if it refuses. Record `sha256`
   (the file's hash) and `editor_sha256` (the editor content's hash) from the
   result.
3. **Write.** Run the file-based command on the saved file.
4. **Check the command.** If it failed or timed out, stop. Do not reload. The
   result says whether the file changed (a stopped command may leave a partial
   file).
5. **Check the file.** If its hash is the one from step 2, skip the reload.
6. **Reload.** `sync_from_disk` with `expected_hash` (the file's hash after
   step 3) and `editor_hash` (`editor_sha256` from step 2). `run_kct_handoff`
   also applies the **snapshot rule** below, so a clean handoff reloads
   without `discard_unsaved`. If the user edited the board while the command
   ran, the reload refuses with `document_dirty`, and the result reports both
   the command's outcome and the refusal.
7. **Report.** One record per step, the command's exit code and JSON output,
   and the final document state.

### The snapshot rule (`run_kct_handoff` only)

Within one `run_kct_handoff` call, step 2 saves the editor (or proves the
editor text byte-identical to the file) and records the hash of that exact
content. This snapshot is taken by the tool itself; it is never an argument
and no caller value can stand in for it. At step 6, openkonnect reads the
editor text fresh:

- **It hashes to the snapshot:** nothing changed in KiCad while the command
  ran, so the editor holds only what step 2 saved. The reload goes ahead
  without `discard_unsaved`, and the result reports `snapshot_matched: true`
  and `discarded_unsaved: false`.
- **It differs from the snapshot:** someone edited the board during the
  command. The reload refuses with `document_dirty`, changes nothing, and
  reports the command's result too, so you can choose what to keep.
  `discard_unsaved` overrides this: the reload goes ahead and the result says
  `discarded_unsaved: true`.
- **No snapshot** (KiCad did not serve the text at step 2, or it did not match
  the saved file): the reload refuses with `dirty_state_unknown` unless
  `discard_unsaved` is set.

A plain `sync_from_disk` call has no snapshot. There, `editor_hash` can only
detect an edit (`document_dirty`); after an external write an unchanged editor
is still `dirty_state_unknown`, and the reload needs `discard_unsaved`.

**The window that remains.** If, while the command runs, the human saves an
edit from KiCad and then undoes it exactly, the editor matches the snapshot
again, while the file now holds the human's save (which replaced the
command's output). The snapshot matches, so the reload loads the human's
saved file into the editor. The human's edit is kept on disk and in the
editor; the command's output, already overwritten by that save, is not. No
read of KiCad 10's API can tell this case apart from a clean run.

The reload is **not an undo step**. Ctrl+Z in KiCad will not bring back the
editor's earlier state. To go back, restore the earlier file and run
`sync_from_disk` again.

## Dirty state: the tri-state

Every document carries `dirty` (`true`, `false` or `"unknown"`) and
`dirty_basis`:

| `dirty_basis` | Meaning | Where it comes from |
|---|---|---|
| `kicad_flag` | KiCad's own "modified since last save" flag | KiCad 11 only. Not produced on KiCad 10 |
| `content_comparison` | Decided by comparing the editor's native-format text (read over IPC) with a reference | `save_before_external_write`, `sync_from_disk`, `run_kct_handoff` |
| `none` | No documented signal decided it | `session_status` on KiCad 10, and whenever a comparison is inconclusive |

**`unknown` counts as dirty.** Every tool that protects unsaved work refuses on
`unknown` exactly as it does on `true`. `false` is never guessed.

How `content_comparison` decides, from the editor text that kicad-python's
`Board.get_as_string()` returns:

- **Against the saved file:** `false` only if the editor text is
  byte-identical to the file, since then nothing in the editor is missing from
  the file. Any difference gives `unknown`, because it may be unsaved work or
  only a formatting difference between KiCad's serializer and whatever wrote
  the file.
- **Against a baseline (`editor_hash`):** the hash of the editor text recorded
  right after a save. Changed since then gives `true`: someone edited the
  board in KiCad after the save. This is what lets step 6 detect an edit made
  while the command ran. Unchanged gives `unknown`, never `false`, unless the
  editor text is also byte-identical to the file now.

`false` comes only from the first rule: openkonnect reads the editor text and
the file during the same call and they are byte-identical. No hash a caller
passes (`editor_hash`, `expected_hash`) can produce `false`; a caller hash can
only move the result to `true` or `unknown`. A recorded hash cannot prove
clean because the file may have changed under it: a human save in KiCad
followed by an edit and an undo leaves the editor matching the baseline while
it differs from the file, and a reload would silently discard that state
(issue #15).

If KiCad does not serve the text, dirty is `unknown` with basis `none`.

`session_status` stays cheap. It does not transfer the whole board, so it
reports `unknown`/`none` for every document on KiCad 10.

## KiCad 10 limits

Checked against kicad-python 0.8.0's installed package (MIT) and the KiCad
IPC API documentation:

| Need | kicad-python 0.8 | Request | Used by |
|---|---|---|---|
| Save the board | `Board.save()` | `SaveDocument` | `save_before_external_write` |
| Reload from file | `Board.revert()` | `RevertDocument` | `sync_from_disk` |
| Read the editor as text | `Board.get_as_string()` | `SaveDocumentToString` | dirty state, verification |
| "Modified since save" | none | KiCad 11 | not used; dirty is `unknown` or decided by content comparison |
| Schematic save, reload or read | none | KiCad 11 | schematic is never saved or reloaded |
| Viewport, zoom or layer visibility | none | none | `view_kept` is always `"unknown"` |

Only `src/openkonnect/backend.py` calls these. Nothing works around a gap with
GUI automation.

What this means on KiCad 10:

- **The schematic.** It cannot be inspected, saved or reloaded over IPC.
  `session_status` reports its path as `inferred_from_project`, with `open`
  and `dirty` set to `unknown`. `save_before_external_write` refuses it with
  `unsupported_on_this_kicad` unless you pass `schematic_unverified_ok`.
  `sync_from_disk` reports it as `manual_reload_required` with instructions.
  `run_kct_handoff` covers the board only.
- **The board.** After an external write, the editor and the file normally
  differ, so a plain `sync_from_disk` without `discard_unsaved` refuses with
  `dirty_state_unknown`: KiCad 10 cannot say whether the editor holds unsaved
  work. Pass `editor_hash` (the `editor_sha256` from the save step) so that an
  edit made since is refused with `document_dirty`. `discard_unsaved` reloads
  in either case and discards that edit; it never reports the edit first.
  `run_kct_handoff` does not need `discard_unsaved` for a clean run: it uses
  its own snapshot (see "The snapshot rule").
- **Verification.** After a reload, openkonnect reads the editor back. If its
  text equals the file, `verified` is `true`. A file written by another tool
  is usually formatted differently from KiCad's own output, so `verified` is
  then `"unverified"`. That is not an error. `editor_changed` says whether the
  editor's content changed at all.
- **The view.** No documented request reads or sets zoom or pan, so
  `view_kept` is `"unknown"`.

These were verified against the installed client and the docs, not against a
running KiCad. The live test below is the check against a real editor.

## Flags

| Flag | Tool | Default | Effect |
|---|---|---|---|
| `allow_save` | `save_before_external_write`, `run_kct_handoff` | false | Save a board that is dirty or `unknown`. Without it, such a board is refused and nothing is saved |
| `schematic_unverified_ok` | `save_before_external_write` | false | You state that the schematic is saved or closed. The result records that you asserted it |
| `discard_unsaved` | `sync_from_disk`, `run_kct_handoff` | false | Reload even if the editor may hold unsaved work, which is then lost. Not needed for a clean `run_kct_handoff`. With it, an edit made during the command is discarded without being reported first |
| `expected_hash` | `sync_from_disk` | none | Refuse with `stale_input` if the file's hash differs (a third party may have written it) |
| `editor_hash` | `sync_from_disk` | none | Baseline for content comparison; normally `editor_sha256` from the save. It can only show the editor changed (`true`) or leave `unknown`, never give `false` |
| `documents` | all three | the board | `"board"`, `"schematic"`, `"all"` or a list. The default is what KiCad reports open, which on KiCad 10 is the board |
| `timeout_s` | `run_kct_handoff` | 300 | Bound on the `kct` command, at most 3600 s. On timeout the command and its children are killed and the editor is not reloaded |

## `run_kct_handoff`

`command` is an argv list **without** `kct`, for example
`["route", "{board}", "-o", "{board}"]`. It is never passed to a shell. The
token `{board}` is required, and is replaced by the open board's path, so the
command writes the file KiCad has open. The first tokens must match the
allow-list of known board-writing commands (`KCT_ALLOW_LIST` in
`src/openkonnect/tools/run_kct_handoff.py`): `route`, `route-auto`,
`optimize-traces`, `optimize-placement`, `stitch`, `zones add`, `zones batch`,
`zones fill`, `fix-vias`, `fix-silkscreen`, `fix-footprints`, `fix-drc`,
`repair-clearance` and `place-silk-refs`. Anything else gives
`invalid_argument` before KiCad is contacted.

Each IPC step runs under the normal per-call bound (`--timeout`). The command
runs under `timeout_s`. The session's connection is not held while it runs, so
other tools stay responsive.

A failure names the step in `failed_step` and keeps the step records so far.

## When a step refuses: recovery

| Refusal | What it means | What to do |
|---|---|---|
| `dirty_state_unknown` at save | openkonnect cannot rule out unsaved edits | Save in KiCad, or pass `allow_save` to let openkonnect save them |
| `document_dirty` at save | The editor holds edits not on disk | Same as above |
| `unsupported_on_this_kicad` (schematic) | The schematic cannot be checked on KiCad 10 | Save or close it in KiCad, then pass `schematic_unverified_ok`, or request only the board |
| `save_failed` | KiCad refused to save, or reported success but the file did not change | Check the path and permissions, and any open dialog in KiCad. Do not run the file tool |
| `kicad_tools_failed` / `timeout` at the command | The command failed, was stopped, or could not be run (step outcome `failed_to_start` or `runner_error`) | The editor was not touched. If `file_changed` is true, restore the file (it may be partial) or rerun the command |
| `document_dirty` at reload | The editor changed since the save: in `run_kct_handoff`, the user edited during the command (the editor no longer matches the step-2 snapshot). The file holds the command's output, and the editor holds the user's edits. The command's result is reported too | Choose one: save from KiCad (keeps the edits, drops the command's output), or `sync_from_disk` with `discard_unsaved` (keeps the output, drops the edits). Then rerun the handoff if needed |
| `dirty_state_unknown` at reload | The editor differs from the file and openkonnect cannot prove it holds nothing the file lacks. This is the normal result of a plain `sync_from_disk` after an external write; in `run_kct_handoff` it happens only when step 2 could not take a snapshot | Save from KiCad to keep the editor's state, or pass `discard_unsaved` if the editor holds nothing you need. With `discard_unsaved` nothing is reported first: an edit made since, if any, is discarded |
| `stale_input` | The file's hash is not the expected one | Something else wrote the file. Inspect it before syncing |
| `not_found` | The board file is missing | Restore it. The editor is untouched |
| `reload_failed` | KiCad rejected the file (for example it does not parse). `editor_state: "previous"` means the editor still holds its earlier state | Fix or restore the file, then sync again |

Every tool also fails fast with the shared connection errors (SES-07):
`kicad_not_running`, `ipc_disabled` (naming the preference that enables the
API), `ipc_unreachable`, `kicad_busy`, `timeout`, `instance_changed` and
`no_document`.

## Tests

- Unit tests, run in CI against the fake backend:
  `tests/test_save_before_external_write.py`, `tests/test_sync_from_disk.py`,
  `tests/test_run_kct_handoff.py`.
- End to end, `live_kicad` marker: `tests/live/test_live_handoff.py` wraps a
  real `kct route` around `tests/fixtures/board.kicad_pcb` open in KiCad 10,
  then restores the fixture. It skips when KiCad is not reachable. Run it with
  `uv run pytest -m live_kicad -rs`.
