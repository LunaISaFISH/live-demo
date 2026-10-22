"""Restore the packaged PMData from the GitHub Release (or a local ZIP)."""
import argparse
import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from download_pmdata import DATA, ROOT, ready

ASSET = 'FitCoach-2026-10-22-with-data.zip'
URL = 'https://github.com/LunaISaFISH/live-demo/releases/download/data-2026-10-22/' + ASSET
SHA256 = '116cdfece6c764a7191a6b2f00e57d755d3f86199b4bf1f02e0bb24ff50a8800'
PREFIX = 'FitCoach/data/pmdata/'


def restore(archive_path: Path, destination: Path, expected_hash: str = SHA256) -> None:
    with archive_path.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != expected_hash:
        raise ValueError('SHA-256 mismatch; use the original data-inclusive release ZIP.')
    with zipfile.ZipFile(archive_path) as archive:
        selected = []
        for member in archive.infolist():
            if not member.filename.startswith(PREFIX):
                continue
            name = member.filename[len(PREFIX):]
            if not name:
                continue
            path = PurePosixPath(name)
            target = destination / name
            if (path.is_absolute() or PureWindowsPath(name).drive or '\\' in name
                    or '..' in path.parts
                    or not target.resolve().is_relative_to(destination.resolve())):
                raise ValueError(f'Unsafe dataset path: {name}')
            selected.append((member, target))
        if not selected:
            raise ValueError('No packaged dataset found in ZIP.')
        for member, target in selected:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='use an already downloaded original ZIP')
    args = parser.parse_args()
    if ready():
        print(f'PMData is ready at {DATA}')
        return
    archive = args.archive or ROOT / 'data' / ASSET
    if not archive.exists() and args.archive:
        parser.error(f'Archive does not exist: {archive}')
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        partial = archive.with_suffix('.zip.part')
        print(f'Downloading 146 MB data release: {URL}', flush=True)
        with urllib.request.urlopen(URL, timeout=60) as response, partial.open('wb') as output:
            shutil.copyfileobj(response, output)
        with partial.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != SHA256:
                partial.unlink()
                raise ValueError('SHA-256 mismatch; download interrupted or corrupt.')
        partial.replace(archive)
    restore(archive, DATA)
    if not ready():
        raise RuntimeError('Data verification failed after extraction.')
    print('All 16 participants and sensor indexes are ready.')


if __name__ == '__main__':
    main()
