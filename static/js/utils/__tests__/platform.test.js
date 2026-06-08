const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadPlatformMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/platform.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        buildPlatformAgentRunsPath: () => "/admin/platform/agent-runs",
        buildPlatformAgentRunPath: (agentRunId) => `/admin/platform/agent-runs/${agentRunId}`,
        buildPlatformSkillsPath: () => "/admin/platform/skills",
        buildPlatformSkillPath: (packageName) => `/admin/platform/skills/${packageName}`,
        buildPlatformToolsPath: () => "/admin/platform/tools",
        buildPlatformToolPath: (toolName) => `/admin/platform/tools/${toolName}`,
        buildPlatformMemoryPath: () => "/admin/platform/memory",
        buildPlatformMemoryEntryPath: (memoryId) => `/admin/platform/memory/${memoryId}`,
        buildToolAuthorizationsPath: () => "/admin/platform/tool-authorizations",
        buildRunLivePath: (runId) => `/admin/runs/${runId}/live`
      }
    },
    URLSearchParams,
    Date,
    JSON,
    String,
    Array,
    Object,
    Number,
    Math
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsolePlatformMethods;
}

test("platform methods build filters, labels, and paths", () => {
  const methods = loadPlatformMethods();
  const context = {
    ...methods,
    platformToolFilters: {
      side_effect_level: "high_write",
      requires_authorization: "true"
    },
    agentRunFilters: {
      agent_key: "pskill.runner",
      status: "waiting_tool_authorization",
      owner_type: "runtime",
      owner_id: "run-1"
    },
    skillPackageFilters: {
      scope: "psop",
      status: "active"
    },
    memoryFilters: {
      namespace: "evaluation",
      memory_type: "episodic",
      status: "pending_review",
      agent_key: "psop.evaluator",
      q: "regression"
    },
    optionLabel: (options, value) => options.find((item) => item.value === value)?.label || value
  };

  expect(methods.platformToolQueryString.call(context)).toBe("side_effect_level=high_write&requires_authorization=true");
  expect(methods.agentRunQueryString.call(context)).toBe(
    "agent_key=pskill.runner&status=waiting_tool_authorization&owner_type=runtime&owner_id=run-1"
  );
  expect(methods.skillPackageQueryString.call(context)).toBe("scope=psop&status=active");
  expect(methods.memoryQueryString.call(context)).toBe(
    "namespace=evaluation&memory_type=episodic&status=pending_review&agent_key=psop.evaluator&q=regression&limit=100"
  );
  expect(methods.agentRunStatusLabel.call(context, "waiting_tool_authorization")).toBe("等待授权");
  expect(methods.skillPackageScopeLabel.call(context, "psop")).toBe("PSOP");
  expect(methods.platformToolSideEffectLabel.call(context, "low_write")).toBe("Low Write");
  expect(methods.memoryStatusLabel.call(context, "pending_review")).toBe("待审核");
  expect(methods.platformAgentRunPath("run-1")).toBe("/admin/platform/agent-runs/run-1");
  expect(methods.platformSkillPath("pskill-builder")).toBe("/admin/platform/skills/pskill-builder");
  expect(methods.platformRunLivePath("runtime-run-1")).toBe("/admin/runs/runtime-run-1/live");
  expect(methods.platformToolPath("psop.memory.search")).toBe("/admin/platform/tools/psop.memory.search");
  expect(methods.platformMemoryEntryPath("mem-1")).toBe("/admin/platform/memory/mem-1");
});

test("platform methods load agent runs with detail observability streams", async () => {
  const methods = loadPlatformMethods();
  const run = {
    id: "agent-run-1",
    agent_key: "pskill.runner",
    status: "succeeded",
    owner_type: "runtime",
    owner_id: "run-1",
    run_id: "runtime-run-1",
    input_payload: {},
    output_payload: {},
    started_at: "2026-01-01T00:00:00.000Z",
    ended_at: "2026-01-01T00:00:03.000Z",
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:03.000Z"
  };
  const context = {
    ...methods,
    busy: { agentRuns: false, agentRunDetail: false },
    agentRunFilters: { agent_key: "pskill.runner", status: "", owner_type: "", owner_id: "" },
    agentRuns: [],
    currentAgentRun: null,
    currentAgentRunEvents: [],
    currentAgentRunModelCalls: [],
    currentAgentRunToolCalls: [],
    currentAgentRunSkillActivations: [],
    currentAgentRunToolAuthorizations: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/agent-runs?agent_key=pskill.runner") {
        return [run];
      }
      if (url === "/agent-runs/agent-run-1") {
        return run;
      }
      if (url.endsWith("/events")) {
        return [{ id: "event-1", event_type: "agent.run.created" }];
      }
      if (url.endsWith("/model-calls")) {
        return [{ id: "model-1", usage_json: { total_tokens: 42 } }];
      }
      if (url.endsWith("/tool-calls")) {
        return [{ id: "tool-1", status: "failed" }];
      }
      if (url.endsWith("/skill-activations")) {
        return [{ id: "activation-1" }];
      }
      if (url.endsWith("/tool-authorizations")) {
        return [{ id: "auth-1" }];
      }
      return null;
    }),
    showNotice: jest.fn(),
    formatDuration: (value) => `${value} ms`
  };

  await methods.loadPlatformAgentRunsPage.call(context);

  expect(context.apiRequest).toHaveBeenNthCalledWith(1, "/agent-runs?agent_key=pskill.runner");
  expect(context.apiRequest).toHaveBeenNthCalledWith(2, "/agent-runs/agent-run-1");
  expect(context.currentAgentRunEvents).toHaveLength(1);
  expect(context.currentAgentRunModelCalls).toHaveLength(1);
  expect(context.currentAgentRunToolCalls).toHaveLength(1);
  expect(context.currentAgentRunSkillActivations).toHaveLength(1);
  expect(context.currentAgentRunToolAuthorizations).toHaveLength(1);
  expect(methods.agentRunDurationLabel.call(context, run)).toBe("3000 ms");
  expect(methods.agentRunToolFailureCount.call(context)).toBe(1);
  expect(methods.agentRunModelTokenUsage(context.currentAgentRunModelCalls[0])).toBe(42);
});

test("platform methods sync, load, create, validate, and activate skill packages", async () => {
  const methods = loadPlatformMethods();
  const summary = {
    id: "pkg-1",
    name: "pskill-builder",
    scope: "psop",
    status: "active",
    version_count: 1,
    active_version_id: "ver-1",
    active_version_label: "sync-123"
  };
  const detail = {
    ...summary,
    versions: [
      {
        id: "ver-1",
        version_label: "sync-123",
        validation_status: "valid",
        validation_diagnostics: [],
        allowed_tools: ["psop.pskills.read"],
        manifest_json: { name: "pskill-builder", description: "Build" },
        resource_count: 1
      }
    ],
    active_version: {
      id: "ver-1",
      allowed_tools: ["psop.pskills.read"],
      manifest_json: { name: "pskill-builder", description: "Build" },
      resource_index: [{ path: "SKILL.md", kind: "skill" }]
    },
    resources: [{ id: "res-1", resource_path: "SKILL.md", resource_kind: "skill", size_bytes: 100 }]
  };
  const validated = {
    ...detail.versions[0],
    validation_diagnostics: [{ severity: "warning", code: "missing_references", message: "missing refs" }]
  };
  const candidate = {
    ...detail.versions[0],
    id: "ver-2",
    version_label: "builder-candidate",
    status: "candidate",
    validation_status: "valid",
    validation_diagnostics: []
  };
  const createdDetail = {
    ...detail,
    version_count: 2,
    versions: [candidate, ...detail.versions]
  };
  const context = {
    ...methods,
    busy: {
      skillPackages: false,
      skillPackageDetail: false,
      skillPackageAction: false,
      platformAgentDefinitions: false
    },
    skillPackageFilters: { scope: "psop", status: "" },
    skillPackages: [],
    currentSkillPackage: null,
    skillPackageSyncResult: null,
    platformAgentDefinitions: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/skills?scope=psop") {
        return [summary];
      }
      if (url === "/skills/sync") {
        return { changed: false, scanned_count: 1, package_count: 1, version_count: 1 };
      }
      if (url === "/agents") {
        return [{ key: "pskill.builder" }];
      }
      if (url === "/agents/pskill.builder") {
        return {
          key: "pskill.builder",
          active_version: { spec_json: { allowed_skill_names: ["pskill-builder"] } }
        };
      }
      if (url === "/skills/pskill-builder") {
        return detail;
      }
      if (url === "/skills/pskill-builder/versions") {
        return createdDetail;
      }
      if (url === "/skills/pskill-builder/versions/ver-1/validate") {
        return validated;
      }
      if (url === "/skills/pskill-builder/versions/ver-1/activate") {
        return { ...createdDetail, active_version_id: "ver-1" };
      }
      return null;
    }),
    promptSkillPackageVersionLabel: jest.fn(() => "builder-candidate"),
    promptSkillPackageVersionManifest: jest.fn(() => JSON.stringify({
      name: "pskill-builder",
      description: "Candidate builder package",
      "allowed-tools": ["psop.pskills.read", "psop.materials.read"]
    })),
    showNotice: jest.fn()
  };

  await methods.syncSkillPackages.call(context);
  await methods.createSkillPackageVersion.call(context);
  await methods.validateSkillPackageVersion.call(context, detail.versions[0]);
  await methods.activateSkillPackageVersion.call(context, detail.versions[0]);

  expect(context.apiRequest).toHaveBeenCalledWith("/skills/sync", { method: "POST" });
  expect(context.apiRequest).toHaveBeenCalledWith("/skills?scope=psop");
  expect(context.apiRequest).toHaveBeenCalledWith("/skills/pskill-builder");
  expect(context.apiRequest).toHaveBeenCalledWith(
    "/skills/pskill-builder/versions",
    expect.objectContaining({ method: "POST" })
  );
  expect(context.apiRequest).toHaveBeenCalledWith("/skills/pskill-builder/versions/ver-1/validate", { method: "POST" });
  expect(context.apiRequest).toHaveBeenCalledWith("/skills/pskill-builder/versions/ver-1/activate", { method: "POST" });
  const createCall = context.apiRequest.mock.calls.find(([url]) => url === "/skills/pskill-builder/versions");
  const createBody = JSON.parse(createCall[1].body);
  expect(createBody.version_label).toBe("builder-candidate");
  expect(createBody.manifest_json["allowed-tools"]).toEqual(["psop.pskills.read", "psop.materials.read"]);
  expect(createBody.resource_index[0].path).toBe("SKILL.md");
  expect(createBody.allowed_tools).toEqual(["psop.pskills.read", "psop.materials.read"]);
  expect(context.skillPackageSyncResult.package_count).toBe(1);
  expect(context.currentSkillPackage.name).toBe("pskill-builder");
  expect(context.currentSkillPackage.versions.find((version) => version.id === "ver-1").validation_diagnostics).toHaveLength(1);
  expect(methods.skillPackageUsedByAgents.call(context, context.currentSkillPackage).map((agent) => agent.key)).toEqual(["pskill.builder"]);
});

test("platform methods load tools and select the first row", async () => {
  const methods = loadPlatformMethods();
  const tools = [
    {
      name: "psop.memory.search",
      side_effect_level: "read",
      requires_authorization: false,
      failure_rate: 0
    }
  ];
  const dryRun = {
    tool_name: "psop.memory.search",
    executable: true,
    dry_run: true,
    side_effect_level: "read",
    requires_authorization: false,
    policy_reason: "console_test_allowed",
    input_echo: { query: "runtime findings", limit: 3 },
    output_preview: { status: "dry_run_succeeded" },
    policy_decision: { allowed: true, reason: "auto_allowed" }
  };
  const context = {
    ...methods,
    busy: { platformTools: false, platformToolAction: false },
    platformToolFilters: { side_effect_level: "read", requires_authorization: "false" },
    platformTools: [],
    currentPlatformTool: null,
    platformToolTestResult: null,
    apiRequest: jest.fn(async (url) => {
      if (url === "/tools/psop.memory.search/test") {
        return dryRun;
      }
      return tools;
    }),
    showNotice: jest.fn()
  };

  await methods.loadPlatformToolsPage.call(context);
  await methods.testPlatformTool.call(context, context.currentPlatformTool);

  expect(context.apiRequest).toHaveBeenCalledWith("/tools?side_effect_level=read&requires_authorization=false");
  expect(context.apiRequest).toHaveBeenCalledWith("/tools/psop.memory.search/test", {
    method: "POST",
    body: JSON.stringify({
      arguments_summary: { query: "runtime findings", limit: 3 },
      requested_side_effect_level: "read"
    })
  });
  expect(context.platformTools).toEqual(tools);
  expect(context.currentPlatformTool.name).toBe("psop.memory.search");
  expect(methods.platformToolAuthorizationsPath.call(context, context.currentPlatformTool.name)).toBe(
    "/admin/platform/tool-authorizations?tool_name=psop.memory.search"
  );
  expect(context.platformToolTestResult.policy_reason).toBe("console_test_allowed");
  expect(context.busy.platformTools).toBe(false);
  expect(context.busy.platformToolAction).toBe(false);
});

test("platform methods search and save memory entries", async () => {
  const methods = loadPlatformMethods();
  const entry = {
    id: "mem-1",
    namespace: "evaluation",
    memory_type: "episodic",
    agent_key: "psop.evaluator",
    status: "pending_review",
    confidence: 62,
    title: "Finding pattern",
    content: "A regression finding pattern.",
    source_refs: [],
    tags: ["finding"],
    metadata: {}
  };
  const updated = {
    ...entry,
    status: "active",
    confidence: 88,
    title: "Updated pattern",
    content: "Updated content.",
    tags: ["finding", "runtime"]
  };
  const context = {
    ...methods,
    busy: { memoryEntries: false, memoryUpdate: false },
    memoryFilters: {
      namespace: "evaluation",
      memory_type: "episodic",
      status: "pending_review",
      agent_key: "",
      q: "finding"
    },
    memoryEntries: [],
    currentMemoryEntry: null,
    memoryEditForm: {},
    apiRequest: jest.fn(async (url) => {
      if (url === "/memory/search") {
        return [entry];
      }
      return updated;
    }),
    showNotice: jest.fn()
  };

  await methods.searchMemoryEntries.call(context);
  context.memoryEditForm = {
    status: "active",
    title: "Updated pattern",
    content: "Updated content.",
    confidence: 88,
    tags: "finding, runtime"
  };
  await methods.saveMemoryEntry.call(context);

  expect(context.apiRequest).toHaveBeenNthCalledWith(1, "/memory/search", {
    method: "POST",
    body: JSON.stringify({
      query: "finding",
      namespace: "evaluation",
      memory_type: "episodic",
      status: "pending_review",
      agent_key: null,
      limit: 100
    })
  });
  expect(context.apiRequest).toHaveBeenNthCalledWith(2, "/memory/mem-1", {
    method: "PATCH",
    body: JSON.stringify({
      status: "active",
      title: "Updated pattern",
      content: "Updated content.",
      confidence: 88,
      tags: ["finding", "runtime"]
    })
  });
  expect(context.memoryEntries[0].status).toBe("active");
  expect(context.currentMemoryEntry.title).toBe("Updated pattern");
  expect(context.busy.memoryUpdate).toBe(false);
});
