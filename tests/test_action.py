# Dialog handlers run against stand-ins: no window is created or shown.
# Licensed under the Apache License 2.0, like the rest of this plugin.

import math
import shutil
import tempfile
import unittest
from collections import Counter
from unittest import mock

import pcbnew
import wx

import round_tracks_action
from round_tracks_action import RoundTracks
from round_tracks_board import ClassSetting, Settings
from tests import fixtures

MM = 1_000_000


class FakeDialog:
    """Just enough of a RoundTracks for its handlers; the board is real or a Mock."""

    def __init__(self, board):
        self.board = board
        self.basefilename = 'unused'
        self.apply = mock.Mock()
        self.apply.GetLabel.return_value = 'Run'
        self.do_create = mock.Mock()
        self.do_create.IsChecked.return_value = False
        self.settings = Settings({'Default': ClassSetting(True, 2.0)})
        self.validate_all_data = mock.Mock()
        self.save_config = mock.Mock()
        self.EndModal = mock.Mock()
        self.prog = None

    def __getattr__(self, name):
        # Helper methods (pulse, close_progress, ...) come from RoundTracks itself.
        return getattr(RoundTracks, name).__get__(self)


class TestRunHandler(unittest.TestCase):
    def setUp(self):
        self.prog = mock.Mock()
        for name, value in (('ProgressDialog', mock.Mock(return_value=self.prog)),
                            ('MessageBox', mock.Mock()), ('Yield', mock.Mock())):
            patcher = mock.patch.object(wx, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def board(self):
        board = mock.Mock()
        board.GetTracks.return_value = []
        return board

    def test_failure_closes_progress_and_reports(self):
        dialog = FakeDialog(self.board())
        with mock.patch.object(round_tracks_action, 'round_board',
                               side_effect=RuntimeError('boom')):
            RoundTracks.run(dialog, None)              # must not raise
        self.prog.Destroy.assert_called_once()
        self.assertEqual(dialog.apply.SetLabel.call_args_list[-1], mock.call('Run'))
        text = wx.MessageBox.call_args[0][0]
        self.assertIn('Traceback', text)
        self.assertIn('boom', text)
        dialog.EndModal.assert_not_called()

    def test_success_reports_and_pulses_per_net(self):
        dialog = FakeDialog(self.board())

        def fake_round(board, settings, progress=None):
            progress('Rounding net A (1 of 2)')
            progress('Rounding net B (2 of 2)')
            return Counter(full=3)
        with mock.patch.object(round_tracks_action, 'round_board', side_effect=fake_round):
            RoundTracks.run(dialog, None)
        self.assertEqual([c.args[0] for c in self.prog.Pulse.call_args_list][-2:],
                         ['Rounding net A (1 of 2)', 'Rounding net B (2 of 2)'])
        self.prog.Destroy.assert_called_once()
        self.assertEqual(dialog.apply.SetLabel.call_args_list[-1], mock.call('Run'))
        self.assertIn('Rounded 3 corners', wx.MessageBox.call_args[0][0])
        dialog.EndModal.assert_called_once()

    def test_unround_failure_reports(self):
        board = self.board()
        board.GetNetsByNetcode.side_effect = RuntimeError('kaput')
        dialog = FakeDialog(board)
        RoundTracks.unrun(dialog, None)                # must not raise
        text = wx.MessageBox.call_args[0][0]
        self.assertIn('Traceback', text)
        self.assertIn('kaput', text)
        dialog.EndModal.assert_not_called()

    def test_unround_counts_hairpin_arcs(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        b = fixtures.Board()
        b.track(0, 0, 10, 0, 'SIG')
        arc = pcbnew.PCB_ARC(b.board)              # turns 175 degrees: a hairpin
        c, r = (20, 5), 5
        for setter, deg in ((arc.SetStart, 182.5), (arc.SetMid, 270), (arc.SetEnd, 357.5)):
            setter(pcbnew.VECTOR2I(round((c[0] + r * math.cos(math.radians(deg))) * MM),
                                   round((c[1] + r * math.sin(math.radians(deg))) * MM)))
        arc.SetWidth(250_000)
        b.board.Add(arc)
        arc.SetNet(b.net('SIG'))
        board = pcbnew.LoadBoard(b.save(directory))
        dialog = FakeDialog(board)
        RoundTracks.unrun(dialog, None)
        texts = [c.args[0] for c in wx.MessageBox.call_args_list]
        self.assertTrue(any('1 hairpin arc' in t for t in texts), texts)
        self.assertFalse(any('nearly-straight' in t for t in texts), texts)
        self.assertEqual(sum(1 for t in board.GetTracks() if t.GetClass() == 'PCB_ARC'), 1)


if __name__ == '__main__':
    unittest.main()
