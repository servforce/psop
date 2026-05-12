const { generateSkillKey } = require("../skill-key.node.cjs");

test("generateSkillKey creates a readable URL-safe key from an English name", () => {
  expect(generateSkillKey("Equipment Diagnosis", "abc123")).toBe("equipment-diagnosis-abc123");
});

test("generateSkillKey falls back for names without URL-safe characters", () => {
  expect(generateSkillKey("设备诊断", "abc123")).toBe("skill-abc123");
});

test("generateSkillKey keeps the key within backend length limits", () => {
  const key = generateSkillKey("a".repeat(200), "abc123");

  expect(key).toHaveLength(120);
  expect(key).toMatch(/^[a-z0-9][a-z0-9-]*$/);
});
