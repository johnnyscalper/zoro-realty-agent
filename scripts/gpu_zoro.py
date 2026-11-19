"""Run on the rented Linux GPU, not on the user's Windows laptop.

Uses the official decision/chat server at a fixed source revision. No public
model port is opened: connect through an SSH local-forwarding tunnel.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import venv

REPOSITORY = "https://github.com/trillion-labs/trida-stack.git"
REVISION = "0ddcf516e458a82892d7fe0d0dabeb113d3cf424"
MODEL = "trillionlabs/Zoro-1"
SGLANG = "0.5.22"
PORT = 30001


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def preflight(runtime: Path, *, need_space=True):
    if sys.platform != "linux" or not (3, 11) <= sys.version_info[:2] < (3, 13):
        raise RuntimeError("Use a Linux GPU image with Python 3.11 or 3.12; Python 3.12 is recommended.")
    for command in ("git", "nvidia-smi"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Missing {command}. Choose a CUDA development / PyTorch GPU template.")
    run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], timeout=15)
    parent = runtime
    while not parent.exists():
        parent = parent.parent
    free_gib = shutil.disk_usage(parent).free / 1024**3
    print(f"Filesystem free space: {free_gib:.1f} GiB. Also check the GPU instance's disk quota.", flush=True)
    if need_space and free_gib < 35:
        raise RuntimeError("At least 35 GiB free is recommended before installation. Use about 80 GB allocated disk.")


def setup(runtime: Path):
    preflight(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    repo = runtime / "trida-stack"
    if not repo.exists():
        run(["git", "init", str(repo)])
        run(["git", "-C", str(repo), "remote", "add", "origin", REPOSITORY])
        run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", REVISION])
        run(["git", "-C", str(repo), "checkout", "--detach", REVISION])
    actual = run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if actual != REVISION:
        raise RuntimeError("Existing runtime uses a different source revision. Use a new --runtime-dir.")
    python = runtime / "venv" / "bin" / "python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(runtime / "venv")
    print("Installing the official stock SGLang decision/chat backend. This can take time.", flush=True)
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", f"sglang=={SGLANG}", "fastapi", "uvicorn", "huggingface_hub"])
    frozen = run([str(python), "-m", "pip", "freeze"], capture_output=True, text=True).stdout
    (runtime / "installed-packages.txt").write_text(frozen, encoding="utf-8")
    (runtime / "source-manifest.json").write_text(json.dumps({
        "repository": REPOSITORY, "revision": REVISION, "model": MODEL,
        "sglang": SGLANG, "mode": "decide", "gpu_verified": False,
    }, indent=2), encoding="utf-8")
    print("Setup finished. This does not yet prove GPU inference works.")
    print("Next: accept access on https://huggingface.co/trillionlabs/Zoro-1")
    print("Then run this helper with 'login', followed by 'start'.")


def runtime_env(runtime: Path):
    env = os.environ.copy()
    env["HF_HOME"] = str(runtime / "hf-cache")
    env["PYTHONUNBUFFERED"] = "1"
    return env


DOWNLOAD = '''
import json, pathlib, sys
from huggingface_hub import snapshot_download
root = pathlib.Path(sys.argv[1])
marker = root / "model-snapshot.json"
try:
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    checkpoint = snapshot_download("trillionlabs/Zoro-1", revision=previous.get("revision", "main"))
    path = pathlib.Path(checkpoint).resolve()
    if not any((path / name).is_file() for name in ("decision_head.pt", "jev_head.pt")):
        raise ValueError("decision head missing")
    marker.write_text(json.dumps({"model": "trillionlabs/Zoro-1", "revision": path.name, "path": str(path)}, indent=2))
except Exception:
    print("Model download failed. Check model access, HF login, network and free disk; no token is printed.", file=sys.stderr)
    sys.exit(1)
'''


def start(runtime: Path):
    preflight(runtime, need_space=False)
    python = runtime / "venv" / "bin" / "python"
    if not python.is_file():
        raise RuntimeError("Run setup first.")
    env = runtime_env(runtime)
    print("Downloading/reusing the official checkpoint. Later starts reuse the same model revision.", flush=True)
    run([str(python), "-c", DOWNLOAD, str(runtime)], env=env)
    metadata = json.loads((runtime / "model-snapshot.json").read_text())
    script = runtime / "trida-stack" / "inference" / "zoro-1" / "serve_decide.py"
    print("Starting Zoro on GPU loopback 127.0.0.1:30001. Keep this terminal open.", flush=True)
    print("On your laptop, forward local port 30001 to port 30001 on this GPU through SSH.", flush=True)
    command = [str(python), str(script), metadata["path"], "--host", "127.0.0.1",
               "--port", str(PORT), "--context-length", "16384", "--mem-fraction", "0.8"]
    os.execve(str(python), command, env)


def status():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{PORT}/v1/models", timeout=5) as response:
        raw = response.read(65_537)
    if len(raw) > 65_536:
        raise RuntimeError("Unexpected model-list size.")
    ids = [item["id"] for item in json.loads(raw)["data"]]
    print("Server is reachable. Self-reported model ID(s):", json.dumps(ids, ensure_ascii=True))
    print("Reachability alone is not an agent test. Run CHECK_ZORO_WINDOWS.cmd on your laptop.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "setup", "login", "start", "status"))
    parser.add_argument("--runtime-dir", type=Path, default=Path("/workspace/zoro-realty-runtime"))
    args = parser.parse_args(argv)
    runtime = args.runtime_dir.expanduser().resolve()
    try:
        if args.action == "preflight":
            preflight(runtime)
        elif args.action == "setup":
            setup(runtime)
        elif args.action == "login":
            executable = runtime / "venv" / "bin" / "hf"
            if not executable.is_file():
                raise RuntimeError("Run setup first.")
            run([str(executable), "auth", "login"], env=runtime_env(runtime))
        elif args.action == "start":
            start(runtime)
        else:
            status()
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("Step failed. Check the preceding installation output, access and GPU environment.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped. This does not stop the cloud GPU instance or its billing.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
