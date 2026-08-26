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

function loadSkillDetailMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/skill-detail.js"), "utf8");
  const sandbox = { window: { PSOPConsoleHelpers: {} }, URLSearchParams };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleSkillDetailMethods;
}

test("skill runtime page loads runs and omits invocation id", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/skill-detail.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  const skillDetailJs = fs.readFileSync(path.join(__dirname, "../../app/skill-detail.js"), "utf8");

  expect(html).toContain('x-model="runtimeFilters.status"');
  expect(html).toContain('@change="loadSkillRuns(currentSkill.id, $event.target.value, { page: 1 })"');
  expect(html).toContain('<option value="queued">排队中</option>');
  expect(html).toContain('<option value="waiting_runtime">等待运行</option>');
  expect(html).toContain('<option value="waiting_input">等待输入</option>');
  expect(html).toContain('x-for="run in currentSkillFilteredRuns()"');
  expect(html).toContain("changeSkillRunPage(skillRunPagination.page - 1)");
  expect(html).toContain("changeSkillRunPage(skillRunPagination.page + 1)");
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
  const response = { items: runs, total: 1, page: 1, page_size: 20, total_pages: 1 };
  const context = {
    ...methods,
    busy: { skillRuns: false },
    skillRuns: [],
    skillRunPagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    runtimeFilters: { status: "waiting_input", created_from: "", created_to: "" },
    apiRequest: jest.fn().mockResolvedValue(response)
  };

  await methods.loadSkillRuns.call(context, "skill-1");

  expect(context.apiRequest).toHaveBeenCalledWith("/runs?skill_id=skill-1&status=waiting_input&page=1&page_size=20");
  expect(context.skillRuns).toEqual(runs);
  expect(context.skillRunPagination.total).toBe(1);
  expect(context.busy.skillRuns).toBe(false);

  context.runtimeFilters.status = "";
  await methods.loadSkillRuns.call(context, "skill-1");
  expect(context.apiRequest).toHaveBeenLastCalledWith("/runs?skill_id=skill-1&page=1&page_size=20");
});

test("skill list sends pagination and stores page metadata", async () => {
  const methods = loadSkillDetailMethods();
  const skills = [{ id: "skill-1", name: "Skill 1" }];
  const context = {
    ...methods,
    busy: { list: false },
    skills: [],
    skillPagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    filters: { search: "pump", published_state: "published", created_from: "", created_to: "" },
    apiRequest: jest.fn().mockResolvedValue({
      items: skills,
      total: 21,
      page: 2,
      page_size: 20,
      total_pages: 2
    })
  };

  await methods.loadSkills.call(context, { page: 2 });

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/skills?search=pump&is_published=true&page=2&page_size=20"
  );
  expect(context.skills).toEqual(skills);
  expect(context.skillPagination).toEqual({
    page: 2,
    page_size: 20,
    total: 21,
    total_pages: 2
  });
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
