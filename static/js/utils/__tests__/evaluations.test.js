const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadEvaluationMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/evaluations.js"), "utf8");
  const sandbox = {
    window: {},
    URLSearchParams,
    Number,
    Math,
    String,
    Array,
    Object
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsoleEvaluationMethods;
}

test("evaluation methods build finding filters and labels", () => {
  const methods = loadEvaluationMethods();
  const context = {
    ...methods,
    evaluationFindingFilters: {
      status: "open",
      category: "runner_issue",
      severity: "high",
      run_id: "run-123",
      pskill_definition_id: ""
    }
  };

  const query = methods.evaluationFindingsQueryString.call(context);

  expect(query).toContain("status=open");
  expect(query).toContain("category=runner_issue");
  expect(query).toContain("severity=high");
  expect(query).toContain("run_id=run-123");
  expect(query).not.toContain("pskill_definition_id=");
  expect(methods.findingCategoryLabel("runner_issue")).toBe("运行智能体");
  expect(methods.findingSeverityLabel("critical")).toBe("严重");
  expect(methods.findingStatusLabel("converted_to_proposal")).toBe("已转提案");
  expect(methods.evaluationOutcomeLabel("completed_with_issues")).toBe("完成但有问题");
  expect(methods.evaluationScoreBarWidth(105)).toBe("100%");
});

test("evaluation methods update finding status in list and current report", async () => {
  const methods = loadEvaluationMethods();
  const updated = { id: "finding-1", status: "accepted", category: "runner_issue" };
  const context = {
    ...methods,
    busy: { evaluationFindingUpdate: false },
    evaluationFindings: [{ id: "finding-1", status: "open" }, { id: "finding-2", status: "open" }],
    currentEvaluation: {
      findings: [{ id: "finding-1", status: "open" }]
    },
    apiRequest: jest.fn(async () => updated),
    showNotice: jest.fn()
  };

  await methods.updateEvaluationFindingStatus.call(context, { id: "finding-1" }, "accepted");

  expect(context.apiRequest).toHaveBeenCalledWith("/evaluations/findings/finding-1", {
    method: "PATCH",
    body: JSON.stringify({ status: "accepted" })
  });
  expect(context.evaluationFindings[0]).toBe(updated);
  expect(context.evaluationFindings[1].status).toBe("open");
  expect(context.currentEvaluation.findings[0]).toBe(updated);
  expect(context.busy.evaluationFindingUpdate).toBe(false);
});

test("evaluation methods create governance proposal from finding and navigate", async () => {
  const methods = loadEvaluationMethods();
  const finding = { id: "finding-1", status: "open", category: "runner_issue" };
  const context = {
    ...methods,
    busy: { evaluationFindingUpdate: false },
    evaluationFindings: [finding],
    currentEvaluation: { findings: [finding] },
    apiRequest: jest.fn(async () => ({ id: "proposal-1" })),
    showNotice: jest.fn(),
    navigate: jest.fn(),
    governanceProposalPath: (proposalId) => `/admin/governance/proposals/${proposalId}`
  };

  await methods.createProposalFromEvaluationFinding.call(context, finding);

  expect(context.apiRequest).toHaveBeenCalledWith("/evaluations/findings/finding-1/create-proposal", {
    method: "POST"
  });
  expect(context.evaluationFindings[0].status).toBe("converted_to_proposal");
  expect(context.currentEvaluation.findings[0].status).toBe("converted_to_proposal");
  expect(context.navigate).toHaveBeenCalledWith("/admin/governance/proposals/proposal-1");
  expect(context.busy.evaluationFindingUpdate).toBe(false);
});
