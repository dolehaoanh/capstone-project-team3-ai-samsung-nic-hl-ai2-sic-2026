// Runs the dashboard <script> from GET / against a minimal fake DOM (used by
// tests/test_dashboard.py; no npm packages needed).
//
// Input (JSON file, argv[2]):
//   script     the inline script of the page
//   elements   {id: {disabled, checked, value}} for every element with an id
//   responses  queue answered by fetch(), in order: {status, body} or {throw: "message"}
//   now        ISO time the page sees as "now"
//   steps      e.g. ["click:load", "check:confirm", "select:profile=growth", "click:advise"]
// Output (stdout, JSON): {initial, states: [one per step], calls: [fetch calls]}

"use strict";
const fs = require("fs");
const vm = require("vm");

const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

class FakeElement {
  constructor(id, props) {
    this.id = id;
    this.textContent = "";
    this.innerHTML = "";
    this.disabled = Boolean(props.disabled);
    this.checked = Boolean(props.checked);
    this.value = props.value ?? "";
    this.attributes = {};
    this.onclick = null;
    this.onchange = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
}

const elements = {};
for (const [id, props] of Object.entries(input.elements)) elements[id] = new FakeElement(id, props);

const document = {
  querySelector(selector) {
    const match = /^#([\w-]+)$/.exec(selector);
    if (!match) throw new Error(`harness only supports #id selectors, got ${selector}`);
    const element = elements[match[1]];
    // A missing id would be null in a browser and crash on first use; fail loudly here.
    if (!element) throw new Error(`no element with id "${match[1]}" in the page`);
    return element;
  },
};

const snapshot = () => Object.fromEntries(Object.entries(elements).map(([id, el]) => [id, {
  textContent: el.textContent,
  innerHTML: el.innerHTML,
  disabled: el.disabled,
  checked: el.checked,
  value: el.value,
  attributes: { ...el.attributes },
}]));

const calls = [];
const queue = [...input.responses];
async function fetch(url, options = {}) {
  // Record what the page looks like while the request is in flight.
  calls.push({
    url,
    method: options.method || "GET",
    body: options.body === undefined ? null : JSON.parse(options.body),
    during: snapshot(),
  });
  const next = queue.shift();
  if (!next) throw new Error(`unexpected fetch ${url}`);
  if (next.throw) throw new TypeError(next.throw);
  return {
    ok: next.status >= 200 && next.status < 300,
    status: next.status,
    json: async () => next.body,
  };
}

const RealDate = Date;
const fixedNow = RealDate.parse(input.now);
class FixedDate extends RealDate {
  constructor(...args) { if (args.length) super(...args); else super(fixedNow); }
  static now() { return fixedNow; }
}

const context = vm.createContext({ document, fetch, Date: FixedDate, Intl, JSON, String, Math, console });
vm.runInContext(input.script, context, { filename: "dashboard.js" });

(async () => {
  const initial = snapshot();
  const states = [];
  for (const step of input.steps) {
    const [action, rest] = step.split(":");
    if (action === "click") {
      const el = elements[rest];
      if (!el.onclick) throw new Error(`#${rest} has no click handler`);
      if (el.disabled) throw new Error(`#${rest} is disabled; a user could not click it`);
      await el.onclick({ currentTarget: el });
    } else if (action === "check" || action === "uncheck") {
      elements[rest].checked = action === "check";
      if (elements[rest].onchange) await elements[rest].onchange({ currentTarget: elements[rest] });
    } else if (action === "select") {
      const [id, value] = rest.split("=");
      elements[id].value = value;
    } else {
      throw new Error(`unknown step ${step}`);
    }
    states.push(snapshot());
  }
  if (queue.length) throw new Error(`${queue.length} prepared response(s) were never requested`);
  process.stdout.write(JSON.stringify({ initial, states, calls }));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
