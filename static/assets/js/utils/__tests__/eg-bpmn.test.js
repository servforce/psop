const { buildBpmnXml, buildEgBpmnViewModel, escapeXml } = require("../eg-bpmn.js");

function artifact(overrides = {}) {
  return {
    nodes: [
      { id: "start", kind: "start", actor: { name: "runtime.start" }, guard: { phase_is: "start" }, merge: [] },
      { id: "collect_input", kind: "input", actor: { name: "runtime.input" }, guard: { phase_is: "input" }, merge: [] },
      { id: "draft<answer>", kind: "llm", actor: { name: "agent.llm" }, guard: { phase_is: "draft" }, merge: [] },
      { id: "finish", kind: "terminal", actor: { name: "runtime.terminal" }, guard: { phase_is: "terminal" }, merge: [] }
    ],
    dependency_graph_for_view: {
      edges: [
        { from: "start", to: "collect_input" },
        { source: "collect_input", target: "draft<answer>" },
        { source_id: "draft<answer>", target_id: "finish" }
      ]
    },
    runtime_contract: {
      workflow_steps: [
        {
          id: "draft<answer>",
          title: "Draft <Answer>",
          goal: "Produce a response",
          source_evidence: "SKILL.md"
        }
      ]
    },
    ...overrides
  };
}

test("buildEgBpmnViewModel reads standard and compatible edge fields", () => {
  const viewModel = buildEgBpmnViewModel(artifact());

  expect(viewModel.nodes).toHaveLength(4);
  expect(viewModel.edges.map((edge) => `${edge.from}->${edge.to}`)).toEqual([
    "start->collect_input",
    "collect_input->draft<answer>",
    "draft<answer>->finish"
  ]);
  expect(viewModel.nodeIdToBpmnId["draft<answer>"]).toMatch(/^EGNode_/);
});

test("buildEgBpmnViewModel falls back to sequential edges", () => {
  const viewModel = buildEgBpmnViewModel(artifact({ dependency_graph_for_view: {} }));

  expect(viewModel.edges.map((edge) => `${edge.from}->${edge.to}`)).toEqual([
    "start->collect_input",
    "collect_input->draft<answer>",
    "draft<answer>->finish"
  ]);
});

test("buildBpmnXml escapes XML and maps node kinds to BPMN elements", () => {
  const { xml } = buildBpmnXml(artifact());

  expect(xml).toContain("<bpmn:startEvent");
  expect(xml).toContain("<bpmn:userTask");
  expect(xml).toContain("<bpmn:serviceTask");
  expect(xml).toContain("<bpmn:endEvent");
  expect(xml).toContain("Draft &lt;Answer&gt;");
});

test("escapeXml escapes XML control characters", () => {
  expect(escapeXml(`A&B<"'>`)).toBe("A&amp;B&lt;&quot;&apos;&gt;");
});
