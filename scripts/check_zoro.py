"""Opt-in live model checks using synthetic fixtures and a disposable database.

Only the configured model endpoint is contacted. No MOLIT keys, user sessions,
bookings or external messages are used. The endpoint's model identity is
self-reported: a successful response is not independent weight attestation.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import __version__
from app.config import Settings
from app.store import SessionStore
from app.tools import ToolService
from app.agent_service import ChatService
from scripts.configure_keys import atomic_write_private
from scripts.configure_zoro import discover_models

CONDITIONS = {
    "hard": {"max_deposit_krw": 1_000_000_000, "min_area_m2": 75,
             "move_in_by": "2026-11-25", "step_free_access": True, "elevator": None},
    "preferences": {"noise_level": "low", "neighborhoods": []},
}
CASES = (
    ("candidate_search", "저장된 조건으로 아파트 전세 후보를 검색해줘.", None),
    ("inspection_recheck", "현장에서 L001의 무계단 접근이 불가능한 것으로 확인했어. 이 확인 결과를 반영해줘.", "record_inspection"),
    ("entry_delay", "2026년 11월 25일 입주 가능 시각이 오후 3시로 늦어졌어. 입주 시각을 변경하고 청소와 이사 일정 대안을 찾아줘.", "set_entry_time"),
)


def trace_summary(state):
    turn = state["agent"]["turns"][-1]
    # These values are generated/validated by our server. Do not export model
    # prose, raw requests, settings, inspection notes or raw HTTP errors.
    return {"status": turn["status"], "zoro_used": turn["zoro_used"],
            "tools": [row["tool"] for row in turn["trace"]]}


async def run_checks(settings, *, provider=None):
    fixtures = {key: json.loads((ROOT / "data" / f"{key}.json").read_text(encoding="utf-8"))
                for key in ("listings", "inspections", "schedules")}
    results = []
    with TemporaryDirectory(prefix="zoro-live-check-") as temporary:
        isolated = replace(settings, molit_trade_api_key="", molit_rent_api_key="",
                           state_db_path=Path(temporary) / "disposable.sqlite3")
        tool_service = ToolService(SessionStore(isolated.state_db_path), fixtures)
        service = ChatService(tool_service, isolated)
        if provider is not None:  # Unit tests inject a fake provider explicitly.
            service.provider = provider

        def chat_payload(state, message):
            return {"expected_revision": state["revision"], "request_id": str(uuid4()),
                    "message": message, "mode": "zoro", "data_mode": "sample"}

        async def forbid_molit(**kwargs):
            raise RuntimeError("MOLIT network disabled for live model checks")

        for case, message, mutation in CASES:
            print(f"Checking {case} ...", flush=True)
            begin = perf_counter()
            record = {"case": case, "passed": False, "turns": [], "approval_checked": False}
            try:
                state = tool_service.create(deepcopy(CONDITIONS))
                if mutation == "record_inspection":
                    state = tool_service.search(state["session_id"], state["revision"])
                before = deepcopy(state)
                state = await service.chat(state["session_id"], chat_payload(state, message), molit_fetcher=forbid_molit)
                record["turns"].append(trace_summary(state))
                if mutation:
                    pending = state["agent"]["pending"]
                    unchanged = all(state[field] == before[field] for field in ("conditions", "listings", "schedule"))
                    pending_ok = bool(pending and pending["tool"] == mutation and unchanged)
                    if pending_ok and mutation == "record_inspection":
                        a = pending["arguments"]
                        pending_ok = a.get("listing_id") == "L001" and a.get("field") == "step_free_access" and a.get("value") is False
                    if pending_ok and mutation == "set_entry_time":
                        expected = datetime.fromisoformat("2026-11-25T15:00:00+09:00")
                        pending_ok = datetime.fromisoformat(pending["arguments"]["new_end"]) == expected
                    record["approval_checked"] = pending_ok
                    # The script approves only this exact test-case change in
                    # its disposable synthetic DB, never a user's real session.
                    if not pending_ok:
                        raise ValueError("Expected approval card missing")
                    state = await service.confirm(state["session_id"], {
                        "expected_revision": state["revision"], "request_id": str(uuid4()),
                        "pending_id": pending["id"], "approve": True,
                    }, molit_fetcher=forbid_molit)
                    record["turns"].append(trace_summary(state))
                if case == "candidate_search":
                    outcome = state["search_result"] is not None and any(
                        "search_candidates" in turn["tools"] for turn in record["turns"])
                elif case == "inspection_recheck":
                    listing = next(row for row in state["listings"] if row["id"] == "L001")
                    excluded = {row["listing"]["id"] for row in (state["search_result"] or {}).get("excluded", [])}
                    outcome = listing["step_free_access"] is False and "L001" in excluded
                else:
                    if not state["schedule_proposals"] and state["agent"]["turns"][-1]["status"] == "completed":
                        state = await service.chat(state["session_id"], chat_payload(state, "변경된 입주 시각에 맞는 청소·이사 일정 대안을 제시해줘."), molit_fetcher=forbid_molit)
                        record["turns"].append(trace_summary(state))
                    outcome = bool(state["schedule"]["conflicts"] and state["schedule_proposals"])
                clean = all(turn["status"] not in {"error", "limit_reached"} for turn in record["turns"])
                record["passed"] = bool(outcome and clean and any(t["zoro_used"] for t in record["turns"]))
            except Exception:
                record["failure"] = "Expected agent behavior was not completed; inspect the app's action trace."
            record["elapsed_seconds"] = round(perf_counter() - begin, 3)
            results.append(record)
            print(f"{case}: {'PASS' if record['passed'] else 'FAIL'} ({record['elapsed_seconds']}s)", flush=True)
    return results


def main():
    try:
        settings = Settings.from_env()
        if not settings.zoro_base_url or not settings.zoro_model:
            print("Configure Zoro first with CONFIGURE_ZORO_WINDOWS.cmd. No network test was run.")
            return 1
        print("This contacts your configured model server and uses GPU time. Fixtures and approvals are synthetic.", flush=True)
        ids = discover_models(settings.zoro_base_url, settings.zoro_api_key)
        if settings.zoro_model not in ids:
            print("Configured model ID differs from the server. Run CONFIGURE_ZORO_WINDOWS.cmd again.")
            return 1
        results = asyncio.run(run_checks(settings))
        report = {"app_version": __version__, "checked_at": datetime.now(timezone.utc).isoformat(),
                  "test_type": "live_configured_endpoint_synthetic_agent_flow", "data": "synthetic",
                  "model_identity": "self_reported_by_configured_endpoint_not_independently_attested",
                  "model_id_sha256": hashlib.sha256(settings.zoro_model.encode()).hexdigest(),
                  "molit_live_test": False, "production_database_used": False,
                  "cases_passed": sum(r["passed"] for r in results), "cases_total": len(results),
                  "results": results}
        atomic_write_private(ROOT / "reports" / "zoro_live_check.json", json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Saved reports/zoro_live_check.json: {report['cases_passed']}/{len(results)} passed.")
        print("These three cases do not replace the full 20-case evaluation or visual rehearsal.")
        return 0 if all(r["passed"] for r in results) else 1
    except KeyboardInterrupt:
        print("Cancelled. No success result recorded.")
        return 130
    except Exception:
        print("Connection/check failed. Check GPU server, SSH tunnel and saved settings. No secret or raw error is printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
