/* Offline contract tests using a small DOM double. Real browser checks are separate. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {webcrypto} = require("node:crypto");
const {test} = require("node:test");

class NodeDouble {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.ownText = "";
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.classes = new Set();
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.parent = null;
    this.classList = {toggle: (name, force = !this.classes.has(name)) => {if (force) this.classes.add(name); else this.classes.delete(name);}, contains: (name) => this.classes.has(name)};
  }
  get className() {return [...this.classes].join(" ");}
  set className(value) {this.classes = new Set(value.split(/\s+/).filter(Boolean));}
  get textContent() {return this.ownText + this.children.map((child) => child.textContent).join("");}
  set textContent(value) {this.ownText = String(value); this.children = [];}
  set innerHTML(_) {throw new Error("HTML interpretation is forbidden");}
  get childElementCount() {return this.children.length;}
  append(...children) {for (const child of children) {child.parent = this; this.children.push(child);}}
  replaceChildren(...children) {this.children = []; this.ownText = ""; this.append(...children);}
  setAttribute(key, value) {this.attributes[key] = String(value);}
  addEventListener(name, callback) {(this.listeners[name] ||= []).push(callback);}
  dispatch(name, overrides = {}) {const event = {target: this, currentTarget: this, preventDefault() {}, ...overrides}; for (const fn of this.listeners[name] || []) fn(event);}
  click() {if (!this.disabled) this.dispatch("click");}
  requestSubmit() {this.dispatch("submit");}
  focus() {this.focused = true;}
  closest() {return this.parent || this;}
  querySelector(selector) {if (selector === "option[value='zoro']") return this.children.find((child) => child.value === "zoro"); return null;}
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
const statusResponse = (available = false) => ({available: true, default_mode: "development", modes: {development: {available: true}, zoro: {available, configured: available, verified: false}}, tools: [], limits: {max_steps: 6}});
const sessionId = "11111111-1111-4111-8111-111111111111";
function session(overrides = {}) {
  return {session_id: sessionId, revision: 0, conditions: {hard: {max_deposit_krw: null, min_area_m2: null, move_in_by: null, step_free_access: null, elevator: null}, preferences: {noise_level: null, neighborhoods: []}}, listings: [], schedule: {events: [], conflicts: []}, verification_tasks: [], schedule_proposals: [], search_result: null, search_stale: false, ...overrides};
}
function agentState({pending = null, mode = "development", zoro_used = false, data_mode = "sample", status = "completed", text = "완료", trace = []} = {}) {
  return {messages: [{id: "m1", role: "assistant", text, created_at: "2026-11-12T03:00:00Z"}], turns: [{id: "t1", mode, zoro_used, data_mode, status, trace}], pending};
}
function harness(saved = "") {
  const nodes = new Map();
  const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
  for (const match of html.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"[^>]*>/g)) nodes.set(match[2], new NodeDouble(match[1]));
  const get = (id) => {assert.ok(nodes.has(id), `HTML must provide ${id}`); return nodes.get(id);};
  // Read the shipped options rather than inventing valid controls in the double.
  for (const id of ["agent-mode", "agent-data-mode"]) {
    const markup = html.match(new RegExp(`<select id="${id}"[^>]*>([\\s\\S]*?)</select>`));
    assert.ok(markup, `HTML must provide select ${id}`);
    for (const optionMarkup of markup[1].matchAll(/<option\b([^>]*)>([^<]*)<\/option>/g)) {
      const option = new NodeDouble("option");
      const value = optionMarkup[1].match(/\bvalue="([^"]+)"/);
      assert.ok(value, "mode options must declare an explicit value");
      option.value = value[1]; option.textContent = optionMarkup[2];
      option.disabled = /\bdisabled\b/.test(optionMarkup[1]);
      get(id).append(option);
      if (/\bselected\b/.test(optionMarkup[1])) get(id).value = option.value;
    }
  }
  const zoro = get("agent-mode").querySelector("option[value='zoro']");
  assert.ok(zoro, "shipped HTML must contain an actual opening Zoro option tag");
  const examples = ["conditions", "inspection", "schedule", "transactions"].map((key) => {const node = new NodeDouble("button"); node.dataset.agentExample = key; return node;});
  const descendants = () => {const output = []; const visit = (node) => {output.push(node); node.children.forEach(visit);}; [...nodes.values()].forEach(visit); return output;};
  const requests = [];
  const writes = [];
  const context = vm.createContext({
    document: {getElementById: get, createElement: (tag) => new NodeDouble(tag), querySelectorAll: (selector) => {
      if (selector === "[data-agent-example]") return examples;
      if (selector === "[data-agent-proposal]") return descendants().filter((node) => Object.hasOwn(node.dataset, "agentProposal"));
      if (selector === "#agent-resume-form input, #agent-resume-form button") return [get("agent-session-id"), get("agent-from-workbench")];
      return [];
    }},
    window: {addEventListener() {}}, crypto: webcrypto, AbortController, Uint8Array, Intl, TypeError, Date,
    setTimeout: (...args) => {const timer = setTimeout(...args); timer.unref(); return timer;}, clearTimeout,
    sessionStorage: {getItem: () => saved, setItem: (key, value) => {writes.push({key, value}); saved = value;}},
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/agent.js"), "utf8"), context);
  const respond = async (index, body, code = 200) => {assert.ok(requests[index], `request ${index} exists`); requests[index].resolve({ok: code < 400, status: code, json: async () => body}); await flush();};
  return {get, zoro, examples, requests, writes, respond, body: (index) => JSON.parse(requests[index].options.body), async ready(available = false) {await respond(0, statusResponse(available));}, async load(next) {get("agent-session-id").value = next.session_id; get("agent-resume-form").requestSubmit(); await respond(requests.length - 1, next);}, async start(next = session()) {get("agent-new").click(); await respond(requests.length - 1, next);}, type(text) {get("agent-message").value = text; get("agent-message").dispatch("input");}, send() {get("agent-chat-form").requestSubmit();}};
}

test("default choices are honest and examples only fill the composer", async () => {
  const ui = harness();
  await ui.ready();
  assert.equal(ui.get("agent-mode").value, "development");
  assert.equal(ui.get("agent-data-mode").value, "sample");
  assert.equal(ui.zoro.disabled, true);
  assert.match(ui.get("agent-mode-description").textContent, /범용 자연어 모델이 아닙니다/);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.writes.length, 0);
  for (const button of ui.examples) {button.click(); assert.ok(ui.get("agent-message").value.length > 10); assert.equal(ui.requests.length, 1);}
  assert.equal(ui.get("agent-data-mode").value, "sample");
  assert.match(ui.get("agent-message").value, /2026년 10월.*전월세/);
});

test("first send creates empty conditions and sends one revision-bound UUID request", async () => {
  const ui = harness();
  await ui.ready();
  ui.type("보증금 10억 원 이하"); ui.send(); ui.send();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].url, "/api/sessions");
  assert.deepEqual(ui.body(1), {});
  await ui.respond(1, session());
  assert.equal(ui.requests.length, 3);
  const body = ui.body(2);
  assert.equal(body.expected_revision, 0);
  assert.equal(body.mode, "development"); assert.equal(body.data_mode, "sample");
  assert.match(body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const pending = {id: "pending-one", tool: "update_conditions", arguments: {conditions: {hard: {max_deposit_krw: 1000000000}}}, summary: "예산 조건 변경", created_revision: 1};
  await ui.respond(2, session({revision: 1, agent: agentState({pending, status: "awaiting_confirmation", text: "<img src=x onerror=alert(1)>", trace: [{tool: "update_conditions", arguments: pending.arguments, summary: "예산 변경 대기", status: "pending_confirmation"}]})}));
  assert.equal(ui.get("agent-pending").hidden, false);
  assert.match(ui.get("agent-review-fields").textContent, /미지정 → 10억 원/);
  assert.match(ui.get("agent-conditions").textContent, /보증금 상한미지정/);
  assert.match(ui.get("agent-transcript").textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(ui.get("agent-message").disabled, true);
  assert.equal(ui.get("agent-confirm").disabled, false);
  assert.ok(ui.writes.every((write) => write.key === "realty-workbench-session-id" && write.value === sessionId));
});

test("confirm and cancel carry exact pending ID/revision and suppress duplicate clicks", async () => {
  const ui = harness(); await ui.ready();
  const pending = {id: "review-42", tool: "set_listing_status", arguments: {listing_id: "L001", status: "contracted"}, summary: "상태 변경", created_revision: 4};
  await ui.load(session({revision: 4, agent: agentState({pending, status: "awaiting_confirmation"})}));
  ui.get("agent-confirm").click(); ui.get("agent-confirm").click();
  assert.equal(ui.requests.length, 3);
  assert.equal(ui.requests[2].url, `/api/sessions/${sessionId}/chat/confirm`);
  assert.equal(ui.body(2).pending_id, "review-42"); assert.equal(ui.body(2).approve, true); assert.equal(ui.body(2).expected_revision, 4);
  await ui.respond(2, session({revision: 5, agent: agentState()}));
  assert.equal(ui.get("agent-pending").hidden, true);
  await ui.load(session({revision: 6, agent: agentState({pending, status: "awaiting_confirmation"})}));
  ui.get("agent-cancel").click();
  assert.equal(ui.body(4).approve, false); assert.equal(ui.body(4).expected_revision, 6);
  assert.notEqual(ui.body(2).request_id, ui.body(4).request_id);
  await ui.respond(4, session({revision: 7, agent: agentState()}));
  assert.match(ui.get("agent-status").textContent, /취소했습니다/);
});

test("revision conflict blocks further writes until explicit reload and preserves draft", async () => {
  const ui = harness(); await ui.ready(); await ui.start();
  ui.type("후보 다시 검색해줘"); ui.send();
  await ui.respond(2, {error: {code: "REVISION_CONFLICT", message: "변경되었습니다"}}, 409);
  assert.equal(ui.get("agent-conflict").classList.contains("hidden"), false);
  assert.equal(ui.get("agent-message").value, "후보 다시 검색해줘");
  ui.send(); assert.equal(ui.requests.length, 3);
  ui.get("agent-conflict-reload").click();
  assert.equal(ui.requests[3].options.method, "GET");
  await ui.respond(3, session({revision: 3}));
  assert.equal(ui.get("agent-message").disabled, false);
  assert.equal(ui.get("agent-conflict").classList.contains("hidden"), true);
  ui.send(); assert.equal(ui.body(4).expected_revision, 3);
  await ui.respond(4, session({revision: 4, agent: agentState()}));
});

test("uncertain transport failure never silently retries a write", async () => {
  const ui = harness(); await ui.ready(); await ui.start();
  ui.type("후보 검색"); ui.send();
  ui.requests[2].reject(new TypeError("private network body must not appear")); await flush();
  assert.equal(ui.requests.length, 3);
  assert.match(ui.get("agent-error").textContent, /서버에 연결하지 못했습니다/);
  assert.doesNotMatch(ui.get("agent-error").textContent, /private network/);
  assert.equal(ui.get("agent-send").disabled, true);
  ui.send(); assert.equal(ui.requests.length, 3);
});

test("Zoro configuration and selected live data do not claim model execution", async () => {
  const ui = harness(); await ui.ready(true); await ui.start();
  assert.equal(ui.zoro.disabled, false);
  assert.match(ui.get("agent-execution-badge").textContent, /실행 전/);
  ui.get("agent-mode").value = "zoro"; ui.get("agent-mode").dispatch("change");
  ui.get("agent-data-mode").value = "live"; ui.get("agent-data-mode").dispatch("change");
  assert.match(ui.get("agent-data-description").textContent, /실패하면 가상 자료로 대체하지 않습니다/);
  ui.type("2026년 10월 매매 실거래 조회"); ui.send();
  assert.equal(ui.body(2).mode, "zoro"); assert.equal(ui.body(2).data_mode, "live");
  await ui.respond(2, session({revision: 1, agent: agentState({mode: "zoro", status: "error", text: "서버 응답 오류"})}));
  assert.match(ui.get("agent-execution-badge").textContent, /사용 완료 안 됨/);
  assert.equal(ui.get("agent-mode").value, "zoro");
  await ui.load(session({revision: 2, agent: agentState({mode: "zoro", zoro_used: true})}));
  assert.match(ui.get("agent-execution-badge").textContent, /Zoro 서버 응답 사용/);
});

test("historical evidence sources remain distinct and unsafe text is never parsed", async () => {
  const ui = harness(); await ui.ready();
  const observation = {source: "molit", synthetic: false, kind: "trade", matched_count: 1, scanned_count: 200, coverage: "partial", warnings: ["일부 자료만 조회"], items: [{apartment_name: "<script>attack</script>", contract_date: "2026-10-01", area_m2: 84, price_krw: 1000000000, cancelled: null, cancellation_status_available: false}]};
  await ui.load(session({agent: agentState({trace: [{tool: "lookup_transactions", arguments: {kind: "trade", deal_ymd: "202610"}, status: "executed", summary: "1건 조회", observation}]})}));
  const text = ui.get("agent-trace").textContent;
  assert.match(text, /국토부 실제 조회 · 과거 기록/); assert.match(text, /일부 자료 검토/);
  assert.match(text, /<script>attack<\/script>/); assert.match(text, /해제 여부 미확인/);
  assert.doesNotMatch(text, /실제 모델 검증 완료/);
  observation.source = "sample"; observation.synthetic = true;
  await ui.load(session({agent: agentState({trace: [{tool: "lookup_transactions", status: "executed", summary: "가상 예시", observation}]})}));
  assert.match(ui.get("agent-trace").textContent, /가상 거래 응답 · 실제 가격 아님/);
});

test("keyboard composition does not submit and workbench reuses the exact session", async () => {
  const ui = harness(sessionId); await ui.ready();
  assert.equal(ui.get("agent-session-id").value, sessionId);
  assert.equal(ui.requests.length, 1);
  ui.type("조건 검색");
  ui.get("agent-message").dispatch("keydown", {key: "Enter", isComposing: true});
  ui.get("agent-message").dispatch("keydown", {key: "Enter", shiftKey: true});
  assert.equal(ui.requests.length, 1);
  await ui.load(session());
  let manualLoads = 0; ui.get("workbench-resume-form").addEventListener("submit", () => {manualLoads += 1;});
  ui.get("agent-open-workbench").click();
  assert.equal(ui.get("workbench-session-id").value, sessionId); assert.equal(manualLoads, 1);
});

test("stale proposals stay disabled while current proposals become usable after load", async () => {
  const ui = harness(); await ui.ready();
  await ui.load(session({revision: 2, schedule_proposals: [{id: "old", based_on_revision: 1, summary: "이전 제안", events: []}, {id: "current", based_on_revision: 2, summary: "새 제안", events: []}]}));
  const cards = ui.get("agent-schedule").children.filter((node) => node.className === "agent-proposal-card");
  const old = cards[0].children.at(-1); const current = cards[1].children.at(-1);
  assert.equal(old.disabled, true); assert.equal(current.disabled, false);
  current.click();
  assert.equal(ui.get("agent-message").value, "2번 일정 대안 적용해줘");
  assert.equal(ui.requests.length, 2);
});

test("rolled-back actions are not presented as completed or saved", async () => {
  const ui = harness(); await ui.ready();
  await ui.load(session({agent: agentState({status: "error", trace: [{tool: "update_conditions", arguments: {conditions: {hard: {max_deposit_krw: 800000000}}}, status: "rolled_back", summary: "후속 처리 실패로 상태 변경을 반영하지 않았습니다."}, {tool: "error", status: "error", summary: "도구 응답 오류"}]})}));
  assert.match(ui.get("agent-trace").textContent, /상태 변경 미반영/);
  assert.match(ui.get("agent-trace").textContent, /요청 중단/);
  assert.match(ui.get("agent-conditions").textContent, /보증금 상한미지정/);
  assert.doesNotMatch(ui.get("agent-trace").textContent, /실행 완료/);
});

test("status refresh recovers both send controls and stale failure copy", async () => {
  const ui = harness();
  await ui.respond(0, {error: {message: "잠시 대기"}}, 503);
  ui.type("후보 다시 검색해줘");
  assert.equal(ui.get("agent-send").disabled, true);
  assert.match(ui.get("agent-status").textContent, /상태를 확인하지 못했습니다/);
  ui.get("refresh-health").click();
  await ui.respond(1, statusResponse());
  assert.equal(ui.get("agent-send").disabled, false);
  assert.match(ui.get("agent-status").textContent, /사용할 수 있습니다/);
  assert.doesNotMatch(ui.get("agent-status").textContent, /확인하지 못했습니다/);
});

test("superseded status failures cannot disable a recovered agent", async () => {
  const ui = harness();
  ui.get("refresh-health").click();
  await ui.respond(1, statusResponse());
  await ui.respond(0, {error: {}}, 500);
  ui.type("후보 다시 검색해줘");
  assert.equal(ui.get("agent-send").disabled, false);
  assert.doesNotMatch(ui.get("agent-status").textContent, /확인하지 못했습니다/);
});

test("Korean amounts use exact 억/만/원 groups and retain one-won boundaries", async () => {
  const ui = harness(); await ui.ready();
  const next = session();
  next.conditions.hard.max_deposit_krw = 1000000001;
  next.search_result = {status: "ready", eligible: [{listing: {id: "L002", name: "가상 후보", deposit_krw: 980000000, area_m2: 84, neighborhood: "삼성동"}, reasons: []}], needs_verification: [], excluded: []};
  next.agent = agentState({trace: [{tool: "lookup_transactions", status: "executed", summary: "가상 전세", observation: {source: "sample", synthetic: true, kind: "rent", coverage: "complete", scanned_count: 1, matched_count: 1, items: [{apartment_name: "가상 주택", contract_date: "2026-10-01", area_m2: 84, deposit_krw: 123450001, monthly_rent_krw: 0}], warnings: []}}]});
  await ui.load(next);
  assert.match(ui.get("agent-conditions").textContent, /10억 1 원/);
  assert.match(ui.get("agent-candidates").textContent, /9억 8,000만 원/);
  assert.match(ui.get("agent-trace").textContent, /1억 2,345만 1 원/);
  assert.match(ui.get("agent-trace").textContent, /월세 0원/);
  assert.doesNotMatch(ui.get("agent-conditions").textContent, /1,000,000,001원/);
});
