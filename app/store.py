"""Transactional, server-only JSON session snapshots.

Every operation opens its own connection, so a store can be used by multiple
request threads or alongside another store instance. SQLite serializes writes;
the revision check and the callback run inside the same write transaction.
"""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Callable, Iterator
from uuid import UUID, uuid4


# These are document/resource limits, not limits on the number of fixtures.
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_JSON_NODES = 250_000
_MAX_JSON_DEPTH = 64
_MAX_REVISION = (1 << 63) - 1
_BUSY_TIMEOUT_SECONDS = 10.0


class StateError(Exception):
    """A safe public error that never includes SQL, paths, or state contents."""

    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _not_found() -> StateError:
    return StateError("SESSION_NOT_FOUND", "실행 세션을 찾을 수 없습니다.", 404)


def _unavailable() -> StateError:
    return StateError("STATE_UNAVAILABLE", "실행 상태를 저장하거나 읽을 수 없습니다. 잠시 후 다시 시도해 주세요.", 503)


def _invalid_state() -> StateError:
    return StateError("INVALID_STATE", "실행 상태는 올바른 JSON 객체여야 합니다.", 422)


def _too_large() -> StateError:
    return StateError("STATE_TOO_LARGE", "실행 상태의 크기 또는 중첩 한도를 초과했습니다.", 413)


def _validate_session_id(session_id: str) -> None:
    # Do not coerce arbitrary values or permit SQLite fragments as identifiers.
    if type(session_id) is not str or len(session_id) != 36:
        raise _not_found()
    try:
        valid = str(UUID(session_id)) == session_id
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise _not_found()


def _validate_revision(revision: int) -> None:
    if type(revision) is not int or not 0 <= revision <= _MAX_REVISION:
        raise StateError("INVALID_REVISION", "현재 실행 상태의 정수 버전을 입력해 주세요.", 422)


def _validate_json(value: dict) -> None:
    """Bound work and reject Python-only types before JSON serialization."""
    if type(value) is not dict:
        raise _invalid_state()
    stack = [(value, 0)]
    node_count = 0
    text_length = 0
    while stack:
        item, depth = stack.pop()
        node_count += 1
        if node_count > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise _too_large()
        item_type = type(item)
        if item_type is dict:
            if node_count + len(stack) + len(item) > _MAX_JSON_NODES:
                raise _too_large()
            for key, child in item.items():
                if type(key) is not str:
                    raise _invalid_state()
                text_length += len(key)
                if text_length > _MAX_SNAPSHOT_BYTES:
                    raise _too_large()
                stack.append((child, depth + 1))
        elif item_type is list:
            if node_count + len(stack) + len(item) > _MAX_JSON_NODES:
                raise _too_large()
            stack.extend((child, depth + 1) for child in item)
        elif item_type is str:
            text_length += len(item)
            if text_length > _MAX_SNAPSHOT_BYTES:
                raise _too_large()
        elif item_type is float:
            if not math.isfinite(item):
                raise _invalid_state()
        elif item is not None and item_type not in (bool, int):
            raise _invalid_state()


def _encode_snapshot(state: dict, session_id: str, revision: int) -> tuple[str, dict]:
    if type(state) is not dict:
        raise _invalid_state()
    # Reserved metadata always comes from the store, even when a caller tries to
    # remove or replace it. The input and the callback's return value are untouched.
    owned = dict(state)
    owned["session_id"] = session_id
    owned["revision"] = revision
    _validate_json(owned)
    try:
        payload = json.dumps(owned, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            raise _too_large()
        saved = json.loads(payload)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid_state() from None
    return payload, saved


def _decode_snapshot(payload: str, session_id: str, revision: int) -> dict:
    """Treat an invalid on-disk snapshot as corruption, never as client input."""
    try:
        if type(payload) is not str or len(payload) > _MAX_SNAPSHOT_BYTES:
            raise ValueError
        if len(payload.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            raise ValueError
        saved = json.loads(payload)
        _validate_json(saved)
        if (saved.get("session_id") != session_id
                or type(saved.get("revision")) is not int
                or saved["revision"] != revision):
            raise ValueError
        _validate_revision(revision)
        return saved
    except (TypeError, ValueError, UnicodeError, RecursionError, StateError):
        raise StateError("STATE_CORRUPT", "저장된 실행 상태를 읽을 수 없습니다. 새 세션을 시작해 주세요.", 500) from None


class SessionStore:
    """Persist isolated JSON session snapshots in a local SQLite file.

    ``db_path`` is a server-controlled filesystem path. No environment settings,
    API credentials, or fixture files are read or written by this store.
    """

    def __init__(self, db_path: Path | str):
        try:
            # Resolve only a filesystem path; never enable SQLite URI options.
            self._db_path = Path(db_path).expanduser().resolve()
            self._db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                descriptor = os.open(self._db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS sessions ("
                    "session_id TEXT PRIMARY KEY NOT NULL, "
                    "revision INTEGER NOT NULL CHECK (revision >= 0), "
                    "snapshot TEXT NOT NULL)"
                )
        except (OSError, TypeError, ValueError):
            raise _unavailable() from None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(
                str(self._db_path), timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None,
            )
            yield connection
        except sqlite3.Error:
            raise _unavailable() from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.rollback()
        except sqlite3.Error:
            # Closing the connection also rolls back an uncommitted transaction.
            # Preserve the original validation/callback exception for the caller.
            pass

    def create(self, state: dict) -> dict:
        session_id = str(uuid4())
        payload, saved = _encode_snapshot(state, session_id, 0)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO sessions (session_id, revision, snapshot) VALUES (?, ?, ?)",
                    (session_id, 0, payload),
                )
                connection.commit()
            except BaseException:
                self._rollback(connection)
                raise
        return saved

    def get(self, session_id: str) -> dict:
        _validate_session_id(session_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT revision, snapshot FROM sessions WHERE session_id = ?", (session_id,),
            ).fetchone()
        if row is None:
            raise _not_found()
        return _decode_snapshot(row[1], session_id, row[0])

    def mutate(
        self, session_id: str, expected_revision: int, mutator: Callable[[dict], dict],
    ) -> dict:
        _validate_session_id(session_id)
        _validate_revision(expected_revision)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT revision, snapshot FROM sessions WHERE session_id = ?", (session_id,),
                ).fetchone()
                if row is None:
                    raise _not_found()
                revision, payload = row
                if revision != expected_revision:
                    raise StateError(
                        "REVISION_CONFLICT", "다른 작업으로 실행 상태가 변경되었습니다. 새로고침 후 다시 시도해 주세요.", 409,
                    )
                current = _decode_snapshot(payload, session_id, revision)
                if revision >= _MAX_REVISION:
                    raise StateError("REVISION_LIMIT", "실행 상태의 버전 한도에 도달했습니다. 새 세션을 시작해 주세요.", 409)
                updated = mutator(current)
                payload, saved = _encode_snapshot(updated, session_id, revision + 1)
                connection.execute(
                    "UPDATE sessions SET revision = ?, snapshot = ? WHERE session_id = ?",
                    (revision + 1, payload, session_id),
                )
                connection.commit()
            except BaseException:
                self._rollback(connection)
                raise
        return saved
