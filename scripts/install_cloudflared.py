#!/usr/bin/env python3
"""Download an official cloudflared release and verify its GitHub SHA-256 digest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def asset_name(system=None, machine=None):
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64', 'amd64': 'amd64'}.get(machine)
    if system not in ('darwin', 'linux') or not arch:
        raise ValueError('Supported platforms: macOS/Linux on arm64 or x86_64')
    return f'cloudflared-{system}-{arch}' + ('.tgz' if system == 'darwin' else '')


def open_url(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        'User-Agent': 'grokbot-video-mcp-installer', 'Accept': 'application/vnd.github+json'}), timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='latest', help='Official release tag, or latest')
    parser.add_argument('--archive', type=Path, help='Reuse a local matching release file; checksum is still verified')
    args = parser.parse_args()
    suffix = 'latest' if args.version == 'latest' else 'tags/' + urllib.parse.quote(args.version, safe='')
    with open_url('https://api.github.com/repos/cloudflare/cloudflared/releases/' + suffix) as response:
        release = json.load(response)
    name = asset_name()
    asset = next((a for a in release['assets'] if a['name'] == name), None)
    if not asset or not str(asset.get('digest', '')).startswith('sha256:'):
        raise RuntimeError('Official asset with a verifiable SHA-256 digest was not found')
    destination = ROOT / 'bin' / 'cloudflared'
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cloudflared-install-') as temp:
        downloaded = Path(temp) / name
        if args.archive:
            shutil.copyfile(args.archive, downloaded)
        else:
            with open_url(asset['browser_download_url']) as response, downloaded.open('wb') as output:
                shutil.copyfileobj(response, output)
        digest = 'sha256:' + hashlib.sha256(downloaded.read_bytes()).hexdigest()
        if digest != asset['digest']:
            raise RuntimeError('SHA-256 mismatch; executable was not installed')
        staged = destination.with_suffix('.new')
        if name.endswith('.tgz'):
            with tarfile.open(downloaded) as archive:
                member = next(m for m in archive.getmembers() if m.isfile() and Path(m.name).name == 'cloudflared')
                # Read only the expected executable; never extract arbitrary archive paths.
                with archive.extractfile(member) as source, staged.open('wb') as output:
                    shutil.copyfileobj(source, output)
        else:
            shutil.copyfile(downloaded, staged)
        os.chmod(staged, 0o755)
        staged.replace(destination)
    print(f'Installed verified cloudflared {release["tag_name"]}: {destination}')


if __name__ == '__main__':
    main()
