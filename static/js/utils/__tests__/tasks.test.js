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
        generateSkillKey: () => "",
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
      PSOPConsoleHelpers: {
        buildTasksPath: (filters = {}) => {
          const params = new URLSearchParams();
          for (const key of ["job_type", "status", "q", "created_from", "created_to"]) {
            if (filters[key]) {
              params.set(key, filters[key]);
            }
          }
          const query = params.toString();
          return query ? `/admin/tasks?${query}` : "/admin/tasks";
        },
        buildEvaluationReportPath: (evaluationId) => `/admin/evaluations/${evaluationId}`,
        buildGovernanceProposalPath: (proposalId) => `/admin/governance/proposals/${proposalId}`,
        buildPlatformMemoryEntryPath: (memoryId) => `/admin/platform/memory/${memoryId}`,
        buildPlatformSkillsPath: () => "/admin/platform/skills",
        buildPlatformSkillPath: (packageName) => `/admin/platform/skills/${packageName}`,
        buildRunLivePath: (runId) => `/admin/runs/${runId}/live`,
        buildSkillDetailPath: (skillId) => `/admin/skills/${skillId}`,
        buildSkillTestScenarioRunReviewPath: (skillId, scenarioId, scenarioRunId) =>
          `/admin/skills/${skillId}/tests/${scenarioId}/runs/${scenarioRunId}/review`,
        buildCompilerArtifactPath: (artifactId) => `/admin/compiler/artifacts/${artifactId}`
      },
      location: {
        pathname: "/admin/tasks",
        search: ""
      },
      history: {
        replaceState: jest.fn((_state, _title, pathValue) => {
          const [pathname, search = ""] = String(pathValue).split("?");
          sandbox.window.location.pathname = pathname;
          sandbox.window.location.search = search ? `?${search}` : "";
        })
      },
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
  expect(methods.formatStatus("executed")).toBe("已执行");
  expect(methods.statusBadgeTone("executed")).toContain("emerald");
});

test("tasks methods build filters and preserve unknown job types", () => {
  const { methods } = loadTaskMethods();
  const context = {
    ...methods,
    tasks: [{ job_type: "compile" }, { job_type: "material_analysis" }, { job_type: "custom_future_job" }],
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
  expect(methods.jobTypeLabel("compile")).toBe("PSkill 编译");
  expect(methods.jobTypeLabel("runtime")).toBe("Runtime 推进");
  expect(methods.jobTypeLabel("skill_test_timeline_driver")).toBe("PSkill 测试");
  expect(methods.jobTypeLabel("material_analysis")).toBe("PSkill 素材解析");
  expect(methods.jobTypeLabel("pskill_build")).toBe("PSkill 智能体构建");
  expect(methods.jobTypeLabel("run_evaluation")).toBe("Run 评估");
  expect(methods.jobTypeLabel("governance_proposal")).toBe("治理提案生成");
  expect(methods.jobTypeLabel("memory_compaction")).toBe("记忆压缩");
  expect(methods.jobTypeLabel("skill_sync")).toBe("Skill 包同步");
  expect(methods.jobTypeLabel("custom_future_job")).toBe("custom_future_job");
  expect(methods.normalizeTaskJobType("compile")).toBe("pskill_compile");
  expect(methods.normalizeTaskJobType("runtime")).toBe("runtime_step");
  expect(methods.normalizeTaskJobType("skill_test_timeline_driver")).toBe("pskill_test");
  expect(methods.normalizeTaskJobType("material_analysis")).toBe("material_analysis");
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "pskill_compile",
    label: "PSkill 编译"
  });
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "pskill_build",
    label: "PSkill 智能体构建"
  });
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "material_analysis",
    label: "PSkill 素材解析"
  });
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "run_evaluation",
    label: "Run 评估"
  });
  expect(methods.taskStatusOptions()).toContainEqual({
    value: "dead_letter",
    label: "死信"
  });
  expect(methods.taskTypeOptions.call(context)).toContainEqual({
    value: "custom_future_job",
    label: "custom_future_job"
  });
});

test("tasks methods expose related entities for closed-loop runtime jobs", () => {
  const { methods } = loadTaskMethods();
  const context = {
    ...methods,
    formatShortId: (value) => String(value).slice(0, 8)
  };

  const evaluationTask = {
    job_type: "run_evaluation",
    run_id: "run-evaluation-1",
    payload: { evaluation_id: "evaluation-closed-loop-1" }
  };
  const proposalTask = {
    job_type: "governance_proposal",
    payload: { proposal_id: "proposal-closed-loop-1", source_evaluation_id: "evaluation-closed-loop-1" }
  };
  const compactionTask = {
    job_type: "memory_compaction",
    payload: { compacted_memory_id: "memory-closed-loop-1" }
  };
  const skillSyncTask = {
    job_type: "skill_sync",
    payload: { package_name: "pskill-runner-field-assistant" }
  };
  const scenarioTask = {
    job_type: "pskill_test",
    payload: {
      pskill_definition_id: "pskill-1",
      scenario_id: "scenario-1",
      scenario_run_id: "scenario-run-1"
    }
  };
  const pendingProposalTask = {
    job_type: "governance_proposal",
    run_id: "run-governance-1",
    payload: { finding_id: "finding-1", source_evaluation_id: "evaluation-closed-loop-1" }
  };
  const testRuntimeTask = {
    job_type: "pskill_test",
    run_id: "run-test-1",
    payload: { scenario_run_id: "scenario-run-2" }
  };

  expect(methods.taskRelatedLabel.call(context, evaluationTask)).toBe("Evaluation evaluati");
  expect(methods.taskRelatedHref.call(context, evaluationTask)).toBe("/admin/evaluations/evaluation-closed-loop-1");
  expect(methods.taskRelatedLabel.call(context, proposalTask)).toBe("Proposal proposal");
  expect(methods.taskRelatedHref.call(context, proposalTask)).toBe("/admin/governance/proposals/proposal-closed-loop-1");
  expect(methods.taskRelatedLabel.call(context, compactionTask)).toBe("Memory memory-c");
  expect(methods.taskRelatedHref.call(context, compactionTask)).toBe("/admin/platform/memory/memory-closed-loop-1");
  expect(methods.taskRelatedLabel.call(context, skillSyncTask)).toBe("Skill Package pskill-r");
  expect(methods.taskRelatedHref.call(context, skillSyncTask)).toBe("/admin/platform/skills/pskill-runner-field-assistant");
  expect(methods.taskRelatedLabel.call(context, scenarioTask)).toBe("Scenario Run scenario");
  expect(methods.taskRelatedHref.call(context, scenarioTask)).toBe(
    "/admin/skills/pskill-1/tests/scenario-1/runs/scenario-run-1/review"
  );
  expect(methods.taskRelatedLabel.call(context, pendingProposalTask)).toBe("Evaluation evaluati");
  expect(methods.taskRelatedHref.call(context, pendingProposalTask)).toBe("/admin/evaluations/evaluation-closed-loop-1");
  expect(methods.taskRelatedLabel.call(context, testRuntimeTask)).toBe("Run run-test");
  expect(methods.taskRelatedHref.call(context, testRuntimeTask)).toBe("/admin/runs/run-test-1/live");
});

test("tasks methods hydrate filters from location query and keep query in sync", () => {
  const { methods, window } = loadTaskMethods();
  const context = {
    ...methods,
    taskFilters: methods.emptyTaskFilters(),
    taskFiltersLocationSearch: "",
    loadTasks: jest.fn(),
    apiRequest: jest.fn()
  };
  window.location.search = "?job_type=memory_compaction&status=pending&q=job-memory-1";

  methods.syncTaskFiltersFromLocation.call(context);

  expect(context.taskFilters).toMatchObject({
    job_type: "memory_compaction",
    status: "pending",
    q: "job-memory-1"
  });
  expect(context.taskFiltersLocationSearch).toBe("?job_type=memory_compaction&status=pending&q=job-memory-1");

  context.taskFilters = {
    ...context.taskFilters,
    status: "running",
    q: "job-memory-2"
  };
  methods.replaceTaskFilterLocation.call(context);

  expect(window.history.replaceState).toHaveBeenCalledWith(
    {},
    "",
    "/admin/tasks?job_type=memory_compaction&status=running&q=job-memory-2"
  );
  expect(context.taskFiltersLocationSearch).toBe("?job_type=memory_compaction&status=running&q=job-memory-2");

  methods.resetTaskFilters.call(context);

  expect(window.location.search).toBe("");
  expect(context.taskFilters).toEqual(methods.emptyTaskFilters());
});

test("task query uses material analysis job type", () => {
  const { methods } = loadTaskMethods();
  const context = {
    ...methods,
    taskFilters: {
      job_type: "material_analysis",
      status: "",
      q: "",
      created_from: "",
      created_to: ""
    }
  };

  const query = methods.taskQueryString.call(context);

  expect(query).toContain("job_type=material_analysis");
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
