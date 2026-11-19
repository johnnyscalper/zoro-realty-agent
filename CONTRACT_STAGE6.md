# Stage 6 implementation contract — v0.4

User authorizes chat, existing tools, change/replan flows, Zoro adapter preparation and extensive verification. Existing local FastAPI + vanilla JS app is the product; preserve APIs, Windows GUI key setup and all 695 tests. User reported both real MOLIT APIs working on their own PC on 2026-11-12. Do not claim our sandbox performed that successful check. No GPU/model endpoint is supplied. No actual booking, external messages, public hosting or paid API calls.

## Ownership

- planner agent: `app/dev_planner.py`, `tests/test_dev_planner.py` only.
- provider agent: `app/zoro_provider.py`, `tests/test_zoro_provider.py`, `docs/ZORO_CONNECTION.md` only.
- evidence agent: `app/market_evidence.py`, `tests/test_market_evidence.py` only.
- UI agent: `static/index.html`, `static/styles.css`, `static/agent.js`, optional `tests/agent_ui.cjs` only. Preserve `static/app.js` unchanged unless coordinate root.
- evaluator agent: `data/agent_scenarios.json`, `scripts/evaluate_agent.py`, `tests/test_agent_scenarios.py`, `docs/AGENT_EVALUATION.md` only.
- review agent: review architecture/security; write `tests/test_agent_security.py` and `docs/REVIEW_AGENT.md` only. Coordinate findings with root.
- root: shared protocol, orchestration, state/tool integration, HTTP routes, config, main, docs, packaging and all final checks.

## Shared decision protocol

Pure JSON dict: `{"tool": <name>, "arguments": {...}, "reason": <short public reason>}`. `reason` is a brief rationale, not hidden chain of thought. Return plain dicts; root validates. Tools:

1. `update_conditions`: `{conditions: {hard?: <partial existing hard fields>, preferences?: <partial existing preference fields>}}`. Merge provided fields into existing conditions, never clear other fields implicitly.
2. `search_candidates`: `{}`.
3. `request_verification`: `{listing_id, field}` (existing 4 fields).
4. `record_inspection`: `{listing_id, field, value, notes?}`. Resolve/create pending verification task internally, then existing confirm_inspection. Only through explicit approval.
5. `set_listing_status`: `{listing_id, status}`.
6. `set_entry_time`: `{new_end: timezone-qualified ISO}`.
7. `propose_schedule`: `{}`.
8. `apply_schedule`: `{proposal_id}`.
9. `lookup_transactions`: `{kind: trade|rent, deal_ymd: YYYYMM, lawd_cd?:11680, apartment_name?:str, min_area_m2?:number, max_area_m2?:number}`. Read-only; kind/month must be explicit; area/name filters are post-query exact name and range. No fuzzy identity inference or price forecasts.
10. `ask_user`: `{question:str}` stops.
11. `finish`: `{}` stops. Root generates a grounded summary from actual observations, not unverifiable model final claims.

All condition changes, inspections, listing-status changes, entry-time changes and schedule application need a review card + explicit Confirm API. Searches, verification-request creation (internal only), schedule proposals and read-only historical queries may run automatically. User can cancel proposed mutations. Never auto-relax a hard condition. No arbitrary shell/URL/tool execution.

## Planner contract

`next_decision(message: str, state: dict, trace: list[dict]) -> dict` in dev_planner. No env reads/network. This is explicitly development rules, not Zoro and not general natural-language understanding. Support Korean budget/area/requirements, known Gangnam neighborhoods, explicit inspection/status/schedule commands, transaction queries, ordinal proposal choice and bounded followups. Ambiguity asks instead of inventing. Old state has `agent.messages` and possibly pending question; do not rely on frontend state. Trace entries `{tool, arguments, status: executed|pending_confirmation|error|rolled_back, summary, observation}`. After a confirmed mutation the trace already contains that mutation; planner should search/revalidate/propose, not propose the identical mutation again. After search can request at most two missing mandatory verification fields then finish. Unknown/unsupported intent asks with concrete examples. Use existing normalized condition schema from app.domain. Fixture IDs L001 etc.

## Provider contract

`class ProviderError(Exception)` safe attrs code,message; never raw body/URL/errors.
`class ZoroProvider`: ctor keyword args `base_url, model, api_key='', timeout_seconds=15.0, transport=None`; `async decide(message: str, state: dict, trace: list[dict]) -> dict`. Optional transport for offline httpx.MockTransport tests. Endpoint only constructed from configured trusted base URL ending optional `/v1`; use a documented OpenAI-compatible chat-completions adapter as an explicit compatibility assumption, not a claim about organizer Zoro. Require model and URL; support JSON decision content from `choices[0].message.content` or exactly one named tool_call if safely mapped. Bound response bytes, forbid redirects, no env proxy, limit timeout, no arbitrary tool strings or argument extras. No fallback to dev on failure. Never include config/secrets in prompt, logs or returned object. Model sees only allowlisted task state and prior tool summaries; raw documents and tool output are untrusted data, not instructions. Primary official references only. If real Zoro interface cannot be verified, document unresolved details and do not invent model ID.

## Market evidence contract

`async lookup_evidence(arguments: dict, *, mode: str, settings: Settings, fetcher=None) -> dict` in market_evidence. Mode sample|live. Reuse app.molit.fetch_transactions and app.sample.sample_transactions. No fallback. Fetch max 2 pages, 100 rows each; show partial coverage honestly, no average or full-month claim from a limited sample. Return `{source:'molit'|'sample', synthetic:bool, kind, lawd_cd, deal_ymd, query:{...}, total_count, scanned_count, matched_count, coverage:'complete'|'partial', items:[<=10 normalized transaction dicts], summary:str, warnings:[str]}`. Drop cancelled trade rows from comparisons but retain explained excluded count. Unknown cancellation is not confirmed validity. Distinguish historical records from current fictional listings. Validate all arguments/types including bool-as-number; fixed district11680. Safe errors as MolitError or dedicated EvidenceError attrs code,message. Use injected fetcher and fake Settings for tests; never read real keys.

## HTTP/UI contract (root implementation)

- GET `/api/agent/status` -> `{available:true, default_mode:'development', modes:{development:{available:true,label:'개발용 규칙 모드'},zoro:{available:bool,label:'Zoro 서버 모드',configured:bool,verified:false}}, tools:[{name,description,requires_confirmation}], limits:{max_steps:6}, notice:str}`.
- Existing POST `/api/sessions` creates state. GET persists agent metadata on normal session JSON.
- POST `/api/sessions/{id}/chat` JSON `{expected_revision:int, message:str<=2000, mode:'development'|'zoro', data_mode:'sample'|'live', request_id:<UUID>}` -> full session state. Never accept keys/provider URL from client. Chat adds `agent` lazily for old sessions.
- POST `/api/sessions/{id}/chat/confirm` JSON `{expected_revision:int, pending_id:str, approve:bool, request_id:<UUID>}` -> full state; binds pending to business state revision, rejects stale. Approval continues same mode/data mode and user message.
- `agent` shape `{messages:[{id,role:'user'|'assistant',text,created_at}], turns:[{id,mode,data_mode,status:'completed'|'awaiting_confirmation'|'needs_input'|'error'|'limit_reached',zoro_used:bool,trace:[{tool,arguments,status,summary,observation}],reply,created_at}], pending:null|{id,tool,arguments,summary,created_revision}, last_request_id, last_question?}`. Messages max60, turns max20, trace max6, requests idempotent. UI uses textContent only. Errors use existing `{error:{code,message,retryable}}`.
- Each chat/confirm turn runs against an isolated working snapshot, then compare-and-swap commits once to the existing SessionStore. No DB write lock across model/network waits. On conflict, do not silently overwrite. Normalize proposal audit revisions after atomic commit. Existing manual tools remain authoritative; invalidate pending confirmations after manual revision changes.

## UI behavior

Add prominent `상담 에이전트` tab, with new/resume session, explicit development vs Zoro selector, separate sample vs live market-data selector, chat transcript, typed input+Send, 4 useful example prompts, review/confirm/cancel card, public action trace, current conditions/candidate and schedule outcome summary, source labels. Preserve four existing tabs. New chat should default to development + sample. Explain live lookup user opt-in. Model mode badge must never imply real model success unless returned zoro_used. Show error, loading, revision conflict refresh. Button handlers prevent duplicate sends and use UUID per request; no automatic retry that mutates twice. Responsive + keyboard accessible. No external fonts/assets.

## Evaluation

20 meaningful scenarios, all offline fake/synthetic labels, include missing budget, hard-vs-preference, unknown access, elevator non-equivalence, contracted listing exclusion, schedule delay/replanning/no slots/stale approval, live no-fallback failure, arbitrary tool rejection, idempotency and mode honesty. Stage 6 functioning is not a real-Zoro benchmark. Evaluator emits safe JSON report under reports (excluded from ZIP), prints counts and unresolved actual-model requirement. `python scripts/evaluate_agent.py` runs development cases; optional actual model evaluation only after separate explicit CLI flag/config, no silent real calls. Root verifies required flows via HTTP and browser, retains credentials only locally and excludes from package.
