# Config file read/write for the Round Tracks dialog. Pure text handling.
# Licensed under the Apache License 2.0, like the rest of this plugin.
#
# The format stays readable by upstream's plugin: class lines keep the legacy
# "passes" column (written as 3, ignored on read) and the checkbox line keeps
# three bools. The extra "min_radius" and "refill_zones" lines are silently
# ignored by upstream.

import math

try:
    from .round_tracks_board import ClassSetting, Settings
except ImportError:
    from round_tracks_board import ClassSetting, Settings

MIN_RADIUS_DEFAULT = 0.1
LEGACY_PASSES = 3


def parse_length(text, default):
    """A finite, non-negative number of mm from `text`, else `default`."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _checkbox_fields(text):
    for line in text.splitlines():
        params = line.split("\t")
        if len(params) == 3 and params[0] in ("True", "False"):
            return params
    return None


def parse_config(text):
    """Parse config text into Settings; malformed lines are skipped.

    A radius that isn't a finite, non-negative number is dropped so the default
    applies (for a class line, the dialog's default radius).
    """
    classes = {}
    min_radius = MIN_RADIUS_DEFAULT
    refill = False
    for line in text.splitlines():
        params = line.split("\t")
        if params[0] == "min_radius" and len(params) == 2:
            min_radius = parse_length(params[1], MIN_RADIUS_DEFAULT)
        elif params[0] == "refill_zones" and len(params) == 2:
            refill = params[1] == "True"
        elif len(params) >= 3 and params[1] in ("True", "False") and params[0]:
            radius = parse_length(params[2], None)
            if radius is not None:
                classes[params[0]] = ClassSetting(params[1] == "True", radius)
    boxes = _checkbox_fields(text)
    avoid = boxes is not None and boxes[2] == "True"
    return Settings(classes=classes, min_radius_mm=min_radius, avoid_junctions=avoid,
                    refill_zones=refill)


def read_new_file(text):
    """The "create a new file" checkbox (first field of the checkbox line)."""
    boxes = _checkbox_fields(text)
    return boxes is not None and boxes[0] == "True"


def format_config(settings, new_file):
    lines = ['%s\t%s\t%s\t%d' % (name, cs.do_round, cs.radius_mm, LEGACY_PASSES)
             for name, cs in settings.classes.items()]
    lines.append('%s\t%s\t%s' % (new_file, True, settings.avoid_junctions))
    lines.append('min_radius\t%s' % settings.min_radius_mm)
    lines.append('refill_zones\t%s' % settings.refill_zones)
    return '\n'.join(lines) + '\n'
