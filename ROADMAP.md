# openkonnect Roadmap

**Mission**: give any MCP-capable LLM agent a free, permissively licensed way to
drive a **running KiCad 10** editor.

openkonnect is the live-editor layer. Anything that works offline on
`.kicad_*` files belongs in [kicad-tools](https://github.com/rjwalters/kicad-tools);
openkonnect adds what only an open editor can offer: the IPC session, file
sync so kicad-tools' file-based writes are safe next to an open editor (#4),
what the user is looking at and has selected, and edits that land in the open
document with undo.

## Principles

1. **Clean room first.** Every capability goes through
   [CLEANROOM.md](CLEANROOM.md): a spec written in our own words, then an
   implementation by someone who has not seen the original source. A feature
   without a spec does not get built.
2. **Thin over kicad-tools.** Compute (parsing, checks, placement, routing,
   exports) lives in kicad-tools. openkonnect moves results into and out of
   the editor. When a feature here needs file-level logic, that logic goes
   upstream into kicad-tools first (#6).
3. **The editor is the source of truth.** Read state from the live session
   rather than the file on disk when they can differ. File-based tools (`kct`)
   may write a `.kicad_*` file that is open in the editor only through the
   handoff protocol (#4): check dirty state, save, let the tool write, then
   reload. If the editor has unsaved changes, refuse unless the caller
   explicitly allows a save or discard.
4. **IPC edits are undoable.** Mutations made through IPC go through
   transactions, so one agent action is one undo step in KiCad. A reload after
   an external write is not an undo step; undoing it means reverting the file.
5. **Fail fast and say why.** If KiCad isn't running, the document isn't open,
   or the IPC API can't do something, the tool returns a clear, structured
   error. It never hangs.
6. **Public APIs only.** KiCad 10's IPC API and its public docs, plus black-box
   runs of KiCad and `kicad-cli`. No `pcbnew` SWIG bindings, and no reading
   KiCad's GPL source to copy behaviour.

## Non-goals

- Replacing KiCad, or reimplementing work KiCad or `kicad-cli` already does well.
- A second copy of file-level features that kicad-tools already ships.
- Support for KiCad 9 or earlier (contributions welcome later, but it is not planned).
- Feature parity with any closed-source product. The target is a useful agent
  interface, not a clone.

---

## Phase 0: Foundations

**Goal**: a spec to build from and a server that connects.

- [x] Project charter, clean-room rules, MIT license
- [x] Agent tooling (Loom, Repo Skills, Squad)
- [ ] **Capability spec v0** (#1): `spec/capabilities.md`, grouped by area. Each
      capability is marked file-level or live-editor, and as already in
      kicad-tools where it is. Includes a provenance section.
- [ ] **Bootstrap** (#2): `pyproject.toml` (uv, Python >= 3.10), the
      `openkonnect serve` MCP entry point, `session_status` and `board_summary`,
      clean errors when KiCad isn't running, CI on mocked IPC.
- [ ] **Live-editor file sync** (#4): dirty state per open document in
      `session_status`, `save_before_external_write`, `sync_from_disk`, and the
      documented handoff protocol (save, `kct` writes, reload), with an
      end-to-end test that wraps one `kct` command such as `kct route`.
- [ ] **IPC client decision** (#2): build on kicad-tools' `kicad_tools.ipc`,
      KiCad's official Python IPC bindings, or KiCAD-MCP-Server's MIT IPC
      backend (IPC parts only: no `pcbnew` SWIG, no AT-SPI). Record the
      license and the reason in the repo.
- [ ] **CLEANROOM.md amendment** (#7): MIT KiCAD-MCP-Server code may be reused
      with attribution, kept separate from anything recalled from Konnect,
      plus a `NOTICE` file listing it.
- [ ] **kicad-tools boundary** (#6): `docs/architecture.md` with the
      file-level vs live-editor rule and the list of gaps filed upstream, a
      pinned kicad-tools version range in `pyproject.toml`, and a CI job that
      runs against kicad-tools `main`.
- [ ] A live-KiCad test harness: a marked test suite that runs against a real
      KiCad 10 locally, and fixture projects written for this repo.

**Exit criteria**: an MCP client can connect, see which board and schematic are
open and whether they are dirty, get a summary of the board, and run a `kct`
write next to the open editor without losing either side's changes.

## Phase 1: Live session and read-only inspection

**Goal**: the agent can see what the user sees.

- [ ] Session: discover running instances, choose one, report KiCad version and
      which IPC API features it supports
- [ ] Open documents, project paths, and whether the board in the editor
      differs from the file on disk (#4)
- [ ] Current selection (#5): `get_selection` returns stable file-level
      references (refdes, net names, `REF.PAD`, a bbox in mm board
      coordinates) that can be passed straight to `kct`
- [ ] Board queries: footprints, nets, tracks, vias, zones, layers, stackup,
      design rules, board outline
- [ ] View state: active layer, visible layers, viewport
- [ ] Hand-off to kicad-tools: snapshot the live board and run file-level
      analysis on that snapshot

**Exit criteria**: an agent can answer "what's on this board and what have I
selected?" without the user saving first.

## Phase 2: Interactive layout edits

**Goal**: safe, undoable edits in the open board.

- [ ] Transaction model: begin, commit and rollback, with one undo step per tool call
- [ ] Footprints: move, rotate, flip, lock, change fields
- [ ] Tracks and vias: create, delete, change width and net
- [ ] Zones: create, edit, refill
- [ ] Selection and view control: select items, zoom to items, highlight a net,
      clear highlighting
- [ ] Dry-run mode for every mutating tool: report what would change, change nothing

**Exit criteria**: an agent can make a multi-step layout change that the user
can review in the editor and undo with one Ctrl+Z per action.

## Phase 3: Placement and routing in the editor

**Goal**: offline compute in kicad-tools, pushed live into the editor.

- [ ] Placement: run kicad-tools placement on a snapshot and apply the result
      as one transaction, either for the whole board or only the selection
- [ ] Routing: route selected nets or the whole board with kicad-tools' router,
      then push the copper
- [ ] Respect locked items, existing copper and keepouts the user drew
- [ ] Before/after summary for every placement or routing push (moved parts,
      added copper, unrouted nets)

**Exit criteria**: "place and route the selected block" works end to end on a
fixture board, and one undo reverts it.

## Phase 4: Design review in the editor

**Goal**: violations the agent can point the user at.

- [ ] Run DRC and return structured results. In-editor DRC is a quick
      check only; sign-off is two-engine (#6): `kct check --mfr` and
      `kicad-cli pcb drc --refill-zones`, both at 0 errors on the saved file.
      openkonnect never reports a board as done from in-editor DRC alone.
- [ ] Show findings (#5): `show_findings` reads kicad-tools' JSON findings
      (no copied schema), then selects, highlights and zooms to them
- [ ] Round-trip test (#5): selection → `kct` references → findings →
      highlight lands on the same items
- [ ] Explain violations in context, using kicad-tools' explanation and intent data
- [ ] Re-check only what an edit touched, after each agent edit

**Exit criteria**: an agent can find a violation, show it to the user in the
editor, fix it, and confirm the fix.

## Phase 5: Schematic

**Goal**: the same live-editor loop for the schematic editor.

This phase depends on what KiCad 10's IPC API exposes for the schematic editor.
Check the KiCad 10 API docs before scoping it.

- [ ] Read the open schematic: sheets, symbols, nets, selection
- [ ] Edit symbols, fields, wires and labels through transactions, if the API allows it
- [ ] Cross-probe between schematic and board
- [ ] Fallback when the API lacks schematic edits: kicad-tools edits the file
      through the #4 handoff protocol (save, write, reload). The reload is not
      an undo step, and that limitation is stated clearly

**Exit criteria**: schematic capabilities match what the IPC API allows, and
the gaps are documented.

## Phase 6: Manufacturing

**Goal**: production outputs from the board that is open.

Mostly delegation. Exports already exist in kicad-tools and `kicad-cli`.

- [ ] Export gerbers, drill files, BOM, CPL and an assembly package from the
      current board state, saving first or snapshotting as needed
- [ ] A pre-fab check that runs before export on the saved file: `kct check
      --mfr` and `kicad-cli pcb drc --refill-zones`, both at 0 errors (#6),
      plus unrouted nets and missing fields

## v1.0

- Every capability in the spec is either implemented or explicitly deferred
  with a reason.
- Stable tool names and schemas, with a deprecation policy for changing them.
- Tested against a released KiCad 10.x on macOS, Linux and Windows.
- Install docs for the common MCP clients.
- Every implementation PR records its spec and a clean-room attestation.
- No board is reported done without two-engine sign-off on the saved file (#6).

---

## Open questions and risks

| Question | Why it matters | Resolve by |
|----------|----------------|------------|
| Which IPC client library? | License and maintenance. Candidates: `kicad_tools.ipc`, KiCad's official bindings, KiCAD-MCP-Server's MIT IPC backend (#7) | Phase 0 (#2) |
| What does KiCad 10's IPC API cover for the schematic editor? | Decides the scope of Phase 5 | Phase 0 spec |
| How do we keep IPC edits from colliding with the user editing at the same time? | Data loss and surprising undo history. External file writes are covered by the #4 handoff protocol | Phase 2 |
| How should live tests run in CI? | KiCad needs a display; headless support varies by platform | Phase 0 harness |
| Where does file-level logic go when it's needed here? | Keeps the two repos from drifting apart | Decided (#6): it lives in kicad-tools; gaps are filed on rjwalters/kicad-tools and listed in `docs/architecture.md` |
