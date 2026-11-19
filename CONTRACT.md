# Shared implementation contract — 2026-11-12

Scope: requested preparation steps 1–4 only. Portable local-first Python FastAPI application with a buildless HTML/CSS/JS UI. No external deployment, no live Zoro inference, no claims of real API verification without keys. Do not implement a fake chat as real AI. Working title: 집의 다음 · Zoro Realty Prep.

## Ownership
- Root: app/main.py, app/config.py, app/schemas.py, package/setup scripts, README, integration and packaging.
- api agent: app/molit.py, tests/test_molit.py, tests/fixtures/molit*.xml only.
- data agent: data/*.json and tests/test_data.py only.
- spec agent: docs/SPEC.md, docs/SCENARIOS.md, docs/NEXT_STEPS.md only.
- UI agent: static/index.html, static/styles.css, static/app.js only.
- QA agent: tests/test_app.py, tests/test_config.py, docs/QA_REVIEW.md only.
- research/security agent: docs/API_REFERENCE.md, docs/SECURITY_REVIEW.md only; review suggestions, do not touch others' files.

## Fixture files (UTF-8 JSON)
- data/listings.json = {schema_version:"1.0", synthetic:true, listings:[...]}; exactly 20 fictional apartment jeonse listings in district_code 11680 (Gangnam-gu). Listing required keys: id, name, district_code, neighborhood, deposit_krw(integer KRW), area_m2(number), floor(integer), elevator(bool|null), step_free_access(bool|null), entry_steps(integer|null), noise_level("low"|"medium"|"high"|null), available_from(YYYY-MM-DD), status("available"|"contracted"|"verification_required"), source_type:"synthetic", notes(string), verified_at(string|null). All names clearly fictional. No real resident information.
- data/inspections.json = {synthetic:true, inspections:[{id,listing_id,field,observed_value,status:"confirmed"|"unverified",observed_at,notes}]}; do not silently treat planned or unverified as evidence.
- data/schedules.json = {synthetic:true, date:"2026-11-25", events:[{id,type:"cleaning"|"moving"|"entry",start,end,status,depends_on:[id]}], provider_slots:[{id,type,start,end}]}; ISO date-time with +09:00; include all three event types.
- data/scenarios.json = {schema_version:"1.0",synthetic:true,scenarios:[{id,title,category:"consultation"|"reverification"|"schedule"|"api",given:{},event:{},expected_actions:[string],must_not:[string],verification_stage:"offline"|"zoro_required"|"live_api_required"}]}; exactly 20 meaningful cases, never claim passed before actual checks. 5 categories? Only the four declared enum categories. Include all three verification stages.

## Runtime API contract (root implements)
- GET /api/health -> {app_name,version,mode:"preparation",zoro:{configured:bool,verified:false,status:"not_connected"},molit:{trade_key_configured:bool,rent_key_configured:bool,verified:false},counts:{listings,scenarios},notice}; no keys, paths, or configured Zoro URL returned.
- GET /api/listings -> fixture envelope, always synthetic true; optional query filters max_deposit_krw, min_area_m2, status (validated); these are deterministic filters, not Zoro decisions.
- GET /api/preparation -> {inspections:<fixture>,schedules:<fixture>,scenarios:<fixture>}
- GET /api/transactions?kind=trade|rent&lawd_cd=11680&deal_ymd=202610&page_no=1&num_of_rows=20&mode=sample|live -> TransactionPage object. Default mode=sample; sample data clearly synthetic, separately labeled. Explicit live never falls back to sample.
- errors -> HTTP status and {error:{code,message,retryable}}. Request validation uses same error shape. No raw exception/remote XML/request URL/secret value in errors or logs.

## app/molit.py interface (api agent owns)
Use httpx.AsyncClient with dependency injection for offline MockTransport tests; secure XML parsing with defusedxml. No browser-side keys, no user-supplied upstream host, no outbound on import.
- MolitError(Exception): attributes code(str), message(str safe Korean), retryable(bool), http_status(int), constructor for those args.
- async fetch_transactions(*, kind:str, lawd_cd:str, deal_ymd:str, page_no:int, num_of_rows:int, api_key:str, client:httpx.AsyncClient|None=None, timeout_seconds:float=10.0) -> dict.
- def parse_transactions(xml_content:bytes|str, *, kind,lawd_cd,deal_ymd,page_no,num_of_rows) -> dict. Use safe amount parsing integer KRW from API units of 10,000 KRW; area float; canceled sales flagged, don't imply active inventory. Input validation performed in client as defense in depth.
- Page shape: {source:"molit"|"synthetic_sample",synthetic:bool,kind,lawd_cd,deal_ymd,page_no,num_of_rows,total_count,returned_count,has_next:bool,retrieved_at:ISO with timezone,items:[{id,apartment_name,legal_dong,contract_date:"YYYY-MM-DD",area_m2,floor,build_year:int|null,price_krw:int|null,deposit_krw:int|null,monthly_rent_krw:int|null,cancelled:bool,cancelled_date:str|null,lease_type:"trade"|"jeonse"|"monthly_rent"}],warnings:[string]}. Every normal page says transactions are historical reported contracts, not available listings. Make malformed rows fail clearly or return warning+explicit skipped_count, never invent values.
- Fixed HTTPS endpoints: https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade and https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent. Official portal pages 15126469, 15126474.
- Query keys serviceKey, LAWD_CD, DEAL_YMD, pageNo, numOfRows. Accept Decoding key; optionally normalize percent-encoded key exactly once with urllib.parse.unquote, never unquote_plus (literal plus matters). Never follow redirects with key. Cap bytes and caller page size; explicit no retry on auth/quota by default.

## Settings
Environment keys: MOLIT_TRADE_API_KEY, MOLIT_RENT_API_KEY (optional shared MOLIT_API_KEY fallback); ZORO_BASE_URL, ZORO_API_KEY (reserved only); MOLIT_TIMEOUT_SECONDS (1–30 default10), APP_HOST=127.0.0.1, APP_PORT=8000. Key values must be secret types or repr=False. Load .env from project root only, do not commit/bundle. Root Settings.from_env(... optional mapping for tests) and public_status(). Root create_app(settings: Settings|None=None) factory and module app. app.state.settings and app.state.molit_fetcher enable explicit test injection. The route calls app.state.molit_fetcher with exactly above fetch_transactions kwargs plus timeout_seconds; tests can inject fake async function. Data loaded from project/data.

## Testing and handoff
pytest + httpx MockTransport/TestClient. Test implementation invariants: key encoding, no leaked secrets, error classification, XML shape/units, pagination/empty results, sample/live separation, fixture referential integrity, controls. Synthetic scenario catalog is planned evaluation; not equal to 20 model tests passed. Separate code test count, live API not tested, Zoro not tested. README Korean beginner-first. No actual network calls without user's keys. New keys not requested in chat.
