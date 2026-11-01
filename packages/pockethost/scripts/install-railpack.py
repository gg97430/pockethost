#!/usr/bin/env python3
"""Install operator-supplied Linux amd64 binaries using the worker's pinned hashes.

No download, binary execution, service restart or secret generation. Supply the
uncompressed release binaries; archives are deliberately not accepted.
"""
import argparse
import hashlib
import os
import pathlib
import platform
import stat
import tempfile

from web_app_railpack import RAILPACK


def verified_bytes(source, digest):
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('A regular binary file is required.')
        data = stream.read(256 * 1024**2 + 1)
    if len(data) > 256 * 1024**2 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Binary SHA-256 differs from the release pin; no files installed.')
    return data


def trusted_directory(directory):
    # Walk from /; never create through an untrusted/symlinked parent.
    current = pathlib.Path('/')
    for part in directory.parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o755)
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Installation directory must be root-owned, without writable parents or symlinks.')


def install(destination, data):
    destination = pathlib.Path(destination)
    trusted_directory(destination.parent)
    if destination.is_symlink():
        raise ValueError('Refusing a symlink destination.')
    if destination.exists():
        info = destination.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o755 and destination.read_bytes() == data:
            return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = pathlib.Path(stream.name)
            os.fchmod(stream.fileno(), 0o755)
            os.fchown(stream.fileno(), 0, 0)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--railpack', required=True, type=pathlib.Path)
    parser.add_argument('--mise', required=True, type=pathlib.Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        parser.error('Run as root on Linux amd64; pins are architecture-specific.')
    # Validate BOTH inputs before any installation changes.
    railpack = verified_bytes(args.railpack, RAILPACK['sha256'])
    mise = verified_bytes(args.mise, RAILPACK['miseSha256'])
    install(RAILPACK['binary'], railpack)
    install(RAILPACK['miseBinary'], mise)
    print('Pinned Railpack and mise installed. No service was restarted.')


if __name__ == '__main__':
    main()
