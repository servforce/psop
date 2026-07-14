const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadRuntimeMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");
  const sandbox = { window: { PSOPConsoleHelpers: {} }, URLSearchParams };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleRuntimeMethods;
}

test("skill runtime page loads runs and omits invocation id", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/skill-detail.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  const skillDetailJs = fs.readFileSync(path.join(__dirname, "../../app/skill-detail.js"), "utf8");

  expect(html).toContain('x-model="runtimeFilters.status"');
  expect(html).toContain('@change="loadSkillRuns(currentSkill.id, $event.target.value)"');
  expect(html).toContain('<option value="queued">排队中</option>');
  expect(html).toContain('<option value="waiting_runtime">等待运行</option>');
  expect(html).toContain('<option value="waiting_input">等待输入</option>');
  expect(html).toContain('x-for="run in currentSkillFilteredRuns()"');
  expect(html).toContain('x-text="run.id"');
  expect(html).not.toContain('<span>Invocation</span>');
  expect(html).not.toContain('x-text="invocation.id"');
  expect(appJs).toContain("skillRuns: []");
  expect(skillDetailJs).toContain("await this.loadSkillRuns(detail.id)");
  expect(skillDetailJs).toContain("await this.loadSkillRuns(this.currentSkill.id)");
  expect(skillDetailJs).not.toContain("await this.loadInvocations(detail.key)");
});

test("skill run list sends skill and status filters to the runs endpoint", async () => {
  const methods = loadRuntimeMethods();
  const runs = [{ id: "run-1", status: "waiting_input" }];
  const context = {
    ...methods,
    busy: { skillRuns: false },
    skillRuns: [],
    runtimeFilters: { status: "waiting_input" },
    apiRequest: jest.fn().mockResolvedValue(runs)
  };

  await methods.loadSkillRuns.call(context, "skill-1");

  expect(context.apiRequest).toHaveBeenCalledWith("/runs?skill_id=skill-1&status=waiting_input");
  expect(context.skillRuns).toBe(runs);
  expect(context.busy.skillRuns).toBe(false);

  context.runtimeFilters.status = "";
  await methods.loadSkillRuns.call(context, "skill-1");
  expect(context.apiRequest).toHaveBeenLastCalledWith("/runs?skill_id=skill-1");
});

test("skill run list keeps creation date filtering on loaded runs", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    skillRuns: [
      { id: "in-range", created_at: "2026-07-14T10:00:00Z" },
      { id: "out-of-range", created_at: "2026-07-13T11:00:00Z" }
    ],
    runtimeFilters: { created_from: "2026-07-14", created_to: "2026-07-14" },
    inDateRange(value, from, to) {
      const time = new Date(value).getTime();
      return time >= new Date(`${from}T00:00:00`).getTime() && time <= new Date(`${to}T23:59:59.999`).getTime();
    }
  };

  expect(methods.currentSkillFilteredRuns.call(context).map((item) => item.id)).toEqual(["in-range"]);
});
