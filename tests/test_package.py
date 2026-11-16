"""Regression checks for accidental credential/artifact distribution."""
from pathlib import Path
import importlib.util
import zipfile

SPEC = importlib.util.spec_from_file_location("build_package", Path(__file__).parents[1] / "scripts/build_package.py")
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


def test_package_excludes_private_files_and_environment(tmp_path, monkeypatch):
    root = tmp_path / "project"
    for name, body in {
        "README.md": "public readme", ".env.example": "MOLIT_API_KEY=\n",
        ".env": "MOLIT_API_KEY=dummy-private-must-not-be-bundled",
        ".env.local": "dummy-private-must-not-be-bundled",
        "credentials.key": "dummy-private-must-not-be-bundled",
        "app/main.py": "print('source')", "static/app.js": "'source';",
        "data/listings.json": "{}", "tests/test_source.py": "pass",
        "docs/guide.md": "public docs", "docs/.private.md": "dummy-private-must-not-be-bundled",
        ".venv/site.py": "dummy-private-must-not-be-bundled",
        "app/__pycache__/cached.py": "dummy-private-must-not-be-bundled",
        "data/raw.log": "dummy-private-must-not-be-bundled",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    monkeypatch.setattr(packager, "ROOT", root)
    output = tmp_path / "result.zip"
    packager.build(output)
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "zoro-realty-prep/.env.example" in names
        assert "zoro-realty-prep/app/main.py" in names
        assert "zoro-realty-prep/.env" not in names
        for name in names:
            assert b"dummy-private-must-not-be-bundled" not in archive.read(name)


def test_packager_does_not_follow_allowed_extension_symlinks(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "README.md").write_text("public")
    private = tmp_path / "private.txt"
    private.write_text("do not copy")
    (root / "app/link.py").symlink_to(private)
    monkeypatch.setattr(packager, "ROOT", root)
    assert root / "app/link.py" not in list(packager.source_files())
