const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadSkillDetailMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/skill-detail.js"), "utf8");
  const helper = () => "";
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        normalizePath: helper,
        resolveAdminRoute: helper,
        buildSkillDetailPath: (skillId) => `/admin/skills/${skillId}`,
        buildRunLivePath: helper,
        buildSkillRunLivePath: helper,
        buildSkillDebugRunLivePath: helper,
        buildReplayPath: helper,
        buildSkillReplayPath: helper,
        buildSkillTestScenarioPath: helper,
        buildSkillTestScenarioNewPath: helper,
        buildSkillTestScenarioRunReviewPath: helper,
        buildCompilerArtifactPath: helper,
        generateSkillKey: helper,
        resolveApiBaseUrl: helper,
        resolveWsUrl: helper,
        escapeHtml: helper,
        highlightJson: helper,
        highlightYamlScalar: helper,
        highlightYaml: helper,
        renderInlineMarkdown: helper,
        renderMarkdown: helper
      }
    },
    Promise,
    JSON,
    URLSearchParams,
    Error,
    Date,
    String,
    Array,
    Object
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleSkillDetailMethods;
}

test("skill publish tab exposes versions and publish gate controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/skill-detail.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  const skillDetailJs = fs.readFileSync(path.join(__dirname, "../../app/skill-detail.js"), "utf8");

  expect(html).toContain("版本链路");
  expect(html).toContain("发布门禁");
  expect(html).toContain("pskillVersions.length");
  expect(html).toContain("runPublishGate()");
  expect(html).toContain("publishGateResult.result_json?.publish_gate_summary");
  expect(appJs).toContain("pskillVersionsLoadedSkillId");
  expect(appJs).toContain("publishGateResult");
  expect(appJs).toContain("publishGate: false");
  expect(skillDetailJs).toContain("/versions");
  expect(skillDetailJs).toContain("/publish-gate");
  expect(skillDetailJs).toContain("loadPublishWorkspaceData");
  expect(skillDetailJs).toContain("connectPublishActivityWebSocket");
  expect(skillDetailJs).toContain("/ws/pskills/");
});

test("skill publish methods load versions and run publish gate", async () => {
  const methods = loadSkillDetailMethods();
  const gate = {
    id: "gate-1",
    status: "review_required",
    score: 92,
    result_json: { publish_gate_summary: "需要人工复核。", checks: {} }
  };
  const context = {
    ...methods,
    busy: { publishRecords: false, pskillVersions: false, publishGate: false },
    publishRecordsLoadedSkillId: null,
    pskillVersionsLoadedSkillId: null,
    publishRecords: [],
    pskillVersions: [],
    publishGateResult: null,
    currentSkill: {
      id: "skill-1",
      latest_published_version: { id: "version-1", version_no: 1 }
    },
    apiRequest: jest.fn(async (url, options) => {
      if (url === "/pskills/skill-1/publishes") {
        return [{ id: "publish-1" }];
      }
      if (url === "/pskills/skill-1/versions") {
        return [{ id: "version-1", status: "published" }];
      }
      if (url === "/pskills/skill-1/publish-gate" && options?.method === "POST") {
        return gate;
      }
      return null;
    }),
    clearNotice: jest.fn(),
    showNotice: jest.fn()
  };

  await methods.loadPublishWorkspaceData.call(context, "skill-1");
  await methods.runPublishGate.call(context);

  expect(context.publishRecords).toEqual([{ id: "publish-1" }]);
  expect(context.pskillVersions).toEqual([{ id: "version-1", status: "published" }]);
  expect(context.publishGateResult).toEqual(gate);
  expect(context.apiRequest).toHaveBeenCalledWith("/pskills/skill-1/publishes");
  expect(context.apiRequest).toHaveBeenCalledWith("/pskills/skill-1/versions");
  const gateCall = context.apiRequest.mock.calls.find(([url]) => url === "/pskills/skill-1/publish-gate");
  expect(gateCall[1].method).toBe("POST");
  expect(JSON.parse(gateCall[1].body)).toEqual({ pskill_id: "skill-1", pskill_version_id: "version-1" });
  expect(context.showNotice).toHaveBeenCalledWith("success", "发布门禁需要人工复核。");
});

test("skill publish methods apply activity snapshots to versions, records, and progress", () => {
  const methods = loadSkillDetailMethods();
  const context = {
    ...methods,
    pskillVersions: [],
    pskillVersionsLoadedSkillId: null,
    publishRecords: [],
    publishRecordsLoadedSkillId: null,
    publishProgress: methods.emptyPublishProgress(),
    busy: { publish: true },
    currentSkill: { id: "skill-1" },
    stopPublishProgressWatchers: jest.fn(),
    loadSkillDetail: jest.fn(),
    showNotice: jest.fn()
  };
  const snapshot = {
    pskill: { id: "skill-1" },
    versions: [{ id: "version-1", status: "published" }],
    publishes: [{ id: "publish-1", publish_status: "compiling" }],
    compile_requests: [
      {
        id: "compile-1",
        status: "running",
        progress: {
          terminal: false,
          terminal_status: null,
          error_message: "",
          stages: [{ key: "source_loaded", status: "running" }]
        }
      }
    ]
  };

  methods.applyPublishActivitySnapshot.call(context, snapshot, "compile-1");

  expect(context.pskillVersions).toEqual([{ id: "version-1", status: "published" }]);
  expect(context.pskillVersionsLoadedSkillId).toBe("skill-1");
  expect(context.publishRecords).toEqual([{ id: "publish-1", publish_status: "compiling" }]);
  expect(context.publishRecordsLoadedSkillId).toBe("skill-1");
  expect(context.publishProgress.compile_request_id).toBe("compile-1");
  expect(context.publishProgress.terminal).toBe(false);
  expect(context.publishProgress.stages).toEqual([{ key: "source_loaded", status: "running" }]);
});

test("publishSkill prefers PSkill activity websocket over compile SSE", async () => {
  const methods = loadSkillDetailMethods();
  const context = {
    ...methods,
    busy: { publish: false },
    currentSkill: { id: "skill-1" },
    publishForm: { publish_reason: "ship it" },
    publishProgress: methods.emptyPublishProgress(),
    apiRequest: jest.fn(async (url, options) => {
      if (url === "/pskills/skill-1/publish" && options?.method === "POST") {
        return { compile_request: { id: "compile-1" } };
      }
      return null;
    }),
    connectPublishActivityWebSocket: jest.fn(() => true),
    startPublishEventStream: jest.fn(),
    clearNotice: jest.fn(),
    showNotice: jest.fn(),
    showCenterToast: jest.fn()
  };

  await methods.publishSkill.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/pskills/skill-1/publish", {
    method: "POST",
    body: JSON.stringify({ publish_reason: "ship it" })
  });
  expect(context.connectPublishActivityWebSocket).toHaveBeenCalledWith("skill-1", "compile-1");
  expect(context.startPublishEventStream).not.toHaveBeenCalled();
  expect(context.publishProgress.compile_request_id).toBe("compile-1");
});
