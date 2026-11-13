# Work Log

Chronological record of merged pull requests and closed issues, newest first.
Maintained by the Loom Guide role; entries are appended automatically.

### 2026-11-12

- **PR #80**: Bump Repo Skills 0.25.10 -> 0.26.1
- **Issue #81** (closed): kicad-tools-main canary red: test_round_trip_footprints_through_kct_check (kct check locator)
- **PR #82**: fix(tests): skip locator-less findings in kct check round-trip test

### 2026-11-07

- **Issue #78** (closed): Implement LAY-06 list_zones (zones and rule areas: net, layers, priority, fill, keepouts)
- **PR #79**: feat: add list_zones tool for copper zones and rule areas
- **PR #77**: Bump Repo Skills 0.24.9 -> 0.25.10

### 2026-11-06

- **Issue #75** (closed): Implement LAY-03 footprint_pads (pads by ref or REF.PAD: net, position, layers, shape, size)
- **PR #76**: feat: add footprint_pads tool resolving pads by REF or REF.PAD

### 2026-11-05

- **Issue #68** (closed): Implement LAY-02 list_footprints (refdes, value, position, rotation, side, locked)
- **PR #72**: feat: add list_footprints tool (LAY-02)
- **Issue #69** (closed): Implement LAY-04 list_nets (name, net class, pad count; include_items on 10.0.1+)
- **PR #73**: feat: add list_nets tool for nets, net classes and pad counts
- **Issue #70** (closed): Implement SES-02 list_instances over the existing socket discovery
- **PR #71**: feat: add list_instances tool for SES-02 instance discovery

### 2026-11-04

- **PR #67**: Resync Loom 0.19.1227 → 0.19.1263 ahead of fleet admission

### 2026-10-30

- **Issue #65** (closed): Small follow-ups from the PR #64 review: --resume test value, .kicad_prl in the _Nlayer doc row, --o on route
- **PR #66**: Refuse --o in run_kct_handoff; --resume test value; handoff doc fixes (#65)
- **Issue #63** (closed): run_kct_handoff: kct route without -o writes <stem>_routed.kicad_pcb, not the open board
- **PR #64**: Require -o {board} for kct route and route-auto in run_kct_handoff
- **Issue #59** (closed): run_kct_handoff accepts flags that write the output elsewhere (kct route --auto-layers reports success, board unrouted)
- **PR #62**: Refuse kct flags that write somewhere other than the open board
- **Issue #60** (closed): Small tidy-ups: gitignore snippet for conditional kct files, a long doc line, GIT_*_PATHSPECS in the hygiene test
- **PR #61**: Tidy the kct gitignore snippet, long doc lines and hygiene-test git env

### 2026-10-29

- **Issue #54** (closed): Document the files kct route writes beside the board during a handoff
- **PR #58**: Document the files kct leaves beside the board
- **Issue #52** (closed): Test robustness: check-ignore -v parsing with ':' in a path; live opt-in test copies hooks via inspect.getsource
- **PR #57**: Parse check-ignore -z output; move --live hooks to tests/live_opt_in.py
- **Issue #50** (closed): Canonical match follow-ups: unknown (not false) on a post-save file change; process for trusting another KiCad version
- **PR #56**: Report editor_matches_file "unknown" when the save-path comparison is skipped; checklist for trusting a KiCad version (#50)
- **Issue #53** (closed): Doc tidy-ups from the PR #44 and PR #51 reviews
- **PR #55**: Doc tidy-ups from the PR #44 and #51 reviews
- **Issue #37** (closed): Editor text never byte-matches a KiCad 10.0.7 save: decide how to compare (from #23)
- **PR #48**: Compare editor text and file token by token on KiCad 10.0.7 (#37)
- **Issue #49** (closed): Hygiene test: cover fp_edge.kicad_pcb; board-level wording in show_findings and ipc-client docs
- **PR #51**: Cover fp_edge fixture in hygiene test, say "board-level" in no_board_outline

### 2026-10-28

- **Issue #41** (closed): Board outline: footprint-owned Edge.Cuts graphics, and fix _edge_outline docstring reason
- **PR #47**: Exclude footprint Edge.Cuts graphics from the board outline, matching kct (#41)
- **Issue #43** (closed): Spec: document the unsaved-board state (path null, path_basis unsaved, no_document saved:false) in SES-01 and C-3
- **PR #44**: Spec: document the never-saved board in SES-01, C-3 and the handoff recovery table
- **Issue #42** (closed): test_fixture_hygiene: assert ignore matches come from the repo .gitignore, not global excludes
- **PR #45**: Count only tracked .gitignore rules in the fixture ignore tests (#42)
- **Issue #40** (closed): Deselect live_kicad tests by default so a bare pytest never drives an open KiCad
- **PR #46**: Skip live_kicad tests unless pytest is given --live
- **Issue #34** (closed): session_status can report a directory as the board path just after KiCad starts
- **PR #39**: Treat a board with no board_filename as not ready, not a directory path
- **Issue #23** (closed): Record whether SaveDocumentToString is byte-identical to a KiCad 10 save
- **PR #38**: Record that SaveDocumentToString does not byte-match a KiCad 10.0.7 save (#23)
- **Issue #32** (closed): Board outline origin includes Edge.Cuts line width, offsetting board-frame points
- **PR #36**: Board outline from Edge.Cuts geometry, not stroked boxes (#32)
- **Issue #33** (closed): Ignore KiCad lock/project files in tests/fixtures and clean up the handoff test's kct output
- **PR #35**: Ignore KiCad lock/project files in tests/fixtures and clean up kct route output
- **Issue #28** (closed): SES-12: record that compare_with_disk must snapshot the editor to a file before kct pcb diff
- **PR #29**: spec: SES-12 snapshots the editor to a temp file before kct pcb diff
- **Issue #26** (closed): test_doc_drift: check README spec IDs and tolerate padded table cells
- **PR #31**: test_doc_drift: check README spec IDs and tolerate padded table cells
- **Issue #27** (closed): handoff-protocol: document save-then-undo before the external write
- **PR #30**: Document save-then-undo before the external write in the handoff window
- **Issue #19** (closed): spec: mark gap G3 resolved upstream (kct pcb diff, kicad-tools 0.24.0)
- **PR #25**: spec: mark gap G3 resolved upstream (kct pcb diff, kicad-tools 0.24.0)
- **Issue #14** (closed): Spec/README follow-ups from #4: C-3 save_failed/reload_failed codes, README tool table
- **PR #24**: Sync spec C-3 and the README tool table with the server (#14)
- **Issue #15** (closed): Session sync: stale editor_hash can report dirty=false and let sync_from_disk discard unsaved edits
- **PR #22**: Never prove a board clean from a caller-supplied editor hash alone (#15)

### 2026-10-27

- **Issue #17** (closed): ROADMAP.md: update the #7 references to the broadened permissive-reuse policy
- **PR #21**: ROADMAP: broadened permissive-reuse policy (#7) and merged Phase 0 ticks
- **Issue #18** (closed): CLEANROOM.md: require license text (and Apache NOTICE) to travel with reused code
- **PR #20**: CLEANROOM.md, NOTICE: require license text and Apache NOTICE to travel with reused code
- **Issue #5** (closed): Selection in, findings out: bridge the live editor's selection to kct, and kct JSON findings back to the editor
- **PR #16**: Selection in, findings out: get_selection and show_findings (#5)
- **Issue #4** (closed): Live-editor session and file sync, so kicad-tools' file-based writes are safe next to an open KiCad
- **PR #13**: Live-editor session and file sync: save, reload, kct handoff (#4)
- **Issue #6** (closed): Dependency boundary with kicad-tools: file-level work stays upstream, gaps get filed there
- **PR #12**: Dependency boundary with kicad-tools (#6)
- **Issue #2** (closed): Bootstrap: Python package, MCP server skeleton, KiCad 10 IPC session
- **PR #11**: Bootstrap: Python package, MCP server skeleton, KiCad 10 IPC session
- **Issue #7** (closed): CLEANROOM.md: allow MIT KiCAD-MCP-Server code with attribution?
- **PR #9**: CLEANROOM.md: allow MIT KiCAD-MCP-Server code with attribution
- **Issue #1** (closed): Capability spec v0 (spec-author role): what the tool surface must do
- **PR #10**: Capability spec v0 (spec-author role)
- **PR #8**: Add ROADMAP.md
- **PR #3**: Install agent tooling: Loom, Repo Skills, Squad

