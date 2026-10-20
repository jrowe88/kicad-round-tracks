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


def fillet_points(corner, d1, d2, setback):
    """Return (start, mid, end) of the fillet arc. d1/d2 are the far ends of the legs."""
    h = interior_angle(corner, d1, d2) / 2
    pts = []
    for d in (d1, d2):
        dx, dy = d[0] - corner[0], d[1] - corner[1]
        length = math.hypot(dx, dy)
        pts.append((round(corner[0] + dx / length * setback),
                    round(corner[1] + dy / length * setback)))
    start, end = pts
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
    stats: Counter = field(default_factory=lambda: Counter(dict.fromkeys(STAT_KEYS, 0)))


@dataclass
class _Corner:
    point: Point
    layer: int
    net: int
    legs: tuple  # (Seg, Seg), ordered by id
    interior: float
    request: float
    setback: float = 0.0

    def far_ends(self):
        return tuple(_far_end(s, self.point) for s in self.legs)

    def arc(self, setback):
        start, mid, end = fillet_points(self.point, *self.far_ends(), setback)
        return Arc(start, mid, end, self.legs[0].width, self.layer, self.net)


def _find_corners(segs, anchors, scaling_by_net, stats):
    """Two-segment bends in deterministic order; mixed-width bends count as skipped."""
    corners = []
    by_id = {s.id: s for s in segs}
    for (layer, net, p), ids in _index(by_id).items():
        if len(ids) != 2 or p in anchors:
            continue
        legs = tuple(by_id[i] for i in sorted(ids))
        f1, f2 = (_far_end(s, p) for s in legs)
        if f1 == p or f2 == p:
            continue
        interior = interior_angle(p, f1, f2)
        if interior >= math.pi - ANGLE_TOL:
            continue
        if legs[0].width != legs[1].width:
            stats['skipped'] += 1
            continue
        corners.append(_Corner(p, layer, net, legs, interior,
                               setback_request(scaling_by_net[net], interior)))
    corners.sort(key=lambda c: (c.net, c.layer, c.point))
    return corners


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


def _allocate(corners, requests):
    """Set each corner's setback, sharing segments with whatever bends their far ends."""
    for c in corners:
        allocations = []
        for s in c.legs:
            mine = requests[(s.id, c.point)]
            total = mine + requests.get((s.id, _far_end(s, c.point)), 0)
            length = math.dist(s.a, s.b)
            allocations.append(mine * length / total if total > length else mine)
        c.setback = min(allocations)


def _search_fit(corner, low, high, fits):
    """Largest arc between setbacks low and high that fits, or None."""
    best = corner.arc(low)
    if not fits(best):
        return None
    for _ in range(SEARCH_STEPS):
        mid = (low + high) / 2
        arc = corner.arc(mid)
        if fits(arc):
            low, best = mid, arc
        else:
            high = mid
    return best


def _size_corner(corner, min_radius_nm, fits):
    """Return (arc or None, stats key) for one corner."""
    radius = arc_radius(corner.setback, corner.interior)
    if radius <= 0 or radius < min_radius_nm:
        return None, 'sharp_length'
    arc = corner.arc(corner.setback)
    if fits(arc):
        return arc, 'full' if corner.setback >= corner.request else 'reduced'
    low = setback_for_radius(min_radius_nm, corner.interior)
    arc = _search_fit(corner, low, corner.setback, fits)
    return (arc, 'reduced') if arc else (None, 'sharp_clearance')


def _shorten(ends, corner, arc):
    """Move both legs' corner ends onto the arc's tangent points."""
    for s, new in zip(corner.legs, (arc.start, arc.end)):
        a, b = ends[s.id]
        ends[s.id] = (new if a == corner.point else a, new if b == corner.point else b)


def plan_fillets(segs, anchors, scaling_by_net, min_radius_nm, fits, avoid_junctions):
    """Plan cleanup, shortenings and fillet arcs for straight segments.

    Plan.updates/removals describe the final state of every original segment id.
    Junctions (3+ segments) are left untouched; avoid_junctions is not used yet.
    """
    original = {s.id: (s.a, s.b) for s in segs}
    cleaned, _, removals = cleanup(segs, anchors)
    plan = Plan({}, set(removals))
    corners = _find_corners(cleaned, anchors, scaling_by_net, plan.stats)
    _allocate(corners, _end_requests(cleaned, anchors, scaling_by_net))
    ends = {s.id: (s.a, s.b) for s in cleaned}
    shortened = set()
    for c in corners:
        arc, key = _size_corner(c, min_radius_nm, fits)
        plan.stats[key] += 1
        if arc is None:
            plan.sharps.append(Sharp(c.point, c.layer, key[len('sharp_'):]))
            continue
        plan.arcs.append(arc)
        _shorten(ends, c, arc)
        shortened.update(s.id for s in c.legs)
    for i, (a, b) in ends.items():
        if i in shortened and math.dist(a, b) <= SLIVER_NM:
            plan.removals.add(i)
        elif (a, b) != original[i]:
            plan.updates[i] = (a, b)
    return plan
