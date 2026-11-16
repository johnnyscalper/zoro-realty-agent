from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import threading
import time
from uuid import UUID, uuid4

import pytest

from app.store import SessionStore, StateError


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "runtime" / "state.sqlite3")


def baseline():
    return {
        "conditions": {"hard": {"max_deposit_krw": 900_000_000}},
        "listings": [{"id": "listing-1", "status": "available", "notes": "가상 매물"}],
        "verification_tasks": [],
        "inspections": [],
        "search_result": None,
        "search_stale": True,
        "schedule": {"events": [], "provider_slots": []},
        "schedule_proposals": [],
        "audit": [],
    }


def assert_safe_error(exc, code, status):
    assert exc.value.code == code
    assert exc.value.http_status == status
    assert exc.value.message == str(exc.value)
    assert "private-secret" not in str(exc.value)
    assert "SELECT" not in str(exc.value)


def test_create_owns_metadata_and_does_not_change_input(store):
    source = baseline()
    source.update(session_id="caller-controlled", revision=999)
    untouched = deepcopy(source)
    saved = store.create(source)
    assert UUID(saved["session_id"]).version == 4
    assert saved["session_id"] != source["session_id"]
    assert saved["revision"] == 0
    assert source == untouched
    assert store.get(saved["session_id"]) == saved


def test_reopening_store_retains_state_and_changes(tmp_path):
    db_path = tmp_path / "nested" / "runtime" / "state.sqlite3"
    first = SessionStore(str(db_path))
    saved = first.create(baseline())
    updated = first.mutate(saved["session_id"], 0, lambda state: {**state, "search_stale": False})
    second = SessionStore(db_path)
    assert second.get(saved["session_id"]) == updated
    assert db_path.is_file()


def test_create_and_get_return_independent_nested_snapshots(store):
    source = baseline()
    saved = store.create(source)
    session_id = saved["session_id"]
    source["listings"][0]["status"] = "contracted"
    saved["listings"][0]["notes"] = "changed outside store"
    first_read = store.get(session_id)
    assert first_read["listings"] == baseline()["listings"]
    first_read["schedule"]["events"].append({"id": "external-edit"})
    assert store.get(session_id)["schedule"]["events"] == []


def test_mutation_commits_once_and_owns_metadata(store):
    original = store.create(baseline())
    retained = []

    def update(state):
        assert state == original
        state["listings"][0]["status"] = "contracted"
        state["session_id"] = "caller-controlled"
        state["revision"] = 700
        retained.append(state)
        return state

    changed = store.mutate(original["session_id"], 0, update)
    assert changed["session_id"] == original["session_id"]
    assert changed["revision"] == 1
    assert changed["listings"][0]["status"] == "contracted"
    assert retained[0]["revision"] == 700
    assert retained[0]["session_id"] == "caller-controlled"
    retained[0]["listings"][0]["status"] = "available"
    changed["audit"].append({"action": "outside"})
    read = store.get(original["session_id"])
    assert read["listings"][0]["status"] == "contracted"
    assert read["audit"] == []
    assert original["revision"] == 0
    assert original["listings"][0]["status"] == "available"


def test_mutator_may_replace_document_but_not_session_metadata(store):
    original = store.create(baseline())
    replacement = {"value": {"nested": [1, True, None, 1.25]}}
    saved = store.mutate(original["session_id"], 0, lambda _: replacement)
    assert saved == {**replacement, "session_id": original["session_id"], "revision": 1}
    assert "revision" not in replacement
    assert store.get(original["session_id"]) == saved


def test_stale_revision_never_calls_callback_or_changes_state(store):
    created = store.create(baseline())
    latest = store.mutate(created["session_id"], 0, lambda state: state)
    called = []
    with pytest.raises(StateError) as exc:
        store.mutate(created["session_id"], 0, lambda state: called.append(state))
    assert_safe_error(exc, "REVISION_CONFLICT", 409)
    assert called == []
    assert store.get(created["session_id"]) == latest


@pytest.mark.parametrize("revision", [True, False, -1, 0.0, 0.5, "0", None, 1 << 63])
def test_invalid_expected_revision_is_rejected(store, revision):
    created = store.create(baseline())
    with pytest.raises(StateError) as exc:
        store.mutate(created["session_id"], revision, lambda state: state)
    assert_safe_error(exc, "INVALID_REVISION", 422)
    assert store.get(created["session_id"])["revision"] == 0


@pytest.mark.parametrize("session_id", [
    "", "private-secret", "' OR 1=1; SELECT * FROM sessions; --",
    "x" * 10_000, None, 17, {}, str(uuid4()), str(uuid4()).upper(),
])
def test_missing_or_malformed_ids_are_safe_404s(store, session_id):
    created = store.create(baseline())
    for operation in (
        lambda: store.get(session_id),
        lambda: store.mutate(session_id, 0, lambda state: state),
    ):
        with pytest.raises(StateError) as exc:
            operation()
        assert_safe_error(exc, "SESSION_NOT_FOUND", 404)
    assert store.get(created["session_id"]) == created


def test_callback_failure_rolls_back_and_preserves_original_exception(store):
    created = store.create(baseline())
    failure = ValueError("safe domain failure")

    def fail(state):
        state["listings"][0]["status"] = "contracted"
        state["audit"].append({"action": "not committed"})
        raise failure

    with pytest.raises(ValueError) as exc:
        store.mutate(created["session_id"], 0, fail)
    assert exc.value is failure
    assert store.get(created["session_id"]) == created
    assert store.mutate(created["session_id"], 0, lambda state: state)["revision"] == 1


@pytest.mark.parametrize("returned", [None, [], "private-secret", {"invalid": object()}])
def test_invalid_callback_result_rolls_back(store, returned):
    created = store.create(baseline())

    def invalidate(state):
        state["listings"].clear()
        return returned

    with pytest.raises(StateError) as exc:
        store.mutate(created["session_id"], 0, invalidate)
    assert_safe_error(exc, "INVALID_STATE", 422)
    assert store.get(created["session_id"]) == created


def test_sessions_are_isolated_from_shared_baseline_and_each_other(store):
    source = baseline()
    first, second = store.create(source), store.create(source)
    assert first["session_id"] != second["session_id"]

    def mark_contracted(state):
        state["listings"][0]["status"] = "contracted"
        return state

    changed = store.mutate(first["session_id"], 0, mark_contracted)
    assert changed["listings"][0]["status"] == "contracted"
    assert store.get(second["session_id"]) == second
    assert source == baseline()


def test_only_one_concurrent_writer_accepts_the_same_revision(tmp_path):
    path = tmp_path / "state.sqlite3"
    stores = [SessionStore(path), SessionStore(path)]
    created = stores[0].create({"counter": 0})
    barrier = threading.Barrier(8)
    callback_count = []

    def write(index):
        barrier.wait(timeout=5)

        def increment(state):
            callback_count.append(index)
            # Keep the winning transaction open while the other connections wait.
            time.sleep(0.025)
            state["counter"] += 1
            return state

        try:
            return stores[index % 2].mutate(created["session_id"], 0, increment)
        except StateError as error:
            return error

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(write, range(8)))
    successes = [result for result in results if isinstance(result, dict)]
    errors = [result for result in results if isinstance(result, StateError)]
    assert len(successes) == len(callback_count) == 1
    assert len(errors) == 7
    assert all(error.code == "REVISION_CONFLICT" and error.http_status == 409 for error in errors)
    assert stores[0].get(created["session_id"])["counter"] == 1
    assert stores[1].get(created["session_id"])["revision"] == 1


def test_concurrent_revision_retries_do_not_lose_updates(tmp_path):
    path = tmp_path / "state.sqlite3"
    stores = [SessionStore(path), SessionStore(path)]
    created = stores[0].create({"counter": 0})

    def write_many(index):
        store = stores[index % 2]
        for _ in range(15):
            for _attempt in range(200):
                current = store.get(created["session_id"])
                try:
                    store.mutate(
                        created["session_id"], current["revision"],
                        lambda state: {**state, "counter": state["counter"] + 1},
                    )
                    break
                except StateError as error:
                    assert error.code == "REVISION_CONFLICT"
            else:
                pytest.fail("Concurrent writer did not finish")

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write_many, range(4)))
    final = stores[0].get(created["session_id"])
    assert final["counter"] == final["revision"] == 60


@pytest.mark.parametrize("invalid", [
    [], None, {"value": float("nan")}, {"value": float("inf")},
    {"value": float("-inf")}, {"value": (1, 2)}, {1: "private-secret"},
    {"value": b"private-secret"}, {"value": object()}, {"value": "\ud800"},
])
def test_non_json_states_are_rejected_without_echoing_contents(store, invalid):
    with pytest.raises(StateError) as exc:
        store.create(invalid)
    assert_safe_error(exc, "INVALID_STATE", 422)


def test_large_legitimate_documents_are_not_limited_to_fixture_size(store):
    source = {"listings": [{"id": f"item-{index}", "notes": "가상 매물 " * 40} for index in range(2_000)]}
    saved = store.create(source)
    assert store.get(saved["session_id"])["listings"] == source["listings"]


def test_oversized_unicode_document_is_rejected_without_mutation(store):
    created = store.create({"notes": "original"})
    with pytest.raises(StateError) as exc:
        store.mutate(created["session_id"], 0, lambda state: {**state, "notes": "가" * (3 * 1024 * 1024)})
    assert_safe_error(exc, "STATE_TOO_LARGE", 413)
    assert store.get(created["session_id"]) == created


def test_circular_and_excessively_deep_json_are_bounded(store):
    cycle = {}
    cycle["self"] = cycle
    deep = current = {}
    for _ in range(100):
        current["nested"] = {}
        current = current["nested"]
    for source in (cycle, deep):
        with pytest.raises(StateError) as exc:
            store.create(source)
        assert_safe_error(exc, "STATE_TOO_LARGE", 413)


def test_many_tiny_json_nodes_are_bounded(store):
    with pytest.raises(StateError) as exc:
        store.create({"values": [None] * 250_001})
    assert_safe_error(exc, "STATE_TOO_LARGE", 413)


@pytest.mark.parametrize("corruption", [
    "private-secret invalid JSON", '{"session_id":"wrong","revision":0}',
    '{"revision":NaN}', "[]",
])
def test_corrupted_snapshots_are_reported_safely(tmp_path, corruption):
    path = tmp_path / "state.sqlite3"
    store = SessionStore(path)
    created = store.create(baseline())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE sessions SET snapshot = ? WHERE session_id = ?", (corruption, created["session_id"]))
    for operation in (
        lambda: store.get(created["session_id"]),
        lambda: store.mutate(created["session_id"], 0, lambda state: state),
    ):
        with pytest.raises(StateError) as exc:
            operation()
        assert_safe_error(exc, "STATE_CORRUPT", 500)


def test_database_errors_do_not_reveal_filesystem_paths(tmp_path):
    directory = tmp_path / "private-secret"
    directory.mkdir()
    with pytest.raises(StateError) as exc:
        SessionStore(directory)
    assert_safe_error(exc, "STATE_UNAVAILABLE", 503)
    assert str(tmp_path) not in str(exc.value)


def test_store_never_copies_environment_credentials(tmp_path, monkeypatch):
    secret = "private-secret-unique-credential"
    monkeypatch.setenv("MOLIT_API_KEY", secret)
    monkeypatch.setenv("ZORO_API_KEY", secret)
    path = tmp_path / "state.sqlite3"
    store = SessionStore(path)
    created = store.create(baseline())
    assert secret not in str(store.get(created["session_id"]))
    assert secret.encode() not in path.read_bytes()


def test_new_database_is_private_on_posix(tmp_path):
    import os
    if os.name != "posix":
        pytest.skip("POSIX permission bits are not supported on this platform")
    path = tmp_path / "runtime" / "state.sqlite3"
    SessionStore(path)
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0
