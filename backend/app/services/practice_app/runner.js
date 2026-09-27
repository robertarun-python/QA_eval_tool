// Checklist runner for a JavaScript practice app. Run as a separate process
// by practice_app.checker - same job format and output as runner.py.
// Names in checklists are written in Python style (UI.search_doctors); the
// JavaScript app's camelCase names (UI.searchDoctors) are found too.
const fs = require("fs");
const vm = require("vm");
const { createRequire } = require("module");

const snake = (name) => String(name).replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
const camel = (name) => String(name).replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());

function plain(value) {
  if (value === null || value === undefined) return null;
  if (["boolean", "number", "string"].includes(typeof value)) return value;
  if (value instanceof Set || Array.isArray(value)) return Array.from(value, plain);
  if (value instanceof Map) return Object.fromEntries(Array.from(value, ([k, v]) => [snake(k), plain(v)]));
  if (typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [snake(k), plain(v)]));
  return String(value);
}

function member(obj, name) {
  if (obj === null || obj === undefined) return undefined;
  if (name in Object(obj)) return obj[name];
  return obj[camel(name)];
}

function resolve(arg, saved) {
  if (arg && typeof arg === "object" && "ref" in arg) {
    const [first, ...rest] = arg.ref.split(".");
    return rest.reduce((value, part) => member(value, part), saved[first]);
  }
  return arg;
}

const job = JSON.parse(fs.readFileSync(0, "utf8"));
const source = fs.readFileSync(job.env_path, "utf8");
const roots = [...new Set(job.checklists.flatMap((c) => c.steps.map((s) => s.call.split(".")[0])))];
const exposeNames = roots.flatMap((r) => [r, camel(r)]);
// Top-level const/function names in a plain script aren't properties of the
// global object, so hand the ones the checklists use back explicitly.
const shim = "\n;globalThis.__practice = {" +
  exposeNames.map((n) => `${JSON.stringify(n)}: (typeof ${n} !== "undefined" ? ${n} : undefined)`).join(", ") + "};";

const results = [];
for (const checklist of job.checklists) {
  // The app's own engine module, next to it - loaded again for every checklist.
  for (const path of job.fresh_modules || []) delete require.cache[fs.realpathSync(path)];
  const context = vm.createContext({ console, require: createRequire(job.env_path) });
  try {
    vm.runInContext(source + shim, context, { filename: "practice_app.js" });
  } catch (e) {
    results.push({ id: checklist.id, load_error: `${e.name}: ${e.message}`, steps: [] });
    continue;
  }
  const scope = context.__practice;
  const saved = {};
  const steps = [];
  for (const step of checklist.steps) {
    try {
      const parts = step.call.split(".");
      let owner = null;
      let target = member(scope, parts[0]);
      for (const part of parts.slice(1)) {
        owner = target;
        target = member(target, part);
      }
      if (typeof target !== "function") throw new TypeError(`${step.call} is not a function in this app`);
      const value = target.apply(owner, (step.args || []).map((a) => resolve(a, saved)));
      if (step.save_as) saved[step.save_as] = value;
      steps.push({ value: plain(value) });
    } catch (e) {
      steps.push({ error: `${e.name}: ${e.message}` });
      break;
    }
  }
  results.push({ id: checklist.id, steps });
}
process.stdout.write(JSON.stringify(results));
