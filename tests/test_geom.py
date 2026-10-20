import math
import unittest
from math import pi

from round_tracks_geom import (setback_request, arc_radius, setback_for_radius,
                               fillet_points, interior_angle, Seg, cleanup)


class TestPrimitives(unittest.TestCase):
    def test_90_degree_radius_equals_setting(self):
        t = setback_request(2_000_000, pi / 2)
        self.assertAlmostEqual(t, 2_000_000, delta=1)
        self.assertAlmostEqual(arc_radius(t, pi / 2), 2_000_000, delta=1)

    def test_45_degree_bend_matches_legacy_scaling(self):
        interior = 3 * pi / 4
        ratio = arc_radius(setback_request(2_000_000, interior), interior) / 2_000_000
        self.assertAlmostEqual(ratio, 2.72, delta=0.01)

    def test_setback_for_radius_inverts_arc_radius(self):
        for interior in (0.5, pi / 2, 2.5):
            t = setback_for_radius(1_000_000, interior)
            self.assertAlmostEqual(arc_radius(t, interior), 1_000_000, delta=1e-3)

    def test_fillet_points_tangent(self):
        start, mid, end = fillet_points((0, 0), (10_000_000, 0), (0, 10_000_000), 2_000_000)
        self.assertEqual(start, (2_000_000, 0))
        self.assertEqual(end, (0, 2_000_000))
        centre = (2_000_000, 2_000_000)
        r_start = math.dist(start, centre)
        self.assertAlmostEqual(math.dist(mid, centre), r_start, delta=2)

    def test_hairpin_and_straight(self):
        self.assertAlmostEqual(interior_angle((0, 0), (10, 0), (-10, 0)), pi, delta=1e-9)
        self.assertLess(arc_radius(setback_request(2_000_000, 0.01), 0.01), 100_000)
        setback_request(2_000_000, 0.0)
        arc_radius(setback_request(2_000_000, 0.0), 0.0)


MM = 1_000_000


def seg(i, a, b, width=200_000, layer=0, net=1):
    return Seg(i, a, b, width, layer, net)


class TestCleanup(unittest.TestCase):
    def test_merges_collinear_fragment(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (5 * MM + 300, 0)),
                seg(2, (5 * MM + 300, 0), (10 * MM, 0))]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual([(s.id, s.a, s.b) for s in new], [(0, (0, 0), (10 * MM, 0))])
        self.assertEqual(removals, {1, 2})
        self.assertEqual(updates, {0: ((0, 0), (10 * MM, 0))})

    def test_anchor_blocks_merge(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (5 * MM + 300, 0)),
                seg(2, (5 * MM + 300, 0), (10 * MM, 0))]
        new, updates, removals = cleanup(segs, {(5 * MM, 0)})
        points = {s.a for s in new} | {s.b for s in new}
        self.assertIn((5 * MM, 0), points)
        self.assertEqual(len(new), 2)
        self.assertTrue(removals.isdisjoint(updates))

    def test_different_width_not_merged(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (10 * MM, 0), width=300_000)]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(len(new), 2)
        self.assertFalse(updates)
        self.assertFalse(removals)

    def test_removes_sliver_before_corner(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (5 * MM + 500, 0)),
                seg(2, (5 * MM + 500, 0), (5 * MM + 500, 5 * MM))]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(len(new), 2)
        self.assertEqual(new[0].b, new[1].a)
        self.assertEqual(len(removals), 1)
        self.assertTrue(removals.isdisjoint(updates))
        self.assertEqual(removals, {1})
        self.assertEqual(updates, {0: ((0, 0), (5 * MM + 500, 0))})

    def test_removes_non_collinear_sliver(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (5 * MM, 500)),
                seg(2, (5 * MM, 500), (10 * MM, 5 * MM + 500))]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(removals, {1})
        self.assertEqual(updates, {0: ((0, 0), (5 * MM, 500))})
        self.assertEqual([s.id for s in new], [0, 2])

    def test_anchored_sliver_end_stays_put(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (5 * MM, 500)),
                seg(2, (5 * MM, 500), (10 * MM, 5 * MM + 500))]
        new, updates, removals = cleanup(segs, {(5 * MM, 0)})
        self.assertEqual(removals, {1})
        self.assertEqual(updates, {2: ((5 * MM, 0), (10 * MM, 5 * MM + 500))})

    def test_sliver_with_both_ends_blocked_is_kept(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (5 * MM, 500)),
                seg(2, (5 * MM, 500), (10 * MM, 5 * MM + 500))]
        new, updates, removals = cleanup(segs, {(5 * MM, 0), (5 * MM, 500)})
        self.assertEqual(len(new), 3)
        self.assertFalse(updates)
        self.assertFalse(removals)

    def test_merge_keeps_survivor_orientation(self):
        segs = [seg(0, (5 * MM, 0), (0, 0)),
                seg(1, (5 * MM, 0), (10 * MM, 0))]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(updates, {0: ((10 * MM, 0), (0, 0))})
        self.assertEqual(removals, {1})

    def test_other_net_at_point_does_not_block_or_join(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (10 * MM, 0)),
                seg(2, (5 * MM, 0), (5 * MM, 5 * MM), net=2)]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(removals, {1})
        self.assertEqual(updates, {0: ((0, 0), (10 * MM, 0))})
        self.assertEqual([s.id for s in new], [0, 2])

    def test_different_nets_not_merged(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (10 * MM, 0), net=2)]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(len(new), 2)
        self.assertFalse(updates or removals)

    def test_merged_survivor_that_becomes_sliver_is_removed(self):
        segs = [seg(0, (0, 0), (300, 0)),
                seg(1, (300, 0), (600, 0)),
                seg(2, (600, 0), (600, 5 * MM))]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(removals, {0, 1})
        self.assertEqual(updates, {2: ((0, 0), (600, 5 * MM))})
        self.assertEqual([s.id for s in new], [2])

    def test_junction_point_not_merged(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (10 * MM, 0)),
                seg(2, (5 * MM, 0), (5 * MM, 5 * MM))]
        new, updates, removals = cleanup(segs, set())
        self.assertEqual(len(new), 3)
        self.assertFalse(updates)
        self.assertFalse(removals)


if __name__ == '__main__':
    unittest.main()
