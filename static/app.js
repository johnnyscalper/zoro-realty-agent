/* Preparation UI: deterministic filters and API data display, never a mock AI chat. */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  health: null,
  listings: [],
  preparation: null,
  transactions: null,
  transactionQuery: null,
  transactionBusy: false,
  listingController: null,
  transactionController: null,
};
const labels = {
  status: {available: "시연상 거래 가능", verification_required: "확인 필요", contracted: "계약 완료"},
  category: {consultation: "기본 상담", reverification: "매물 재검증", schedule: "일정 복구", api: "API 조회"},
  stage: {offline: "코드 검증 대상", zoro_required: "Zoro 연결 후 검증", live_api_required: "실제 API 검증 필요"},
  event: {entry: "입주 가능", cleaning: "입주 청소", moving: "이삿짐 반입"},
  field: {elevator: "엘리베이터", step_free_access: "무계단 접근", entry_steps: "입구 계단", noise_level: "소음", available_from: "입주 가능일", status: "매물 상태"},
};

const scenarioDescriptions = {
  S01: "부모님이 거주할 전셋집을 찾지만 예산 등 필요한 조건을 아직 말하지 않은 상황입니다.",
  S02: "계단 없는 이동은 필수이고, 조용한 집은 선호 조건인 고객을 상담합니다.",
  S03: "보증금 상한과 정확히 같은 매물은 포함하고, 상한을 넘는 매물은 제외해야 합니다.",
  S04: "후보 중 이미 계약된 매물을 거래 가능한 집으로 안내하지 않아야 합니다.",
  S05: "최소 면적과 입주 기한에 정확히 맞는 후보를 검사합니다.",
  S06: "엘리베이터는 있지만, 도로에서 현관까지 계단 없이 갈 수 있는지는 아직 확인하지 않았습니다.",
  S07: "예산·면적·접근성을 모두 충족하는 후보가 없는 상황입니다.",
  S08: "추천했던 집의 입구에 계단 8개가 있고 우회 경로도 없다는 현장 결과가 들어옵니다.",
  S09: "현장 확인을 아직 하지 못했다는 응답을 받았습니다.",
  S10: "보류 중인 집의 무계단 접근은 확인됐지만, 소음 등 다른 정보는 여전히 미확인입니다.",
  S11: "고객이 보증금 상한을 10억 원에서 8억 원으로 낮춥니다.",
  S12: "기존 확인 기록과 새로운 메모의 접근성 정보가 서로 다릅니다.",
  S13: "입주 가능 시간이 오후 3시로 늦어져 기존 청소·이사 일정에 영향을 줍니다.",
  S14: "당일 가능한 업체 시간이 없어 다음 날로 옮길 수 있는지 고객 확인이 필요합니다.",
  S15: "고객이 제시된 일정 중 하나를 선택합니다. 수정 대상은 시연용 내부 일정표입니다.",
  S16: "청소 종료 시각과 이사 시작 시각이 같은 경우에 선후 관계를 검사합니다.",
  S17: "국토부 조회는 정상적으로 끝났지만 해당 월·지역의 신고 거래가 0건입니다.",
  S18: "국토부가 인증 오류를 반환합니다. 키를 노출하지 않고 원인을 안내해야 합니다.",
  S19: "국토부 응답이 제한 시간 안에 도착하지 않는 상황입니다.",
  S20: "실제 키를 설정한 뒤 매매·전월세 API를 각각 확인할 예정입니다. 현재는 검증 전입니다.",
};

const actionLabels = {
  ask_missing_constraints: "빠진 예산·면적·입주일 등 필요한 조건을 질문합니다.",
  retain_hard_constraints: "고객이 정한 필수 조건을 그대로 유지합니다.",
  exclude_candidate: "필수 조건을 충족하지 않는 후보를 제외합니다.",
  explain_preference_tradeoff: "선호 조건 중 충족하는 점과 아쉬운 점을 설명합니다.",
  include_budget_boundary: "예산 상한과 정확히 같은 후보를 포함합니다.",
  exclude_over_budget: "예산을 초과하는 후보를 제외합니다.",
  exclude_contracted_candidates: "계약 완료된 후보를 제외합니다.",
  include_exact_boundaries: "면적·입주일 기준에 정확히 맞는 후보를 포함합니다.",
  exclude_insufficient_area: "최소 면적을 충족하지 않는 후보를 제외합니다.",
  exclude_late_move_in: "입주 기한보다 늦게 입주할 수 있는 후보를 제외합니다.",
  mark_candidate_pending: "후보를 확인이 필요한 보류 상태로 표시합니다.",
  create_inspection_task: "현장에서 확인할 작업을 생성합니다.",
  report_no_matching_candidates: "현재 조건을 모두 충족하는 후보가 없음을 알립니다.",
  ask_which_constraint_may_change: "어떤 조건을 조정할 수 있는지 고객에게 묻습니다.",
  invalidate_previous_recommendation: "새로운 정보로 이전 추천을 다시 검토해야 한다고 표시합니다.",
  search_alternatives: "다른 후보를 다시 찾습니다.",
  keep_verification_pending: "확인이 끝날 때까지 미확인 상태를 유지합니다.",
  request_human_verification: "중개사에게 직접 확인할 작업을 요청합니다.",
  record_inspection_evidence: "현장 확인 결과와 확인 시점을 기록합니다.",
  recheck_all_hard_constraints: "모든 필수 조건을 다시 검사합니다.",
  retain_unverified_noise_status: "아직 확인하지 않은 소음 정보는 미확인으로 남깁니다.",
  update_customer_constraints: "고객이 변경한 조건을 반영합니다.",
  search_candidates_again: "변경된 조건으로 후보를 다시 검색합니다.",
  flag_conflicting_evidence: "서로 맞지 않는 확인 기록을 표시합니다.",
  detect_dependency_conflicts: "입주·청소·이사의 순서와 시간 충돌을 찾습니다.",
  propose_feasible_slots: "입력된 가능 시간 안에서 실행 가능한 대안을 제시합니다.",
  ask_user_selection: "어떤 대안을 사용할지 고객에게 선택을 요청합니다.",
  report_no_same_day_plan: "같은 날 실행할 수 있는 일정이 없음을 알립니다.",
  ask_if_next_day_allowed: "다음 날로 옮겨도 되는지 고객에게 묻습니다.",
  validate_selected_plan: "선택한 일정의 선후 관계와 충돌 여부를 확인합니다.",
  update_internal_schedule: "사용자가 선택한 안으로 내부 일정표를 수정합니다.",
  draft_provider_change_requests: "업체에 보낼 일정 변경 요청 초안을 작성합니다.",
  accept_equal_boundary_if_other_dependencies_hold: "다른 선행 조건도 맞으면 종료·시작 시각이 같은 일정을 허용합니다.",
  display_empty_transactions: "조회 결과가 0건임을 표시합니다.",
  retain_molit_source_label: "국토부 조회 결과라는 출처를 유지합니다.",
  surface_safe_auth_error: "인증 오류를 비밀 정보 없이 안내합니다.",
  suggest_checking_key_and_api_approval: "키 설정과 해당 API의 활용 승인을 확인하도록 안내합니다.",
  surface_timeout_error: "응답 대기 시간이 초과됐음을 안내합니다.",
  offer_explicit_retry: "사용자가 직접 다시 조회할 수 있게 합니다.",
  verify_each_api_with_authorized_key: "승인된 키로 매매·전월세 API를 각각 확인합니다.",
  record_real_response_status_and_retrieved_at: "실제 응답 상태와 조회 시각을 기록합니다.",
  label_historical_transactions: "조회 자료가 과거 신고 거래임을 표시합니다.",
  invent_budget: "고객의 예산을 임의로 만들지 않습니다.",
  recommend_as_verified_without_constraints: "조건을 모르는 상태에서 검증된 추천이라고 안내하지 않습니다.",
  treat_preference_as_hard_without_confirmation: "선호를 확인 없이 필수 조건으로 바꾸지 않습니다.",
  relax_step_free_requirement: "계단 없는 이동이라는 필수 조건을 완화하지 않습니다.",
  round_over_budget_into_range: "금액을 반올림해서 예산 초과 매물을 포함하지 않습니다.",
  recommend_contracted_as_available: "계약 완료 매물을 거래 가능한 매물로 추천하지 않습니다.",
  round_area_up_to_pass: "면적을 올림해서 기준에 맞는 것처럼 처리하지 않습니다.",
  ignore_move_in_deadline: "고객의 입주 기한을 무시하지 않습니다.",
  assume_step_free_from_elevator: "엘리베이터가 있다는 이유만으로 무계단 접근이 가능하다고 판단하지 않습니다.",
  treat_unknown_as_false: "미확인 정보를 확인된 불가 정보로 바꾸지 않습니다.",
  silently_raise_budget: "동의 없이 예산을 늘리지 않습니다.",
  silently_reduce_area: "동의 없이 최소 면적을 줄이지 않습니다.",
  invent_new_listing: "존재하지 않는 후보를 만들어내지 않습니다.",
  keep_recommendation_despite_hard_violation: "필수 조건을 위반하는데도 이전 추천을 유지하지 않습니다.",
  hide_changed_evidence: "새로 확인된 정보를 숨기지 않습니다.",
  claim_inspection_completed: "현장 확인이 끝나지 않았는데 완료됐다고 말하지 않습니다.",
  invent_accessibility_fact: "확인하지 않은 접근성 정보를 만들어내지 않습니다.",
  mark_all_fields_verified_from_one_inspection: "한 항목을 확인했다고 다른 항목까지 확인 완료로 표시하지 않습니다.",
  reuse_stale_recommendations: "변경 전 조건으로 만든 추천을 그대로 재사용하지 않습니다.",
  silently_keep_old_budget: "이전 예산을 몰래 유지하지 않습니다.",
  silently_overwrite_confirmed_fact: "확인된 사실을 새 메모만으로 덮어쓰지 않습니다.",
  present_conflict_as_resolved: "정보가 충돌하는데 해결됐다고 안내하지 않습니다.",
  move_before_entry: "집에 들어갈 수 있는 시간보다 먼저 이사를 배치하지 않습니다.",
  clean_before_entry: "집에 들어갈 수 있는 시간보다 먼저 청소를 배치하지 않습니다.",
  confirm_provider_booking: "실제 업체 예약이 확정됐다고 말하지 않습니다.",
  invent_provider_availability: "입력되지 않은 업체 가능 시간을 만들어내지 않습니다.",
  change_move_date_without_user_choice: "고객 선택 없이 이사 날짜를 바꾸지 않습니다.",
  send_external_message: "외부 업체에 메시지를 발송하지 않습니다.",
  claim_actual_booking_confirmed: "내부 일정 수정만으로 실제 예약이 확정됐다고 말하지 않습니다.",
  allow_moving_before_cleaning_finishes: "청소가 끝나기 전에 이사를 시작하도록 배치하지 않습니다.",
  double_book_same_event: "같은 작업을 중복 예약하지 않습니다.",
  fill_missing_records_with_synthetic_data: "없는 실제 거래를 가상 자료로 채우지 않습니다.",
  claim_no_current_listings: "신고 거래가 0건이라는 이유로 현재 매물이 없다고 말하지 않습니다.",
  print_service_key: "API 키를 화면이나 로그에 표시하지 않습니다.",
  retry_auth_failure_indefinitely: "인증 오류 요청을 무한히 반복하지 않습니다.",
  fallback_to_sample_in_live_mode: "실제 조회가 실패했을 때 가상 응답으로 대체하지 않습니다.",
  hang_indefinitely: "응답을 무기한 기다리지 않습니다.",
  present_stale_or_fake_records_as_fresh: "오래되거나 가상인 자료를 새 실제 자료처럼 보여주지 않습니다.",
  claim_live_test_passed_without_keys: "실제 키로 확인하지 않았는데 검증을 통과했다고 말하지 않습니다.",
  claim_transactions_are_current_inventory: "실거래 자료를 현재 거래 가능한 매물이라고 안내하지 않습니다.",
  expose_keys_to_browser: "API 키를 브라우저에 전달하지 않습니다.",
};

function actionText(code) {
  const [action, target] = String(code).split(":", 2);
  const text = actionLabels[action] || "정의된 행동을 확인합니다. 원문은 아래 입력 데이터에서 볼 수 있습니다.";
  if (!target) return text;
  return `${text} (${labels.field[target] || `가상 매물 ${target}`})`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function won(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "미확인";
  const amount = Number(value);
  if (amount === 0) return "0원";
  const eok = Math.floor(amount / 100000000);
  const man = Math.floor((amount % 100000000) / 10000);
  const remainder = amount % 10000;
  const parts = [];
  if (eok) parts.push(`${eok.toLocaleString("ko-KR")}억`);
  if (man) parts.push(`${man.toLocaleString("ko-KR")}만`);
  if (remainder) parts.push(remainder.toLocaleString("ko-KR"));
  return `${parts.join(" ")} 원`;
}

function dateText(value) {
  if (!value) return "미확인";
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text.replaceAll("-", ".");
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return "미확인";
  return date.toLocaleString("ko-KR", {timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false});
}

function timeText(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "미확인";
  return date.toLocaleTimeString("ko-KR", {timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false});
}

function truthText(value, yes, no) { return value === true ? yes : value === false ? no : "미확인"; }
function showError(selector, message) {
  const node = $(selector);
  node.textContent = message || "";
  node.classList.toggle("hidden", !message);
}
function emptyState(title, description) {
  const box = el("div", "empty-state");
  box.append(el("span", "empty-symbol", "◇"), el("h3", "", title), el("p", "", description));
  return box;
}

async function getJSON(path, signal) {
  let response;
  try {
    response = await fetch(path, {headers: {Accept: "application/json"}, signal, cache: "no-store"});
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("서버에 연결하지 못했습니다. 프로그램 실행 상태를 확인하고 다시 시도하세요.");
  }
  let data;
  try { data = await response.json(); }
  catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("서버 응답을 읽지 못했습니다. 잠시 후 다시 시도하세요.");
  }
  if (!response.ok) {
    const message = data?.error?.message;
    const error = new Error(typeof message === "string" ? message : "요청을 처리하지 못했습니다. 입력값과 서버 설정을 확인하세요.");
    error.code = data?.error?.code;
    throw error;
  }
  return data;
}

async function loadHealth() {
  const refresh = $("#refresh-health");
  refresh.disabled = true;
  showError("#health-error", "");
  try {
    const health = await getJSON("/api/health");
    state.health = health;
    $("#listing-count").textContent = health.counts?.listings ?? "—";
    $("#scenario-count").textContent = health.counts?.scenarios ?? "—";
    const trade = health.molit?.trade_key_configured === true;
    const rent = health.molit?.rent_key_configured === true;
    const keyCount = Number(trade) + Number(rent);
    $("#molit-status").replaceChildren(el("span", `status-dot ${keyCount ? "" : "amber"}`), document.createTextNode(keyCount === 2 ? "키 설정됨" : keyCount === 1 ? "키 일부 설정됨" : "키 설정 대기"));
    $("#molit-detail").textContent = keyCount ? `매매 ${trade ? "설정" : "미설정"} · 전월세 ${rent ? "설정" : "미설정"} / 실제 조회 별도 검증` : "가상 응답 조회는 바로 사용할 수 있습니다.";
    $("#zoro-detail").textContent = health.zoro?.configured ? "서버 주소 설정됨 · 실제 연결·판단 미검증" : "실제 모델 판단은 아직 실행하지 않습니다.";
  } catch (error) {
    state.health = null;
    $("#molit-status").textContent = "상태 확인 실패";
    $("#molit-detail").textContent = "새로고침으로 다시 확인하세요.";
    showError("#health-error", error.message);
  } finally {
    refresh.disabled = false;
    updateTransactionControls();
  }
}

async function loadListings() {
  if (state.listingController) state.listingController.abort();
  const controller = new AbortController();
  state.listingController = controller;
  const form = $("#listing-form");
  const query = new URLSearchParams();
  for (const [key, value] of new FormData(form)) if (String(value).trim()) query.set(key, value);
  $("#listing-submit").disabled = true;
  $("#listing-grid").setAttribute("aria-busy", "true");
  $("#listing-result").textContent = "조건에 맞는 자료를 불러오고 있습니다.";
  showError("#listing-error", "");
  try {
    const payload = await getJSON(`/api/listings?${query}`, controller.signal);
    if (state.listingController !== controller) return;
    if (payload.synthetic !== true || !Array.isArray(payload.listings)) throw new Error("가상 매물 자료 형식을 확인할 수 없습니다.");
    state.listings = payload.listings;
    renderListings();
  } catch (error) {
    if (error.name === "AbortError" || state.listingController !== controller) return;
    $("#listing-grid").replaceChildren(emptyState("자료를 불러오지 못했습니다", "연결 상태를 확인한 뒤 조건 적용을 다시 눌러주세요."));
    $("#listing-result").textContent = "조회 실패";
    showError("#listing-error", error.message);
  } finally {
    if (state.listingController === controller) {
      $("#listing-submit").disabled = false;
      $("#listing-grid").setAttribute("aria-busy", "false");
    }
  }
}

function renderListings() {
  const grid = $("#listing-grid");
  grid.replaceChildren();
  $("#listing-result").textContent = `총 ${state.listings.length}개 · 조건에 맞는 가상 매물`;
  if (!state.listings.length) {
    grid.append(emptyState("조건에 맞는 시연용 매물이 없습니다", "입력한 조건을 직접 조정해 다시 조회하세요. 조건은 자동으로 완화하지 않습니다."));
    return;
  }
  state.listings.forEach((listing) => {
    const card = el("article", "listing-card");
    const top = el("div", "card-top");
    top.append(el("span", "card-neighborhood", `강남구 ${listing.neighborhood || ""}`), el("span", `pill ${listing.status}`, labels.status[listing.status] || "미확인"));
    const body = el("div", "card-main");
    body.append(el("h3", "", listing.name));
    const price = el("p", "deposit");
    price.append(el("small", "", "전세"), document.createTextNode(won(listing.deposit_krw)));
    const facts = el("div", "card-facts");
    facts.append(el("span", "", `전용 ${listing.area_m2}㎡`), el("span", "", `${listing.floor}층`));
    const checks = el("div", "card-checks");
    checks.append(
      el("span", `check-chip ${listing.elevator == null ? "unknown" : ""}`, `승강기 ${truthText(listing.elevator, "있음", "없음")}`),
      el("span", `check-chip ${listing.step_free_access == null ? "unknown" : ""}`, `무계단 ${truthText(listing.step_free_access, "가능", "불가")}`),
    );
    body.append(price, facts, checks);
    const bottom = el("div", "card-bottom");
    const button = el("button", "detail-button", "확인 항목 보기 ↗");
    button.type = "button";
    button.setAttribute("aria-label", `${listing.name} 현장 확인 항목 보기`);
    button.addEventListener("click", () => openDetail(listing));
    bottom.append(el("span", "", `입주 ${dateText(listing.available_from)}`), button);
    card.append(top, body, bottom);
    grid.append(card);
  });
}

function openDetail(listing) {
  const root = $("#listing-detail");
  root.replaceChildren();
  const heading = el("div", "detail-heading");
  const title = el("h2", "", listing.name);
  title.id = "detail-title";
  heading.append(title, el("span", "pill synthetic", "가상 매물"));
  root.append(heading, el("p", "detail-subtitle", `강남구 ${listing.neighborhood} · ${labels.status[listing.status] || "상태 미확인"}`));
  const fields = el("div", "detail-grid");
  const noise = {low: "낮음", medium: "보통", high: "높음"};
  const pairs = [
    ["전세 보증금", won(listing.deposit_krw)], ["전용면적 / 층", `${listing.area_m2}㎡ / ${listing.floor}층`],
    ["엘리베이터", truthText(listing.elevator, "있음", "없음")], ["무계단 접근", truthText(listing.step_free_access, "가능", "불가")],
    ["입구 계단", listing.entry_steps == null ? "미확인" : `${listing.entry_steps}개`], ["소음", noise[listing.noise_level] || "미확인"],
    ["입주 가능일", dateText(listing.available_from)], ["자료 확인 시점", dateText(listing.verified_at)],
  ];
  pairs.forEach(([label, value]) => {const field = el("div", "detail-field"); field.append(el("span", "", label), el("strong", "", value)); fields.append(field);});
  root.append(fields, el("p", "detail-note", listing.notes || "추가 메모가 없습니다."));
  root.append(el("h3", "detail-section-title", "현장 확인 자료"));
  if (!state.preparation) {
    root.append(el("p", "detail-note", "현장 확인 자료를 아직 불러오지 못했습니다. 시연 설계 탭에서 상태를 확인하세요."));
  } else {
    const inspections = state.preparation.inspections?.inspections?.filter((x) => x.listing_id === listing.id) || [];
    if (!inspections.length) root.append(el("p", "detail-note", "등록된 현장 확인 자료가 없습니다. 미확인 정보는 사실로 간주하지 않습니다."));
    inspections.forEach((inspection) => {
      const item = el("div", "inspection");
      const top = el("div", "inspection-heading", labels.field[inspection.field] || inspection.field);
      top.append(el("span", `pill ${inspection.status === "confirmed" ? "available" : "verification_required"}`, inspection.status === "confirmed" ? "시연상 확인됨" : "미확인"));
      const value = typeof inspection.observed_value === "object" ? JSON.stringify(inspection.observed_value) : String(inspection.observed_value ?? "기록 없음");
      item.append(top, el("p", "", `입력값: ${value} · ${dateText(inspection.observed_at)}`), el("p", "", inspection.notes || ""));
      root.append(item);
    });
  }
  root.append(el("div", "notice info", "읽기 전용 준비 자료입니다. 이 화면에서는 추천 판단이나 확인 결과 변경을 실행하지 않습니다."));
  $("#listing-dialog").showModal();
}

function transactionMode() { return $("input[name='mode']:checked").value; }
function updateTransactionControls() {
  const mode = transactionMode();
  const kind = $("#transaction-kind").value;
  const keyConfigured = state.health?.molit?.[`${kind}_key_configured`] === true;
  $("#transaction-submit").disabled = state.transactionBusy || (mode === "live" && !keyConfigured);
  $("#transaction-submit").textContent = state.transactionBusy ? "조회 중…" : mode === "sample" ? "가상 응답 조회 ↗" : keyConfigured ? "국토부 실제 조회 ↗" : "서버에 키 설정 필요";
  $("#mode-description").textContent = mode === "sample"
    ? "API 키 없이 동작하는 가상 응답입니다. 실제 거래 가격으로 사용하지 마세요."
    : keyConfigured
      ? "서버에 설정된 키로 국토부에 요청합니다. 실패하면 오류를 표시하며 가상 자료로 바꾸지 않습니다."
      : "선택한 API 키를 서버의 .env 파일에 설정하고 프로그램을 재시작한 뒤, 오른쪽 위 새로고침을 눌러주세요. 키는 이 화면에 입력하지 않습니다.";
  $("#previous-page").disabled = state.transactionBusy || !state.transactions || state.transactions.page_no <= 1;
  $("#next-page").disabled = state.transactionBusy || !state.transactions?.has_next;
}

function clearTransactions() {
  if (state.transactionController) state.transactionController.abort();
  state.transactionController = null;
  state.transactions = null;
  state.transactionQuery = null;
  state.transactionBusy = false;
  $("#transaction-result").setAttribute("aria-busy", "false");
  $("#transaction-result").replaceChildren(emptyState("조회할 자료를 선택하세요", "조회 버튼을 눌러 선택한 종류와 계약 연월의 자료를 불러오세요."));
  $("#transaction-pagination").classList.add("hidden");
  showError("#transaction-error", "");
  updateTransactionControls();
}

async function loadTransactions(pageNo = 1, reuseQuery = false) {
  if (!$("#transaction-form").reportValidity()) return;
  if (state.transactionController) state.transactionController.abort();
  const controller = new AbortController();
  state.transactionController = controller;
  const query = reuseQuery && state.transactionQuery
    ? new URLSearchParams(state.transactionQuery)
    : new URLSearchParams({mode: transactionMode(), kind: $("#transaction-kind").value, lawd_cd: $("#lawd-cd").value, deal_ymd: $("#deal-month").value.replace("-", ""), num_of_rows: "20"});
  query.set("page_no", String(pageNo));
  state.transactionBusy = true;
  state.transactions = null;
  $("#transaction-result").setAttribute("aria-busy", "true");
  $("#transaction-result").replaceChildren(emptyState("자료를 조회하고 있습니다", query.get("mode") === "live" ? "국토부 응답을 기다리는 중입니다." : "가상 응답 예시를 불러오는 중입니다."));
  $("#transaction-pagination").classList.add("hidden");
  showError("#transaction-error", "");
  updateTransactionControls();
  try {
    const result = await getJSON(`/api/transactions?${query}`, controller.signal);
    if (state.transactionController !== controller) return;
    if (!Array.isArray(result.items) || (query.get("mode") === "live" && (result.synthetic !== false || result.source !== "molit")) || (query.get("mode") === "sample" && result.synthetic !== true)) throw new Error("선택한 조회 방식과 응답 출처가 일치하지 않아 표시하지 않았습니다.");
    state.transactions = result;
    state.transactionQuery = query.toString();
    renderTransactions(result);
  } catch (error) {
    if (error.name === "AbortError" || state.transactionController !== controller) return;
    $("#transaction-result").replaceChildren(emptyState("조회가 완료되지 않았습니다", "오류를 확인한 뒤 다시 시도하세요. 가상 자료로 대체하지 않았습니다."));
    showError("#transaction-error", error.message);
  } finally {
    if (state.transactionController === controller) {
      state.transactionBusy = false;
      $("#transaction-result").setAttribute("aria-busy", "false");
      updateTransactionControls();
    }
  }
}

function renderTransactions(result) {
  const root = $("#transaction-result");
  root.replaceChildren();
  const synthetic = result.synthetic === true;
  const meta = el("div", "transaction-meta");
  const title = el("h3", "", `${result.returned_count ?? result.items.length}건 조회`);
  title.append(el("span", `pill ${synthetic ? "synthetic" : "available"}`, synthetic ? "가상 응답 예시" : "국토부 실제 응답"));
  meta.append(title, el("span", "", `조회 시각 ${dateText(result.retrieved_at)} · KST`));
  root.append(meta);
  if (!result.items.length) {
    if (result.skipped_count > 0) {
      root.append(emptyState("응답 자료를 표시하지 못했습니다", `형식 오류 등으로 ${result.skipped_count}건이 제외되었습니다. 신고 자료가 없는 경우와 다릅니다. 아래 경고와 다른 페이지를 확인하세요.`));
    } else if (result.total_count > 0) {
      root.append(emptyState("이 페이지에 표시할 거래가 없습니다", "전체 조회 결과는 존재합니다. 다른 페이지를 확인하거나 조회를 다시 시도하세요."));
    } else {
      root.append(emptyState("조회된 거래가 없습니다", "조회 월과 지역을 확인하세요. 신고 자료가 없다는 뜻이며, 현재 매물이 없다는 뜻은 아닙니다."));
    }
  } else {
    const wrapper = el("div", "table-wrapper");
    wrapper.tabIndex = 0;
    wrapper.setAttribute("aria-label", "실거래 조회 표, 좁은 화면에서는 좌우로 스크롤할 수 있습니다");
    const table = el("table");
    const caption = el("caption", "sr-only", synthetic ? "가상 실거래 응답 예시" : "국토부 신고 실거래 자료");
    const head = el("thead");
    const headRow = el("tr");
    ["아파트 / 법정동", "계약일", "면적 / 층", result.kind === "trade" ? "매매금액" : "보증금 / 월세", "거래 구분"].forEach((value) => {const th = el("th", "", value); th.scope = "col"; headRow.append(th);});
    head.append(headRow);
    const body = el("tbody");
    result.items.forEach((item) => {
      const row = el("tr");
      const name = el("td"); name.append(el("strong", "", item.apartment_name || "명칭 미확인"), el("small", "", item.legal_dong || "법정동 미확인"));
      const date = el("td", "", dateText(item.contract_date));
      const area = el("td"); area.append(el("strong", "", `${item.area_m2 ?? "—"}㎡`), el("small", "", item.floor == null ? "층 미확인" : `${item.floor}층`));
      const price = el("td", "money-cell", won(result.kind === "trade" ? item.price_krw : item.deposit_krw));
      if (result.kind === "rent") price.append(el("small", "", `월세 ${won(item.monthly_rent_krw)}`));
      const status = el("td");
      const type = {trade: "매매", jeonse: "전세", monthly_rent: "월세"}[item.lease_type] || "미확인";
      status.append(el("span", `pill ${item.cancelled ? "contracted" : ""}`, item.cancelled ? "해제 신고" : type));
      if (item.cancelled_date) status.append(el("small", "", dateText(item.cancelled_date)));
      if (result.kind === "rent" && item.cancellation_status_available !== true && !item.cancelled) status.append(el("small", "", "해제 여부 확인 불가"));
      row.append(name, date, area, price, status);
      body.append(row);
    });
    table.append(caption, head, body); wrapper.append(table); root.append(wrapper);
  }
  const warnings = el("ul", "transaction-warnings");
  if (synthetic) warnings.append(el("li", "", "이 표의 명칭과 금액은 화면·조회 기능 검증을 위해 만든 가상 자료입니다."));
  (result.warnings || []).forEach((warning) => warnings.append(el("li", "", warning)));
  if (result.skipped_count) warnings.append(el("li", "", `정규화하지 못한 항목 ${result.skipped_count}건이 제외되었습니다.`));
  root.append(warnings);
  $("#page-status").textContent = `${result.page_no}페이지 · 전체 ${result.total_count}건`;
  $("#transaction-pagination").classList.toggle("hidden", !result.items.length && !result.has_next && result.page_no <= 1);
}

async function loadPreparation() {
  showError("#preparation-error", "");
  try {
    state.preparation = await getJSON("/api/preparation");
    renderSchedules();
    renderScenarios();
  } catch (error) {
    showError("#preparation-error", error.message);
    $("#schedule-list").replaceChildren(emptyState("준비 자료를 불러오지 못했습니다", "프로그램 연결 상태를 확인한 뒤 페이지를 새로고침하세요."));
  }
}

function renderSchedules() {
  const root = $("#schedule-list");
  root.replaceChildren();
  const events = state.preparation?.schedules?.events || [];
  if (!events.length) {root.append(emptyState("등록된 일정이 없습니다", "시연용 일정 자료를 확인하세요.")); return;}
  events.forEach((event) => {
    const item = el("article", "schedule-item");
    const range = event.start === event.end ? timeText(event.start) : `${timeText(event.start)} — ${timeText(event.end)}`;
    item.append(el("strong", "", labels.event[event.type] || event.type), el("span", "schedule-time", range), el("small", "", `${dateText(state.preparation.schedules.date)} · KST · 시연용 입력`));
    const dependencies = (event.depends_on || []).map((id) => labels.event[events.find((x) => x.id === id)?.type] || id);
    if (dependencies.length) item.append(el("small", "", `선행 조건: ${dependencies.join(", ")}`));
    root.append(item);
  });
}

function renderScenarios() {
  const root = $("#scenario-list");
  root.replaceChildren();
  const filter = $("#scenario-filter").value;
  const scenarios = state.preparation?.scenarios?.scenarios || [];
  scenarios.filter((scenario) => !filter || scenario.category === filter).forEach((scenario) => {
    const item = el("details", "scenario");
    const summary = el("summary");
    summary.append(el("span", "scenario-id", scenario.id), el("span", "scenario-title", scenario.title), el("span", `pill ${scenario.verification_stage}`, labels.stage[scenario.verification_stage] || "검증 대기"));
    const body = el("div", "scenario-body");
    body.append(el("h4", "", `${labels.category[scenario.category] || "시나리오"} · 상황`), el("p", "scenario-description", scenarioDescriptions[scenario.id] || scenario.title));
    const expected = el("ul");
    (scenario.expected_actions || []).forEach((action) => expected.append(el("li", "", actionText(action))));
    const forbidden = el("ul");
    (scenario.must_not || []).forEach((action) => forbidden.append(el("li", "", actionText(action))));
    body.append(el("h4", "", "기대 행동"), expected, el("h4", "never-title", "하면 안 되는 행동"), forbidden);
    const technical = el("details", "scenario-data");
    technical.append(el("summary", "", "입력 데이터 보기"), el("pre", "", JSON.stringify({given: scenario.given, event: scenario.event, expected_actions: scenario.expected_actions, must_not: scenario.must_not}, null, 2)));
    body.append(technical);
    item.append(summary, body); root.append(item);
  });
  if (!root.childElementCount) root.append(emptyState("이 분류의 시나리오가 없습니다", "다른 분류를 선택하세요."));
}

function activateTab(name, focus = false) {
  $$(".nav-tab").forEach((button) => {
    const selected = button.dataset.tab === name;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(`#panel-${button.dataset.tab}`).hidden = !selected;
    if (selected && focus) button.focus();
  });
}

$$(".nav-tab").forEach((button, index, tabs) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
    if (delta) {event.preventDefault(); activateTab(tabs[(index + delta + tabs.length) % tabs.length].dataset.tab, true);}
    if (event.key === "Home" || event.key === "End") {event.preventDefault(); activateTab(tabs[event.key === "Home" ? 0 : tabs.length - 1].dataset.tab, true);}
  });
});
$("#refresh-health").addEventListener("click", loadHealth);
$("#listing-form").addEventListener("submit", (event) => {event.preventDefault(); loadListings();});
$("#listing-reset").addEventListener("click", () => {$("#listing-form").reset(); loadListings();});
$("#transaction-form").addEventListener("submit", (event) => {event.preventDefault(); loadTransactions(1);});
// Typed edits must withdraw the old query before the field loses focus.
$("#transaction-form").addEventListener("input", clearTransactions);
$("#transaction-form").addEventListener("change", clearTransactions);
$("#previous-page").addEventListener("click", () => {if (state.transactions && state.transactions.page_no > 1) loadTransactions(state.transactions.page_no - 1, true);});
$("#next-page").addEventListener("click", () => {if (state.transactions?.has_next) loadTransactions(state.transactions.page_no + 1, true);});
$("#scenario-filter").addEventListener("change", renderScenarios);
$("#close-dialog").addEventListener("click", () => $("#listing-dialog").close());
$("#listing-dialog").addEventListener("click", (event) => {if (event.target === event.currentTarget) {const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.currentTarget.close();}});
updateTransactionControls();
Promise.allSettled([loadHealth(), loadListings(), loadPreparation()]);

/* Stage 5: explicit, local manual tools. Only an opaque session ID is stored in the tab. */
(() => {
  const panel = $("#panel-workbench");
  const sessionKey = "realty-workbench-session-id";
  const sessionIdPattern = /^[A-Za-z0-9_-]{16,80}$/;
  const noiseLabels = {low: "낮음", medium: "보통", high: "높음"};
  const verificationFields = ["entry_steps", "step_free_access", "elevator", "noise_level"];
  const workbench = {session: null, busy: false, blocked: false, controller: null, request: 0, conditionsDirty: false, proposalsChecked: false};
  const defaultConditions = () => ({hard: {max_deposit_krw: 1000000000, min_area_m2: 75, move_in_by: "2026-11-25", step_free_access: true, elevator: null}, preferences: {noise_level: "low", neighborhoods: []}});

  function rememberSession(id) {
    try { sessionStorage.setItem(sessionKey, id); } catch (_) { /* Storage is optional. */ }
  }

  function budgetToWon(raw) {
    const value = raw.trim();
    if (!value) return null;
    if (!/^\d+(?:\.\d{1,8})?$/.test(value)) throw new Error("보증금은 억 원 단위의 양수로 입력하세요. 소수점 아래 8자리까지 원 단위로 정확히 변환합니다.");
    const [whole, fraction = ""] = value.split(".");
    const amount = BigInt(whole) * 100000000n + BigInt(fraction.padEnd(8, "0"));
    if (amount <= 0n || amount > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("보증금은 0원보다 크고 정확히 저장할 수 있는 범위로 입력하세요.");
    return Number(amount);
  }

  function wonToBudget(value) {
    if (value == null) return "";
    const amount = BigInt(value);
    const fraction = (amount % 100000000n).toString().padStart(8, "0").replace(/0+$/, "");
    return `${amount / 100000000n}${fraction ? `.${fraction}` : ""}`;
  }

  function updateBudgetPreview() {
    const input = $("#workbench-budget");
    input.setCustomValidity("");
    try {
      const amount = budgetToWon(input.value);
      $("#workbench-budget-preview").textContent = amount == null ? "예산 미입력 · 조건 보완 필요" : `${amount.toLocaleString("ko-KR")}원`;
    } catch (error) {
      input.setCustomValidity(error.message);
      $("#workbench-budget-preview").textContent = "숫자와 소수점으로 입력하세요 · 최대 소수 8자리";
    }
  }

  function readConditions() {
    updateBudgetPreview();
    if (!$("#workbench-conditions-form").reportValidity()) return null;
    const boolValue = (selector) => $(selector).value === "" ? null : $(selector).value === "true";
    const areaText = $("#workbench-area").value.trim();
    const area = areaText === "" ? null : Number(areaText);
    if (area !== null && (!Number.isFinite(area) || area <= 0)) throw new Error("최소 면적은 0보다 큰 숫자로 입력하거나 비워 두세요.");
    return {hard: {max_deposit_krw: budgetToWon($("#workbench-budget").value), min_area_m2: area, move_in_by: $("#workbench-move-by").value || null, step_free_access: boolValue("#workbench-step-free"), elevator: boolValue("#workbench-elevator")}, preferences: {noise_level: $("#workbench-noise").value || null, neighborhoods: $("#workbench-neighborhoods").value.split(",").map((value) => value.trim()).filter(Boolean)}};
  }

  function fillConditions(conditions) {
    const hard = conditions.hard || {};
    const preferences = conditions.preferences || {};
    $("#workbench-budget").value = wonToBudget(hard.max_deposit_krw);
    $("#workbench-area").value = hard.min_area_m2 ?? "";
    $("#workbench-move-by").value = hard.move_in_by || "";
    const stepFree = $("#workbench-step-free");
    // Preserve an explicitly supplied false condition when resuming an API-created session.
    if (hard.step_free_access === false && !$("option[value='false']", stepFree)) {
      const option = el("option", "", "저장된 조건: 무계단 접근 불가");
      option.value = "false";
      stepFree.append(option);
    }
    stepFree.value = hard.step_free_access == null ? "" : String(hard.step_free_access);
    $("#workbench-elevator").value = hard.elevator == null ? "" : String(hard.elevator);
    $("#workbench-noise").value = preferences.noise_level || "";
    $("#workbench-neighborhoods").value = (preferences.neighborhoods || []).join(", ");
    workbench.conditionsDirty = false;
    $("#workbench-condition-state").textContent = "저장된 조건";
    updateBudgetPreview();
  }

  function updateControls() {
    $$("button, input, select, textarea", panel).forEach((control) => {
      const requiresSession = control.hasAttribute("data-session-action") || control.hasAttribute("data-needs-session");
      const writeBlocked = control.hasAttribute("data-session-action") && workbench.blocked;
      control.disabled = workbench.busy || (requiresSession && !workbench.session) || writeBlocked || control.dataset.unavailable === "true";
    });
    $("#workbench-content").setAttribute("aria-busy", String(workbench.busy));
    $("#workbench-conflict").classList.toggle("hidden", !workbench.blocked);
  }

  async function requestJSON(path, method = "GET", payload) {
    const requestNumber = ++workbench.request;
    const controller = new AbortController();
    workbench.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, {method, headers: {Accept: "application/json", ...(method === "GET" ? {} : {"Content-Type": "application/json"})}, body: payload === undefined ? undefined : JSON.stringify(payload), credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal});
      let data;
      try { data = await response.json(); }
      catch (_) { throw new Error("서버 응답을 읽지 못했습니다. 최신 상태를 불러와 처리 결과를 확인하세요."); }
      if (requestNumber !== workbench.request) throw new Error("이전 요청의 응답을 취소했습니다. 최신 상태를 다시 불러오세요.");
      if (!response.ok) {
        const message = data?.error?.message;
        const error = new Error(typeof message === "string" && message.length <= 600 ? message : "요청을 처리하지 못했습니다. 입력값을 확인하세요.");
        error.status = response.status;
        error.code = data?.error?.code;
        throw error;
      }
      if (!sessionIdPattern.test(data?.session_id || "") || !Number.isSafeInteger(data?.revision) || data.revision < 0 || !data.conditions || !Array.isArray(data.listings) || !Array.isArray(data.verification_tasks) || !Array.isArray(data.schedule?.events)) {
        throw new Error("세션 응답 형식을 확인할 수 없습니다. 최신 상태를 다시 불러오세요.");
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("응답 대기 시간이 지났습니다. 요청이 저장됐을 수 있으므로 최신 상태를 먼저 불러오세요.");
      if (error instanceof TypeError) throw new Error("서버에 연결하지 못했습니다. 실행 상태를 확인하고 최신 세션을 다시 불러오세요.");
      throw error;
    } finally {
      clearTimeout(timeout);
      if (workbench.controller === controller) workbench.controller = null;
    }
  }

  function acceptSession(next, {syncConditions = false, newSession = false} = {}) {
    workbench.session = next;
    workbench.blocked = false;
    $("#workbench-session-id").value = next.session_id;
    rememberSession(next.session_id);
    if (syncConditions) fillConditions(next.conditions);
    if (newSession) {
      workbench.proposalsChecked = false;
      const entry = next.schedule.events.find((event) => event.type === "entry");
      const date = kstLocal(entry?.end).slice(0, 10) || "2026-11-25";
      $("#workbench-entry-end").value = `${date}T15:00`;
    }
    renderWorkbench();
  }

  async function mutate(path, payload = {}, method = "POST", options = {}) {
    const current = workbench.session;
    if (!current || workbench.blocked) throw new Error("최신 세션을 불러온 뒤 다시 실행하세요.");
    const next = await requestJSON(`/api/sessions/${encodeURIComponent(current.session_id)}${path}`, method, {...payload, expected_revision: current.revision});
    if (next.session_id !== current.session_id) throw new Error("응답 세션이 현재 작업과 일치하지 않습니다. 최신 상태를 다시 불러오세요.");
    workbench.proposalsChecked = path === "/schedule/proposals";
    acceptSession(next, options);
    return next;
  }

  async function runAction(pendingMessage, action, {write = false} = {}) {
    if (workbench.busy) return;
    workbench.busy = true;
    showError("#workbench-error", "");
    $("#workbench-status").textContent = pendingMessage;
    updateControls();
    try {
      const result = await action();
      $("#workbench-status").textContent = result?.message || "작업을 완료했습니다.";
      return result;
    } catch (error) {
      if (error.status === 409 || error.code === "REVISION_CONFLICT") {
        workbench.blocked = true;
        showError("#workbench-error", "저장된 버전이 현재 화면과 다릅니다. 최신 상태를 불러온 뒤 변경 내용을 확인하세요. 요청은 자동으로 다시 실행하지 않습니다.");
      } else {
        // A transport/response failure may happen after commit. Never resubmit a write silently.
        if (write && workbench.session && !error.status) workbench.blocked = true;
        showError("#workbench-error", error.message || "작업을 완료하지 못했습니다. 최신 상태를 확인하세요.");
      }
      $("#workbench-status").textContent = "작업이 완료되지 않았습니다. 안내를 확인하세요.";
      return null;
    } finally {
      workbench.busy = false;
      updateControls();
    }
  }

  function actionButton(text, handler, className = "button secondary") {
    const button = el("button", className, text);
    button.type = "button";
    button.dataset.sessionAction = "";
    button.addEventListener("click", handler);
    return button;
  }

  function fieldValue(field, value) {
    if (value == null) return "미확인";
    if (field === "entry_steps") return `${value}개`;
    if (field === "step_free_access") return truthText(value, "가능", "불가");
    if (field === "elevator") return truthText(value, "있음", "없음");
    if (field === "noise_level") return noiseLabels[value] || "미확인";
    return "기록 있음";
  }

  function listingLabel(id) {
    const listing = workbench.session.listings.find((item) => item.id === id);
    return listing ? `${listing.id} · ${listing.name}` : "매물 정보 없음";
  }

  async function createTask(listingId, field) {
    const result = await runAction("현장 확인 작업을 만들고 있습니다.", async () => {
      const next = await mutate("/verification-tasks", {listing_id: listingId, field});
      const task = next.verification_tasks.find((item) => item.listing_id === listingId && item.field === field && item.status === "pending");
      return {message: "확인 작업이 준비됐습니다. 직접 확인한 시연값을 기록하세요. 작업 생성만으로 매물 정보가 바뀌지 않습니다.", taskId: task?.id};
    }, {write: true});
    if (result?.taskId) {
      const form = $$(".verification-task-form", panel).find((item) => item.dataset.taskId === result.taskId);
      form?.scrollIntoView({behavior: "smooth", block: "center"});
      $("[name='value']", form || panel)?.focus({preventScroll: true});
    }
  }

  function renderCandidates() {
    const root = $("#workbench-candidates");
    const session = workbench.session;
    const result = session.search_result;
    root.replaceChildren();
    $("#workbench-search-state").textContent = session.search_stale ? "재검색 필요" : result?.status === "incomplete" ? "조건 보완 필요" : result ? "규칙 검사 결과" : "검색 전";
    $("#workbench-search-note").textContent = session.search_stale
      ? "조건이나 근거가 바뀌었습니다. 이전 결과 대신 저장된 조건으로 다시 검색하세요."
      : result?.status === "incomplete"
        ? `필수 입력이 부족합니다: ${(result.missing_fields || []).map((field) => ({max_deposit_krw: "보증금 상한", "hard.max_deposit_krw": "보증금 상한"}[field] || "고객 조건")).join(", ") || "보증금 상한"}. 조건 충족 후보로 안내하지 않습니다.`
        : "필수 조건과 확인된 사실로 분류합니다. 소음 등 선호는 정렬에만 반영하며, 미확인 필수 정보는 보류합니다.";
    if (!result || session.search_stale) {
      root.append(emptyState(session.search_stale ? "조건을 다시 검사해 주세요" : "아직 후보를 검색하지 않았습니다", "조건 저장 · 후보 검색 또는 저장된 조건으로 재검색을 누르세요."));
      return;
    }
    const groups = [
      {key: "eligible", title: "조건 충족", hint: "확인된 필수 조건 통과", className: "eligible", empty: "현재 조건을 충족하는 후보가 없습니다."},
      {key: "needs_verification", title: "확인 필요", hint: "필수 정보가 미확인", className: "pending", empty: "추가 확인으로 보류된 후보가 없습니다."},
      {key: "excluded", title: "제외", hint: "계약 완료 또는 조건 위반", className: "excluded", empty: "제외된 후보가 없습니다."},
    ];
    groups.forEach((group) => {
      const candidates = result[group.key] || [];
      const section = el("section", `candidate-bucket ${group.className}`);
      const header = el("div", "candidate-bucket-heading");
      const heading = el("h4", "", group.title);
      heading.append(el("span", "", candidates.length));
      header.append(heading, el("p", "", group.hint));
      section.append(header);
      const list = el("div", "candidate-bucket-list");
      list.tabIndex = 0;
      list.setAttribute("aria-label", `${group.title} ${candidates.length}개 · 목록 안에서 스크롤할 수 있습니다`);
      if (!candidates.length) list.append(el("p", "bucket-empty", group.empty));
      candidates.forEach((candidate) => list.append(candidateCard(candidate)));
      section.append(list);
      root.append(section);
    });
  }

  function candidateCard(candidate) {
    const listing = candidate.listing;
    const card = el("article", "candidate-card");
    const top = el("div", "candidate-top");
    top.append(el("span", "", listing.id), el("span", `pill ${listing.status}`, labels.status[listing.status] || "미확인"));
    card.append(top, el("h5", "", listing.name), el("p", "candidate-price", won(listing.deposit_krw)), el("p", "candidate-facts", `${listing.area_m2}㎡ · ${listing.neighborhood} · 입주 ${dateText(listing.available_from)}`));
    const facts = el("div", "candidate-checks");
    ["entry_steps", "step_free_access", "elevator", "noise_level"].forEach((field) => facts.append(el("span", `check-chip ${listing[field] == null ? "unknown" : ""}`, `${labels.field[field]} ${fieldValue(field, listing[field])}`)));
    card.append(facts);
    const reasons = el("ul", "candidate-reasons");
    (candidate.reasons || []).forEach((reason) => reasons.append(el("li", "", reason.message)));
    if (!reasons.childElementCount) reasons.append(el("li", "", "입력한 필수 조건을 충족합니다."));
    card.append(reasons, el("p", "candidate-score", `선호 점수 ${candidate.preference_score ?? 0} · 규칙 기반 정렬`));
    const details = el("details", "candidate-controls");
    details.append(el("summary", "", "확인 작업 · 매물 상태 관리"));
    const controls = el("div", "candidate-control-body");
    if (listing.status !== "contracted") {
      const label = el("label", "", "확인할 항목");
      const select = el("select");
      select.dataset.sessionAction = "";
      select.setAttribute("aria-label", `${listing.id} 확인할 항목`);
      verificationFields.forEach((field) => {const option = el("option", "", labels.field[field]); option.value = field; select.append(option);});
      select.value = listing.id === "L001" ? "entry_steps" : "step_free_access";
      label.append(select);
      controls.append(label, actionButton("확인 작업 만들기", () => createTask(listing.id, select.value)));
    } else {
      controls.append(el("p", "workbench-helper", "계약 완료 매물은 확인 작업을 만들 수 없습니다."));
    }
    const statusForm = el("form", "candidate-status-form");
    const statusLabel = el("label", "", "매물 상태");
    const status = el("select");
    status.dataset.sessionAction = "";
    status.setAttribute("aria-label", `${listing.id} 매물 상태`);
    Object.entries(labels.status).forEach(([value, text]) => {const option = el("option", "", text); option.value = value; status.append(option);});
    status.value = listing.status;
    statusLabel.append(status);
    const save = el("button", "button secondary", "상태 저장");
    save.type = "submit";
    save.dataset.sessionAction = "";
    statusForm.append(statusLabel, save);
    statusForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (status.value === listing.status) {$("#workbench-status").textContent = "현재 상태와 같습니다. 변경할 상태를 선택하세요."; return;}
      const nextStatus = status.value;
      runAction("매물 상태와 후보 분류를 갱신하고 있습니다.", async () => {
        await mutate(`/listings/${encodeURIComponent(listing.id)}/status`, {status: nextStatus}, "PATCH");
        workbench.proposalsChecked = false;
        return {message: `${listing.id}의 상태를 ${labels.status[nextStatus]}로 저장했습니다. 후보 분류와 대기 작업을 확인하세요.`};
      }, {write: true});
    });
    controls.append(statusForm);
    details.append(controls);
    card.append(details);
    return card;
  }

  function renderVerification() {
    const session = workbench.session;
    const select = $("#workbench-task-listing");
    const previousListing = select.value;
    select.replaceChildren();
    session.listings.filter((listing) => listing.status !== "contracted").forEach((listing) => {
      const option = el("option", "", `${listing.id} · ${listing.name}`);
      option.value = listing.id;
      select.append(option);
    });
    if ([...select.options].some((option) => option.value === previousListing)) select.value = previousListing;
    $("#workbench-task-create").dataset.unavailable = String(!select.options.length);
    const root = $("#workbench-tasks");
    const drafts = new Map($$(".verification-task-form", root).map((form) => [form.dataset.taskId, {value: $("[name='value']", form)?.value, notes: $("[name='notes']", form)?.value}]));
    const pending = session.verification_tasks.filter((task) => task.status === "pending");
    $("#workbench-task-count").textContent = `대기 ${pending.length}건`;
    root.replaceChildren();
    if (!pending.length) root.append(el("p", "workbench-empty-inline", "대기 중인 확인 작업이 없습니다. 매물과 항목을 골라 작업을 만드세요."));
    pending.forEach((task) => {
      const form = el("form", "verification-task-form");
      form.dataset.taskId = task.id;
      const title = el("h4", "", `${listingLabel(task.listing_id)} / ${labels.field[task.field] || "확인 항목"}`);
      title.append(el("span", "pill verification_required", "확인 대기"));
      form.append(title, el("p", "task-question", task.question || "해당 항목을 직접 확인한 결과를 입력하세요."));
      const fields = el("div", "verification-result-fields");
      const valueLabel = el("label", "form-field", "확인된 값");
      const input = task.field === "entry_steps" ? el("input") : el("select");
      input.name = "value";
      input.required = true;
      input.dataset.sessionAction = "";
      input.setAttribute("aria-label", `${task.listing_id} ${labels.field[task.field] || "항목"} 확인된 값`);
      if (task.field === "entry_steps") {
        input.type = "number"; input.min = "0"; input.max = "10000"; input.step = "1"; input.placeholder = "시연 예: 8";
      } else {
        const choices = task.field === "noise_level" ? [["", "확인 결과 선택"], ["low", "낮음"], ["medium", "보통"], ["high", "높음"]] : [["", "확인 결과 선택"], ["true", task.field === "elevator" ? "있음" : "가능 — 전체 동선 확인"], ["false", task.field === "elevator" ? "없음" : "불가 — 이용 가능한 무계단 경로 없음"]];
        choices.forEach(([value, text]) => {const option = el("option", "", text); option.value = value; input.append(option);});
      }
      const noteLabel = el("label", "form-field", "확인 메모 (선택)");
      const notes = el("input");
      notes.name = "notes"; notes.type = "text"; notes.maxLength = 1000; notes.placeholder = "시연 상황과 확인한 구간을 기록하세요"; notes.dataset.sessionAction = "";
      if (drafts.has(task.id)) {input.value = drafts.get(task.id).value ?? ""; notes.value = drafts.get(task.id).notes ?? "";}
      valueLabel.append(input); noteLabel.append(notes); fields.append(valueLabel, noteLabel);
      const submit = el("button", "button primary", "이 확인값 기록 · 후보 재검사");
      submit.type = "submit"; submit.dataset.sessionAction = "";
      fields.append(submit); form.append(fields);
      if (task.field === "entry_steps") form.append(el("p", "workbench-helper", "계단 수를 기록해도 무계단 접근 가능 여부는 별도 확인 전까지 바뀌지 않습니다."));
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        let value = input.value;
        if (task.field === "entry_steps") value = Number(value);
        else if (task.field === "step_free_access" || task.field === "elevator") value = value === "true";
        const noteValue = notes.value.trim();
        runAction("확인값을 기록하고 후보를 다시 검사하고 있습니다.", async () => {
          await mutate("/inspections", {task_id: task.id, value, notes: noteValue});
          return {message: `${task.listing_id}의 ${labels.field[task.field]} 확인값을 기록했습니다. 다른 미확인 항목은 그대로 유지합니다.`};
        }, {write: true});
      });
      root.append(form);
    });
    const evidence = $("#workbench-evidence");
    evidence.replaceChildren();
    const inspections = session.inspections || [];
    const completed = session.verification_tasks.filter((task) => task.status !== "pending");
    $("#workbench-evidence-summary").textContent = `확인 기록 ${inspections.length}건 · 완료·취소 작업 ${completed.length}건`;
    [...inspections].reverse().forEach((item) => {
      const row = el("article", "evidence-item");
      const value = Object.prototype.hasOwnProperty.call(item, "value") ? item.value : item.observed_value;
      const confirmed = item.status === "confirmed";
      row.append(el("strong", "", `${item.listing_id} · ${labels.field[item.field] || "확인 항목"} · ${confirmed ? fieldValue(item.field, value) : "미확인"}`), el("small", "", `${confirmed ? "시연 확인 기록" : "확인 전 자료"} · ${dateText(item.observed_at || item.created_at || item.verified_at)}`));
      if (item.notes) row.append(el("p", "", item.notes));
      evidence.append(row);
    });
    completed.forEach((task) => evidence.append(el("p", "workbench-helper", `${task.listing_id} · ${labels.field[task.field] || "확인 항목"} 작업 ${task.status === "confirmed" ? "완료" : task.status === "cancelled" ? "취소" : "종료"}`)));
    if (!inspections.length && !completed.length) evidence.append(el("p", "workbench-helper", "아직 기록된 확인 결과가 없습니다."));
  }

  function kstLocal(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).formatToParts(date).map((part) => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  }

  function scheduleRange(event) {
    const start = kstLocal(event.start);
    const end = kstLocal(event.end);
    if (!start || !end) return "시각 미확인";
    if (start === end) return `${start.replace("T", " ")} KST`;
    return `${start.replace("T", " ")} — ${start.slice(0, 10) === end.slice(0, 10) ? end.slice(11) : end.replace("T", " ")} KST`;
  }

  function renderSchedule() {
    const session = workbench.session;
    const events = session.schedule.events;
    const root = $("#workbench-schedule-events");
    root.replaceChildren();
    events.forEach((event) => {
      const item = el("article", `workbench-event ${event.type}`);
      item.append(el("span", "", labels.event[event.type] || "일정"), el("strong", "", event.type === "entry" ? dateText(event.end) : `${timeText(event.start)} — ${timeText(event.end)}`), el("small", "", event.type === "entry" ? "이 시각부터 집에 들어갈 수 있습니다 · KST" : `${kstLocal(event.start).slice(0, 10)} · KST`));
      const dependencies = (event.depends_on || []).map((id) => labels.event[events.find((item) => item.id === id)?.type] || "선행 일정");
      if (dependencies.length) item.append(el("small", "", `선행: ${dependencies.join(" → ")}`));
      root.append(item);
    });
    const conflicts = $("#workbench-schedule-conflicts");
    conflicts.replaceChildren();
    const items = session.schedule.conflicts;
    if (Array.isArray(items) && items.length) {
      conflicts.className = "schedule-conflicts has-conflicts";
      conflicts.append(el("strong", "", `시간 충돌 ${items.length}건`));
      const list = el("ul");
      items.forEach((item) => list.append(el("li", "", item.message)));
      conflicts.append(list);
    } else {
      conflicts.className = "schedule-conflicts";
      conflicts.append(el("p", "", Array.isArray(items) ? "현재 내부 일정에서 시간 충돌이 발견되지 않았습니다." : "입주 시각을 변경하면 선후 관계와 가상 업체 가능 시간을 검사합니다."));
    }
    const slots = $("#workbench-provider-slots");
    slots.replaceChildren();
    (session.schedule.provider_slots || []).forEach((slot) => {const row = el("div", "provider-slot"); row.append(el("strong", "", labels.event[slot.type] || "업체 시간"), el("span", "", scheduleRange(slot))); slots.append(row);});
    if (!slots.childElementCount) slots.append(el("p", "workbench-helper", "등록된 업체 가능 시간이 없습니다. 임의의 시간을 만들어 대안을 제시하지 않습니다."));
    const proposals = $("#workbench-proposals");
    proposals.replaceChildren();
    const options = session.schedule_proposals || [];
    if (!options.length) proposals.append(el("p", "workbench-empty-inline", workbench.proposalsChecked ? "등록된 업체 가능 시간으로 충돌 없는 대안을 만들 수 없습니다. 내부 일정표는 그대로입니다." : "일정 대안을 만들면 등록된 가능 시간으로 구성한 선택지가 여기에 표시됩니다."));
    options.forEach((proposal, index) => {
      const card = el("article", "schedule-proposal");
      const current = proposal.based_on_revision === session.revision;
      card.append(el("h5", "", `대안 ${index + 1}`), el("p", "", proposal.summary || "등록된 가상 업체 가능 시간을 사용하는 일정입니다."));
      const list = el("ul");
      (proposal.events || []).forEach((event) => list.append(el("li", "", `${labels.event[event.type] || "일정"} · ${scheduleRange(event)}`)));
      card.append(list);
      const button = actionButton("이 안을 내부 일정에 적용", () => runAction("선택한 대안을 검사하고 내부 일정표에 적용하고 있습니다.", async () => {
        await mutate("/schedule/apply", {proposal_id: proposal.id});
        workbench.proposalsChecked = false;
        renderSchedule();
        return {message: "선택한 안으로 내부 일정표를 변경했습니다. 실제 업체 예약이나 메시지 발송은 하지 않았습니다."};
      }, {write: true}), "button primary");
      button.dataset.unavailable = String(!current);
      if (!current) card.append(el("p", "workbench-helper", "이후 세션이 바뀌었습니다. 최신 버전으로 대안을 다시 만드세요."));
      card.append(button, el("small", "", `기준 버전 ${proposal.based_on_revision ?? "미확인"} · 적용 전 내부 일정은 유지`));
      proposals.append(card);
    });
  }

  function renderAudit() {
    const root = $("#workbench-audit");
    const records = workbench.session.audit || [];
    root.replaceChildren();
    $("#workbench-audit-count").textContent = `${records.length}건`;
    [...records].reverse().slice(0, 30).forEach((record) => {
      const row = el("li", "audit-item");
      row.append(el("span", "audit-mark", ""), el("p", "", record.summary || "세션 변경 기록"), el("small", "", `${dateText(record.timestamp || record.at || record.created_at)}${Number.isInteger(record.revision) ? ` · 버전 ${record.revision}` : ""}`));
      root.append(row);
    });
    if (!records.length) root.append(el("li", "workbench-helper", "아직 변경 이력이 없습니다."));
    if (records.length > 30) root.append(el("li", "workbench-helper", `최근 30건 표시 · 세션에 총 ${records.length}건 보관`));
  }

  function renderWorkbench() {
    const session = workbench.session;
    $("#workbench-content").hidden = !session;
    if (!session) return;
    $("#workbench-active-id").textContent = session.session_id;
    $("#workbench-revision").textContent = `버전 ${session.revision}`;
    renderCandidates();
    renderVerification();
    renderSchedule();
    renderAudit();
    updateControls();
  }

  function loadSession(id) {
    if (!sessionIdPattern.test(id)) {showError("#workbench-error", "발급된 세션 ID를 입력하세요. 영문·숫자·밑줄·하이픈으로 된 16~80자 ID입니다."); return;}
    return runAction("저장된 세션을 불러오고 있습니다.", async () => {
      const next = await requestJSON(`/api/sessions/${encodeURIComponent(id)}`);
      if (next.session_id !== id) throw new Error("요청한 세션과 응답이 일치하지 않습니다.");
      acceptSession(next, {syncConditions: true, newSession: true});
      return {message: `세션을 불러왔습니다. 버전 ${next.revision}의 조건과 변경 이력을 확인하세요.`};
    });
  }

  $("#workbench-create").addEventListener("click", () => runAction("새 시연 세션을 만들고 기본 조건을 검사하고 있습니다.", async () => {
    const next = await requestJSON("/api/sessions", "POST", {conditions: defaultConditions()});
    acceptSession(next, {syncConditions: true, newSession: true});
    await mutate("/search");
    return {message: "새 세션을 만들고 기본 조건으로 후보를 분류했습니다. 확인 작업과 일정 변경을 실행해 보세요."};
  }, {write: true}));
  $("#workbench-resume-form").addEventListener("submit", (event) => {event.preventDefault(); loadSession($("#workbench-session-id").value.trim());});
  ["#workbench-reload", "#workbench-conflict-reload"].forEach((selector) => $(selector).addEventListener("click", () => {if (workbench.session) loadSession(workbench.session.session_id);}));
  $("#workbench-budget").addEventListener("input", updateBudgetPreview);
  $("#workbench-conditions-form").addEventListener("input", () => {workbench.conditionsDirty = true; $("#workbench-condition-state").textContent = "미저장 변경 있음 · 저장 후 적용";});
  $("#workbench-conditions-form").addEventListener("submit", (event) => {
    event.preventDefault();
    let conditions;
    try { conditions = readConditions(); } catch (error) {showError("#workbench-error", error.message); return;}
    if (!conditions) return;
    runAction("조건을 저장하고 후보를 다시 검사하고 있습니다.", async () => {
      await mutate("/conditions", {conditions}, "PATCH", {syncConditions: true});
      workbench.proposalsChecked = false;
      await mutate("/search");
      return {message: "입력한 조건을 저장하고 후보를 다시 분류했습니다."};
    }, {write: true});
  });
  $("#workbench-search").addEventListener("click", () => runAction("저장된 조건으로 후보를 검사하고 있습니다.", async () => {
    await mutate("/search");
    return {message: workbench.conditionsDirty ? "저장된 조건으로 검색했습니다. 화면의 미저장 입력은 아직 반영하지 않았습니다." : "저장된 조건으로 후보를 다시 분류했습니다."};
  }, {write: true}));
  $("#workbench-task-form").addEventListener("submit", (event) => {event.preventDefault(); if ($("#workbench-task-form").reportValidity()) createTask($("#workbench-task-listing").value, $("#workbench-task-field").value);});
  $("#workbench-entry-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("#workbench-entry-form").reportValidity()) return;
    const value = $("#workbench-entry-end").value;
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {showError("#workbench-error", "입주 가능 날짜와 시각을 분 단위로 입력하세요."); return;}
    runAction("입주 가능 시각을 변경하고 일정 충돌을 검사하고 있습니다.", async () => {
      await mutate("/schedule/entry", {new_end: `${value}:00+09:00`}, "PATCH");
      workbench.proposalsChecked = false;
      renderSchedule();
      return {message: "입주 가능 시각을 변경했습니다. 충돌 결과를 확인하고 일정 대안을 만들어 보세요."};
    }, {write: true});
  });
  $("#workbench-propose").addEventListener("click", () => runAction("등록된 가상 업체 시간으로 가능한 대안을 계산하고 있습니다.", async () => {
    await mutate("/schedule/proposals");
    workbench.proposalsChecked = true;
    renderSchedule();
    return {message: workbench.session.schedule_proposals?.length ? "일정 대안을 만들었습니다. 날짜와 시간을 확인한 뒤 적용할 안을 직접 선택하세요." : "등록된 가능 시간으로 실행 가능한 대안이 없습니다. 내부 일정표는 바뀌지 않았습니다."};
  }, {write: true}));
  window.addEventListener("pagehide", () => {workbench.request += 1; workbench.controller?.abort();});
  try {
    const saved = sessionStorage.getItem(sessionKey);
    if (saved && sessionIdPattern.test(saved)) {$("#workbench-session-id").value = saved; $("#workbench-status").textContent = "이 탭에서 사용한 세션 ID가 있습니다. 세션 불러오기를 눌러 이어가세요.";}
  } catch (_) { /* The workbench remains usable without sessionStorage. */ }
  updateControls();
})();
