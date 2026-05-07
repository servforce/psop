const {
  normalizePath,
  resolveAdminRoute,
  buildSkillDetailPath,
  buildRunLivePath,
  buildSkillRunLivePath,
  buildReplayPath,
  buildSkillReplayPath,
  buildSkillTestCasePath,
  buildSkillTestCaseNewPath,
  buildSkillTestRunLivePath,
  buildCompilerArtifactPath
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
  expect(resolveAdminRoute("/admin/skills/skill-123/runs/run-123/replay")).toEqual({
    name: "skill-replay-detail",
    params: { skillId: "skill-123", runId: "run-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/tests/new")).toEqual({
    name: "skill-test-new",
    params: { skillId: "skill-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/tests/case-123")).toEqual({
    name: "skill-test-case",
    params: { skillId: "skill-123", caseId: "case-123" }
  });
  expect(resolveAdminRoute("/admin/skills/skill-123/tests/case-123/runs/test-run-123/live")).toEqual({
    name: "skill-test-live",
    params: { skillId: "skill-123", caseId: "case-123", testRunId: "test-run-123" }
  });
  expect(resolveAdminRoute("/admin/replay")).toEqual({ name: "replay-list", params: {} });
  expect(resolveAdminRoute("/admin/replay/runs/run-123")).toEqual({
    name: "replay-detail",
    params: { runId: "run-123" }
  });
});

test("runtime route builders create live and replay locations", () => {
  expect(buildRunLivePath("run-123")).toBe("/admin/runs/run-123/live");
  expect(buildSkillRunLivePath("skill-123", "run-123")).toBe("/admin/skills/skill-123/runs/run-123/live");
  expect(buildReplayPath("run-123")).toBe("/admin/replay/runs/run-123");
  expect(buildSkillReplayPath("skill-123", "run-123")).toBe("/admin/skills/skill-123/runs/run-123/replay");
  expect(buildSkillTestCaseNewPath("skill-123")).toBe("/admin/skills/skill-123/tests/new");
  expect(buildSkillTestCasePath("skill-123", "case-123")).toBe("/admin/skills/skill-123/tests/case-123");
  expect(buildSkillTestRunLivePath("skill-123", "case-123", "test-run-123")).toBe(
    "/admin/skills/skill-123/tests/case-123/runs/test-run-123/live"
  );
  expect(buildCompilerArtifactPath("artifact-123")).toBe("/admin/compiler/artifacts/artifact-123");
});
