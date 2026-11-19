# 5단계 도구 실행과 20개 시나리오의 검증 범위

기존 [시나리오 목록](SCENARIOS.md)과 `data/scenarios.json`의 S01~S20을 유지하면서, v0.2에서 실제 실행 가능한 수동 도구와 남은 검증을 구분한다. 이 문서의 도구는 가상 매물·가상 현장 정보·가상 업체 시간표를 사용한다. 자동 대화, Zoro 추론, 실제 현장 확인, 업체 연락·예약을 실행하지 않는다.

`verification_stage: zoro_required`인 항목에서 일부 규칙을 코드로 실행할 수 있게 되어도, 원래 시나리오 전체나 Zoro 판단을 검증한 것으로 바꾸지 않는다. S20은 사용자가 로컬에서 인증키를 설정하고 두 API를 직접 호출해 실제 응답을 확인해야 완료할 수 있다.

## 실행 경로

실행실은 `POST /api/sessions`로 독립 상담을 만들고, 모든 변경을 SQLite에 저장한다. `GET /api/sessions/{id}`로 같은 상담을 다시 읽을 수 있다. 변경에는 최신 `expected_revision`이 필요하며, 이전 버전의 요청은 409 오류로 거부된다. 오류가 난 요청은 조건·매물·작업·현장 기록·일정·이력을 일부만 바꾸지 않는다.

| 도구 | 요청 경로 | 실행 결과 |
|---|---|---|
| 조건 저장 | `PATCH /api/sessions/{id}/conditions` | 필수 조건과 선호를 구분해 저장하고 이전 검색·일정 대안을 무효화 |
| 후보 검색 | `POST /api/sessions/{id}/search` | 충족 후보·확인 필요·제외를 근거와 함께 분류 |
| 확인 요청 | `POST /api/sessions/{id}/verification-tasks` | 매물·필드별 대기 작업 생성; 같은 대기 요청은 중복 생성하지 않음 |
| 확인 결과 반영 | `POST /api/sessions/{id}/inspections` | 대기 작업에 명시적 확인 결과를 기록하고 기존 검색을 재계산 |
| 매물 상태 변경 | `PATCH /api/sessions/{id}/listings/{listing_id}/status` | 계약 완료를 검색에 반영하고 해당 매물의 대기 작업을 취소 |
| 입주 시각 변경 | `PATCH /api/sessions/{id}/schedule/entry` | 입주 절차의 종료 시각을 바꾸고 청소·이사와의 충돌을 표시 |
| 일정 대안 계산 | `POST /api/sessions/{id}/schedule/proposals` | 입력된 업체 시간표에서 실행 가능한 대안을 최대 5개 제안 |
| 선택 대안 반영 | `POST /api/sessions/{id}/schedule/apply` | 현재 상담 버전에서 만든 대안만 내부 일정표에 반영 |

## S01~S20 대응표

아래의 “검사”는 자동화된 코드 검사의 근거다. 전체 사용자 시나리오의 성공 판정이나 실제 서비스 연결 판정이 아니다. `통합`은 `tests/test_tools_api.py`, `도메인`은 `tests/test_domain.py`, `일정`은 `tests/test_scheduling.py`를 뜻한다.

| ID · 시나리오 | 5단계에서 실행하는 부분 | 검사 근거 | 아직 남은 부분 |
|---|---|---|---|
| S01 · 예산이 빠진 첫 상담 | 예산이 없으면 검색을 `incomplete`로 반환하고 충족 후보를 내놓지 않음 | 통합 `test_missing_budget_is_incomplete_and_never_produces_eligible_recommendations` | 자연어에서 빠진 예산을 알아내고 질문하는 Zoro 대화 |
| S02 · 접근성 필수와 소음 선호 구분 | 필수 접근성은 통과 조건으로, 소음 선호는 순위 점수로 처리; 시끄러운 매물을 선호만으로 제외하지 않음 | 통합 `test_noise_preference_does_not_become_an_unrequested_hard_constraint` | 고객 발화를 조건·선호로 추출하고 동의를 확인하는 대화 |
| S03 · 예산 상한 경계 | 10억 원인 L004 포함, 10억 10만 원인 L005 제외; 원 단위 정수 검증 | 통합 `test_search_enforces_hard_boundaries_and_preserves_unknown_evidence`, `test_invalid_conditions_leave_every_persisted_field_unchanged` | 자연어 금액 표현 해석은 미구현 |
| S04 · 계약 완료 제외 | L007·L016 제외; 상태 변경 시 검색을 재계산하고 대기 확인 작업 취소 | 통합 `test_contracting_a_listing_cancels_pending_tasks_and_recalculates_search` | 실제 매물의 계약 상태를 수집·확인하는 연결 |
| S05 · 입주일·면적 경계 | 입주 마감일 당일과 75㎡는 허용; 늦은 날짜와 74.99㎡는 제외 | 통합 `test_search_enforces_hard_boundaries_and_preserves_unknown_evidence` | 실제 매물의 면적·입주 가능일 검증 |
| S06 · 엘리베이터와 무단차 구분 | 엘리베이터가 있어도 무단차가 미확인이면 확인 필요로 분류; 미확인은 `null` 유지 | 통합 `test_confirming_one_field_does_not_invent_other_facts`, `test_explicit_unknown_accessibility_requirement_is_not_false` | Zoro가 적절한 현장 확인 질문을 자율적으로 선택하는 과정 |
| S07 · 맞는 후보 없음 | 빈 결과를 그대로 반환하고 예산·면적·접근성을 자동 완화하지 않음 | 통합 `test_no_matching_candidates_does_not_relax_conditions` | 어떤 조건을 조정해도 되는지 묻고 합의하는 대화 |
| S08 · 현장 확인 후 재검토 | L001의 `step_free_access=false` 확인 결과를 저장하면 즉시 제외하고 기존 검색 재계산 | 통합 `test_confirmed_false_accessibility_recalculates_search_and_retains_evidence`, `test_eight_entry_steps_alone_does_not_disprove_an_unknown_alternate_route` | Zoro의 변경 설명·대안 안내; 실제 우회 경로 확인. 계단 8개만으로 무단차 불가를 추론하지 않음 |
| S09 · 확인 전 정보 유지 | 요청 생성만으로 매물 사실을 바꾸지 않음; `null`·문자열 등 미확정/잘못된 결과는 거부하고 대기 상태 유지 | 통합 `test_creating_and_deduplicating_pending_tasks_is_not_confirmed_evidence`, `test_unconfirmed_or_malformed_observation_is_atomic_and_remains_pending` | 자유형 메시지의 확인 여부 판단 |
| S10 · 무단차 확인 후 보류 해제 | L009의 무단차를 명시적으로 확인하면 모든 필수 조건을 다시 검사; 소음·계단 수는 미확인 유지 | 통합 `test_confirming_one_field_does_not_invent_other_facts` | Zoro가 결과를 해석하고 추가 확인 여부를 설명하는 과정 |
| S11 · 예산 변경으로 과거 결과 무효화 | 조건 저장 시 이전 검색과 대안을 무효화; 다시 검색하면 새 예산으로만 분류 | 통합 `test_conditions_change_invalidates_search_and_proposals_before_explicit_research` | 발화에서 조건 변경을 알아내고 검색 도구를 선택하는 과정 |
| S12 · 상충하는 현장 정보 | 새 확인 요청은 기존 확인 사실을 덮어쓰지 않음; 완료된 작업을 다시 제출할 수 없고 새 작업이 필요 | 통합 `test_completed_task_cannot_be_silently_rewritten_but_new_confirmation_can_be_requested` | **부분 구현.** 충돌 주장 전용 입력·충돌 표시·후보 보류 전환·사람에게 재확인 요청하는 전체 흐름은 미구현 |
| S13 · 입주 지연과 연관 일정 | 입주 가능 시각이 15:00으로 늦어지면 기존 청소·이사를 유지한 채 의존성 충돌 표시; 대안 계산은 별도 실행 | 통합 `test_delayed_entry_exposes_dependency_conflicts_without_moving_later_events` | 자연어 변경 해석과 Zoro의 대안 제시. 원 시나리오의 15:30에도 같은 규칙 적용 |
| S14 · 같은 날 업체 시간 없음 | 공급된 슬롯으로만 대안 계산; 당일 대안이 없으면 다음 날 슬롯을 제시할 수 있으나 자동 적용하지 않음; 가능한 슬롯이 전혀 없으면 빈 대안 | 통합 `test_next_day_options_require_explicit_selection_and_do_not_invent_same_day_slots`, `test_no_available_provider_slots_returns_no_alternatives` | 다음 날 이동 허용 여부를 대화로 질문하는 단계; 실제 업체 가용 시간 조회 |
| S15 · 선택한 가상 일정만 반영 | 저장된 대안 ID를 명시적으로 선택해야 적용; 이전 버전·없는 대안은 거부 | 통합 `test_proposals_use_listed_fixture_slots_and_apply_only_after_explicit_selection`, `test_stale_or_unknown_schedule_proposal_cannot_be_applied` | 업체 변경 요청 초안 생성은 미구현. 외부 발송·예약은 하지 않음 |
| S16 · 청소 종료와 이사 시작 경계 | 종료와 시작이 같은 시각은 허용; 의존성·작업 길이·업체 슬롯 안의 배치를 검증 | 일정 경계·의존성 단위 검사; 통합 대안 검사의 독립적인 시각·길이·슬롯 검증 | 실제 업체의 준비·이동 시간 정책은 별도 입력 필요 |
| S17 · 실거래 0건 | 주입한 MOLIT 응답의 0건을 그대로 표시; 가상 거래로 채우지 않음 | `tests/test_molit.py::test_empty_and_beyond_last_page_are_not_parse_errors`, `tests/test_app.py::test_live_routes_forward_only_the_correct_server_side_key` | 실제 인증 응답으로 동일 경로를 확인하는 작업은 S20 |
| S18 · 인증 오류 표시 | 인증 실패를 안전한 오류로 반환; 키를 노출하거나 샘플로 바꾸지 않음 | `tests/test_molit.py::test_provider_codes_have_safe_errors`, `tests/test_app.py::test_known_upstream_failure_keeps_safe_error_and_does_not_fallback` | 실제 키·활용 승인 상태 확인은 S20 |
| S19 · 지연·시간 초과 | 주입한 시간 초과를 오류로 반환; 응답·로그에 키를 노출하지 않음 | `tests/test_molit.py::test_transport_exceptions_suppress_secret_details` | 실제 네트워크 지연 상태에서의 운영 관찰 |
| S20 · 두 API 실제 연결 | 서버 키 설정과 명시적 로컬 확인 절차 제공; 설정 여부와 검증 여부를 구분 | 키 설정 자동 검사와 기존 MOLIT 모의 응답 검사; 통합 `test_stage5_preserves_sample_and_live_separation_without_claiming_connections` | **미검증.** 실제 키가 없어 매매·전월세 API 실호출을 수행하지 않음. Zoro도 미연결 |

## 통합 검사의 추가 보장

- 상담별 데이터가 분리되고 앱을 새로 만들어도 같은 SQLite의 상담을 복원한다. 공개 가상 매물 목록과 원본 JSON 파일을 변경하지 않는다.
- `expected_revision`은 정수만 받는다. 불일치하면 409로 거부하며 기존 기록을 덮어쓰지 않는다.
- 잘못된 날짜·금액·필드·확인 결과는 안전한 오류를 반환하고 전체 상태를 그대로 유지한다.
- 현장 작업 ID는 상담별로 제한된다. 다른 상담의 작업을 제출할 수 없다.
- 일정 대안은 서버에 저장된 현재 대안만 선택할 수 있다. 시간 길이·의존성·제공된 슬롯을 검사하며 제안만으로 일정이 바뀌지 않는다.
- 샘플과 실조회 경로를 분리한다. 키가 없는 실조회는 오류이고, 상태 응답은 계속 `mode=preparation`, `zoro.status=not_connected`, `verified=false`를 유지한다.
- HTTP 요청은 JSON·동일 출처 조건을 적용한다. 서버 인증키와 비공개 Zoro 주소를 상담 응답·SQLite·로그에 복사하지 않는다.

## 재현과 판정

프로젝트 폴더에서 의존성 설치가 완료된 Python으로 실행한다.

```bash
python -m pytest tests/test_tools_api.py -q
python -m pytest tests/test_domain.py tests/test_store.py tests/test_scheduling.py -q
python -m pytest -q
```

2026-11-12에 작성한 통합 검사 52개는 `TestClient`와 임시 SQLite를 사용해 통과했다. 이 수치는 20개 상담 시나리오를 Zoro가 해결했다는 뜻이 아니다. 전체 검사 수와 브라우저 확인 결과는 최종 검증 기록을 별도로 확인한다.

구분해야 할 결과는 세 가지다: **도구의 오프라인 동작 검증**, **인증키로 수행한 MOLIT 실호출 검증**, **Zoro를 연결한 대화·판단 검증**. 현재 이 문서가 입증하는 범위는 첫 번째이며, 나머지 두 가지를 완료로 표시하지 않는다.
