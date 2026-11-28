#!/usr/bin/env python3
"""Check required entry points, ELF runpaths, and payload symlinks."""
from build_paths import bundle_path, validate_tree
import argparse
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--archive', action='store_true')
parser.add_argument('--links-only', action='store_true', help='skip SDK-only readelf checks')
args = parser.parse_args()
root = bundle_path(args.archive)
validate_tree(root)
for name in ("bin/beremiz", "bin/beremiz-cli", "bin/beremiz-python", "matiec/iec2c",
             "matiec/lib/ieclib.txt", "toolchain/bin/gcc", "beremiz/Beremiz.py",
             "BACnet/include/bacdef.h", "BACnet/ports/linux/bip-init.c",
             "BACnet/lib/libbacnet.a", "PATCHES/0001-bacnet-python3.patch",
             "PATCHES/0002-bacnet-wx-editor.patch"):
    if not (root / name).is_file():
        raise SystemExit("Missing payload entry: " + name)
if args.links_only:
    print("Payload entries and symlinks verified")
    raise SystemExit(0)
for path in root.rglob("*"):
    if not path.is_file():
        continue
    with path.open("rb") as stream:
        elf = stream.read(4) == b"\x7fELF"
    if elf:
        dynamic = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
        for line in dynamic.splitlines():
            if ("RPATH" in line or "RUNPATH" in line) and str(root.parent) in line:
                raise SystemExit("Build path in ELF: " + str(path))
print("Payload layout verified")
