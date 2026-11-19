"""Regressions for Zoro tool-call wire formats and the live check's diagnostics."""
import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.zoro_provider import ProviderError, ZoroProvider, _tool_definitions
from scripts.check_zoro import run_checks


def call(name, args, preface=None):
    return {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": preface, "tool_calls": [{"type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}],
    }}]}


def test_inspection_union_preserves_numbers_and_booleans_in_the_official_parser():
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in _tool_definitions()}
    value = schemas["record_inspection"]["properties"]["value"]
    # The pinned server treats any top-level type containing string as text.
    assert "type" not in value
    assert {s["type"] for s in value["oneOf"]} == {"boolean", "integer", "string"}
    assert schemas["lookup_transactions"]["properties"]["lawd_cd"]["type"] == "string"


def test_one_native_call_discards_prose_and_keeps_strict_validation():
    provider = ZoroProvider(base_url="http://localhost:30001", model="test")
    raw = call("record_inspection", {"listing_id": "L003", "field": "elevator", "value": False},
               "현장 결과를 확인할게요. discarded-marker")
    decision = provider._decision(json.dumps(raw).encode())
    assert decision["arguments"]["value"] is False
    assert "discarded-marker" not in json.dumps(decision)
    msg = raw["choices"][0]["message"]
    msg["tool_calls"][0]["function"]["arguments"] = json.dumps(
        {"listing_id": "L003", "field": "elevator", "value": "false"})
    with pytest.raises(ProviderError) as exc:
        provider._decision(json.dumps(raw).encode())
    assert exc.value.diagnostics["stage"] == "argument_schema"
    assert exc.value.diagnostics["attempted_tool"] == "record_inspection"
    assert "field:elevator,value:string" in exc.value.diagnostics["argument_shape"]


@pytest.mark.parametrize("content", ['{"tool": "finish"}', '<tool_call>second</tool_call>', ["other action"]])
def test_another_possible_action_alongside_native_call_is_rejected(content):
    provider = ZoroProvider(base_url="http://localhost:30001", model="test")
    with pytest.raises(ProviderError) as exc:
        provider._decision(json.dumps(call("search_candidates", {}, content)).encode())
    assert exc.value.diagnostics["stage"] == "mixed_actions"


def test_three_distinct_failures_visible_without_model_prose(capsys):
    # The first tool is valid. A later duplicate rolls back the search. The
    # other scenarios stop inside provider validation before the first action.
    wrong = call("record_inspection", {"listing_id": "L001", "field": "elevator", "value": "false"})
    replies = iter([call("search_candidates", {}), call("search_candidates", {}), wrong, wrong,
                    {"choices": [{"message": {"content": "private-diagnostic-marker"}}]}])
    provider = ZoroProvider(base_url="http://localhost:30001", model="test",
                            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=next(replies))))
    rows = asyncio.run(run_checks(Settings(zoro_base_url="http://localhost:30001", zoro_model="test"),
                                  provider=provider))
    assert all(not r["passed"] for r in rows)
    assert rows[0]["turns"][0]["error"] == {"code": "REPEATED_ACTION", "attempted_tool": "search_candidates"}
    assert rows[0]["turns"][0]["zoro_used"] is True
    assert rows[1]["turns"][0]["error"]["stage"] == "argument_schema"
    assert rows[1]["turns"][0]["error"]["attempted_tool"] == "record_inspection"
    assert rows[1]["turns"][0]["error"]["retry_after"] == "argument_schema"
    assert rows[2]["turns"][0]["error"]["stage"] == "content_json"
    assert all(not r["approval_checked"] for r in rows)
    output = capsys.readouterr().out
    assert output.count("DIAG ") == 3
    assert "private-diagnostic-marker" not in output + json.dumps(rows)
