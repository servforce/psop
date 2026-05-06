const { mergeById, mergeBySeq } = require("../runtime-events.node.cjs");

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
