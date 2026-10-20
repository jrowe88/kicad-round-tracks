import math
import unittest
from collections import Counter
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
        self.assertIn(1, p.removals)
        self.assertNotIn(1, p.updates)
        self.assertEqual(set(p.updates), {0, 2})

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

    def test_segment_shortened_from_both_ends_survives(self):
        segs = [seg(0, (0, 10 * MM), (0, 0)),
                seg(1, (0, 0), (5 * MM, 0)),
                seg(2, (5 * MM, 0), (5 * MM, -10 * MM))]
        p = plan(segs)
        self.assertEqual(p.stats['full'], 2)
        self.assertEqual(p.updates[1], ((2 * MM, 0), (3 * MM, 0)))
        self.assertFalse(p.removals)

    def test_mixed_width_bend_reserves_its_leg(self):
        segs = [seg(0, (0, 10 * MM), (0, 0)),
                seg(1, (0, 0), (MM, 0)),
                seg(2, (MM, 0), (MM, 10 * MM), width=300_000)]
        p = plan(segs)
        self.assertEqual(p.stats['skipped'], 1)
        self.assertEqual(len(p.arcs), 1)
        self.assertEqual(p.updates[1], ((MM // 2, 0), (MM, 0)))
        self.assertNotIn(1, p.removals)
        self.assertNotIn(2, p.updates)

    def test_junction_reserves_its_leg(self):
        segs = [seg(0, (0, 10 * MM), (0, 0)),
                seg(1, (0, 0), (MM, 0)),
                seg(2, (MM, 0), (MM, 10 * MM)),
                seg(3, (MM, 0), (5 * MM, 0))]
        p = plan_fillets(segs, set(), {1: 2 * MM}, 100_000, lambda a: True, True)
        self.assertEqual(p.stats['skipped'], 1)
        self.assertEqual(len(p.arcs), 1)
        self.assertEqual(p.updates[1], ((MM // 2, 0), (MM, 0)))
        self.assertEqual(set(p.updates), {0, 1})
        self.assertFalse(p.removals)

    def test_neighbour_sharp_for_clearance_keeps_geometry(self):
        segs = [seg(0, (0, 10 * MM), (0, 0)),
                seg(1, (0, 0), (MM, 0)),
                seg(2, (MM, 0), (MM, -10 * MM))]
        p = plan(segs, fits=lambda a: max(a.start[0], a.mid[0], a.end[0]) < MM - 1)
        self.assertEqual(p.sharps, [Sharp((MM, 0), 0, 'clearance')])
        self.assertEqual(len(p.arcs), 1)
        arc = p.arcs[0]
        self.assertEqual(p.updates, {0: ((0, 10 * MM), (0, MM // 2)),
                                     1: ((MM // 2, 0), (MM, 0))})
        self.assertEqual({arc.start, arc.end}, {(0, MM // 2), (MM // 2, 0)})
        self.assertFalse(p.removals)


def tee(stem_width=200_000):
    """Bar along y=0 through the origin, stem going up."""
    return [seg(0, (-10 * MM, 0), (0, 0)),
            seg(1, (0, 0), (10 * MM, 0)),
            seg(2, (0, 0), (0, 10 * MM), width=stem_width)]


def touches_up(arc):
    return max(arc.start[1], arc.mid[1], arc.end[1]) > 0


class TestJunctions(unittest.TestCase):
    def test_y_junction_three_arcs(self):
        segs = [seg(0, (0, 0), (0, 10 * MM)),
                seg(1, (-10 * MM, -10 * MM), (0, 0)),
                seg(2, (0, 0), (10 * MM, -10 * MM))]
        p = plan(segs)
        self.assertEqual(len(p.arcs), 3)
        self.assertFalse(p.tracks)
        self.assertEqual(p.stats['full'], 1)
        self.assertEqual(sum(p.stats.values()), 1)
        self.assertFalse(p.sharps)
        # every leg is shortened by the tightest pair's request (2 mm at 90 degrees)
        d = round(2 * MM / math.sqrt(2))
        self.assertEqual(p.updates, {0: ((0, 2 * MM), (0, 10 * MM)),
                                     1: ((-10 * MM, -10 * MM), (-d, -d)),
                                     2: ((d, -d), (10 * MM, -10 * MM))})
        ends = Counter(pt for a in p.arcs for pt in (a.start, a.end))
        self.assertEqual(ends, Counter({(0, 2 * MM): 2, (-d, -d): 2, (d, -d): 2}))
        radii = sorted(arc_radius_of(a) for a in p.arcs)
        self.assertAlmostEqual(radii[0], 2 * MM, delta=2_000)
        self.assertAlmostEqual(radii[2], 2 * MM * math.tan(3 * pi / 8), delta=20_000)

    def test_t_junction_two_arcs_and_connector(self):
        p = plan(tee())
        self.assertEqual(len(p.arcs), 2)
        self.assertEqual(p.tracks, [Seg(-1, (-2 * MM, 0), (2 * MM, 0), 200_000, 0, 1)])
        self.assertEqual(p.updates, {0: ((-10 * MM, 0), (-2 * MM, 0)),
                                     1: ((2 * MM, 0), (10 * MM, 0)),
                                     2: ((0, 2 * MM), (0, 10 * MM))})
        self.assertEqual({(a.start, a.end) for a in p.arcs},
                         {((2 * MM, 0), (0, 2 * MM)), ((0, 2 * MM), (-2 * MM, 0))})
        self.assertEqual(p.stats['full'], 1)
        self.assertFalse(p.sharps)

    def test_t_junction_all_or_nothing(self):
        p = plan(tee(), fits=lambda a: not touches_up(a))
        self.assertEqual(p.sharps, [Sharp((0, 0), 0, 'clearance')])
        self.assertFalse(p.arcs)
        self.assertFalse(p.tracks)
        self.assertFalse(p.updates)
        self.assertEqual(p.stats['sharp_clearance'], 1)

    def test_one_failing_arc_keeps_whole_junction_sharp(self):
        p = plan(tee(), fits=lambda a: a.mid[0] > 0)
        self.assertEqual(p.sharps, [Sharp((0, 0), 0, 'clearance')])
        self.assertFalse(p.arcs)
        self.assertFalse(p.tracks)
        self.assertFalse(p.updates)

    def test_fits_asked_in_sorted_leg_order(self):
        asked = []
        plan(tee(), fits=lambda a: asked.append(a) or True)
        self.assertEqual([(a.start, a.end) for a in asked],
                         [((2 * MM, 0), (0, 2 * MM)), ((0, 2 * MM), (-2 * MM, 0))])

    def test_clearance_shrinks_shared_setback(self):
        p = plan(tee(), fits=lambda a: arc_radius_of(a) <= MM)
        self.assertEqual(p.stats['reduced'], 1)
        self.assertEqual(len(p.arcs), 2)
        for a in p.arcs:
            self.assertTrue(950_000 <= arc_radius_of(a) <= MM)
        t = p.updates[1][0][0]
        self.assertEqual(p.updates[0][1], (-t, 0))
        self.assertEqual(p.updates[2][0], (0, t))
        self.assertEqual(p.tracks, [Seg(-1, (-t, 0), (t, 0), 200_000, 0, 1)])

    def test_reflex_gap_gets_no_arc_or_connector(self):
        segs = [seg(0, (0, 0), (10 * MM, 0)),
                seg(1, (0, 0), (10 * MM, 10 * MM)),
                seg(2, (0, 0), (0, 10 * MM))]
        p = plan(segs)
        self.assertEqual(len(p.arcs), 2)
        self.assertFalse(p.tracks)
        self.assertEqual(p.stats['full'], 1)
        t = setback_request(2 * MM, pi / 4)
        self.assertEqual(p.updates[0], ((round(t), 0), (10 * MM, 0)))
        self.assertEqual(p.updates[2], ((0, round(t)), (0, 10 * MM)))
        middle = p.updates[1][0]
        self.assertEqual(sum(pt == middle for a in p.arcs for pt in (a.start, a.end)), 2)

    def test_mixed_width_junction_skipped(self):
        p = plan(tee(stem_width=300_000))
        self.assertEqual(p.stats['skipped'], 1)
        self.assertEqual(sum(p.stats.values()), 1)
        self.assertFalse(p.arcs)
        self.assertFalse(p.tracks)
        self.assertFalse(p.updates)
        self.assertFalse(p.sharps)

    def test_min_radius_all_or_nothing(self):
        a20 = math.radians(20)
        segs = tee()[:2] + [seg(2, (0, 0), (round(10 * MM * math.cos(a20)),
                                            round(10 * MM * math.sin(a20))))]
        # the 20 degree pair gets radius ~0.24 mm at the shared setback; the 160 degree
        # pair alone would be ~7.8 mm, but the junction stays sharp as a whole
        p = plan(segs, min_radius=300_000)
        self.assertEqual(p.sharps, [Sharp((0, 0), 0, 'length')])
        self.assertFalse(p.arcs)
        self.assertFalse(p.tracks)
        self.assertFalse(p.updates)
        self.assertEqual(p.stats['sharp_length'], 1)
        self.assertEqual(sum(p.stats.values()), 1)

    def test_avoid_junctions_skips(self):
        p = plan_fillets(tee(), set(), {1: 2 * MM}, 100_000, lambda a: True, True)
        self.assertEqual(p.stats['skipped'], 1)
        self.assertEqual(sum(p.stats.values()), 1)
        self.assertFalse(p.arcs)
        self.assertFalse(p.tracks)
        self.assertFalse(p.updates)
        self.assertFalse(p.sharps)

    def test_on_accept_gets_exactly_the_kept_arcs(self):
        accepted = []
        p = plan_fillets(tee(), set(), {1: 2 * MM}, 100_000,
                         lambda a: arc_radius_of(a) <= MM, False, on_accept=accepted.append)
        self.assertEqual(p.stats['reduced'], 1)
        self.assertEqual(len(p.arcs), 2)
        self.assertEqual(accepted, p.arcs)

    def test_on_accept_not_called_for_sharp_junction(self):
        corner = [seg(10, (30 * MM, 10 * MM), (30 * MM, 0)),
                  seg(11, (30 * MM, 0), (40 * MM, 0))]
        accepted = []
        p = plan_fillets(tee() + corner, set(), {1: 2 * MM}, 100_000,
                         lambda a: a.start[0] > 20 * MM, False, on_accept=accepted.append)
        self.assertEqual(p.sharps, [Sharp((0, 0), 0, 'clearance')])
        self.assertEqual(len(p.arcs), 1)
        self.assertEqual(accepted, p.arcs)

    def test_junction_shares_leg_with_corner(self):
        segs = [seg(0, (-10 * MM, 0), (0, 0)),
                seg(1, (0, 0), (2 * MM, 0)),
                seg(2, (0, 0), (0, 10 * MM)),
                seg(3, (2 * MM, 0), (2 * MM, -10 * MM))]
        p = plan(segs)
        self.assertEqual(p.stats['reduced'], 2)
        self.assertEqual(len(p.arcs), 3)
        self.assertEqual(p.tracks, [Seg(-1, (-MM, 0), (MM, 0), 200_000, 0, 1)])
        self.assertEqual(p.removals, {1})
        self.assertEqual(p.updates, {0: ((-10 * MM, 0), (-MM, 0)),
                                     2: ((0, MM), (0, 10 * MM)),
                                     3: ((2 * MM, -MM), (2 * MM, -10 * MM))})
        corner_arc = [a for a in p.arcs if a.start[1] < 0 or a.end[1] < 0]
        self.assertEqual(len(corner_arc), 1)
        self.assertEqual({corner_arc[0].start, corner_arc[0].end}, {(MM, 0), (2 * MM, -MM)})

    def test_four_way_cross(self):
        segs = [seg(0, (0, 0), (10 * MM, 0)), seg(1, (0, 0), (0, 10 * MM)),
                seg(2, (0, 0), (-10 * MM, 0)), seg(3, (0, 0), (0, -10 * MM))]
        p = plan(segs)
        self.assertEqual(len(p.arcs), 4)
        self.assertFalse(p.tracks)
        self.assertEqual(p.stats['full'], 1)
        self.assertEqual(p.updates, {0: ((2 * MM, 0), (10 * MM, 0)),
                                     1: ((0, 2 * MM), (0, 10 * MM)),
                                     2: ((-2 * MM, 0), (-10 * MM, 0)),
                                     3: ((0, -2 * MM), (0, -10 * MM))})
        for a in p.arcs:
            self.assertAlmostEqual(arc_radius_of(a), 2 * MM, delta=2_000)

    def test_junction_leg_with_free_end_consumed(self):
        segs = tee()[:2] + [seg(2, (0, 0), (0, 1_500_000))]
        p = plan(segs)
        self.assertEqual(p.stats['reduced'], 1)
        self.assertEqual(p.removals, {2})
        self.assertEqual(p.updates, {0: ((-10 * MM, 0), (-1_500_000, 0)),
                                     1: ((1_500_000, 0), (10 * MM, 0))})
        self.assertEqual(Counter(pt for a in p.arcs for pt in (a.start, a.end))[(0, 1_500_000)], 2)
        self.assertEqual(len(p.tracks), 1)

    def test_corner_plan_has_no_tracks(self):
        self.assertEqual(plan(ell()).tracks, [])


if __name__ == '__main__':
    unittest.main()
