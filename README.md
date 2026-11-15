# Beremiz community Linux releases

Builds [Beremiz](https://beremiz.org/) sources into a Linux archive
for FlatPark. This repository is independent of the Beremiz project. Upstream
publishes Linux Snap packages; this build does not unpack or depend on Snap.

The archive contains Beremiz, MatIEC, Modbus, CANopen, BACnet/IP, open62541, Python
dependencies and a GCC toolchain. It targets `org.gnome.Platform//51`, rather
than arbitrary host distributions. Xenomai and Inkscape/SVG HMI tooling
are not included. BACnet support exposes PLC variables as a BACnet/IP
device/server; it does not add BACnet client, MS/TP or BACnet/SC support.
Small Python 3 and wxPython compatibility patches are included for BACnet
project storage, runtime configuration, CLI object tables and the GUI editor.
wxPython is staged from official Ubuntu 26.04 packages, as in
upstream's documented Linux dependency setup. See [the build instructions](docs/build.md).
