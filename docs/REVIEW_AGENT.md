# Stage 6 architecture and security review

Review date: 2026-11-12. Scope: the local chat agent, its HTTP boundary,
confirmation flow, session persistence, model adapter and historical-data tools.
All review tests use synthetic fixtures, fake provider decisions and temporary
SQLite databases. They do not read the operator's `.env`, use real credentials,
contact MOLIT or contact a model endpoint. This review does not establish real
Zoro compatibility or reasoning quality.

## Authority and transaction boundaries

| Boundary | Required behavior | Independent regression coverage |
| --- | --- | --- |
| Model decision → executable tool | Exact tool allowlist, exact argument keys and strict values; no shell, URL or arbitrary function dispatch | Invalid tools, extra fields, incorrect booleans, unknown inspection fields and unsupported district |
| Proposed change → mutation | Server-stored arguments plus a separate explicit confirmation | Conditions, inspections, status, entry time and schedule application remain unchanged before approval |
| Review → current session | Pending ID belongs to the same session and current revision | Forged IDs, cross-session IDs, stale revisions and manual mutations |
| One turn → persistence | Isolated working state followed by one compare-and-swap commit | Multi-step audit revisions, failure rollback and concurrent manual changes |
| Request retry → execution | Request ID is bound to operation and payload; replay cannot execute a different command | Earlier-turn replay, changed payload, chat/confirm collisions, cancel-to-approve replay and concurrent duplicates |
| Request limit → pending review | Reserve the final ledger entry for resolving an existing review; stop before creating a further pending change | Review at request 199, approval at request 200, and rejection of later new chats |
| Model output → public response | Server-generated result summary; raw provider diagnostics are not displayed or saved | Unperformed booking claim in a finish reason; fake secrets in upstream exceptions |
| Model mode → provider | Preserve the original mode across approval and fail without a development fallback | Provider error and confirmation continuation |
| Data mode → source | Preserve the original sample/live selection and fail without substituted sample data | Live failure and confirmation continuation |
| Proposal → schedule | Revalidate against the current schedule and normalize proposal revisions after the atomic commit | Chat proposal, review and explicit application sequence |
| HTTP request → local application | Strict JSON/type validation and existing cross-site protections | Invalid request shapes, non-boolean approval and foreign-origin requests |

## Reviewed implementation properties

The decision protocol treats model responses as proposed data. Allowed tools are
enumerated and validated before dispatch. Condition updates merge only supplied
fields, so changing the budget does not silently remove accessibility or other
requirements. An observation about one inspection field does not verify another
field. Confirming an internal schedule never creates an external booking.

The provider adapter uses an operator-configured destination and model name. It
does not accept a destination or credential from chat requests. Its prompt uses
an allowlisted projection of task state and bounded prior summaries, identifies
task content as untrusted, and omits configuration and raw external documents.
Redirects and environment proxies are disabled. Response size, request size,
tool-call count and timeout are bounded. Upstream response bodies and exception
details do not become public error messages. These controls constrain authority;
they do not prove that a real model will choose a useful next action.

The historical evidence tool checks source labels and normalized record shapes,
reads at most two pages of 100 records, and displays at most ten matches. It
distinguishes historical records from fictional current listings, excludes
cancelled trade records and reports unknown cancellation status. Partial scans
do not support a full-month or market-wide average claim. Review identified a
query-limit inconsistency; the evidence implementation was aligned with the
shared agent protocol for minimum year, name length and area range.

## Findings resolved during review

- A multi-step turn initially rebased stored schedule proposals but left their
  public trace copies at intermediate revisions. Matching proposal observations
  now use the committed revision, and the independent test compares both copies.
- The non-evicting request ledger initially allowed its last confirmation to
  create another review that could never be confirmed. The last slot now applies
  the already-approved action and stops before additional planning. The next
  proposed change is neither applied nor left pending.
- Discarded tool operations use an explicit `rolled_back` trace status. The
  protocol and UI now recognize it and display that the state change was not
  retained, rather than presenting the operation as completed.

The tested approval, stale-state, replay, secret-handling and rollback boundaries
passed after these changes. Replayed requests return current state without
running another mutation; conflicting requests do not overwrite newer manual
changes. A failing turn may record safe conversation/audit metadata while its
business changes are discarded.

## Practical limits

This remains a local application without multi-user authentication. Session
UUIDs isolate application state but are not a user-account authorization system.
Same-origin checks and the trusted-host policy reduce browser-origin attacks;
they do not establish protection from a local operating-system user with access
to the application's files or process. Public hosting and shared-user operation
are outside this delivery.

The model adapter is a prepared OpenAI-compatible interface assumption, not a
verified organizer Zoro interface. Actual endpoint shape, model identity and
real-model quality remain unresolved until separately configured and tested.
The reported successful MOLIT checks on the user's own PC are not attributed to
this offline review.

## Verification record

Independent cases are in `tests/test_agent_security.py`. The test module guards
the convenience `app.main` import with temporary explicit settings, installs a
network-denying transport, injects deterministic planners/providers and asserts
persisted state as well as HTTP responses.

Command: `python -m pytest tests/test_agent_security.py -q`, executed with the
isolated `zoro-clean-check` interpreter. Result: **53 passed** in 2.84 seconds.
The only warning was the installed Starlette/TestClient dependency's existing
`httpx` deprecation notice. This is a targeted architecture/security result; the
complete application suite and browser checks are recorded separately.

## Final provider-integration review

A separate provider review reproduced a configured MOLIT credential in manually
entered session data reaching the model adapter. The provider knows only its own
credentials, so the orchestration layer now checks the full outgoing context
and trace against configured secrets before calling it. Three independent
MockTransport tests cover a neighborhood preference, inspection notes and prior
conversation state; all assert zero model requests and safe persisted failure.
These checks do not claim to detect every unknown secret a person could type.

`tests/test_agent_provider_boundary.py`: **3 passed**. The complete release
suite: **1,186 passed**; detailed scope is in `VERIFICATION.md`.
