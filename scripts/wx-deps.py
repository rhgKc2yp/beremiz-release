#!/usr/bin/env python3
"""Stage the distribution wxPython dependency used by upstream's Linux setup."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request
from build_paths import (bundle_path, output_path, workspace,
                         reject_destination_links, validate_tree)

PACKAGES = {
    'universe': {'python3-wxgtk4.0', 'python3-wxgtk-webview4.0',
                 'libwxbase3.2-1t64', 'libwxgtk3.2-1t64',
                 'libwxgtk-gl3.2-1t64', 'libwxgtk-webview3.2-1t64'},
    'main': {'libjpeg-turbo8'},
}


ARCHES = ('x86_64', 'aarch64')
DEB_NAME = r'[A-Za-z0-9][A-Za-z0-9.+_~:-]*\.deb'


def cache_path(arch):
    if arch == 'x86_64':
        return workspace('ubuntu-wx', 'x86_64')
    if arch == 'aarch64':
        return workspace('ubuntu-wx', 'aarch64')
    raise ValueError('Unsupported Ubuntu architecture')


def package_path(cache, filename):
    if not isinstance(filename, str) or not re.fullmatch(DEB_NAME, filename):
        raise ValueError('Invalid Debian package filename')
    return output_path(cache, filename)


def pool_filename(value):
    # The remote Packages index may supply only a relative Ubuntu pool path.
    if not re.fullmatch(r'pool/(?:main|universe)/(?:[A-Za-z0-9][A-Za-z0-9.+_-]*/)+' + DEB_NAME, value):
        raise ValueError('Invalid Ubuntu pool path')
    return value.rsplit('/', 1)[1]


def check_package(data, record):
    if (len(data) != int(record['size']) or
            hashlib.sha256(data).hexdigest() != record['sha256']):
        raise SystemExit('Ubuntu package checksum mismatch: ' + record['package'])


def download(url):
    print(f'Downloading: {url}', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'beremiz-release/1.0'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def fetch(arch):
    distro_arch, base = {
        'x86_64': ('amd64', 'https://archive.ubuntu.com/ubuntu'),
        'aarch64': ('arm64', 'https://ports.ubuntu.com/ubuntu-ports'),
    }[arch]
    release = download(base + '/dists/resolute/Release').decode()
    checksums = {}
    section = ''
    for line in release.splitlines():
        if line and not line.startswith(' '):
            section = line.rstrip(':')
        elif section == 'SHA256':
            digest, size, filename = line.split()
            checksums[filename] = (digest, int(size))
    records = []
    cache = cache_path(arch)
    cache.mkdir(parents=True, exist_ok=True)
    for component, wanted in PACKAGES.items():
        index_path = f'{component}/binary-{distro_arch}/Packages.xz'
        data = download(f'{base}/dists/resolute/{index_path}')
        digest, size = checksums[index_path]
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit('Ubuntu package index checksum mismatch')
        found = {}
        for paragraph in lzma.decompress(data).decode().split('\n\n'):
            fields = dict(line.split(': ', 1) for line in paragraph.splitlines()
                          if line and not line.startswith(' ') and ': ' in line)
            if fields.get('Package') in wanted:
                found[fields['Package']] = fields
        if set(found) != wanted:
            raise SystemExit(f'Missing Ubuntu packages: {wanted - set(found)}')
        for name, fields in sorted(found.items()):
            filename = pool_filename(fields['Filename'])
            url = base + '/' + fields['Filename']
            package = package_path(cache, filename)
            record = {'size': int(fields['Size']), 'sha256': fields['SHA256'], 'package': name}
            if not package.exists():
                data = download(url)
                check_package(data, record)
                with package.open('xb') as stream:
                    stream.write(data)
            else:
                check_package(package.read_bytes(), record)
            if name.startswith('python3-wxgtk') and not fields['Version'].startswith('4.2.4+'):
                raise SystemExit('Unexpected wxPython version: ' + fields['Version'])
            records.append({'package': name, 'version': fields['Version'],
                            'architecture': distro_arch, 'url': url,
                            'sha256': fields['SHA256'], 'size': int(fields['Size']),
                            'source': fields.get('Source', name), 'file': filename})
    output_path(cache, 'packages.lock.json').write_text(json.dumps(records, indent=2) + '\n')
    print(f'Pinned Ubuntu 26.04 wxPython dependency for {arch}')


def install():
    arch = subprocess.check_output(['uname', '-m'], text=True).strip()
    cache = cache_path(arch)
    records = json.loads(output_path(cache, 'packages.lock.json').read_text())
    bundle = bundle_path()
    # Keep extraction separate from the payload and use a fresh private stage.
    with tempfile.TemporaryDirectory(prefix='ubuntu-wx-stage-', dir=workspace()) as directory:
        stage = Path(directory)
        for record in records:
            package = package_path(cache, record['file'])
            check_package(package.read_bytes(), record)
            with tempfile.TemporaryDirectory(prefix='deb-', dir=stage) as container_dir:
                container = Path(container_dir)
                subprocess.run(['bsdtar', '--no-same-owner', '-xf', str(package), '-C', str(container)], check=True)
                data, = container.glob('data.tar.*')
                if data.is_symlink() or not data.is_file():
                    raise ValueError('Invalid Debian data archive')
                subprocess.run(['bsdtar', '--no-same-owner', '-xf', str(data), '-C', str(stage)], check=True)
        validate_tree(stage)
        python = output_path(stage, 'usr/lib/python3/dist-packages')
        python_target = output_path(bundle, 'python')
        reject_destination_links(python_target)
        shutil.copytree(python, python_target, dirs_exist_ok=True, symlinks=True)
        for source in output_path(stage, 'usr/lib').rglob('*.so*'):
            if 'python3' in source.parts:
                continue
            target = output_path(bundle, 'lib', source.name)
            if source.is_symlink():
                link = source.readlink()
                if link.is_absolute() or not (target.parent / link).resolve().is_relative_to(target.parent):
                    raise ValueError(f'Unsafe relocated library link: {link}')
                target.symlink_to(link)
            elif source.is_file():
                shutil.copy2(source, target)
        notices = output_path(bundle, 'licenses/ubuntu')
        reject_destination_links(notices)
        shutil.copytree(output_path(stage, 'usr/share/doc'), notices, dirs_exist_ok=True, symlinks=True)
        validate_tree(python_target)
        validate_tree(notices)
        shutil.copy2(output_path(cache, 'packages.lock.json'),
                     output_path(bundle, 'UBUNTU-PACKAGES.json'))
    print('Staged unmodified distribution wxPython and its shared libraries')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fetch_parser = commands.add_parser('fetch')
    fetch_parser.add_argument('arch', choices=ARCHES)
    commands.add_parser('install')
    args = parser.parse_args()
    if args.command == 'fetch':
        fetch(args.arch)
    else:
        install()
