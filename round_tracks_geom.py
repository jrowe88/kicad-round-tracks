# Pure geometry for adaptive fillets (no pcbnew). Units are integer nanometres.
# Licensed under the Apache License 2.0, like the rest of this plugin.

import math

Point = tuple  # (int, int)
ANGLE_TOL = 0.001  # radians


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
