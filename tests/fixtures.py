# Synthetic boards for the case tests. Inputs are in mm.
# Licensed under the Apache License 2.0, like the rest of this plugin.

import json
import math
import os
import shutil
import tempfile

import pcbnew


def _v(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


class Board:
    """Builds a board in memory; save() writes it for pcbnew.LoadBoard / kicad-cli."""

    def __init__(self):
        self.board = pcbnew.BOARD()
        self.nets = {}

    def net(self, name):
        if name not in self.nets:
            n = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(n)
            self.nets[name] = n
        return self.nets[name]

    def track(self, x1, y1, x2, y2, net, layer=pcbnew.F_Cu, w=0.25):
        t = pcbnew.PCB_TRACK(self.board)
        t.SetStart(_v(x1, y1))
        t.SetEnd(_v(x2, y2))
        t.SetWidth(pcbnew.FromMM(w))
        t.SetLayer(layer)
        self.board.Add(t)
        t.SetNet(self.net(net))
        return t

    def via(self, x, y, net, size=0.6, drill=0.3):
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(_v(x, y))
        v.SetWidth(pcbnew.FromMM(size))
        v.SetDrill(pcbnew.FromMM(drill))
        self.board.Add(v)
        v.SetNet(self.net(net))
        return v

    def _pad(self, x, y, attrib, shape, size, drill=None):
        fp = pcbnew.FOOTPRINT(self.board)
        self.board.Add(fp)
        p = pcbnew.PAD(fp)
        p.SetAttribute(attrib)
        p.SetShape(shape)
        p.SetSize(size)
        if drill is not None:
            p.SetDrillSize(_v(drill, drill))
        fp.Add(p)
        fp.SetPosition(_v(x, y))
        p.SetPosition(_v(x, y))
        return p

    def smd_pad(self, x, y, w, h, net, layer=pcbnew.F_Cu):
        p = self._pad(x, y, pcbnew.PAD_ATTRIB_SMD, pcbnew.PAD_SHAPE_RECT, _v(w, h))
        if layer == pcbnew.F_Cu:
            p.SetLayerSet(p.SMDMask())
        else:
            mask = pcbnew.LSET()
            mask.AddLayer(layer)
            p.SetLayerSet(mask)
        p.SetNet(self.net(net))
        return p

    def tht_pad(self, x, y, size, drill, net):
        p = self._pad(x, y, pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_SHAPE_CIRCLE, _v(size, size), drill)
        p.SetLayerSet(p.PTHMask())
        p.SetNet(self.net(net))
        return p

    def npth(self, x, y, drill):
        p = self._pad(x, y, pcbnew.PAD_ATTRIB_NPTH, pcbnew.PAD_SHAPE_CIRCLE, _v(drill, drill), drill)
        p.SetLayerSet(p.UnplatedHoleMask())
        return p

    def edge_rect(self, x1, y1, x2, y2):
        corners = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(_v(ax, ay))
            s.SetEnd(_v(bx, by))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(pcbnew.FromMM(0.05))
            self.board.Add(s)

    def keepout(self, pts, layer=pcbnew.F_Cu):
        z = pcbnew.ZONE(self.board)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(True)
        z.SetLayer(layer)
        outline = z.Outline()
        outline.NewOutline()
        for x, y in pts:
            outline.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        self.board.Add(z)
        return z

    def save(self, directory, name='case'):
        """Write <directory>/<name>.kicad_pcb and return its path.

        The board is saved elsewhere and moved into place: in KiCad 10.0.7, a
        LoadBoard of a path this process has already SaveBoard'ed ignores the
        .kicad_pro next to it (netclass patterns don't resolve).
        """
        stage = tempfile.mkdtemp()
        staged = os.path.join(stage, name + '.kicad_pcb')
        pcbnew.SaveBoard(staged, self.board)
        path = os.path.join(directory, name + '.kicad_pcb')
        shutil.move(staged, path)
        shutil.rmtree(stage, ignore_errors=True)
        return path


DEFAULT_CLASS = {
    "name": "Default", "bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25,
    "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2, "line_style": 0,
    "microvia_diameter": 0.3, "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
    "priority": 2147483647, "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": 0.2,
    "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6,
}


def project_path(board_path):
    return os.path.splitext(board_path)[0] + '.kicad_pro'


def write_project(path, netclasses, patterns):
    """Write a minimal .kicad_pro next to the board at `path` (.kicad_pcb or .kicad_pro).

    `netclasses` maps a class name to a partial dict (e.g. {"clearance": 0.2,
    "track_width": 0.5}); a net matched by one gets the composite "<name>,Default".
    `patterns` is a list of (netclass, pattern) pairs.
    """
    classes = [dict(DEFAULT_CLASS)]
    for priority, (name, fields) in enumerate(netclasses.items()):
        classes.append(dict({"name": name, "priority": priority}, **fields))
    pro = {
        "meta": {"filename": os.path.basename(project_path(path)), "version": 3},
        "net_settings": {
            "meta": {"version": 5},
            "classes": classes,
            "netclass_patterns": [{"netclass": c, "pattern": p} for c, p in patterns],
        },
    }
    with open(project_path(path), 'w') as f:
        json.dump(pro, f, indent=2)


def save_copy(board, path):
    """SaveBoard to `path`, with a copy of the loaded board's .kicad_pro beside it."""
    pcbnew.SaveBoard(path, board)
    source = project_path(board.GetFileName())
    if os.path.isfile(source) and not os.path.isfile(project_path(path)):
        shutil.copy(source, project_path(path))
    return path


def _pt(v):
    return (v.x, v.y)


def _cos(u, w):
    nu, nw = math.hypot(*u), math.hypot(*w)
    return (u[0] * w[0] + u[1] * w[1]) / (nu * nw)


def assert_tangent(test, board, min_len=1000):
    """Every PCB_ARC end meets a PCB_TRACK end (or another arc's end) tangentially.

    Tracks shorter than `min_len` nm have no reliable direction; they count for
    the meeting but not for the perpendicularity check.
    """
    tracks = [t for t in board.GetTracks() if t.GetClass() == 'PCB_TRACK']
    arcs = [t for t in board.GetTracks() if t.GetClass() == 'PCB_ARC']
    for arc in arcs:
        c = _pt(arc.GetCenter())
        for end in (_pt(arc.GetStart()), _pt(arc.GetEnd())):
            radius = (end[0] - c[0], end[1] - c[1])
            met = False
            for t in tracks:
                if t.GetLayer() != arc.GetLayer() or t.GetNetCode() != arc.GetNetCode():
                    continue
                s, e = _pt(t.GetStart()), _pt(t.GetEnd())
                if end not in (s, e):
                    continue
                met = True
                far = e if end == s else s
                if math.dist(s, e) >= min_len:
                    direction = (end[0] - far[0], end[1] - far[1])
                    test.assertLess(abs(_cos(radius, direction)), 1e-3,
                                    'arc at %s not tangent to track %s-%s' % (end, s, e))
            for other in arcs:
                if other is arc or other.GetLayer() != arc.GetLayer():
                    continue
                if end in (_pt(other.GetStart()), _pt(other.GetEnd())):
                    met = True
                    oc = _pt(other.GetCenter())
                    test.assertGreater(abs(_cos(radius, (end[0] - oc[0], end[1] - oc[1]))),
                                       1 - 1e-3, 'arcs meet non-tangentially at %s' % (end,))
            test.assertTrue(met, 'arc end %s meets no track or arc' % (end,))
