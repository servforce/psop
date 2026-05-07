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
  if (normalized === "/" || normalized === "/admin" || normalized === "/admin/skills") {
    return { name: "skills-list", params: {} };
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

  const skillReplayRunMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/replay$/);
  if (skillReplayRunMatch) {
    return {
      name: "skill-replay-detail",
      params: { skillId: skillReplayRunMatch[1], runId: skillReplayRunMatch[2] }
    };
  }

  const skillTestRunLiveMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/([^/]+)\/runs\/([^/]+)\/live$/);
  if (skillTestRunLiveMatch) {
    return {
      name: "skill-test-live",
      params: { skillId: skillTestRunLiveMatch[1], caseId: skillTestRunLiveMatch[2], testRunId: skillTestRunLiveMatch[3] }
    };
  }

  const skillTestNewMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/new$/);
  if (skillTestNewMatch) {
    return {
      name: "skill-test-new",
      params: { skillId: skillTestNewMatch[1] }
    };
  }

  const skillTestCaseMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/([^/]+)$/);
  if (skillTestCaseMatch) {
    return {
      name: "skill-test-case",
      params: { skillId: skillTestCaseMatch[1], caseId: skillTestCaseMatch[2] }
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

  if (normalized === "/admin/replay") {
    return { name: "replay-list", params: {} };
  }

  const replayRunMatch = normalized.match(/^\/admin\/replay\/runs\/([^/]+)$/);
  if (replayRunMatch) {
    return { name: "replay-detail", params: { runId: replayRunMatch[1] } };
  }

  return { name: "skills-list", params: {} };
}

export function buildSkillDetailPath(skillId) {
  return `/admin/skills/${skillId}`;
}

export function buildRunLivePath(runId) {
  return `/admin/runs/${runId}/live`;
}

export function buildSkillRunLivePath(skillId, runId) {
  return `/admin/skills/${skillId}/runs/${runId}/live`;
}

export function buildReplayPath(runId) {
  return `/admin/replay/runs/${runId}`;
}

export function buildSkillReplayPath(skillId, runId) {
  return `/admin/skills/${skillId}/runs/${runId}/replay`;
}

export function buildSkillTestCasePath(skillId, caseId) {
  return `/admin/skills/${skillId}/tests/${caseId}`;
}

export function buildSkillTestCaseNewPath(skillId) {
  return `/admin/skills/${skillId}/tests/new`;
}

export function buildSkillTestRunLivePath(skillId, caseId, testRunId) {
  return `/admin/skills/${skillId}/tests/${caseId}/runs/${testRunId}/live`;
}

export function buildCompilerArtifactPath(artifactId) {
  return `/admin/compiler/artifacts/${artifactId}`;
}
