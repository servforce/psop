const {
  normalizePath,
  resolveAdminRoute,
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
  buildTasksPath
} = require("../router.node.cjs");

test("normalizePath handles root", () => {
  expect(normalizePath("/")).toBe("/");
});

test("normalizePath strips trailing slash", () => {
  expect(normalizePath("/docs/")).toBe("/docs");
});

test("resolveAdminRoute maps the skills list route", () => {
  expect(resolveAdminRoute("/admin/skills")).toEqual({ name: "skills-list", params: {} });
});

test("resolveAdminRoute maps the tasks route", () => {
  expect(resolveAdminRoute("/admin/tasks")).toEqual({ name: "tasks-list", params: {} });
  expect(buildTasksPath()).toBe("/admin/tasks");
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
