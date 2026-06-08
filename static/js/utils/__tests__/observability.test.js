const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadObservabilityMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/observability.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        buildPlatformObservabilityPath: () => "/admin/platform/observability",
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
          for (const key of ["tab", "event_id", "model_call_id", "tool_call_id", "authorization_id"]) {
            if (focus[key]) {
              params.set(key, focus[key]);
            }
          }
          const query = params.toString();
          return query ? `/admin/platform/agent-runs/${agentRunId}?${query}` : `/admin/platform/agent-runs/${agentRunId}`;
        },
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
        buildEvaluationReportsPath: () => "/admin/evaluations",
        buildEvaluationFindingsPath: () => "/admin/evaluations/findings",
        buildGovernanceProposalsPath: () => "/admin/governance/proposals",
        buildGovernanceExperimentsPath: () => "/admin/governance/experiments",
        buildRunLivePath: (runId) => `/admin/runs/${runId}/live`,
        buildReplayPath: (runId, focus = {}) => {
          const params = new URLSearchParams();
          if (focus.event_id) {
            params.set("event_id", focus.event_id);
          }
          if (focus.trace_id) {
            params.set("trace_id", focus.trace_id);
          }
          if (focus.seq_no) {
            params.set("seq_no", focus.seq_no);
          }
          const query = params.toString();
          return query ? `/admin/runs/${runId}/live/replay?${query}` : `/admin/runs/${runId}/live/replay`;
        }
      }
    },
    URLSearchParams,
    Intl,
    Number,
    Math,
    String,
    Array,
    Object,
    Map,
    JSON
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleObservabilityMethods;
}

test("observability methods load global metrics with the selected window", async () => {
  const methods = loadObservabilityMethods();
  const payload = {
    generated_at: "2026-06-05T00:00:00Z",
    since: "2026-06-04T00:00:00Z",
    runtime: { run_trace_event_type_counts: {} },
    agents: {},
    evaluations: {
      outcome_counts: { failed: 1 },
      finding_status_counts: { open: 1 },
      finding_category_counts: { runner_issue: 1 }
    },
    governance: {
      status_counts: { canary: 1 },
      proposal_type_counts: { agent_skill_update: 1 }
    },
    open_telemetry: { configured: true }
  };
  const context = {
    ...methods,
    busy: { observabilityMetrics: false },
    observabilityFilters: { window_hours: 72, run_id: "", trace_event_type: "", agent_run_id: "" },
    observabilityMetrics: null,
    apiRequest: jest.fn(async () => payload),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityMetrics.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/observability/metrics?window_hours=72");
  expect(context.observabilityMetrics).toBe(payload);
  expect(context.busy.observabilityMetrics).toBe(false);
  expect(methods.platformObservabilityPath()).toBe("/admin/platform/observability");
  expect(methods.observabilityAgentRunsAgentPath("pskill.runner")).toBe(
    "/admin/platform/agent-runs?agent_key=pskill.runner"
  );
  expect(methods.observabilityAgentRunsStatusPath("waiting_tool_authorization")).toBe(
    "/admin/platform/agent-runs?status=waiting_tool_authorization"
  );
  expect(methods.observabilityToolAuthorizationsStatusPath("pending")).toBe(
    "/admin/platform/tool-authorizations?status=pending"
  );
  expect(methods.observabilityEvaluationReportsPath()).toBe("/admin/evaluations");
  expect(methods.observabilityEvaluationFindingsPath()).toBe("/admin/evaluations/findings");
  expect(methods.observabilityGovernanceProposalsPath()).toBe("/admin/governance/proposals");
  expect(methods.observabilityGovernanceExperimentsPath()).toBe("/admin/governance/experiments");
  expect(methods.observabilityEvaluationOutcomeOptions.call(context)).toEqual(["failed"]);
  expect(methods.observabilityFindingStatusOptions.call(context)).toEqual(["open"]);
  expect(methods.observabilityFindingCategoryOptions.call(context)).toEqual(["runner_issue"]);
  expect(methods.observabilityGovernanceStatusOptions.call(context)).toEqual(["canary"]);
  expect(methods.observabilityGovernanceTypeOptions.call(context)).toEqual(["agent_skill_update"]);
});

test("observability methods query run traces with optional event type", async () => {
  const methods = loadObservabilityMethods();
  const traces = [{ id: "trace-1", event_type: "runtime.failed", payload: { error: "failed" } }];
  const context = {
    ...methods,
    busy: { observabilityTraceLookup: false },
    observabilityFilters: {
      window_hours: 24,
      run_id: "run 1",
      trace_event_type: "runtime.failed"
    },
    observabilityRunTraces: [],
    observabilityTraceLookupRunId: "",
    apiRequest: jest.fn(async () => traces),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityRunTraces.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/runs/run%201/traces?event_type=runtime.failed");
  expect(context.observabilityRunTraces).toEqual(traces);
  expect(context.observabilityTraceLookupRunId).toBe("run 1");
  expect(methods.observabilityRunLivePath("run-1")).toBe("/admin/runs/run-1/live");
  expect(methods.observabilityRunReplayPath.call(context, { id: "trace-1", run_id: "run 1", seq_no: 7 })).toBe(
    "/admin/runs/run 1/live/replay?trace_id=trace-1"
  );
  expect(methods.observabilityRunReplayPath.call(context, { run_id: "run 1", seq_no: 7 })).toBe(
    "/admin/runs/run 1/live/replay?seq_no=7"
  );
});

test("observability methods query global run traces when run id is empty", async () => {
  const methods = loadObservabilityMethods();
  const traces = [{ id: "trace-1", run_id: "run-1", event_type: "runtime.failed", payload: { error: "failed" } }];
  const context = {
    ...methods,
    busy: { observabilityTraceLookup: false },
    observabilityFilters: {
      window_hours: 72,
      run_id: "",
      trace_event_type: "runtime.failed"
    },
    observabilityRunTraces: [],
    observabilityTraceLookupRunId: "",
    apiRequest: jest.fn(async () => traces),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityRunTraces.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/observability/run-traces?event_type=runtime.failed&window_hours=72&limit=50"
  );
  expect(context.observabilityRunTraces).toEqual(traces);
  expect(context.observabilityTraceLookupRunId).toBe("");

  await methods.selectObservabilityTraceEventType.call(context, "runtime.completed");

  expect(context.observabilityFilters.run_id).toBe("");
  expect(context.observabilityFilters.trace_event_type).toBe("runtime.completed");
  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/run-traces?event_type=runtime.completed&window_hours=72&limit=50"
  );
});

test("observability methods query global run events from distribution filters", async () => {
  const methods = loadObservabilityMethods();
  const events = [
    { id: "event-1", run_id: "run-1", event_kind: "tool_authorization_request", payload_inline: {} }
  ];
  const context = {
    ...methods,
    busy: { observabilityEventLookup: false },
    observabilityFilters: {
      window_hours: 24,
      event_run_id: "",
      run_event_kind: "tool_authorization_request"
    },
    observabilityRunEvents: [],
    observabilityEventLookupRunId: "",
    apiRequest: jest.fn(async () => events),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityRunEvents.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/observability/run-events?event_kind=tool_authorization_request&window_hours=24&limit=50"
  );
  expect(context.observabilityRunEvents).toEqual(events);
  expect(context.observabilityEventLookupRunId).toBe("");
  expect(methods.observabilityRunEventReplayPath.call(context, events[0])).toBe(
    "/admin/runs/run-1/live/replay?event_id=event-1"
  );

  context.observabilityFilters.event_run_id = "run 1";
  context.observabilityFilters.run_event_kind = "";
  await methods.loadObservabilityRunEvents.call(context);

  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/run-events?run_id=run+1&window_hours=24&limit=50"
  );
  expect(context.observabilityEventLookupRunId).toBe("run 1");

  await methods.selectObservabilityRunEventKind.call(context, "runtime.output");

  expect(context.observabilityFilters.event_run_id).toBe("");
  expect(context.observabilityFilters.run_event_kind).toBe("runtime.output");
  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/run-events?event_kind=runtime.output&window_hours=24&limit=50"
  );

  methods.resetObservabilityEventQuery.call(context);

  expect(context.observabilityFilters.event_run_id).toBe("");
  expect(context.observabilityFilters.run_event_kind).toBe("");
  expect(context.observabilityRunEvents).toEqual([]);
});

test("observability methods derive run event kind options", () => {
  const methods = loadObservabilityMethods();
  const context = {
    ...methods,
    observabilityMetrics: {
      runtime: {
        run_event_kind_counts: {
          "runtime.output": 1,
          "tool_authorization_request": 3
        }
      }
    }
  };

  expect(methods.observabilityRunEventKindOptions.call(context)).toEqual([
    "runtime.output",
    "tool_authorization_request"
  ]);
});

test("observability methods query global agent events from distribution filters", async () => {
  const methods = loadObservabilityMethods();
  const events = [
    {
      id: "agent-event-1",
      agent_run_id: "agent-run-1",
      event_type: "tool.authorization_requested",
      payload: {}
    }
  ];
  const context = {
    ...methods,
    busy: { observabilityAgentEventLookup: false },
    observabilityFilters: {
      window_hours: 24,
      agent_event_agent_key: "",
      agent_event_run_id: "",
      agent_event_type: "tool.authorization_requested"
    },
    observabilityAgentEventResults: [],
    apiRequest: jest.fn(async () => events),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityAgentEvents.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/observability/agent-events?event_type=tool.authorization_requested&window_hours=24&limit=50"
  );
  expect(context.observabilityAgentEventResults).toEqual(events);
  expect(methods.observabilityAgentEventPath(events[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=events&event_id=agent-event-1"
  );

  context.observabilityFilters.agent_event_agent_key = "pskill.runner";
  context.observabilityFilters.agent_event_run_id = "run 1";
  context.observabilityFilters.agent_event_type = "";
  await methods.loadObservabilityAgentEvents.call(context);

  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/agent-events?agent_key=pskill.runner&run_id=run+1&window_hours=24&limit=50"
  );

  await methods.selectObservabilityAgentEventType.call(context, "agent.run.created");

  expect(context.observabilityFilters.agent_event_agent_key).toBe("");
  expect(context.observabilityFilters.agent_event_run_id).toBe("");
  expect(context.observabilityFilters.agent_event_type).toBe("agent.run.created");
  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/agent-events?event_type=agent.run.created&window_hours=24&limit=50"
  );

  methods.resetObservabilityAgentEventQuery.call(context);

  expect(context.observabilityFilters.agent_event_agent_key).toBe("");
  expect(context.observabilityFilters.agent_event_run_id).toBe("");
  expect(context.observabilityFilters.agent_event_type).toBe("");
  expect(context.observabilityAgentEventResults).toEqual([]);
});

test("observability methods query global tool calls from status filters", async () => {
  const methods = loadObservabilityMethods();
  const calls = [
    {
      id: "tool-call-1",
      agent_run_id: "agent-run-1",
      tool_name: "psop.repository.commit_patch",
      status: "failed",
      arguments_summary: {}
    }
  ];
  const context = {
    ...methods,
    busy: { observabilityToolCallLookup: false },
    observabilityFilters: {
      window_hours: 24,
      tool_call_agent_key: "",
      tool_call_run_id: "",
      tool_call_status: "failed",
      tool_call_tool_name: ""
    },
    observabilityToolCallResults: [],
    apiRequest: jest.fn(async () => calls),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityToolCalls.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/observability/tool-calls?status=failed&window_hours=24&limit=50"
  );
  expect(context.observabilityToolCallResults).toEqual(calls);
  expect(methods.observabilityToolCallPath(calls[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=tools&tool_call_id=tool-call-1"
  );

  context.observabilityFilters.tool_call_agent_key = "pskill.runner";
  context.observabilityFilters.tool_call_run_id = "run 1";
  context.observabilityFilters.tool_call_status = "";
  context.observabilityFilters.tool_call_tool_name = "psop.memory.search";
  await methods.loadObservabilityToolCalls.call(context);

  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/tool-calls?agent_key=pskill.runner&run_id=run+1&tool_name=psop.memory.search&window_hours=24&limit=50"
  );

  await methods.selectObservabilityToolCallStatus.call(context, "blocked");

  expect(context.observabilityFilters.tool_call_agent_key).toBe("");
  expect(context.observabilityFilters.tool_call_run_id).toBe("");
  expect(context.observabilityFilters.tool_call_status).toBe("blocked");
  expect(context.observabilityFilters.tool_call_tool_name).toBe("");
  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/tool-calls?status=blocked&window_hours=24&limit=50"
  );

  methods.resetObservabilityToolCallQuery.call(context);

  expect(context.observabilityFilters.tool_call_agent_key).toBe("");
  expect(context.observabilityFilters.tool_call_run_id).toBe("");
  expect(context.observabilityFilters.tool_call_status).toBe("");
  expect(context.observabilityFilters.tool_call_tool_name).toBe("");
  expect(context.observabilityToolCallResults).toEqual([]);
});

test("observability methods query global model calls from provider filters", async () => {
  const methods = loadObservabilityMethods();
  const calls = [
    {
      id: "model-call-1",
      agent_run_id: "agent-run-1",
      provider: "deterministic",
      route_key: "runner",
      model_name: "test-model",
      status: "failed",
      usage_json: { total_tokens: 42 }
    }
  ];
  const context = {
    ...methods,
    busy: { observabilityModelCallLookup: false },
    observabilityFilters: {
      window_hours: 24,
      model_call_agent_key: "",
      model_call_run_id: "",
      model_call_provider: "deterministic",
      model_call_status: ""
    },
    observabilityModelCallResults: [],
    apiRequest: jest.fn(async () => calls),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityModelCalls.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/observability/model-calls?provider=deterministic&window_hours=24&limit=50"
  );
  expect(context.observabilityModelCallResults).toEqual(calls);
  expect(methods.observabilityModelCallPath(calls[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=model&model_call_id=model-call-1"
  );

  context.observabilityFilters.model_call_agent_key = "pskill.runner";
  context.observabilityFilters.model_call_run_id = "run 1";
  context.observabilityFilters.model_call_provider = "";
  context.observabilityFilters.model_call_status = "failed";
  await methods.loadObservabilityModelCalls.call(context);

  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/model-calls?agent_key=pskill.runner&run_id=run+1&status=failed&window_hours=24&limit=50"
  );

  await methods.selectObservabilityModelCallProvider.call(context, "openai");

  expect(context.observabilityFilters.model_call_agent_key).toBe("");
  expect(context.observabilityFilters.model_call_run_id).toBe("");
  expect(context.observabilityFilters.model_call_provider).toBe("openai");
  expect(context.observabilityFilters.model_call_status).toBe("");
  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/model-calls?provider=openai&window_hours=24&limit=50"
  );

  methods.resetObservabilityModelCallQuery.call(context);

  expect(context.observabilityFilters.model_call_agent_key).toBe("");
  expect(context.observabilityFilters.model_call_run_id).toBe("");
  expect(context.observabilityFilters.model_call_provider).toBe("");
  expect(context.observabilityFilters.model_call_status).toBe("");
  expect(context.observabilityModelCallResults).toEqual([]);
});

test("observability methods query global skill activations from package filters", async () => {
  const methods = loadObservabilityMethods();
  const activations = [
    {
      id: "activation-1",
      agent_run_id: "agent-run-1",
      package_id: "skill-package-1",
      version_id: "skill-version-1",
      activation_context: {}
    }
  ];
  const context = {
    ...methods,
    busy: { observabilitySkillActivationLookup: false },
    observabilityFilters: {
      window_hours: 24,
      skill_activation_agent_key: "",
      skill_activation_run_id: "",
      skill_activation_package_id: "skill-package-1",
      skill_activation_version_id: ""
    },
    observabilitySkillActivationResults: [],
    apiRequest: jest.fn(async () => activations),
    showNotice: jest.fn()
  };

  await methods.loadObservabilitySkillActivations.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/observability/skill-activations?package_id=skill-package-1&window_hours=24&limit=50"
  );
  expect(context.observabilitySkillActivationResults).toEqual(activations);
  expect(methods.observabilitySkillActivationPath(activations[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=skills"
  );

  context.observabilityFilters.skill_activation_agent_key = "pskill.runner";
  context.observabilityFilters.skill_activation_run_id = "run 1";
  context.observabilityFilters.skill_activation_package_id = "";
  context.observabilityFilters.skill_activation_version_id = "skill-version-1";
  await methods.loadObservabilitySkillActivations.call(context);

  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/skill-activations?agent_key=pskill.runner&run_id=run+1&version_id=skill-version-1&window_hours=24&limit=50"
  );

  await methods.selectObservabilitySkillActivationPackage.call(context, "skill-package-2");

  expect(context.observabilityFilters.skill_activation_agent_key).toBe("");
  expect(context.observabilityFilters.skill_activation_run_id).toBe("");
  expect(context.observabilityFilters.skill_activation_package_id).toBe("skill-package-2");
  expect(context.observabilityFilters.skill_activation_version_id).toBe("");
  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/skill-activations?package_id=skill-package-2&window_hours=24&limit=50"
  );

  methods.resetObservabilitySkillActivationQuery.call(context);

  expect(context.observabilityFilters.skill_activation_agent_key).toBe("");
  expect(context.observabilityFilters.skill_activation_run_id).toBe("");
  expect(context.observabilityFilters.skill_activation_package_id).toBe("");
  expect(context.observabilityFilters.skill_activation_version_id).toBe("");
  expect(context.observabilitySkillActivationResults).toEqual([]);
});

test("observability methods query global tool authorizations from status filters", async () => {
  const methods = loadObservabilityMethods();
  const authorizations = [
    {
      id: "auth-1",
      agent_run_id: "agent-run-1",
      tool_name: "psop.repository.commit_patch",
      status: "pending",
      risk_level: "high",
      tool_arguments_summary: {}
    }
  ];
  const context = {
    ...methods,
    busy: { observabilityToolAuthorizationLookup: false },
    observabilityFilters: {
      window_hours: 24,
      tool_authorization_agent_key: "",
      tool_authorization_run_id: "",
      tool_authorization_status: "pending",
      tool_authorization_risk_level: "high",
      tool_authorization_tool_name: ""
    },
    observabilityToolAuthorizationResults: [],
    apiRequest: jest.fn(async () => authorizations),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityToolAuthorizations.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith(
    "/observability/tool-authorizations?status=pending&risk_level=high&window_hours=24&limit=50"
  );
  expect(context.observabilityToolAuthorizationResults).toEqual(authorizations);
  expect(methods.observabilityToolAuthorizationPath(authorizations[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=authorizations&authorization_id=auth-1"
  );
  expect(methods.observabilityToolAuthorizationHistoryPath(authorizations[0])).toBe(
    "/admin/platform/tool-authorizations?status=pending&tool_name=psop.repository.commit_patch"
  );

  context.observabilityFilters.tool_authorization_agent_key = "pskill.runner";
  context.observabilityFilters.tool_authorization_run_id = "run 1";
  context.observabilityFilters.tool_authorization_status = "";
  context.observabilityFilters.tool_authorization_risk_level = "";
  context.observabilityFilters.tool_authorization_tool_name = "psop.memory.search";
  await methods.loadObservabilityToolAuthorizations.call(context);

  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/tool-authorizations?agent_key=pskill.runner&run_id=run+1&tool_name=psop.memory.search&window_hours=24&limit=50"
  );

  await methods.selectObservabilityToolAuthorizationStatus.call(context, "rejected");

  expect(context.observabilityFilters.tool_authorization_agent_key).toBe("");
  expect(context.observabilityFilters.tool_authorization_run_id).toBe("");
  expect(context.observabilityFilters.tool_authorization_status).toBe("rejected");
  expect(context.observabilityFilters.tool_authorization_risk_level).toBe("");
  expect(context.observabilityFilters.tool_authorization_tool_name).toBe("");
  expect(context.apiRequest).toHaveBeenLastCalledWith(
    "/observability/tool-authorizations?status=rejected&window_hours=24&limit=50"
  );

  methods.resetObservabilityToolAuthorizationQuery.call(context);

  expect(context.observabilityFilters.tool_authorization_agent_key).toBe("");
  expect(context.observabilityFilters.tool_authorization_run_id).toBe("");
  expect(context.observabilityFilters.tool_authorization_status).toBe("");
  expect(context.observabilityFilters.tool_authorization_risk_level).toBe("");
  expect(context.observabilityFilters.tool_authorization_tool_name).toBe("");
  expect(context.observabilityToolAuthorizationResults).toEqual([]);
});

test("observability methods query agent run observability streams", async () => {
  const methods = loadObservabilityMethods();
  const run = { id: "agent-run-1", agent_key: "pskill.runner", status: "succeeded", run_id: "run-1" };
  const events = [{ id: "event-1", event_type: "agent.run.created", payload: {} }];
  const modelCalls = [{ id: "model-1", route_key: "runner", status: "succeeded", usage_json: { total_tokens: 42 } }];
  const toolCalls = [{ id: "tool-1", tool_name: "psop.runtime.read", status: "succeeded", arguments_summary: {} }];
  const activations = [{ id: "activation-1", package_id: "pkg-1", version_id: "ver-1" }];
  const authorizations = [{ id: "auth-1", tool_name: "psop.repository.commit_patch", status: "pending" }];
  const memoryEntries = [{ id: "memory-1", title: "Replay finding", memory_type: "episodic" }];
  const context = {
    ...methods,
    busy: { observabilityAgentRunLookup: false },
    observabilityFilters: {
      window_hours: 24,
      run_id: "",
      trace_event_type: "",
      agent_run_id: "agent run 1"
    },
    observabilityAgentRunDetail: null,
    observabilityAgentEvents: [],
    observabilityModelCalls: [],
    observabilityToolCalls: [],
    observabilitySkillActivations: [],
    observabilityToolAuthorizations: [],
    observabilityMemoryEntries: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/agent-runs/agent%20run%201") {
        return run;
      }
      if (url.endsWith("/events")) {
        return events;
      }
      if (url.endsWith("/model-calls")) {
        return modelCalls;
      }
      if (url.endsWith("/tool-calls")) {
        return toolCalls;
      }
      if (url.endsWith("/skill-activations")) {
        return activations;
      }
      if (url.endsWith("/tool-authorizations")) {
        return authorizations;
      }
      if (url.endsWith("/memory-entries")) {
        return memoryEntries;
      }
      return null;
    }),
    showNotice: jest.fn()
  };

  await methods.loadObservabilityAgentRun.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent%20run%201");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent%20run%201/events");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent%20run%201/model-calls");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent%20run%201/tool-calls");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent%20run%201/skill-activations");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent%20run%201/tool-authorizations");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent%20run%201/memory-entries");
  expect(context.observabilityAgentRunDetail).toBe(run);
  expect(context.observabilityAgentEvents).toEqual(events);
  expect(context.observabilityModelCalls).toEqual(modelCalls);
  expect(context.observabilityToolCalls).toEqual(toolCalls);
  expect(context.observabilitySkillActivations).toEqual(activations);
  expect(context.observabilityToolAuthorizations).toEqual(authorizations);
  expect(context.observabilityMemoryEntries).toEqual(memoryEntries);
  expect(methods.observabilityAgentRunPath("agent-run-1")).toBe("/admin/platform/agent-runs/agent-run-1");
  expect(methods.observabilityAgentRunToolCallPath.call(context, toolCalls[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=tools&tool_call_id=tool-1"
  );
  expect(methods.observabilityAgentRunAuthorizationPath.call(context, authorizations[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=authorizations&authorization_id=auth-1"
  );
  expect(methods.observabilityToolAuthorizationHistoryPath(authorizations[0])).toBe(
    "/admin/platform/tool-authorizations?status=pending&tool_name=psop.repository.commit_patch"
  );

  methods.resetObservabilityAgentRunQuery.call(context);

  expect(context.observabilityFilters.agent_run_id).toBe("");
  expect(context.observabilityAgentRunDetail).toBeNull();
  expect(context.observabilityToolAuthorizations).toEqual([]);
  expect(context.observabilityMemoryEntries).toEqual([]);
});

test("observability methods sort distribution entries and derive trace event options", () => {
  const methods = loadObservabilityMethods();
  const context = {
    ...methods,
    observabilityMetrics: {
      runtime: {
        run_trace_event_type_counts: {
          "runtime.completed": 1,
          "runtime.failed": 3
        }
      },
      agents: {
        agent_event_type_counts: {
          "agent.run.created": 1,
          "tool.authorization_requested": 3
        },
        agent_run_key_counts: {
          "pskill.runner": 2,
          "psop.governance": 1
        },
        tool_call_status_counts: {
          failed: 2,
          succeeded: 1
        },
        model_call_provider_counts: {
          deterministic: 2,
          openai: 1
        },
        model_call_status_counts: {
          failed: 1,
          succeeded: 2
        },
        skill_activation_package_counts: {
          "skill-package-1": 2,
          "skill-package-2": 1
        },
        tool_authorization_status_counts: {
          approved: 1,
          executed: 1,
          pending: 2
        },
        tool_authorization_risk_counts: {
          high: 2,
          medium: 1
        }
      }
    }
  };

  expect(methods.observabilityTopEntries({ beta: 1, alpha: 3, gamma: 3 }, 2)).toEqual([
    { key: "alpha", value: 3 },
    { key: "gamma", value: 3 }
  ]);
  expect(methods.observabilityTraceEventTypeOptions.call(context)).toEqual(["runtime.completed", "runtime.failed"]);
  expect(methods.observabilityAgentEventTypeOptions.call(context)).toEqual([
    "agent.run.created",
    "tool.authorization_requested"
  ]);
  expect(methods.observabilityAgentKeyOptions.call(context)).toEqual(["pskill.runner", "psop.governance"]);
  expect(methods.observabilityToolCallStatusOptions.call(context)).toEqual(["failed", "succeeded"]);
  expect(methods.observabilityModelCallProviderOptions.call(context)).toEqual(["deterministic", "openai"]);
  expect(methods.observabilityModelCallStatusOptions.call(context)).toEqual(["failed", "succeeded"]);
  expect(methods.observabilitySkillActivationPackageOptions.call(context)).toEqual([
    "skill-package-1",
    "skill-package-2"
  ]);
  expect(methods.observabilityToolAuthorizationStatusOptions.call(context)).toEqual([
    "pending",
    "approved",
    "executed",
    "rejected",
    "expired",
    "cancelled"
  ]);
  expect(methods.observabilityToolAuthorizationRiskOptions.call(context)).toEqual(["high", "medium"]);
  expect(methods.observabilityOtelTone(true)).toContain("emerald");
  expect(methods.observabilityOtelTone(false)).toContain("amber");
});

test("observability page exposes linked distribution filters", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/platform-observability.html"), "utf8");

  expect(html).toContain("observabilityAgentRunsAgentPath(item.key)");
  expect(html).toContain("observabilityAgentRunsStatusPath(item.key)");
  expect(html).toContain("selectObservabilityToolAuthorizationStatus(item.key)");
  expect(html).toContain("observabilityMetrics.agents.agent_run_status_counts");
  expect(html).toContain("selectObservabilityRunEventKind(item.key)");
  expect(html).toContain("observabilityRunEventReplayPath(event)");
  expect(html).toContain("selectObservabilityAgentEventType(item.key)");
  expect(html).toContain("observabilityAgentEventPath(event)");
  expect(html).toContain("selectObservabilityToolCallStatus(item.key)");
  expect(html).toContain("observabilityToolCallPath(call)");
  expect(html).toContain("selectObservabilityModelCallProvider(item.key)");
  expect(html).toContain("observabilityModelCallPath(call)");
  expect(html).toContain("selectObservabilitySkillActivationPackage(item.key)");
  expect(html).toContain("observabilitySkillActivationPath(activation)");
  expect(html).toContain("observabilityToolAuthorizationPath(authorization)");
  expect(html).toContain("observabilityToolAuthorizationHistoryPath(authorization)");
  expect(html).toContain("selectObservabilityTraceEventType(item.key)");
  expect(html).toContain("observabilityRunReplayPath(trace)");
  expect(html).toContain("trace.trace_id");
  expect(html).toContain("trace.span_id");
  expect(html).toContain("observabilityAgentRunToolCallPath(call)");
  expect(html).toContain("observabilityAgentRunAuthorizationPath(authorization)");
});
