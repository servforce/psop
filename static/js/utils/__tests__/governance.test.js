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

function loadGovernanceHarness(locationSearch = "") {
  const FakeWebSocket = createFakeWebSocketClass();
  const code = fs.readFileSync(path.join(__dirname, "../../app/governance.js"), "utf8");
  const sandbox = {
    window: {
      location: { search: locationSearch },
      history: {
        replaceState: jest.fn((_state, _title, pathValue) => {
          const [, search = ""] = String(pathValue).split("?");
          sandbox.window.location.search = search ? `?${search}` : "";
        })
      },
      PSOPConsoleHelpers: {
        buildSkillDetailPath: (skillId) => `/admin/skills/${skillId}`,
        buildEvaluationReportPath: (evaluationId) => `/admin/evaluations/${evaluationId}`,
        buildGovernanceProposalsPath: () => "/admin/governance/proposals",
        buildGovernanceProposalPath: (proposalId) => `/admin/governance/proposals/${proposalId}`,
        buildGovernanceExperimentsPath: () => "/admin/governance/experiments",
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
        buildPlatformAgentPath: (agentKey) => `/admin/platform/agents/${agentKey}`,
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
        buildPlatformSkillPath: (packageName) => `/admin/platform/skills/${packageName}`,
        buildPlatformMemoryEntryPath: (memoryId) => `/admin/platform/memory/${memoryId}`,
        buildReplayPath: (runId, focus = {}) => {
          const params = new URLSearchParams();
          if (focus.event_id) {
            params.set("event_id", focus.event_id);
          }
          const query = params.toString();
          return query ? `/admin/runs/${runId}/live/replay?${query}` : `/admin/runs/${runId}/live/replay`;
        },
        resolveWsUrl: (_apiBaseUrl, pathname) => `ws://localhost${pathname}`
      }
    },
    WebSocket: FakeWebSocket,
    URLSearchParams,
    JSON,
    String,
    Array,
    Object,
    Number
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return { methods: sandbox.window.PSOPConsoleGovernanceMethods, FakeWebSocket };
}

function loadGovernanceMethods(locationSearch = "") {
  return loadGovernanceHarness(locationSearch).methods;
}

test("governance methods build filters and labels", () => {
  const methods = loadGovernanceMethods();
  const context = {
    ...methods,
    governanceProposalFilters: { status: "testing" },
    governanceExperimentFilters: { proposal_id: "proposal-1", status: "running", experiment_type: "canary" },
    toolAuthorizationFilters: { status: "pending", tool_name: "psop.repository.commit_patch" },
    optionLabel: (options, value) => options.find((item) => item.value === value)?.label || value
  };

  expect(methods.governanceProposalQueryString.call(context)).toBe("status=testing");
  expect(methods.governanceExperimentQueryString.call(context)).toBe(
    "proposal_id=proposal-1&status=running&experiment_type=canary"
  );
  expect(methods.toolAuthorizationQueryString.call(context)).toBe("status=pending&tool_name=psop.repository.commit_patch");
  expect(methods.governanceProposalTypeLabel.call(context, "tool_policy_update")).toBe("Tool Policy");
  expect(methods.governanceProposalStatusLabel.call(context, "rolled_back")).toBe("已回滚");
  expect(methods.governanceExperimentTypeLabel.call(context, "canary")).toBe("Canary");
  expect(methods.governanceExperimentStatusLabel.call(context, "rolled_back")).toBe("已回滚");
  expect(methods.toolAuthorizationStatusLabel.call(context, "approved")).toBe("已批准");
  expect(methods.toolAuthorizationStatusLabel.call(context, "expired")).toBe("已过期");
  expect(methods.toolAuthorizationStatusLabel.call(context, "cancelled")).toBe("已取消");
  expect(methods.toolAuthorizationStatusLabel.call(context, "executed")).toBe("已执行");
  expect(methods.governanceProposalPath("proposal-1")).toBe("/admin/governance/proposals/proposal-1");
  expect(methods.governanceEvaluationReportPath("evaluation-1")).toBe("/admin/evaluations/evaluation-1");
});

test("governance methods build tool authorization context links", () => {
  const methods = loadGovernanceMethods();
  const authorization = {
    agent_run_id: "agent-run-1",
    agent_tool_call_id: "tool-call-1",
    run_id: "runtime-run-1",
    run_event_id: "event-1",
    tool_arguments_summary: {
      package_name: "pskill.runner",
      nested: { evaluation_id: "eval-1" }
    },
    request_payload: {
      decision: {
        arguments_summary: {
          proposal_id: "proposal-1",
          agent_key: "psop.governance"
        }
      }
    },
    response_payload: {
      result: {
        memory_id: "memory-1"
      }
    }
  };
  const links = methods.toolAuthorizationContextLinks.call(methods, authorization);

  expect(links.map((item) => item.key)).toEqual([
    "agent-run-agent-run-1",
    "tool-call-tool-call-1",
    "run-replay-runtime-run-1",
    "run-event-event-1",
    "proposal-proposal-1",
    "evaluation-eval-1",
    "skill-package-pskill.runner",
    "agent-psop.governance",
    "memory-memory-1"
  ]);
  expect(links.find((item) => item.key === "tool-call-tool-call-1").href).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=tools&tool_call_id=tool-call-1"
  );
  expect(links.find((item) => item.key === "run-replay-runtime-run-1").href).toBe(
    "/admin/runs/runtime-run-1/live/replay"
  );
  expect(links.find((item) => item.key === "run-event-event-1").href).toBe(
    "/admin/runs/runtime-run-1/live/replay?event_id=event-1"
  );
  expect(links.find((item) => item.key === "proposal-proposal-1").href).toBe(
    "/admin/governance/proposals/proposal-1"
  );
});

test("governance methods sync tool authorization filters from location", () => {
  const methods = loadGovernanceMethods("?status=approved&tool_name=psop.agent_version.activate");
  const context = {
    ...methods,
    toolAuthorizationFilters: { status: "pending", tool_name: "" },
    toolAuthorizationLocationSearch: ""
  };

  methods.syncToolAuthorizationFiltersFromLocation.call(context);

  expect(context.toolAuthorizationFilters.status).toBe("approved");
  expect(context.toolAuthorizationFilters.tool_name).toBe("psop.agent_version.activate");
});

test("governance methods treat tool authorization tool-only location as history", () => {
  const methods = loadGovernanceMethods("?tool_name=psop.agent_version.activate");
  const context = {
    ...methods,
    toolAuthorizationFilters: { status: "pending", tool_name: "" },
    toolAuthorizationLocationSearch: ""
  };

  methods.syncToolAuthorizationFiltersFromLocation.call(context);

  expect(context.toolAuthorizationFilters.status).toBe("");
  expect(context.toolAuthorizationFilters.tool_name).toBe("psop.agent_version.activate");
});

test("governance methods keep tool authorization filters in location", async () => {
  const { methods, FakeWebSocket } = loadGovernanceHarness();
  const context = {
    ...methods,
    busy: { toolAuthorizations: false },
    toolAuthorizationFilters: { status: "pending", tool_name: "psop.repository.commit_patch" },
    toolAuthorizationLocationSearch: "",
    toolAuthorizations: [],
    apiRequest: jest.fn(async () => []),
    showNotice: jest.fn()
  };

  await methods.applyToolAuthorizationFilters.call(context);

  expect(FakeWebSocket.instances).toHaveLength(0);
  expect(context.apiRequest).toHaveBeenCalledWith(
    "/tool-authorizations?status=pending&tool_name=psop.repository.commit_patch"
  );
  expect(context.toolAuthorizationLocationSearch).toBe("?status=pending&tool_name=psop.repository.commit_patch");

  await methods.resetToolAuthorizationFilters.call(context);

  expect(context.toolAuthorizationFilters).toEqual({ status: "pending", tool_name: "" });
  expect(context.toolAuthorizationLocationSearch).toBe("?status=pending");
});

test("tool authorization page connects websocket and applies realtime updates", async () => {
  const { methods, FakeWebSocket } = loadGovernanceHarness("?status=pending");
  const initial = { id: "auth-1", status: "pending", tool_name: "psop.repository.commit_patch" };
  const next = { id: "auth-2", status: "pending", tool_name: "psop.agent_version.activate" };
  const ignored = { id: "auth-3", status: "approved", tool_name: "psop.agent_version.activate" };
  const context = {
    ...methods,
    apiBaseUrl: "/api/v1",
    busy: { toolAuthorizations: false },
    toolAuthorizationFilters: { status: "pending", tool_name: "" },
    toolAuthorizationLocationSearch: "",
    toolAuthorizations: [],
    toolAuthorizationWs: null,
    toolAuthorizationWsStatus: "idle",
    apiRequest: jest.fn(async () => [initial]),
    showNotice: jest.fn()
  };

  await methods.loadToolAuthorizationsPage.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/tool-authorizations?status=pending");
  expect(FakeWebSocket.instances).toHaveLength(1);
  expect(FakeWebSocket.instances[0].url).toBe("ws://localhost/ws/tool-authorizations");
  expect(context.toolAuthorizations).toEqual([initial]);
  expect(context.toolAuthorizationWsStatus).toBe("connecting");

  FakeWebSocket.instances[0].open();
  expect(context.toolAuthorizationWsStatus).toBe("open");

  FakeWebSocket.instances[0].message({
    event_type: "tool.authorization_requested",
    payload: next
  });
  FakeWebSocket.instances[0].message({
    event_type: "tool.authorization_approved",
    payload: ignored
  });
  FakeWebSocket.instances[0].message({
    event_type: "tool.authorization_approved",
    payload: { ...initial, status: "approved" }
  });
  FakeWebSocket.instances[0].message({
    event_type: "tool.authorization_executed",
    payload: { ...initial, status: "executed", executed_at: "2026-06-08T00:00:00Z" }
  });

  expect(context.toolAuthorizations.map((item) => item.id)).toEqual(["auth-2", "auth-1"]);
  expect(context.toolAuthorizations[1].status).toBe("executed");
  expect(context.toolAuthorizations[1].executed_at).toBe("2026-06-08T00:00:00Z");

  methods.disconnectToolAuthorizationWebSocket.call(context);

  expect(context.toolAuthorizationWs).toBeNull();
  expect(context.toolAuthorizationWsStatus).toBe("idle");
});

test("governance methods extract tool authorization patch diffs", () => {
  const methods = loadGovernanceMethods();
  const context = { ...methods };
  const patchAuthorization = {
    tool_arguments_summary: {
      patch: "--- a/SKILL.md\n+++ b/SKILL.md\n@@\n-old\n+new"
    },
    request_payload: {}
  };
  const changesAuthorization = {
    tool_arguments_summary: {
      changes: [
        { path: "SKILL.md", diff: "@@\n-old\n+new" },
        { path: "README.md", before: "old", after: "new" }
      ]
    },
    request_payload: {}
  };
  const beforeAfterAuthorization = {
    tool_arguments_summary: {
      before: { status: "draft" },
      after: { status: "published" }
    },
    request_payload: {}
  };

  expect(methods.toolAuthorizationHasDiff.call(context, patchAuthorization)).toBe(true);
  expect(methods.toolAuthorizationDiffText.call(context, patchAuthorization)).toContain("+++ b/SKILL.md");
  expect(methods.toolAuthorizationDiffText.call(context, changesAuthorization)).toContain("@@");
  expect(methods.toolAuthorizationDiffText.call(context, changesAuthorization)).toContain('"path": "README.md"');
  expect(methods.toolAuthorizationDiffText.call(context, beforeAfterAuthorization)).toContain("--- before");
  expect(methods.toolAuthorizationDiffText.call(context, { tool_arguments_summary: {}, request_payload: {} })).toBe("");
});

test("governance methods edit proposal payloads and source finding links", async () => {
  const methods = loadGovernanceMethods();
  const proposal = {
    id: "proposal-1",
    status: "draft",
    proposal_type: "test_suite_update",
    problem_statement: "add tests",
    target: { kind: "test_suite", patch: "--- a/test\n+++ b/test\n@@\n-old\n+new" },
    evidence_refs: [{ kind: "run_trace", id: "trace-1" }],
    proposed_changes: [{ kind: "patch", before: { enabled: false }, after: { enabled: true } }],
    risk_assessment: { risk_level: "medium" },
    required_tests: [{ kind: "regression" }],
    activation_plan: { strategy: "review" },
    source_finding_ids: ["finding-1"],
    source_findings: [
      {
        id: "finding-1",
        evaluation_id: "evaluation-1",
        run_id: "run-1",
        pskill_definition_id: "pskill-1",
        description: "runner issue",
        evidence_refs: [{ kind: "run_trace", id: "trace-1" }]
      }
    ],
    experiments: []
  };
  const updated = { ...proposal, problem_statement: "add better tests" };
  const context = {
    ...methods,
    busy: { governanceProposalSave: false },
    governanceProposals: [proposal],
    currentGovernanceProposal: proposal,
    governanceProposalEditOpen: false,
    governanceProposalEditForm: {},
    apiRequest: jest.fn(async () => updated),
    showNotice: jest.fn(),
    refreshGovernanceExperimentRows: jest.fn()
  };

  expect(methods.governanceCanEditProposal(proposal)).toBe(true);
  expect(methods.governanceCanEditProposal({ status: "approved" })).toBe(false);
  expect(methods.governanceProposalSourceFindings(proposal)[0].id).toBe("finding-1");
  expect(methods.governanceSourceFindingReplayPath(proposal.source_findings[0], proposal.source_findings[0].evidence_refs[0])).toBe(
    "/admin/runs/run-1/live/replay?event_id=trace-1"
  );
  expect(methods.governanceProposalHasPatchDiff.call(context, proposal)).toBe(true);
  expect(methods.governanceProposalPatchDiffText.call(context, proposal)).toContain("+++ b/test");
  expect(methods.governanceProposalChangeDiffText.call(context, proposal.proposed_changes[0])).toContain("--- before");

  methods.openGovernanceProposalEdit.call(context, proposal);
  context.governanceProposalEditForm.problem_statement = "add better tests";
  context.governanceProposalEditForm.required_tests_json = "[{\"kind\":\"regression\"},{\"kind\":\"replay\"}]";

  await methods.saveGovernanceProposalEdit.call(context, proposal);

  expect(context.apiRequest).toHaveBeenCalledWith("/governance/proposals/proposal-1", expect.objectContaining({
    method: "PATCH"
  }));
  const body = JSON.parse(context.apiRequest.mock.calls[0][1].body);
  expect(body.problem_statement).toBe("add better tests");
  expect(body.target).toEqual(proposal.target);
  expect(body.required_tests).toEqual([{ kind: "regression" }, { kind: "replay" }]);
  expect(context.currentGovernanceProposal.problem_statement).toBe("add better tests");
  expect(context.governanceProposalEditOpen).toBe(false);
  expect(context.showNotice).toHaveBeenCalledWith("success", "治理提案已保存。");

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/governance-proposals.html"), "utf8");
  expect(html).toContain("openGovernanceProposalEdit(currentGovernanceProposal)");
  expect(html).toContain("governanceProposalSourceFindings(currentGovernanceProposal)");
  expect(html).toContain("governanceProposalPatchDiffText(currentGovernanceProposal)");
  expect(html).toContain("governanceProposalToolAuthorizations");
});

test("governance methods flatten proposal experiments", () => {
  const methods = loadGovernanceMethods();
  const rows = methods.flattenGovernanceExperiments([
    {
      id: "proposal-1",
      status: "testing",
      proposal_type: "test_suite_update",
      problem_statement: "add tests",
      experiments: [
        { id: "experiment-1", proposal_id: "proposal-1", created_at: "2026-01-01T00:00:00Z" }
      ]
    },
    {
      id: "proposal-2",
      status: "canary",
      proposal_type: "agent_skill_update",
      problem_statement: "adjust agent",
      experiments: [
        { id: "experiment-2", proposal_id: "proposal-2", created_at: "2026-01-02T00:00:00Z" }
      ]
    }
  ]);

  expect(rows.map((item) => item.id)).toEqual(["experiment-2", "experiment-1"]);
  expect(rows[0].proposal_status).toBe("canary");
  expect(rows[0].problem_statement).toBe("adjust agent");
});

test("governance methods load experiments from read model filters", async () => {
  const methods = loadGovernanceMethods();
  const rows = [
    {
      id: "experiment-1",
      proposal_id: "proposal-1",
      proposal_status: "canary",
      status: "running",
      experiment_type: "canary"
    }
  ];
  const context = {
    ...methods,
    busy: { governanceExperiments: false },
    governanceExperimentFilters: { proposal_id: "proposal-1", status: "running", experiment_type: "canary" },
    governanceExperimentRows: [],
    governanceExperimentDetail: { id: "experiment-1", status: "planned" },
    governanceExperimentProposal: null,
    governanceProposals: [],
    apiRequest: jest.fn(async () => rows),
    showNotice: jest.fn()
  };

  await methods.loadGovernanceExperiments.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/governance/experiments?proposal_id=proposal-1&status=running&experiment_type=canary"
  );
  expect(context.governanceExperimentRows).toEqual(rows);
  expect(context.governanceExperimentDetail.status).toBe("running");
  expect(context.governanceExperimentDetail.proposal_status).toBe("canary");
  expect(context.busy.governanceExperiments).toBe(false);
});

test("governance methods manage experiment proposal actions and metric comparisons", async () => {
  const methods = loadGovernanceMethods();
  const experiment = {
    id: "experiment-1",
    proposal_id: "proposal-1",
    proposal_status: "approved",
    proposal_type: "agent_skill_update",
    source_run_id: "run-1",
    experiment_type: "canary",
    status: "running",
    summary: "canary running",
    before_metrics: { proposal_status: "approved", risk_level: "high" },
    after_metrics: { canary_status: "running", risk_level: "high" },
    result: {
      outcome: "canary_running",
      checks: [{ kind: "regression" }],
      canary_scope: { cohort: "internal" },
      rollback_conditions: ["metric_regression"]
    },
    canary_scope: { cohort: "internal" },
    rollback_conditions: ["metric_regression"],
    created_at: "2026-01-01T00:00:00Z"
  };
  const proposal = {
    id: "proposal-1",
    status: "approved",
    proposal_type: "agent_skill_update",
    problem_statement: "adjust agent",
    source_run_id: "run-1",
    experiments: [experiment]
  };
  const updatedProposal = {
    ...proposal,
    status: "canary",
    experiments: [
      experiment,
      {
        ...experiment,
        id: "experiment-2",
        proposal_status: "canary",
        created_at: "2026-01-02T00:00:00Z"
      }
    ]
  };
  const context = {
    ...methods,
    busy: { governanceProposalAction: false },
    governanceProposals: [],
    governanceExperimentRows: [experiment],
    governanceExperimentDetail: null,
    governanceExperimentLookupId: "",
    governanceExperimentProposal: null,
    apiRequest: jest.fn(async (url) => {
      if (url.endsWith("/activate-canary")) {
        return updatedProposal;
      }
      return proposal;
    }),
    showNotice: jest.fn()
  };

  const metricRows = methods.governanceExperimentMetricRows.call(context, experiment);
  expect(metricRows.map((row) => row.key)).toEqual(["canary_status", "proposal_status", "risk_level"]);
  expect(metricRows.find((row) => row.key === "risk_level").changed).toBe(false);
  expect(methods.governanceExperimentRegressionChecks(experiment)).toEqual([{ kind: "regression" }]);
  expect(methods.governanceExperimentCanaryScope(experiment)).toEqual({ cohort: "internal" });
  expect(methods.governanceExperimentRollbackConditions(experiment)).toEqual(["metric_regression"]);

  await methods.selectGovernanceExperiment.call(context, experiment);
  expect(context.apiRequest).toHaveBeenCalledWith("/governance/proposals/proposal-1");
  expect(context.governanceExperimentProposal.status).toBe("approved");
  expect(methods.governanceCanActivateCanary(methods.governanceExperimentProposalContext.call(context))).toBe(true);

  await methods.activateCanaryFromGovernanceExperiment.call(context, experiment);

  expect(context.apiRequest).toHaveBeenCalledWith("/governance/proposals/proposal-1/activate-canary", {
    method: "POST"
  });
  expect(context.governanceExperimentProposal.status).toBe("canary");
  expect(context.governanceExperimentDetail.id).toBe("experiment-2");
  expect(context.governanceExperimentRows[0].id).toBe("experiment-2");
  expect(context.showNotice).toHaveBeenCalledWith("success", "灰度已激活。");

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/governance-experiments.html"), "utf8");
  expect(html).toContain("selectGovernanceExperiment(experiment)");
  expect(html).toContain("governanceExperimentMetricRows(governanceExperimentDetail)");
  expect(html).toContain("activateCanaryFromGovernanceExperiment(governanceExperimentDetail)");
  expect(html).toContain("governanceExperimentRollbackConditions(governanceExperimentDetail)");
});

test("governance methods create proposals and run state actions", async () => {
  const methods = loadGovernanceMethods();
  const created = { id: "proposal-1", status: "draft", experiments: [] };
  const updated = { id: "proposal-1", status: "testing", experiments: [] };
  const context = {
    ...methods,
    busy: { governanceProposalCreate: false, governanceProposalAction: false },
    governanceProposalForm: {
      proposal_type: "test_suite_update",
      problem_statement: "add regression coverage",
      target_json: "{\"kind\":\"test_suite\"}"
    },
    governanceProposals: [],
    governanceReviewForm: { decision: "approved", review_notes: "" },
    apiRequest: jest.fn(async (url) => (url.includes("run-tests") ? updated : created)),
    loadGovernanceProposals: jest.fn(),
    navigate: jest.fn(),
    showNotice: jest.fn(),
    governanceProposalPath: (proposalId) => `/admin/governance/proposals/${proposalId}`,
    refreshGovernanceExperimentRows: jest.fn()
  };

  await methods.createGovernanceProposal.call(context);
  await methods.runGovernanceProposalTests.call(context, created);

  expect(context.apiRequest).toHaveBeenNthCalledWith(1, "/governance/proposals", {
    method: "POST",
    body: JSON.stringify({
      proposal_type: "test_suite_update",
      problem_statement: "add regression coverage",
      target: { kind: "test_suite" }
    })
  });
  expect(context.navigate).toHaveBeenCalledWith("/admin/governance/proposals/proposal-1");
  expect(context.apiRequest).toHaveBeenNthCalledWith(2, "/governance/proposals/proposal-1/run-tests", {
    method: "POST"
  });
  expect(context.currentGovernanceProposal.status).toBe("testing");
});

test("governance methods stream proposal activity snapshots", async () => {
  const { methods, FakeWebSocket } = loadGovernanceHarness();
  const proposal = {
    id: "proposal-activity",
    agent_run_id: "agent-run-activity",
    status: "draft",
    proposal_type: "test_suite_update",
    problem_statement: "add regression coverage",
    experiments: [],
    updated_at: "2026-01-01T00:00:00.000Z"
  };
  const context = {
    ...methods,
    apiBaseUrl: "/api/v1",
    busy: { governanceProposals: false },
    governanceProposals: [proposal],
    currentGovernanceProposal: null,
    governanceExperimentRows: [],
    governanceProposalActivityWs: null,
    governanceProposalActivityWsId: "",
    governanceProposalActivityWsStatus: "idle",
    governanceProposalAgentRun: null,
    governanceProposalAgentEvents: [],
    governanceProposalModelCalls: [],
    governanceProposalToolCalls: [],
    governanceProposalSkillActivations: [],
    governanceProposalToolAuthorizations: [],
    governanceProposalMemoryEntries: [],
    apiRequest: jest.fn(async () => proposal),
    showNotice: jest.fn()
  };

  await methods.loadGovernanceProposalDetail.call(context, "proposal-activity");
  const socket = FakeWebSocket.instances[0];
  socket.open();
  socket.message({
    event_type: "governance_proposal.activity.snapshot",
    payload: {
      proposal: {
        ...proposal,
        status: "testing",
        experiments: [{ id: "experiment-1", experiment_type: "regression", status: "succeeded" }]
      },
      agent_run: { id: "agent-run-activity", agent_key: "psop.governance", status: "succeeded" },
      agent_events: [{ id: "event-1", event_type: "governance.proposal.created" }],
      model_calls: [{ id: "model-call-1", provider: "deterministic" }],
      tool_calls: [],
      skill_activations: [],
      tool_authorizations: [],
      memory_entries: [{ id: "memory-1", memory_type: "episodic" }]
    }
  });

  expect(context.apiRequest).toHaveBeenCalledWith("/governance/proposals/proposal-activity");
  expect(socket.url).toBe("ws://localhost/ws/governance/proposals/proposal-activity");
  expect(context.governanceProposalActivityWsStatus).toBe("open");
  expect(context.currentGovernanceProposal.status).toBe("testing");
  expect(context.governanceProposals[0].status).toBe("testing");
  expect(context.governanceExperimentRows[0].id).toBe("experiment-1");
  expect(context.governanceProposalAgentRun.agent_key).toBe("psop.governance");
  expect(context.governanceProposalAgentEvents).toHaveLength(1);
  expect(context.governanceProposalModelCalls).toHaveLength(1);
  expect(context.governanceProposalMemoryEntries).toHaveLength(1);

  methods.disconnectGovernanceProposalActivityWebSocket.call(context);

  expect(context.governanceProposalActivityWs).toBeNull();
  expect(context.governanceProposalActivityWsStatus).toBe("idle");
});

test("governance methods decide tool authorizations", async () => {
  const methods = loadGovernanceMethods();
  const authorization = { id: "auth-1", status: "pending" };
  const context = {
    ...methods,
    busy: { toolAuthorizationAction: false },
    toolAuthorizations: [authorization],
    apiRequest: jest.fn(async () => ({ id: "auth-1", status: "approved" })),
    showNotice: jest.fn()
  };

  await methods.decideToolAuthorization.call(context, authorization, "approve");

  expect(context.apiRequest).toHaveBeenCalledWith("/tool-authorizations/auth-1/approve", {
    method: "POST",
    body: JSON.stringify({
      response_payload: {
        decision_source: "platform_tool_authorizations_ui"
      }
    })
  });
  expect(context.toolAuthorizations[0].status).toBe("approved");
  expect(context.busy.toolAuthorizationAction).toBe(false);
});
