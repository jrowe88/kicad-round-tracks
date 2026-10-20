import math
import os
import shutil
import tempfile
import time
import unittest

import pcbnew

from collections import Counter

from round_tracks_board import (ClassSetting, ClearanceChecker, Settings, round_board,
                                summary_text)
from round_tracks_geom import SLIVER_NM, Arc
from tests import drc, fixtures

MM = 1_000_000


def arcs(board):
    return [t for t in board.GetTracks() if t.GetClass() == 'PCB_ARC']


def geometry(board, net):
    """Byte-level snapshot of a net's straight tracks."""
    return sorted((t.GetStart().x, t.GetStart().y, t.GetEnd().x, t.GetEnd().y,
                   t.GetWidth(), t.GetLayer())
                  for t in board.GetTracks()
                  if t.GetClass() == 'PCB_TRACK' and t.GetNetname() == net)


def ends_at(t, x, y):
    p = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
    return t.GetStart() == p or t.GetEnd() == p


class TestCases(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def load(self, b, netclasses=None, patterns=()):
        path = b.save(self.dir)
        if netclasses is not None:
            fixtures.write_project(path, netclasses, list(patterns))
        return path, pcbnew.LoadBoard(path)

    def test_case_90_and_45(self):
        b = fixtures.Board()
        b.edge_rect(0, 0, 100, 60)
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 30, 40, 'SIG')        # 90 degree corner
        b.track(50, 10, 70, 10, 'SIG2')
        b.track(70, 10, 90, 30, 'SIG2')       # 45 degree corner
        path, board = self.load(b)
        before = drc.clearance_violations(drc.run(path))

        round_board(board, Settings({'Default': ClassSetting(True, 2.0)}))

        radii = sorted(a.GetRadius() / MM for a in arcs(board))
        self.assertEqual(len(radii), 2)
        self.assertAlmostEqual(radii[0], 2.0, delta=0.002)
        self.assertAlmostEqual(radii[1], 5.44, delta=0.01)
        fixtures.assert_tangent(self, board)
        after_path = fixtures.save_copy(board, os.path.join(self.dir, 'after.kicad_pcb'))
        after = drc.clearance_violations(drc.run(after_path))
        self.assertEqual(sorted(v['type'] for v in after), sorted(v['type'] for v in before))

    def test_composite_netclass_uses_primary(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'GND')
        b.track(30, 10, 30, 30, 'GND')
        b.track(50, 10, 70, 10, 'SIG')
        b.track(70, 10, 70, 30, 'SIG')
        _, board = self.load(b, {'Power': {'clearance': 0.2, 'track_width': 0.5}},
                             [('Power', 'GND')])
        gnd = board.FindNet('GND')
        self.assertEqual(gnd.GetNetClassName(), 'Power,Default')

        round_board(board, Settings({'Default': ClassSetting(True, 1.0),
                                     'Power': ClassSetting(True, 3.0)}))

        by_net = {}
        for a in arcs(board):
            by_net.setdefault(a.GetNetname(), []).append(a.GetRadius() / MM)
        self.assertEqual(len(by_net['GND']), 1)
        self.assertAlmostEqual(by_net['GND'][0], 3.0, delta=0.002)
        self.assertEqual(len(by_net['SIG']), 1)
        self.assertAlmostEqual(by_net['SIG'][0], 1.0, delta=0.002)
        fixtures.assert_tangent(self, board)

    def test_disabled_class_untouched(self):
        for name, classes in (
                ('disabled', {'Default': ClassSetting(False, 2.0),
                              'Power': ClassSetting(True, 2.0)}),
                ('missing', {'Power': ClassSetting(True, 2.0)})):
            with self.subTest(name):
                self.dir = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, self.dir, True)
                b = fixtures.Board()
                b.track(10, 10, 30, 10, 'GND')
                b.track(30, 10, 30, 30, 'GND')
                b.track(50, 10, 70, 10, 'SIG')
                b.track(70, 10, 70, 30, 'SIG')
                b.track(70, 30, 70, 30.0005, 'SIG')   # sliver: cleanup must not touch it
                _, board = self.load(b, {'Power': {'clearance': 0.2}}, [('Power', 'GND')])
                sig = geometry(board, 'SIG')

                round_board(board, Settings(classes))

                self.assertEqual(geometry(board, 'SIG'), sig)
                self.assertEqual([a.GetNetname() for a in arcs(board)], ['GND'])

    def test_selection_only(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 30, 30, 'SIG')        # both selected: rounded
        b.track(50, 10, 70, 10, 'SIG')
        b.track(70, 10, 70, 30, 'SIG')        # unselected: untouched
        b.track(90, 10, 110, 10, 'SIG')
        b.track(110, 10, 110, 30, 'SIG')      # half selected: corner is an anchor
        b.track(130, 10, 150, 10, 'SIG')
        b.track(150, 10, 150, 30, 'SIG')      # selected corner ...
        b.track(150, 10, 160, 0, 'SIG')       # ... touching an unselected track
        _, board = self.load(b)
        for t in board.GetTracks():
            if (ends_at(t, 30, 10) or (ends_at(t, 110, 10) and ends_at(t, 90, 10))
                    or (ends_at(t, 150, 10) and not ends_at(t, 160, 0))):
                t.SetSelected()
        out = sorted(g for g in geometry(board, 'SIG') if g[0] >= 50 * MM or g[2] >= 50 * MM)

        round_board(board, Settings({'Default': ClassSetting(True, 2.0)}, only_selection=True))

        self.assertEqual(len(arcs(board)), 1)
        self.assertTrue(arcs(board)[0].IsSelected())
        self.assertEqual(sorted(g for g in geometry(board, 'SIG')
                                if g[0] >= 50 * MM or g[2] >= 50 * MM), out)
        fixtures.assert_tangent(self, board)

    def test_selection_only_respects_unselected_pad(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 30, 30, 'SIG')
        b.smd_pad(30, 10, 1.0, 1.0, 'SIG')    # corner inside a pad, legs leave it
        _, board = self.load(b)
        for t in board.GetTracks():
            t.SetSelected()                   # tracks only; the pad stays unselected
        sig = geometry(board, 'SIG')

        round_board(board, Settings({'Default': ClassSetting(True, 2.0)}, only_selection=True))

        self.assertEqual(arcs(board), [])
        self.assertEqual(geometry(board, 'SIG'), sig)

    def test_rerun_does_not_double_round(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 30, 30, 'SIG')
        b.track(30, 30, 50, 30, 'SIG')
        _, board = self.load(b)
        settings = Settings({'Default': ClassSetting(True, 2.0)})

        round_board(board, settings)
        first = (len(arcs(board)), geometry(board, 'SIG'))
        round_board(board, settings)

        self.assertEqual(first[0], 2)
        self.assertEqual((len(arcs(board)), geometry(board, 'SIG')), first)

    def test_corner_on_via_skipped(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 30, 30, 'SIG')
        b.via(30, 10, 'SIG')
        b.track(50, 10, 70, 10, 'SIG')
        b.track(70, 10, 70, 30, 'SIG')
        b.smd_pad(70, 10, 1.0, 1.0, 'SIG')    # corner inside a pad, legs leave it
        b.track(90, 10, 110, 10, 'SIG')
        b.track(110, 10, 110, 30, 'SIG')
        arc = pcbnew.PCB_ARC(b.board)         # corner where an existing arc ends
        arc.SetStart(pcbnew.VECTOR2I(110 * MM, 10 * MM))
        arc.SetMid(pcbnew.VECTOR2I(round((110 + 5 - 5 / math.sqrt(2)) * MM),
                                   round((10 - 5 / math.sqrt(2)) * MM)))
        arc.SetEnd(pcbnew.VECTOR2I(115 * MM, 5 * MM))
        arc.SetWidth(250000)
        b.board.Add(arc)
        arc.SetNet(b.net('SIG'))
        _, board = self.load(b)
        sig = geometry(board, 'SIG')

        stats = round_board(board, Settings({'Default': ClassSetting(True, 2.0)}))

        self.assertEqual(len(arcs(board)), 1)
        self.assertEqual(geometry(board, 'SIG'), sig)
        self.assertEqual(stats['full'], 0)

    # --- clearance (Task 7) ---------------------------------------------------
    # Most cases put an obstacle of net OTHER inside a 90 degree bend at (30, 10)
    # whose full 3 mm arc would pass 1.24 mm from the corner. The obstacle sits
    # clear of the original tracks, so the arc must shrink to stay clear of it.

    def bend(self, b, layer=pcbnew.F_Cu, net='SIG'):
        b.track(10, 10, 30, 10, net, layer)
        b.track(30, 10, 30, 30, net, layer)

    def round_and_check(self, b, radius=3.0, min_radius=0.1):
        """Load, DRC, round, DRC again: no new clearance-family violation."""
        path, board = self.load(b)
        before = drc.clearance_violations(drc.run(path))
        stats = round_board(board, Settings({'Default': ClassSetting(True, radius)},
                                            min_radius_mm=min_radius))
        fixtures.assert_tangent(self, board)
        nets = [t.GetNetname() for t in board.GetTracks()]
        after_path = fixtures.save_copy(board, os.path.join(self.dir, 'after.kicad_pcb'))
        # An arc that shorts onto another net's pad or via makes SaveBoard hand
        # that net to the whole chain, which would hide the short from DRC.
        self.assertEqual([t.GetNetname() for t in board.GetTracks()], nets)
        after = drc.clearance_violations(drc.run(after_path))
        self.assertEqual(sorted(v['type'] for v in after), sorted(v['type'] for v in before),
                         after)
        return board, stats

    def assert_shrunk(self, board, stats, below=3.0, above=0.1):
        radii = [a.GetRadius() / MM for a in arcs(board) if a.GetNetname() == 'SIG']
        self.assertEqual(len(radii), 1, radii)
        self.assertLess(radii[0], below)
        self.assertGreater(radii[0], above)
        self.assertEqual(stats['reduced'], 1)
        return radii[0]

    def test_via_inside_bend_shrinks_arc(self):
        b = fixtures.Board()
        self.bend(b)
        b.via(29.2, 10.8, 'OTHER')
        board, stats = self.round_and_check(b)
        # The via's copper needs 0.3 + 0.125 + 0.2 mm from the arc centreline,
        # which allows a radius of about 1.22 mm.
        r = self.assert_shrunk(board, stats, below=1.23)
        self.assertGreater(r, 1.0)

    def test_pad_inside_bend(self):
        b = fixtures.Board()
        self.bend(b)
        b.smd_pad(29.2, 10.8, 0.5, 0.5, 'OTHER')
        board, stats = self.round_and_check(b)
        self.assert_shrunk(board, stats)

    def test_npth_inside_bend(self):
        b = fixtures.Board()
        self.bend(b)
        b.npth(29.2, 10.8, 0.6)
        board, stats = self.round_and_check(b)
        self.assert_shrunk(board, stats)

    def test_board_edge_inside_bend(self):
        b = fixtures.Board()
        b.edge_rect(0, 0, 60, 40)
        b.edge_rect(28.5, 10.7, 29.3, 11.5)   # cutout inside the bend
        self.bend(b)
        board, stats = self.round_and_check(b)
        # 0.5 mm edge clearance + half width from the cutout corner: about 0.88 mm.
        self.assert_shrunk(board, stats, below=0.9)

    def test_keepout_inside_bend(self):
        b = fixtures.Board()
        self.bend(b)
        b.keepout([(28.5, 10.7), (29.3, 10.7), (29.3, 11.5), (28.5, 11.5)])
        board, stats = self.round_and_check(b)
        self.assert_shrunk(board, stats, below=2.1)

    def two_45s(self, b):
        d = 0.3 / math.sqrt(2)
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 30 + d, 10 + d, 'SIG')   # two 45 degree bends 0.3 mm apart
        b.track(30 + d, 10 + d, 30 + d, 30, 'SIG')

    def test_merge_rejected_by_via(self):
        b = fixtures.Board()
        self.two_45s(b)
        b.via(29.4, 10.8, 'OTHER')               # where the merged 2 mm arc would run
        board, stats = self.round_and_check(b, radius=2.0)
        self.assertEqual(stats['merged'], 0)
        radii = [a.GetRadius() / MM for a in arcs(board)]
        self.assertEqual(len(radii), 2)
        self.assertTrue(all(r < 1.0 for r in radii), radii)

    def test_merge_accepted_without_via(self):
        # Control for test_merge_rejected_by_via: the checker lets the merge through.
        b = fixtures.Board()
        self.two_45s(b)
        b.via(25, 20, 'OTHER')
        board, stats = self.round_and_check(b, radius=2.0)
        self.assertEqual(stats['merged'], 1)

    def test_tht_pad_blocks_bottom_layer(self):
        b = fixtures.Board()
        self.bend(b, layer=pcbnew.B_Cu)
        b.tht_pad(29.2, 10.8, 0.6, 0.3, 'OTHER')
        board, stats = self.round_and_check(b)
        self.assertEqual(arcs(board)[0].GetLayer(), pcbnew.B_Cu)
        self.assert_shrunk(board, stats, below=1.23)

    def test_smd_pad_on_other_layer_does_not_block(self):
        b = fixtures.Board()
        self.bend(b, layer=pcbnew.B_Cu)
        b.smd_pad(29.2, 10.8, 0.5, 0.5, 'OTHER')   # F.Cu only
        board, stats = self.round_and_check(b)
        self.assertEqual(stats['full'], 1)

    def test_same_net_via_does_not_block(self):
        b = fixtures.Board()
        self.bend(b)
        b.via(29.2, 10.8, 'SIG')
        board, stats = self.round_and_check(b)
        self.assertEqual(stats['full'], 1)

    def test_net_zero_not_rounded_but_blocks(self):
        b = fixtures.Board()
        self.bend(b)
        b.via(29.2, 10.8, 'OTHER').SetNetCode(0)
        for t in (b.track(50, 10, 70, 10, 'X'), b.track(70, 10, 70, 30, 'X')):
            t.SetNetCode(0)
        board, stats = self.round_and_check(b)
        self.assertEqual([a.GetNetname() for a in arcs(board)], ['SIG'])
        self.assert_shrunk(board, stats, below=1.23)

    def test_accepted_arc_blocks_other_nets_only(self):
        b = fixtures.Board()
        self.bend(b)
        b.via(80, 80, 'OTHER')
        _, board = self.load(b)
        sig, other = board.FindNet('SIG').GetNetCode(), board.FindNet('OTHER').GetNetCode()
        checker = ClearanceChecker(board)
        arc = Arc((40 * MM, 10 * MM), (41 * MM, 11 * MM), (42 * MM, 10 * MM), 250_000,
                  pcbnew.F_Cu, sig)
        near = Arc((40 * MM, 10_500_000), (41 * MM, 11_500_000), (42 * MM, 10_500_000),
                   250_000, pcbnew.F_Cu, other)
        self.assertTrue(checker.fits(arc))
        self.assertTrue(checker.fits(near))
        checker.accept(Arc(near.start, near.mid, near.end, near.width, near.layer, sig))
        self.assertTrue(checker.fits(arc))      # same net never blocks
        checker.accept(near)
        self.assertFalse(checker.fits(arc))     # an accepted other-net arc does
        self.assertTrue(checker.fits(Arc(arc.start, arc.mid, arc.end, arc.width,
                                         pcbnew.B_Cu, sig)))

    def test_t_junction_connector_committed(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 50, 10, 'SIG')
        b.track(30, 10, 30, 30, 'SIG')
        board, stats = self.round_and_check(b, radius=2.0)
        self.assertEqual(len(arcs(board)), 2)
        spans = sorted(sorted((t.GetStart().x, t.GetEnd().x)) for t in board.GetTracks()
                       if t.GetClass() == 'PCB_TRACK'
                       and t.GetStart().y == t.GetEnd().y == 10 * MM)
        # West leg, east leg, and the new bar across the junction between the arcs.
        self.assertEqual(len(spans), 3, spans)
        west, bar, east = spans
        self.assertEqual((west[1], east[0]), tuple(bar))
        self.assertAlmostEqual(sum(bar) / 2, 30 * MM, delta=2)
        self.assertGreater(bar[1] - bar[0], 0)

    def test_performance_500_corners(self):
        b = fixtures.Board()
        for i in range(500):
            x, y = (i % 25) * 6, (i // 25) * 6
            b.track(x, y, x + 4, y, 'SIG')
            b.track(x + 4, y, x + 4, y + 4, 'SIG')
            b.via(x + 3.2, y + 0.8, 'OTHER%d' % (i % 7))   # forces a radius search
            b.track(x, y + 5, x + 4, y + 5, 'OTHER%d' % (i % 5))
        _, board = self.load(b)
        start = time.perf_counter()
        stats = round_board(board, Settings({'Default': ClassSetting(True, 3.0)}))
        elapsed = time.perf_counter() - start
        print('\n500 corners: %.2f s, %s' % (elapsed, dict(stats)))
        self.assertLess(elapsed, 20)
        self.assertEqual(stats['reduced'], 500)

    def test_nothing_in_scope(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        _, board = self.load(b)
        stats = round_board(board, Settings({'Default': ClassSetting(False, 2.0)}))
        self.assertEqual(sum(stats.values()), 0)

    def sharp_board(self):
        b = fixtures.Board()
        b.track(10, 10, 10.08, 10, 'SIG')
        b.track(10.08, 10, 10.08, 10.08, 'SIG')   # legs too short for min radius
        return self.load(b)

    def warning_groups(self, board):
        return [g for g in board.Groups() if g.GetName() == 'round-tracks-warnings']

    def test_sharp_gets_arrow(self):
        _, board = self.sharp_board()
        stats = round_board(board, Settings({'Default': ClassSetting(True, 2.0)}))
        self.assertEqual(stats['sharp_length'], 1)
        groups = self.warning_groups(board)
        self.assertEqual(len(groups), 1)
        items = list(groups[0].GetItems())
        texts = [i for i in items if i.GetClass() == 'PCB_TEXT']
        shapes = [i for i in items if i.GetClass() == 'PCB_SHAPE']
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0].GetText(), 'round-tracks: too short')
        self.assertGreaterEqual(len(shapes), 3)
        for i in items:
            self.assertEqual(i.GetLayer(), pcbnew.Cmts_User)
        # Items are on the board, not orphaned.
        self.assertEqual(sum(1 for d in board.GetDrawings()
                             if d.GetLayer() == pcbnew.Cmts_User), len(items))

    def test_warning_group_survives_save_load(self):
        _, board = self.sharp_board()
        round_board(board, Settings({'Default': ClassSetting(True, 2.0)}))
        n = len(list(self.warning_groups(board)[0].GetItems()))
        path = fixtures.save_copy(board, os.path.join(self.dir, 'warn.kicad_pcb'))
        loaded = pcbnew.LoadBoard(path)
        groups = self.warning_groups(loaded)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(list(groups[0].GetItems())), n)

    def test_rerun_replaces_warning_group(self):
        _, board = self.sharp_board()
        settings = Settings({'Default': ClassSetting(True, 2.0)})
        round_board(board, settings)
        round_board(board, settings)
        self.assertEqual(len(self.warning_groups(board)), 1)
        self.assertEqual(sum(1 for d in board.GetDrawings()
                             if d.GetLayer() == pcbnew.Cmts_User),
                         len(list(self.warning_groups(board)[0].GetItems())))

    def test_empty_scope(self):
        _, board = self.sharp_board()
        round_board(board, Settings({'Default': ClassSetting(True, 2.0)}))
        stats = round_board(board, Settings({'Default': ClassSetting(False, 2.0)}))
        self.assertEqual(sum(stats.values()), 0)
        self.assertEqual(self.warning_groups(board), [])
        self.assertEqual(sum(1 for d in board.GetDrawings()
                             if d.GetLayer() == pcbnew.Cmts_User), 0)

    def test_summary_text(self):
        stats = Counter(full=130, reduced=9, merged=3, sharp_length=2,
                        sharp_clearance=1, skipped=4)
        self.assertEqual(summary_text(stats),
                         'Rounded 142 corners (130 full, 9 reduced, 3 merged). '
                         'Left sharp: 2 too short, 1 clearance. Skipped: 4.')
        self.assertEqual(summary_text(Counter()),
                         'Rounded 0 corners (0 full, 0 reduced, 0 merged). '
                         'Left sharp: 0 too short, 0 clearance. Skipped: 0.')

    # --- problem cases (spec "Intent" #1-#4) ---------------------------------
    # Each shape is drawn from an origin (x, y) so the mixed board can reuse it.

    def polyline(self, b, pts, net):
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            b.track(x1, y1, x2, y2, net)

    def sliver_corner(self, b, x, y, net='SIG'):
        """90 degree corner at (x + 20, y) hiding a collinear 0.5 mm fragment and
        a 0.7 um diagonal sliver right before it."""
        self.polyline(b, [(x, y), (x + 19.5, y), (x + 20, y),
                          (x + 20.0005, y + 0.0005), (x + 20.0005, y + 10)], net)

    def stair(self, b, x, y, net='SIG'):
        """Four 45 degree steps down-right: 0.5 mm risers, 0.5 mm treads."""
        d = 0.5 / math.sqrt(2)
        pts = [(x, y), (x + 10, y)]
        for _ in range(4):
            pts.append((pts[-1][0] + d, pts[-1][1] + d))
            pts.append((pts[-1][0] + 0.5, pts[-1][1]))
        pts[-1] = (pts[-1][0] + 10, pts[-1][1])
        self.polyline(b, pts, net)

    def bump(self, b, x, y, net='SIG'):
        """A __/---\\__ bump: 1 mm 45 degree flanks, 0.3 mm top."""
        d = 1 / math.sqrt(2)
        self.polyline(b, [(x, y), (x + 10, y), (x + 10 + d, y - d), (x + 10.3 + d, y - d),
                          (x + 10.3 + 2 * d, y), (x + 20.3 + 2 * d, y)], net)

    def s_jog(self, b, x, y, net='SIG'):
        """Reverse 45 degree bends joined by a 0.6 mm diagonal."""
        d = 0.6 / math.sqrt(2)
        self.polyline(b, [(x, y), (x + 10, y), (x + 10 + d, y + d), (x + 20 + d, y + d)], net)

    def close_45s(self, b, x, y, net='SIG'):
        """Two same-direction 45 degree bends 0.3 mm apart (a 90 degree turn)."""
        d = 0.3 / math.sqrt(2)
        self.polyline(b, [(x, y), (x + 10, y), (x + 10 + d, y + d), (x + 10 + d, y + 10)],
                      net)

    def zigzag(self, b, x, y, net='SIG'):
        """Reverse 90 degree bends 0.05 mm apart (too close for any arc), then a
        plain 90 degree corner at (x + 20, y + 0.05)."""
        self.polyline(b, [(x, y), (x + 10, y), (x + 10, y + 0.05), (x + 20, y + 0.05),
                          (x + 20, y + 10)], net)

    def radii(self, board, net='SIG'):
        return sorted(a.GetRadius() / MM for a in arcs(board) if a.GetNetname() == net)

    def straight(self, board, net='SIG'):
        return [t for t in board.GetTracks()
                if t.GetClass() == 'PCB_TRACK' and t.GetNetname() == net]

    def test_problem1_hidden_sliver(self):
        b = fixtures.Board()
        self.sliver_corner(b, 10, 10)
        board, stats = self.round_and_check(b, radius=2.0)
        # Without cleanup the 0.5 mm fragment would cap the radius at 0.5 mm.
        r = self.radii(board)
        self.assertEqual(len(r), 1)
        self.assertAlmostEqual(r[0], 2.0, delta=0.002)
        self.assertEqual((stats['full'], stats['sharp_length'], stats['sharp_clearance']),
                         (1, 0, 0))
        self.assertEqual(len(self.straight(board)), 2)

    def test_problem2_stair_step(self):
        b = fixtures.Board()
        self.stair(b, 10, 10)
        board, stats = self.round_and_check(b, radius=2.0)
        # Every tread and riser is shared half-and-half: a 0.25 mm setback at a
        # 135 degree bend is a 0.60 mm radius.
        r = self.radii(board)
        self.assertEqual(len(r), 8)
        for x in r:
            self.assertAlmostEqual(x, 0.25 * math.tan(math.radians(67.5)), delta=0.005)
        self.assertEqual((stats['merged'], stats['reduced']), (0, 8))
        self.assertEqual(stats['sharp_length'] + stats['sharp_clearance'], 0)

    def test_problem2_bump(self):
        b = fixtures.Board()
        self.bump(b, 10, 20)
        board, stats = self.round_and_check(b, radius=2.0)
        # The two top bends turn the same way across a 0.3 mm top: one 90 degree
        # arc. The bottom bends take half of each 1 mm flank, the merged arc the
        # rest, so the flanks are used up and the curve is smooth throughout.
        self.assertEqual((stats['merged'], stats['reduced'], stats['full']), (1, 2, 0))
        r = self.radii(board)
        self.assertEqual(len(r), 3)
        self.assertAlmostEqual(r[0], 0.712, delta=0.005)   # merged: 0.71 mm setback at 90
        for x in r[1:]:
            self.assertAlmostEqual(x, 0.5 * math.tan(math.radians(67.5)), delta=0.005)
        # Only the two outer runs are real tracks. Where the merged arc and a
        # bottom arc meet, independent rounding can leave a sub-micron connector,
        # which the planner keeps on purpose so the arcs stay connected.
        lengths = sorted(t.GetLength() for t in self.straight(board))
        self.assertEqual(lengths[-2:], [9.5 * MM, 9.5 * MM])
        self.assertTrue(all(x < SLIVER_NM for x in lengths[:-2]), lengths)

    def test_problem2_s_jog(self):
        b = fixtures.Board()
        self.s_jog(b, 10, 10)
        board, stats = self.round_and_check(b, radius=2.0)
        # Reverse curves never merge; they split the 0.6 mm diagonal and meet
        # each other tangentially in its middle.
        self.assertEqual((stats['merged'], stats['reduced']), (0, 2))
        r = self.radii(board)
        self.assertEqual(len(r), 2)
        for x in r:
            self.assertAlmostEqual(x, 0.3 * math.tan(math.radians(67.5)), delta=0.005)
        self.assertEqual(len(self.straight(board)), 2)

    def test_problem2_close_45s_merge(self):
        b = fixtures.Board()
        self.close_45s(b, 10, 10)
        board, stats = self.round_and_check(b, radius=2.0)
        self.assertEqual(stats['merged'], 1)
        self.assertEqual(stats['full'] + stats['reduced'], 0)
        r = self.radii(board)
        self.assertEqual(len(r), 1)
        self.assertAlmostEqual(r[0], 2.0, delta=0.005)    # one full 90 degree arc
        self.assertEqual(len(self.straight(board)), 2)     # the 0.3 mm segment is gone

    def test_problem3_never_non_tangent(self):
        b = fixtures.Board()
        self.sliver_corner(b, 10, 10, 'A')
        self.stair(b, 10, 30, 'B')
        self.bump(b, 10, 50, 'C')
        self.s_jog(b, 50, 10, 'D')
        self.close_45s(b, 50, 30, 'E')
        self.zigzag(b, 50, 50, 'F')
        b.track(90, 10, 110, 10, 'G')
        b.track(110, 10, 110, 30, 'G')
        b.via(109.2, 10.8, 'H')                            # shrinks G's corner
        board, stats = self.round_and_check(b, radius=2.0)
        # round_and_check asserts every arc is tangent. Corners left sharp next
        # to arcs keep their straight legs: F's zig-zag stays sharp while its
        # last corner still gets a full arc.
        self.assertEqual((stats['sharp_length'], stats['sharp_clearance']), (2, 0))
        self.assertEqual(stats['merged'], 2)               # C's top and E
        self.assertEqual([len(self.radii(board, n)) for n in 'ABCDEFG'],
                         [1, 8, 3, 2, 1, 1, 1])
        self.assertAlmostEqual(self.radii(board, 'F')[0], 2.0, delta=0.002)
        self.assertLess(self.radii(board, 'G')[0], 1.23)
        zig = (pcbnew.VECTOR2I(60 * MM, 50 * MM), pcbnew.VECTOR2I(60 * MM, 50_050_000))
        f_ends = [p for t in self.straight(board, 'F') for p in (t.GetStart(), t.GetEnd())]
        for p in zig:
            self.assertEqual(f_ends.count(p), 2)           # sharp corners untouched
        groups = self.warning_groups(board)
        self.assertEqual(len(groups), 1)
        self.assertEqual(sum(1 for i in groups[0].GetItems() if i.GetClass() == 'PCB_TEXT'),
                         2)

    def test_problem4_via_clearance(self):
        # Each via sits inside a bend, clear of the original tracks, where the
        # full arc would pass within the via's clearance without touching it.
        # (An arc touching it would make SaveBoard merge the nets and hide the
        # short from DRC.)
        h = math.radians(67.5)                             # half the 45 degree bend's angle

        def build():
            b = fixtures.Board()
            b.track(10, 10, 20, 10, 'SIGA')
            b.track(20, 10, 20, 20, 'SIGA')               # 90 degree corner
            s = 1.77 / math.sqrt(2)                        # 1.77 mm in, on the bisector
            b.via(20 - s, 10 + s, 'OTHER')
            b.track(40, 10, 50, 10, 'SIGB')
            b.track(50, 10, 60, 20, 'SIGB')               # 45 degree bend
            b.via(50 - 1.2 * math.cos(h), 10 + 1.2 * math.sin(h), 'OTHER')  # 1.2 mm in
            return b

        # Unchecked, both full arcs cut into the vias' clearance: the reason to check.
        loose_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, loose_dir, True)
        loose_path = build().save(loose_dir)
        loose = pcbnew.LoadBoard(loose_path)
        before = drc.clearance_violations(drc.run(loose_path))
        round_board(loose, Settings({'Default': ClassSetting(True, 3.0)}), fits=lambda a: True)
        nets = [t.GetNetname() for t in loose.GetTracks()]
        after_path = fixtures.save_copy(loose, os.path.join(loose_dir, 'after.kicad_pcb'))
        self.assertEqual([t.GetNetname() for t in loose.GetTracks()], nets)
        after = drc.clearance_violations(drc.run(after_path))
        self.assertEqual(before, [])
        self.assertEqual([v['type'] for v in after], ['clearance', 'clearance'], after)

        board, stats = self.round_and_check(build(), radius=3.0)
        self.assertEqual((stats['reduced'], stats['full']), (2, 0))
        ra, rb = self.radii(board, 'SIGA'), self.radii(board, 'SIGB')
        self.assertEqual((len(ra), len(rb)), (1, 1))
        # The via needs 0.3 + 0.125 + 0.2 mm from the arc centreline: about
        # 2.77 mm at 90 degrees, and about 6.97 mm (of the full 8.16) at 45.
        self.assertTrue(2.5 < ra[0] < 2.78, ra)
        self.assertTrue(6.0 < rb[0] < 6.98, rb)

    def test_t_junction_blocked_leg_all_sharp(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        b.track(30, 10, 50, 10, 'SIG')
        b.track(30, 10, 30, 30, 'SIG')
        # Keepout tucked into the south-east quadrant, clear of the legs: any arc
        # there of 0.5 mm or more crosses it. The south-west arc alone would fit.
        b.keepout([(30.13, 10.13), (32, 10.13), (32, 12), (30.13, 12)])
        board, stats = self.round_and_check(b, radius=2.0, min_radius=0.5)
        self.assertEqual(arcs(board), [])
        self.assertEqual(geometry(board, 'SIG'),
                         sorted((x1 * MM, y1 * MM, x2 * MM, y2 * MM, 250_000, pcbnew.F_Cu)
                                for x1, y1, x2, y2 in ((10, 10, 30, 10), (30, 10, 50, 10),
                                                       (30, 10, 30, 30))))
        self.assertEqual((stats['sharp_clearance'], stats['full'], stats['reduced']),
                         (1, 0, 0))
        texts = [i.GetText() for i in self.warning_groups(board)[0].GetItems()
                 if i.GetClass() == 'PCB_TEXT']
        self.assertEqual(texts, ['round-tracks: clearance'])

    def test_min_radius_floor_arrow(self):
        # Reverse 90 degree corners 0.6 mm apart: each can have at most 0.3 mm.
        for min_radius, expect_arcs in ((0.5, False), (0.25, True)):
            with self.subTest(min_radius=min_radius):
                self.dir = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, self.dir, True)
                b = fixtures.Board()
                self.polyline(b, [(10, 0), (10, 10), (10.6, 10), (10.6, 20)], 'SIG')
                board, stats = self.round_and_check(b, radius=2.0, min_radius=min_radius)
                groups = self.warning_groups(board)
                if expect_arcs:
                    self.assertEqual(stats['reduced'], 2)
                    for r in self.radii(board):
                        self.assertAlmostEqual(r, 0.3, delta=0.002)
                    self.assertEqual(groups, [])
                    continue
                self.assertEqual(arcs(board), [])
                self.assertEqual(stats['sharp_length'], 2)
                self.assertEqual(len(groups), 1)
                items = list(groups[0].GetItems())
                self.assertEqual([i.GetText() for i in items if i.GetClass() == 'PCB_TEXT'],
                                 ['round-tracks: too short'] * 2)
                for x in (10, 10.6):
                    corner = pcbnew.VECTOR2I(round(x * MM), 10 * MM)
                    tips = [i for i in items if i.GetClass() == 'PCB_SHAPE'
                            and corner in (i.GetStart(), i.GetEnd())]
                    self.assertEqual(len(tips), 3)          # shaft and head meet at the tip


if __name__ == '__main__':
    unittest.main()
