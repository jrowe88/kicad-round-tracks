# Board-access layer for adaptive fillets: read tracks into plain data, plan, commit.
# Runs headless (pcbnew.LoadBoard) as well as from the action plugin.
# Licensed under the Apache License 2.0, like the rest of this plugin.

from collections import Counter
from dataclasses import dataclass, field

import pcbnew

try:
    from .round_tracks_geom import Seg, STAT_KEYS, plan_fillets
    from .round_tracks_utils import withinPadPoints
except ImportError:
    from round_tracks_geom import Seg, STAT_KEYS, plan_fillets
    from round_tracks_utils import withinPadPoints


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
        if cs is not None and cs.do_round:
            scaling[code] = pcbnew.FromMM(cs.radius_mm)
    return scaling


def _pad_anchors(segs, pads, layer):
    """Shared track ends inside a same-net pad that the tracks leave (legacy withinPad)."""
    meeting = {}
    for s in segs:
        meeting.setdefault(s.a, []).append(s.b)
        meeting.setdefault(s.b, []).append(s.a)
    anchors = set()
    for point, far_ends in meeting.items():
        if len(far_ends) < 2:
            continue
        if any(p.IsOnLayer(layer) and withinPadPoints(p, point, far_ends) for p in pads):
            anchors.add(point)
    return anchors


_ANY_NET = -1        # obstacle net for board edges and keepouts: blocks every net
_CELL = 2_000_000    # grid cell size of the obstacle index, nm
_BIG = 64            # obstacles spanning more cells than this are kept in a plain list
_MARGIN = 1_000      # nm added to every required gap, against arc approximation error


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
        for ob in index.near(box, max(own, index.max_gap) + _MARGIN):
            if ob.net == arc.net:
                continue
            gap = (max(own, ob.gap) if ob.copper else ob.gap) + _MARGIN
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


def round_board(board, settings, fits=None):
    """Fillet every in-scope corner of `board` and return the run's stats Counter.

    `fits` defaults to a ClearanceChecker over the board (whose accepted arcs
    then block later ones); a custom `fits` is used as is.
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

    vias, arcs = [], []
    for t in board.GetTracks():
        cls = t.GetClass()
        if cls == 'PCB_VIA':
            vias.append(t)
        elif cls == 'PCB_ARC':
            arcs.append(t)
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
    pads_by_net = {}
    for p in board.GetPads():  # selection never matters: pads protect connectivity
        pads_by_net.setdefault(p.GetNetCode(), []).append(p)

    plans = []
    for (net, layer), g in sorted(groups.items()):
        segs = g['segs']
        if not segs:
            continue
        anchors = set(g['anchors'])
        anchors.update(_pt(v.GetPosition()) for v in vias
                       if v.GetNetCode() == net and v.IsOnLayer(layer))
        for a in arcs:
            if a.GetNetCode() == net and a.GetLayer() == layer:
                anchors.update((_pt(a.GetStart()), _pt(a.GetEnd())))
        anchors |= _pad_anchors(segs, pads_by_net.get(net, []), layer)
        plans.append(plan_fillets(segs, anchors, {net: scaling[net]}, min_radius_nm, fits,
                                  settings.avoid_junctions, on_accept))
    # Compute everything first, then touch the board.
    for plan in plans:
        commit(board, plan, objects, settings.only_selection)
        stats.update(plan.stats)
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
