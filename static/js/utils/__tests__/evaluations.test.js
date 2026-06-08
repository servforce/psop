const fs = require("fs");
const path = require("path");
const vm = require("vm");

function createFakeWebSocketClass() {
  const instances = [];

  class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(url) {
      this.url = url;
      this.readyState = FakeWebSocket.CONNECTING;
      this.listeners = {};
      instances.push(this);
    }

    addEventListener(type, handler) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(handler);
    }

    close() {
      this.readyState = FakeWebSocket.CLOSED;
      this.emit("close", {});
    }

    open() {
      this.readyState = FakeWebSocket.OPEN;
      this.emit("open", {});
    }

    message(payload) {
      this.emit("message", { data: JSON.stringify(payload) });
    }

    emit(type, event) {
      for (const handler of this.listeners[type] || []) {
        handler(event);
      }
    }
  }

  FakeWebSocket.instances = instances;
  return FakeWebSocket;
}

function loadEvaluationHarness() {
  const FakeWebSocket = createFakeWebSocketClass();
  const code = fs.readFileSync(path.join(__dirname, "../../app/evaluations.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        resolveWsUrl: (_apiBaseUrl, pathname) => `ws://localhost${pathname}`,
        buildReplayPath: (runId, focus = {}) => {
          const params = new URLSearchParams();
          for (const key of ["event_id", "seq_no", "snapshot_seq"]) {
            const value = String(focus?.[key] || "").trim();
            if (value) {
              params.set(key, value);
            }
          }
          const query = params.toString();
          return query ? `/admin/runs/${runId}/live/replay?${query}` : `/admin/runs/${runId}/live/replay`;
        }
      }
    },
    WebSocket: FakeWebSocket,
    URLSearchParams,
    JSON,
    Number,
    Math,
    String,
    Array,
    Object
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return { methods: sandbox.window.PSOPConsoleEvaluationMethods, FakeWebSocket };
}

function loadEvaluationMethods() {
  return loadEvaluationHarness().methods;
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

test("evaluation finding evidence refs build run replay deep links", () => {
  const methods = loadEvaluationMethods();
  const context = {
    ...methods,
    currentEvaluation: { id: "evaluation-1", run_id: "run-current" },
    navigate: jest.fn(),
    showNotice: jest.fn()
  };
  const findingWithRun = {
    id: "finding-1",
    run_id: "run-1",
    evidence_refs: [{ kind: "run_trace", id: "trace-1", event_type: "runtime.failed" }]
  };
  const findingWithoutRun = {
    id: "finding-2",
    evidence_refs: [{ kind: "run_event", seq_no: 4, event_kind: "terminal.text.input.v1" }]
  };

  expect(methods.evaluationRunReplayPath({ run_id: "run-1" })).toBe("/admin/runs/run-1/live/replay");
  expect(methods.findingRunReplayPath.call(context, findingWithRun, findingWithRun.evidence_refs[0])).toBe(
    "/admin/runs/run-1/live/replay?event_id=trace-1"
  );
  expect(methods.findingRunReplayPath.call(context, findingWithoutRun, findingWithoutRun.evidence_refs[0])).toBe(
    "/admin/runs/run-current/live/replay?seq_no=4"
  );
  expect(methods.canOpenFindingEvidenceReplay.call(context, findingWithoutRun)).toBe(true);

  methods.openFindingEvidenceReplay.call(context, findingWithRun, findingWithRun.evidence_refs[0]);

  expect(context.navigate).toHaveBeenCalledWith("/admin/runs/run-1/live/replay?event_id=trace-1");

  const htmlReport = fs.readFileSync(path.join(__dirname, "../../../pages/evaluation-reports.html"), "utf8");
  const htmlFindings = fs.readFileSync(path.join(__dirname, "../../../pages/evaluation-findings.html"), "utf8");
  expect(htmlReport).toContain("openFindingEvidenceReplay(finding, ref, currentEvaluation)");
  expect(htmlFindings).toContain("openFindingEvidenceReplay(finding, ref)");
  expect(htmlReport).toContain("canOpenFindingEvidenceReplay(finding, currentEvaluation)");
  expect(htmlFindings).toContain("canOpenFindingEvidenceReplay(finding)");
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

test("evaluation methods stream activity snapshots into current report", async () => {
  const { methods, FakeWebSocket } = loadEvaluationHarness();
  const finding = { id: "finding-1", status: "open", category: "runner_issue" };
  const evaluation = {
    id: "evaluation-1",
    run_id: "run-1",
    agent_run_id: "agent-run-1",
    overall_outcome: "failed",
    quality_score: 18,
    findings: [finding],
    created_at: "2026-01-01T00:00:00.000Z"
  };
  const context = {
    ...methods,
    apiBaseUrl: "/api/v1",
    busy: { evaluationReport: false },
    currentEvaluation: null,
    evaluationForm: { evaluation_id: "", run_id: "" },
    evaluationFindings: [finding],
    evaluationActivityWs: null,
    evaluationActivityWsId: "",
    evaluationActivityWsStatus: "idle",
    evaluationAgentRun: null,
    evaluationAgentEvents: [],
    evaluationModelCalls: [],
    apiRequest: jest.fn(async () => evaluation),
    showNotice: jest.fn()
  };

  await methods.loadEvaluationReport.call(context, "evaluation-1");
  const socket = FakeWebSocket.instances[0];
  socket.open();
  socket.message({
    event_type: "evaluation.activity.snapshot",
    payload: {
      evaluation: { ...evaluation, findings: [{ ...finding, status: "accepted" }] },
      findings: [{ ...finding, status: "accepted" }],
      agent_run: { id: "agent-run-1", agent_key: "pskill.evaluator", status: "succeeded" },
      agent_events: [{ id: "event-1", event_type: "evaluation.run.completed" }],
      model_calls: [{ id: "model-call-1", provider: "deterministic" }]
    }
  });

  expect(context.apiRequest).toHaveBeenCalledWith("/evaluations/evaluation-1");
  expect(socket.url).toBe("ws://localhost/ws/evaluations/evaluation-1");
  expect(context.evaluationActivityWsStatus).toBe("open");
  expect(context.currentEvaluation.findings[0].status).toBe("accepted");
  expect(context.evaluationFindings[0].status).toBe("accepted");
  expect(context.evaluationForm.evaluation_id).toBe("evaluation-1");
  expect(context.evaluationForm.run_id).toBe("run-1");
  expect(context.evaluationAgentRun.agent_key).toBe("pskill.evaluator");
  expect(context.evaluationAgentEvents).toHaveLength(1);
  expect(context.evaluationModelCalls).toHaveLength(1);

  methods.disconnectEvaluationActivityWebSocket.call(context);

  expect(context.evaluationActivityWs).toBeNull();
  expect(context.evaluationActivityWsStatus).toBe("idle");
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
