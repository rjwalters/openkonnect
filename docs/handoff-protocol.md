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
   step 3), `editor_hash` (`editor_sha256` from step 2) and `editor_file_hash`
   (`sha256` from step 2). If the user edited the board while the command ran,
   this refuses with `document_dirty`.
7. **Report.** One record per step, the command's exit code and JSON output,
   and the final document state.

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
  while the command ran, without `discard_unsaved`. A baseline alone proves
  nothing clean: unchanged gives `false` only when the baseline is bound to the
  file, that is `editor_file_hash` (the file's `sha256` recorded with the
  baseline) equals a fresh read of the file, or the file is the one the
  caller's tool wrote (`expected_hash`, which is checked first). Otherwise it
  gives `unknown`. Without the binding, a human save in KiCad followed by an
  edit and an undo would leave the editor matching the baseline while it
  differs from the file, and a reload would silently discard that state.

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
  differ, so a reload without a bound baseline or `discard_unsaved` refuses
  with `dirty_state_unknown`. This is the intended, honest behaviour. Pass the
  `editor_sha256` and `sha256` from the save step (as `editor_hash` and
  `editor_file_hash`) with `expected_hash` to avoid it.
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
| `discard_unsaved` | `sync_from_disk`, `run_kct_handoff` | false | Reload even if the editor may hold unsaved work, which is then lost |
| `expected_hash` | `sync_from_disk` | none | Refuse with `stale_input` if the file's hash differs (a third party may have written it) |
| `editor_hash` | `sync_from_disk` | none | Baseline for content comparison; normally `editor_sha256` from the save. Alone it can only show the editor changed (`true`) or give `unknown`, never `false` |
| `editor_file_hash` | `sync_from_disk` | none | Binds `editor_hash` to the file: the file's `sha256` recorded with the baseline. `false` needs it to equal a fresh read of the file, or `expected_hash` to match the file |
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
| `document_dirty` at reload | The user edited during the command. The file holds the command's output, and the editor holds the user's edits | Choose one: save from KiCad (keeps the edits, drops the command's output), or `sync_from_disk` with `discard_unsaved` (keeps the output, drops the edits). Then rerun the handoff if needed |
| `dirty_state_unknown` at reload | No bound baseline could prove the editor unchanged. A baseline alone proves nothing clean: it needs the bound file hash, else `unknown` | Pass `editor_hash` and `editor_file_hash` from the save with `expected_hash`, or `discard_unsaved` if the editor holds nothing you need |
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
