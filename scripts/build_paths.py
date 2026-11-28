"""Filesystem boundaries for the trusted, private release-build workspace.

These helpers do not make a workspace safe against a concurrent writer. Build
and verification must run as an unprivileged user in a private checkout.
"""
import argparse
import os
from pathlib import Path
import re
import stat

REPO = Path(__file__).resolve().parent.parent


def output_path(root, *parts):
    """Return a descendant, rejecting symlinks in existing path components."""
    path = root
    for part in parts:
        part = Path(part)
        if part.is_absolute() or not part.parts or '..' in part.parts:
            raise ValueError(f'Unsafe relative path: {part}')
        for component in part.parts:
            path = path / component
            if path.is_symlink():
                raise ValueError(f'Symlink in destination path: {path}')
            if path.exists() and not (path.is_dir() or path.is_file()):
                raise ValueError(f'Special file in workspace path: {path}')
    return path


def workspace(*parts):
    return output_path(REPO, 'build', *parts)


def bundle_path(archive=False):
    path = (workspace('archive-test', 'beremiz') if archive
            else workspace('beremiz'))
    if not path.is_dir():
        raise ValueError(f'Expected build payload directory: {path}')
    return path


def fixed_bundle_args(description, *, allow_archive=False):
    parser = argparse.ArgumentParser(description=description)
    if allow_archive:
        parser.add_argument('--archive', action='store_true',
                            help='use build/archive-test/beremiz instead of build/beremiz')
    return parser.parse_args()


def validate_tree(root):
    """Allow only ordinary files/directories and internal, relative symlinks."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f'Expected real directory: {root}')
    for path in root.rglob('*'):
        if path.is_symlink():
            target = path.readlink()
            if target.is_absolute() or not path.resolve().is_relative_to(root):
                raise ValueError(f'External symlink: {path}: {target}')
        elif not (path.is_dir() or path.is_file()):
            raise ValueError(f'Unsupported special file: {path}')


def reject_destination_links(root):
    """Reject existing symlinks/special files before merging into a tree."""
    if root.is_symlink():
        raise ValueError(f'Symlink destination: {root}')
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError(f'Expected directory: {root}')
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f'Unsafe merge destination: {path}')


def bacnet_work_base():
    base = workspace('bacnet-work')
    base.mkdir(mode=0o700, exist_ok=True)
    info = base.stat()
    if info.st_uid != os.getuid() or (stat.S_IMODE(info.st_mode) & 0o777) != 0o700:
        raise ValueError(f'BACnet work directory must be private: {base}')
    return base


def bacnet_child_project():
    """Internal subprocess accepts no project path; require a private work dir."""
    work = Path.cwd()
    if work.parent != bacnet_work_base() or not re.fullmatch(r'run-[a-z0-9_]+', work.name):
        raise ValueError('Project-data check requires a generated BACnet work directory')
    info = work.stat()
    if info.st_uid != os.getuid() or (stat.S_IMODE(info.st_mode) & 0o777) != 0o700:
        raise ValueError('BACnet work directory must be private')
    project = output_path(work, 'project')
    validate_tree(project)
    return project


if __name__ == '__main__':
    # Used before shell build scripts create or remove the fixed directories.
    fixed_bundle_args(__doc__)
    workspace('beremiz')
    workspace('archive-test', 'beremiz')
