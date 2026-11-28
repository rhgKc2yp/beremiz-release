#!/usr/bin/env python3
"""Bundle SDK C compiler and a sysroot; never use a host compiler at runtime."""
from pathlib import Path
import os
import shutil
import subprocess
import re
from build_paths import bundle_path, fixed_bundle_args, output_path, reject_destination_links

fixed_bundle_args(__doc__)
root = bundle_path()
toolchain = output_path(root, "toolchain")
sysroot = output_path(root, "sysroot")
# These trees are created by this invocation, not merged from external input.
toolchain.mkdir()
sysroot.mkdir()


def copy(source, destination):
    destination = output_path(root, destination.relative_to(root))
    if source.is_dir():
        reject_destination_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=True,
                        ignore=lambda directory, names: [name for name in names
                            if (Path(directory) / name).is_symlink()
                            and not (Path(directory) / name).exists()])
    else:
        shutil.copy2(source.resolve(), destination)


triplet = subprocess.check_output(["gcc", "-dumpmachine"], text=True).strip()
version = subprocess.check_output(["gcc", "-dumpversion"], text=True).strip()
if not re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+", triplet) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
    raise SystemExit("Unexpected GCC target or version")
gccdir = Path(subprocess.check_output(["gcc", "-print-libgcc-file-name"], text=True).strip()).parent
copy(gccdir, toolchain / "lib/gcc" / triplet / version)
for name in ("cc1", "collect2", "lto-wrapper", "lto1"):
    source = Path(subprocess.check_output(["gcc", "-print-prog-name=" + name], text=True).strip())
    if source.is_file():
        copy(source, toolchain / "lib/gcc" / triplet / version / name)
plugin = Path(subprocess.check_output(["gcc", "-print-prog-name=liblto_plugin.so"], text=True).strip())
copy(plugin, toolchain / "lib/gcc" / triplet / version / plugin.name)
for name in ("gcc", "cpp", "as", "ld", "ar", "ranlib", "nm", "strip"):
    source = Path(shutil.which(name))
    target = toolchain / "bin" / (name + ".real" if name in ("gcc", "cpp") else name)
    copy(source, target)
    if name in ("gcc", "cpp"):
        wrapper = output_path(root, target.with_name(name).relative_to(root))
        wrapper.write_text('#!/bin/sh\nset -eu\ntoolchain=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                           f'exec "$toolchain/bin/{name}.real" -B"$toolchain/lib/gcc/{triplet}/{version}/" '
                           '-B"$toolchain/bin/" --sysroot="$toolchain/../sysroot" "$@"\n')
        wrapper.chmod(0o755)
copy(Path("/usr/include"), sysroot / "usr/include")
libcdir = Path(os.path.normpath(subprocess.check_output(
    ["gcc", "-print-file-name=libc.so"], text=True).strip())).parent
if not libcdir.is_absolute() or not libcdir.is_relative_to(Path("/usr")):
    raise SystemExit("GCC libc directory must be inside the SDK /usr")
for pattern in ("crt*.o", "*crt*.o", "libc.so*", "libc.a", "libc_nonshared.a",
                "libm.so*", "libm.a", "libm-*.a", "libmvec*", "libpthread*",
                "librt*", "libdl*", "libutil*", "ld-linux*", "libresolv*",
                "libgcc_s*", "libatomic*"):
    for source in libcdir.glob(pattern):
        copy(source, sysroot / source.relative_to("/"))
for link in ("lib",):
    if not (sysroot / link).is_symlink():
        (sysroot / link).symlink_to("usr/lib")
if (sysroot / "lib64").is_symlink():
    (sysroot / "lib64").unlink()
if Path("/usr/lib64").is_dir():
    copy(Path("/usr/lib64"), sysroot / "lib64")
for loader in Path("/usr/lib").glob("ld-linux*"):
    if loader.exists():
        copy(loader, sysroot / "usr/lib" / loader.name)
library_dir = output_path(root, "lib")
library_dir.mkdir(exist_ok=True)
copy(Path("/usr/manifest.json"), root / "SDK-MANIFEST.json")
for component in ("gcc", "binutils", "glibc", "linux-headers", "gmp", "mpfr", "mpc", "isl", "zstd"):
    notices = Path("/usr/share/licenses/freedesktop-sdk") / component
    if notices.exists():
        copy(notices, root / "licenses" / component)
for source in toolchain.rglob("*"):
    if not source.is_file():
        continue
    with source.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            continue
    output = subprocess.run(["ldd", str(source)], capture_output=True, text=True).stdout
    for line in output.splitlines():
        if "=> /usr/" in line:
            dependency = Path(line.split("=>", 1)[1].split()[0])
            if dependency.name.startswith(("libgmp", "libmpfr", "libmpc", "libzstd", "libisl", "libbfd", "libsframe", "libopcodes", "libctf")):
                copy(dependency, library_dir / dependency.name)
print(f"Bundled GCC {version} ({triplet})")
