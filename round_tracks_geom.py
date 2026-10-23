# Pure geometry for adaptive fillets (no pcbnew). Units are integer nanometres.
# Licensed under the Apache License 2.0, like the rest of this plugin.

import math
from collections import Counter
from dataclasses import dataclass, field, replace

Point = tuple  # (int, int)
ANGLE_TOL = 0.001  # radians
# A joint turning less than this is left as is: its arc would be enormous
# (metres for a router kink) and differ from the corner by a few microns.
STRAIGHT_TOL = 0.02  # radians
# Largest arc radius ever planned: KiCad's coordinates are int32 nm (+-2.1 m),
# and an arc's centre must stay representable.
MAX_RADIUS_NM = 1_000_000_000
SLIVER_NM = 1000  # segments shorter than this are cleanup candidates
ATTACH_NM = 1000  # a point this close to a segment's line, between its ends, is on it
_ATTACH_CELL = 1_000_000  # grid cell of the attachment point index, nm


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
        return s1.id, s2.id
    return None


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


JOG_PARALLEL = math.pi / 8  # neighbours this close in direction count as parallel


def _keeps_direction(s, old, new):
    """Moving `s`'s end at `old` to `new` neither reverses it nor makes it a sliver."""
    far = _far_end(s, old)
    if math.dist(far, new) <= SLIVER_NM:
        return False
    return (old[0] - far[0]) * (new[0] - far[0]) + (old[1] - far[1]) * (new[1] - far[1]) > 0


def _line_meet(f1, p, q, f2):
    """Where line f1-p meets line q-f2, rounded to nm; None when parallel."""
    d1 = (p[0] - f1[0], p[1] - f1[1])
    d2 = (f2[0] - q[0], f2[1] - q[1])
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if det == 0:
        return None
    t = ((q[0] - p[0]) * d2[1] - (q[1] - p[1]) * d2[0]) / det
    return (round(p[0] + t * d1[0]), round(p[1] + t * d1[1]))


def _jog_moves(s, neighbours):
    """End moves, as (segment, old end, new end), that collapse jog `s`; or None.

    `neighbours` maps each end of `s` to the other segments ending there.
    A: both ends are plain two-segment points and the neighbours' lines meet
    within twice the jog's length of both ends: both neighbours extend to that
    point. B: the neighbours are (near-)parallel and head the same way, an
    S-offset: the longer one tilts to reach the jog's far end. C: one end is a
    junction, the other a plain point: the neighbour there reaches across to
    the junction.
    """
    p, q = s.a, s.b
    length = math.dist(p, q)
    plain = [e for e in (p, q) if len(neighbours[e]) == 1]
    for e in plain:
        n = neighbours[e][0]
        f = _far_end(n, e)
        if f == e or interior_angle(e, f, _far_end(s, e)) >= math.pi - ANGLE_TOL:
            return None  # degenerate, or collinear (merging handles that)
    if len(plain) == 2:
        n1, n2 = neighbours[p][0], neighbours[q][0]
        if n1 is n2:
            return None
        f1, f2 = _far_end(n1, p), _far_end(n2, q)
        v = _line_meet(f1, p, q, f2)
        if (v is not None and math.dist(v, p) <= 2 * length and math.dist(v, q) <= 2 * length
                and _keeps_direction(n1, p, v) and _keeps_direction(n2, q, v)):
            return [(n1, p, v), (n2, q, v)]
        if interior_angle((0, 0), (p[0] - f1[0], p[1] - f1[1]),
                          (f2[0] - q[0], f2[1] - q[1])) > JOG_PARALLEL:
            return None
        l1, l2 = math.dist(f1, p), math.dist(q, f2)
        n, old, new = (n1, p, q) if (l1, -n1.id) >= (l2, -n2.id) else (n2, q, p)
        return [(n, old, new)] if _keeps_direction(n, old, new) else None
    if len(plain) == 1:
        e = plain[0]
        j = q if e == p else p
        n = neighbours[e][0]
        if _far_end(n, e) == j:
            return None
        return [(n, e, j)] if _keeps_direction(n, e, j) else None
    return None


def _along_interior(pt, a, b):
    """Distance from `a` along segment a-b to `pt` when `pt` is within ATTACH_NM
    of its line and strictly between its ends; None otherwise."""
    length = math.dist(a, b)
    if length == 0 or pt == a or pt == b:
        return None
    dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    rx, ry = pt[0] - a[0], pt[1] - a[1]
    along = rx * dx + ry * dy
    if 0 < along < length and abs(rx * dy - ry * dx) <= ATTACH_NM:
        return along
    return None


def _on_interior(pt, a, b):
    return _along_interior(pt, a, b) is not None


class _PointGrid:
    """Points bucketed on a grid, to find those near a segment quickly."""

    def __init__(self, points):
        self.cells = {}
        for p in points:
            self.cells.setdefault((p[0] // _ATTACH_CELL, p[1] // _ATTACH_CELL), []).append(p)

    def near(self, a, b):
        """Every point within ATTACH_NM of segment a-b, maybe with a few more."""
        length = math.dist(a, b)
        # Sample at most a cell apart: anything within ATTACH_NM of the segment
        # is then in a cell next to a sample's.
        n = int(length // _ATTACH_CELL) + 1
        cells = set()
        for k in range(n + 1):
            cx = int((a[0] + (b[0] - a[0]) * k / n) // _ATTACH_CELL)
            cy = int((a[1] + (b[1] - a[1]) * k / n) // _ATTACH_CELL)
            cells.update((cx + i, cy + j) for i in (-1, 0, 1) for j in (-1, 0, 1))
        for cell in cells:
            yield from self.cells.get(cell, ())

    def on_interior(self, a, b):
        return [p for p in self.near(a, b) if _on_interior(p, a, b)]


def _keeps_attachments(original, moved, grid):
    """Every point attached to `original`'s interior is still on `moved`'s."""
    return all(_on_interior(pt, moved.a, moved.b) for pt in grid.on_interior(original.a, original.b))


def _try_collapse_jog(segs, index, anchors, seg_fits):
    """Collapse one micro-jog if every moved segment passes `seg_fits`.

    A jog is a segment no longer than half its width, with neither end an
    anchor or free, whose neighbours all share its width. Nothing may be
    attached partway along the jog (it goes away), and a collapse that tilts
    a neighbour must not move its line off anything attached partway along it
    (an anchor or attachment, or another segment's end), or that copper would
    stop counting as attached and lose its connection. Returns the ids of the
    moved segments, or None.
    """
    grid = None
    for s in sorted(segs.values(), key=lambda s: s.id):
        length = math.dist(s.a, s.b)
        if length == 0 or length > s.width / 2 or s.a in anchors or s.b in anchors:
            continue
        neighbours = {e: [segs[i] for i in sorted(index[(s.layer, s.net, e)] - {s.id})]
                      for e in (s.a, s.b)}
        if any(not ns for ns in neighbours.values()):
            continue
        if any(n.width != s.width for ns in neighbours.values() for n in ns):
            continue
        moves = _jog_moves(s, neighbours)
        if not moves:
            continue
        moved = []
        for n, old, new in moves:
            m = replace(n)
            _replace_end(m, old, new)
            moved.append(m)
        if grid is None:  # once per pass, and only once a candidate gets this far
            grid = _PointGrid(set(anchors) | {e for o in segs.values() for e in (o.a, o.b)})
        if grid.on_interior(s.a, s.b):
            continue
        if not all(_keeps_attachments(n, m, grid) for (n, _, _), m in zip(moves, moved)):
            continue
        if not all(seg_fits(m) for m in moved):
            continue
        for n, old, new in moves:
            _replace_end(n, old, new)
        del segs[s.id]
        return [n.id for n, _, _ in moves]
    return None


def _cleanup(segs, anchors, seg_fits=None):
    """cleanup(), plus the ids of surviving segments a jog collapse moved."""
    original = {s.id: (s.a, s.b) for s in segs}
    work = {s.id: Seg(s.id, s.a, s.b, s.width, s.layer, s.net) for s in segs}
    jogged = set()
    while True:
        merged = _try_merge(work, _index(work), anchors)
        if merged:
            if merged[1] in jogged:
                jogged.add(merged[0])
            continue
        if _try_remove_sliver(work, _index(work), anchors):
            continue
        moved = seg_fits and _try_collapse_jog(work, _index(work), anchors, seg_fits)
        if not moved:
            break
        jogged.update(moved)
    new_segs = [work[i] for i in sorted(work)]
    updates = {s.id: (s.a, s.b) for s in new_segs if (s.a, s.b) != original[s.id]}
    return new_segs, updates, set(original) - set(work), jogged & set(work)


def cleanup(segs, anchors, seg_fits=None):
    """Merge collinear fragments, drop slivers and, given `seg_fits`, collapse micro-jogs.

    Returns (new_segs, updates, removals): updates maps the id of a surviving
    original segment to its new (a, b); removals holds the ids that went away.
    A jog collapse moves copper sideways (by at most the jog's length), so it
    happens only when `seg_fits(seg) -> bool` accepts every moved segment;
    without `seg_fits` no jog is collapsed.
    """
    return _cleanup(segs, anchors, seg_fits)[:3]


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
LADDER_STEPS = 8  # setbacks probed, geometrically spaced from min radius to allocated
SEARCH_STEPS = 6  # bisections between the largest fitting probe and the next one up


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
        return [(i, j, gap) for i, j, gap in self.pairs if gap < math.pi - STRAIGHT_TOL]

    def _straight_pairs(self):
        return [(i, j, gap) for i, j, gap in self.pairs
                if abs(gap - math.pi) <= STRAIGHT_TOL]

    @property
    def interior(self):
        """Tightest arc pair: it has the smallest radius at the shared setback."""
        return min(gap for _, _, gap in self._arc_pairs())

    def arcs(self, setback):
        return [Arc(*fillet_points(self.point, self._far(i), self._far(j), setback),
                    self.legs[0].width, self.layer, self.net)
                for i, j, _ in self._arc_pairs()]

    def tracks(self, setback):
        return [bar for bar, _ in self.bars(setback)]

    def bars(self, setback):
        """(connector Seg, gap) for each straight pair."""
        return [(Seg(-1, point_along(self.point, self._far(i), setback),
                     point_along(self.point, self._far(j), setback),
                     self.legs[0].width, self.layer, self.net), gap)
                for i, j, gap in self._straight_pairs()]


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
            if interior >= math.pi - STRAIGHT_TOL:
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
            if angles and min(angles) < math.pi - STRAIGHT_TOL:
                requests[(s.id, p)] = setback_request(scaling_by_net[net], min(angles))
    return requests


def _share(s, p, requests):
    """Length of `s` its end at `p` may use: its request, or a proportional part
    of the segment when the requests at both ends don't fit."""
    mine = requests.get((s.id, p), 0)
    total = mine + requests.get((s.id, _far_end(s, p)), 0)
    length = math.dist(s.a, s.b)
    return mine * length / total if total > length else mine


def _attachment_limits(segs, points):
    """Distance from each segment end to the nearest of `points` on its interior.

    Keyed by (seg id, end point); ends with nothing between them and the other
    end are left out. A point is on a segment when it is within ATTACH_NM of its
    line and strictly between its ends.
    """
    grid = _PointGrid(points)
    limits = {}
    for s in segs:
        length = math.dist(s.a, s.b)
        if length == 0:
            continue
        near_a = near_b = math.inf
        for p in grid.near(s.a, s.b):
            along = _along_interior(p, s.a, s.b)
            if along is not None:
                near_a, near_b = min(near_a, along), min(near_b, length - along)
        if near_a < math.inf:
            limits[(s.id, s.a)], limits[(s.id, s.b)] = near_a, near_b
    return limits


def _kink_limits(segs, anchors, limits):
    """Keep every leg two slivers short of a near-straight kink at its far end.

    A joint turning by ANGLE_TOL..STRAIGHT_TOL gets no arc and reserves
    nothing, so a bend at the leg's other end could use the leg up and end its
    arc right at the kink, off-tangent to the next segment. The margin is
    two slivers so that, after rounding to nm, the piece left is never a
    sliver that a later run would clean up (moving the kink onto the arc).
    """
    by_id = {s.id: s for s in segs}
    for (_, _, p), ids in _index(by_id).items():
        if len(ids) != 2 or p in anchors:
            continue
        s1, s2 = (by_id[i] for i in sorted(ids))
        f1, f2 = _far_end(s1, p), _far_end(s2, p)
        if f1 == p or f2 == p:
            continue
        if not math.pi - STRAIGHT_TOL <= interior_angle(p, f1, f2) < math.pi - ANGLE_TOL:
            continue
        for s, f in ((s1, f1), (s2, f2)):
            cap = max(math.dist(s.a, s.b) - 2 * SLIVER_NM, 0)
            limits[(s.id, f)] = min(limits.get((s.id, f), math.inf), cap)


def _usable(s, p, requests, limits):
    """Length of `s` its end at `p` may use: its share, stopping at any attachment."""
    return min(_share(s, p, requests), limits.get((s.id, p), math.inf))


def _radius_cap(gaps):
    """Largest setback that keeps every arc of these gaps within MAX_RADIUS_NM.

    The widest gap has the largest radius at a shared setback, so it decides.
    """
    return setback_for_radius(MAX_RADIUS_NM, max(gaps))


def _allocate(bends, requests, limits):
    """Set each bend's shared setback: its request, capped by every leg's usable
    length and by MAX_RADIUS_NM for each of its arcs."""
    for b in bends:
        b.setback = min([b.request, _radius_cap([g for _, _, g in b._arc_pairs()])]
                        + [_usable(s, b.point, requests, limits) for s in b.legs])


def _turn(prev, p, nxt):
    """Cross product of the two directions through p; its sign is the turn side."""
    return (p[0] - prev[0]) * (nxt[1] - p[1]) - (p[1] - prev[1]) * (nxt[0] - p[0])


def _other_leg(corner, s):
    return corner.legs[1] if corner.legs[0] is s else corner.legs[0]


def _find_runs(bends, requests, limits, attach):
    """Chains of 2+ corners (as indices into bends, in path order) to try merging.

    Neighbours in a run share a segment too short for both requests and turn the
    same way. Only two-segment corners take part, and closed loops are never runs.
    A shared segment with an attachment on it, or at either end, never links.
    """
    at = {}
    for k, b in enumerate(bends):
        if len(b.legs) == 2:
            for s in b.legs:
                at.setdefault(s.id, []).append((k, s))
    links = {}
    for ends in at.values():
        if len(ends) != 2:
            continue
        (i, s), (j, _) = ends
        c, d = bends[i], bends[j]
        if _share(s, c.point, requests) >= requests[(s.id, c.point)]:
            continue
        if (s.id, c.point) in limits or c.point in attach or d.point in attach:
            continue
        fc, fd = _far_end(_other_leg(c, s), c.point), _far_end(_other_leg(d, s), d.point)
        if _turn(fc, c.point, d.point) * _turn(c.point, d.point, fd) <= 0:
            continue
        links.setdefault(i, []).append(j)
        links.setdefault(j, []).append(i)
    runs, seen = [], set()
    for start in sorted(links):
        if start in seen or len(links[start]) != 1:
            continue
        run, prev = [start], None
        while True:
            seen.add(run[-1])
            nxt = [k for k in links[run[-1]] if k != prev]
            if not nxt:
                break
            prev = run[-1]
            run.append(nxt[0])
        runs.append(run)
    return runs


def _merge(run, bends, requests, limits, scaling_nm, min_radius_nm):
    """The run as one virtual corner where its outer legs' lines meet.

    Returns (virtual bend, setback, inner segments, (outer leg id, run end) pairs),
    or None when the merged arc can't be tangent to both outer legs, is below
    min radius, or isn't larger than the run's best separate arc (by allocation).
    """
    corners = [bends[k] for k in run]
    inner = [next(s for s in c.legs if s in d.legs) for c, d in zip(corners, corners[1:])]
    first, last = corners[0], corners[-1]
    outer = (_other_leg(first, inner[0]), _other_leg(last, inner[-1]))
    turn = sum(math.pi - c.interior for c in corners)
    if outer[0] is outer[1] or turn >= math.pi - ANGLE_TOL:
        return None
    (p0, f0), (pn, fn) = ((c.point, _far_end(s, c.point)) for c, s in zip((first, last), outer))
    u0 = [(p0[i] - f0[i]) / math.dist(p0, f0) for i in (0, 1)]
    un = [(fn[i] - pn[i]) / math.dist(pn, fn) for i in (0, 1)]
    det = u0[0] * un[1] - u0[1] * un[0]
    if det == 0:
        return None
    d = (pn[0] - p0[0], pn[1] - p0[1])
    to_v = ((d[0] * un[1] - d[1] * un[0]) / det, (u0[0] * d[1] - u0[1] * d[0]) / det)
    if min(to_v) <= 0:
        return None
    v = (p0[0] + to_v[0] * u0[0], p0[1] + to_v[0] * u0[1])
    # Each tangent point stays clear of whatever the leg's far end reserves, and
    # short of any attachment on the leg. The merged request is below every corner
    # request in the run, so two merged runs sharing a leg can't overlap either.
    legs, caps = [], []
    for s, p, dist_v in zip(outer, (p0, pn), to_v):
        caps.append(dist_v + math.dist(s.a, s.b) - _share(s, _far_end(s, p), requests))
        caps.append(dist_v + limits.get((s.id, p), math.inf))
        leg = replace(s)
        _replace_end(leg, p, v)
        legs.append(leg)
    legs.sort(key=lambda s: s.id)
    interior = interior_angle(v, *(_far_end(s, v) for s in legs))
    virtual = _Bend(v, first.layer, first.net, tuple(legs), ((0, 1, interior),))
    setback = min([setback_request(scaling_nm, interior), _radius_cap([interior])] + caps)
    radius = arc_radius(setback, interior)
    best = max(arc_radius(c.setback, c.interior) for c in corners)
    if setback < max(to_v) or radius < min_radius_nm or radius <= best:
        return None
    return virtual, setback, inner, ((outer[0].id, p0), (outer[1].id, pn))


def _bar_fits(bar, gap, seg_fits):
    """A connector on exactly straight legs lies on their copper; one across a
    slight kink cuts the corner, so it needs `seg_fits` (none: it can't go)."""
    if abs(gap - math.pi) <= ANGLE_TOL:
        return True
    return seg_fits is not None and seg_fits(bar)


def _fits_all(bend, setback, fits, seg_fits=None):
    """Ask `fits` about each arc in leg order, then about each connector,
    stopping at the first that fails."""
    return (all(fits(arc) for arc in bend.arcs(setback))
            and all(_bar_fits(bar, gap, seg_fits) for bar, gap in bend.bars(setback)))


def _search_fit(bend, low, high, fits, seg_fits=None):
    """Largest setback between low and high at which every arc fits, or None.

    `high` is known to fail. Fitting needn't be monotonic in the setback (an
    obstacle can block small arcs and miss larger ones), so a geometric ladder
    from low to high is probed first, largest first; the search then bisects
    between the largest probe that fits and the next probe up.
    """
    if low <= 0:  # no min radius: start the ladder well below high instead
        low = high / 2 ** (LADDER_STEPS - 1)
    ratio = (high / low) ** (1 / (LADDER_STEPS - 1))
    ladder = [low * ratio ** k for k in range(LADDER_STEPS - 1)] + [high]
    for k in range(LADDER_STEPS - 2, -1, -1):
        if _fits_all(bend, ladder[k], fits, seg_fits):
            break
    else:
        return None
    low, high = ladder[k], ladder[k + 1]
    for _ in range(SEARCH_STEPS):
        mid = (low + high) / 2
        if _fits_all(bend, mid, fits, seg_fits):
            low = mid
        else:
            high = mid
    return low


def _size_bend(bend, min_radius_nm, fits, seg_fits=None):
    """Return (setback or None, stats key) for one bend, all-or-nothing."""
    radius = arc_radius(bend.setback, bend.interior)
    if radius <= 0 or radius < min_radius_nm:
        return None, 'sharp_length'
    if _fits_all(bend, bend.setback, fits, seg_fits):
        return bend.setback, 'full' if bend.setback >= bend.request else 'reduced'
    low = setback_for_radius(min_radius_nm, bend.interior)
    setback = _search_fit(bend, low, bend.setback, fits, seg_fits)
    return (setback, 'reduced') if setback is not None else (None, 'sharp_clearance')


def _shorten(ends, bend, setback):
    """Move every leg's bend end onto its arc tangent point."""
    for s in bend.legs:
        new = point_along(bend.point, _far_end(s, bend.point), setback)
        a, b = ends[s.id]
        ends[s.id] = (new if a == bend.point else a, new if b == bend.point else b)


def plan_fillets(segs, anchors, scaling_by_net, min_radius_nm, fits, avoid_junctions,
                 on_accept=None, attach_points=None, seg_fits=None, on_accept_seg=None):
    """Plan cleanup, shortenings, fillet arcs and junction connectors.

    Plan.updates/removals describe the final state of every original segment id.
    Junctions (3+ segments) are rounded all-or-nothing, or skipped if avoid_junctions.
    A run of same-direction corners joined by short segments becomes one arc on
    its outer legs when that arc is larger and fits (take-or-leave, no search);
    otherwise its corners are sized one by one. The run is decided in the place
    of its first corner in bend order.

    `fits(arc) -> bool` must be a pure predicate: it is also asked about probe
    arcs that are never kept. `on_accept(arc)`, if given, is called exactly once
    per arc the plan keeps, in Plan.arcs order, as soon as its bend is decided
    (so later `fits` calls can account for it).

    `attach_points` are where other same-net copper (track and arc ends, vias,
    pads) meets these segments. Cleanup never merges or drops a sliver through
    one, and no setback or merged arc passes one lying on a segment: the copper
    left over still reaches it. Ends of the segments themselves that land on
    another segment's interior count too.

    `seg_fits(seg) -> bool`, if given, enables micro-jog collapse in cleanup
    (see cleanup) and is asked about junction connectors that cut across a
    slight kink (without it such a junction stays sharp); like `fits` it must
    be pure. `on_accept_seg(seg)`, if given, is called for each kept junction
    connector as its bend is decided, and once for each surviving segment a
    jog collapse moved, with its final ends, after the plan is made.
    """
    original = {s.id: (s.a, s.b) for s in segs}
    attach = set(attach_points or ())
    cleaned, _, removals, jogged = _cleanup(segs, set(anchors) | attach, seg_fits)
    plan = Plan({}, set(removals))
    bends = _find_bends(cleaned, anchors, scaling_by_net, avoid_junctions, plan.stats)
    requests = _end_requests(cleaned, anchors, scaling_by_net)
    limits = _attachment_limits(cleaned, attach | {e for s in cleaned for e in (s.a, s.b)})
    _kink_limits(cleaned, anchors, limits)
    _allocate(bends, requests, limits)
    runs = {min(run): run for run in _find_runs(bends, requests, limits, attach)}
    ends = {s.id: (s.a, s.b) for s in cleaned}
    shortened = set()
    merged = set()
    for k, b in enumerate(bends):
        if k in merged:
            continue
        if k in runs:
            m = _merge(runs[k], bends, requests, limits, scaling_by_net[b.net],
                       min_radius_nm)
            arc = m and m[0].arcs(m[1])[0]
            if arc and fits(arc):
                virtual, setback, inner, joins = m
                plan.arcs.append(arc)
                if on_accept:
                    on_accept(arc)
                plan.stats['merged'] += 1
                merged.update(runs[k])
                for s in inner:
                    del ends[s.id]
                    plan.removals.add(s.id)
                for i, p in joins:  # extend the outer legs to the virtual corner
                    ends[i] = tuple(virtual.point if e == p else e for e in ends[i])
                _shorten(ends, virtual, setback)
                shortened.update(i for i, _ in joins)
                continue
        setback, key = _size_bend(b, min_radius_nm, fits, seg_fits)
        plan.stats[key] += 1
        if setback is None:
            plan.sharps.append(Sharp(b.point, b.layer, key[len('sharp_'):]))
            continue
        for arc in b.arcs(setback):
            plan.arcs.append(arc)
            if on_accept:
                on_accept(arc)
        for bar in b.tracks(setback):
            plan.tracks.append(bar)
            if on_accept_seg:
                on_accept_seg(bar)
        _shorten(ends, b, setback)
        shortened.update(s.id for s in b.legs)
    for i, (a, b) in ends.items():
        # Only a fully used-up segment goes; any leftover, however short, keeps
        # the arcs at its ends connected (and is collinear with their tangents).
        if i in shortened and a == b:
            plan.removals.add(i)
        elif (a, b) != original[i]:
            plan.updates[i] = (a, b)
    if on_accept_seg:
        for s in cleaned:
            if s.id in jogged and s.id not in plan.removals:
                on_accept_seg(Seg(s.id, *ends[s.id], s.width, s.layer, s.net))
    return plan
