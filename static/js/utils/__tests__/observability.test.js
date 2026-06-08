const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadObservabilityMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/observability.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        buildPlatformObservabilityPath: () => "/admin/platform/observability",
        buildPlatformAgentRunsPath: () => "/admin/platform/agent-runs",
        buildToolAuthorizationsPath: () => "/admin/platform/tool-authorizations",
        buildRunLivePath: (runId) => `/admin/runs/${runId}/live`
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
});

test("observability methods query agent run observability streams", async () => {
  const methods = loadObservabilityMethods();
  const run = { id: "agent-run-1", agent_key: "pskill.runner", status: "succeeded", run_id: "run-1" };
  const events = [{ id: "event-1", event_type: "agent.run.created", payload: {} }];
  const modelCalls = [{ id: "model-1", route_key: "runner", status: "succeeded", usage_json: { total_tokens: 42 } }];
  const toolCalls = [{ id: "tool-1", tool_name: "psop.runtime.read", status: "succeeded", arguments_summary: {} }];
  const activations = [{ id: "activation-1", package_id: "pkg-1", version_id: "ver-1" }];
  const authorizations = [{ id: "auth-1", tool_name: "psop.repository.commit_patch", status: "pending" }];
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
  expect(context.observabilityAgentRunDetail).toBe(run);
  expect(context.observabilityAgentEvents).toEqual(events);
  expect(context.observabilityModelCalls).toEqual(modelCalls);
  expect(context.observabilityToolCalls).toEqual(toolCalls);
  expect(context.observabilitySkillActivations).toEqual(activations);
  expect(context.observabilityToolAuthorizations).toEqual(authorizations);
  expect(methods.observabilityAgentRunPath("agent-run-1")).toBe("/admin/platform/agent-runs/agent-run-1");

  methods.resetObservabilityAgentRunQuery.call(context);

  expect(context.observabilityFilters.agent_run_id).toBe("");
  expect(context.observabilityAgentRunDetail).toBeNull();
  expect(context.observabilityToolAuthorizations).toEqual([]);
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
      }
    }
  };

  expect(methods.observabilityTopEntries({ beta: 1, alpha: 3, gamma: 3 }, 2)).toEqual([
    { key: "alpha", value: 3 },
    { key: "gamma", value: 3 }
  ]);
  expect(methods.observabilityTraceEventTypeOptions.call(context)).toEqual(["runtime.completed", "runtime.failed"]);
  expect(methods.observabilityOtelTone(true)).toContain("emerald");
  expect(methods.observabilityOtelTone(false)).toContain("amber");
});
