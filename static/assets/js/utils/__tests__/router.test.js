const { normalizePath, isKnownScaffoldRoute, resolveScaffoldRoute } = require("../router.node.cjs");

test("normalizePath handles root", () => {
  expect(normalizePath("/")).toBe("/");
});

test("normalizePath strips trailing slash", () => {
  expect(normalizePath("/docs/")).toBe("/docs");
});

test("isKnownScaffoldRoute recognizes preserved routes", () => {
  expect(isKnownScaffoldRoute("/docs")).toBe(true);
});

test("resolveScaffoldRoute falls back to root", () => {
  expect(resolveScaffoldRoute("/legacy/business/page")).toBe("/");
});
