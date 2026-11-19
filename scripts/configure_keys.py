"""Local, hidden credential entry; saving keys is never a live verification.

Official endpoint/key references: docs/API_REFERENCE.md. Only the existing
MOLIT client decodes a key for transport; validation here never changes it.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import getpass
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv.parser import parse_stream

from app.molit import MolitError, _normalize_key


KEY_NAMES = ("MOLIT_TRADE_API_KEY", "MOLIT_RENT_API_KEY", "MOLIT_API_KEY")


class KeySetupError(ValueError):
    """A fixed public diagnostic with no credential or filesystem contents."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes invalid arguments, which might be secrets.
        self.print_usage(sys.stderr)
        self.exit(2, "인수를 확인하세요. 인증키는 명령행 인수나 채팅에 입력하지 말고 로컬 키 설정 도구에서 입력하세요.\n")


def validate_key(value: str) -> str:
    """Validate once-decoded syntax but preserve the original for the client."""
    if not isinstance(value, str) or len(value) > 4096:
        raise KeySetupError("인증키 형식을 확인하세요. 공공데이터포털의 일반 인증키(Decoding)를 권장합니다.")
    try:
        _normalize_key(value)
    except MolitError:
        raise KeySetupError("인증키가 비어 있거나 형식이 올바르지 않습니다. 일반 인증키(Decoding)를 다시 입력하세요.") from None
    return value.strip()


def _private_permissions(path: Path) -> None:
    """Set permissions before writing; Windows chmod alone is not an ACL."""
    if os.name != "nt":
        path.chmod(0o600)
        return
    account = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = list(csv.reader(io.StringIO(account.stdout)))
    sid = rows[0][1] if len(rows) == 1 and len(rows[0]) == 2 else ""
    if not re.fullmatch(r"S-1-(?:[0-9]+-)*[0-9]+", sid):
        raise KeySetupError("현재 사용자 파일 권한을 확인할 수 없어 저장하지 않았습니다.")
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
        check=True, capture_output=True, timeout=10,
    )


def atomic_write_private(path: Path, text: str) -> None:
    """Write a private sibling then replace; never truncate an existing file."""
    path = Path(path)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise KeySetupError("심볼릭 링크에는 설정이나 검증 기록을 저장하지 않습니다.")
        if not path.parent.exists():
            path.parent.mkdir(mode=0o700, parents=True)
        if path.exists() and not path.is_file():
            raise KeySetupError("저장 대상이 일반 파일이 아닙니다.")
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        _private_permissions(temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            descriptor = None
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if path.is_symlink() or path.parent.is_symlink():
            raise KeySetupError("심볼릭 링크에는 설정이나 검증 기록을 저장하지 않습니다.")
        os.replace(temporary, path)
        temporary = None
    except KeySetupError:
        raise
    except Exception:
        raise KeySetupError("파일을 안전하게 저장하지 못했습니다. 폴더 권한과 남은 공간을 확인하세요.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _quote(value: str) -> str:
    # Settings disables dotenv interpolation, including for literal ${...}.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def save_keys(path: Path, trade_key: str, rent_key: str, *, shared: bool = False) -> None:
    """Update exactly the three MOLIT key settings, preserving other bindings."""
    trade_key, rent_key = validate_key(trade_key), validate_key(rent_key)
    if shared and trade_key != rent_key:
        raise KeySetupError("같은 키 사용을 선택했으므로 두 인증키가 같아야 합니다.")
    path = Path(path)
    if path.is_symlink():
        raise KeySetupError("심볼릭 링크에는 인증키를 저장하지 않습니다.")
    try:
        original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        bindings = list(parse_stream(io.StringIO(original)))
    except Exception:
        raise KeySetupError("기존 설정 파일을 읽지 못했습니다. 설정 파일 형식과 권한을 확인하세요.") from None
    if any(binding.error for binding in bindings):
        raise KeySetupError("기존 설정 파일의 형식 오류를 먼저 수정하세요. 기존 파일은 변경하지 않았습니다.")
    # Parse instead of replacing lines: another quoted value may span lines.
    preserved = "".join(binding.original.string for binding in bindings if binding.key not in KEY_NAMES)
    if preserved and not preserved.endswith("\n"):
        preserved += "\n"
    values = {
        "MOLIT_TRADE_API_KEY": "" if shared else trade_key,
        "MOLIT_RENT_API_KEY": "" if shared else rent_key,
        "MOLIT_API_KEY": trade_key if shared else "",
    }
    updated = preserved + "".join(f"{name}={_quote(value)}\n" for name, value in values.items())
    atomic_write_private(path, updated)


def _read_hidden(prompt: str) -> str:
    # getpass warns then echoes on unsupported terminals; abort that fallback.
    if not sys.stdin.isatty():
        raise KeySetupError("키 입력은 로컬 대화형 터미널에서 실행하세요. 파이프 입력은 받지 않습니다.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return validate_key(getpass.getpass(prompt))
        except getpass.GetPassWarning:
            raise KeySetupError("숨김 입력을 지원하지 않는 터미널입니다. 일반 로컬 터미널에서 다시 실행하세요.") from None


def _yes(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes", "예", "네"}


def main(argv: list[str] | None = None) -> int:
    SafeArgumentParser(description="로컬 터미널에서 국토부 키를 숨김 입력합니다. 인증키 인수는 지원하지 않습니다.").parse_args(argv)
    try:
        print("국토부 인증키를 이 컴퓨터에 저장합니다. 입력 내용은 화면에 표시되지 않습니다.")
        print("키는 채팅·스크린샷·제출 ZIP에 넣지 마세요. 키 저장만으로 연결이 검증되지는 않습니다.")
        trade_key = _read_hidden("매매 API 일반 인증키(Decoding): ")
        shared = _yes("전월세에도 같은 인증키를 사용하나요? [y/N]: ")
        rent_key = trade_key if shared else _read_hidden("전월세 API 일반 인증키(Decoding): ")
        save_keys(ROOT / ".env", trade_key, rent_key, shared=shared)
        del trade_key, rent_key
        print("키를 저장했습니다. 실행 중인 앱이 있다면 종료한 뒤 다시 시작하세요.")
        if any(os.environ.get(name) for name in KEY_NAMES):
            print("주의: 현재 터미널의 MOLIT 환경 변수는 .env보다 우선합니다. 아래 검증도 그 우선순위를 따릅니다.")
        print("다음 단계: 매매·전월세 활용승인을 각각 확인한 뒤 실제 API 호출을 검증하세요.")
        python_command = r".venv\Scripts\python.exe" if os.name == "nt" else ".venv/bin/python"
        print(f"나중에 검증: {python_command} scripts/check_api.py --kind both --lawd-cd 11680 --month 202610")
        if not _yes("지금 매매·전월세 API를 각각 1회 호출할까요? (11680 / 202610 / 최대 5건씩) [y/N]: "):
            print("실제 API 검증은 실행하지 않았습니다. 앱 실행은 START_WINDOWS.cmd를 사용하세요.")
            return 0
        from scripts.check_api import check

        return asyncio.run(check(argparse.Namespace(kind="both", lawd_cd="11680", month="202610")))
    except (EOFError, KeyboardInterrupt):
        print("입력을 중단했습니다. 연결 검증 완료로 기록하지 않습니다.")
        return 1
    except KeySetupError as exc:
        print(str(exc))
        return 1
    except Exception:
        print("키 설정을 완료하지 못했습니다. 로컬 실행 환경을 확인하세요. 인증키나 오류 원문을 공유하지 마세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
