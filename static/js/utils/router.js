export function normalizePath(pathname) {
  if (!pathname || pathname === "/") {
    return "/";
  }

  if (pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

export function resolveAdminRoute(pathname) {
  const normalized = normalizePath(pathname);
  if (normalized === "/" || normalized === "/admin" || normalized === "/admin/dashboard") {
    return { name: "dashboard", params: {} };
  }

  if (normalized === "/admin/skills") {
    return { name: "skills-list", params: {} };
  }

  if (normalized === "/admin/tasks") {
    return { name: "tasks-list", params: {} };
  }

  if (normalized === "/admin/evaluations") {
    return { name: "evaluation-reports", params: {} };
  }

  if (normalized === "/admin/evaluations/findings") {
    return { name: "evaluation-findings", params: {} };
  }

  if (normalized === "/admin/governance" || normalized === "/admin/governance/proposals") {
    return { name: "governance-proposals", params: {} };
  }

  const governanceProposalMatch = normalized.match(/^\/admin\/governance\/proposals\/([^/]+)$/);
  if (governanceProposalMatch) {
    return {
      name: "governance-proposal",
      params: { proposalId: governanceProposalMatch[1] }
    };
  }

  if (normalized === "/admin/governance/experiments") {
    return { name: "governance-experiments", params: {} };
  }

  if (normalized === "/admin/platform/agents") {
    return { name: "platform-agents", params: {} };
  }

  const platformAgentMatch = normalized.match(/^\/admin\/platform\/agents\/([^/]+)$/);
  if (platformAgentMatch) {
    return {
      name: "platform-agent",
      params: { agentKey: platformAgentMatch[1] }
    };
  }

  if (normalized === "/admin/platform/agent-runs") {
    return { name: "platform-agent-runs", params: {} };
  }

  const platformAgentRunMatch = normalized.match(/^\/admin\/platform\/agent-runs\/([^/]+)$/);
  if (platformAgentRunMatch) {
    return {
      name: "platform-agent-run",
      params: { agentRunId: platformAgentRunMatch[1] }
    };
  }

  if (normalized === "/admin/platform/skills") {
    return { name: "platform-skills", params: {} };
  }

  const platformSkillMatch = normalized.match(/^\/admin\/platform\/skills\/([^/]+)$/);
  if (platformSkillMatch) {
    return {
      name: "platform-skill",
      params: { packageName: platformSkillMatch[1] }
    };
  }

  if (normalized === "/admin/platform/tools") {
    return { name: "platform-tools", params: {} };
  }

  const platformToolMatch = normalized.match(/^\/admin\/platform\/tools\/([^/]+)$/);
  if (platformToolMatch) {
    return {
      name: "platform-tool",
      params: { toolName: platformToolMatch[1] }
    };
  }

  if (normalized === "/admin/platform/memory") {
    return { name: "platform-memory", params: {} };
  }

  if (normalized === "/admin/platform/observability") {
    return { name: "platform-observability", params: {} };
  }

  const platformMemoryMatch = normalized.match(/^\/admin\/platform\/memory\/([^/]+)$/);
  if (platformMemoryMatch) {
    return {
      name: "platform-memory-entry",
      params: { memoryId: platformMemoryMatch[1] }
    };
  }

  if (normalized === "/admin/platform/tool-authorizations") {
    return { name: "tool-authorizations", params: {} };
  }

  const evaluationReportMatch = normalized.match(/^\/admin\/evaluations\/([^/]+)$/);
  if (evaluationReportMatch) {
    return {
      name: "evaluation-report",
      params: { evaluationId: evaluationReportMatch[1] }
    };
  }

  const detailMatch = normalized.match(/^\/admin\/skills\/([^/]+)$/);
  if (detailMatch) {
    return {
      name: "skill-detail",
      params: { skillId: detailMatch[1] }
    };
  }

  const skillRunLiveMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/live$/);
  if (skillRunLiveMatch) {
    return {
      name: "skill-run-live",
      params: { skillId: skillRunLiveMatch[1], runId: skillRunLiveMatch[2] }
    };
  }

  const skillRunReplayMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/live\/replay$/);
  if (skillRunReplayMatch) {
    return {
      name: "skill-run-live",
      params: { skillId: skillRunReplayMatch[1], runId: skillRunReplayMatch[2], view: "replay" }
    };
  }

  const skillRunEventsMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/events$/);
  if (skillRunEventsMatch) {
    return {
      name: "skill-run-live",
      params: { skillId: skillRunEventsMatch[1], runId: skillRunEventsMatch[2], view: "events" }
    };
  }

  const skillDebugRunLiveMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/debug\/runs\/([^/]+)\/live$/);
  if (skillDebugRunLiveMatch) {
    return {
      name: "skill-debug-live",
      params: { skillId: skillDebugRunLiveMatch[1], runId: skillDebugRunLiveMatch[2] }
    };
  }

  const skillReplayRunMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/replay$/);
  if (skillReplayRunMatch) {
    return {
      name: "skill-run-live",
      params: { skillId: skillReplayRunMatch[1], runId: skillReplayRunMatch[2], view: "replay" }
    };
  }

  const skillTestRunReviewMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/([^/]+)\/runs\/([^/]+)\/review$/);
  if (skillTestRunReviewMatch) {
    return {
      name: "skill-test-scenario-review",
      params: { skillId: skillTestRunReviewMatch[1], scenarioId: skillTestRunReviewMatch[2], scenarioRunId: skillTestRunReviewMatch[3] }
    };
  }

  const skillTestNewMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/new$/);
  if (skillTestNewMatch) {
    return {
      name: "skill-test-scenario-new",
      params: { skillId: skillTestNewMatch[1] }
    };
  }

  const skillTestScenarioMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/([^/]+)$/);
  if (skillTestScenarioMatch) {
    return {
      name: "skill-test-scenario",
      params: { skillId: skillTestScenarioMatch[1], scenarioId: skillTestScenarioMatch[2] }
    };
  }

  const skillCompilerArtifactMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/compiler\/artifacts\/([^/]+)$/);
  if (skillCompilerArtifactMatch) {
    return {
      name: "skill-compiler-artifact",
      params: { skillId: skillCompilerArtifactMatch[1], artifactId: skillCompilerArtifactMatch[2] }
    };
  }

  if (normalized === "/admin/compiler") {
    return { name: "compiler-list", params: {} };
  }

  const compilerArtifactMatch = normalized.match(/^\/admin\/compiler\/artifacts\/([^/]+)$/);
  if (compilerArtifactMatch) {
    return {
      name: "compiler-artifact",
      params: { artifactId: compilerArtifactMatch[1] }
    };
  }

  if (normalized === "/admin/invocations") {
    return { name: "invocations-list", params: {} };
  }

  const runLiveMatch = normalized.match(/^\/admin\/runs\/([^/]+)\/live$/);
  if (runLiveMatch) {
    return { name: "run-live", params: { runId: runLiveMatch[1] } };
  }

  const runReplayMatch = normalized.match(/^\/admin\/runs\/([^/]+)\/live\/replay$/);
  if (runReplayMatch) {
    return { name: "run-live", params: { runId: runReplayMatch[1], view: "replay" } };
  }

  const runEventsMatch = normalized.match(/^\/admin\/runs\/([^/]+)\/events$/);
  if (runEventsMatch) {
    return { name: "run-live", params: { runId: runEventsMatch[1], view: "events" } };
  }

  if (normalized === "/admin/replay") {
    return { name: "replay-list", params: {} };
  }

  const replayRunMatch = normalized.match(/^\/admin\/replay\/runs\/([^/]+)$/);
  if (replayRunMatch) {
    return { name: "run-live", params: { runId: replayRunMatch[1], view: "replay" } };
  }

  const replayTraceMatch = normalized.match(/^\/admin\/replay\/traces\/([^/]+)$/);
  if (replayTraceMatch) {
    return { name: "replay-trace", params: { traceId: replayTraceMatch[1] } };
  }

  return { name: "skills-list", params: {} };
}

export function buildSkillDetailPath(skillId) {
  return `/admin/skills/${skillId}`;
}

export function buildDashboardPath() {
  return "/admin/dashboard";
}

export function buildTasksPath(filters = {}) {
  const params = new URLSearchParams();
  for (const key of ["job_type", "status", "q", "created_from", "created_to"]) {
    const value = String(filters?.[key] || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `/admin/tasks?${query}` : "/admin/tasks";
}

function buildFilteredPath(basePath, filters = {}, keys = []) {
  const params = new URLSearchParams();
  for (const key of keys) {
    const value = String(filters?.[key] || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `${basePath}?${query}` : basePath;
}

export function buildEvaluationReportsPath(filters = {}) {
  return buildFilteredPath(
    "/admin/evaluations",
    filters,
    ["run_id", "pskill_definition_id", "overall_outcome"]
  );
}

export function buildEvaluationReportPath(evaluationId) {
  return `/admin/evaluations/${evaluationId}`;
}

export function buildEvaluationFindingsPath(filters = {}) {
  return buildFilteredPath(
    "/admin/evaluations/findings",
    filters,
    ["status", "category", "severity", "run_id", "pskill_definition_id"]
  );
}

export function buildGovernanceProposalsPath(filters = {}) {
  return buildFilteredPath("/admin/governance/proposals", filters, ["status"]);
}

export function buildGovernanceProposalPath(proposalId) {
  return `/admin/governance/proposals/${proposalId}`;
}

export function buildGovernanceExperimentsPath(filters = {}) {
  return buildFilteredPath(
    "/admin/governance/experiments",
    filters,
    ["experiment_id", "proposal_id", "status", "experiment_type"]
  );
}

export function buildToolAuthorizationsPath(filters = {}) {
  const params = new URLSearchParams();
  for (const key of [
    "status",
    "tool_name",
    "agent_run_id",
    "run_id",
    "agent_key",
    "proposal_id",
    "source_run_id",
    "source_evaluation_id",
    "source_finding_id"
  ]) {
    const value = String(filters?.[key] || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `/admin/platform/tool-authorizations?${query}` : "/admin/platform/tool-authorizations";
}

export function buildPlatformAgentsPath() {
  return "/admin/platform/agents";
}

export function buildPlatformAgentPath(agentKey) {
  return `/admin/platform/agents/${agentKey}`;
}

export function buildPlatformAgentRunsPath(filters = {}) {
  const params = new URLSearchParams();
  for (const key of ["agent_key", "status", "owner_type", "owner_id"]) {
    const value = String(filters?.[key] || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `/admin/platform/agent-runs?${query}` : "/admin/platform/agent-runs";
}

export function buildPlatformAgentRunPath(agentRunId, focus = {}) {
  const params = new URLSearchParams();
  for (const key of ["tab", "event_id", "model_call_id", "tool_call_id", "authorization_id"]) {
    const value = String(focus?.[key] || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `/admin/platform/agent-runs/${agentRunId}?${query}` : `/admin/platform/agent-runs/${agentRunId}`;
}

export function buildPlatformSkillsPath() {
  return "/admin/platform/skills";
}

export function buildPlatformSkillPath(packageName) {
  return `/admin/platform/skills/${packageName}`;
}

export function buildPlatformToolsPath() {
  return "/admin/platform/tools";
}

export function buildPlatformToolPath(toolName) {
  return `/admin/platform/tools/${toolName}`;
}

export function buildPlatformMemoryPath() {
  return "/admin/platform/memory";
}

export function buildPlatformMemoryEntryPath(memoryId) {
  return `/admin/platform/memory/${memoryId}`;
}

export function buildPlatformObservabilityPath() {
  return "/admin/platform/observability";
}

export function buildRunLivePath(runId) {
  return `/admin/runs/${runId}/live`;
}

export function buildRunEventsPath(runId) {
  return `/admin/runs/${runId}/events`;
}

export function buildSkillRunLivePath(skillId, runId) {
  return `/admin/skills/${skillId}/runs/${runId}/live`;
}

export function buildSkillRunEventsPath(skillId, runId) {
  return `/admin/skills/${skillId}/runs/${runId}/events`;
}

export function buildSkillDebugRunLivePath(skillId, runId) {
  return `/admin/skills/${skillId}/debug/runs/${runId}/live`;
}

export function buildReplayPath(runId, focus = {}) {
  const params = new URLSearchParams();
  for (const key of ["event_id", "trace_id", "seq_no", "snapshot_seq"]) {
    const value = String(focus?.[key] || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `/admin/runs/${runId}/live/replay?${query}` : `/admin/runs/${runId}/live/replay`;
}

export function buildReplayTracePath(traceId) {
  return `/admin/replay/traces/${traceId}`;
}

export function buildSkillReplayPath(skillId, runId) {
  return `/admin/skills/${skillId}/runs/${runId}/live/replay`;
}

export function buildSkillTestScenarioPath(skillId, scenarioId) {
  return `/admin/skills/${skillId}/tests/${scenarioId}`;
}

export function buildSkillTestScenarioNewPath(skillId) {
  return `/admin/skills/${skillId}/tests/new`;
}

export function buildSkillTestScenarioRunReviewPath(skillId, scenarioId, scenarioRunId) {
  return `/admin/skills/${skillId}/tests/${scenarioId}/runs/${scenarioRunId}/review`;
}

export function buildCompilerArtifactPath(artifactId) {
  return `/admin/compiler/artifacts/${artifactId}`;
}

export function buildCompilerRequestPath(compileRequestId) {
  const params = new URLSearchParams();
  const normalized = String(compileRequestId || "").trim();
  if (normalized) {
    params.set("compile_request_id", normalized);
  }
  const query = params.toString();
  return query ? `/admin/compiler?${query}` : "/admin/compiler";
}

export function buildSkillCompilerArtifactPath(skillId, artifactId) {
  return `/admin/skills/${skillId}/compiler/artifacts/${artifactId}`;
}
