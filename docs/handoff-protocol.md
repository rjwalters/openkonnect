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
   without `discard_unsaved` when the editor text matches the saved file
   (byte for byte, or on KiCad 10.0.7 by the strict token comparison; see "Is
   the editor text byte-identical to a KiCad save?"). If the user edited the
   board while the command ran, the reload refuses with `document_dirty`, and the result reports both
   the command's outcome and the refusal.
7. **Report.** One record per step, the command's exit code and JSON output,
   and the final document state.

### The snapshot rule (`run_kct_handoff` only)

Within one `run_kct_handoff` call, step 2 saves the editor (or proves the
editor text equal to the file, by the rule in "Dirty state: the tri-state")
and records the hash of that exact editor content. This snapshot is taken by the tool itself; it is never an argument
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
  the saved file, which is always the case after a save on a KiCad version not
  on the measured allow-list): the reload refuses with `dirty_state_unknown` unless
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
The opposite order loses the human's save instead. If the human saves an
edit after the command has read the file but before it writes, and then
undoes that edit exactly, the command's write replaces the human's save on
disk. The editor matches the snapshot again, so the reload goes ahead and
loads the command's output, which was computed from the file without the
edit. The saved edit is gone from disk. The command's write causes this
loss, not the reload, and no read of KiCad 10's API can tell this case apart
from a clean run either. Without the exact undo, the editor does not match
the snapshot and the reload refuses with `document_dirty`. A save made
before the command reads the file becomes part of the command's input, so
nothing is lost.

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

- **Against the saved file:** `false` only if the editor text equals the
  file, since then nothing in the editor is missing from the file. "Equals"
  means byte-identical (`match_basis: "bytes"`), or, on a KiCad version on the
  measured allow-list, equal by the strict token comparison below
  (`match_basis: "canonical"`). Any other difference gives `unknown`, because
  it may be unsaved work or only a formatting difference between KiCad's
  serializer and whatever wrote the file. On KiCad 10.0.7 KiCad's own save is
  never byte-identical to the editor text, so there `false` comes from the
  token comparison (measured; see "Is the editor text byte-identical to a
  KiCad save?").
- **Against a baseline (`editor_hash`):** the hash of the editor text recorded
  right after a save. Changed since then gives `true`: someone edited the
  board in KiCad after the save. This is what lets step 6 detect an edit made
  while the command ran. Unchanged gives `unknown`, never `false`, unless the
  editor text is also byte-identical to the file now.

`false` comes only from the first rule: openkonnect reads the editor text and
the file during the same call and they are equal by that rule. No hash a caller
passes (`editor_hash`, `expected_hash`) can produce `false`; a caller hash can
only move the result to `true` or `unknown`. A recorded hash cannot prove
clean because the file may have changed under it: a human save in KiCad
followed by an edit and an undo leaves the editor matching the baseline while
it differs from the file, and a reload would silently discard that state
(issue #15).

If KiCad does not serve the text, dirty is `unknown` with basis `none`.

### The strict token comparison (KiCad 10.0.7 only)

`src/openkonnect/kicad_text.py` (`same_document`) compares the editor text
and the file bytes as token sequences:

- Both sides are split into `(`, `)`, quoted strings and bare atoms. A quoted
  string is kept verbatim, quotes and escapes included, so `"A B"` and `"AB"`
  differ. A bare atom is kept verbatim, so `1` and `1.0` differ, and so do
  `"x"` and `x`.
- Whitespace between tokens is ignored. That covers the line breaking of
  short lists, line endings and the trailing newline.
- In the **editor text only**, a list of the exact form `(version T)`,
  `(generator T)` or `(generator_version T)`, with one atom or string `T`, is
  ignored when it is a direct child of a list whose head is the bare atom
  `footprint`. The board header and the same lists anywhere else are kept.
  The file is tokenized strictly: what was measured is that the editor text
  adds these lists and KiCad's saved file has none. A footprint `(version …)`
  in the file is an unmeasured difference (a loader may use it to read the
  rest of the footprint), so it stays a difference and gives `unknown`.
- The file must be exactly one top-level `(kicad_pcb …)` list, so an empty or
  whitespace-only text never matches.
- Whitespace between tokens is assumed to carry no meaning (for example, no
  line-comment syntax). KiCad's own save form, which this compares against,
  has none; the assumption was not probed separately.
- Anything else is a difference: a changed, added, removed or reordered
  token, a missing uuid. Text that does not tokenize (an unterminated string,
  unbalanced parentheses, a truncated file, two atoms or strings with no
  whitespace between them, bytes that are not UTF-8) is never a match.

It is used only when KiCad reports a version on the allow-list
`CANONICAL_MATCH_VERSIONS` (exact `KiCad.get_version()` string; today only
`10.0.7`). On any other version, or if KiCad does not report one, only byte
equality can give `false`, as before. A version is added only after the
checklist in [Adding a KiCad version to
`CANONICAL_MATCH_VERSIONS`](#adding-a-kicad-version-to-canonical_match_versions)
has been run on it, repeating the measurement the subsection below records.

The comparison does not change the #15 rule. Both sides are still read fresh
by openkonnect in the same call, and no recorded value can produce `false`.
It also does not change the window above: an edit and an exact undo leave the
same tokens as no edit, under byte equality too. In `save_before_external_write`
the comparison after a save uses only the file bytes whose hash the result
reports as `sha256`; a file deleted or written again in between is not
compared, and `editor_matches_file` is `"unknown"`, not `false`: nothing showed
that the editor and the file differ, only that the file changed. The same binding holds without a save: the
dirty check records the hash of the exact file bytes it compared
(`DirtyState.file_sha256`), and a `false` counts only if that hash equals the
hash the caller read first (the `sha256` that `save_before_external_write`
reports, or the hash `sync_from_disk` checks against `expected_hash`). If the
file was written between the two reads, the state is `unknown`.

**Where `false` applies.** A canonical match gives `dirty: false` in every
caller of the dirty check, not only inside `run_kct_handoff`: a
`save_before_external_write` on a board just saved from KiCad leaves it
alone, and a plain `sync_from_disk` after a save from KiCad (with the file
unchanged since) reloads without `discard_unsaved`. That is the same
guarantee byte equality gives: the editor, read now, holds nothing the file,
read now, lacks.

**Wire fields.** `save_before_external_write` keeps `editor_matches_file`
(now meaning "equal by the rule above") and adds `match_basis`: `"bytes"`,
`"canonical"` or `null` when they do not match or were not compared.
`editor_matches_file` is `true`, `false` (compared and different) or
`"unknown"` (the editor text or the file could not be read, or the file
changed after the save check, so they were not compared); only `true` lets
`run_kct_handoff` take its snapshot. A clean `state_before`
record carries `match_basis` too. `run_kct_handoff` copies both into its step
2 record.

**Cost.** Measured on an Apple M5 Max with Python 3 (2026-10-28): about
0.1 s per MB of board text, linear (1 MB in 0.10 s, 5 MB in 0.57 s, 20 MB in
2.0 s, 50 MB in 4.9 s, both sides together). It runs only when the two sides
are not byte-identical. There is no size cap: a board large enough to push a
call past its C-4 bound fails with `timeout`, never with `false`. Raise
`OPENKONNECT_TIMEOUT` for such boards.

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
  `run_kct_handoff` does not need `discard_unsaved` for a clean run when it
  can take its own snapshot (see "The snapshot rule"). On KiCad 10.0.7 it
  takes one through the strict token comparison. On a version not on the
  allow-list it cannot, so it needs `discard_unsaved` too (see the next
  subsection).
- **Verification.** After a reload, openkonnect reads the editor back. If its
  text equals the file, `verified` is `true`. A file written by another tool
  is usually formatted differently from KiCad's own output, so `verified` is
  then `"unverified"`. That is not an error. `editor_changed` says whether the
  editor's content changed at all.
- **The view.** No documented request reads or sets zoom or pan, so
  `view_kept` is `"unknown"`.

These were verified against the installed client and the docs, not against a
running KiCad. The live test below is the check against a real editor. The one
point that was measured on a running KiCad is in the next subsection.

### Is the editor text byte-identical to a KiCad save?

**No, not on KiCad 10.0.7.** Even right after KiCad saves the board, the text
from `SaveDocumentToString` (`Board.get_as_string()`) is not byte-identical to
the file that save wrote. The two differ only in the two ways described
below, and both are removed by the strict token comparison (issue #37), so on
10.0.7 `save_before_external_write` reports `editor_matches_file: true` with
`match_basis: "canonical"`, `run_kct_handoff` takes its snapshot, and content
comparison can give `dirty: false`. On any other KiCad version, which is not
measured, byte equality alone applies, and there a KiCad save would again
give `editor_matches_file: false` and no snapshot.

Measured on 2026-10-28 (issue #23), black box only (kicad-python and the
bytes on disk; no KiCad source was read):

- KiCad `10.0.7` (`KiCad.get_version()`), kicad-python 0.8.0, built against
  API `10.0.6-0-gcaf7377e9c` (`kipy.kicad_api_version`), on macOS, socket
  `ipc:///tmp/kicad/api.sock`.
- The board was `tests/fixtures/board.kicad_pcb`, open in the PCB Editor. A
  throwaway script (not committed) ran the cases below in order. It compared
  `get_as_string().encode("utf-8")` with the file's bytes, which is how
  openkonnect compares them. Saves used `Board.save()`, and the edit used
  `begin_commit` / `update_items` / `push_commit`.
- Afterwards the fixture was restored with `git checkout`, its `git
  hash-object` was confirmed unchanged (`4c952bb1…`), and the editor was
  reverted with `Board.revert()`.

| # | Case | Editor text equals file? | Notes |
|---|---|---|---|
| 1 | Fresh open, no save | Not equal (file 1845 B, text 3632 B) | Expected: the fixture was written by `openkonnect-fixture`, not KiCad. KiCad rewrites the header, reorders and adds layers, and adds the full `setup` block |
| 2 | After a KiCad save, no edit | **Not equal** (file 3618 B, text 3632 B) | The question the handoff depends on. The difference is described below |
| 3 | Idempotence: two reads, then two more saves with no edit | Not equal, but stable | The two reads gave the same text, and it equalled case 2's text. Both later saves left the file byte-identical to case 2's file. Each save is stable, and so is each read, but the two forms still differ |
| 4 | Move one footprint by 1 mm, then save | Not equal (file 3618 B, text 3632 B) | Before the save, the editor text differed from the old file and the file was unchanged, so the text tracks unsaved work. After the save, the file differed from case 2's file only in the moved `(at …)`. The text-to-file difference was the same as in case 2 |
| 5 | Move it back, then save | Not equal | The file was byte-identical to case 2's file, and the text to case 2's text. Nothing drifts over a round trip |
| 6 | A board saved by an older KiCad | Not tested | No older-KiCad board is available: only KiCad 10.0.7 is installed, and the repo's only boards are the two fixtures |

Only `board.kicad_pcb` was measured in #23. `selection.kicad_pcb` was
measured in #37 (next part).

**How the bytes differ (cases 2 to 5).** The text is 14 bytes longer than the
saved file, with 178 lines to the file's 188. Things that match:

- Both use LF line endings, tab indentation, and one trailing newline (both
  end in `\n)\n`).
- The sections are in the same order, and the uuid sets are identical.
- No timestamp or uuid changes between reads or between saves.

Two things differ, and they appear wherever the construct appears:

1. **A header inside each footprint.** The text repeats the file header
   inside every `(footprint …)`. The saved file has no such lines. The first
   difference is at byte offset 1823, line 85, which is the first footprint.
   Redacted:

   ```
   	(footprint "openkonnect:R_Fixture"
   		(version 20260206)          <- text only
   		(generator "pcbnew")        <- text only
   		(generator_version "10.0")  <- text only
   		(layer "F.Cu")
   ```

2. **Short lists on one line.** The text writes `font` and `stroke` lists on
   one line. The saved file breaks them over several lines:

   ```
   text:  (font (size 1 1) (thickness 0.15))
   file:  (font
            (size 1 1)
            (thickness 0.15)
          )
   ```

   The same pattern appears for `(font (size 1.27 1.27))` and
   `(stroke (width 0.1) (type default))`.

Normalizing only line endings or the trailing newline would change nothing,
since those already match. Two steps together make the two equal for this
board: drop the three header lines that follow each `(footprint "…"` line,
then compare with all whitespace removed. Either step alone does not.
Removing *all* whitespace would also merge `"A B"` into `"AB"`, so the rule
openkonnect uses is the token comparison above, not this recipe.

**Measured again with the token comparison (issue #37, 2026-10-28).** Same
KiCad 10.0.7, kicad-python 0.8.0, macOS. Each fixture was copied to a scratch
directory and the copy opened, so KiCad's saves never wrote a tracked file. A
throwaway script (not committed) read `get_as_string()` and the file's bytes
and ran `same_document` on them:

| Case | `board.kicad_pcb` | `selection.kicad_pcb` |
|---|---|---|
| As opened (written by `openkonnect-fixture`) | file 1845 B, text 3632 B: bytes no, tokens no | file 3549 B, text 6197 B: bytes no, tokens no |
| After `Board.save()` | file 3618 B: bytes no, **tokens equal** (660 = 660) | file 6137 B: bytes no, **tokens equal** (1109 = 1109) |
| Second read, no change | bytes no, tokens equal | bytes no, tokens equal |
| R1 moved 1 mm, not saved | bytes no, **tokens not equal** | bytes no, **tokens not equal** |
| R1 moved back, not saved | bytes no, tokens equal | bytes no, tokens equal |
| After `Board.revert()` | bytes no, tokens equal | bytes no, tokens equal |

Before the ignore list, the editor text had 672 and 1145 tokens: 12 and 36
more, which is four tokens for each of the three header lists in each of the
1 and 3 footprints. Through openkonnect, on each copy: `board_dirty_state`
gave `false` (`match_basis: "canonical"`) after the save and after the
revert, and `unknown` after the unsaved move, and `save_before_external_write`
without `allow_save` left the saved board alone and refused the moved one
with `dirty_state_unknown`. A live `run_kct_handoff` of `kct route` on
`tests/fixtures/selection.kicad_pcb` saved at step 2 (`match_basis:
"canonical"`), changed the file at step 3, and reloaded at step 6 with
`snapshot_matched: true` and `discarded_unsaved: false`. Afterwards the
fixture was restored and its `git hash-object` (`a206aaea…`) checked
unchanged. `kct route` leaves `board.kicad_pcb` unchanged (it is fully
routed), so the live handoff test uses `selection.kicad_pcb`.

**Also observed: fresh uuids at load.** The fixture's footprint has no uuid
on its `Datasheet` and `Description` properties. Each time KiCad loaded the
file (`Board.revert()`), it gave those two properties new random uuids. After
two reverts, the text differed from case 1 only in those two uuid lines. So
when a file written by another tool leaves out uuids, even two reads of the
same unchanged file, with a reload in between, do not give the same text.
Once KiCad has saved the uuids (cases 2 to 5), they stay fixed.

### Adding a KiCad version to CANONICAL_MATCH_VERSIONS

The token comparison is trusted only on the exact versions measured. Before
another `KiCad.get_version()` string joins `CANONICAL_MATCH_VERSIONS`
(`src/openkonnect/kicad_text.py`), repeat the measurement on that version,
black box only (kicad-python and the bytes on disk; never KiCad's source):

1. Record `KiCad.get_version()` (the exact string that will be added), the
   kicad-python version, the OS and the date.
2. Copy `tests/fixtures/board.kicad_pcb` and
   `tests/fixtures/selection.kicad_pcb` to a scratch directory (never save a
   tracked file) and open each copy.
3. For each copy, run `same_document` on `get_as_string()` and the file bytes
   and record bytes equal and tokens equal for: after `Board.save()` (tokens
   **must** be equal), a second read (equal), one footprint moved 1 mm and
   not saved (tokens **must differ**), moved back (equal), and after
   `Board.revert()` (equal). Use the same table shape as the #37 table above.
4. **Comment syntax probe.** Add a candidate comment line to a scratch copy
   (for example a line starting with `#` or `;` between two top-level
   items). Reload it with `Board.revert()`, and record whether KiCad loads
   it and whether the line appears in `get_as_string()` or the next save. If
   KiCad accepts any comment form, the whitespace assumption in [the strict
   token comparison](#the-strict-token-comparison-kicad-1007-only) is false:
   do not add the version, and open an issue against `kicad_text.py`.
   Otherwise update that assumption bullet to say it was probed, and on
   which version.
5. Run the live handoff test, `uv run pytest --live -m live_kicad -rs`, with
   `selection.kicad_pcb` open. It must pass on the real reload path (step 2
   `match_basis: "canonical"`, reload with `snapshot_matched: true` and
   `discarded_unsaved: false`), not the `dirty_state_unknown` fallback.
6. Only then add the string to `CANONICAL_MATCH_VERSIONS`, update the
   allow-list assertion in `tests/test_kicad_text.py`
   (`sorted(CANONICAL_MATCH_VERSIONS) == [...]`), and add a dated block with
   the results to [Is the editor text byte-identical to a KiCad
   save?](#is-the-editor-text-byte-identical-to-a-kicad-save), after the #37
   table.

No committed script or live test runs steps 3 and 4: they need a running
KiCad, cannot run in CI, and would only repeat what this checklist records.
The checklist and the existing live test (step 5) are the recorded check.

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

The table covers refusals from `save_before_external_write` and
`sync_from_disk` used alone, as well as from a handoff step. A board that has
never been saved is refused before anything is saved or written: by the tool
itself, or in `run_kct_handoff` at step 1 (`failed_step: "session_status"`).

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
| `no_document` with `details.saved: false` | The open board has never been saved, so it has no file to save, sync or hand off | Save it in KiCad with File > Save As, then call the tool again |

Every tool also fails fast with the shared connection errors (SES-07):
`kicad_not_running`, `ipc_disabled` (naming the preference that enables the
API), `ipc_unreachable`, `kicad_busy`, `timeout`, `instance_changed` and
`no_document`.

## Tests

- Unit tests, run in CI against the fake backend:
  `tests/test_save_before_external_write.py`, `tests/test_sync_from_disk.py`,
  `tests/test_run_kct_handoff.py`.
- End to end, `live_kicad` marker: `tests/live/test_live_handoff.py` wraps a
  real `kct route` around `tests/fixtures/selection.kicad_pcb` open in KiCad 10,
  then restores the fixture. It skips when KiCad is not reachable. Run it with
  `uv run pytest --live -m live_kicad -rs`.
