# Pure geometry for adaptive fillets (no pcbnew). Units are integer nanometres.
# Licensed under the Apache License 2.0, like the rest of this plugin.

import math
from collections import Counter
from dataclasses import dataclass, field

Point = tuple  # (int, int)
ANGLE_TOL = 0.001  # radians
SLIVER_NM = 1000  # segments shorter than this are cleanup candidates


def interior_angle(corner, d1, d2):
    """Angle between the two legs at `corner`, in [0, pi]."""
    a1 = math.atan2(d1[1] - corner[1], d1[0] - corner[0])
    a2 = math.atan2(d2[1] - corner[1], d2[0] - corner[0])
    diff = abs(a1 - a2) % (2 * math.pi)
    return 2 * math.pi - diff if diff > math.pi else diff


def setback_request(scaling_nm, interior):
    """Setback each leg is asked for; a 90 degree bend yields a true radius of scaling_nm."""
    radius = scaling_nm / (math.sin(math.pi / 4) + 1)
    return radius * (math.sin(interior / 2) + 1)


def arc_radius(setback, interior):
    """True arc radius produced by a given setback."""
    return setback * math.tan(interior / 2)


def setback_for_radius(radius, interior):
    """Inverse of arc_radius; infinite for a degenerate (zero) interior angle."""
    t = math.tan(interior / 2)
    return radius / t if t > 0 else math.inf


def point_along(corner, far, distance):
    """Point `distance` from `corner` towards `far`, rounded to integer nm."""
    dx, dy = far[0] - corner[0], far[1] - corner[1]
    length = math.hypot(dx, dy)
    return (round(corner[0] + dx / length * distance),
            round(corner[1] + dy / length * distance))


def fillet_points(corner, d1, d2, setback):
    """Return (start, mid, end) of the fillet arc. d1/d2 are the far ends of the legs."""
    h = interior_angle(corner, d1, d2) / 2
    start, end = (point_along(corner, d, setback) for d in (d1, d2))
    f = 1 / (2 * math.cos(math.pi / 2 - h) + 2)
    mid = (round(corner[0] * (1 - 2 * f) + start[0] * f + end[0] * f),
           round(corner[1] * (1 - 2 * f) + start[1] * f + end[1] * f))
    return start, mid, end


@dataclass
class Seg:
    id: int
    a: Point
    b: Point
    width: int
    layer: int
    net: int


def _index(segs):
    """Map (layer, net, point) to the set of ids of segments ending there."""
    index = {}
    for s in segs.values():
        for p in (s.a, s.b):
            index.setdefault((s.layer, s.net, p), set()).add(s.id)
    return index


def _far_end(s, p):
    return s.b if s.a == p else s.a


def _replace_end(s, old, new):
    if s.a == old:
        s.a = new
    if s.b == old:
        s.b = new


def _try_merge(segs, index, anchors):
    """Merge one collinear pair meeting at a plain two-segment point; True if done."""
    for (layer, net, p), ids in index.items():
        if len(ids) != 2 or p in anchors:
            continue
        s1, s2 = (segs[i] for i in sorted(ids))
        f1, f2 = _far_end(s1, p), _far_end(s2, p)
        if s1.width != s2.width or f1 == p or f2 == p:
            continue
        if interior_angle(p, f1, f2) < math.pi - ANGLE_TOL:
            continue
        _replace_end(s1, p, f2)
        del segs[s2.id]
        return True
    return False


def _try_remove_sliver(segs, index, anchors):
    """Remove one short segment by pulling its neighbour across; True if done."""
    for s in sorted(segs.values(), key=lambda s: s.id):
        if math.dist(s.a, s.b) >= SLIVER_NM:
            continue
        options = []
        for drop, keep in ((s.a, s.b), (s.b, s.a)):
            others = index[(s.layer, s.net, drop)] - {s.id}
            if drop in anchors or len(others) != 1:
                continue
            neighbour = segs[next(iter(others))]
            if _far_end(neighbour, drop) == keep:
                continue
            fixed = keep in anchors or len(index[(s.layer, s.net, keep)]) > 2
            options.append((not fixed, drop, keep, neighbour))
        if options:
            _, drop, keep, neighbour = min(options, key=lambda o: o[0])
            _replace_end(neighbour, drop, keep)
            del segs[s.id]
            return True
    return False


def cleanup(segs, anchors):
    """Merge collinear fragments and drop slivers.

    Returns (new_segs, updates, removals): updates maps the id of a surviving
    original segment to its new (a, b); removals holds the ids that went away.
    """
    original = {s.id: (s.a, s.b) for s in segs}
    work = {s.id: Seg(s.id, s.a, s.b, s.width, s.layer, s.net) for s in segs}
    while (_try_merge(work, _index(work), anchors)
           or _try_remove_sliver(work, _index(work), anchors)):
        pass
    new_segs = [work[i] for i in sorted(work)]
    updates = {s.id: (s.a, s.b) for s in new_segs if (s.a, s.b) != original[s.id]}
    return new_segs, updates, set(original) - set(work)


@dataclass
class Arc:
    start: Point
    mid: Point
    end: Point
    width: int
    layer: int
    net: int


@dataclass
class Sharp:
    point: Point
    layer: int
    reason: str  # "length" or "clearance"


STAT_KEYS = ('full', 'reduced', 'merged', 'sharp_length', 'sharp_clearance', 'skipped')
SEARCH_STEPS = 8


@dataclass
class Plan:
    updates: dict
    removals: set
    arcs: list = field(default_factory=list)
    sharps: list = field(default_factory=list)
    tracks: list = field(default_factory=list)  # new straight connectors, Seg(id=-1)
    stats: Counter = field(default_factory=lambda: Counter(dict.fromkeys(STAT_KEYS, 0)))


@dataclass
class _Bend:
    """A point to fillet: a two-segment corner or a junction of 3+ same-width segments.

    Every leg is shortened by the same setback. `pairs` lists adjacent legs as
    (i, j, gap): an arc when the gap is below pi, a straight connector when the
    legs are opposite, nothing when the gap is reflex.
    """
    point: Point
    layer: int
    net: int
    legs: tuple  # corner: ordered by id; junction: ordered by angle
    pairs: tuple
    request: float = 0.0
    setback: float = 0.0

    def _far(self, i):
        return _far_end(self.legs[i], self.point)

    def _arc_pairs(self):
        return [(i, j, gap) for i, j, gap in self.pairs if gap < math.pi - ANGLE_TOL]

    def _straight_pairs(self):
        return [(i, j, gap) for i, j, gap in self.pairs if abs(gap - math.pi) <= ANGLE_TOL]

    @property
    def interior(self):
        """Tightest arc pair: it has the smallest radius at the shared setback."""
        return min(gap for _, _, gap in self._arc_pairs())

    def arcs(self, setback):
        return [Arc(*fillet_points(self.point, self._far(i), self._far(j), setback),
                    self.legs[0].width, self.layer, self.net)
                for i, j, _ in self._arc_pairs()]

    def tracks(self, setback):
        return [Seg(-1, point_along(self.point, self._far(i), setback),
                    point_along(self.point, self._far(j), setback),
                    self.legs[0].width, self.layer, self.net)
                for i, j, _ in self._straight_pairs()]


def _direction(p, q):
    return math.atan2(q[1] - p[1], q[0] - p[0])


def _find_bends(segs, anchors, scaling_by_net, avoid_junctions, stats):
    """Corners and junctions in deterministic order; skipped ones are only counted."""
    bends = []
    by_id = {s.id: s for s in segs}
    for (layer, net, p), ids in _index(by_id).items():
        if len(ids) < 2 or p in anchors:
            continue
        legs = [by_id[i] for i in sorted(ids)]
        if any(_far_end(s, p) == p for s in legs):
            continue
        if len(legs) == 2:
            interior = interior_angle(p, *(_far_end(s, p) for s in legs))
            if interior >= math.pi - ANGLE_TOL:
                continue
            pairs = ((0, 1, interior),)
        else:
            if avoid_junctions:
                stats['skipped'] += 1
                continue
            legs.sort(key=lambda s: _direction(p, _far_end(s, p)))
            angles = [_direction(p, _far_end(s, p)) for s in legs]
            n = len(legs)
            pairs = tuple((i, (i + 1) % n, (angles[(i + 1) % n] - angles[i]) % (2 * math.pi))
                          for i in range(n))
        if len({s.width for s in legs}) > 1:
            stats['skipped'] += 1
            continue
        bend = _Bend(p, layer, net, tuple(legs), pairs)
        bend.request = setback_request(scaling_by_net[net], bend.interior)
        bends.append(bend)
    bends.sort(key=lambda b: (b.net, b.layer, b.point))
    return bends


def _end_requests(segs, anchors, scaling_by_net):
    """Setback each segment end reserves, keyed by (seg id, point).

    Every bend reserves, filleted or not: a two-segment bend asks for its own
    request, a junction leg for the request of its tightest angle to another leg.
    Anchors, free ends and straight joints reserve nothing.
    """
    by_id = {s.id: s for s in segs}
    requests = {}
    for (layer, net, p), ids in _index(by_id).items():
        if len(ids) < 2 or p in anchors:
            continue
        legs = [by_id[i] for i in ids if _far_end(by_id[i], p) != p]
        for s in legs:
            angles = [interior_angle(p, _far_end(s, p), _far_end(o, p))
                      for o in legs if o is not s]
            if angles and min(angles) < math.pi - ANGLE_TOL:
                requests[(s.id, p)] = setback_request(scaling_by_net[net], min(angles))
    return requests


def _allocate(bends, requests):
    """Set each bend's shared setback: its request, capped by every leg's share.

    A leg's share splits its length with whatever bends its far end.
    """
    for b in bends:
        allocations = [b.request]
        for s in b.legs:
            mine = requests[(s.id, b.point)]
            total = mine + requests.get((s.id, _far_end(s, b.point)), 0)
            length = math.dist(s.a, s.b)
            allocations.append(mine * length / total if total > length else mine)
        b.setback = min(allocations)


def _fits_all(bend, setback, fits):
    """Ask `fits` about each arc in leg order, stopping at the first that fails."""
    return all(fits(arc) for arc in bend.arcs(setback))


def _search_fit(bend, low, high, fits):
    """Largest setback between low and high at which every arc fits, or None."""
    if not _fits_all(bend, low, fits):
        return None
    for _ in range(SEARCH_STEPS):
        mid = (low + high) / 2
        if _fits_all(bend, mid, fits):
            low = mid
        else:
            high = mid
    return low


def _size_bend(bend, min_radius_nm, fits):
    """Return (setback or None, stats key) for one bend, all-or-nothing."""
    radius = arc_radius(bend.setback, bend.interior)
    if radius <= 0 or radius < min_radius_nm:
        return None, 'sharp_length'
    if _fits_all(bend, bend.setback, fits):
        return bend.setback, 'full' if bend.setback >= bend.request else 'reduced'
    low = setback_for_radius(min_radius_nm, bend.interior)
    setback = _search_fit(bend, low, bend.setback, fits)
    return (setback, 'reduced') if setback is not None else (None, 'sharp_clearance')


def _shorten(ends, bend, setback):
    """Move every leg's bend end onto its arc tangent point."""
    for s in bend.legs:
        new = point_along(bend.point, _far_end(s, bend.point), setback)
        a, b = ends[s.id]
        ends[s.id] = (new if a == bend.point else a, new if b == bend.point else b)


def plan_fillets(segs, anchors, scaling_by_net, min_radius_nm, fits, avoid_junctions,
                 on_accept=None):
    """Plan cleanup, shortenings, fillet arcs and junction connectors.

    Plan.updates/removals describe the final state of every original segment id.
    Junctions (3+ segments) are rounded all-or-nothing, or skipped if avoid_junctions.

    `fits(arc) -> bool` must be a pure predicate: it is also asked about probe
    arcs that are never kept. `on_accept(arc)`, if given, is called exactly once
    per arc the plan keeps, in Plan.arcs order, as soon as its bend is decided
    (so later `fits` calls can account for it). Plan.tracks are not reported.
    """
    original = {s.id: (s.a, s.b) for s in segs}
    cleaned, _, removals = cleanup(segs, anchors)
    plan = Plan({}, set(removals))
    bends = _find_bends(cleaned, anchors, scaling_by_net, avoid_junctions, plan.stats)
    _allocate(bends, _end_requests(cleaned, anchors, scaling_by_net))
    ends = {s.id: (s.a, s.b) for s in cleaned}
    shortened = set()
    for b in bends:
        setback, key = _size_bend(b, min_radius_nm, fits)
        plan.stats[key] += 1
        if setback is None:
            plan.sharps.append(Sharp(b.point, b.layer, key[len('sharp_'):]))
            continue
        for arc in b.arcs(setback):
            plan.arcs.append(arc)
            if on_accept:
                on_accept(arc)
        plan.tracks.extend(b.tracks(setback))
        _shorten(ends, b, setback)
        shortened.update(s.id for s in b.legs)
    for i, (a, b) in ends.items():
        if i in shortened and math.dist(a, b) <= SLIVER_NM:
            plan.removals.add(i)
        elif (a, b) != original[i]:
            plan.updates[i] = (a, b)
    return plan
