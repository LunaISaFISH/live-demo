"""Prepare the local environment and start FitCoach: python run.py."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="prepare and check installation without starting the server")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required; Python 3.13 is recommended.")
    os.environ["PYTHONUTF8"] = "1"
    folder = ROOT / ".venv"
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating .venv...", flush=True)
        venv.create(folder, with_pip=True, symlinks=os.name != "nt")
    check = ("import importlib.metadata as m, pathlib; "
             "requirements = pathlib.Path('requirements.txt').read_text().splitlines(); "
             "assert all(m.version(name) == version for name, version in "
             "(line.split('==') for line in requirements if line and not line.startswith('#')))")
    installed = subprocess.run([str(python), "-c", check], cwd=ROOT, capture_output=True)
    if installed.returncode:
        subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], cwd=ROOT, check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", "requirements.txt"],
                       cwd=ROOT, check=True)
    subprocess.run([str(python), "restore_data.py"], cwd=ROOT, check=True)
    subprocess.run([str(python), "-c", "import server"], cwd=ROOT / "prototype", check=True)
    if args.check:
        print("Dependencies, data and application imports are ready. No model request was made.")
        return 0
    return subprocess.call([str(python), "-u", "server.py"], cwd=ROOT / "prototype")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}. Fix the reported error, then run this command again.", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
