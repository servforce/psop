const { normalizePath, resolveAdminRoute, buildSkillDetailPath } = require("../router.node.cjs");

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
