# Adaptive Clearance-Aware Fillets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plugin's rounding engine with one that cleans up fragments, shares short segments fairly, merges close bends, shrinks each arc to clear other-net copper, and marks corners it had to leave sharp.

**Architecture:** A pure-Python geometry planner (`round_tracks_geom.py`, no `pcbnew`) turns plain segment data into a `Plan` (segment updates/removals, new arcs, sharp corners), asking a `fits(arc)` callback for clearance. A board layer (`round_tracks_board.py`) reads the board into plain data, implements `fits` with `pcbnew` shapes, commits the plan, and draws warnings. It is callable headless as `round_board(board, settings)`. The wx dialog only gathers settings and calls it.

**Tech Stack:** Python 3.11 as bundled with KiCad 10 (`C:\Program Files\KiCad\10.0\bin\python.exe`), SWIG `pcbnew`, wxPython, stdlib `unittest`, `kicad-cli` for DRC in tests.

**Spec:** `docs/superpowers/specs/2026-10-20-adaptive-fillets-design.md`

## Global Constraints

- Repo: `C:\Dev\jrowe88\kicad-round-tracks`, branch `adaptive-fillets`. Run every command from the repo root.
- Test command: `& "C:\Program Files\KiCad\10.0\bin\python.exe" -m unittest discover -s tests -t . -v` (append a module/test name to narrow).
- Any test or script that imports `pcbnew` must first run `import wx; wx.DisableAsserts()`. Without it, KiCad pops a modal "wxWidgets Debug Alert" on the user's desktop (seen on 10.0.7). `tests/__init__.py` does this once.
- Never call `pcbnew.WriteDRCReport` headless. It crashes the process (access violation) without a running KiCad. Use `kicad-cli pcb drc --format json --severity-all -o <out.json> <board>` (`C:\Program Files\KiCad\10.0\bin\kicad-cli.exe`).
- Do not open, copy, or test against `C:\Dev\jrowe88\ecoport-bridge` or the installed plugin under `...\OneDrive - Genius SIS\Documents\KiCad\10.0\3rdparty\plugins\` until the user says ecoport-bridge is committed and pushed (Task 11 is gated on that).
- Units: geometry works in integer nanometres (KiCad internal units). Points are `tuple[int, int]`.
- Radius meaning (user decision): netclass *Radius (mm)* = true radius on a 90° bend. `RADIUS = scaling_nm / (sin(π/4) + 1)`. A corner whose two tracks meet at interior angle `2h` asks each leg for setback `RADIUS · (sin h + 1)`. True arc radius from a setback `t` is `t · tan h`.
- Min radius: hard floor on the true arc radius. Below it the corner stays sharp and gets a warning. Default `0.1` mm *(Claude's call)*.
- Netclass per net: `net.GetNetClassName().split(',')[0]`, handled once per net.
- Warnings: arrow + text on `pcbnew.Cmts_User`, all in one `PCB_GROUP` named `round-tracks-warnings`. Remove the previous group at the start of each run.
- Keep Apache-2.0 headers and credits. Match existing style (4-space indent in `.py` files except the generated `round_tracks_gui.py`, which uses tabs).
- Commit after every task; end commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **Re-running on an already-rounded board:** corners touching an existing arc or via must be skipped, never double-rounded. Test: `test_rerun_does_not_double_round` (Task 6).
- **Mixed widths at a corner** (deliberate neck-downs): leave sharp silently. Count as `skipped`, no warning arrow. Test: `test_mixed_width_corner_skipped` (Task 3).
- **Near-straight and hairpin angles:** interior angle within `angleTolerance` of π is not a corner. Near 0 (hairpin), the radius collapses below Min radius, so the corner is left sharp, with no crash or divide-by-zero. Test: `test_hairpin_and_straight` (Task 1).
- **Layers:** B.Cu and inner-layer tracks; THT pads and through vias block on every copper layer, SMD pads only on their own. Test: `test_tht_pad_blocks_bottom_layer` (Task 7).
- **Empty scope** (no enabled classes, or nothing selected in scope): no crash, all-zero summary, no warning group left behind. Test: `test_empty_scope` (Task 8).

---

### Task 1: Test harness and fillet primitives

**Files:**
- Create: `tests/__init__.py`, `tests/test_geom.py`, `round_tracks_geom.py`

**Interfaces:**
- Produces (in `round_tracks_geom.py`):
  - `setback_request(scaling_nm: int, interior: float) -> float`
  - `arc_radius(setback: float, interior: float) -> float`
  - `setback_for_radius(radius: float, interior: float) -> float` (inverse of `arc_radius`)
  - `fillet_points(corner: Point, d1: Point, d2: Point, setback: float) -> tuple[Point, Point, Point]` returns `(start, mid, end)`, where `d1`/`d2` are the far ends of the two legs
  - `interior_angle(corner: Point, d1: Point, d2: Point) -> float` (radians, in `[0, π]`)
  - `ANGLE_TOL = 0.001`, `Point = tuple[int, int]`

- [ ] **Step 1:** `tests/__init__.py` inserts the repo root into `sys.path`. If `wx` is importable, it calls `wx.DisableAsserts()`.
- [ ] **Step 2: Write failing tests** in `tests/test_geom.py` (`class TestPrimitives(unittest.TestCase)`):
  - `test_90_degree_radius_equals_setting`: `t = setback_request(2_000_000, pi/2)` → `assertAlmostEqual(t, 2_000_000, delta=1)`; `arc_radius(t, pi/2)` ≈ 2_000_000.
  - `test_45_degree_bend_matches_legacy_scaling`: interior `3*pi/4` → `arc_radius(setback_request(2_000_000, 3*pi/4), 3*pi/4) / 2_000_000` ≈ 2.72 (±0.01).
  - `test_fillet_points_tangent`: corner `(0,0)`, `d1=(10_000_000,0)`, `d2=(0,10_000_000)`, setback 2_000_000 → start `(2_000_000,0)`, end `(0,2_000_000)`. Mid is the same distance (±2 nm) from centre `(2_000_000,2_000_000)` as start.
  - `test_hairpin_and_straight`: `interior_angle` of collinear opposite legs is π (±1e-9); `arc_radius(setback_request(2_000_000, 0.01), 0.01) < 100_000`; no exception at interior `0.0`.
- [ ] **Step 3:** Run `... -m unittest tests.test_geom -v` → FAIL (module missing).
- [ ] **Step 4:** Implement the primitives. Mid point uses the legacy formula from `round_tracks_action.py:493-501`: `f = 1/(2cos(π/2−h)+2)`, `mid = corner·(1−2f) + start·f + end·f`.
- [ ] **Step 5:** Run the tests → PASS.
- [ ] **Step 6:** Commit `Add geometry primitives and test harness`.

### Task 2: Segment graph and cleanup

**Files:**
- Modify: `round_tracks_geom.py`; Test: `tests/test_geom.py`

**Interfaces:**
- Produces:
  - `@dataclass Seg: id: int; a: Point; b: Point; width: int; layer: int; net: int`
  - `cleanup(segs: list[Seg], anchors: set[Point]) -> tuple[list[Seg], dict[int, tuple[Point, Point]], set[int]]` returns `(new_segs, updates, removals)`. `updates[id]` holds new endpoints for a surviving original id; `removals` holds the ids that were merged away or deleted.
  - `SLIVER_NM = 1000`

Rules (spec step 2): merge two segments only if they share an endpoint `p` and have the same width, layer and net. They must also be collinear within `ANGLE_TOL`, with `p` not in `anchors` and exactly two segments at `p`. The survivor keeps the lower id. Delete segments shorter than `SLIVER_NM` and reconnect their neighbours to one endpoint, but only when that endpoint isn't in `anchors` and the reconnected neighbours stay collinear-or-cornered as before. Otherwise leave them.

- [ ] **Step 1: Failing tests** (`class TestCleanup`):
  - `test_merges_collinear_fragment`: segs `(0,0)-(5mm,0)`, `(5mm,0)-(5.0003mm,0)`, `(5.0003mm,0)-(10mm,0)` → one seg `(0,0)-(10mm,0)` with id 0; `removals == {1, 2}`.
  - `test_anchor_blocks_merge`: same, with `(5mm,0)` in anchors → `(5mm,0)` survives as an endpoint.
  - `test_different_width_not_merged`.
  - `test_removes_sliver_before_corner`: `(0,0)-(5mm,0)`, `(5mm,0)-(5mm+500nm,0)`, `(5mm+500nm,0)-(5mm+500nm,5mm)` → two segs meeting at one point.
  - `test_junction_point_not_merged`: three segs meeting at a point stay unmerged.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `Add segment cleanup pass`.

### Task 3: Corner planner: allocation, min radius, clearance callback

**Files:**
- Modify: `round_tracks_geom.py`; Test: `tests/test_geom.py`

**Interfaces:**
- Produces:
  - `@dataclass Arc: start: Point; mid: Point; end: Point; width: int; layer: int; net: int`
  - `@dataclass Sharp: point: Point; layer: int; reason: str` (`"length"` or `"clearance"`)
  - `@dataclass Plan: updates: dict[int, tuple[Point, Point]]; removals: set[int]; arcs: list[Arc]; sharps: list[Sharp]; stats: collections.Counter` (keys: `full`, `reduced`, `merged`, `sharp_length`, `sharp_clearance`, `skipped`)
  - `plan_fillets(segs: list[Seg], anchors: set[Point], scaling_by_net: dict[int, int], min_radius_nm: int, fits: Callable[[Arc], bool], avoid_junctions: bool) -> Plan`. It calls `cleanup` first and merges its updates/removals into the plan.

Behaviour:
- A corner is a non-anchor point with exactly two same-width segments whose interior angle is `< π − ANGLE_TOL`.
- Mixed widths → `skipped`, no `Sharp`.
- Allocation per spec step 4: each segment end that is a corner requests `setback_request`. If the two requests exceed the segment's length, both are scaled by `length / (r1 + r2)`. A corner's setback is the min of its two legs' allocations.
- If `arc_radius(setback) < min_radius_nm` → `Sharp(reason="length")`.
- Clearance: if `fits(arc)` → accept; `full` if the setback equals the request, else `reduced`. Otherwise binary-search the setback between `setback_for_radius(min_radius_nm)` and the current setback, 8 iterations, keeping the largest that fits (`reduced`). If none fits → `Sharp(reason="clearance")`.
- An accepted corner shortens both legs by its setback; record this in `updates`. A leg shortened to ≤ `SLIVER_NM` goes in `removals`.
- Process corners in a deterministic order (by net, layer, then point).

- [ ] **Step 1: Failing tests** (`class TestPlanner`, `fits=lambda a: True` unless noted):
  - `test_single_corner_full`: L of 10 mm legs, scaling 2 mm → 1 arc, `stats['full'] == 1`, both legs updated to end 2 mm from the corner.
  - `test_short_shared_segment_split_proportionally`: a 45° bend and a 90° bend sharing a 1 mm segment → allocations proportional to requests (assert each arc start/end lies on the shared segment, the two tangent points don't overlap, and their distances from the segment's ends are in the ratio of the requests ±1%).
  - `test_min_radius_leaves_sharp`: legs of 0.15 mm, min radius 0.1 mm → `Sharp(reason="length")`, no arc.
  - `test_clearance_shrinks_radius`: `fits = lambda a: arc_radius_of(a) <= 1_000_000` (helper computing the radius from three points) → one arc with radius in `[0.95 mm, 1.0 mm]`, `stats['reduced'] == 1`.
  - `test_clearance_never_fits`: `fits=lambda a: False` → `Sharp(reason="clearance")`.
  - `test_mixed_width_corner_skipped`: `stats['skipped'] == 1`, no sharps.
  - `test_anchor_corner_untouched`: corner point in anchors → no arc, no update.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `Add corner planner with allocation and clearance search`.

### Task 4: Junctions

**Files:** Modify `round_tracks_geom.py`; Test `tests/test_geom.py`

**Interfaces:** Extends `plan_fillets` (same signature).

Behaviour (spec step 4b):
- A non-anchor point with ≥3 same-width segments is a junction. If `avoid_junctions` is set, it's skipped (`skipped`).
- Otherwise sort the legs by angle. Every leg gets the same setback: the min over adjacent pairs of `setback_request`, capped by each leg's allocation.
- Add one arc per adjacent pair, including the pair that wraps around (as the legacy code does for >2 legs).
- All-or-nothing: shrink the shared setback by binary search until *every* arc fits. Below min radius → one `Sharp` at the junction point.

- [ ] **Step 1: Failing tests:** `test_t_junction_three_arcs`; `test_t_junction_all_or_nothing`, where `fits` rejects any arc touching the region `y > 0` above the bar, giving one Sharp, no arcs and no updates; and `test_avoid_junctions_skips`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `Round junctions all-or-nothing`.

### Task 5: Bend-run merging

**Files:** Modify `round_tracks_geom.py`; Test `tests/test_geom.py`

**Interfaces:** Extends `plan_fillets`. Adds a `merged` stat.

Behaviour (spec step 5):
- A run is ≥2 consecutive two-segment corners with the same turn sign (sign of the cross product). Every inner segment must be "short": its allocation is less than the request at either end.
- The two outer legs' lines must intersect at a virtual corner `v` on the far side of the run, and the total turn must be `< π − ANGLE_TOL`.
- The merged setback is `setback_request` at `v`, capped so its tangent points lie on the outer legs between `v`'s projection and each leg's far-end allocation. It must also be ≥ the distance from `v` to each outer leg's inner end; otherwise the merge is invalid.
- Use the merge only if its radius is greater than the best separate-arc radius in the run and `fits` accepts it. Then remove the inner segments, update the outer legs, and add one arc.
- If the merge is rejected, fall back to separate arcs.
- S-jogs and bumps (opposite turn signs) are never merged. They use proportional allocation, so each arc stays tangent.

- [ ] **Step 1: Failing tests:**
  - `test_two_45s_merge_into_one_arc`: legs 10 mm, two 45° bends joined by 0.3 mm, scaling 2 mm → one arc, `stats['merged'] == 1`, inner segment in `removals`, arc tangent to both outer legs.
  - `test_merge_rejected_falls_back`: `fits` rejects any arc with radius > 1 mm → two arcs, `merged == 0`.
  - `test_s_jog_not_merged_and_tangent`: opposite turns joined by 0.6 mm → two arcs, both ending on the 0.6 mm segment, end-to-end gap ≥ 0.
  - `test_stair_step_all_tangent`: four 45° steps with 0.5 mm treads → every arc endpoint lies on a remaining segment's endpoint.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `Merge runs of close same-direction bends`.

### Task 6: Board layer: collect, commit, headless entry point, fixtures

**Files:**
- Create: `round_tracks_board.py`, `tests/fixtures.py`, `tests/drc.py`, `tests/test_cases.py`
- Modify: `round_tracks_utils.py` (keep `withinPad` and the helpers that Unround uses)

**Interfaces:**
- Consumes: `plan_fillets`, `Seg`, `Arc`, `Plan`.
- Produces (in `round_tracks_board.py`):
  - `@dataclass ClassSetting: do_round: bool; radius_mm: float`
  - `@dataclass Settings: classes: dict[str, ClassSetting]; min_radius_mm: float = 0.1; only_selection: bool = False; avoid_junctions: bool = False`
  - `primary_class(net) -> str`
  - `round_board(board, settings: Settings, fits=None) -> collections.Counter` (`fits=None` → `ClearanceChecker` from Task 7; until then, always True)
  - `commit(board, plan: Plan, objects: dict[int, pcbnew.PCB_TRACK], select: bool) -> None`
- Produces (tests):
  - `fixtures.Board` builder: `.net(name)`, `.track(x1,y1,x2,y2,net,layer=F_Cu,w=0.25)`, `.via(x,y,net,size=0.6,drill=0.3)`, `.smd_pad(x,y,w,h,net,layer)`, `.tht_pad(x,y,size,drill,net)`, `.npth(x,y,drill)`, `.edge_rect(x1,y1,x2,y2)`, `.keepout(pts, layer)`, `.save(dir) -> path` (mm inputs)
  - `fixtures.write_project(path, netclasses: dict[str, dict], patterns: list[tuple[str,str]])` writes a minimal `.kicad_pro` with `net_settings`. It allows partial netclass dicts, as in the user's project (e.g. `{"clearance": 0.2, "track_width": 0.5}`), which produce composite `"Power,Default"` classes.
  - `drc.run(path) -> dict` (kicad-cli JSON); `drc.clearance_violations(report) -> list` (filter on `type` in the clearance family; confirm the exact type strings once against a board with a known violation and pin them in a constant)
  - `fixtures.assert_tangent(test, board)`: every `PCB_ARC` endpoint coincides with a `PCB_TRACK` endpoint, and the radius vector is perpendicular to that track (|cos| < 1e-3)

Board-layer behaviour:
- Scope = nets whose `primary_class` row has `do_round`. With `only_selection`, keep only selected tracks.
- Anchors = via positions, arc endpoints, endpoints of out-of-scope tracks, and corner points `withinPad` (legacy rule, using `p.IsSelected()` and fixing the stale-`t` bug).
- `scaling_by_net` = radius_mm in nm.
- `commit` applies the updates, removes the removals, and adds arcs. It sets each new arc's net *after* `board.Add` (legacy order).

- [ ] **Step 1: Failing case tests** (`class TestCases`, each building a board in a temp dir):
  - `test_case_90_and_45`: radii 2.0 mm and ≈5.44 mm; `assert_tangent`; DRC clearance violations before == after.
  - `test_composite_netclass_uses_primary`: project with partial `Power`, pattern `GND`. Assert `GetNetClassName()` is `"Power,Default"` (precondition), then that GND is rounded with Power's radius exactly once (one arc per corner).
  - `test_disabled_class_untouched` and `test_selection_only`: track geometry of out-of-scope tracks is byte-identical.
  - `test_rerun_does_not_double_round`: run twice → arc count unchanged.
  - `test_corner_on_via_skipped`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `Add headless board layer and case-test fixtures`.

### Task 7: Clearance checker

**Files:** Modify `round_tracks_board.py`; Test `tests/test_cases.py`

**Interfaces:**
- Produces `class ClearanceChecker: __init__(self, board, layer_items_cache=None); fits(self, arc: Arc) -> bool; accept(self, arc: Arc) -> None`. `round_board` wraps it as `fits = lambda a: checker.fits(a) and (checker.accept(a) or True)` (or the equivalent), so accepted arcs block later ones.

Behaviour (spec step 6):
- Build an unattached `pcbnew.PCB_ARC(board)` from the `Arc` and take `GetEffectiveShape(layer)`.
- Candidates are other-net items whose bounding box, inflated by the clearance, overlaps the arc's bounding box:
  - pads: THT/NPTH on all copper layers, SMD only on their layer;
  - vias spanning the layer;
  - tracks and arcs;
  - accepted arcs from this run.
- Clearance = `max(arc_net_clearance, item.GetOwnClearance(layer))`. The arc's own clearance comes from its net's effective netclass; read it from any track on that net.
- Also check:
  - hole clearance `ds.m_HoleClearance` against `GetEffectiveHoleShape()` of pads and vias;
  - copper-to-edge `ds.m_CopperEdgeClearance` against `Edge.Cuts` shapes;
  - rule-area zones with `GetDoNotAllowTracks()` on the layer: any overlap fails.
- Ignore filled copper zones.

- [ ] **Step 1: Failing case tests:** `test_via_inside_bend_shrinks_arc` (DRC clean after; the arc exists but its radius is below the setting); `test_pad_inside_bend`; `test_npth_inside_bend`; `test_board_edge_inside_bend`; `test_keepout_inside_bend`; `test_merge_rejected_by_via`; `test_tht_pad_blocks_bottom_layer`; `test_performance_500_corners` (500 L-corners spread out, `round_board` < 20 s).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `Add clearance-aware arc checking`.

### Task 8: Warning arrows and summary

**Files:** Modify `round_tracks_board.py`; Test `tests/test_cases.py`

**Interfaces:**
- Produces `draw_warnings(board, sharps: list[Sharp]) -> None` and `summary_text(stats: Counter) -> str`. `round_board` calls `draw_warnings` after `commit`.

Behaviour:
- Remove any existing group named `round-tracks-warnings` and its items.
- If there are sharps, create the group. For each sharp:
  - a 1.5 mm arrow on `Cmts_User`: a shaft plus two head segments, pointing at the corner from the upper-left at 45°, line width 0.1 mm;
  - a `PCB_TEXT` at the tail: `"round-tracks: too short"` or `"round-tracks: clearance"`, height 0.5 mm.
- `summary_text` example: `"Rounded 142 corners (130 full, 9 reduced, 3 merged). Left sharp: 2 too short, 1 clearance. Skipped: 4."`

- [ ] **Step 1: Failing tests:** `test_sharp_gets_arrow` (1 group, 1 text, ≥3 shapes on `Cmts_User`); `test_rerun_replaces_warning_group` (still exactly one group after two runs); `test_empty_scope` (all-zero stats, no group); `test_summary_text` (exact string for a given Counter).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `Mark sharp corners with warning arrows`.

### Task 9: Dialog, config, and Unround

**Files:**
- Create: `round_tracks_config.py`, `tests/test_config.py`
- Modify: `round_tracks_action.py`, `round_tracks_gui.py`, `round_tracks_ui.fbp` (keep the generated GUI and the `.fbp` in sync by hand)

**Interfaces:**
- Produces `parse_config(text: str) -> Settings` and `format_config(settings: Settings, new_file: bool) -> str`, plus `read_new_file(text) -> bool`.

Behaviour:
- Class lines keep 4 tab-separated fields: `name, do_round, radius, 3` (passes is written as `3` and ignored on read), so upstream's plugin can still read the file.
- The checkbox line keeps 3 bools: `new_file, True, avoid_junctions`.
- New line: `min_radius\t<float>`. Upstream's parser silently ignores it.
- Old files without `min_radius` load with 0.1.
- In the dialog:
  - remove the *Passes #* column and the *use native fillets* checkbox;
  - add a `wx.TextCtrl` labelled *Min radius (mm)*;
  - `run()` builds `Settings` and calls `round_board`, then shows `summary_text` in the existing "Done" box;
  - delete `addIntermediateTracks`;
  - rename the *Unround (native fillets only)* button to *Unround*.
- In `unrun`, skip arcs where `interdistance > 10 * a.GetRadius()`, and count them in the existing failure message.

- [ ] **Step 1: Failing tests:** `test_parse_legacy_file` (upstream format with passes and 3-bool line → classes and flags, min 0.1); `test_round_trip`; `test_upstream_parser_tolerates_new_file` (paste upstream's `load_config` loop body into the test as a function and assert it doesn't raise on `format_config` output).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement the config module, then the dialog wiring.
- [ ] **Step 4:** Run the full suite → PASS. Also `python -c "import ast; ast.parse(open('round_tracks_action.py').read())"` for every changed `.py`.
- [ ] **Step 5:** Commit `Wire new engine into dialog; drop subdivision mode`.

### Task 10: Problem-case regression suite and docs

**Files:** Modify `tests/test_cases.py`, `README.md`, `metadata.json.template` (description: drop "subdivision")

- [ ] **Step 1:** Add end-to-end cases (board + `round_board` + `assert_tangent` + no new DRC clearance violations): `test_problem1_hidden_sliver`, `test_problem2_stair_step`, `test_problem2_bump`, `test_problem2_s_jog`, `test_problem2_close_45s_merge`, `test_problem3_never_non_tangent` (mix of the above on one board), `test_problem4_via_clearance`, `test_t_junction_blocked_leg_all_sharp`, `test_min_radius_floor_arrow`.
- [ ] **Step 2:** Run the full suite → PASS (fix the engine where a case fails; don't weaken the asserts).
- [ ] **Step 3:** Update the README:
  - remove the subdivision/passes instructions;
  - explain the radius meaning (true radius at 90°, larger for shallower bends) and Min radius;
  - explain the clearance behaviour and the warning-arrow group;
  - explain how to run the tests;
  - add a fork note crediting mitxela.
- [ ] **Step 4:** Commit `Add problem-case regression tests and update docs`, then `git push -u origin adaptive-fillets`.

### Task 11 (gated): Install and real-board check

**Gate:** start only after the user says ecoport-bridge is committed and pushed.

- [ ] **Step 1:** Back up the installed plugin dir. Then copy the repo's `*.py` and `round_tracks.png` into it, after the user confirms.
- [ ] **Step 2:** Headless run on a *copy* of the user's board(s) in the scratchpad: `round_board` + DRC before/after. Report the summary, new violations (expected 0), and sharp-corner locations.
- [ ] **Step 3:** User runs the plugin in KiCad (Refresh Plugins). Collect feedback; any fixes go back through tests first.
