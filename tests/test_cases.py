import math
import os
import shutil
import tempfile
import time
import unittest

import pcbnew

from round_tracks_board import ClassSetting, ClearanceChecker, Settings, round_board
from round_tracks_geom import Arc
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

    def round_and_check(self, b, radius=3.0):
        """Load, DRC, round, DRC again: no new clearance-family violation."""
        path, board = self.load(b)
        before = drc.clearance_violations(drc.run(path))
        stats = round_board(board, Settings({'Default': ClassSetting(True, radius)}))
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


if __name__ == '__main__':
    unittest.main()
