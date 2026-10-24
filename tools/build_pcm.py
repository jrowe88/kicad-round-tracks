# Builds the KiCad Plugin and Content Manager package and the custom
# repository files that let KiCad install this plugin straight from GitHub.
#
#   python tools/build_pcm.py 2.0.0
#
# Writes dist/kicad-round-tracks-<version>.zip (attach it to the GitHub
# release tagged v<version>) and refreshes pcm/repository.json,
# pcm/packages.json and pcm/resources.zip (commit those to master).
# Zip entries use a fixed timestamp so rebuilding the same sources gives the
# same checksum.

import hashlib
import json
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "jrowe88/kicad-round-tracks"
RAW = "https://raw.githubusercontent.com/%s/master/pcm/" % REPO
RELEASE = "https://github.com/%s/releases/download/v{v}/kicad-round-tracks-{v}.zip" % REPO
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
PLUGIN_EXTRAS = ("round_tracks.png", "round_tracks_dark.png")


def add(zf, arcname, data):
    info = zipfile.ZipInfo(arcname, FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def sha256(path):
    return hashlib.sha256(read(path)).hexdigest()


def resource_ref(name, now):
    path = os.path.join(ROOT, "pcm", name)
    return {
        "url": RAW + name,
        "sha256": sha256(path),
        "update_timestamp": int(now),
        "update_time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
    }


def main(version):
    with open(os.path.join(ROOT, "metadata.json.template"), encoding="utf-8") as f:
        meta = json.loads(f.read().replace("VERSION", version))

    # package archive: plugins/, resources/icon.png, metadata.json (no download_* keys)
    files = sorted(n for n in os.listdir(ROOT) if n.endswith(".py")) + list(PLUGIN_EXTRAS)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    pkg = os.path.join(ROOT, "dist", "kicad-round-tracks-%s.zip" % version)
    install_size = 0
    with zipfile.ZipFile(pkg, "w") as zf:
        for name in files:
            data = read(os.path.join(ROOT, name))
            add(zf, "plugins/" + name, data)
            install_size += len(data)
        icon = read(os.path.join(ROOT, "icon.png"))
        add(zf, "resources/icon.png", icon)
        meta_bytes = (json.dumps(meta, indent=4) + "\n").encode()
        add(zf, "metadata.json", meta_bytes)
        install_size += len(icon) + len(meta_bytes)

    entry = dict(meta["versions"][0])
    entry.update({
        "download_url": RELEASE.format(v=version),
        "download_sha256": sha256(pkg),
        "download_size": os.path.getsize(pkg),
        "install_size": install_size,
    })
    listed = dict(meta)
    listed.pop("$schema", None)
    listed["versions"] = [entry]

    pcm = os.path.join(ROOT, "pcm")
    os.makedirs(pcm, exist_ok=True)
    with open(os.path.join(pcm, "packages.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"packages": [listed]}, f, indent=4)
        f.write("\n")
    with zipfile.ZipFile(os.path.join(pcm, "resources.zip"), "w") as zf:
        add(zf, meta["identifier"] + "/icon.png", icon)

    now = time.time()
    repo = {
        "$schema": "https://go.kicad.org/pcm/schemas/v1#/definitions/Repository",
        "name": "Round Tracks (Adaptive) by jrowe88",
        "maintainer": meta["maintainer"],
        "packages": resource_ref("packages.json", now),
        "resources": resource_ref("resources.zip", now),
    }
    with open(os.path.join(pcm, "repository.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(repo, f, indent=4)
        f.write("\n")

    print("package: %s (%d bytes, sha256 %s)" % (pkg, entry["download_size"], entry["download_sha256"]))
    print("repository: %srepository.json" % RAW)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tools/build_pcm.py <version>")
    main(sys.argv[1])
