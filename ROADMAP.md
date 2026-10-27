# openkonnect Roadmap

**Mission**: give any MCP-capable LLM agent a free, permissively licensed way to
drive a **running KiCad 10** editor.

openkonnect is the live-editor layer. Anything that works offline on
`.kicad_*` files belongs in [kicad-tools](https://github.com/rjwalters/kicad-tools);
openkonnect adds what only an open editor can offer: the IPC session, what the
user is looking at and has selected, and edits that land in the open document
with undo.

## Principles

1. **Clean room first.** Every capability goes through
   [CLEANROOM.md](CLEANROOM.md): a spec written in our own words, then an
   implementation by someone who has not seen the original source. A feature
   without a spec does not get built.
2. **Thin over kicad-tools.** Compute (parsing, checks, placement, routing,
   exports) lives in kicad-tools. openkonnect moves results into and out of
   the editor. When a feature here needs file-level logic, that logic goes
   upstream into kicad-tools first.
3. **The editor is the source of truth.** Read state from the live session
   rather than the file on disk when they can differ. Never write a
   `.kicad_*` file behind the editor's back while it has that document open.
4. **Every edit is undoable.** Mutations go through IPC transactions, so one
   agent action is one undo step in KiCad.
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
- [ ] **IPC client decision**: build on kicad-tools' `kicad_tools.ipc` or on
      KiCad's official Python IPC bindings. Record the license and the reason
      in the repo.
- [ ] A live-KiCad test harness: a marked test suite that runs against a real
      KiCad 10 locally, and fixture projects written for this repo.

**Exit criteria**: an MCP client can connect, see which board and schematic are
open, and get a summary of the board.

## Phase 1: Live session and read-only inspection

**Goal**: the agent can see what the user sees.

- [ ] Session: discover running instances, choose one, report KiCad version and
      which IPC API features it supports
- [ ] Open documents, project paths, and whether the board in the editor
      differs from the file on disk
- [ ] Current selection: what is selected and its properties
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

- [ ] Run DRC, from KiCad or from kicad-tools on a snapshot, and return
      structured results
- [ ] Jump to a violation: select it, zoom to it, highlight it
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
- [ ] Fallback when the API lacks schematic edits: kicad-tools edits the file,
      and the editor is asked to reload, with that limitation stated clearly

**Exit criteria**: schematic capabilities match what the IPC API allows, and
the gaps are documented.

## Phase 6: Manufacturing

**Goal**: production outputs from the board that is open.

Mostly delegation. Exports already exist in kicad-tools and `kicad-cli`.

- [ ] Export gerbers, drill files, BOM, CPL and an assembly package from the
      current board state, saving first or snapshotting as needed
- [ ] A pre-fab check (DRC, unrouted nets, missing fields) that runs before export

## v1.0

- Every capability in the spec is either implemented or explicitly deferred
  with a reason.
- Stable tool names and schemas, with a deprecation policy for changing them.
- Tested against a released KiCad 10.x on macOS, Linux and Windows.
- Install docs for the common MCP clients.
- Every implementation PR records its spec and a clean-room attestation.

---

## Open questions and risks

| Question | Why it matters | Resolve by |
|----------|----------------|------------|
| Which IPC client library? | License and maintenance. kicad-tools already has a client. | Phase 0 |
| What does KiCad 10's IPC API cover for the schematic editor? | Decides the scope of Phase 5 | Phase 0 spec |
| How do we keep edits from colliding with the user editing at the same time? | Data loss and surprising undo history | Phase 2 |
| How should live tests run in CI? | KiCad needs a display; headless support varies by platform | Phase 0 harness |
| Where does file-level logic go when it's needed here? | Keeps the two repos from drifting apart | Ongoing: default is upstream into kicad-tools |
