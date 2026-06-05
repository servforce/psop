const {
  normalizePath,
  resolveAdminRoute,
  buildDashboardPath,
  buildSkillDetailPath,
  buildRunLivePath,
  buildSkillRunLivePath,
  buildSkillDebugRunLivePath,
  buildReplayPath,
  buildSkillReplayPath,
  buildSkillTestScenarioPath,
  buildSkillTestScenarioNewPath,
  buildSkillTestScenarioRunReviewPath,
  buildCompilerArtifactPath,
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
  buildPlatformObservabilityPath
} = require("../router.node.cjs");

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
});

test("resolveAdminRoute maps evaluation routes", () => {
  expect(resolveAdminRoute("/admin/evaluations")).toEqual({ name: "evaluation-reports", params: {} });
  expect(resolveAdminRoute("/admin/evaluations/findings")).toEqual({ name: "evaluation-findings", params: {} });
  expect(resolveAdminRoute("/admin/evaluations/eval-123")).toEqual({
    name: "evaluation-report",
    params: { evaluationId: "eval-123" }
  });
  expect(buildEvaluationReportsPath()).toBe("/admin/evaluations");
  expect(buildEvaluationReportPath("eval-123")).toBe("/admin/evaluations/eval-123");
  expect(buildEvaluationFindingsPath()).toBe("/admin/evaluations/findings");
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
  expect(buildGovernanceProposalPath("proposal-123")).toBe("/admin/governance/proposals/proposal-123");
  expect(buildGovernanceExperimentsPath()).toBe("/admin/governance/experiments");
  expect(buildToolAuthorizationsPath()).toBe("/admin/platform/tool-authorizations");
  expect(buildPlatformAgentsPath()).toBe("/admin/platform/agents");
  expect(buildPlatformAgentPath("pskill.runner")).toBe("/admin/platform/agents/pskill.runner");
  expect(buildPlatformAgentRunsPath()).toBe("/admin/platform/agent-runs");
  expect(buildPlatformAgentRunPath("agent-run-123")).toBe("/admin/platform/agent-runs/agent-run-123");
  expect(buildPlatformSkillsPath()).toBe("/admin/platform/skills");
  expect(buildPlatformSkillPath("pskill-builder")).toBe("/admin/platform/skills/pskill-builder");
  expect(buildPlatformToolsPath()).toBe("/admin/platform/tools");
  expect(buildPlatformToolPath("psop.memory.search")).toBe("/admin/platform/tools/psop.memory.search");
  expect(buildPlatformMemoryPath()).toBe("/admin/platform/memory");
  expect(buildPlatformMemoryEntryPath("mem-123")).toBe("/admin/platform/memory/mem-123");
  expect(buildPlatformObservabilityPath()).toBe("/admin/platform/observability");
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
  expect(resolveAdminRoute("/admin/runs/run-123/live/replay")).toEqual({
    name: "run-live",
    params: { runId: "run-123", view: "replay" }
  });
});

test("runtime route builders create live and replay locations", () => {
  expect(buildRunLivePath("run-123")).toBe("/admin/runs/run-123/live");
  expect(buildSkillRunLivePath("skill-123", "run-123")).toBe("/admin/skills/skill-123/runs/run-123/live");
  expect(buildSkillDebugRunLivePath("skill-123", "run-123")).toBe(
    "/admin/skills/skill-123/debug/runs/run-123/live"
  );
  expect(buildReplayPath("run-123")).toBe("/admin/runs/run-123/live/replay");
  expect(buildSkillReplayPath("skill-123", "run-123")).toBe("/admin/skills/skill-123/runs/run-123/live/replay");
  expect(buildSkillTestScenarioNewPath("skill-123")).toBe("/admin/skills/skill-123/tests/new");
  expect(buildSkillTestScenarioPath("skill-123", "scenario-123")).toBe("/admin/skills/skill-123/tests/scenario-123");
  expect(buildSkillTestScenarioRunReviewPath("skill-123", "scenario-123", "scenario-run-123")).toBe(
    "/admin/skills/skill-123/tests/scenario-123/runs/scenario-run-123/review"
  );
  expect(buildCompilerArtifactPath("artifact-123")).toBe("/admin/compiler/artifacts/artifact-123");
  expect(buildSkillCompilerArtifactPath("skill-123", "artifact-123")).toBe(
    "/admin/skills/skill-123/compiler/artifacts/artifact-123"
  );
});
