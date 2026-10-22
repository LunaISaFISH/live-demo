#!/usr/bin/env python3
"""Download and extract the PMData files used by FitCoach."""

from __future__ import annotations

import argparse
import http.client
import json
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "pmdata"
ARCHIVE = ROOT / "data" / "pmdata.zip"
URL = "https://datasets.simula.no/downloads/pmdata.zip"
PAGE = "https://datasets.simula.no/pmdata/"


def ready() -> bool:
    try:
        files = json.loads((DATA / ".fitcoach-ready.json").read_text(encoding="utf-8"))
        return (bool(files) and (DATA / "participant-overview.xlsx").is_file()
                and all((DATA / f"p{i:02d}").is_dir() for i in range(1, 17))
                and all((DATA / name).is_file() and (DATA / name).stat().st_size == size
                        for name, size in files.items()))
    except (OSError, ValueError, AttributeError):
        return False


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # The dataset host omits this intermediate. Still require a system-trusted root.
    context.load_verify_locations(ROOT / "certificates" / "sectigo-dv-r36.pem")
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


def check_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise zipfile.BadZipFile(f"damaged member: {bad}")


def download() -> None:
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    partial = ARCHIVE.with_suffix(".zip.part")
    for attempt in range(3):
        copied = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "FitCoach/1.0", "Accept-Encoding": "identity"}
        if copied:
            headers["Range"] = f"bytes={copied}-"
        request = urllib.request.Request(URL, headers=headers)
        started, initial = time.monotonic(), copied
        try:
            with urllib.request.urlopen(request, context=tls_context(), timeout=60) as response:
                if response.status == 206:
                    span = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                        response.headers.get("Content-Range", ""))
                    if not span or int(span[1]) != copied:
                        raise OSError("server returned an invalid resume range")
                    total = int(span[3])
                else:
                    copied = initial = 0  # A server without Range support sends the whole file.
                    total = int(response.headers.get("Content-Length", 0))
                with partial.open("ab" if copied else "wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        copied += len(chunk)
                        speed = (copied - initial) / max(time.monotonic() - started, 0.001) / 1024**2
                        percent = f"{copied / total:.0%}" if total else ""
                        print(f"\rDownloading PMData: {percent} {copied / 1024**2:.1f} MiB "
                              f"({speed:.1f} MiB/s)", end="", flush=True)
                print()
                if total and copied != total:
                    raise OSError(f"incomplete download: {copied} of {total} bytes")
            check_archive(partial)
            partial.replace(ARCHIVE)
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 416 or not partial.exists():
                raise
            # A previous attempt may have finished just before it was interrupted.
            try:
                check_archive(partial)
            except zipfile.BadZipFile:
                partial.unlink()
            else:
                partial.replace(ARCHIVE)
                return
        except zipfile.BadZipFile:
            partial.unlink(missing_ok=True)
            raise RuntimeError("The downloaded ZIP is corrupt. Run the download again.") from None
        except (OSError, http.client.HTTPException) as exc:
            if isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError):
                raise RuntimeError("HTTPS verification failed. Check your system CA certificates "
                                   "or corporate proxy CA (SSL_CERT_FILE); verification is required.") from exc
            if attempt == 2:
                raise RuntimeError("Download interrupted. Run this command again to resume "
                                   "the saved .zip.part file.") from exc
            print(f"\nDownload interrupted; retrying ({attempt + 2}/3).", flush=True)
            time.sleep(1)
    raise RuntimeError("Could not complete the download; run again to retry.")


def extract() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    marker = DATA / ".fitcoach-ready.json"
    marker.unlink(missing_ok=True)
    with zipfile.ZipFile(ARCHIVE) as archive:
        members = []
        for item in archive.infolist():
            parts = PurePosixPath(item.filename).parts
            if (PurePosixPath(item.filename).is_absolute() or PureWindowsPath(item.filename).drive
                    or "\\" in item.filename or ".." in parts):
                raise RuntimeError(f"unsafe archive path: {item.filename}")
            if "food-images" not in parts:
                members.append(item)
        archive.extractall(DATA, members)
    subprocess.run(
        [sys.executable, str(ROOT / "prototype" / "realtime.py"), "--index-all"],
        check=True,
    )
    files = {item.filename: item.file_size for item in members if not item.is_dir()}
    files.update({p.relative_to(DATA).as_posix(): p.stat().st_size
                  for p in DATA.glob("p??/fitbit/*.day-index.json")})
    marker.write_text(json.dumps(files, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download the 1.4 GB PMData archive and prepare FitCoach data."
    )
    parser.add_argument("--check", action="store_true", help="only check local data")
    args = parser.parse_args()

    if ready():
        print(f"PMData is ready at {DATA}")
        return 0
    if args.check:
        print(f"PMData is missing. Dataset page: {PAGE}")
        return 1
    if not ARCHIVE.is_file():
        print(f"Downloading from {URL}")
        download()
    print(f"Extracting required files to {DATA} (food images are skipped)")
    extract()
    if not ready():
        raise RuntimeError("PMData extraction did not produce all 16 participant folders")
    print("PMData is ready. The downloaded archive may now be deleted to save 1.4 GB.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print(f"PMData setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
