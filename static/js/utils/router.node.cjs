function normalizePath(pathname) {
  if (!pathname || pathname === "/") {
    return "/";
  }

  if (pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

function resolveAdminRoute(pathname) {
  const normalized = normalizePath(pathname);
  if (normalized === "/" || normalized === "/admin" || normalized === "/admin/skills") {
    return { name: "skills-list", params: {} };
  }

  if (normalized === "/admin/tasks") {
    return { name: "tasks-list", params: {} };
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

  if (normalized === "/admin/replay") {
    return { name: "replay-list", params: {} };
  }

  const replayRunMatch = normalized.match(/^\/admin\/replay\/runs\/([^/]+)$/);
  if (replayRunMatch) {
    return { name: "run-live", params: { runId: replayRunMatch[1], view: "replay" } };
  }

  return { name: "skills-list", params: {} };
}

function buildSkillDetailPath(skillId) {
  return `/admin/skills/${skillId}`;
}

function buildTasksPath() {
  return "/admin/tasks";
}

function buildRunLivePath(runId) {
  return `/admin/runs/${runId}/live`;
}

function buildSkillRunLivePath(skillId, runId) {
  return `/admin/skills/${skillId}/runs/${runId}/live`;
}

function buildSkillDebugRunLivePath(skillId, runId) {
  return `/admin/skills/${skillId}/debug/runs/${runId}/live`;
}

function buildReplayPath(runId) {
  return `/admin/runs/${runId}/live/replay`;
}

function buildSkillReplayPath(skillId, runId) {
  return `/admin/skills/${skillId}/runs/${runId}/live/replay`;
}

function buildSkillTestScenarioPath(skillId, scenarioId) {
  return `/admin/skills/${skillId}/tests/${scenarioId}`;
}

function buildSkillTestScenarioNewPath(skillId) {
  return `/admin/skills/${skillId}/tests/new`;
}

function buildSkillTestScenarioRunReviewPath(skillId, scenarioId, scenarioRunId) {
  return `/admin/skills/${skillId}/tests/${scenarioId}/runs/${scenarioRunId}/review`;
}

function buildCompilerArtifactPath(artifactId) {
  return `/admin/compiler/artifacts/${artifactId}`;
}

function buildSkillCompilerArtifactPath(skillId, artifactId) {
  return `/admin/skills/${skillId}/compiler/artifacts/${artifactId}`;
}

module.exports = {
  normalizePath,
  resolveAdminRoute,
  buildSkillDetailPath,
  buildTasksPath,
  buildRunLivePath,
  buildSkillRunLivePath,
  buildSkillDebugRunLivePath,
  buildReplayPath,
  buildSkillReplayPath,
  buildSkillTestScenarioPath,
  buildSkillTestScenarioNewPath,
  buildSkillTestScenarioRunReviewPath,
  buildCompilerArtifactPath,
  buildSkillCompilerArtifactPath
};
