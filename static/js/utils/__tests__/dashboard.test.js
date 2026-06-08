const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadDashboardMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/dashboard.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        buildDashboardPath: () => "/admin/dashboard",
        buildEvaluationReportsPath: () => "/admin/evaluations",
        buildEvaluationFindingsPath: () => "/admin/evaluations/findings",
        buildGovernanceProposalsPath: (filters = {}) => {
          const params = new URLSearchParams();
          if (filters.status) {
            params.set("status", filters.status);
          }
          const query = params.toString();
          return query ? `/admin/governance/proposals?${query}` : "/admin/governance/proposals";
        },
        buildGovernanceExperimentsPath: (filters = {}) => {
          const params = new URLSearchParams();
          for (const key of ["experiment_id", "proposal_id", "status", "experiment_type"]) {
            if (filters[key]) {
              params.set(key, filters[key]);
            }
          }
          const query = params.toString();
          return query ? `/admin/governance/experiments?${query}` : "/admin/governance/experiments";
        },
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
        buildPlatformSkillsPath: () => "/admin/platform/skills",
        buildPlatformToolsPath: () => "/admin/platform/tools",
        buildPlatformMemoryPath: () => "/admin/platform/memory",
        buildPlatformObservabilityPath: () => "/admin/platform/observability",
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
    Map
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleDashboardMethods;
}

test("dashboard methods load metrics through the observability API", async () => {
  const methods = loadDashboardMethods();
  const payload = {
    generated_at: "2026-06-05T00:00:00Z",
    window_hours: 72,
    pskills: {},
    runtime: {},
    evaluations: {},
    governance: {},
    observability: {},
    agents: [
      {
        agent_key: "pskill.runner",
        recent_run_count: 3,
        success_rate: 0.6667,
        average_duration_ms: 1250,
        tool_failure_rate: 0.1
      }
    ]
  };
  const context = {
    ...methods,
    busy: { dashboard: false },
    dashboardFilters: { window_hours: 72 },
    dashboardMetrics: null,
    apiRequest: jest.fn(async () => payload),
    showNotice: jest.fn()
  };

  await methods.loadDashboardMetrics.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/observability/dashboard?window_hours=72");
  expect(context.dashboardMetrics).toBe(payload);
  expect(context.busy.dashboard).toBe(false);
  expect(methods.dashboardPath()).toBe("/admin/dashboard");
  expect(methods.dashboardAgentRunsForAgentPath("pskill.runner")).toBe(
    "/admin/platform/agent-runs?agent_key=pskill.runner"
  );
  expect(methods.dashboardWaitingAuthorizationsForAgentPath("pskill.runner")).toBe(
    "/admin/platform/agent-runs?agent_key=pskill.runner&status=waiting_tool_authorization"
  );
  expect(methods.dashboardToolAuthorizationsPath({ status: "pending" })).toBe(
    "/admin/platform/tool-authorizations?status=pending"
  );
  expect(methods.dashboardGovernanceProposalsPath({ status: "testing" })).toBe(
    "/admin/governance/proposals?status=testing"
  );
  expect(methods.dashboardGovernanceExperimentsPath({ status: "running" })).toBe(
    "/admin/governance/experiments?status=running"
  );
  expect(methods.dashboardObservabilityPath()).toBe("/admin/platform/observability");
});

test("dashboard methods format metrics and preserve the six-agent row order", () => {
  const methods = loadDashboardMethods();
  const context = {
    ...methods,
    dashboardMetrics: {
      agents: [
        {
          agent_key: "pskill.runner",
          recent_run_count: 2,
          succeeded_count: 1,
          success_rate: 0.5,
          average_duration_ms: 3000,
          model_call_count: 3,
          failed_model_call_count: 1,
          model_failure_rate: 0.3333,
          tool_call_count: 2,
          failed_tool_call_count: 1,
          tool_failure_rate: 0.5
        }
      ],
      evaluations: {
        outcome_counts: { success: 1 },
        finding_status_counts: { open: 2 }
      }
    },
    formatDuration: (value) => `${value} ms`
  };

  const rows = methods.dashboardAgentRows.call(context);

  expect(rows.map((row) => row.agent_key).slice(0, 6)).toEqual([
    "pskill.builder",
    "pskill.compiler",
    "pskill.tester",
    "pskill.runner",
    "pskill.evaluator",
    "psop.governance"
  ]);
  expect(rows[3].recent_run_count).toBe(2);
  expect(rows[3].model_failure_rate).toBe(0.3333);
  expect(rows[0].model_call_count).toBe(0);
  expect(methods.dashboardAgentLabel("psop.governance")).toBe("Governance");
  expect(methods.dashboardPercent(0.6667)).toBe("67%");
  expect(methods.dashboardNumber(12345)).toBe("12,345");
  expect(methods.dashboardDuration.call(context, 3000)).toBe("3000 ms");
  expect(methods.dashboardOutcomeCount.call(context, "success")).toBe(1);
  expect(methods.dashboardFindingStatusCount.call(context, "open")).toBe(2);
});

test("dashboard page exposes agent and pending authorization drilldown links", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/dashboard.html"), "utf8");

  expect(html).toContain("dashboardAgentRunsForAgentPath(agent.agent_key)");
  expect(html).toContain("dashboardWaitingAuthorizationsForAgentPath(agent.agent_key)");
  expect(html).toContain("dashboardToolAuthorizationsPath({ status: 'pending' })");
  expect(html).toContain("dashboardGovernanceProposalsPath({ status: 'testing' })");
  expect(html).toContain("dashboardGovernanceExperimentsPath()");
  expect(html).toContain("dashboardObservabilityPath()");
  expect(html).toContain("agent.model_failure_rate");
  expect(html).toContain("agent.failed_model_call_count");
});
