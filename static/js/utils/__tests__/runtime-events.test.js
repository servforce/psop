const { mergeById, mergeBySeq, shouldReplaceTaskStatus } = require("../runtime-events.node.cjs");

test("mergeBySeq sorts and replaces existing sequence events", () => {
  const merged = mergeBySeq(
    [{ seq_no: 2, value: "old" }],
    [
      { seq_no: 1, value: "first" },
      { seq_no: 2, value: "new" }
    ]
  );

  expect(merged).toEqual([
    { seq_no: 1, value: "first" },
    { seq_no: 2, value: "new" }
  ]);
});

test("mergeById merges objects by stable id", () => {
  const merged = mergeById([{ id: "a", status: "pending" }], [{ id: "a", status: "active" }, { id: "b" }]);

  expect(merged).toEqual([{ id: "a", status: "active" }, { id: "b" }]);
});

test("task status accepts newer snapshots and same-snapshot lifecycle updates", () => {
  const current = { run_id: "run-1", snapshot_seq: 4, updated_at: "2026-07-14T10:00:00Z" };

  expect(shouldReplaceTaskStatus(current, { ...current, snapshot_seq: 5 })).toBe(true);
  expect(shouldReplaceTaskStatus(current, { ...current, snapshot_seq: 3 })).toBe(false);
  expect(
    shouldReplaceTaskStatus(current, { ...current, updated_at: "2026-07-14T10:00:01Z", run_status: "succeeded" })
  ).toBe(true);
  expect(shouldReplaceTaskStatus(current, { ...current })).toBe(false);
  expect(shouldReplaceTaskStatus(current, { ...current, updated_at: "2026-07-14T09:59:59Z" })).toBe(false);
  expect(shouldReplaceTaskStatus(current, { ...current, run_id: "run-2" })).toBe(true);
});
