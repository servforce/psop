const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadRuntimeMethods(locationSearch = "") {
  const source = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");
  const helperNames = [
    "normalizePath",
    "resolveAdminRoute",
    "buildSkillDetailPath",
    "buildRunLivePath",
    "buildSkillRunLivePath",
    "buildSkillDebugRunLivePath",
    "buildReplayPath",
    "buildSkillReplayPath",
    "buildSkillTestScenarioPath",
    "buildSkillTestScenarioNewPath",
    "buildSkillTestScenarioRunReviewPath",
    "buildCompilerArtifactPath",
    "generateSkillKey",
    "resolveApiBaseUrl",
    "resolveWsUrl",
    "escapeHtml",
    "highlightJson",
    "highlightYamlScalar",
    "highlightYaml",
    "renderInlineMarkdown",
    "renderMarkdown"
  ];
  const context = {
    window: {
      location: { search: locationSearch },
      PSOPConsoleHelpers: Object.fromEntries(helperNames.map((name) => [name, jest.fn()])),
      PSOPRuntimeEvents: {
        mergeBySeq: (_existing, incoming) => incoming || [],
        mergeById: (existing, incoming) => [...(existing || []), ...(incoming || [])]
      }
    },
    Promise,
    Set,
    Date,
    Math,
    Number,
    String,
    Array,
    Object,
    JSON,
    URLSearchParams
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.PSOPConsoleRuntimeMethods;
}

test("run live page exposes embedded tool authorization tab", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  const runtimeJs = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");

  expect(html).toContain("liveRunInteractionTab === 'authorizations'");
  expect(html).toContain("liveRunToolAuthorizations.length");
  expect(html).toContain("decideLiveRunToolAuthorization(authorization, 'approve')");
  expect(html).toContain("decideLiveRunToolAuthorization(authorization, 'reject')");
  expect(appJs).toContain("liveRunToolAuthorizations");
  expect(runtimeJs).toContain("/runs/${runId}/tool-authorizations");
  expect(runtimeJs).toContain("isToolAuthorizationRunEvent");
  expect(runtimeJs).toContain("refreshLiveRunToolAuthorizations");
});

test("loadRunLive loads run-scoped tool authorizations", async () => {
  const methods = loadRuntimeMethods();
  const authorization = {
    id: "auth-1",
    status: "pending",
    tool_name: "psop.agent_version.activate"
  };
  const context = {
    ...methods,
    busy: { liveRun: false },
    liveRunLoadedRunId: "",
    liveRunTerminalEvents: [],
    liveRunTraceEvents: [],
    liveRunToolAuthorizations: [],
    selectedLiveRunReplayItemKey: "",
    selectedLiveRunProcessEventKey: "",
    route: { params: {} },
    apiRequest: jest.fn(async (url) => {
      if (url === "/runs/run-1") {
        return { id: "run-1", status: "waiting_input" };
      }
      if (url === "/runs/run-1/bindings") {
        return [];
      }
      if (url === "/terminal/sessions/run-1") {
        return { terminal_session: { id: "terminal-1", status: "open" } };
      }
      if (url === "/runs/run-1/events") {
        return [];
      }
      if (url === "/runs/run-1/traces") {
        return [];
      }
      if (url === "/replay/runs/run-1") {
        return { timeline: [], snapshots: [], run_events: [], run_traces: [] };
      }
      if (url === "/runs/run-1/tool-authorizations") {
        return [authorization];
      }
      return null;
    }),
    ensureLiveRunProcessSelection: jest.fn(),
    scrollTerminalTranscriptToBottom: jest.fn(),
    connectRunWebSocket: jest.fn()
  };

  await methods.loadRunLive.call(context, "run-1");

  expect(context.apiRequest).toHaveBeenCalledWith("/runs/run-1/tool-authorizations");
  expect(context.liveRunToolAuthorizations).toEqual([authorization]);
  expect(context.liveRunAuthorizationCountByStatus("pending")).toBe(1);
});

test("run live websocket authorization events refresh authorization list", async () => {
  const methods = loadRuntimeMethods();
  const latest = { id: "auth-1", status: "pending", tool_name: "psop.repository.commit_patch" };
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    liveRunToolAuthorizations: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/runs/run-1/tool-authorizations") {
        return [latest];
      }
      return [];
    }),
    mergeTerminalEvents: jest.fn(),
    refreshLiveRunToolAuthorizations: jest.fn()
  };

  methods.handleRunWsEvent.call(context, {
    event_type: "terminal.event.appended",
    payload: {
      id: "run-event-1",
      event_kind: "tool_authorization_request",
      seq_no: 3
    }
  });

  expect(context.mergeTerminalEvents).toHaveBeenCalledWith([
    {
      id: "run-event-1",
      event_kind: "tool_authorization_request",
      seq_no: 3
    }
  ]);
  expect(context.refreshLiveRunToolAuthorizations).toHaveBeenCalled();

  await methods.refreshLiveRunToolAuthorizations.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/runs/run-1/tool-authorizations");
  expect(context.liveRunToolAuthorizations).toEqual([latest]);
});

test("run replay selection follows event id from location", () => {
  const methods = loadRuntimeMethods("?event_id=run-event-1");
  const replayEvent = {
    seq_no: 4,
    event_type: "terminal.event.appended",
    occurred_at: "2026-01-01T00:00:04.000Z",
    payload: { id: "run-event-1" }
  };
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    replayDetail: {
      run: { id: "run-1" },
      timeline: [
        {
          seq_no: 1,
          event_type: "runtime.step",
          occurred_at: "2026-01-01T00:00:01.000Z",
          payload: { id: "trace-1" }
        },
        replayEvent
      ]
    },
    selectedLiveRunReplayItemKey: ""
  };

  methods.syncLiveRunReplaySelectionFromLocation.call(context);

  expect(context.selectedLiveRunReplayItemKey).toBe(methods.liveRunReplayItemKey(replayEvent));
  expect(methods.selectedLiveRunReplayItem.call(context)).toBe(replayEvent);
});

test("run replay exposes closed-loop evidence counts", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    replayDetail: {
      run: { id: "run-1" },
      agent_runs: [{ id: "agent-run-1", agent_key: "pskill.runner", status: "succeeded", owner_type: "runtime" }],
      agent_model_calls: [
        {
          id: "model-call-1",
          provider: "deterministic",
          route_key: "runner",
          status: "succeeded",
          usage_json: { total_tokens: 42 }
        }
      ],
      agent_tool_calls: [
        {
          id: "tool-call-1",
          tool_name: "psop.runtime.read",
          status: "succeeded",
          side_effect_level: "read"
        }
      ],
      agent_tool_authorizations: [
        {
          id: "auth-1",
          tool_name: "psop.repository.commit_patch",
          status: "pending"
        }
      ],
      run_evaluations: [
        {
          id: "evaluation-1",
          overall_outcome: "success",
          quality_score: 94,
          findings: []
        }
      ],
      run_evaluation_findings: [
        {
          id: "finding-1",
          category: "runner_issue",
          severity: "high",
          status: "open"
        }
      ]
    }
  };

  expect(methods.liveRunReplayAgentRunCount.call(context)).toBe(1);
  expect(methods.liveRunReplayModelCallCount.call(context)).toBe(1);
  expect(methods.liveRunReplayToolCallCount.call(context)).toBe(1);
  expect(methods.liveRunReplayToolAuthorizationCount.call(context)).toBe(1);
  expect(methods.liveRunReplayEvaluationCount.call(context)).toBe(1);
  expect(methods.liveRunReplayFindingCount.call(context)).toBe(1);
  expect(methods.liveRunReplayAgentRuns.call(context)).toEqual(context.replayDetail.agent_runs);
  expect(methods.liveRunReplayModelCalls.call(context)).toEqual(context.replayDetail.agent_model_calls);
  expect(methods.liveRunReplayToolCalls.call(context)).toEqual(context.replayDetail.agent_tool_calls);
  expect(methods.liveRunReplayToolAuthorizations.call(context)).toEqual(
    context.replayDetail.agent_tool_authorizations
  );
  expect(methods.liveRunReplayEvaluations.call(context)).toEqual(context.replayDetail.run_evaluations);
  expect(methods.liveRunReplayFindings.call(context)).toEqual(context.replayDetail.run_evaluation_findings);
  expect(methods.liveRunReplayAgentRunSummary(context.replayDetail.agent_runs[0])).toBe(
    "pskill.runner · succeeded · runtime"
  );
  expect(methods.liveRunReplayModelCallSummary(context.replayDetail.agent_model_calls[0])).toBe(
    "runner · succeeded · 42 tokens"
  );
  expect(methods.liveRunReplayToolCallSummary(context.replayDetail.agent_tool_calls[0])).toBe(
    "psop.runtime.read · succeeded · read"
  );
  expect(methods.liveRunReplayEvaluationSummary(context.replayDetail.run_evaluations[0])).toBe(
    "success · score 94 · 0 findings"
  );
  expect(methods.liveRunReplayFindingSummary(context.replayDetail.run_evaluation_findings[0])).toBe(
    "runner_issue · high · open"
  );

  const fallbackContext = {
    ...context,
    replayDetail: {
      run: { id: "run-1" },
      model_calls: [{ id: "model-call-1" }],
      tool_calls: [{ id: "tool-call-1" }],
      tool_authorizations: [{ id: "auth-1" }]
    }
  };

  expect(methods.liveRunReplayModelCallCount.call(fallbackContext)).toBe(1);
  expect(methods.liveRunReplayToolCallCount.call(fallbackContext)).toBe(1);
  expect(methods.liveRunReplayToolAuthorizationCount.call(fallbackContext)).toBe(1);

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  expect(html).toContain("Closed-loop Evidence");
  expect(html).toContain("liveRunReplayAgentRunCount()");
  expect(html).toContain("liveRunReplayModelCallCount()");
  expect(html).toContain("liveRunReplayToolAuthorizationCount()");
  expect(html).toContain("liveRunReplayEvaluationCount()");
  expect(html).toContain("liveRunReplayFindingCount()");
  expect(html).toContain("liveRunReplayAgentRunSummary(agentRun)");
  expect(html).toContain("liveRunReplayFindingSummary(finding)");
});

test("run live authorization decisions update local list and refresh run", async () => {
  const methods = loadRuntimeMethods();
  const pending = { id: "auth-1", status: "pending", tool_name: "psop.repository.commit_patch" };
  const approved = { ...pending, status: "approved" };
  const context = {
    ...methods,
    busy: { toolAuthorizationAction: false },
    liveRun: { id: "run-1" },
    liveRunToolAuthorizations: [pending],
    toolAuthorizations: [],
    apiRequest: jest.fn(async (url, options) => {
      if (url === "/tool-authorizations/auth-1/approve" && options?.method === "POST") {
        return approved;
      }
      return null;
    }),
    loadRunLive: jest.fn(),
    showNotice: jest.fn(),
    replaceToolAuthorization: jest.fn()
  };

  await methods.decideLiveRunToolAuthorization.call(context, pending, "approve");

  expect(context.apiRequest).toHaveBeenCalledWith("/tool-authorizations/auth-1/approve", {
    method: "POST",
    body: JSON.stringify({
      response_payload: {
        decision_source: "run_live_ui"
      }
    })
  });
  expect(context.liveRunToolAuthorizations).toEqual([approved]);
  expect(context.replaceToolAuthorization).toHaveBeenCalledWith(approved);
  expect(context.loadRunLive).toHaveBeenCalledWith("run-1");
  expect(context.showNotice).toHaveBeenCalledWith("success", "工具授权已批准。");
});
