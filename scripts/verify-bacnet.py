#!/usr/bin/env python3
"""Build, transfer and probe the upstream BACnet example over isolated loopback."""
from pathlib import Path
from build_paths import (bundle_path, bacnet_work_base, bacnet_child_project,
                         output_path, validate_tree)
import argparse
import ctypes
import gettext
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET


def verify_project_data(bundle, project):
    # Exercise actual object-table persistence, including Python 2-era examples.
    sys.path.insert(0, str(bundle / "beremiz"))
    import fake_wx  # noqa: F401 -- CLI's headless wx implementation
    from CLIController import CLIController

    controller = CLIController(SimpleNamespace(project_home=str(project), buildpath=None))
    assert not controller.check_and_load_project()
    node = controller.GetChildByName("bacnet_0")
    assert node is not None, "BACnet node failed to load"
    table = node.ObjTablesData["AO_Obj"]
    assert table[0]["Object Name"] == "TemperatureSetPoint"
    table[0]["Description"] = "BACnet round trip: 23.5 °C"
    assert node.SaveToFile(node.GetFileName()) is True
    table[0]["Description"] = "unsaved"
    assert node.LoadFromFile(node.GetFileName()) is True
    assert node.ObjTablesData["AO_Obj"][0]["Description"] == "BACnet round trip: 23.5 °C"
    print("BACnet project load/save/reload passed")


def verify_settings(bundle, project, work):
    sys.path.insert(0, str(bundle / "beremiz"))
    gettext.install("beremiz")
    template = project / "build/runtime_bacnet_websettings.py"
    settings = {"WorkingDir": str(work), "_": lambda message: message}
    exec(compile(template.read_bytes(), str(template), "exec"), settings)
    library = ctypes.CDLL(str(project / "build/project.so"))
    for name, label, ctype, webtype in settings["BACnet_parameters"]:
        getter = getattr(library, "__bacnet_0_get_ConfigParam_" + name)
        getter.restype, getter.argtypes = ctype, []
        setter = getattr(library, "__bacnet_0_set_ConfigParam_" + name)
        setter.restype, setter.argtypes = None, [ctype]
        settings["BacnetGetParamFuncs"][name] = getter
        settings["BacnetSetParamFuncs"][name] = setter
    config = settings["_GetBacnetPLCConfiguration"]()
    assert isinstance(config["device_name"], str)
    config["device_name"] = "BACnet test: 23.5 °C"
    settings["_SetBacnetPLCConfiguration"](config)
    assert settings["_GetBacnetPLCConfiguration"]() == config
    settings["_SetBacnetSavedConfiguration"](config)
    saved = settings["_GetBacnetSavedConfiguration"]()
    assert saved == config
    settings["_SetBacnetPLCConfiguration"](saved)
    assert settings["_GetBacnetPLCConfiguration"]() == config
    print("BACnet runtime configuration text/JSON round trip passed")


def verify(bundle, work, archive=False):
    project = work / "project"
    example = output_path(bundle, "beremiz/tests/projects/BACnet")
    validate_tree(example)
    shutil.copytree(example, project)
    # Use a free UDP port, without requiring a LAN interface or physical device.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = project / "bacnet_0@bacnet/confnode.xml"
    tree = ET.parse(config)
    tree.getroot().set("Network_Interface", "lo")
    tree.getroot().set("UDP_Port_Number", str(port))
    tree.write(config, encoding="utf-8", xml_declaration=True)
    interpreter = output_path(bundle, "bin/beremiz-python")
    cli = output_path(bundle, "bin/beremiz-cli")
    args = [str(interpreter), str(Path(__file__).resolve()), "--project-data"]
    if archive:
        args.append("--archive")
    subprocess.run(args, cwd=work, check=True, timeout=30)
    command = [str(cli), "-k", "--project-home",
               str(project), "build", "transfer", "run"]
    logpath = work / "runtime.log"
    with logpath.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.bind(("127.0.0.1", 0))
                client.settimeout(0.5)

                def request(apdu):
                    # BACnet/IP Original-Unicast-NPDU, local NPDU expecting reply.
                    packet = b"\x01\x04" + apdu
                    client.sendto(b"\x81\x0a" + struct.pack(">H", len(packet) + 4)
                                  + packet, ("127.0.0.1", port))
                    while True:
                        reply, peer = client.recvfrom(2048)
                        if peer != ("127.0.0.1", port):
                            continue
                        if (reply[:2] != b"\x81\x0a" or
                                struct.unpack(">H", reply[2:4])[0] != len(reply) or
                                reply[4:6] != b"\x01\x00"):
                            raise AssertionError(f"Unexpected BACnet framing: {reply.hex()}")
                        return reply[6:]

                # ReadProperty: device 3, object-identifier (75). Retry startup only.
                device = b"\x0c" + struct.pack(">I", (8 << 22) | 3) + b"\x19\x4b"
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("BACnet PLC exited before responding")
                    try:
                        reply = request(b"\x00\x05\x01\x0c" + device)
                        break
                    except socket.timeout:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("BACnet PLC did not respond within 90 seconds")
                expected = b"\x30\x01\x0c" + device + b"\x3e\xc4" + struct.pack(">I", (8 << 22) | 3) + b"\x3f"
                assert reply == expected, f"Device ReadProperty failed: {reply.hex()}"
                client.settimeout(2)

                # Confirm that the example's object table loaded, rather than an
                # empty BACnet node being built after a swallowed load error.
                name = b"\x0c" + struct.pack(">I", 1 << 22) + b"\x19\x4d"
                reply = request(b"\x00\x05\x02\x0c" + name)
                assert reply.startswith(b"\x30\x02\x0c" + name + b"\x3e"), reply.hex()
                assert b"TemperatureSetPoint" in reply, reply.hex()

                # WriteProperty / ReadProperty: analog-output 0, present-value
                # (85), priority 16. The PLC example uses this as its setpoint.
                value = b"\x0c" + struct.pack(">I", 1 << 22) + b"\x19\x55"
                real = b"\x44" + struct.pack(">f", 23.5)
                reply = request(b"\x00\x05\x03\x0f" + value + b"\x3e" + real + b"\x3f\x49\x10")
                assert reply == b"\x20\x03\x0f", f"WriteProperty failed: {reply.hex()}"
                reply = request(b"\x00\x05\x04\x0c" + value)
                assert reply == b"\x30\x04\x0c" + value + b"\x3e" + real + b"\x3f", reply.hex()
                assert (project / "build/server_0.c").is_file()
                verify_settings(bundle, project, work)
                print("BACnet PLC compile, transfer, ReadProperty and WriteProperty passed")
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            print(logpath.read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--project-data", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    bundle = bundle_path(args.archive)
    validate_tree(bundle)
    if args.project_data:
        verify_project_data(bundle, bacnet_child_project())
    else:
        with tempfile.TemporaryDirectory(prefix="run-", dir=bacnet_work_base()) as directory:
            verify(bundle, Path(directory), args.archive)
