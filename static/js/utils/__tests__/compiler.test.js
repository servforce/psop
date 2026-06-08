const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadCompilerMethods(locationSearch = "") {
  const code = fs.readFileSync(path.join(__dirname, "../../app/compiler.js"), "utf8");
  const helper = () => "";
  const sandbox = {
    window: {
      location: { search: locationSearch },
      PSOPConsoleHelpers: {
        normalizePath: helper,
        resolveAdminRoute: helper,
        buildRunLivePath: helper,
        buildSkillRunLivePath: helper,
        buildSkillDebugRunLivePath: helper,
        buildReplayPath: helper,
        buildSkillReplayPath: helper,
        buildSkillTestScenarioPath: helper,
        buildSkillTestScenarioNewPath: helper,
        buildSkillTestScenarioRunReviewPath: helper,
        buildCompilerArtifactPath: (artifactId) => `/admin/compiler/artifacts/${artifactId}`,
        buildSkillCompilerArtifactPath: (skillId, artifactId) => `/admin/skills/${skillId}/compiler/artifacts/${artifactId}`,
        buildPlatformAgentRunPath: (agentRunId, focus = {}) => {
          const params = new URLSearchParams();
          if (focus.tab) {
            params.set("tab", focus.tab);
          }
          const query = params.toString();
          return query
            ? `/admin/platform/agent-runs/${agentRunId}?${query}`
            : `/admin/platform/agent-runs/${agentRunId}`;
        },
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
    URLSearchParams,
    JSON,
    Number,
    String,
    Math,
    Array,
    Object
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleCompilerMethods;
}

test("compiler list page exposes progress, AgentRun, and artifact evidence actions", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/compiler-list.html"), "utf8");
  const artifactHtml = fs.readFileSync(path.join(__dirname, "../../../pages/compiler-artifact-detail.html"), "utf8");

  expect(html).toContain("compilerRequestProgressLabel(compileRequest)");
  expect(html).toContain("compilerRequestProgressBarWidth(compileRequest)");
  expect(html).toContain("compilerFilters.compile_request_id");
  expect(html).toContain("openCompilerAgentRun(compileRequest)");
  expect(html).toContain("compileRequest.agent_run_id");
  expect(html).toContain("openCompilerArtifact(compileRequest.artifact_id)");
  expect(artifactHtml).toContain("compilerArtifact.compile_request_id");
  expect(artifactHtml).toContain("compilerArtifactCompileRequest()");
  expect(artifactHtml).toContain("openCompilerArtifactAgentRun()");
});

test("compiler methods build progress and AgentRun evidence links", () => {
  const methods = loadCompilerMethods();
  const context = {
    ...methods,
    formatStatus: (value) => ({ running: "运行中", succeeded: "成功" })[value] || value,
    navigate: jest.fn()
  };
  const compileRequest = {
    id: "compile-1",
    agent_run_id: "agent-run-1",
    progress: {
      current_stage: "agent_compiling",
      current_stage_label: "智能体编译 EG",
      current_stage_status: "running",
      percent: 42
    }
  };

  expect(methods.compilerRequestProgressLabel.call(context, compileRequest)).toBe("智能体编译 EG · 运行中");
  expect(methods.compilerRequestProgressPercentLabel.call(context, compileRequest)).toBe("42%");
  expect(methods.compilerRequestProgressBarWidth.call(context, compileRequest)).toBe("42%");
  expect(methods.compilerRequestAgentRunPath.call(context, compileRequest)).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=events"
  );

  methods.openCompilerAgentRun.call(context, compileRequest);

  expect(context.navigate).toHaveBeenCalledWith("/admin/platform/agent-runs/agent-run-1?tab=events");
});

test("compiler artifact methods expose compile request evidence links", () => {
  const methods = loadCompilerMethods();
  const context = {
    ...methods,
    compilerArtifact: {
      id: "artifact-1",
      compile_request: {
        id: "compile-1",
        agent_run_id: "agent-run-1",
        progress: {
          current_stage_label: "写入编译产物",
          current_stage_status: "succeeded",
          percent: 100
        }
      }
    },
    formatStatus: (value) => ({ succeeded: "成功" })[value] || value,
    navigate: jest.fn()
  };

  expect(methods.compilerArtifactCompileRequest.call(context).id).toBe("compile-1");
  expect(methods.compilerRequestProgressLabel.call(context, methods.compilerArtifactCompileRequest.call(context))).toBe(
    "写入编译产物 · 成功"
  );

  methods.openCompilerArtifactAgentRun.call(context);

  expect(context.navigate).toHaveBeenCalledWith("/admin/platform/agent-runs/agent-run-1?tab=events");
});

test("compiler progress percentage is clamped for partial or malformed responses", () => {
  const methods = loadCompilerMethods();
  const context = { ...methods };

  expect(methods.compilerRequestProgressPercent.call(context, { progress: { percent: -10 } })).toBe(0);
  expect(methods.compilerRequestProgressPercent.call(context, { progress: { percent: 140 } })).toBe(100);
  expect(methods.compilerRequestProgressPercent.call(context, { progress: { percent: "bad" } })).toBe(0);
});

test("compiler list filters can focus a compile request from URL", () => {
  const methods = loadCompilerMethods("?compile_request_id=compile-2");
  const context = {
    ...methods,
    compilerFilters: {
      compile_request_id: "",
      skill_search: "",
      status: "",
      requested_from: "",
      requested_to: ""
    },
    compilerRequests: [
      { id: "compile-1", pskill_definition_id: "skill-1", status: "succeeded", requested_at: "2026-01-01" },
      { id: "compile-2", pskill_definition_id: "skill-2", status: "running", requested_at: "2026-01-02" }
    ],
    skills: [
      { id: "skill-1", name: "技能一" },
      { id: "skill-2", name: "技能二" }
    ],
    inDateRange: () => true
  };

  methods.syncCompilerFiltersFromLocation.call(context);

  expect(context.compilerFilters.compile_request_id).toBe("compile-2");
  expect(methods.filteredCompilerRequests.call(context).map((item) => item.id)).toEqual(["compile-2"]);

  methods.clearCompilerFilters.call(context);

  expect(context.compilerFilters.compile_request_id).toBe("");
});
