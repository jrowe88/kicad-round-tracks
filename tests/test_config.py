import unittest

from round_tracks_board import ClassSetting, Settings
from round_tracks_config import parse_config, format_config, read_new_file, parse_length


def upstream_load_config(text):
    """Loop body of upstream mitxela's RoundTracks.load_config, on a string."""
    config = {}
    new_config = {}
    for line in text.splitlines(True):
        params = line[:-1].split("\t")
        new_config_line = {}
        try:
            new_config_line['do_round'] = params[1] == "True"
            new_config_line['scaling'] = float(params[2])
            new_config_line['passes'] = int(params[3])
            new_config[params[0]] = new_config_line
        except Exception:
            try:
                new_config_line['new_file'] = params[0] == "True"
                new_config_line['native'] = params[1] == "True"
                new_config_line['avoid_junctions'] = params[2] == "True"
                config['checkboxes'] = new_config_line
            except Exception:
                pass
    config['classes'] = new_config
    return config


LEGACY = "Default\tTrue\t2.0\t3\nPower\tFalse\t1.5\t2\nTrue\tTrue\tTrue\n"


class ConfigTests(unittest.TestCase):
    def test_parse_legacy_file(self):
        s = parse_config(LEGACY)
        self.assertEqual(s.classes['Default'], ClassSetting(True, 2.0))
        self.assertEqual(s.classes['Power'], ClassSetting(False, 1.5))
        self.assertTrue(s.avoid_junctions)
        self.assertEqual(s.min_radius_mm, 0.1)
        self.assertTrue(read_new_file(LEGACY))

    def test_round_trip(self):
        s = Settings(classes={'Default': ClassSetting(True, 2.5),
                              'Fast': ClassSetting(False, 0.75)},
                     min_radius_mm=0.2, avoid_junctions=True)
        text = format_config(s, True)
        s2 = parse_config(text)
        self.assertEqual(s2.classes, s.classes)
        self.assertEqual(s2.min_radius_mm, 0.2)
        self.assertTrue(s2.avoid_junctions)
        self.assertTrue(read_new_file(text))
        self.assertFalse(read_new_file(format_config(s, False)))

    def test_upstream_parser_tolerates_new_file(self):
        s = Settings(classes={'Default': ClassSetting(True, 2.0)},
                     min_radius_mm=0.3)
        cfg = upstream_load_config(format_config(s, True))
        self.assertEqual(cfg['classes']['Default']['scaling'], 2.0)
        self.assertEqual(cfg['classes']['Default']['passes'], 3)
        self.assertTrue(cfg['checkboxes']['new_file'])

    def test_malformed_lines_skipped(self):
        s = parse_config("garbage\n\n\tx\nA\tTrue\tabc\t3\nmin_radius\tzz\n")
        self.assertEqual(s.classes, {})
        self.assertEqual(s.min_radius_mm, 0.1)
        self.assertFalse(read_new_file(""))

    def test_non_finite_and_negative_values_rejected(self):
        for bad in ('nan', 'inf', '-inf', '-1', '1e999'):
            with self.subTest(bad=bad):
                s = parse_config("Default\tTrue\t%s\t3\nPower\tTrue\t1.5\t3\n"
                                 "min_radius\t%s\n" % (bad, bad))
                self.assertNotIn('Default', s.classes)    # the dialog's default applies
                self.assertEqual(s.classes['Power'], ClassSetting(True, 1.5))
                self.assertEqual(s.min_radius_mm, 0.1)

    def test_parse_length(self):
        self.assertEqual(parse_length('2.5', 1.0), 2.5)
        self.assertEqual(parse_length(' 0 ', 1.0), 0.0)
        for bad in ('nan', 'NaN', 'inf', '-inf', '-1', '-0.001', 'abc', '', None):
            with self.subTest(bad=bad):
                self.assertEqual(parse_length(bad, 1.0), 1.0)


if __name__ == '__main__':
    unittest.main()
