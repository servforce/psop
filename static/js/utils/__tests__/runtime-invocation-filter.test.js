const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadRuntimeMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");
  const sandbox = { window: { PSOPConsoleHelpers: {} } };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleRuntimeMethods;
}

test("skill runtime toolbar exposes the status filter", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/skill-detail.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");

  expect(html).toContain('x-model="runtimeFilters.status"');
  expect(html).toContain('<option value="">全部状态</option>');
  expect(html).toContain('<option value="running">运行中</option>');
  expect(html).toContain('<option value="succeeded">成功</option>');
  expect(html).toContain('<option value="failed">失败</option>');
  expect(html).toContain('<option value="aborted">已中止</option>');
  expect(html).toContain('<option value="cancelled">已取消</option>');
  expect(appJs).toMatch(/runtimeFilters:\s*{\s*status: ""/);
});

test("skill invocation list filters by status and creation date", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    currentSkill: { id: "skill-1" },
    invocations: [
      { id: "running-in-range", skill_definition_id: "skill-1", status: "running", created_at: "2026-07-14T10:00:00Z" },
      { id: "failed-in-range", skill_definition_id: "skill-1", status: "failed", created_at: "2026-07-14T11:00:00Z" },
      { id: "running-out-of-range", skill_definition_id: "skill-1", status: "running", created_at: "2026-07-13T11:00:00Z" },
      { id: "other-skill", skill_definition_id: "skill-2", status: "running", created_at: "2026-07-14T12:00:00Z" }
    ],
    runtimeFilters: { status: "running", created_from: "2026-07-14", created_to: "2026-07-14" },
    inDateRange(value, from, to) {
      const time = new Date(value).getTime();
      return time >= new Date(`${from}T00:00:00`).getTime() && time <= new Date(`${to}T23:59:59.999`).getTime();
    }
  };

  expect(methods.currentSkillFilteredInvocations.call(context).map((item) => item.id)).toEqual(["running-in-range"]);

  context.runtimeFilters.status = "";
  expect(methods.currentSkillFilteredInvocations.call(context).map((item) => item.id)).toEqual([
    "running-in-range",
    "failed-in-range"
  ]);
});
