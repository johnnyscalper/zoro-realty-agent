"""Outgoing model-context checks with fake settings and MockTransport only."""
from copy import deepcopy
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.agent_service import ChatService
from app.config import Settings
from app.store import SessionStore
from app.tools import ToolService
from app.zoro_provider import ZoroProvider


FAKE_MOLIT_KEY = "offline-provider-boundary-molit-secret"
BUSINESS_FIELDS = (
    "conditions", "listings", "verification_tasks", "inspections",
    "search_result", "search_stale", "schedule", "schedule_proposals",
)


@pytest.mark.parametrize("location", ["neighborhoods", "inspection_notes", "prior_conversation"])
def test_existing_secret_never_crosses_model_request_boundary(location, tmp_path, monkeypatch):
    def forbidden_configuration(*_args, **_kwargs):
        pytest.fail("Provider boundary regression must not read real configuration")

    async def forbidden_network(*_args, **_kwargs):
        pytest.fail("Provider boundary regression must not perform network requests")

    monkeypatch.setattr(Settings, "from_env", forbidden_configuration)
    monkeypatch.setattr("app.config.dotenv_values", forbidden_configuration)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_network)
    root = Path(__file__).resolve().parents[1]
    fixtures = {name: json.loads((root / "data" / f"{name}.json").read_text(encoding="utf-8"))
                for name in ("listings", "inspections", "schedules")}
    tool_service = ToolService(SessionStore(tmp_path / "provider-boundary.sqlite3"), fixtures)
    conditions = {"hard": {"max_deposit_krw": 700_000_000}}
    if location == "neighborhoods":
        # Existing manual tools accept short arbitrary neighborhood text. This
        # must not authorize forwarding a configured credential to a model.
        conditions["preferences"] = {"neighborhoods": [FAKE_MOLIT_KEY]}
    state = tool_service.create(conditions)
    if location == "inspection_notes":
        state = tool_service.request_verification(state["session_id"], state["revision"], "L001", "elevator")
        task = state["verification_tasks"][-1]
        state = tool_service.confirm_inspection(state["session_id"], state["revision"], task["id"], True,
                                                notes=FAKE_MOLIT_KEY)
    elif location == "prior_conversation":
        # Simulate a retained conversation written by an earlier application
        # version; current chat correctly rejects direct secret submissions.
        def old_conversation(snapshot):
            snapshot["agent"] = {"messages": [{
                "id": str(uuid4()), "role": "user", "text": FAKE_MOLIT_KEY,
                "created_at": "2026-11-12T00:00:00+00:00",
            }]}
            return snapshot
        state = tool_service.store.mutate(state["session_id"], state["revision"], old_conversation)

    before = {key: deepcopy(state[key]) for key in BUSINESS_FIELDS}
    settings = Settings(molit_trade_api_key=FAKE_MOLIT_KEY,
                        zoro_base_url="https://offline-provider-boundary.invalid",
                        zoro_model="offline-provider-boundary-model",
                        state_db_path=tmp_path / "provider-boundary.sqlite3")
    chat = ChatService(tool_service, settings)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"tool": "finish", "arguments": {}, "reason": "완료"}),
        }}]})

    chat.provider = ZoroProvider(base_url=settings.zoro_base_url, model=settings.zoro_model,
                                 transport=httpx.MockTransport(handler))
    result = asyncio.run(chat.chat(state["session_id"], {
        "expected_revision": state["revision"], "message": "현재 조건을 확인해 주세요",
        "mode": "zoro", "data_mode": "sample", "request_id": str(uuid4()),
    }))

    assert requests == []
    assert {key: result[key] for key in BUSINESS_FIELDS} == before
    turn = result["agent"]["turns"][-1]
    assert turn["status"] == "error"
    assert turn["zoro_used"] is False
    assert turn["trace"][-1]["observation"]["code"] == "SENSITIVE_INPUT"
    assert result["agent"]["pending"] is None
    # Existing user-authored state remains intact; this failure must not echo
    # the value into newly generated replies, trace, or conversation entries.
    generated = {"turn": turn, "messages": result["agent"]["messages"][-2:]}
    assert FAKE_MOLIT_KEY not in json.dumps(generated, ensure_ascii=False)
    assert tool_service.get(result["session_id"]) == result
