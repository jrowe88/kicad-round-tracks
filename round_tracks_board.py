# Board-access layer for adaptive fillets: read tracks into plain data, plan, commit.
# Runs headless (pcbnew.LoadBoard) as well as from the action plugin.
# Licensed under the Apache License 2.0, like the rest of this plugin.

import math
from collections import Counter
from dataclasses import dataclass, field

import pcbnew

try:
    from .round_tracks_geom import Seg, STAT_KEYS, plan_fillets
except ImportError:
    from round_tracks_geom import Seg, STAT_KEYS, plan_fillets


@dataclass
class ClassSetting:
    do_round: bool
    radius_mm: float


@dataclass
class Settings:
    classes: dict = field(default_factory=dict)  # netclass name -> ClassSetting
    min_radius_mm: float = 0.1
    only_selection: bool = False
    avoid_junctions: bool = False


def primary_class(net):
    """Highest-priority class of a net: "Power" for the composite "Power,Default"."""
    return net.GetNetClassName().split(',')[0]


def _pt(v):
    return (v.x, v.y)


def _vec(p):
    return pcbnew.VECTOR2I(int(p[0]), int(p[1]))


def _scaling_by_net(board, settings):
    """Radius in nm for every net whose primary class is enabled; missing = disabled.

    Net 0 (no net) is never rounded.
    """
    scaling = {}
    for code, net in board.GetNetsByNetcode().items():
        if code == 0:
            continue
        cs = settings.classes.get(primary_class(net))
        if cs is not None and cs.do_round and cs.radius_mm > 0:
            scaling[code] = pcbnew.FromMM(cs.radius_mm)
    return scaling


_ANY_NET = -1        # obstacle net for board edges and keepouts: blocks every net
_CELL = 2_000_000    # grid cell size of the obstacle index, nm
_BIG = 64            # obstacles spanning more cells than this are kept in a plain list
# KiCad's DRC accepts a gap up to its epsilon (0.0005 mm, BOARD_DESIGN_SETTINGS::
# GetDRCEpsilon) short of the rule: kicad-cli 10.0.7 passes copper, hole and edge
# gaps 500 nm short and flags them 501 nm short. Half of that keeps a margin for
# rounding, so copper routed at exactly minimum spacing can still be rounded.
DRC_EPSILON_NM = 250


def _bbox(shape):
    box = shape.BBox()
    return box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()


class _Obstacle:
    """Something a new arc must keep `gap` away from (max'ed with the arc's own
    clearance when `copper`); same-net obstacles never block."""
    __slots__ = ('net', 'box', 'shape', 'gap', 'copper', 'owner')

    def __init__(self, net, shape, gap, copper, owner=None):
        self.net, self.shape, self.gap, self.copper = net, shape, gap, copper
        self.box = _bbox(shape)
        self.owner = owner  # keeps the pcbnew item behind `shape` alive


class _LayerIndex:
    """Bucket grid over one copper layer's obstacles, for bounding-box prefiltering."""

    def __init__(self):
        self.cells = {}
        self.big = []
        self.max_gap = 0

    def add(self, ob):
        self.max_gap = max(self.max_gap, ob.gap)
        l, t, r, b = ob.box
        xs = range(l // _CELL, r // _CELL + 1)
        ys = range(t // _CELL, b // _CELL + 1)
        if len(xs) * len(ys) > _BIG:
            self.big.append(ob)
            return
        for x in xs:
            for y in ys:
                self.cells.setdefault((x, y), []).append(ob)

    def near(self, box, reach):
        """Obstacles whose box comes within `reach` of `box` (each once)."""
        l, t, r, b = box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach
        seen = set()
        for x in range(l // _CELL, r // _CELL + 1):
            for y in range(t // _CELL, b // _CELL + 1):
                for ob in self.cells.get((x, y), ()):
                    if id(ob) not in seen:
                        seen.add(id(ob))
                        yield ob
        yield from self.big


class _Copper:
    """A same-net pad or via as plain numbers: its bounding box and position."""
    __slots__ = ('box', 'pos', 'gap')

    def __init__(self, item):
        box = item.GetBoundingBox()
        self.box = (box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom())
        self.pos = _pt(item.GetPosition())
        self.gap = 0


def _contains(box, p):
    """Inclusive, like BOX2I::Contains."""
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def _copper_by_layer(items, layers):
    """(net, layer) -> [_Copper] for each pad or via on each of `layers`."""
    out = {}
    for item in items:
        copper = None
        for layer in layers:
            if item.IsOnLayer(layer):
                copper = copper or _Copper(item)
                out.setdefault((item.GetNetCode(), layer), []).append(copper)
    return out


def _copper_index(items):
    index = _LayerIndex()
    for item in items:
        index.add(item)
    return index


def _pad_anchors(segs, pads):
    """Shared track ends inside a same-net pad that the tracks leave (legacy withinPad).

    `pads` is a _LayerIndex of the net's _Copper pads on the segments' layer.
    """
    meeting = {}
    for s in segs:
        meeting.setdefault(s.a, []).append(s.b)
        meeting.setdefault(s.b, []).append(s.a)
    anchors = set()
    for point, far_ends in meeting.items():
        if len(far_ends) < 2:
            continue
        for pad in pads.near(point + point, 0):
            if (_contains(pad.box, point)
                    and not any(_contains(pad.box, f) for f in far_ends)):
                anchors.add(point)
                break
    return anchors


def _clip(a, b, box):
    """Parameter range (t0, t1) of segment a-b inside `box`, or None."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]),
                 (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
    return (t0, t1) if t0 <= t1 else None


def _copper_points(segs, copper):
    """Where same-net pads and vias meet the segments' centrelines.

    For each segment crossing an item's bounding box: the item's position
    projected onto the segment, kept inside the part of it within the box (the
    position itself when it lies on the segment). `copper` is a _LayerIndex.
    """
    points = set()
    for s in segs:
        box = (min(s.a[0], s.b[0]), min(s.a[1], s.b[1]),
               max(s.a[0], s.b[0]), max(s.a[1], s.b[1]))
        dx, dy = s.b[0] - s.a[0], s.b[1] - s.a[1]
        length2 = dx * dx + dy * dy
        if length2 == 0:
            continue
        for item in copper.near(box, 0):
            inside = _clip(s.a, s.b, item.box)
            if inside is None:
                continue
            t = ((item.pos[0] - s.a[0]) * dx + (item.pos[1] - s.a[1]) * dy) / length2
            t = min(max(t, inside[0]), inside[1])
            points.add((round(s.a[0] + t * dx), round(s.a[1] + t * dy)))
    return points


class ClearanceChecker:
    """Decides whether a candidate fillet Arc clears everything around it.

    Obstacles: other-net tracks, arcs, vias, pads (THT on every copper layer,
    SMD on their own) and copper graphics, at the larger of the two items'
    clearances; drilled holes at the hole clearance; Edge.Cuts at the
    copper-to-edge clearance; rule areas forbidding tracks, at zero. Filled
    zones are ignored. Arcs passed to `accept` become obstacles for later arcs.
    """

    def __init__(self, board, layer_items_cache=None):
        self.board = board
        self.layers = {} if layer_items_cache is None else layer_items_cache
        ds = board.GetDesignSettings()
        self.hole_gap = ds.m_HoleClearance
        self.edge_gap = ds.m_CopperEdgeClearance
        self.net_gap = {}
        for t in board.GetTracks():
            if t.GetClass() == 'PCB_TRACK' and t.GetNetCode() not in self.net_gap:
                self.net_gap[t.GetNetCode()] = t.GetOwnClearance(t.GetLayer())

    def _arc_item(self, arc):
        item = pcbnew.PCB_ARC(self.board)
        item.SetStart(_vec(arc.start))
        item.SetMid(_vec(arc.mid))
        item.SetEnd(_vec(arc.end))
        item.SetWidth(arc.width)
        item.SetLayer(arc.layer)
        return item

    def _gap(self, arc):
        if arc.net not in self.net_gap:
            item = self._arc_item(arc)
            item.SetNetCode(arc.net)
            self.net_gap[arc.net] = item.GetOwnClearance(arc.layer)
        return self.net_gap[arc.net]

    def _index(self, layer):
        if layer not in self.layers:
            self.layers[layer] = self._build(layer)
        return self.layers[layer]

    def _build(self, layer):
        index = _LayerIndex()
        b = self.board
        for t in b.GetTracks():
            if t.GetClass() == 'PCB_VIA':
                if t.IsOnLayer(layer):
                    index.add(_Obstacle(t.GetNetCode(), t.GetEffectiveShape(layer),
                                        t.GetOwnClearance(layer), True))
                    index.add(_Obstacle(t.GetNetCode(), t.GetEffectiveHoleShape(),
                                        self.hole_gap, False))
            elif t.GetLayer() == layer:
                index.add(_Obstacle(t.GetNetCode(), t.GetEffectiveShape(layer),
                                    t.GetOwnClearance(layer), True))
        for p in b.GetPads():
            if p.IsOnLayer(layer):
                index.add(_Obstacle(p.GetNetCode(), p.GetEffectiveShape(layer),
                                    p.GetOwnClearance(layer), True))
            if p.GetDrillSize().x > 0:  # a drilled hole passes through every layer
                index.add(_Obstacle(p.GetNetCode(), p.GetEffectiveHoleShape(),
                                    self.hole_gap, False))
        drawings = list(b.GetDrawings())
        zones = list(b.Zones())
        for fp in b.GetFootprints():
            drawings.extend(fp.GraphicalItems())
            zones.extend(fp.Zones())
        for d in drawings:
            if d.GetClass() != 'PCB_SHAPE':
                continue
            if d.GetLayer() == pcbnew.Edge_Cuts:
                index.add(_Obstacle(_ANY_NET, d.GetEffectiveShape(), self.edge_gap, False))
            elif d.GetLayer() == layer:
                index.add(_Obstacle(d.GetNetCode(), d.GetEffectiveShape(layer),
                                    d.GetOwnClearance(layer), True))
        for z in zones:
            if z.GetIsRuleArea() and z.GetDoNotAllowTracks() and z.IsOnLayer(layer):
                index.add(_Obstacle(_ANY_NET, z.GetEffectiveShape(layer), 0, False))
        return index

    def fits(self, arc):
        """True when `arc` clears every obstacle; pure (no state change)."""
        item = self._arc_item(arc)
        shape = item.GetEffectiveShape(arc.layer)
        own = self._gap(arc)
        box = _bbox(shape)
        index = self._index(arc.layer)
        for ob in index.near(box, max(own, index.max_gap)):
            if ob.net == arc.net:
                continue
            # A keepout (gap 0) stays at 0: any overlap fails.
            gap = max((max(own, ob.gap) if ob.copper else ob.gap) - DRC_EPSILON_NM, 0)
            l, t, r, b = ob.box
            if l - gap > box[2] or r + gap < box[0] or t - gap > box[3] or b + gap < box[1]:
                continue
            if shape.Collide(ob.shape, gap):
                return False
        return True

    def accept(self, arc):
        """Make a kept arc an obstacle for arcs checked after it."""
        item = self._arc_item(arc)
        self._index(arc.layer).add(_Obstacle(arc.net, item.GetEffectiveShape(arc.layer),
                                             self._gap(arc), True, item))


def round_board(board, settings, fits=None, progress=None):
    """Fillet every in-scope corner of `board` and return the run's stats Counter.

    `fits` defaults to a ClearanceChecker over the board (whose accepted arcs
    then block later ones); a custom `fits` is used as is. `progress(message)`,
    if given, is called before each net is planned.
    """
    on_accept = None
    if fits is None:
        checker = ClearanceChecker(board)
        fits, on_accept = checker.fits, checker.accept
    scaling = _scaling_by_net(board, settings)
    groups = {}   # (net, layer) -> {'segs': in-scope Segs, 'anchors': out-of-scope track ends}
    objects = {}  # Seg id -> PCB_TRACK

    def group(net, layer):
        return groups.setdefault((net, layer), {'segs': [], 'anchors': set()})

    vias, arc_ends = [], {}  # same-net vias; (net, layer) -> existing arc ends
    for t in board.GetTracks():
        cls = t.GetClass()
        if cls == 'PCB_VIA':
            if t.GetNetCode() in scaling:
                vias.append(t)
        elif cls == 'PCB_ARC':
            if t.GetNetCode() in scaling:
                arc_ends.setdefault((t.GetNetCode(), t.GetLayer()), set()).update(
                    (_pt(t.GetStart()), _pt(t.GetEnd())))
        elif cls == 'PCB_TRACK':
            net, layer = t.GetNetCode(), t.GetLayer()
            in_scope = net in scaling and (not settings.only_selection or t.IsSelected())
            if in_scope:
                objects[len(objects)] = t
                group(net, layer)['segs'].append(
                    Seg(len(objects) - 1, _pt(t.GetStart()), _pt(t.GetEnd()),
                        t.GetWidth(), layer, net))
            elif net in scaling:
                group(net, layer)['anchors'].update((_pt(t.GetStart()), _pt(t.GetEnd())))

    stats = Counter(dict.fromkeys(STAT_KEYS, 0))
    min_radius_nm = pcbnew.FromMM(settings.min_radius_mm)
    # Pads and vias are read from pcbnew once per run, not once per corner.
    # Selection never matters: they protect connectivity.
    layers = {layer for _, layer in groups}
    pads = _copper_by_layer([p for p in board.GetPads() if p.GetNetCode() in scaling], layers)
    via_copper = _copper_by_layer(vias, layers)
    todo = [(key, g) for key, g in sorted(groups.items()) if g['segs']]
    names = ({code: net.GetNetname() for code, net in board.GetNetsByNetcode().items()}
             if progress else {})

    plans = []
    for n, (key, g) in enumerate(todo):
        if progress:
            progress('Rounding net %s (%d of %d)' % (names.get(key[0], key[0]), n + 1, len(todo)))
        segs = g['segs']
        pad_items, via_items = pads.get(key, []), via_copper.get(key, [])
        ends = g['anchors'] | arc_ends.get(key, set())  # other same-net track and arc ends
        anchors = ends | {v.pos for v in via_items}
        anchors |= _pad_anchors(segs, _copper_index(pad_items))
        attach = ends | _copper_points(segs, _copper_index(pad_items + via_items))
        plans.append(plan_fillets(segs, anchors, {key[0]: scaling[key[0]]}, min_radius_nm,
                                  fits, settings.avoid_junctions, on_accept,
                                  attach_points=attach))
    # Compute everything first, then touch the board.
    sharps = []
    for plan in plans:
        commit(board, plan, objects, settings.only_selection)
        stats.update(plan.stats)
        sharps.extend(plan.sharps)
    draw_warnings(board, sharps)
    return stats


def _add(board, item, layer, width, net, select):
    item.SetWidth(width)
    item.SetLayer(layer)
    board.Add(item)
    item.SetNetCode(net)  # after Add, as the legacy code does
    if select:
        item.SetSelected()


def commit(board, plan, objects, select):
    """Apply a Plan: move and remove original tracks, add arcs and connector tracks."""
    for i, (a, b) in plan.updates.items():
        objects[i].SetStart(_vec(a))
        objects[i].SetEnd(_vec(b))
    for i in plan.removals:
        board.Remove(objects[i])
    for arc in plan.arcs:
        item = pcbnew.PCB_ARC(board)
        item.SetStart(_vec(arc.start))
        item.SetMid(_vec(arc.mid))
        item.SetEnd(_vec(arc.end))
        _add(board, item, arc.layer, arc.width, arc.net, select)
    for seg in plan.tracks:
        item = pcbnew.PCB_TRACK(board)
        item.SetStart(_vec(seg.a))
        item.SetEnd(_vec(seg.b))
        _add(board, item, seg.layer, seg.width, seg.net, select)


WARNING_GROUP = 'round-tracks-warnings'
_WARNING_TEXT = {'length': 'too short', 'clearance': 'clearance'}
_ARROW_LEN_MM = 1.5
_ARROW_HEAD_MM = 0.4
_ARROW_WIDTH_MM = 0.1
_TEXT_HEIGHT_MM = 0.5


def _remove_warnings(board):
    for group in [g for g in board.Groups() if g.GetName() == WARNING_GROUP]:
        items = list(group.GetItems())
        for item in items:
            group.RemoveItem(item)
            board.Remove(item)
        board.Remove(group)


def _line(board, group, a, b):
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
    shape.SetStart(_vec(a))
    shape.SetEnd(_vec(b))
    shape.SetLayer(pcbnew.Cmts_User)
    shape.SetWidth(pcbnew.FromMM(_ARROW_WIDTH_MM))
    board.Add(shape)
    group.AddItem(shape)


def draw_warnings(board, sharps):
    """Replace the warning group: an arrow and a label at every corner left sharp."""
    _remove_warnings(board)
    if not sharps:
        return
    group = pcbnew.PCB_GROUP(board)
    group.SetName(WARNING_GROUP)
    board.Add(group)
    length = pcbnew.FromMM(_ARROW_LEN_MM)
    head = pcbnew.FromMM(_ARROW_HEAD_MM)
    back = math.radians(45 + 180)  # from the tip towards the tail (upper-left)
    for sharp in sharps:
        tip = sharp.point
        tail = (tip[0] + round(length * math.cos(back)),
                tip[1] + round(length * math.sin(back)))
        _line(board, group, tail, tip)
        for spread in (-30, 30):
            ang = back + math.radians(spread)
            _line(board, group, tip, (tip[0] + round(head * math.cos(ang)),
                                      tip[1] + round(head * math.sin(ang))))
        text = pcbnew.PCB_TEXT(board)
        text.SetText(_WARNING_TEXT[sharp.reason])
        text.SetLayer(pcbnew.Cmts_User)
        text.SetPosition(_vec(tail))
        size = pcbnew.FromMM(_TEXT_HEIGHT_MM)
        text.SetTextSize(pcbnew.VECTOR2I(size, size))
        board.Add(text)
        group.AddItem(text)


def summary_text(stats):
    rounded = stats['full'] + stats['reduced'] + stats['merged']
    return ('Rounded %d corners (%d full, %d reduced, %d merged). '
            'Left sharp: %d too short, %d clearance. Skipped: %d.'
            % (rounded, stats['full'], stats['reduced'], stats['merged'],
               stats['sharp_length'], stats['sharp_clearance'], stats['skipped']))
