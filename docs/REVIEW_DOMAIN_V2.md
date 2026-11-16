# Stage 5 second-wave domain review

Reviewed 2026-11-12. Scope: `CONTRACT_STAGE5.md`, `app/domain.py`,
`app/tools.py`, HTTP request models, transactional session storage, fixture
evidence, and focused domain/session tests. No production code was changed.

## Finding D-V2-01 — supplied null conditions bypass strict input validation

**Severity: P3.** `app/tools_routes.py:15` declares creation conditions nullable,
and `app/tools.py:24` substitutes `{}` for falsey input. Consequently,
`POST /api/sessions` with `{"conditions": null}` returns **201** and persists a
default session. The contract permits `{}` or a supplied Conditions object and
requires malformed conditions to be refused. The pure domain function correctly
rejects `None`; creation bypasses that validation. A null payload is also rejected
by the conditions-update request model, so creation and update are inconsistent.

The observed impact is silently accepting malformed creation input and writing
an unintended default session. Missing-budget protection still applies; this
does **not** make an unknown-budget session eligible or fabricate evidence.

Recommended correction: declare creation conditions as a non-null dictionary
with an empty-dictionary default, for example
`conditions: dict[str, Any] = Field(default_factory=dict)`. Preserve acceptance
of an omitted property and `{}`. Avoid a general falsey fallback at the service
boundary if it is intended to be strict for direct callers too.

Regression: `tests/test_review_domain_v2.py` contains one isolated HTTP test.
It checks that both allowed forms remain valid and explicitly supplied null is
rejected with the standard 422 error envelope. It fails on the reviewed source
with actual status 201.

## Confirmed semantic behavior

- Missing budget produces an incomplete search with no eligible candidates.
  Unknown required accessibility fields stay pending; explicit false remains a
  real requirement/value. A hard failure wins over pending evidence.
- Noise and neighborhood preferences affect ranking and explanations, without
  becoming hard exclusions or relaxing budget, area, date, or accessibility.
- Requests and unverified fixture records do not become confirmed evidence.
  Inspection updates only the submitted field and the update timestamp.
  Elevator and stair observations do not infer full-route accessibility.
- Existing search results are recalculated after confirmed evidence or status
  changes. Condition replacement removes the previous result. Relevant writes
  clear schedule proposals; applying a proposal also checks its stored revision.
- Pending tasks are deduplicated within a session. Confirmed/cancelled tasks
  cannot be silently reused. Contracting a listing cancels its pending tasks.
- Session copies, transaction rollback, revision conflicts, scoped task lookup,
  and reload persistence passed the focused test suites. Baseline fixtures and
  other sessions remain unchanged by a session mutation.

No additional defect was identified in these semantic paths.

## Verification

Command:

```text
.venv/bin/python -m pytest -q tests/test_domain.py tests/test_tools_api.py tests/test_store.py tests/test_review_domain_v2.py
```

Result on the reviewed source: **222 passed, 1 failed**. The sole failure is
D-V2-01 above. Two dependency deprecation warnings do not affect the findings.
The execution was offline; no MOLIT verification, model call, booking, or
external message was performed.

## Integration resolution

Root changed CreateRequest.conditions to a non-null dictionary with Field(default_factory=dict). The regression now passes; the former failing result above is the review-time reproduction, not the final release result. See VERIFICATION.md for the complete final run.
