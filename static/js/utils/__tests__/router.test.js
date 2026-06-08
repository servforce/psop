const {
  normalizePath,
  resolveAdminRoute,
  buildDashboardPath,
  buildSkillDetailPath,
  buildRunLivePath,
  buildSkillRunLivePath,
  buildSkillDebugRunLivePath,
  buildReplayPath,
  buildReplayTracePath,
  buildSkillReplayPath,
  buildSkillTestScenarioPath,
  buildSkillTestScenarioNewPath,
  buildSkillTestScenarioRunReviewPath,
  buildCompilerArtifactPath,
  buildCompilerRequestPath,
  buildSkillCompilerArtifactPath,
  buildTasksPath,
  buildEvaluationReportsPath,
  buildEvaluationReportPath,
  buildEvaluationFindingsPath,
  buildGovernanceProposalsPath,
  buildGovernanceProposalPath,
  buildGovernanceExperimentsPath,
  buildToolAuthorizationsPath,
  buildPlatformAgentsPath,
  buildPlatformAgentPath,
  buildPlatformAgentRunsPath,
  buildPlatformAgentRunPath,
  buildPlatformSkillsPath,
  buildPlatformSkillPath,
  buildPlatformToolsPath,
  buildPlatformToolPath,
  buildPlatformMemoryPath,
  buildPlatformMemoryEntryPath,
  buildPlatformObservabilityPath,
  buildRunEventsPath,
  buildSkillRunEventsPath
} = require("../router.node.cjs");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROUTER_ES_MODULE_PATH = path.join(__dirname, "../router.js");
const APP_JS_PATH = path.join(__dirname, "../../app.js");

test("normalizePath handles root", () => {
  expect(normalizePath("/")).toBe("/");
});

test("normalizePath strips trailing slash", () => {
  expect(normalizePath("/docs/")).toBe("/docs");
});

test("resolveAdminRoute maps the dashboard entry routes", () => {
  expect(resolveAdminRoute("/")).toEqual({ name: "dashboard", params: {} });
  expect(resolveAdminRoute("/admin")).toEqual({ name: "dashboard", params: {} });
  expect(resolveAdminRoute("/admin/dashboard")).toEqual({ name: "dashboard", params: {} });
  expect(buildDashboardPath()).toBe("/admin/dashboard");
});

test("resolveAdminRoute maps the skills list route", () => {
  expect(resolveAdminRoute("/admin/skills")).toEqual({ name: "skills-list", params: {} });
});

test("resolveAdminRoute maps the tasks route", () => {
  expect(resolveAdminRoute("/admin/tasks")).toEqual({ name: "tasks-list", params: {} });
  expect(buildTasksPath()).toBe("/admin/tasks");
  expect(buildTasksPath({ job_type: "skill_sync", q: "job-1", status: "pending" })).toBe(
    "/admin/tasks?job_type=skill_sync&status=pending&q=job-1"
  );
});

test("resolveAdminRoute maps evaluation routes", () => {
  expect(resolveAdminRoute("/admin/evaluations")).toEqual({ name: "evaluation-reports", params: {} });
  expect(resolveAdminRoute("/admin/evaluations/findings")).toEqual({ name: "evaluation-findings", params: {} });
  expect(resolveAdminRoute("/admin/evaluations/eval-123")).toEqual({
    name: "evaluation-report",
    params: { evaluationId: "eval-123" }
  });
  expect(buildEvaluationReportsPath()).toBe("/admin/evaluations");
  expect(buildEvaluationReportsPath({ overall_outcome: "failed", run_id: "run-1" })).toBe(
    "/admin/evaluations?run_id=run-1&overall_outcome=failed"
  );
  expect(buildEvaluationReportPath("eval-123")).toBe("/admin/evaluations/eval-123");
  expect(buildEvaluationFindingsPath()).toBe("/admin/evaluations/findings");
  expect(buildEvaluationFindingsPath({ status: "open", category: "runner_issue" })).toBe(
    "/admin/evaluations/findings?status=open&category=runner_issue"
  );
});

test("resolveAdminRoute maps governance and platform authorization routes", () => {
  expect(resolveAdminRoute("/admin/governance")).toEqual({ name: "governance-proposals", params: {} });
  expect(resolveAdminRoute("/admin/governance/proposals")).toEqual({ name: "governance-proposals", params: {} });
  expect(resolveAdminRoute("/admin/governance/proposals/proposal-123")).toEqual({
    name: "governance-proposal",
    params: { proposalId: "proposal-123" }
  });
  expect(resolveAdminRoute("/admin/governance/experiments")).toEqual({ name: "governance-experiments", params: {} });
  expect(resolveAdminRoute("/admin/platform/agents")).toEqual({ name: "platform-agents", params: {} });
  expect(resolveAdminRoute("/admin/platform/agents/pskill.runner")).toEqual({
    name: "platform-agent",
    params: { agentKey: "pskill.runner" }
  });
  expect(resolveAdminRoute("/admin/platform/agent-runs")).toEqual({ name: "platform-agent-runs", params: {} });
  expect(resolveAdminRoute("/admin/platform/agent-runs/agent-run-123")).toEqual({
    name: "platform-agent-run",
    params: { agentRunId: "agent-run-123" }
  });
  expect(resolveAdminRoute("/admin/platform/skills")).toEqual({ name: "platform-skills", params: {} });
  expect(resolveAdminRoute("/admin/platform/skills/pskill-builder")).toEqual({
    name: "platform-skill",
    params: { packageName: "pskill-builder" }
  });
  expect(resolveAdminRoute("/admin/platform/tools")).toEqual({ name: "platform-tools", params: {} });
  expect(resolveAdminRoute("/admin/platform/tools/psop.memory.search")).toEqual({
    name: "platform-tool",
    params: { toolName: "psop.memory.search" }
  });
  expect(resolveAdminRoute("/admin/platform/memory")).toEqual({ name: "platform-memory", params: {} });
  expect(resolveAdminRoute("/admin/platform/memory/mem-123")).toEqual({
    name: "platform-memory-entry",
    params: { memoryId: "mem-123" }
  });
  expect(resolveAdminRoute("/admin/platform/observability")).toEqual({ name: "platform-observability", params: {} });
  expect(resolveAdminRoute("/admin/platform/tool-authorizations")).toEqual({ name: "tool-authorizations", params: {} });
  expect(buildGovernanceProposalsPath()).toBe("/admin/governance/proposals");
  expect(buildGovernanceProposalsPath({ status: "canary" })).toBe("/admin/governance/proposals?status=canary");
  expect(buildGovernanceProposalPath("proposal-123")).toBe("/admin/governance/proposals/proposal-123");
  expect(buildGovernanceExperimentsPath()).toBe("/admin/governance/experiments");
  expect(buildGovernanceExperimentsPath({ proposal_id: "proposal-1", status: "running", experiment_type: "canary" })).toBe(
    "/admin/governance/experiments?proposal_id=proposal-1&status=running&experiment_type=canary"
  );
  expect(buildGovernanceExperimentsPath({ experiment_id: "experiment-1" })).toBe(
    "/admin/governance/experiments?experiment_id=experiment-1"
  );
  expect(buildToolAuthorizationsPath()).toBe("/admin/platform/tool-authorizations");
  expect(buildToolAuthorizationsPath({ status: "pending", tool_name: "psop.memory.search" })).toBe(
    "/admin/platform/tool-authorizations?status=pending&tool_name=psop.memory.search"
  );
  expect(buildPlatformAgentsPath()).toBe("/admin/platform/agents");
  expect(buildPlatformAgentPath("pskill.runner")).toBe("/admin/platform/agents/pskill.runner");
  expect(buildPlatformAgentRunsPath()).toBe("/admin/platform/agent-runs");
  expect(buildPlatformAgentRunsPath({ agent_key: "pskill.runner", status: "waiting_tool_authorization" })).toBe(
    "/admin/platform/agent-runs?agent_key=pskill.runner&status=waiting_tool_authorization"
  );
  expect(buildPlatformAgentRunPath("agent-run-123")).toBe("/admin/platform/agent-runs/agent-run-123");
  expect(buildPlatformAgentRunPath("agent-run-123", { tab: "tools", tool_call_id: "tool-call-1" })).toBe(
    "/admin/platform/agent-runs/agent-run-123?tab=tools&tool_call_id=tool-call-1"
  );
  expect(buildPlatformAgentRunPath("agent-run-123", { tab: "model", model_call_id: "model-call-1" })).toBe(
    "/admin/platform/agent-runs/agent-run-123?tab=model&model_call_id=model-call-1"
  );
  expect(buildPlatformSkillsPath()).toBe("/admin/platform/skills");
  expect(buildPlatformSkillPath("pskill-builder")).toBe("/admin/platform/skills/pskill-builder");
  expect(buildPlatformToolsPath()).toBe("/admin/platform/tools");
  expect(buildPlatformToolPath("psop.memory.search")).toBe("/admin/platform/tools/psop.memory.search");
  expect(buildPlatformMemoryPath()).toBe("/admin/platform/memory");
  expect(buildPlatformMemoryEntryPath("mem-123")).toBe("/admin/platform/memory/mem-123");
  expect(buildPlatformObservabilityPath()).toBe("/admin/platform/observability");
});

test("router ES module exposes the closed-loop route helpers", () => {
  const source = fs.readFileSync(ROUTER_ES_MODULE_PATH, "utf8");
  const helperNames = [
    "buildEvaluationReportsPath",
    "buildEvaluationReportPath",
    "buildEvaluationFindingsPath",
    "buildGovernanceProposalsPath",
    "buildGovernanceProposalPath",
    "buildGovernanceExperimentsPath",
    "buildToolAuthorizationsPath",
    "buildPlatformAgentRunsPath",
    "buildPlatformAgentRunPath",
    "buildPlatformSkillsPath",
    "buildPlatformSkillPath",
    "buildPlatformToolsPath",
    "buildPlatformToolPath",
    "buildPlatformMemoryPath",
    "buildPlatformMemoryEntryPath",
    "buildRunEventsPath",
    "buildSkillRunEventsPath"
  ];

  for (const helperName of helperNames) {
    expect(source).toContain(`export function ${helperName}`);
  }
  for (const routeName of ["evaluation-report", "governance-proposal", "platform-agent-run", "platform-memory-entry"]) {
    expect(source).toContain(`name: "${routeName}"`);
  }
});

test("browser console helpers preserve closed-loop route filters", () => {
  const source = fs.readFileSync(APP_JS_PATH, "utf8");
  const sandbox = {
    window: {
      location: { origin: "http://localhost", port: "8000" },
      PSOPSkillKey: { generateSkillKey: (name) => String(name || "") }
    },
    document: { addEventListener: jest.fn() },
    URL,
    URLSearchParams
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);

  const helpers = sandbox.window.PSOPConsoleHelpers;
  expect(helpers.buildEvaluationReportsPath({ run_id: "run-1", overall_outcome: "failed" })).toBe(
    "/admin/evaluations?run_id=run-1&overall_outcome=failed"
  );
  expect(helpers.buildEvaluationFindingsPath({ status: "open", category: "runner_issue" })).toBe(
    "/admin/evaluations/findings?status=open&category=runner_issue"
  );
  expect(helpers.buildGovernanceProposalsPath({ status: "canary" })).toBe(
    "/admin/governance/proposals?status=canary"
  );
  expect(helpers.buildGovernanceExperimentsPath({ experiment_id: "experiment-1" })).toBe(
    "/admin/governance/experiments?experiment_id=experiment-1"
  );
});

test("resolveAdminRoute extracts skill detail params", () => {
  expect(resolveAdminRoute("/admin/skills/skill-123")).toEqual({
    name: "skill-detail",
    params: { skillId: "skill-123" }
  });
});

test("buildSkillDetailPath builds the detail location", () => {
  expect(buildSkillDetailPath("skill-123")).toBe("/admin/skills/skill-123");
});

test("resolveAdminRoute maps issue #1 runtime pages", () => {
  expect(resolveAdminRoute("/admin/compiler")).toEqual({ name: "compiler-list", params: {} });
  expect(resolveAdminRoute("/admin/compiler/artifacts/artifact-123")).toEqual({
    name: "compiler-artifact",
    params: { artifactId: "artifact-123" }
  });
  expect(resolveAdminRoute("/admin/invocations")).toEqual({ name: "invocations-list", params: {} });
  expect(resolveAdminRoute("/admin/runs/run-123/live")).toEqual({
    name: "run-live",
    params: { runId: "run-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/runs/run-123/live")).toEqual({
    name: "skill-run-live",
    params: { skillId: "skill-123", runId: "run-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/runs/run-123/live/replay")).toEqual({
    name: "skill-run-live",
    params: { skillId: "skill-123", runId: "run-123", view: "replay" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/runs/run-123/events")).toEqual({
    name: "skill-run-live",
    params: { skillId: "skill-123", runId: "run-123", view: "events" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/debug/runs/run-123/live")).toEqual({
    name: "skill-debug-live",
    params: { skillId: "skill-123", runId: "run-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/runs/run-123/replay")).toEqual({
    name: "skill-run-live",
    params: { skillId: "skill-123", runId: "run-123", view: "replay" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/tests/new")).toEqual({
    name: "skill-test-scenario-new",
    params: { skillId: "skill-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/tests/scenario-123")).toEqual({
    name: "skill-test-scenario",
    params: { skillId: "skill-123", scenarioId: "scenario-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/tests/scenario-123/runs/scenario-run-123/review")).toEqual({
    name: "skill-test-scenario-review",
    params: { skillId: "skill-123", scenarioId: "scenario-123", scenarioRunId: "scenario-run-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/compiler/artifacts/artifact-123")).toEqual({
    name: "skill-compiler-artifact",
    params: { skillId: "skill-123", artifactId: "artifact-123" }
  });
  expect(resolveAdminRoute("/admin/replay")).toEqual({ name: "replay-list", params: {} });
  expect(resolveAdminRoute("/admin/replay/runs/run-123")).toEqual({
    name: "run-live",
    params: { runId: "run-123", view: "replay" }
  });
  expect(resolveAdminRoute("/admin/replay/traces/trace-123")).toEqual({
    name: "replay-trace",
    params: { traceId: "trace-123" }
  });
  expect(resolveAdminRoute("/admin/runs/run-123/live/replay")).toEqual({
    name: "run-live",
    params: { runId: "run-123", view: "replay" }
  });
  expect(resolveAdminRoute("/admin/runs/run-123/events")).toEqual({
    name: "run-live",
    params: { runId: "run-123", view: "events" }
  });
});

test("runtime route builders create live and replay locations", () => {
  expect(buildRunLivePath("run-123")).toBe("/admin/runs/run-123/live");
  expect(buildRunEventsPath("run-123")).toBe("/admin/runs/run-123/events");
  expect(buildSkillRunLivePath("skill-123", "run-123")).toBe("/admin/skills/skill-123/runs/run-123/live");
  expect(buildSkillRunEventsPath("skill-123", "run-123")).toBe("/admin/skills/skill-123/runs/run-123/events");
  expect(buildSkillDebugRunLivePath("skill-123", "run-123")).toBe(
    "/admin/skills/skill-123/debug/runs/run-123/live"
  );
  expect(buildReplayPath("run-123")).toBe("/admin/runs/run-123/live/replay");
  expect(buildReplayPath("run-123", { event_id: "event-1" })).toBe(
    "/admin/runs/run-123/live/replay?event_id=event-1"
  );
  expect(buildReplayPath("run-123", { trace_id: "trace-1" })).toBe(
    "/admin/runs/run-123/live/replay?trace_id=trace-1"
  );
  expect(buildReplayTracePath("trace-1")).toBe("/admin/replay/traces/trace-1");
  expect(buildSkillReplayPath("skill-123", "run-123")).toBe("/admin/skills/skill-123/runs/run-123/live/replay");
  expect(buildSkillTestScenarioNewPath("skill-123")).toBe("/admin/skills/skill-123/tests/new");
  expect(buildSkillTestScenarioPath("skill-123", "scenario-123")).toBe("/admin/skills/skill-123/tests/scenario-123");
  expect(buildSkillTestScenarioRunReviewPath("skill-123", "scenario-123", "scenario-run-123")).toBe(
    "/admin/skills/skill-123/tests/scenario-123/runs/scenario-run-123/review"
  );
  expect(buildCompilerArtifactPath("artifact-123")).toBe("/admin/compiler/artifacts/artifact-123");
  expect(buildCompilerRequestPath("compile-123")).toBe("/admin/compiler?compile_request_id=compile-123");
  expect(buildCompilerRequestPath("")).toBe("/admin/compiler");
  expect(buildSkillCompilerArtifactPath("skill-123", "artifact-123")).toBe(
    "/admin/skills/skill-123/compiler/artifacts/artifact-123"
  );
});
