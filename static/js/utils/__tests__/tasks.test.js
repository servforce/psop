const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadFormatMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/formatters.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        normalizePath: (value) => value,
        resolveAdminRoute: () => ({}),
        buildSkillDetailPath: () => "",
        buildRunLivePath: () => "",
        buildSkillRunLivePath: () => "",
        buildSkillDebugRunLivePath: () => "",
        buildReplayPath: () => "",
        buildSkillReplayPath: () => "",
        buildSkillTestScenarioPath: () => "",
        buildSkillTestScenarioNewPath: () => "",
        buildSkillTestScenarioRunReviewPath: () => "",
        buildCompilerArtifactPath: () => "",
        buildAgentPromptPath: () => "",
        resolveApiBaseUrl: () => "",
        resolveWsUrl: () => "",
        escapeHtml: (value) => String(value || ""),
        highlightJson: (value) => String(value || ""),
        highlightYamlScalar: (value) => String(value || ""),
        highlightYaml: (value) => String(value || ""),
        renderInlineMarkdown: (value) => String(value || ""),
        renderMarkdown: (value) => String(value || "")
      }
    },
    Intl,
    Number,
    Math,
    Date,
    JSON,
    String
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleFormatMethods;
}

function loadTaskMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/tasks.js"), "utf8");
  const sandbox = {
    window: {
      setInterval: jest.fn(() => 123),
      clearInterval: jest.fn()
    },
    URLSearchParams,
    Intl,
    Number,
    Math,
    Date,
    String,
    Boolean,
    Array,
    Map
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return { methods: sandbox.window.PSOPConsoleTasksMethods, window: sandbox.window };
}

test("task formatters render duration and missing token usage", () => {
  const methods = loadFormatMethods();

  expect(methods.formatDuration(950)).toBe("950 ms");
  expect(methods.formatDuration(65_000)).toBe("1 m 5 s");
  expect(methods.formatDuration(null)).toBe("N/A");
  expect(methods.formatTokenUsage(null)).toBe("N/A");
  expect(methods.formatTokenUsage({ total_tokens: 1200 })).toBe("1,200");
  expect(methods.formatStatus("aborted")).toBe("已中止");
  expect(methods.statusBadgeTone("aborted")).toContain("rose");
});

test("tasks methods build filters and preserve unknown job types", () => {
  const { methods } = loadTaskMethods();
  const context = {
    ...methods,
    tasks: [{ job_type: "compile" }, { job_type: "raw_material_video_analysis" }, { job_type: "custom_future_job" }],
    taskFilters: {
      job_type: "custom_future_job",
      status: "running",
      q: "abc",
      created_from: "2026-05-01",
      created_to: "2026-05-02"
    }
  };

  const query = methods.taskQueryString.call(context);

  expect(query).toContain("job_type=custom_future_job");
  expect(query).toContain("status=running");
  expect(query).toContain("q=abc");
  expect(query).toContain("created_from=");
  expect(query).toContain("created_to=");
  expect(methods.jobTypeLabel("compile")).toBe("Skill 编译");
  expect(methods.jobTypeLabel("raw_material_analysis")).toBe("Skill 素材解析");
  expect(methods.jobTypeLabel("raw_material_video_analysis")).toBe("Skill 素材解析");
  expect(methods.jobTypeLabel("skill_raw_material_generation")).toBe("Skill 智能体构建");
  expect(methods.jobTypeLabel("custom_future_job")).toBe("custom_future_job");
  expect(methods.normalizeTaskJobType("raw_material_video_analysis")).toBe("raw_material_analysis");
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "skill_raw_material_generation",
    label: "Skill 智能体构建"
  });
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "raw_material_analysis",
    label: "Skill 素材解析"
  });
  expect(methods.taskTypeOptions.call(context)).not.toContainEqual({
    value: "raw_material_video_analysis",
    label: "Skill 素材解析"
  });
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "custom_future_job",
    label: "custom_future_job"
  });
});

test("task query normalizes legacy raw material analysis job type", () => {
  const { methods } = loadTaskMethods();
  const context = {
    ...methods,
    taskFilters: {
      job_type: "raw_material_video_analysis",
      status: "",
      q: "",
      created_from: "",
      created_to: ""
    }
  };

  const query = methods.taskQueryString.call(context);

  expect(query).toContain("job_type=raw_material_analysis");
  expect(query).not.toContain("raw_material_video_analysis");
});

test("task polling starts and stops with the route", () => {
  const { methods, window } = loadTaskMethods();
  const context = {
    ...methods,
    route: { name: "tasks-list" },
    taskPollTimer: null,
    loadTasks: jest.fn()
  };

  methods.startTaskPolling.call(context);
  expect(context.taskPollTimer).toBe(123);
  expect(window.setInterval).toHaveBeenCalledWith(expect.any(Function), 5000);

  methods.stopTaskPolling.call(context);
  expect(window.clearInterval).toHaveBeenCalledWith(123);
  expect(context.taskPollTimer).toBeNull();
});
