const fs = require("fs");
const path = require("path");
const vm = require("vm");

function createFakeWebSocketClass() {
  const instances = [];

  class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(url) {
      this.url = url;
      this.readyState = FakeWebSocket.CONNECTING;
      this.listeners = {};
      instances.push(this);
    }

    addEventListener(type, handler) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(handler);
    }

    close() {
      this.readyState = FakeWebSocket.CLOSED;
      this.emit("close", {});
    }

    open() {
      this.readyState = FakeWebSocket.OPEN;
      this.emit("open", {});
    }

    message(payload) {
      this.emit("message", { data: JSON.stringify(payload) });
    }

    emit(type, event) {
      for (const handler of this.listeners[type] || []) {
        handler(event);
      }
    }
  }

  FakeWebSocket.instances = instances;
  return FakeWebSocket;
}

function loadPlatformHarness(locationSearch = "") {
  const FakeWebSocket = createFakeWebSocketClass();
  const code = fs.readFileSync(path.join(__dirname, "../../app/platform.js"), "utf8");
  const sandbox = {
    window: {
      location: { search: locationSearch },
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
        buildPlatformAgentPath: (agentKey) => `/admin/platform/agents/${agentKey}`,
        buildPlatformAgentRunsPath: (filters = {}) => {
          const params = new URLSearchParams();
          for (const key of ["agent_key", "status", "owner_type", "owner_id"]) {
            if (filters[key]) {
              params.set(key, filters[key]);
            }
          }
          const query = params.toString();
          return query ? `/admin/platform/agent-runs?${query}` : "/admin/platform/agent-runs";
        },
        buildPlatformAgentRunPath: (agentRunId, focus = {}) => {
          const params = new URLSearchParams();
          for (const key of ["tab", "tool_call_id", "authorization_id", "event_id"]) {
            if (focus[key]) {
              params.set(key, focus[key]);
            }
          }
          const query = params.toString();
          return query ? `/admin/platform/agent-runs/${agentRunId}?${query}` : `/admin/platform/agent-runs/${agentRunId}`;
        },
        buildPlatformSkillsPath: () => "/admin/platform/skills",
        buildPlatformSkillPath: (packageName) => `/admin/platform/skills/${packageName}`,
        buildPlatformToolsPath: () => "/admin/platform/tools",
        buildPlatformToolPath: (toolName) => `/admin/platform/tools/${toolName}`,
        buildPlatformMemoryPath: () => "/admin/platform/memory",
        buildPlatformMemoryEntryPath: (memoryId) => `/admin/platform/memory/${memoryId}`,
        buildToolAuthorizationsPath: (filters = {}) => {
          const params = new URLSearchParams();
          if (filters.status) {
            params.set("status", filters.status);
          }
          if (filters.tool_name) {
            params.set("tool_name", filters.tool_name);
          }
          const query = params.toString();
          return query ? `/admin/platform/tool-authorizations?${query}` : "/admin/platform/tool-authorizations";
        },
        buildGovernanceProposalPath: (proposalId) => `/admin/governance/proposals/${proposalId}`,
        buildRunLivePath: (runId) => `/admin/runs/${runId}/live`,
        buildReplayPath: (runId, focus = {}) => {
          const params = new URLSearchParams();
          for (const key of ["event_id", "seq_no", "snapshot_seq"]) {
            if (focus[key]) {
              params.set(key, focus[key]);
            }
          }
          const query = params.toString();
          return query ? `/admin/runs/${runId}/live/replay?${query}` : `/admin/runs/${runId}/live/replay`;
        },
        resolveWsUrl: (_apiBaseUrl, pathname) => `ws://localhost${pathname}`
      }
    },
    WebSocket: FakeWebSocket,
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
  return { methods: sandbox.window.PSOPConsolePlatformMethods, FakeWebSocket };
}

function loadPlatformMethods(locationSearch = "") {
  return loadPlatformHarness(locationSearch).methods;
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
  expect(methods.platformAgentDefinitionPath("pskill.builder")).toBe("/admin/platform/agents/pskill.builder");
  expect(methods.platformAgentRunsPath({ status: "waiting_tool_authorization" })).toBe(
    "/admin/platform/agent-runs?status=waiting_tool_authorization"
  );
  expect(methods.agentRunToolCallPath("agent-run-1", "tool-call-1")).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=tools&tool_call_id=tool-call-1"
  );
  expect(methods.agentRunAuthorizationPath("agent-run-1", "auth-1")).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=authorizations&authorization_id=auth-1"
  );
  expect(methods.platformTasksPath()).toBe("/admin/tasks");
  expect(methods.platformTaskJobPath({ id: "job-memory-1", job_type: "memory_compaction" })).toBe(
    "/admin/tasks?job_type=memory_compaction&q=job-memory-1"
  );
  expect(methods.platformSkillPath("pskill-builder")).toBe("/admin/platform/skills/pskill-builder");
  expect(methods.platformRunLivePath("runtime-run-1")).toBe("/admin/runs/runtime-run-1/live");
  expect(methods.platformToolPath("psop.memory.search")).toBe("/admin/platform/tools/psop.memory.search");
  expect(methods.platformMemoryEntryPath("mem-1")).toBe("/admin/platform/memory/mem-1");
});

test("platform methods sync agent run filters from location", async () => {
  const methods = loadPlatformMethods("?agent_key=pskill.runner&status=waiting_tool_authorization");
  const run = {
    id: "agent-run-waiting",
    agent_key: "pskill.runner",
    status: "waiting_tool_authorization",
    owner_type: "runtime",
    owner_id: "run-1"
  };
  const context = {
    ...methods,
    apiBaseUrl: "/api/v1",
    busy: { agentRuns: false, agentRunDetail: false },
    agentRunFilters: { agent_key: "", status: "", owner_type: "", owner_id: "" },
    agentRuns: [],
    currentAgentRun: null,
    currentAgentRunEvents: [],
    currentAgentRunModelCalls: [],
    currentAgentRunToolCalls: [],
    currentAgentRunSkillActivations: [],
    currentAgentRunToolAuthorizations: [],
    currentAgentRunMemoryEntries: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/agent-runs?agent_key=pskill.runner&status=waiting_tool_authorization") {
        return [run];
      }
      if (url === "/agent-runs/agent-run-waiting") {
        return run;
      }
      return [];
    }),
    connectAgentRunActivityWebSocket: jest.fn(),
    showNotice: jest.fn()
  };

  await methods.loadPlatformAgentRunsPage.call(context);

  expect(context.agentRunFilters).toEqual({
    agent_key: "pskill.runner",
    status: "waiting_tool_authorization",
    owner_type: "",
    owner_id: ""
  });
  expect(context.apiRequest).toHaveBeenCalledWith(
    "/agent-runs?agent_key=pskill.runner&status=waiting_tool_authorization"
  );
});

test("platform methods sync agent run detail focus from location", () => {
  const methods = loadPlatformMethods("?tool_call_id=tool-call-1");
  const context = { ...methods, agentRunDetailTab: "events" };

  methods.syncAgentRunDetailFromLocation.call(context);

  expect(context.agentRunDetailTab).toBe("tools");
  expect(methods.agentRunFocusedToolCallId.call(context)).toBe("tool-call-1");
  expect(methods.isAgentRunToolCallFocused.call(context, { id: "tool-call-1" })).toBe(true);
  expect(methods.isAgentRunToolCallFocused.call(context, { id: "tool-call-2" })).toBe(false);

  const authorizationMethods = loadPlatformMethods("?authorization_id=auth-1");
  const authorizationContext = { ...authorizationMethods, agentRunDetailTab: "events" };
  authorizationMethods.syncAgentRunDetailFromLocation.call(authorizationContext);

  expect(authorizationContext.agentRunDetailTab).toBe("authorizations");
  expect(authorizationMethods.agentRunFocusedAuthorizationId.call(authorizationContext)).toBe("auth-1");
  expect(authorizationMethods.isAgentRunAuthorizationFocused.call(authorizationContext, { id: "auth-1" })).toBe(true);
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
    apiBaseUrl: "/api/v1",
    busy: { agentRuns: false, agentRunDetail: false },
    agentRunFilters: { agent_key: "pskill.runner", status: "", owner_type: "", owner_id: "" },
    agentRuns: [],
    currentAgentRun: null,
    currentAgentRunEvents: [],
    currentAgentRunModelCalls: [],
    currentAgentRunToolCalls: [],
    currentAgentRunSkillActivations: [],
    currentAgentRunToolAuthorizations: [],
    currentAgentRunMemoryEntries: [],
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
      if (url.endsWith("/memory-entries")) {
        return [{ id: "memory-1", memory_type: "episodic", status: "pending_review" }];
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
  expect(context.currentAgentRunMemoryEntries).toHaveLength(1);
  expect(methods.agentRunDurationLabel.call(context, run)).toBe("3000 ms");
  expect(methods.agentRunToolFailureCount.call(context)).toBe(1);
  expect(methods.agentRunModelTokenUsage(context.currentAgentRunModelCalls[0])).toBe(42);
});

test("platform methods stream agent run activity snapshots", async () => {
  const { methods, FakeWebSocket } = loadPlatformHarness();
  const run = {
    id: "agent-run-activity",
    agent_key: "pskill.runner",
    status: "queued",
    owner_type: "runtime",
    owner_id: "run-activity",
    run_id: "runtime-run-activity",
    input_payload: {},
    output_payload: {},
    error_message: "",
    started_at: null,
    ended_at: null,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z"
  };
  const context = {
    ...methods,
    apiBaseUrl: "/api/v1",
    busy: { agentRunDetail: false },
    agentRuns: [],
    currentAgentRun: null,
    currentAgentRunEvents: [],
    currentAgentRunModelCalls: [],
    currentAgentRunToolCalls: [],
    currentAgentRunSkillActivations: [],
    currentAgentRunToolAuthorizations: [],
    currentAgentRunMemoryEntries: [],
    agentRunActivityWs: null,
    agentRunActivityWsAgentRunId: "",
    agentRunActivityWsStatus: "idle",
    apiRequest: jest.fn(async (url) => {
      if (url === "/agent-runs/agent-run-activity") {
        return run;
      }
      if (url.endsWith("/events")) {
        return [{ id: "event-1", event_type: "agent.run.created" }];
      }
      if (url.endsWith("/model-calls")) {
        return [];
      }
      if (url.endsWith("/tool-calls")) {
        return [];
      }
      if (url.endsWith("/skill-activations")) {
        return [];
      }
      if (url.endsWith("/tool-authorizations")) {
        return [];
      }
      if (url.endsWith("/memory-entries")) {
        return [];
      }
      return null;
    }),
    showNotice: jest.fn()
  };

  await methods.loadPlatformAgentRunDetail.call(context, "agent-run-activity");
  const socket = FakeWebSocket.instances[0];
  socket.open();
  socket.message({
    event_type: "agent_run.activity.snapshot",
    payload: {
      agent_run: { ...run, status: "waiting_tool_authorization" },
      events: [
        { id: "event-1", event_type: "agent.run.created" },
        { id: "event-2", event_type: "tool.authorization_requested" }
      ],
      model_calls: [{ id: "model-1", usage_json: { total_tokens: 16 } }],
      tool_calls: [{ id: "tool-1", status: "waiting_authorization" }],
      skill_activations: [{ id: "activation-1" }],
      tool_authorizations: [{ id: "auth-1", status: "pending" }],
      memory_entries: [{ id: "memory-1", memory_type: "episodic" }]
    }
  });

  expect(socket.url).toBe("ws://localhost/ws/agent-runs/agent-run-activity");
  expect(context.agentRunActivityWsStatus).toBe("open");
  expect(context.currentAgentRun.status).toBe("waiting_tool_authorization");
  expect(context.currentAgentRunEvents).toHaveLength(2);
  expect(context.currentAgentRunModelCalls).toHaveLength(1);
  expect(context.currentAgentRunToolCalls).toHaveLength(1);
  expect(context.currentAgentRunSkillActivations).toHaveLength(1);
  expect(context.currentAgentRunToolAuthorizations).toHaveLength(1);
  expect(context.currentAgentRunMemoryEntries).toHaveLength(1);

  methods.disconnectAgentRunActivityWebSocket.call(context);

  expect(context.agentRunActivityWs).toBeNull();
  expect(context.agentRunActivityWsStatus).toBe("idle");
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
    skillPackageSyncJob: null,
    platformAgentDefinitions: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/skills?scope=psop") {
        return [summary];
      }
      if (url === "/skills/sync") {
        return { changed: false, scanned_count: 1, package_count: 1, version_count: 1 };
      }
      if (url === "/skills/sync/queue") {
        return { id: "job-skill-sync-1", job_type: "skill_sync", status: "pending" };
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
      "allowed-tools": ["psop.pskills.read", "psop.materials.read_analysis"]
    })),
    showNotice: jest.fn()
  };

  await methods.syncSkillPackages.call(context);
  await methods.queueSkillPackageSync.call(context);
  await methods.createSkillPackageVersion.call(context);
  await methods.validateSkillPackageVersion.call(context, detail.versions[0]);
  await methods.activateSkillPackageVersion.call(context, detail.versions[0]);

  expect(context.apiRequest).toHaveBeenCalledWith("/skills/sync", { method: "POST" });
  expect(context.apiRequest).toHaveBeenCalledWith("/skills/sync/queue", {
    method: "POST",
    body: expect.stringContaining("ui-skill-sync-")
  });
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
  expect(createBody.manifest_json["allowed-tools"]).toEqual(["psop.pskills.read", "psop.materials.read_analysis"]);
  expect(createBody.resource_index[0].path).toBe("SKILL.md");
  expect(createBody.allowed_tools).toEqual(["psop.pskills.read", "psop.materials.read_analysis"]);
  expect(context.skillPackageSyncResult.package_count).toBe(1);
  expect(context.skillPackageSyncJob.id).toBe("job-skill-sync-1");
  expect(context.currentSkillPackage.name).toBe("pskill-builder");
  expect(context.currentSkillPackage.versions.find((version) => version.id === "ver-1").validation_diagnostics).toHaveLength(1);
  expect(methods.skillPackageUsedByAgents.call(context, context.currentSkillPackage).map((agent) => agent.key)).toEqual(["pskill.builder"]);
  expect(methods.skillPackageAgentRunsPath("pskill.builder")).toBe(
    "/admin/platform/agent-runs?agent_key=pskill.builder"
  );
});

test("platform skills page exposes used-by agent navigation", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/platform-skills.html"), "utf8");

  expect(html).toContain("platformAgentDefinitionPath(agent.key)");
  expect(html).toContain("skillPackageAgentRunsPath(agent.key)");
});

test("platform methods load tools and select the first row", async () => {
  const methods = loadPlatformMethods();
  const tools = [
    {
      name: "psop.memory.search",
      side_effect_level: "read",
      requires_authorization: false,
      failure_rate: 0,
      policy_summary: {
        policy_reason: "auto_allowed",
        policy_decision: { reason: "auto_allowed" },
        permission_rule: "AgentSpec.allowed_tools ∩ SkillPackage.allowed_tools ∩ ToolPolicy.allowed_tools"
      }
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
  const toolCalls = [
    {
      id: "tool-call-1",
      agent_run_id: "agent-run-1",
      tool_name: "psop.memory.search",
      status: "succeeded",
      updated_at: "2026-06-05T00:00:00Z"
    }
  ];
  const context = {
    ...methods,
    busy: { platformTools: false, platformToolAction: false },
    platformToolFilters: { side_effect_level: "read", requires_authorization: "false" },
    platformTools: [],
    currentPlatformTool: null,
    currentPlatformToolCalls: [],
    platformToolTestResult: null,
    apiRequest: jest.fn(async (url) => {
      if (url === "/tools/psop.memory.search/calls?limit=10") {
        return toolCalls;
      }
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
  expect(context.apiRequest).toHaveBeenCalledWith("/tools/psop.memory.search/calls?limit=10");
  expect(context.apiRequest).toHaveBeenCalledWith("/tools/psop.memory.search/test", {
    method: "POST",
    body: JSON.stringify({
      arguments_summary: { query: "runtime findings", limit: 3 },
      requested_side_effect_level: "read"
    })
  });
  expect(context.platformTools).toEqual(tools);
  expect(context.currentPlatformTool.name).toBe("psop.memory.search");
  expect(context.currentPlatformToolCalls).toEqual(toolCalls);
  expect(methods.platformToolCallPath(toolCalls[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=tools&tool_call_id=tool-call-1"
  );
  expect(methods.platformToolAuthorizationsPath.call(context, context.currentPlatformTool.name)).toBe(
    "/admin/platform/tool-authorizations?tool_name=psop.memory.search"
  );
  expect(methods.platformToolAuthorizationsPath.call(context)).toBe(
    "/admin/platform/tool-authorizations?status=pending"
  );
  expect(context.currentPlatformTool.policy_summary.policy_reason).toBe("auto_allowed");
  expect(context.platformToolTestResult.policy_reason).toBe("console_test_allowed");
  expect(context.busy.platformTools).toBe(false);
  expect(context.busy.platformToolAction).toBe(false);
});

test("platform tools page exposes default ToolPolicy reason", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/platform-tools.html"), "utf8");

  expect(html).toContain("Policy Reason");
  expect(html).toContain("currentPlatformTool.policy_summary?.policy_reason");
  expect(html).toContain("currentPlatformTool.policy_summary?.policy_decision?.reason");
  expect(html).toContain("currentPlatformToolCalls.length");
  expect(html).toContain("platformToolCallPath(call)");
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
    source_refs: [
      { kind: "run", id: "runtime-run-1" },
      { kind: "governance_proposal", proposal_id: "proposal-1" },
      { kind: "agent_memory_entry", id: "memory-source-1" },
      { kind: "run_trace", id: "trace-1", run_id: "runtime-run-1", seq_no: 7 },
      { kind: "run_event", id: "run-event-1", run_id: "runtime-run-1" },
      { kind: "tool_call", id: "tool-call-1", agent_run_id: "agent-run-1" },
      {
        kind: "tool_authorization",
        id: "auth-1",
        agent_run_id: "agent-run-1",
        tool_name: "psop.repository.commit_patch",
        status: "pending"
      }
    ],
    created_by_agent_run_id: "agent-run-1",
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
    busy: { memoryEntries: false, memoryUpdate: false, memoryCompaction: false },
    memoryFilters: {
      namespace: "evaluation",
      memory_type: "episodic",
      status: "active",
      agent_key: "",
      q: "finding"
    },
    memoryEntries: [],
    currentMemoryEntry: null,
    memoryCompactionJob: null,
    memoryEditForm: {},
    apiRequest: jest.fn(async (url) => {
      if (url === "/memory/search") {
        return [entry];
      }
      if (url === "/memory/compactions/queue") {
        return { id: "job-memory-compaction-1", job_type: "memory_compaction", status: "pending" };
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
  await methods.queueMemoryCompaction.call(context);

  expect(context.apiRequest).toHaveBeenNthCalledWith(1, "/memory/search", {
    method: "POST",
    body: JSON.stringify({
      query: "finding",
      namespace: "evaluation",
      memory_type: "episodic",
      status: "active",
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
  expect(context.apiRequest).toHaveBeenNthCalledWith(3, "/memory/compactions/queue", {
    method: "POST",
    body: expect.stringContaining("ui-memory-compaction-")
  });
  const compactionBody = JSON.parse(context.apiRequest.mock.calls[2][1].body);
  expect(compactionBody).toMatchObject({
    namespace: "evaluation",
    memory_type: "episodic",
    status: "active",
    agent_key: null,
    target_namespace: "evaluation",
    target_memory_type: "artifact",
    target_status: "pending_review",
    title: "Compacted platform memory",
    archive_source_entries: true
  });
  expect(context.memoryEntries[0].status).toBe("active");
  expect(context.memoryCompactionJob.id).toBe("job-memory-compaction-1");
  expect(context.currentMemoryEntry.title).toBe("Updated pattern");
  expect(methods.memorySourceLinks.call(context, context.currentMemoryEntry)).toEqual([
    {
      key: "created-by-agent-run-1",
      label: "AgentRun agent-run-1",
      href: "/admin/platform/agent-runs/agent-run-1"
    },
    {
      key: "run-runtime-run-1",
      label: "Run runtime-run-1",
      href: "/admin/runs/runtime-run-1/live"
    },
    {
      key: "proposal-proposal-1",
      label: "Proposal proposal-1",
      href: "/admin/governance/proposals/proposal-1"
    },
    {
      key: "memory-memory-source-1",
      label: "Memory memory-source-1",
      href: "/admin/platform/memory/memory-source-1"
    },
    {
      key: "run-trace-trace-1",
      label: "RunTrace trace-1",
      href: "/admin/runs/runtime-run-1/live/replay?seq_no=7"
    },
    {
      key: "run-event-run-event-1",
      label: "RunEvent run-event-1",
      href: "/admin/runs/runtime-run-1/live/replay?event_id=run-event-1"
    },
    {
      key: "tool-call-tool-call-1",
      label: "ToolCall tool-call-1",
      href: "/admin/platform/agent-runs/agent-run-1?tab=tools&tool_call_id=tool-call-1"
    },
    {
      key: "tool-auth-auth-1",
      label: "ToolAuth auth-1",
      href: "/admin/platform/agent-runs/agent-run-1?tab=authorizations&authorization_id=auth-1"
    }
  ]);
  expect(context.busy.memoryUpdate).toBe(false);
});

test("platform memory deep link fetches detail when entry is outside current filters", async () => {
  const methods = loadPlatformMethods();
  const listedEntry = {
    id: "mem-listed",
    namespace: "evaluation",
    memory_type: "episodic",
    agent_key: "pskill.evaluator",
    status: "active",
    confidence: 80,
    title: "Listed memory",
    content: "Visible in current filters.",
    source_refs: [],
    tags: [],
    metadata: {}
  };
  const deepLinkedEntry = {
    id: "mem-deep",
    namespace: "runtime",
    memory_type: "artifact",
    agent_key: "pskill.runner",
    status: "archived",
    confidence: 72,
    title: "Deep linked memory",
    content: "Loaded by id outside current filters.",
    source_refs: [],
    tags: [],
    metadata: {}
  };
  const context = {
    ...methods,
    busy: { memoryEntries: false, memoryUpdate: false, memoryCompaction: false },
    memoryFilters: {
      namespace: "evaluation",
      memory_type: "episodic",
      status: "active",
      agent_key: "",
      q: ""
    },
    memoryEntries: [],
    currentMemoryEntry: null,
    memoryEditForm: {},
    apiRequest: jest.fn(async (url) => {
      if (url === "/memory?namespace=evaluation&memory_type=episodic&status=active&limit=100") {
        return [listedEntry];
      }
      if (url === "/memory/mem-deep") {
        return deepLinkedEntry;
      }
      throw new Error(`unexpected url ${url}`);
    }),
    showNotice: jest.fn()
  };

  await methods.loadPlatformMemoryPage.call(context, "mem-deep");

  expect(context.apiRequest).toHaveBeenNthCalledWith(
    1,
    "/memory?namespace=evaluation&memory_type=episodic&status=active&limit=100"
  );
  expect(context.apiRequest).toHaveBeenNthCalledWith(2, "/memory/mem-deep");
  expect(context.currentMemoryEntry).toEqual(deepLinkedEntry);
  expect(context.memoryEntries.map((item) => item.id)).toEqual(["mem-deep", "mem-listed"]);
  expect(context.showNotice).not.toHaveBeenCalled();
  expect(context.busy.memoryEntries).toBe(false);
});
