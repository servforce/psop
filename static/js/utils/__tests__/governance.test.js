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
      PSOPConsoleHelpers: {
        buildGovernanceProposalsPath: () => "/admin/governance/proposals",
        buildGovernanceProposalPath: (proposalId) => `/admin/governance/proposals/${proposalId}`,
        buildGovernanceExperimentsPath: () => "/admin/governance/experiments",
        buildToolAuthorizationsPath: () => "/admin/platform/tool-authorizations",
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
  expect(methods.governanceProposalPath("proposal-1")).toBe("/admin/governance/proposals/proposal-1");
});

test("governance methods sync tool authorization filter from location", () => {
  const methods = loadGovernanceMethods("?tool_name=psop.agent_version.activate");
  const context = {
    ...methods,
    toolAuthorizationFilters: { status: "pending", tool_name: "" },
    toolAuthorizationLocationSearch: ""
  };

  methods.syncToolAuthorizationFiltersFromLocation.call(context);

  expect(context.toolAuthorizationFilters.tool_name).toBe("psop.agent_version.activate");
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
    { id: "experiment-1", proposal_id: "proposal-1", status: "running", experiment_type: "canary" }
  ];
  const context = {
    ...methods,
    busy: { governanceExperiments: false },
    governanceExperimentFilters: { proposal_id: "proposal-1", status: "running", experiment_type: "canary" },
    governanceExperimentRows: [],
    governanceExperimentDetail: { id: "experiment-1", status: "planned" },
    apiRequest: jest.fn(async () => rows),
    showNotice: jest.fn()
  };

  await methods.loadGovernanceExperiments.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/governance/experiments?proposal_id=proposal-1&status=running&experiment_type=canary"
  );
  expect(context.governanceExperimentRows).toEqual(rows);
  expect(context.governanceExperimentDetail.status).toBe("running");
  expect(context.busy.governanceExperiments).toBe(false);
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
