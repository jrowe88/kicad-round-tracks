import math
import os
import shutil
import tempfile
import unittest

import pcbnew

from round_tracks_board import ClassSetting, Settings, round_board
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

    def test_nothing_in_scope(self):
        b = fixtures.Board()
        b.track(10, 10, 30, 10, 'SIG')
        _, board = self.load(b)
        stats = round_board(board, Settings({'Default': ClassSetting(False, 2.0)}))
        self.assertEqual(sum(stats.values()), 0)


if __name__ == '__main__':
    unittest.main()
