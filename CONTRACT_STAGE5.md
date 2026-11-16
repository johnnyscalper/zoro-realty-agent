# Stage 5 integration contract

Work in /workspace/scratch/f8c036d27619/zoro-realty-prep. User requests real MOLIT verification plus stage5 tools integrated with v0.1; no keys are available, so never claim real API or Zoro execution. Implement meaningful manual tools before Zoro. v0.2. Existing 180 tests must remain passing. No model inference, external booking, messages, or deployment. Root handles integration/packaging. Use inherited high reasoning. Read original CONTRACT.md and source files as needed.

## Ownership (avoid overlapping writes)
1 domain agent: app/domain.py + tests/test_domain.py
2 state agent: app/store.py + tests/test_store.py
3 schedule agent: app/scheduling.py + tests/test_scheduling.py
4 secret setup agent: scripts/configure_keys.py, scripts/check_api.py, CONFIGURE_API_KEYS_WINDOWS.cmd, tests/test_key_setup.py
5 UI agent: static/index.html,static/styles.css,static/app.js only
6 independent integration tests agent: tests/test_tools_api.py + docs/STAGE5_SCENARIOS.md only
Root: app/tools.py,app/tools_routes.py,app/main.py,app/config.py,README,START_HERE,version,package,report.
Second wave agents7-10 review domain, schedule, security, usability/docs; no implementation overlap unless root asks.

## Shared JSON contract
Conditions = {hard:{max_deposit_krw:int|null,min_area_m2:number|null,move_in_by:YYYY-MM-DD|null,step_free_access:bool|null,elevator:bool|null},preferences:{noise_level:"low"|"medium"|"high"|null,neighborhoods:[str]}}. Missing budget is incomplete input; do not claim suitable recommendations. All money integers KRW. None means unknown/unspecified, never false. Domain normalization fills omitted keys; refuse unknown/malformed input and bool-as-number. district fixed11680. No automatic relaxation.

State = {session_id,revision:int,conditions,listings:[original listing schema],verification_tasks:[],inspections:[],search_result:null|SearchResult,search_stale:bool,schedule:{events:[],provider_slots:[]},schedule_proposals:[],audit:[]}. Initial state copies baseline fixtures; no global changes to fixture files. A session starts revision0; each successful mutation increments once. Bounded audit records safe timestamp,action,summary. SearchResult = {status:"ready"|"incomplete",missing_fields:[str],eligible:[Candidate],needs_verification:[Candidate],excluded:[Candidate]}; Candidate = {listing:<original listing>,reasons:[{code,message,field}],preference_score:int}. Excluded includes contracted, hard violations. Unknown hard evidence is pending, never eligible. Sort by preference score descending then id. Reason strings Korean. Decisions are deterministic tools, NOT Zoro choices.

## Pure domain module interface
- DomainError(ValueError) with code,message (safe strings); no payload echoed.
- normalize_conditions(payload:dict)->normalized dict.
- search_candidates(listings:list,conditions:dict)->SearchResult.
- make_verification_task(listing:dict,field:str,existing_tasks:list)->dict (UUID task id; keys id,listing_id,field,status:"pending",question,created_at). Deduplicate pending same listing+field or return existing. Valid fields: entry_steps,step_free_access,elevator,noise_level. Listing must exist and not contracted (root resolves existence).
- apply_inspection(listing:dict,*,field:str,value:any,notes:str="")->new listing copy, validated field/value. Confirmed observation only; value null invalid. entry_steps positive cannot by itself disprove step_free_access (alternative route unknown), elevator=true not proof of step-free. Set verified_at ISO. No unknown→known inference beyond submitted field. Root manages tasks and evidence history.

## Store interface (SQLite, synchronous)
- StateError(Exception) attributes code,message,http_status.
- SessionStore(db_path:Path|str), create(state:dict)->saved state (generates session_id/revision0), get(session_id)->state, mutate(session_id,expected_revision:int,mutator:Callable[[dict],dict])->saved state (BEGIN IMMEDIATE, compare revision, callback returns new state, commit once), delete optional not required.
- Root initially uses cfg.state_db_path (default project/.runtime/realty.sqlite3). No persistence secrets. StateError SESSION_NOT_FOUND404, REVISION_CONFLICT409. Snapshot copies, failure rollback, concurrent expected revision only one succeeds. Validate id format/small bound, no SQL interpolation. Store owns session_id/revision; callback cannot override.

## Schedule module interface
- ScheduleError(ValueError) attrs code,message.
- validate_schedule(schedule:dict)->dict normalized or error; ISO timestamps require timezone offset (all fixtures KST), unique ids, positive durations except entry may be a point, known refs, no cycles.
- detect_conflicts(schedule:dict)->list of {code,message,event_ids:[str]}; honor dependencies and available provider slots for cleaning/moving; no fake provider availability. Baseline events must be valid. Adjacent end==start allowed.
- shift_entry(schedule:dict,new_end:str)->new schedule; move entry event preserving its original duration, then conflicts visible. Entry end is the time dwelling is accessible. Do not auto-move later tasks.
- propose_schedules(schedule:dict,*,limit:int=5)->list of {id,events:[...],summary:str}; enumerate compatible listed cleaning/moving slot combinations, preserve durations and dependency graph, no booking. Use available slots only; alternatives may span days IF supplied slots do. <=5 alternatives, deterministic ordering. Root annotates revision after storage. No alternatives -> empty with explicit UI message.
- apply_proposal(schedule:dict,proposal:dict)->new valid schedule; root validates proposal from session not client arbitrary values, recheck conflicts.

## Router (root)
Use /api/sessions. POST JSON requests only, same origin; no keys to client. Errors standard {error:{code,message,retryable}}. Mutations require expected_revision int (reject bool/fraction). Code strings safe, no payload/raw exceptions in errors/logs. Audit cap200.
- POST /api/sessions body {} or {conditions:<Conditions>} -> state (201)
- GET /api/sessions/{id} -> state
- PATCH /api/sessions/{id}/conditions {expected_revision,conditions:<full conditions replacement>} -> state; invalidate previous search and proposals.
- POST /api/sessions/{id}/search {expected_revision} -> state with recalculated search_result and search_stale=false.
- POST /api/sessions/{id}/verification-tasks {expected_revision,listing_id,field} -> state, dedup task.
- POST /api/sessions/{id}/inspections {expected_revision,task_id,value,notes?} -> state; pending task required, mark confirmed and append evidence, update listing. Re-run stored search if any, keep changed result visible. Previously completed task cannot be edited silently; create new request later for corrections. No unsafe inference.
- PATCH /api/sessions/{id}/listings/{listing_id}/status {expected_revision,status:"available"|"contracted"|"verification_required"} -> state; recalc search if exists, cancel pending tasks for contracted listing, invalidate proposals.
- PATCH /api/sessions/{id}/schedule/entry {expected_revision,new_end:ISO} -> state with schedule.conflicts populated, clear proposals.
- POST /api/sessions/{id}/schedule/proposals {expected_revision} -> state with <=5 validated alternatives, each based_on_revision = resulting revision.
- POST /api/sessions/{id}/schedule/apply {expected_revision,proposal_id} -> state; only stored proposal at current revision can be selected, revalidate, apply internal schedule, clear proposals, record audit.

UI: retain original3 tabs and add '실행실'. No chat, no model claims. Simple create/resume session (store ONLY random session_id in sessionStorage if used), conditions form (budget in 억 converted exact integer, area/date/access requirements/preferences), buckets candidate display, verification create/submit, status change dropdown, schedule entry change/proposals/apply, audit and revision. Show synthetic local manual-tool mode, no automatic booking. Backend returns full state for every write; update expected_revision from latest. 409 shows refresh action (do not silently retry overwrite). User-facing errors Korean, never raw server traceback.

## Key setup
Existing env contains NO keys. Build terminal getpass entry tool, preserves other .env settings, atomic write/private permissions, no key args/logs/reports. User can run locally, then explicit live check of both APIs writes reports/api_verification_report.json with counts/status/timestamp only (no names,raw bodies,keys). Existing scripts/check_api.py may be refactored for report generation while preserving CLI. ZIP excludes .env/.runtime/reports/*.json runtime files. Do not add real credentials or network calls to tests. Installer callable from Windows launcher if Pythondeps absent. User must locally enter keys; report actual verification not 'connected' just because keys saved.

## Verification
Run each unit suite, integrated tests and existing tests. Synthetic evaluation cases != live model success. Stage5 tools actually executed in integration/browser and persist across reload. Cross-session isolation, stale revision protection, confirmed vs pending evidence, valid dates/money and no leaking API keys are core invariants. Do not implement phase6 Zoro caller yet. Public health preserves existing preparation mode and zoro not_connected; add tool status/version separately if needed.
