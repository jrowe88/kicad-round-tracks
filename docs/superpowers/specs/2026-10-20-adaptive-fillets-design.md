# Adaptive, clearance-aware fillets — design

Fork: `jrowe88/kicad-round-tracks` (upstream `mitxela/kicad-round-tracks`, last upstream commit 50374f8).
Target: KiCad 10.x, SWIG `pcbnew` Python API (deprecated but present until KiCad 11).

## Intent

Fillet a whole board, or a selection, in one pass, with results as good as careful hand
filleting (KiCad's *Fillet Tracks*), and with no new DRC violations to fix afterwards.

The user tried this plugin on a real board (ecoport-bridge), gave up, and filleted by hand.
What went wrong by hand and with the plugin:

1. **Hidden short segments.** Tiny leftover segments right before a corner make one leg
   too short, so the corner is skipped or the radius collapses.
2. **Closely spaced corners.** Zig-zags, bumps like `__/‾‾‾\__`, and 45° stair-steps:
   corners that share a short segment compete for its length. By hand, the user got better
   results by simplifying two nearby bends into one corner and filleting that.
3. **Skipped joints next to arcs.** When one joint was skipped, the result sometimes had a
   curve meeting a straight non-tangentially. That looks worse than a sharp corner.
4. **Via clearance.** KiCad's UI shows pad clearance but not via clearance. All the DRC
   errors after hand filleting were arcs cutting too close to vias.

## Goals

- **G1. Correct netclass matching on KiCad 10** (composite netclasses such as
  `"Power,Default"`). Each net gets its highest-priority class's settings; see step 1a.
  (Upstream PR #35 solves the same bug but would match a multi-class net more than once.)
- **G2. Fix the pad-selection bug** in `addIntermediateTracks`: the pad loop tests
  `t.IsSelected()` (a stale track variable) instead of `p.IsSelected()`.
- **G3. Native arcs only.** Remove the subdivision (non-native) algorithm, the *Passes*
  column, and the *use native fillets* checkbox.
- **G4. Cleanup pre-pass.** Within the scope being rounded, merge consecutive collinear
  segments and remove near-zero-length segments, so hidden fragments stop blocking corners.
- **G5. Fair sharing of short segments.** When two corners share a segment that can't fit
  both full fillets, split its length between them in proportion to what each corner needs
  (instead of the current flat "half of the shortest leg"), so a shallow bend doesn't steal
  length from a sharp one.
- **G6. Bend-run merging.** A run of same-direction bends joined by too-short segments
  (e.g. two 45° bends close together) can be replaced by one arc tangent to the two outer
  legs, which is what the user did by hand. Use it when it gives a larger radius than
  fillets for each bend separately, and only if it passes the clearance check.
- **G7. Clearance-aware radius.** Before committing each arc, check it against copper on
  other nets and shrink it until it clears. If it still doesn't clear at the minimum
  radius, leave that corner sharp.
- **G8. Compute, then commit.** Calculate every change before touching the board, then
  apply all changes at once (one undo step, as action plugins already get).
- **G9. Run summary.** After a run, report counts: rounded at full radius, reduced for
  clearance or length, merged runs, left sharp (by reason).
- **G10. Warning arrows on the board** (user request: "leverage the DRC methodology").
  Every corner left sharp gets a marker so the user can find it. Real DRC markers aren't
  possible: `pcbnew.PCB_MARKER` exists but `DRC_ITEM`, which it needs, isn't exposed to
  Python in KiCad 10.0.7 (checked). Instead, draw an arrow pointing at the corner, plus a
  short text with the reason, on the `User.Comments` layer. Put all markers in one
  `PCB_GROUP` named `round-tracks-warnings` so the user can delete them in one click. Each
  run first removes the previous run's group. *(Layer and group name: Claude's call.)*
- **G11. Specific-case tests** (user request). Each problem above is captured as a named
  test case. See *Testing* below.

## Non-goals

- Porting to the IPC API / kicad-python. (Keep the geometry in pure-Python modules so a
  future port only replaces the board-access layer.)
- Custom DRC rules in `.kicad_dru`. Clearances come from netclasses and board design
  settings only; the user must still run DRC (the dialog already says so).
- Refilling zones. Zone fills are ignored for clearance; KiCad refills them.
- Differential-pair awareness. Pairs are filleted as independent tracks.
- Rounding across layer changes, or rerouting anything beyond the corner region.

## Anti-goals (failures even if the code "works")

- **A curve meeting a straight non-tangentially.** Every arc must be tangent to both of
  its neighbouring segments. If a tangent arc can't be placed, the corner stays sharp.
- **New DRC clearance violations** against items the check covers (pads, vias, tracks,
  arcs, holes, board edge, keepouts).
- **Broken connectivity.** Never move an endpoint that lands on a pad or via, or a
  junction of three or more tracks unless junction rounding is enabled.
- **Touching tracks outside the scope** (unselected tracks, disabled netclasses), except
  that the cleanup and merge steps may only modify segments that are themselves in scope.

## Constraints

- Plugin runs in-process via `pcbnew.ActionPlugin` with wxPython UI, KiCad 10.x.
- APIs confirmed available on KiCad 10.0.7: `GetEffectiveShape(layer)` on tracks, arcs
  (including an unattached `pcbnew.PCB_ARC(board)`), pads and vias; `SHAPE.Collide(shape,
  clearance)`; `GetOwnClearance(layer)`; `board.GetDesignSettings()`; `WriteDRCReport`.
- Keep the existing per-board config file (`<board>.round-tracks-config`); read old-format
  files without error.
- Keep Apache-2.0 headers and existing credits; match the existing code style.
- Keep existing features: per-netclass enable + radius, *create a new file*,
  *ignore junctions*, selection-only mode, skipping corners inside pads or touching
  vias/arcs, and the *Unround* button.

## How it works (decided parts)

Order of operations per run:

1. **Scope.** Collect in-scope tracks: enabled netclasses (G1), and if anything is
   selected, selected tracks only.
1a. **Netclass per net.** Each net is handled once, using the settings of its
   highest-priority class: the first part of its effective netclass name (e.g. `Power`
   for `"Power,Default"`). A net whose name is plain `Default` uses the Default row. This
   differs from PR #35's matcher, which would match a multi-class net more than once.
   *(Claude's call.)*
2. **Cleanup (G4).** Per net, per layer: merge two in-scope straight segments when they
   share an endpoint, have the same width, are collinear within the existing angle
   tolerance, and that shared point touches nothing else (no third track, pad, or via).
   Remove slivers shorter than 1 µm when doing so leaves connectivity intact. Short
   segments that are *not* collinear are left alone here; G5 and G6 handle them. Cleanup
   never moves copper sideways, so it needs no clearance check. *(Claude's call.)*
3. **Corners.** Find corners (shared endpoints between two straight segments) as today.
4. **Length allocation (G5).** *Radius keeps today's meaning* (user decision): the
   netclass value is the true arc radius for a 90° bend, and other angles use the
   existing formula. With `RADIUS = scaling / (sin 45° + 1)` and `h` = half the angle
   between the two tracks, a corner asks each leg for a setback of `RADIUS · (sin h + 1)`.
   If the two requests on one segment exceed its length, both are scaled down
   proportionally to fit. Ends at pads/vias/free ends ask for nothing. A corner's usable
   setback is the smaller of its two legs' allocations; the arc's actual radius follows
   from the setback and the angle. (The README should explain this meaning when docs are
   updated.)
4a. **Min radius is a hard floor** (user decision). A corner whose usable arc radius
   (true radius, in mm) would be below *Min radius*, whether because of length or
   clearance, is left sharp and gets a warning arrow (G10).
4b. **Junctions (3+ tracks).** Keep today's behaviour: with *ignore junctions*
   unchecked, a junction gets an arc between each pair of adjacent legs, with every leg
   shortened by the same setback (otherwise the arcs wouldn't meet the legs). So a
   junction is all-or-nothing: if any of its arcs fails clearance, shrink the shared
   setback for all of them; if that hits Min radius, the whole junction stays sharp.
   Junctions never take part in merging (G6). *(Claude's call.)*
5. **Merging (G6).** For a run of same-direction corners joined by segments too short to
   give full radius, try one arc tangent to the run's two outer legs. Keep it if its
   radius is larger than the best of the separate arcs and it passes step 6; otherwise
   keep the separate arcs.
6. **Clearance (G7).** For each candidate arc (built as an unattached `PCB_ARC`, never
   added to the board), test `GetEffectiveShape(layer).Collide(other, clearance)` against
   nearby other-net items on that layer: pads (THT pads on every copper layer), vias,
   existing tracks/arcs, and arcs already accepted in this run. Clearance is the larger of
   the two items' `GetOwnClearance(layer)`. Also check hole clearance against drilled
   holes, copper-to-edge clearance against `Edge.Cuts`, and keepout zones that forbid
   tracks. Only the new arc needs checking: shortening the legs only removes copper.
   On collision, binary-search the radius down to the minimum radius. If even that fails,
   the corner stays sharp. (Shrinking the arc pulls it back toward the original corner,
   which was presumably clear.)
7. **Commit (G8).** Apply shortenings, removals, new arcs, and warning arrows to the
   board together.
8. **Summary (G9).** Show the counts in the existing "Done" message box.

**Unround** stays approximate, as its message already says. It rebuilds each corner where
the arc's tangent lines meet. For a merged arc (G6) that point is one virtual corner the
board never had, and cleanup merges can't be reversed. Ctrl+Z is the exact undo. Unround
skips arcs whose rebuilt corner would land more than 10× the arc's radius away (nearly
180° turns). *(Claude's call.)*

Code layout:

- `round_tracks_geom.py` (new): pure Python, no `pcbnew` import. Points are integer nm
  tuples. Holds cleanup, corner finding, length allocation, merge, and arc geometry.
- `round_tracks_action.py`: the board-access layer (reading tracks/pads/vias into plain
  data, clearance checks via `pcbnew` shapes, commit) and the dialog wiring. The core run
  is callable without the dialog as `round_board(board, settings) -> summary`, so it can
  run headless via `pcbnew.LoadBoard`.
- Tests: see *Testing*.

## Testing

Two layers, both runnable from the command line with KiCad's bundled Python
(`C:\Program Files\KiCad\10.0\bin\python.exe`), no KiCad GUI needed:

1. **Unit tests** (stdlib `unittest`) on `round_tracks_geom.py`: setback formula,
   proportional allocation, merge geometry, tangency, cleanup.
2. **Case tests**: each case builds a small synthetic board in code with `pcbnew`, runs
   `round_board` headless, then asserts on the result. Assertions cover arc count,
   radii, which corners stayed sharp and why, warning-arrow count, connectivity
   unchanged, and every arc tangent to its neighbours. Each case also runs DRC with
   `kicad-cli pcb drc --format json` and asserts no *new* clearance violations
   (`pcbnew.WriteDRCReport` crashes without a running KiCad, found while planning).
   Cases:
   - plain 90° corner, and 45° corner (radius semantics);
   - hidden sliver / collinear fragment just before a corner (problem 1);
   - 45° stair-step with short treads (problems 2, 3);
   - bump `__/‾‾‾\__` and S-jog with a short middle segment (problem 2);
   - two same-direction 45° bends close together → one merged arc (G6);
   - merge rejected because the merged arc would hit a via;
   - via inside the bend (problem 4), pad inside the bend, NPTH hole, board edge,
     keepout zone;
   - corner that can only fit an arc below Min radius → left sharp + arrow;
   - T-junction: rounded, and all-or-nothing when one leg is blocked;
   - net in a composite netclass (`"Power,Default"`) uses Power's settings, once;
   - disabled netclass and selection-only mode leave out-of-scope tracks untouched;
   - corner touching a pad or via is skipped;
   - re-running replaces the previous warning group.

Real boards (ecoport-bridge and others the user supplies) are a final check, on copies,
after the case tests pass. That waits until the user says ecoport-bridge is committed
and pushed.

Dialog: per-netclass table keeps *Net Class*, *Rounding*, *Radius (mm)*; *Passes*
column removed; *use native fillets* checkbox removed; a global *Min radius (mm)* field
added.

## Left to the builder

- Spatial filtering for the clearance search (bounding-box prefilter is enough).
- Binary-search depth / stop tolerance.
- Exact data structures in the geometry module.
- Config file format details, as long as old files still load.
- How the summary is worded.
