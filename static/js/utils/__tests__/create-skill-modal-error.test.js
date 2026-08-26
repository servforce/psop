const fs = require("fs");
const path = require("path");
const vm = require("vm");

const corePath = path.join(__dirname, "../../app/core.js");
const skillDetailPath = path.join(__dirname, "../../app/skill-detail.js");
const createModalPath = path.join(__dirname, "../../../pages/create-skill-modal.html");
const deleteModalPath = path.join(__dirname, "../../../pages/delete-skill-modal.html");

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

function loadCoreMethods(windowOverrides = {}) {
  const context = {
    fetch: jest.fn(),
    window: {
      PSOPConsoleHelpers: helperMap(),
      ...windowOverrides
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

test("create skill sends only name and description", async () => {
  const methods = loadSkillDetailMethods({
    buildSkillDetailPath: jest.fn((skillId) => `/admin/skills/${skillId}`)
  });
  const created = { id: "skill-id", name: "测试 Skill", description: "测试描述" };
  const app = {
    busy: { create: false },
    createForm: { name: "测试 Skill", description: "测试描述" },
    createFormError: "",
    createModalOpen: true,
    clearNotice: jest.fn(),
    showNotice: jest.fn(),
    navigate: jest.fn(),
    apiRequest: jest.fn(async () => created)
  };

  await methods.createSkill.call(app);

  expect(app.apiRequest).toHaveBeenCalledWith("/skills", {
    method: "POST",
    body: JSON.stringify({ name: "测试 Skill", description: "测试描述" })
  });
  expect(app.navigate).toHaveBeenCalledWith("/admin/skills/skill-id", { skillDetail: created });
  expect(app.createModalOpen).toBe(false);
});

test("navigation forwards prefetched skill detail to route loading", async () => {
  const pushState = jest.fn();
  const methods = loadCoreMethods({
    location: { pathname: "/admin/skills" },
    history: { pushState }
  });
  const skillDetail = { id: "skill-id" };
  const app = {
    syncRoute: jest.fn(),
    loadCurrentRoute: jest.fn()
  };

  await methods.navigate.call(app, "/admin/skills/skill-id", { skillDetail });

  expect(pushState).toHaveBeenCalledWith({}, "", "/admin/skills/skill-id");
  expect(app.syncRoute).toHaveBeenCalled();
  expect(app.loadCurrentRoute).toHaveBeenCalledWith({ skillDetail });
});

test("skill detail route forwards the create response to detail loading", async () => {
  const methods = loadCoreMethods();
  const skillDetail = { id: "skill-id" };
  const app = {
    loadingPage: false,
    route: { name: "skill-detail", params: { skillId: "skill-id" } },
    activeDetailTab: "overview",
    clearNotice: jest.fn(),
    destroyLiveRunView: jest.fn(),
    destroyCompilerArtifactViewer: jest.fn(),
    closeCompilerArtifactNodeDrawer: jest.fn(),
    loadSkillDetail: jest.fn(),
    scheduleButtonTooltipRefresh: jest.fn()
  };

  await methods.loadCurrentRoute.call(app, { skillDetail });

  expect(app.loadSkillDetail).toHaveBeenCalledWith("skill-id", { initialDetail: skillDetail });
  expect(app.loadingPage).toBe(false);
});

test("skill detail loading uses prefetched create response without another API request", async () => {
  const methods = loadSkillDetailMethods();
  const detail = { id: "skill-id", name: "测试 Skill", description: "测试描述" };
  const app = {
    busy: { detail: false },
    activeDetailTab: "overview",
    apiRequest: jest.fn(),
    resetLazyDetailState: jest.fn()
  };

  await methods.loadSkillDetail.call(app, "skill-id", { initialDetail: detail });

  expect(app.apiRequest).not.toHaveBeenCalled();
  expect(app.currentSkill).toBe(detail);
  expect(app.metadataForm).toEqual({ name: "测试 Skill", description: "测试描述" });
  expect(app.resetLazyDetailState).toHaveBeenCalledWith("skill-id");
  expect(app.busy.detail).toBe(false);
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

test("create modal marks fields as required and limits the description to 500 characters", () => {
  const html = fs.readFileSync(createModalPath, "utf8");

  expect(html.match(/aria-label="必填">\*<\/span>/g)).toHaveLength(2);
  expect(html).toMatch(/<input[^>]*x-model="createForm\.name"[^>]*required/);
  expect(html).toMatch(/<textarea[^>]*x-model="createForm\.description"[^>]*maxlength="500"[^>]*required/);
  expect(html).toContain(`@invalid="$el.setCustomValidity('请输入 skill 名称。')"`);
  expect(html).toContain(`@invalid="$el.setCustomValidity('请输出 skill 描述。')"`);
  expect(html.match(/@input="\$el\.setCustomValidity\(''\)"/g)).toHaveLength(2);
  expect(html).toContain(`x-text="(createForm.description || '').length + ' / 500'"`);
});

test("duplicate rename failure is shown as a friendly notice", async () => {
  const methods = loadSkillDetailMethods();
  const app = {
    currentSkill: { id: "skill-id" },
    metadataForm: { name: "重复名称", description: "" },
    busy: { metadata: false },
    clearNotice: jest.fn(),
    loadSkillDetail: jest.fn(),
    showNotice: jest.fn(),
    apiRequest: jest.fn(async () => {
      throw new Error("Skill 名称“重复名称”已存在，请使用其他名称。");
    })
  };

  await methods.saveMetadata.call(app);

  expect(app.showNotice).toHaveBeenCalledWith("error", "Skill 名称“重复名称”已存在，请使用其他名称。");
  expect(app.loadSkillDetail).not.toHaveBeenCalled();
  expect(app.busy.metadata).toBe(false);
});

test("delete modal explains that the GitLab project will be deleted", () => {
  const html = fs.readFileSync(deleteModalPath, "utf8");

  expect(html).toContain("对应 GitLab 仓库项目会被删除");
  expect(html).toContain("确认删除");
  expect(html).not.toContain("确认删除并归档");
});
