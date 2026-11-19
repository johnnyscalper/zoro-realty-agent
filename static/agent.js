/* Stage 6: explicit development / Zoro choice, grounded tools, reviewed writes. */
"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  if (!byId("panel-agent")) return;
  const make = (tag, className = "", text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const items = (value) => Array.isArray(value) ? value : [];
  const safeText = (value, fallback = "") => typeof value === "string" ? value : fallback;
  const sessionKey = "realty-workbench-session-id";
  const sessionPattern = /^[A-Za-z0-9_-]{16,80}$/;
  const model = {session: null, busy: false, blocked: false, status: null, statusFailed: false, controller: null, statusController: null};
  const toolNames = {update_conditions: "상담 조건 변경", search_candidates: "후보 조건 검사", request_verification: "내부 확인 작업 생성", record_inspection: "현장 확인값 기록", set_listing_status: "매물 상태 변경", set_entry_time: "입주 가능 시각 변경", propose_schedule: "일정 대안 계산", apply_schedule: "내부 일정 적용", lookup_transactions: "과거 실거래 자료 조회", ask_user: "추가 정보 질문", finish: "결과 정리", error: "요청 중단"};
  const fieldNames = {max_deposit_krw: "보증금 상한", min_area_m2: "최소 전용면적", move_in_by: "입주 기한", step_free_access: "무계단 접근", elevator: "엘리베이터", entry_steps: "입구 계단", noise_level: "소음 선호", neighborhoods: "선호 동", status: "매물 상태", new_end: "입주 가능 시각", listing_id: "가상 매물 ID", proposal_id: "일정 대안 ID", notes: "확인 메모", field: "확인 항목", value: "확인값", kind: "거래 종류", deal_ymd: "계약 연월", lawd_cd: "시군구 코드", apartment_name: "아파트명 일치 조건", min_area_m2_filter: "최소 면적", max_area_m2: "최대 면적", question: "질문"};
  const statusNames = {available: "시연상 거래 가능", verification_required: "확인 필요", contracted: "계약 완료"};
  const eventNames = {entry: "입주 가능", cleaning: "입주 청소", moving: "이삿짐 반입"};
  const turnStatusNames = {completed: "완료", awaiting_confirmation: "승인 대기", needs_input: "추가 답변 필요", error: "실행 오류", limit_reached: "단계 제한 도달"};
  const examples = {
    conditions: "보증금 10억 원 이하, 75㎡ 이상, 2026-11-25까지 입주, 무계단 접근 필수, 조용한 집 선호",
    inspection: "L001 무계단 접근 불가로 확인했어",
    schedule: "입주 가능 시간을 2026-11-25T15:00:00+09:00로 변경해줘",
    transactions: "2026년 10월 강남구 전월세 실거래 조회해줘",
  };

  function amount(value) {
    if (!Number.isSafeInteger(value) || value < 0) return "미지정";
    if (value === 0) return "0원";
    const eok = Math.floor(value / 100000000);
    const man = Math.floor((value % 100000000) / 10000);
    const remainder = value % 10000;
    const parts = [];
    if (eok) parts.push(`${eok.toLocaleString("ko-KR")}억`);
    if (man) parts.push(`${man.toLocaleString("ko-KR")}만`);
    if (remainder) parts.push(remainder.toLocaleString("ko-KR"));
    return `${parts.join(" ")} 원`;
  }
  function time(value, compact = false) {
    if (typeof value !== "string" || !value) return "시각 미확인";
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return "시각 미확인";
    return new Intl.DateTimeFormat("ko-KR", {timeZone: "Asia/Seoul", ...(compact ? {} : {year: "numeric", month: "2-digit", day: "2-digit"}), hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(parsed);
  }
  function valueText(field, value, inspection = false) {
    if (value === null || value === undefined || value === "") return "미지정";
    if (field === "max_deposit_krw") return amount(value);
    if (["min_area_m2", "max_area_m2", "min_area_m2_filter"].includes(field)) return `${value}㎡`;
    if (field === "step_free_access") return inspection ? (value ? "가능 · 전체 동선 확인" : "불가 · 이용 가능한 무계단 경로 없음") : (value ? "필수" : "불가가 필수");
    if (field === "elevator") return inspection ? (value ? "있음" : "없음") : (value ? "필수" : "없음이 필수");
    if (field === "entry_steps") return `${value}개`;
    if (field === "noise_level") return ({low: "낮음", medium: "보통", high: "높음"})[value] || "미확인";
    if (field === "neighborhoods") return items(value).join(", ") || "선호 없음";
    if (field === "status") return statusNames[value] || "미확인";
    if (field === "new_end") return `${time(value)} KST`;
    if (field === "kind") return value === "trade" ? "매매" : value === "rent" ? "전월세" : "미확인";
    if (field === "deal_ymd" && /^\d{6}$/.test(value)) return `${value.slice(0, 4)}년 ${value.slice(4)}월`;
    if (field === "field") return value === "noise_level" ? "소음 수준" : fieldNames[value] || "확인 항목";
    return typeof value === "string" || typeof value === "number" ? String(value) : "미확인";
  }
  function row(dl, key, value) {
    const pair = make("div", "agent-data-row");
    pair.append(make("dt", "", key), make("dd", "", value));
    dl.append(pair);
  }
  function uuid() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function showError(message = "") {
    byId("agent-error").textContent = message;
    byId("agent-error").classList.toggle("hidden", !message);
  }
  function remember(id) {
    try { sessionStorage.setItem(sessionKey, id); } catch (_) { /* Only an opaque session ID is optional browser storage. */ }
  }
  function remembered() {
    try { return sessionStorage.getItem(sessionKey) || ""; } catch (_) { return ""; }
  }

  async function request(path, method = "GET", body) {
    const controller = new AbortController();
    model.controller = controller;
    const timer = setTimeout(() => controller.abort(), 125000);
    try {
      const response = await fetch(path, {method, credentials: "same-origin", redirect: "error", cache: "no-store", headers: {Accept: "application/json", ...(method === "GET" ? {} : {"Content-Type": "application/json"})}, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal});
      let result;
      try { result = await response.json(); } catch (_) { throw new Error("서버 응답을 읽지 못했습니다. 최신 상태를 불러와 저장 결과를 확인하세요."); }
      if (!response.ok) {
        const message = result?.error?.message;
        const error = new Error(typeof message === "string" && message.length <= 600 ? message : "요청을 처리하지 못했습니다. 입력과 현재 상태를 확인하세요.");
        error.status = response.status;
        error.code = safeText(result?.error?.code);
        throw error;
      }
      if (!sessionPattern.test(result?.session_id || "") || !Number.isSafeInteger(result?.revision) || result.revision < 0 || !result.conditions?.hard || !result.conditions?.preferences || !Array.isArray(result.listings) || !Array.isArray(result.schedule?.events)) {
        throw new Error("세션 응답 형식을 확인할 수 없습니다. 최신 상태를 불러오세요.");
      }
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("응답 대기 시간이 지났습니다. 처리가 저장됐을 수 있으므로 최신 상태를 먼저 불러오세요.");
      if (error instanceof TypeError) throw new Error("서버에 연결하지 못했습니다. 서버 상태를 확인하고 최신 세션을 불러오세요.");
      throw error;
    } finally {
      clearTimeout(timer);
      if (model.controller === controller) model.controller = null;
    }
  }

  async function run(message, action, write = false) {
    if (model.busy) return;
    model.busy = true;
    showError();
    byId("agent-status").textContent = message;
    controls();
    try {
      await action();
    } catch (error) {
      if (error.status === 409 || /REVISION|STALE|CONFLICT/.test(error.code || "")) {
        model.blocked = true;
        showError("다른 요청에서 세션이 바뀌었거나 승인 대상이 오래되었습니다. 최신 상태를 확인한 뒤 다시 선택하세요.");
      } else {
        if (write && model.session && (!error.status || error.status >= 500)) model.blocked = true;
        showError(error.message || "요청을 완료하지 못했습니다. 최신 상태를 확인하세요.");
      }
      byId("agent-status").textContent = "작업이 완료되지 않았습니다. 위 안내를 확인하세요.";
    } finally {
      model.busy = false;
      controls();
    }
  }

  function accept(next, expectedId) {
    if (expectedId && next.session_id !== expectedId) throw new Error("응답 세션이 현재 상담과 다릅니다. 최신 상태를 불러오세요.");
    model.session = next;
    model.blocked = false;
    remember(next.session_id);
    byId("agent-session-id").value = next.session_id;
    if (next.agent?.pending) {
      const latest = items(next.agent.turns).at(-1);
      const pendingMode = next.agent.pending.mode || latest?.mode;
      const pendingDataMode = next.agent.pending.data_mode || latest?.data_mode;
      if (["development", "zoro"].includes(pendingMode)) byId("agent-mode").value = pendingMode;
      if (["sample", "live"].includes(pendingDataMode)) byId("agent-data-mode").value = pendingDataMode;
    }
    render();
  }

  function modeDescription() {
    const zoro = byId("agent-mode").value === "zoro";
    byId("agent-mode-description").textContent = zoro ? "설정된 Zoro 서버에 도구 판단을 요청합니다. 연결·실제 모델 성능은 별도 검증 대상이며 실패 시 규칙 모드로 바꾸지 않습니다." : "정해진 표현과 규칙으로 도구를 선택합니다. Zoro 또는 범용 자연어 모델이 아닙니다.";
    byId("agent-data-description").textContent = byId("agent-data-mode").value === "live" ? "직접 선택한 실제 조회 모드입니다. 실거래 요청 시 서버의 국토부 API를 호출하며, 실패하면 가상 자료로 대체하지 않습니다. 매물과 업체 시간은 여전히 가상입니다." : "실거래 요청에만 적용됩니다. 가상 응답을 사용하며 실제 가격 근거가 아닙니다.";
    byId("agent-data-description").classList.toggle("agent-live-note", byId("agent-data-mode").value === "live");
  }
  function controls() {
    const pending = Boolean(model.session?.agent?.pending);
    const zoroUnavailable = byId("agent-mode").value === "zoro" && model.status?.modes?.zoro?.available !== true;
    const unavailable = model.status?.available === false;
    byId("agent-new").disabled = model.busy;
    byId("agent-reload").disabled = model.busy || !model.session;
    byId("agent-conflict-reload").disabled = model.busy || !model.session;
    byId("agent-open-workbench").disabled = model.busy || !model.session;
    byId("agent-mode").disabled = model.busy || pending;
    byId("agent-data-mode").disabled = model.busy || pending;
    byId("agent-message").disabled = model.busy || pending || model.blocked;
    byId("agent-send").disabled = model.busy || pending || model.blocked || zoroUnavailable || unavailable || !byId("agent-message").value.trim();
    byId("agent-send").textContent = model.busy ? "처리 중…" : "보내기 ↑";
    byId("agent-confirm").disabled = model.busy || model.blocked || !pending;
    byId("agent-cancel").disabled = model.busy || model.blocked || !pending;
    for (const node of document.querySelectorAll("#agent-resume-form input, #agent-resume-form button")) node.disabled = model.busy;
    for (const node of document.querySelectorAll("[data-agent-example]")) node.disabled = model.busy || pending || model.blocked;
    byId("agent-conflict").classList.toggle("hidden", !model.blocked);
    byId("agent-conversation-title").closest("section").setAttribute("aria-busy", String(model.busy));
    for (const node of document.querySelectorAll("[data-agent-proposal]")) node.disabled = node.dataset.agentProposal !== "current" || model.busy || model.blocked || pending;
    byId("agent-composer-hint").textContent = pending ? "먼저 위 변경을 승인하거나 취소하세요." : model.blocked ? "최신 상태를 불러온 뒤 이어가세요." : "Enter 전송 · Shift+Enter 줄바꿈";
    byId("agent-character-count").textContent = `${byId("agent-message").value.length.toLocaleString("ko-KR")} / 2,000`;
    modeDescription();
  }

  async function loadStatus() {
    model.statusController?.abort();
    const controller = new AbortController();
    model.statusController = controller;
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch("/api/agent/status", {headers: {Accept: "application/json"}, credentials: "same-origin", redirect: "error", cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error("status");
      const status = await response.json();
      if (model.statusController !== controller) return;
      if (status?.available !== true || typeof status?.modes?.zoro?.available !== "boolean") throw new Error("status");
      model.status = status;
      const option = byId("agent-mode").querySelector("option[value='zoro']");
      if (!option) throw new Error("status UI");
      option.disabled = !status.modes.zoro.available;
      option.textContent = status.modes.zoro.available ? "Zoro 서버 모드 · 실제 판단 미검증" : "Zoro 서버 모드 · 설정 필요";
      const overview = byId("agent-overview-zoro");
      if (overview) overview.textContent = status.modes.zoro.available ? "서버 설정됨" : "연결 준비";
      if (model.statusFailed && !model.busy) byId("agent-status").textContent = model.session ? resultStatus() : "상담 기능을 사용할 수 있습니다. 예시를 선택하거나 조건을 입력하세요.";
      model.statusFailed = false;
    } catch (_) {
      if (model.statusController !== controller) return;
      model.status = {available: false};
      model.statusFailed = true;
      if (!model.busy) byId("agent-status").textContent = "상담 기능 상태를 확인하지 못했습니다. 상단 연결 상태 새로고침을 누르세요.";
    } finally {
      clearTimeout(timer);
      if (model.statusController === controller) {
        model.statusController = null;
        controls();
      }
    }
  }

  function executionLabel(turn) {
    if (turn?.zoro_used === true) return "Zoro 서버 응답 사용";
    if (turn?.mode === "zoro") return "Zoro 사용 완료 안 됨";
    return turn ? "개발용 규칙 실행 · Zoro 아님" : "아직 실행 전";
  }
  function renderTranscript() {
    const root = byId("agent-transcript");
    root.replaceChildren();
    const messages = items(model.session?.agent?.messages);
    if (!messages.length) {
      const welcome = make("div", "agent-welcome");
      const mark = make("span", "agent-welcome-mark", "✳");
      mark.setAttribute("aria-hidden", "true");
      welcome.append(mark, make("h4", "", "안녕하세요. 어떤 집을 찾고 계세요?"), make("p", "", "강남구 아파트 전세 조건을 함께 정리해 볼게요. 최대 보증금과 꼭 필요한 조건부터 알려 주세요. 확인한 결과와 다음에 살펴볼 내용을 짧게 설명해 드릴게요."));
      const note = make("p", "agent-welcome-disclosure", "개발용 규칙 모드로 시작합니다. 매물·업체 일정은 가상이며, 조건 변경과 기록 저장에는 승인이 필요합니다.");
      welcome.append(note);
      root.append(welcome);
    } else {
      for (const message of messages) {
        const user = message.role === "user";
        const article = make("article", `agent-message ${user ? "agent-message-user" : "agent-message-assistant"}`);
        const heading = make("div", "agent-message-heading");
        heading.append(make("strong", "", user ? "나" : "상담 도우미"));
        if (message.created_at) heading.append(make("span", "", `${time(message.created_at, true)} KST`));
        article.append(heading, make("p", "agent-message-text", safeText(message.text)));
        root.append(article);
      }
    }
    const latest = items(model.session?.agent?.turns).at(-1);
    byId("agent-execution-badge").textContent = executionLabel(latest);
    byId("agent-execution-badge").classList.toggle("synthetic", Boolean(latest && latest.mode === "development"));
    root.scrollTop = root.scrollHeight;
  }

  function argumentRows(dl, tool, args, current = false) {
    if (!args || typeof args !== "object") return;
    if (tool === "update_conditions") {
      for (const section of ["hard", "preferences"]) {
        const changes = args.conditions?.[section];
        if (!changes || typeof changes !== "object") continue;
        for (const field of ["max_deposit_krw", "min_area_m2", "move_in_by", "step_free_access", "elevator", "noise_level", "neighborhoods"]) {
          if (!Object.prototype.hasOwnProperty.call(changes, field)) continue;
          const before = valueText(field, model.session?.conditions?.[section]?.[field]);
          row(dl, `${section === "hard" ? "필수" : "선호"} · ${fieldNames[field]}`, `${current ? `${before} → ` : ""}${valueText(field, changes[field])}`);
        }
      }
      return;
    }
    for (const field of ["listing_id", "field", "value", "notes", "status", "new_end", "proposal_id", "kind", "lawd_cd", "deal_ymd", "apartment_name", "min_area_m2", "max_area_m2", "question"]) {
      if (!Object.prototype.hasOwnProperty.call(args, field)) continue;
      const label = fieldNames[field];
      row(dl, label, valueText(field === "value" ? args.field : field, args[field], tool === "record_inspection"));
    }
  }
  function renderPending() {
    const pending = model.session?.agent?.pending;
    byId("agent-pending").hidden = !pending;
    byId("agent-review-fields").replaceChildren();
    if (!pending) return;
    byId("agent-review-title").textContent = `${toolNames[pending.tool] || "변경 내용"} 승인`;
    byId("agent-review-summary").textContent = safeText(pending.summary, "아래 변경을 확인한 뒤 저장 여부를 선택하세요.");
    argumentRows(byId("agent-review-fields"), pending.tool, pending.arguments, true);
    if (pending.tool === "apply_schedule") {
      const proposal = items(model.session.schedule_proposals).find((item) => item.id === pending.arguments?.proposal_id);
      for (const event of items(proposal?.events)) row(byId("agent-review-fields"), eventNames[event.type] || "일정", `${time(event.start)} — ${time(event.end, true)} KST`);
    }
    byId("agent-review-note").textContent = pending.tool === "record_inspection" ? "직접 확인한 시연 결과를 기록합니다. 이 항목의 기록만 바뀌며, 다른 미확인 항목은 그대로입니다." : pending.tool === "apply_schedule" ? "승인하면 이 세션의 내부 일정표를 바꿉니다. 실제 업체 예약이나 메시지 발송은 하지 않습니다." : "승인하면 이 세션에 변경을 저장하고 후속 검사를 이어갑니다. 취소하면 이 변경은 저장하지 않습니다.";
  }

  function sourceLabel(entry) {
    if (entry.status === "rolled_back") return "실행 취소 · 이번 요청의 상태 변경 미반영";
    if (entry.tool === "error") return "실행 오류 · 후속 진행 중단";
    if (entry.tool === "lookup_transactions") {
      if (entry.status === "error") return "조회 오류 · 가상 대체 없음";
      const observation = entry.observation;
      if (observation?.source === "molit" && observation.synthetic === false) return "국토부 실제 조회 · 과거 기록";
      if (observation?.source === "sample" && observation.synthetic === true) return "가상 거래 응답 · 실제 가격 아님";
      return "실거래 자료 · 출처 확인 필요";
    }
    if (["update_conditions", "ask_user", "finish"].includes(entry.tool)) return "사용자 조건 · 내부 처리";
    if (["set_entry_time", "propose_schedule", "apply_schedule"].includes(entry.tool)) return "가상 업체 시간 · 내부 일정";
    if (entry.tool === "record_inspection") return "사용자 확인값 · 가상 매물";
    return "가상 매물 · 내부 도구";
  }
  function evidenceCard(observation) {
    const box = make("div", "agent-market-evidence");
    if (!observation || typeof observation !== "object") return box;
    const live = observation.source === "molit" && observation.synthetic === false;
    const count = `${Number.isInteger(observation.matched_count) ? observation.matched_count : "—"}건 일치 / ${Number.isInteger(observation.scanned_count) ? observation.scanned_count : "—"}건 검토`;
    box.append(make("p", "agent-evidence-heading", `${count} · ${observation.coverage === "complete" ? "반환 자료 전체 검토" : "일부 자료 검토"}`));
    box.append(make("p", "agent-small-note", "아래 최대 10건은 과거 신고 계약입니다. 현재 가상 매물과 동일한 집으로 추정하지 않습니다."));
    for (const record of items(observation.items).slice(0, 10)) {
      const item = make("article", "agent-transaction-row");
      item.append(make("strong", "", safeText(record.apartment_name, "이름 미확인")), make("span", "", `${safeText(record.contract_date, "계약일 미확인")} · ${Number.isFinite(record.area_m2) ? `${record.area_m2}㎡` : "면적 미확인"}`));
      const price = observation.kind === "trade" ? amount(record.price_krw) : `보증금 ${amount(record.deposit_krw)} · 월세 ${amount(record.monthly_rent_krw)}`;
      item.append(make("span", "", price));
      if (observation.kind === "trade") item.append(make("small", "", record.cancelled === true ? "해제 신고 기록" : record.cancellation_status_available !== true || record.cancelled !== false ? "해제 여부 미확인" : "조회 자료상 해제 표시 없음"));
      box.append(item);
    }
    for (const warning of items(observation.warnings)) if (typeof warning === "string") box.append(make("p", "agent-evidence-warning", warning));
    if (Number.isInteger(observation.excluded_cancelled_count) && observation.excluded_cancelled_count > 0) box.append(make("p", "agent-evidence-warning", `해제 신고 거래 ${observation.excluded_cancelled_count}건 제외`));
    box.append(make("p", "agent-small-note", live ? "출처: 국토교통부 신고 자료. 제한된 조회 결과로 월 전체 가격이나 시세를 판단하지 않습니다." : "출처: 가상 응답 예시. 실제 거래가격 판단에 사용할 수 없습니다."));
    return box;
  }
  function renderTrace() {
    const root = byId("agent-trace");
    root.replaceChildren();
    const turns = items(model.session?.agent?.turns);
    const count = turns.reduce((total, turn) => total + items(turn.trace).length, 0);
    byId("agent-trace-count").textContent = `${count}건`;
    if (!turns.length) root.append(make("p", "agent-empty-note", "요청을 보내면 실행한 도구, 변경 대기 상태, 자료 출처를 여기에서 확인할 수 있습니다."));
    [...turns].reverse().forEach((turn, index) => {
      const section = make("details", "agent-trace-turn");
      section.open = index === 0;
      const summary = make("summary");
      summary.append(make("strong", "", `요청 ${turns.length - index} · ${turnStatusNames[turn.status] || "처리 상태 확인"}`), make("span", "", executionLabel(turn)));
      section.append(summary);
      const traceList = make("ol", "agent-action-list");
      for (const entry of items(turn.trace)) {
        const step = make("li", "agent-action");
        const heading = make("div", "agent-action-heading");
        const state = ({executed: "실행 완료", pending_confirmation: "승인 대기", error: "오류", rolled_back: "상태 변경 미반영"})[entry.status] || "상태 확인";
        heading.append(make("strong", "", toolNames[entry.tool] || "도구 처리"), make("span", `pill ${entry.status === "pending_confirmation" ? "verification_required" : ""}`, state));
        step.append(heading, make("p", "", safeText(entry.summary, "처리 결과를 확인하세요.")), make("span", "agent-source-label", sourceLabel(entry)));
        const fields = make("dl", "agent-trace-fields");
        argumentRows(fields, entry.tool, entry.arguments);
        if (fields.childElementCount) {
          const detail = make("details", "agent-argument-details");
          detail.append(make("summary", "", "실행 입력 보기"), fields);
          step.append(detail);
        }
        if (entry.tool === "lookup_transactions" && entry.status === "executed") step.append(evidenceCard(entry.observation));
        traceList.append(step);
      }
      if (!traceList.childElementCount) section.append(make("p", "agent-empty-note", "완료된 도구 실행이 없습니다."));
      section.append(traceList);
      root.append(section);
    });
  }

  function renderConditions() {
    const root = byId("agent-conditions");
    root.replaceChildren();
    if (!model.session) {root.append(make("p", "agent-empty-note", "예산과 필수 조건을 말하면 변경 내용을 먼저 보여드립니다.")); return;}
    const conditions = model.session.conditions;
    const hard = make("dl", "agent-condition-list");
    for (const field of ["max_deposit_krw", "min_area_m2", "move_in_by", "step_free_access", "elevator"]) row(hard, fieldNames[field], valueText(field, conditions.hard[field]));
    root.append(make("span", "agent-condition-kind", "필수 조건"), hard);
    const preferences = make("dl", "agent-condition-list agent-preference-list");
    for (const field of ["noise_level", "neighborhoods"]) row(preferences, fieldNames[field], valueText(field, conditions.preferences[field]));
    root.append(make("span", "agent-condition-kind", "선호 · 제외 기준 아님"), preferences);
  }
  function renderCandidates() {
    const root = byId("agent-candidates");
    root.replaceChildren();
    const session = model.session;
    const result = session?.search_result;
    if (!result || session.search_stale) {
      root.append(make("p", "agent-empty-note", session?.search_stale ? "조건이나 확인값이 바뀌었습니다. ‘후보 다시 검색해줘’로 최신 조건을 검사하세요." : "조건을 저장하고 검색하면 충족·확인 필요·제외 후보가 표시됩니다."));
      return;
    }
    if (result.status === "incomplete") root.append(make("p", "agent-evidence-warning", "예산 등 필수 입력을 먼저 보완하세요. 조건 충족 후보로 판단하지 않습니다."));
    const groups = [["eligible", "조건 충족"], ["needs_verification", "확인 필요"], ["excluded", "제외"]];
    const counts = make("div", "agent-candidate-counts");
    for (const [key, label] of groups) {const item = make("div", `agent-count ${key}`); item.append(make("strong", "", items(result[key]).length), make("span", "", label)); counts.append(item);}
    root.append(counts);
    for (const [key, label] of groups) {
      const candidates = items(result[key]);
      if (!candidates.length) continue;
      const group = make("details", "agent-candidate-group");
      group.open = key === "eligible" || key === "needs_verification";
      group.append(make("summary", "", `${label} ${candidates.length}개`));
      for (const candidate of candidates) {
        const listing = candidate.listing || {};
        const card = make("article", "agent-candidate-item");
        card.append(make("strong", "", `${safeText(listing.id)} · ${safeText(listing.name)}`), make("p", "", `${amount(listing.deposit_krw)} · ${Number.isFinite(listing.area_m2) ? listing.area_m2 : "—"}㎡ · ${safeText(listing.neighborhood)}`));
        const reasons = items(candidate.reasons).filter((reason) => key === "eligible" ? /^PREFERENCE_/.test(reason.code || "") : ["HARD_EVIDENCE_UNKNOWN", "HARD_REQUIREMENT_MISMATCH", "CONTRACTED", "BUDGET_EXCEEDED", "AREA_TOO_SMALL", "MOVE_IN_TOO_LATE", "BUDGET_REQUIRED"].includes(reason.code));
        for (const reason of reasons.slice(0, 2)) card.append(make("small", "", safeText(reason.message)));
        if (key === "eligible") card.append(make("small", "", "확인된 필수 조건 통과 · 실제 매물 아님"));
        group.append(card);
      }
      root.append(group);
    }
    const pending = items(session.verification_tasks).filter((task) => task.status === "pending");
    if (pending.length) root.append(make("p", "agent-small-note", `내부 확인 작업 ${pending.length}건 대기 · 실제 현장 확인은 별도로 필요합니다.`));
  }
  function renderSchedule() {
    const root = byId("agent-schedule");
    root.replaceChildren();
    if (!model.session) {root.append(make("p", "agent-empty-note", "새 상담에는 2026-11-25의 가상 일정이 준비됩니다. 입주 시각 변경과 대안 생성을 요청할 수 있습니다.")); return;}
    for (const event of items(model.session.schedule.events)) {
      const item = make("div", "agent-event-row");
      item.append(make("span", "", eventNames[event.type] || "일정"), make("strong", "", event.type === "entry" ? `${time(event.end)} KST` : `${time(event.start)} — ${time(event.end, true)} KST`));
      root.append(item);
    }
    const conflicts = model.session.schedule.conflicts;
    if (Array.isArray(conflicts)) {
      const note = make("div", conflicts.length ? "agent-schedule-alert" : "agent-schedule-ok");
      note.append(make("strong", "", conflicts.length ? `시간 충돌 ${conflicts.length}건` : "현재 내부 일정의 시간 충돌 없음"));
      for (const conflict of conflicts) note.append(make("p", "", safeText(conflict.message)));
      root.append(note);
    }
    const proposals = items(model.session.schedule_proposals);
    proposals.forEach((proposal, index) => {
      const card = make("article", "agent-proposal-card");
      const current = proposal.based_on_revision === model.session.revision;
      card.append(make("strong", "", `일정 대안 ${index + 1}`), make("p", "", safeText(proposal.summary, "등록된 가상 업체 가능 시간으로 구성한 대안")));
      for (const event of items(proposal.events)) card.append(make("small", "", `${eventNames[event.type] || "일정"} · ${time(event.start)} — ${time(event.end, true)} KST`));
      const choose = make("button", "button secondary", current ? `대안 ${index + 1} 선택 요청` : "이전 버전 대안 · 재생성 필요");
      choose.type = "button";
      choose.dataset.agentProposal = current ? "current" : "stale";
      choose.disabled = !current || model.busy || model.blocked || Boolean(model.session.agent?.pending);
      choose.addEventListener("click", () => fillPrompt(`${index + 1}번 일정 대안 적용해줘`));
      card.append(choose);
      root.append(card);
    });
  }
  function render() {
    byId("agent-session-label").textContent = model.session ? "상담 세션" : "새 상담을 시작하세요";
    byId("agent-active-id").textContent = model.session?.session_id || "";
    byId("agent-revision").hidden = !model.session;
    byId("agent-revision").textContent = model.session ? `버전 ${model.session.revision}` : "";
    renderTranscript();
    renderPending();
    renderTrace();
    renderConditions();
    renderCandidates();
    renderSchedule();
    controls();
  }
  function fillPrompt(text) {
    if (model.busy || model.blocked || model.session?.agent?.pending) return;
    byId("agent-message").value = text;
    controls();
    byId("agent-message").focus();
  }
  function resultStatus() {
    const latest = items(model.session?.agent?.turns).at(-1);
    if (model.session?.agent?.pending) return "저장 전 승인이 필요합니다. 변경 내용을 검토한 뒤 승인하거나 취소하세요.";
    if (latest?.status === "error") return "요청 중 오류가 발생했습니다. 대화와 도구 실행 기록을 확인하세요.";
    if (latest?.status === "needs_input") return "추가 정보가 필요합니다. 대화에 표시된 질문에 답해 주세요.";
    if (latest?.status === "limit_reached") return "이번 요청의 도구 실행 한도에 도달했습니다. 결과를 확인하고 다음 요청을 입력하세요.";
    return "결과와 현재 상태를 확인하세요. 필요한 다음 요청을 이어갈 수 있습니다.";
  }
  async function send() {
    const message = byId("agent-message").value.trim();
    if (!message || message.length > 2000 || model.busy || model.blocked || model.session?.agent?.pending || model.status?.available === false) return;
    const mode = byId("agent-mode").value;
    const dataMode = byId("agent-data-mode").value;
    if (mode === "zoro" && model.status?.modes?.zoro?.available !== true) {showError("Zoro 서버 설정을 확인해야 이 모드를 사용할 수 있습니다."); return;}
    await run(mode === "zoro" ? "Zoro 서버의 도구 판단과 실행 결과를 기다리고 있습니다." : "개발용 규칙으로 요청을 확인하고 도구를 실행하고 있습니다.", async () => {
      if (!model.session) accept(await request("/api/sessions", "POST", {}));
      const current = model.session;
      const next = await request(`/api/sessions/${encodeURIComponent(current.session_id)}/chat`, "POST", {expected_revision: current.revision, message, mode, data_mode: dataMode, request_id: uuid()});
      accept(next, current.session_id);
      byId("agent-message").value = "";
      byId("agent-status").textContent = resultStatus();
      if (next.agent?.pending) byId("agent-pending").focus({preventScroll: true});
    }, true);
  }
  function loadSession(id) {
    if (!sessionPattern.test(id)) {showError("영문·숫자·밑줄·하이픈으로 된 16~80자의 발급된 세션 ID를 입력하세요."); return;}
    return run("저장된 상담과 최신 상태를 불러오고 있습니다.", async () => {
      accept(await request(`/api/sessions/${encodeURIComponent(id)}`), id);
      byId("agent-session-details").open = false;
      byId("agent-status").textContent = model.session.agent?.pending ? resultStatus() : `버전 ${model.session.revision}의 상담을 불러왔습니다. 저장된 조건과 기록을 확인하세요.`;
    });
  }
  async function confirm(approve) {
    const pending = model.session?.agent?.pending;
    if (!pending || model.busy || model.blocked) return;
    await run(approve ? "승인한 변경을 저장하고 후속 도구를 실행하고 있습니다." : "변경 제안을 취소하고 있습니다.", async () => {
      const current = model.session;
      const next = await request(`/api/sessions/${encodeURIComponent(current.session_id)}/chat/confirm`, "POST", {expected_revision: current.revision, pending_id: pending.id, approve, request_id: uuid()});
      accept(next, current.session_id);
      byId("agent-status").textContent = approve ? resultStatus() : "이 변경을 취소했습니다. 다른 요청을 입력할 수 있습니다.";
    }, true);
  }

  byId("agent-chat-form").addEventListener("submit", (event) => {event.preventDefault(); send();});
  byId("agent-message").addEventListener("input", controls);
  byId("agent-message").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {event.preventDefault(); send();}
  });
  byId("agent-mode").addEventListener("change", controls);
  byId("agent-data-mode").addEventListener("change", controls);
  byId("agent-new").addEventListener("click", () => run("필수 조건을 미리 정하지 않은 새 상담을 만들고 있습니다.", async () => {
    accept(await request("/api/sessions", "POST", {}));
    byId("agent-mode").value = "development";
    byId("agent-data-mode").value = "sample";
    byId("agent-message").value = "";
    byId("agent-status").textContent = "새 상담을 만들었습니다. 예산과 필수 조건을 알려주세요. 기존 세션은 ID로 다시 불러올 수 있습니다.";
  }, true));
  byId("agent-resume-form").addEventListener("submit", (event) => {event.preventDefault(); loadSession(byId("agent-session-id").value.trim());});
  byId("agent-from-workbench").addEventListener("click", () => {
    const active = byId("workbench-active-id")?.textContent?.trim();
    const input = byId("workbench-session-id")?.value?.trim();
    const id = active || input || remembered();
    if (!id) {showError("이어갈 실행실 세션이 없습니다. 새 상담을 시작하거나 기존 세션 ID를 입력하세요."); return;}
    loadSession(id);
  });
  for (const id of ["agent-reload", "agent-conflict-reload"]) byId(id).addEventListener("click", () => {if (model.session) loadSession(model.session.session_id);});
  byId("agent-confirm").addEventListener("click", () => confirm(true));
  byId("agent-cancel").addEventListener("click", () => confirm(false));
  for (const button of document.querySelectorAll("[data-agent-example]")) button.addEventListener("click", () => fillPrompt(examples[button.dataset.agentExample]));
  byId("agent-open-workbench").addEventListener("click", () => {
    if (!model.session || model.busy) return;
    byId("workbench-session-id").value = model.session.session_id;
    byId("tab-workbench").click();
    byId("workbench-resume-form").requestSubmit();
  });
  byId("tab-agent").addEventListener("click", () => {if (model.session && !model.busy) loadSession(model.session.session_id);});
  byId("refresh-health").addEventListener("click", loadStatus);
  window.addEventListener("pagehide", () => {model.controller?.abort(); model.statusController?.abort();});
  const saved = remembered();
  if (sessionPattern.test(saved)) {
    byId("agent-session-id").value = saved;
    byId("agent-status").textContent = "이 탭의 마지막 세션 ID가 있습니다. ‘세션 이어가기’에서 불러오거나 새 상담을 시작하세요.";
  }
  render();
  loadStatus();
})();
