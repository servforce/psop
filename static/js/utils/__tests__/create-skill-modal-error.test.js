const fs = require("fs");
const path = require("path");
const vm = require("vm");

const corePath = path.join(__dirname, "../../app/core.js");
const skillDetailPath = path.join(__dirname, "../../app/skill-detail.js");
const createModalPath = path.join(__dirname, "../../../pages/create-skill-modal.html");

const helperNames = [
  "normalizePath",
  "resolveAdminRoute",
  "buildSkillDetailPath",
  "buildRunLivePath",
  "buildSkillRunLivePath",
  "buildSkillDebugRunLivePath",
  "buildReplayPath",
  "buildSkillReplayPath",
  "buildSkillTestScenarioPath",
  "buildSkillTestScenarioNewPath",
  "buildSkillTestScenarioRunReviewPath",
  "buildCompilerArtifactPath",
  "generateSkillKey",
  "resolveApiBaseUrl",
  "resolveWsUrl",
  "escapeHtml",
  "highlightJson",
  "highlightYamlScalar",
  "highlightYaml",
  "renderInlineMarkdown",
  "renderMarkdown"
];

function helperMap(overrides = {}) {
  return {
    ...Object.fromEntries(helperNames.map((name) => [name, jest.fn()])),
    ...overrides
  };
}

function loadCoreMethods() {
  const context = {
    fetch: jest.fn(),
    window: {
      PSOPConsoleHelpers: helperMap()
    }
  };
  vm.runInNewContext(fs.readFileSync(corePath, "utf8"), context);
  return context.window.PSOPConsoleCoreMethods;
}

function loadSkillDetailMethods(overrides = {}) {
  const context = {
    window: {
      PSOPConsoleHelpers: helperMap(overrides)
    }
  };
  vm.runInNewContext(fs.readFileSync(skillDetailPath, "utf8"), context);
  return context.window.PSOPConsoleSkillDetailMethods;
}

test("create skill failure is rendered inside the create modal", async () => {
  const methods = loadSkillDetailMethods({
    generateSkillKey: jest.fn(() => "test-skill"),
    buildSkillDetailPath: jest.fn((skillId) => `/admin/skills/${skillId}`)
  });
  const app = {
    busy: { create: false },
    createForm: { name: "测试 Skill", description: "" },
    createFormError: "旧错误",
    createModalOpen: true,
    clearNotice: jest.fn(),
    showNotice: jest.fn(),
    navigate: jest.fn(),
    apiRequest: jest.fn(async () => {
      throw new Error("GitLab 项目创建失败。");
    })
  };

  await methods.createSkill.call(app);

  expect(app.createFormError).toBe("GitLab 项目创建失败。");
  expect(app.createModalOpen).toBe(true);
  expect(app.busy.create).toBe(false);
  expect(app.showNotice).not.toHaveBeenCalled();
});

test("create modal clears stale form errors when opened or closed", () => {
  const methods = loadCoreMethods();
  const app = {
    busy: { create: false },
    createForm: { name: "旧名称", description: "旧描述" },
    createFormError: "旧错误",
    createModalOpen: false
  };

  methods.openCreateModal.call(app);
  expect(app.createForm).toEqual({ name: "", description: "" });
  expect(app.createFormError).toBe("");
  expect(app.createModalOpen).toBe(true);

  app.createFormError = "关闭前错误";
  methods.closeCreateModal.call(app);
  expect(app.createFormError).toBe("");
  expect(app.createModalOpen).toBe(false);
});

test("create modal owns its submit error message markup", () => {
  const html = fs.readFileSync(createModalPath, "utf8");

  expect(html).toContain('x-if="createFormError"');
  expect(html).toContain('x-text="createFormError"');
});
