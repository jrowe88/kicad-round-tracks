import math
import unittest
from math import pi

from round_tracks_geom import (setback_request, arc_radius, setback_for_radius,
                               fillet_points, interior_angle, Seg, cleanup,
                               Sharp, plan_fillets)


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


def arc_radius_of(arc):
    """Circumradius of the arc's three points."""
    a = math.dist(arc.start, arc.mid)
    b = math.dist(arc.mid, arc.end)
    c = math.dist(arc.start, arc.end)
    cross = ((arc.mid[0] - arc.start[0]) * (arc.end[1] - arc.start[1])
             - (arc.mid[1] - arc.start[1]) * (arc.end[0] - arc.start[0]))
    return a * b * c / (2 * abs(cross))


def plan(segs, anchors=(), scaling=2 * MM, min_radius=100_000, fits=lambda a: True):
    return plan_fillets(segs, set(anchors), {1: scaling}, min_radius, fits, False)


def ell(leg=10 * MM, width2=200_000):
    return [seg(0, (0, leg), (0, 0)), seg(1, (0, 0), (leg, 0), width=width2)]


class TestPlanner(unittest.TestCase):
    def test_single_corner_full(self):
        p = plan(ell())
        self.assertEqual(len(p.arcs), 1)
        self.assertEqual(p.stats['full'], 1)
        self.assertEqual(p.updates, {0: ((0, 10 * MM), (0, 2 * MM)),
                                     1: ((2 * MM, 0), (10 * MM, 0))})
        self.assertFalse(p.removals)
        self.assertFalse(p.sharps)
        arc = p.arcs[0]
        self.assertEqual({arc.start, arc.end}, {(0, 2 * MM), (2 * MM, 0)})
        self.assertEqual((arc.width, arc.layer, arc.net), (200_000, 0, 1))

    def test_stats_keys(self):
        p = plan(ell())
        self.assertEqual(set(p.stats), {'full', 'reduced', 'merged', 'sharp_length',
                                        'sharp_clearance', 'skipped'})

    def test_short_shared_segment_split_proportionally(self):
        segs = [seg(0, (-10 * MM, -10 * MM), (0, 0)),
                seg(1, (0, 0), (MM, 0)),
                seg(2, (MM, 0), (MM, 10 * MM))]
        p = plan(segs)
        self.assertEqual(len(p.arcs), 2)
        on_shared = sorted(pt for a in p.arcs for pt in (a.start, a.end) if pt[1] == 0)
        self.assertEqual(len(on_shared), 2)
        (x0, _), (x1, _) = on_shared
        self.assertTrue(0 < x0 <= x1 + 1 and x1 < MM)
        ratio = x0 / (MM - x1)
        expected = setback_request(2 * MM, 3 * pi / 4) / setback_request(2 * MM, pi / 2)
        self.assertAlmostEqual(ratio, expected, delta=expected * 0.01)
        self.assertEqual(p.stats['reduced'], 2)

    def test_min_radius_leaves_sharp(self):
        p = plan(ell(leg=80_000), min_radius=100_000)
        self.assertEqual(p.sharps, [Sharp((0, 0), 0, 'length')])
        self.assertFalse(p.arcs)
        self.assertFalse(p.updates)
        self.assertEqual(p.stats['sharp_length'], 1)

    def test_clearance_shrinks_radius(self):
        p = plan(ell(), fits=lambda a: arc_radius_of(a) <= 1_000_000)
        self.assertEqual(len(p.arcs), 1)
        r = arc_radius_of(p.arcs[0])
        self.assertTrue(950_000 <= r <= 1_000_000, r)
        self.assertEqual(p.stats['reduced'], 1)

    def test_clearance_never_fits(self):
        p = plan(ell(), fits=lambda a: False)
        self.assertEqual(p.sharps, [Sharp((0, 0), 0, 'clearance')])
        self.assertFalse(p.arcs)
        self.assertFalse(p.updates)
        self.assertEqual(p.stats['sharp_clearance'], 1)

    def test_fits_only_asked_about_arcs_above_min_radius(self):
        radii = []
        plan(ell(), min_radius=500_000, fits=lambda a: radii.append(arc_radius_of(a)) or False)
        self.assertTrue(radii)
        self.assertTrue(all(r >= 500_000 - 2 for r in radii), radii)

    def test_mixed_width_corner_skipped(self):
        p = plan(ell(width2=300_000))
        self.assertEqual(p.stats['skipped'], 1)
        self.assertFalse(p.sharps)
        self.assertFalse(p.arcs)
        self.assertFalse(p.updates)

    def test_anchor_corner_untouched(self):
        p = plan(ell(), anchors={(0, 0)})
        self.assertFalse(p.arcs)
        self.assertFalse(p.updates)
        self.assertFalse(p.sharps)
        self.assertEqual(sum(p.stats.values()), 0)

    def test_cleanup_results_folded_in(self):
        segs = [seg(0, (0, 0), (5 * MM, 0)),
                seg(1, (5 * MM, 0), (5 * MM + 500, 0)),
                seg(2, (5 * MM + 500, 0), (5 * MM + 500, 5 * MM))]
        p = plan(segs)
        self.assertEqual(len(p.arcs), 1)
        self.assertEqual(p.removals, {1})
        self.assertTrue(p.removals.isdisjoint(p.updates))
        self.assertEqual(set(p.updates), {0, 2})


if __name__ == '__main__':
    unittest.main()
