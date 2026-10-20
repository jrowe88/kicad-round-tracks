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
    """Radius in nm for every net whose primary class is enabled; missing = disabled."""
    scaling = {}
    for code, net in board.GetNetsByNetcode().items():
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


def round_board(board, settings, fits=None):
    """Fillet every in-scope corner of `board` and return the run's stats Counter."""
    if fits is None:
        fits = lambda arc: True  # Task 7 replaces this with ClearanceChecker
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
                                  settings.avoid_junctions))
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
