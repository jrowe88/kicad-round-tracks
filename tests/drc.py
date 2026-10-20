# Run KiCad's DRC through kicad-cli (pcbnew.WriteDRCReport crashes headless).
# Licensed under the Apache License 2.0, like the rest of this plugin.

import json
import os
import subprocess
import tempfile

KICAD_CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"

# Violation types confirmed with kicad-cli 10.0.7 against deliberate violations:
# track-track gap, track over an NPTH hole, track at the board edge, track
# through a keepout rule area.
CLEARANCE_TYPES = frozenset({
    'clearance',
    'hole_clearance',
    'copper_edge_clearance',
    'items_not_allowed',
})


def run(path, timeout=300):
    """Run DRC on the board file at `path`; return the parsed JSON report."""
    out_dir = tempfile.mkdtemp()
    out = os.path.join(out_dir, 'drc.json')
    subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json', '--severity-all',
                    '-o', out, path], capture_output=True, check=True, timeout=timeout)
    with open(out) as f:
        return json.load(f)


def clearance_violations(report):
    return [v for v in report['violations'] if v['type'] in CLEARANCE_TYPES]


def connectivity(report):
    """Dangling-item violation types, plus one 'unconnected' per unconnected pair."""
    return sorted([v['type'] for v in report['violations'] if 'dangling' in v['type']]
                  + ['unconnected'] * len(report.get('unconnected_items', [])))
