/* Offline transaction UI tests. This is a DOM-shaped test double, not a browser. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

class NodeDouble {
  constructor(tag = "div", text = "") {
    this.tagName = tag;
    this.children = [];
    this.ownText = text;
    this.classes = new Set();
    this.attributes = {};
    this.listeners = {};
    this.value = "";
    this.disabled = false;
    this.dataset = {};
    this.classList = {
      add: (...names) => names.forEach((name) => this.classes.add(name)),
      remove: (...names) => names.forEach((name) => this.classes.delete(name)),
      contains: (name) => this.classes.has(name),
      toggle: (name, force = !this.classes.has(name)) => {
        if (force) this.classes.add(name); else this.classes.delete(name);
        return force;
      },
    };
  }
  get className() { return [...this.classes].join(" "); }
  set className(value) { this.classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
  get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.ownText = ""; this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name) { for (const callback of this.listeners[name] || []) callback({target: this, preventDefault() {}}); }
  reportValidity() { return true; }
}

function harness() {
  const nodes = new Map();
  const get = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble());
    return nodes.get(selector);
  };
  get("input[name='mode']:checked").value = "sample";
  get("#transaction-kind").value = "rent";
  get("#lawd-cd").value = "11680";
  get("#deal-month").value = "2026-10";
  const requests = [];
  const context = vm.createContext({
    document: {
      querySelector: get,
      querySelectorAll: () => [],
      createElement: (tag) => new NodeDouble(tag),
      createTextNode: (text) => new NodeDouble("#text", text),
    },
    URLSearchParams,
    AbortController,
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const bootstrap = "Promise.allSettled([loadHealth(), loadListings(), loadPreparation()]);";
  const boundary = source.indexOf(bootstrap);
  assert.ok(boundary > 0, "UI startup boundary must remain explicit");
  vm.runInContext(source.slice(0, boundary), context);
  return {
    get,
    requests,
    run: (code) => vm.runInContext(code, context),
    response: (index, body, ok = true) => requests[index].resolve({ok, json: async () => body}),
    setHealth: (trade, rent) => vm.runInContext(`state.health = {molit: {trade_key_configured: ${trade}, rent_key_configured: ${rent}}}; updateTransactionControls()`, context),
  };
}

function page(overrides = {}) {
  return {
    source: "synthetic_sample", synthetic: true, kind: "rent", lawd_cd: "11680",
    deal_ymd: "202610", page_no: 1, num_of_rows: 20, total_count: 1,
    returned_count: 1, skipped_count: 0, has_next: false,
    retrieved_at: "2026-11-12T02:00:00Z", warnings: [],
    items: [{apartment_name: "가상 주택", legal_dong: "가상동", contract_date: "2026-10-12", area_m2: 84.5, floor: 0, price_krw: null, deposit_krw: 800000000, monthly_rent_krw: 0, lease_type: "jeonse", cancelled: false, cancellation_status_available: false}],
    ...overrides,
  };
}

test("separate key configuration enables only the selected live API", () => {
  const ui = harness();
  ui.get("input[name='mode']:checked").value = "live";
  ui.setHealth(true, false);
  assert.equal(ui.get("#transaction-submit").disabled, true);
  ui.get("#transaction-kind").value = "trade";
  ui.get("#transaction-form").dispatch("change");
  assert.equal(ui.get("#transaction-submit").disabled, false);
  assert.match(ui.get("#mode-description").textContent, /실패하면 오류/);
  ui.setHealth(false, false);
  assert.equal(ui.get("#transaction-submit").disabled, true);
  ui.get("input[name='mode']:checked").value = "sample";
  ui.get("#transaction-form").dispatch("change");
  assert.equal(ui.get("#transaction-submit").disabled, false);
});

test("configured keys are described as configuration rather than completed verification", async () => {
  const ui = harness();
  const pending = ui.run("loadHealth()");
  ui.response(0, {counts: {listings: 20, scenarios: 20}, molit: {trade_key_configured: true, rent_key_configured: true, verified: false}, zoro: {configured: true, verified: false}});
  await pending;
  assert.equal(ui.get("#molit-status").textContent, "키 설정됨");
  assert.match(ui.get("#molit-detail").textContent, /실제 조회 별도 검증/);
  assert.match(ui.get("#zoro-detail").textContent, /실제 연결·판단 미검증/);
  assert.equal(ui.get("#refresh-health").disabled, false);
});

test("a failed health refresh no longer leaves live queries enabled", async () => {
  const ui = harness();
  ui.get("input[name='mode']:checked").value = "live";
  ui.setHealth(true, true);
  const pending = ui.run("loadHealth()");
  ui.requests[0].reject(new Error("offline failure"));
  await pending;
  assert.equal(ui.get("#molit-status").textContent, "상태 확인 실패");
  assert.equal(ui.get("#transaction-submit").disabled, true);
  assert.equal(ui.run("state.health"), null);
});

test("a mode change clears data and a late canceled response cannot restore it", async () => {
  const ui = harness();
  const pending = ui.run("loadTransactions()");
  assert.equal(ui.get("#transaction-submit").disabled, true);
  ui.get("input[name='mode']:checked").value = "live";
  ui.get("#transaction-form").dispatch("change");
  assert.equal(ui.requests[0].options.signal.aborted, true);
  ui.response(0, page()); // Even a transport that ignores AbortSignal must be harmless.
  await pending;
  assert.equal(ui.run("state.transactions"), null);
  assert.equal(ui.get("#transaction-result").attributes["aria-busy"], "false");
  assert.match(ui.get("#transaction-result").textContent, /조회할 자료를 선택하세요/);
  assert.equal(ui.get("#transaction-submit").disabled, true);
});

test("editing a query before blur clears completed results and pagination", async () => {
  const ui = harness();
  const pending = ui.run("loadTransactions()");
  ui.response(0, page({total_count: 21, has_next: true}));
  await pending;
  assert.equal(ui.get("#next-page").disabled, false);
  ui.get("#lawd-cd").value = "11110";
  ui.get("#transaction-form").dispatch("input"); // No change/blur event.
  assert.equal(ui.run("state.transactions"), null);
  assert.equal(ui.run("state.transactionQuery"), null);
  assert.match(ui.get("#transaction-result").textContent, /조회할 자료를 선택하세요/);
  assert.equal(ui.get("#transaction-pagination").classList.contains("hidden"), true);
  assert.equal(ui.get("#previous-page").disabled, true);
  assert.equal(ui.get("#next-page").disabled, true);
});

test("editing a query before blur aborts its request and ignores a late result", async () => {
  const ui = harness();
  const pending = ui.run("loadTransactions()");
  ui.get("#lawd-cd").value = "11110";
  ui.get("#transaction-form").dispatch("input"); // No change/blur event.
  assert.equal(ui.requests[0].options.signal.aborted, true);
  assert.equal(ui.get("#transaction-result").attributes["aria-busy"], "false");
  assert.equal(ui.get("#transaction-submit").disabled, false);
  ui.response(0, page());
  await pending;
  assert.equal(ui.run("state.transactions"), null);
  assert.match(ui.get("#transaction-result").textContent, /조회할 자료를 선택하세요/);
});

test("an older request cannot clear the newer request's loading state or results", async () => {
  const ui = harness();
  const first = ui.run("loadTransactions()");
  const second = ui.run("loadTransactions()");
  ui.requests[0].reject(Object.assign(new Error("aborted"), {name: "AbortError"}));
  await first;
  assert.equal(ui.get("#transaction-submit").disabled, true);
  assert.equal(ui.get("#transaction-result").attributes["aria-busy"], "true");
  ui.response(1, page());
  await second;
  assert.equal(ui.get("#transaction-submit").disabled, false);
  assert.match(ui.get("#transaction-result").textContent, /가상 주택/);
});

test("live requests reject a fabricated response and do not fall back", async () => {
  const ui = harness();
  ui.get("input[name='mode']:checked").value = "live";
  ui.setHealth(true, true);
  const pending = ui.run("loadTransactions()");
  ui.response(0, page());
  await pending;
  assert.equal(ui.run("state.transactions"), null);
  assert.match(ui.get("#transaction-error").textContent, /응답 출처가 일치하지 않아/);
  assert.doesNotMatch(ui.get("#transaction-result").textContent, /가상 주택/);
  assert.equal(ui.requests.length, 1);
});

test("live errors remain explicit and allow a deliberate retry", async () => {
  const ui = harness();
  ui.get("input[name='mode']:checked").value = "live";
  ui.setHealth(true, true);
  const pending = ui.run("loadTransactions()");
  ui.response(0, {error: {code: "MOLIT_AUTH_ERROR", message: "인증 또는 이용 권한 확인 필요", retryable: false}}, false);
  await pending;
  assert.equal(ui.requests.length, 1);
  assert.match(ui.get("#transaction-error").textContent, /인증 또는 이용 권한/);
  assert.match(ui.get("#transaction-result").textContent, /가상 자료로 대체하지 않았습니다/);
  assert.equal(ui.get("#transaction-submit").disabled, false);
});

test("empty, all skipped, and empty later pages have distinct explanations", async () => {
  for (const [result, expected, pageVisible] of [
    [page({items: [], returned_count: 0, total_count: 0}), /조회된 거래가 없습니다/, false],
    [page({items: [], returned_count: 0, skipped_count: 1}), /응답 자료를 표시하지 못했습니다/, false],
    [page({items: [], returned_count: 0, total_count: 22, page_no: 3}), /이 페이지에 표시할 거래가 없습니다/, true],
    [page({items: [], returned_count: 0, total_count: 22, skipped_count: 20, has_next: true}), /응답 자료를 표시하지 못했습니다/, true],
  ]) {
    const ui = harness();
    ui.get("input[name='mode']:checked").value = "live";
    ui.setHealth(true, true);
    const pending = ui.run("loadTransactions()");
    ui.response(0, {...result, source: "molit", synthetic: false});
    await pending;
    assert.match(ui.get("#transaction-result").textContent, expected);
    assert.match(ui.get("#transaction-result").textContent, /국토부 실제 응답/);
    assert.equal(ui.get("#transaction-pagination").classList.contains("hidden"), !pageVisible);
  }
});

test("partially usable responses retain rows, exclusion warning, and provider total", async () => {
  const ui = harness();
  ui.get("input[name='mode']:checked").value = "live";
  ui.setHealth(true, true);
  const pending = ui.run("loadTransactions()");
  ui.response(0, page({source: "molit", synthetic: false, total_count: 22, skipped_count: 19, has_next: true}));
  await pending;
  assert.match(ui.get("#transaction-result").textContent, /가상 주택/);
  assert.match(ui.get("#transaction-result").textContent, /정규화하지 못한 항목 19건/);
  assert.equal(ui.get("#page-status").textContent, "1페이지 · 전체 22건");
  assert.equal(ui.get("#next-page").disabled, false);
  assert.equal(ui.get("#previous-page").disabled, true);
});

test("next page uses the same query and re-enables only available page controls", async () => {
  const ui = harness();
  const first = ui.run("loadTransactions()");
  ui.response(0, page({total_count: 21, has_next: true}));
  await first;
  const second = ui.run("loadTransactions(state.transactions.page_no + 1, true)");
  const request = new URL(ui.requests[1].url, "https://offline.invalid");
  assert.deepEqual(Object.fromEntries(request.searchParams), {mode: "sample", kind: "rent", lawd_cd: "11680", deal_ymd: "202610", num_of_rows: "20", page_no: "2"});
  assert.equal(ui.get("#previous-page").disabled, true);
  assert.equal(ui.get("#next-page").disabled, true);
  ui.response(1, page({page_no: 2, total_count: 21}));
  await second;
  assert.equal(ui.get("#previous-page").disabled, false);
  assert.equal(ui.get("#next-page").disabled, true);
});

test("sale and rental money render the correct fields and zero monthly rent is jeonse", async () => {
  for (const [kind, item, expected, absent] of [
    ["trade", {price_krw: 1234560001, deposit_krw: null, monthly_rent_krw: null, lease_type: "trade", cancelled: true, cancelled_date: "2026-10-20"}, ["매매금액", "12억 3,456만 1 원", "해제 신고", "2026.10.20"], "월세 미확인"],
    ["rent", {price_krw: null, deposit_krw: 800000000, monthly_rent_krw: 0, lease_type: "jeonse", cancelled: false, cancellation_status_available: false}, ["보증금 / 월세", "8억 원", "월세 0원", "전세", "해제 여부 확인 불가"], "매매금액"],
    ["rent", {price_krw: null, deposit_krw: 300000000, monthly_rent_krw: 1500000, lease_type: "monthly_rent", cancelled: false, cancellation_status_available: false}, ["3억 원", "월세 150만 원", "해제 여부 확인 불가"], "매매금액"],
  ]) {
    const ui = harness();
    ui.get("input[name='mode']:checked").value = "live";
    ui.setHealth(true, true);
    ui.get("#transaction-kind").value = kind;
    const pending = ui.run("loadTransactions()");
    ui.response(0, page({source: "molit", synthetic: false, kind, items: [{...page().items[0], ...item}]}));
    await pending;
    const text = ui.get("#transaction-result").textContent;
    for (const value of expected) assert.ok(text.includes(value), `${kind}: ${value}`);
    assert.ok(!text.includes(absent));
    assert.ok(text.includes("0층"));
  }
});
