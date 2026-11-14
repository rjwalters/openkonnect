# openkonnect Roadmap

**Mission**: give any MCP-capable LLM agent a free, permissively licensed way to
drive a **running KiCad 10** editor. openkonnect is the live-editor layer;
anything that works offline on `.kicad_*` files belongs in
[kicad-tools](https://github.com/rjwalters/kicad-tools).

**Status as of 2026-11-13, `main` @ `eb3173a`.** Phase and principle numbers
are stable: [spec/capabilities.md](spec/capabilities.md) cites them.

## Principles

1. **Clean room first.** Every capability goes through
   [CLEANROOM.md](CLEANROOM.md): a spec written in our own words, then an
   implementation by someone who has not seen the original source. That
   applies to Konnect and to GPL code, including KiCad's. Permissively
   licensed code (MIT, BSD, ISC, Apache-2.0) may be reused under
   CLEANROOM.md's attribution rules. A feature without a spec does not get
   built.
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

## Where it stands

Twelve MCP tools are registered and unit-tested on a fake IPC backend (README
tool table; `src/openkonnect/tools/`). CI runs lint, tests on Python 3.10 and
3.12, and a non-blocking `kicad-tools-main` job.

- **Phase 0, Foundations: done.** Spec v0 (#1), bootstrap with `session_status`
  and `board_summary` (#2), file sync and the handoff protocol with
  `save_before_external_write`, `sync_from_disk`, `run_kct_handoff` (#4, PR #13;
  hardened in #59, #63, #65), IPC client decision `kipy` (#2,
  `docs/ipc-client.md`), permissive-reuse rules (#7), kicad-tools boundary
  (#6, `docs/architecture.md`). The live harness exists: `tests/live/`, the
  `live_kicad` marker, the `--live` opt-in (#40) and fixture boards in
  `tests/fixtures/`. It runs only by hand; see Blocked and waiting.
- **Phase 1, Live session and read-only inspection: partly built.** Done:
  `list_instances` SES-02 (#70), `get_selection` SES-08 (#5, PR #16),
  `list_footprints` LAY-02 (#68), `footprint_pads` LAY-03 (#75), `list_nets`
  LAY-04 (#69), `list_zones` LAY-06 (#78). Not built: SES-03, SES-11, SES-12,
  SES-16, LAY-05, LAY-07, LAY-08, LAY-09.
- **Phase 4, Design review: two items built early.** `show_findings` SES-09
  (PR #16) and the selection round-trip tests SES-10
  (`tests/test_show_findings.py`, `test_round_trip_*`; kept green against
  kicad-tools `main` by #81).
- **Phases 2, 3, 5, 6: not started.** No tool edits the open board over IPC
  yet; the only writes are save, reload and the `kct` handoff.
- **Live verification is behind the code.** The PRs for `list_instances` (#71),
  `list_footprints` (#72), `list_nets` (#73), `footprint_pads` (#76) and
  `list_zones` (#79) each record that no run against a real KiCad was made.
  `list_zones` has no test under `tests/live/` at all.

## Now

Can start today; each is one PR built from an existing spec entry. Per
[spec/README.md](spec/README.md), the implementer must not be that entry's
spec author. None has an issue yet.

| What | Done when | Blocked by |
|------|-----------|------------|
| `list_copper` (LAY-05): tracks, arcs and vias by net, layer or box | Tool registered; fake-backend unit tests; a `tests/live/` test; README row; spec entry marked built; `tests/test_doc_drift.py` passes | nothing |
| `layers_and_stackup` (LAY-07) | Same checklist as above | nothing |
| `board_outline` (LAY-08): closed polygons, extents, frame offset, grid and drill origins | Same checklist; an open or missing outline is reported, never a rectangle | nothing |
| Catch up with kicad-tools: `pyproject.toml` pins `>=0.24,<0.25` and `uv.lock` resolves 0.24.0, while upstream is at v0.29.0 and has closed G1, G2 and G4 (kicad-tools #8015, #8016, #8017) | Pin and `uv.lock` raised to the current release, `uv run pytest -rs` green; the gap tables in `docs/architecture.md` and the spec say which release closed each gap | nothing |
| `snapshot_board` (SES-11): the open board, unsaved changes included, as a temporary `.kicad_pcb` with project files beside it | Same checklist as LAY-05; the saved file's hash is unchanged after the call | nothing |
| `select_instance` (SES-03) | Same checklist; a different instance answering later fails with `instance_changed` | nothing |
| Live test for `list_zones` | `tests/live/test_live_list_zones.py` exists, is skipped without `--live`, and names the fixture board it needs | nothing |

## Next

| What | Done when | Unblocked by |
|------|-----------|--------------|
| `compare_with_disk` (SES-12), built on `kct pcb diff` | Tool registered with tests; its temporary directory is gone after every call | `snapshot_board` |
| `view_state` (SES-16, read half: active and visible layers) | Tool registered with tests; the viewport is not reported | `layers_and_stackup`, so both tools name layers the same way |
| `design_rules` (LAY-09), file-level on KiCad 10 through kicad-tools | Tool registered; the result carries the C-8 note that unsaved rule edits are not visible | the kicad-tools pin raise, so it is written against current `kct` output |
| Adopt the versioned finding format in `show_findings` (G1) | `show_findings` reads the versioned format and refuses an unknown version, with a test | the kicad-tools pin raise |
| Tick the Phase 1 exit criteria | Every Phase 1 spec entry is marked built or deferred with a reason, and each has a recorded live run | the Now items, and #74 |

## Later

Direction only. Each phase keeps its original exit criterion.

- **Phase 2, Interactive layout edits** (SES-13, SES-17, LAY-10 to LAY-17,
  dry-run for every mutating tool). Exit: a multi-step change the user can
  undo with one Ctrl+Z per action. First answer: how do IPC edits avoid
  colliding with the user editing at the same time (the open question in
  SES-17)? `focus_view` (SES-14) and `highlight_net` (SES-15) stay deferred
  until KiCad documents an API for them.
- **Phase 3, Placement and routing in the editor** (PLC-01 to PLC-04, RTE-01
  to RTE-08). Exit: "place and route the selected block" works on a fixture
  board and one undo reverts it. First answer: does a push of kicad-tools
  results land as one transaction on a real KiCad 10?
- **Phase 4, Design review, remainder** (REV-01 to REV-08). Exit: find a
  violation, show it, fix it, confirm the fix. `recheck_touched` (REV-07)
  needs the region-limited check (G2) from the pinned kicad-tools. Sign-off
  stays two-engine on the saved file (#6).
- **Phase 5, Schematic** (SCH-01 to SCH-07). The spec settled the scoping
  question: KiCad 10's API serves the PCB editor only, so live schematic
  reading and editing wait for KiCad 11. First answer: is the file-level
  fallback through the #4 handoff worth shipping before then?
- **Phase 6, Manufacturing** (MFG-01 to MFG-05). Mostly delegation to
  kicad-tools and `kicad-cli` on the saved file. First answer: which of the
  G4 exports does the pinned kicad-tools release provide?
- **v1.0.** Every spec capability implemented or deferred with a reason;
  stable tool names and schemas with a deprecation policy; tested against a
  released KiCad 10.x on macOS, Linux and Windows; install docs for the
  common MCP clients; every implementation PR records its spec and a
  clean-room attestation; no board reported done without two-engine sign-off.

## Blocked and waiting

- **Nothing is dispatchable today.** [WORK_PLAN.md](WORK_PLAN.md) shows every
  bucket empty, and the Now items above have no issues yet.
- **#74, live run of `list_nets` against KiCad 10**, is held for a person at a
  machine with KiCad 10 and the API on. The same kind of run is owed for
  `list_instances`, `list_footprints`, `footprint_pads` and `list_zones`.
- **Live tests cannot run in CI.** KiCad 10's API talks only to a running GUI;
  headless use arrives in KiCad 11 (spec, "What KiCad 10's IPC API allows").
  Until then live evidence comes from hand runs with `--live`.
- **On KiCad 10 `dirty` is always `unknown`**, because the API has no
  modified-since-save query. The handoff treats unknown as dirty.

## Not planned

- Replacing KiCad, or reimplementing work KiCad or `kicad-cli` already does well.
- A second copy of file-level features that kicad-tools already ships.
- Support for KiCad 9 or earlier (contributions welcome later).
- Feature parity with any closed-source product. The target is a useful agent
  interface, not a clone.
- An external autorouter round trip (RTE-09) and GUI automation: both are out
  of scope in the spec's "Considered and not specified".

## What changed in this revision (2026-11-13)

The per-phase checklists became the status and Now / Next / Later sections
above. Corrected: Phase 0's live harness and six Phase 1 tools were still
unticked though merged; the IPC-client, schematic-scope and live-CI questions
are answered and moved out of the open-questions table; upstream gaps G1, G2
and G4, listed as open in `docs/architecture.md`, are closed upstream.
