# Build and release

Install Flatpak, GNU tar, Python 3 and the Flathub remote, then install
`org.gnome.Sdk//51` and `org.gnome.Platform//51`.

```sh
python3 scripts/resolve.py --tag v1.6.0 --revision 1
bash scripts/run-build.sh
```

The resolver uses the official stable Beremiz tag and the exact dependency
revisions from the recorded upstream distribution recipe, plus BACnet-stack
0.8.7 pinned to commit `0af5a273304a4ecb5215c7e3fe473f527dcf0079`.
This legacy version matches Beremiz's flat header layout and server API;
upgrading to the current stack requires a separate compatibility migration.
All downloaded application source archives have SHA-256 entries in
`SOURCES.lock.json`.
Upstream's Python dependency lock is used, with wxPython 4.2.4 supplied by the
official Ubuntu 26.04 packages, matching upstream's documented distribution
dependency setup and the GNOME SDK's Python 3.14. Other Python wheels are built
in the SDK. Ubuntu package indexes are checked against the release metadata;
every downloaded package has its size and SHA-256 checked and recorded in
`UBUNTU-PACKAGES.json`. The build applies the patches in `scripts/patches/`
to a copy of Beremiz, preserving the original source archive. These fix
Python 3 pickle file modes, text conversion at the BACnet runtime's ctypes
boundary, CLI initialization of object tables, and wxPython cell editor
parameter setup. Applied patches are also
included in the payload's `PATCHES/` directory and the source archive.

GCC, binutils, C headers and libc development files come from the recorded
SDK commit. Runtime validation uses the Platform, with networking disabled,
so it cannot accidentally use the SDK compiler. The extracted release archive
is tested a second time at a different path. The test compiles the upstream
IEC language project, transfers it to the local runtime, starts it and requires
its `ALL TESTS OK` result. It also builds and transfers the upstream BACnet
example, runs it on loopback with a temporary UDP port, and checks BACnet
ReadProperty and WriteProperty requests using an independent UDP client.
BACnet checks also cover object-table load/save/reload and runtime configuration
text/JSON round trips against the generated native library.
Both checks run before packaging and after extraction at a different path.
They do not verify physical hardware or BACnet interoperability on a LAN.

With a desktop display available, also check the BACnet object editors:

```sh
flatpak run --unshare=network --share=ipc --socket=wayland --socket=fallback-x11 \
  --filesystem="$PWD" --command="$PWD/build/beremiz/bin/beremiz-python" \
  org.gnome.Platform//51 "$PWD/scripts/verify-bacnet-gui.py"
```

This creates all nine editor grids with real wxGTK and saves a temporary
project. It keeps the window hidden and does not modify user projects.

The default workflow builds on pushes and pull requests without publishing.
Scheduled runs follow new stable upstream releases. A manual run with `publish`
enabled publishes after both native x86_64 and aarch64 builds pass. Existing
release assets are never replaced; increment `revision` for a rebuild.
Only the final release job has repository write permissions.

To use BACnet, add a BACnet node to the PLC project, configure its objects
and map the located variables. Set `Network_Interface` to an interface that
exists on the runtime machine (the upstream example uses `virbr0`). The usual
BACnet/IP UDP port is 47808. The Flatpak needs network sharing, which the
companion manifest already grants. Loopback verification works with external
networking disabled. Generated PLC binaries embed the BACnet stack and require
a runtime with a matching architecture and compatible ABI.

The application source archives, build locks and installed Python package list
accompany the payload. Source URLs and exact commits for Python VCS packages
are in the requirements file. GCC/binutils and runtime sources are those of
the recorded GNOME/Freedesktop SDK commit; retain their licence notices and
corresponding sources when redistributing the toolchain.

After publishing, run FlatPark's resolver and `scripts/update-pins.mjs` against
the real release assets. Never submit a manifest with placeholder hashes or
an unavailable release URL. Test the installed Flatpak, including its GUI,
settings persistence and PLC compile/run workflow, before opening a PR.

## Security regression tests and script arguments

Run `python3 -m unittest discover -s tests -v` without downloading dependencies.
The scripts use the checkout containing them as their workspace. Run builds
as an unprivileged user in a private checkout (no concurrent untrusted writers).
The build payload is always `build/beremiz`; the extracted verification payload
is always `build/archive-test/beremiz`. Arbitrary filesystem paths are no longer
accepted by the helper scripts. The updated build driver uses these commands:

```sh
python3 scripts/wx-deps.py fetch x86_64   # or aarch64
python3 scripts/wx-deps.py install
python3 scripts/toolchain.py
python3 scripts/check-layout.py
python3 scripts/source-index.py
bash scripts/verify.sh build
bash scripts/verify.sh archive
```

BACnet verification and its optional GUI check use the build payload by default;
pass `--archive` to use the extracted payload. The internal `--project-data`
mode accepts no path and requires a private generated test working directory.
The resolver permits stable version tags and 40-character commit IDs only,
uses explicitly approved HTTPS GitHub endpoints, and refuses HTTP redirects.
These controls do not establish the authenticity of Ubuntu Release metadata or
Python dependency distributions; those are separate supply-chain concerns.
