"""Explicit allowlist packaging. Credentials and machine state cannot enter ZIP."""
from pathlib import Path
import argparse
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {
    "README.md", "START_HERE_KO.md", "requirements.txt", "requirements-dev.txt",
    "pyproject.toml", "run.py", "START_WINDOWS.cmd", "START_MAC_LINUX.sh",
    ".env.example", ".gitignore", "CONTRACT.md",
    "CONTRACT_STAGE5.md", "CONTRACT_STAGE6.md", "CONFIGURE_API_KEYS_WINDOWS.cmd",
    "CONFIGURE_ZORO_WINDOWS.cmd", "CHECK_ZORO_WINDOWS.cmd",
}
DIR_EXTENSIONS = {
    "app": {".py"}, "static": {".html", ".css", ".js"},
    "data": {".json"}, "docs": {".md"},
    "tests": {".py", ".xml", ".json", ".cjs"}, "scripts": {".py"},
}


def source_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if any(p in {".venv", "__pycache__", ".git", ".pytest_cache", "node_modules", "dist", ".runtime", "reports"} for p in relative.parts):
            continue
        if len(relative.parts) == 1 and path.name in TOP_FILES:
            yield path
        elif len(relative.parts) > 1 and relative.parts[0] in DIR_EXTENSIONS and path.suffix in DIR_EXTENSIONS[relative.parts[0]] and not any(p.startswith(".") for p in relative.parts):
            yield path


def build(output: Path, *, flat: bool = False):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = list(source_files())
    if not files:
        raise SystemExit("No source files selected")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            prefix = "" if flat else "zoro-realty-prep/"
            archive.write(path, prefix + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(output) as archive:
        forbidden = {".env", ".venv", ".git", "node_modules", ".pytest_cache", ".runtime", "reports"}
        if any(forbidden.intersection(Path(name).parts) for name in archive.namelist()):
            output.unlink()
            raise SystemExit("Unexpected private file in package")
        if archive.testzip() is not None:
            raise SystemExit("ZIP validation failed")
    print(f"{output}\nFiles: {len(files)}\nSHA256: {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / "Zoro_Realty_Prep_v0.4.3.zip")
    parser.add_argument("--flat", action="store_true", help="put START_WINDOWS.cmd directly at the ZIP root")
    options = parser.parse_args()
    build(options.output, flat=options.flat)
