"""Create an isolated local environment, install pinned deps, and launch."""
from pathlib import Path
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-run", action="store_true", help="install only")
    parser.add_argument("--guided", action="store_true", help="show first-run setup and open the app in a browser")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required. https://www.python.org/downloads/")
    environment = ROOT / ".venv"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.exists():
        print("Creating a local Python environment...")
        venv.EnvBuilder(with_pip=True).create(environment)
    requirements = ROOT / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / ".requirements-sha256"
    if not marker.exists() or marker.read_text().strip() != digest:
        print("Installing dependencies. The first run needs an internet connection.")
        subprocess.run([str(executable), "-m", "pip", "install", "-r", str(requirements)], check=True, cwd=ROOT)
        marker.write_text(digest)
    env_path = ROOT / ".env"
    if not env_path.exists():
        shutil.copyfile(ROOT / ".env.example", env_path)
        try:
            env_path.chmod(0o600)
        except OSError:
            pass
        print("Created .env with empty key fields. Sample mode works without keys.")
    if args.no_run:
        print("Setup complete.")
        return
    if args.guided:
        subprocess.run([str(executable), str(ROOT / "scripts" / "first_run.py")], cwd=ROOT, check=True)
    print("Open the address printed below in your browser. Keep this terminal open.")
    command = [str(executable), str(ROOT / "run.py")]
    if args.guided:
        command.append("--open-browser")
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        raise SystemExit("Setup or launch failed. See the preceding error; never share API keys.") from None
    except KeyboardInterrupt:
        print("Stopped.")
