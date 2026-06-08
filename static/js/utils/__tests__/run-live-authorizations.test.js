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

function loadRuntimeHarness(locationSearch = "") {
  const FakeWebSocket = createFakeWebSocketClass();
  const source = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");
  const helperNames = [
    "normalizePath",
    "resolveAdminRoute",
    "buildSkillDetailPath",
    "buildRunLivePath",
    "buildRunEventsPath",
    "buildSkillRunLivePath",
    "buildSkillRunEventsPath",
    "buildSkillDebugRunLivePath",
    "buildReplayPath",
    "buildSkillReplayPath",
    "buildSkillTestScenarioPath",
    "buildSkillTestScenarioNewPath",
    "buildSkillTestScenarioRunReviewPath",
    "buildCompilerArtifactPath",
    "buildCompilerRequestPath",
    "buildPlatformAgentRunPath",
    "buildEvaluationReportPath",
    "buildEvaluationFindingsPath",
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
        mergeBySeq: (existing = [], incoming = []) => {
          const map = new Map();
          for (const item of existing || []) {
            if (item && Number.isFinite(Number(item.seq_no))) {
              map.set(Number(item.seq_no), item);
            }
          }
          for (const item of incoming || []) {
            if (item && Number.isFinite(Number(item.seq_no))) {
              const seq = Number(item.seq_no);
              map.set(seq, { ...(map.get(seq) || {}), ...item });
            }
          }
          return Array.from(map.values()).sort((left, right) => Number(left.seq_no) - Number(right.seq_no));
        },
        mergeById: (existing = [], incoming = []) => {
          const map = new Map();
          for (const item of existing || []) {
            if (item?.id) {
              map.set(item.id, item);
            }
          }
          for (const item of incoming || []) {
            if (item?.id) {
              map.set(item.id, { ...(map.get(item.id) || {}), ...item });
            }
          }
          return Array.from(map.values());
        }
      }
    },
    WebSocket: FakeWebSocket,
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
  context.window.PSOPConsoleHelpers.buildRunEventsPath = (runId) => `/admin/runs/${runId}/events`;
  context.window.PSOPConsoleHelpers.buildSkillRunEventsPath = (skillId, runId) => (
    `/admin/skills/${skillId}/runs/${runId}/events`
  );
  context.window.PSOPConsoleHelpers.buildCompilerRequestPath = (compileRequestId) => (
    `/admin/compiler?compile_request_id=${encodeURIComponent(compileRequestId)}`
  );
  context.window.PSOPConsoleHelpers.buildPlatformAgentRunPath = (agentRunId, focus = {}) => {
    const params = new URLSearchParams();
    for (const key of ["tab", "event_id", "model_call_id", "tool_call_id", "authorization_id"]) {
      const value = String(focus?.[key] || "").trim();
      if (value) {
        params.set(key, value);
      }
    }
    const query = params.toString();
    return query ? `/admin/platform/agent-runs/${agentRunId}?${query}` : `/admin/platform/agent-runs/${agentRunId}`;
  };
  context.window.PSOPConsoleHelpers.buildEvaluationReportPath = (evaluationId) => `/admin/evaluations/${evaluationId}`;
  context.window.PSOPConsoleHelpers.buildEvaluationFindingsPath = () => "/admin/evaluations/findings";
  context.window.PSOPConsoleHelpers.resolveWsUrl = (_apiBaseUrl, pathname) => `ws://localhost${pathname}`;
  vm.createContext(context);
  vm.runInContext(source, context);
  return { methods: context.window.PSOPConsoleRuntimeMethods, FakeWebSocket };
}

function loadRuntimeMethods(locationSearch = "") {
  return loadRuntimeHarness(locationSearch).methods;
}

test("run live page exposes embedded tool authorization tab", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  const runtimeJs = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");

  expect(html).toContain("liveRunInteractionTab === 'authorizations'");
  expect(html).toContain("liveRunToolAuthorizations.length");
  expect(html).toContain("liveRunToolAuthorizationWsStatus");
  expect(html).toContain("decideLiveRunToolAuthorization(authorization, 'approve')");
  expect(html).toContain("decideLiveRunToolAuthorization(authorization, 'reject')");
  expect(appJs).toContain("liveRunToolAuthorizations");
  expect(appJs).toContain("liveRunToolAuthorizationWsStatus");
  expect(runtimeJs).toContain("/runs/${runId}/tool-authorizations");
  expect(runtimeJs).toContain("/ws/tool-authorizations");
  expect(runtimeJs).toContain("connectLiveRunToolAuthorizationWebSocket");
  expect(runtimeJs).toContain("isToolAuthorizationRunEvent");
  expect(runtimeJs).toContain("refreshLiveRunToolAuthorizations");
  expect(html).toContain("Replay Provenance");
  expect(html).toContain("liveRunCompileRequestId()");
  expect(html).toContain("openLiveRunCompileRequest()");
  expect(html).toContain("openCompilerArtifact(liveRun.compile_artifact_id)");
  expect(html).toContain("replayDetail?.provenance?.compile_request_id");
  expect(html).toContain("replayDetail?.provenance?.latest_session_token_snapshot_id");
});

test("run live compile request evidence link opens compiler request filter", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    liveRun: { compile_request_id: "compile-1" },
    replayDetail: null,
    navigate: jest.fn()
  };

  expect(methods.liveRunCompileRequestId.call(context)).toBe("compile-1");
  expect(methods.liveRunCompileRequestPath.call(context)).toBe("/admin/compiler?compile_request_id=compile-1");

  methods.openLiveRunCompileRequest.call(context);

  expect(context.navigate).toHaveBeenCalledWith("/admin/compiler?compile_request_id=compile-1");
});

test("run live opens the raw events tab from the Run Events route view", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    route: { name: "run-live", params: { runId: "run-1", view: "events" } },
    liveRunInteractionTab: "run-events",
    currentSkill: null
  };

  methods.syncLiveRunInteractionTabFromRoute.call(context, false);
  context.currentSkill = { id: "skill-1" };

  expect(context.liveRunInteractionTab).toBe("events");
  expect(methods.runEventsPath.call({ ...context, currentSkill: null }, "run-1")).toBe("/admin/runs/run-1/events");
  expect(methods.runEventsPath.call(context, "run-1")).toBe("/admin/skills/skill-1/runs/run-1/events");

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  expect(html).toContain("navigate(runEventsPath(liveRun.id))");
});

test("run live maps legacy terminal route view to run events tab", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    route: { name: "run-live", params: { runId: "run-1", view: "terminal" } },
    liveRunInteractionTab: "events"
  };

  methods.syncLiveRunInteractionTabFromRoute.call(context, false);

  expect(context.liveRunInteractionTab).toBe("run-events");
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
    liveRunEvents: [],
    liveRunTraceEvents: [],
    liveRunToolAuthorizations: [],
    selectedLiveRunReplayItemKey: "",
    selectedLiveRunProcessEventKey: "",
    route: { params: {} },
    apiRequest: jest.fn(async (url) => {
      if (url === "/runs/run-1") {
        return { id: "run-1", status: "waiting_input", terminal_session_id: "terminal-1" };
      }
      if (url === "/runs/run-1/bindings") {
        return [];
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
    scrollRunEventTranscriptToBottom: jest.fn(),
    connectRunWebSocket: jest.fn()
  };

  await methods.loadRunLive.call(context, "run-1");

  expect(context.apiRequest).not.toHaveBeenCalledWith("/terminal/sessions/run-1");
  expect(context.liveRunTerminalSession).toMatchObject({ id: "terminal-1", status: "open" });
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
    mergeRunEvents: jest.fn(),
    refreshLiveRunToolAuthorizations: jest.fn()
  };

  methods.handleRunWsEvent.call(context, {
    event_type: "run.event.appended",
    payload: {
      id: "run-event-1",
      event_kind: "tool_authorization_request",
      seq_no: 3
    }
  });

  expect(context.mergeRunEvents).toHaveBeenCalledWith([
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

test("run live websocket accepts legacy run event aliases", () => {
  const methods = loadRuntimeMethods();
  const runEvent = {
    id: "run-event-legacy",
    run_id: "run-1",
    direction: "output",
    event_kind: "agent_output",
    seq_no: 4,
    payload_inline: "legacy event",
    parts: []
  };
  const runTrace = {
    id: "run-trace-legacy",
    run_id: "run-1",
    event_type: "runtime.failed",
    seq_no: 5,
    payload: {}
  };
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    liveRunEvents: [],
    liveRunTraceEvents: [],
    liveRunToolAuthorizations: [],
    replayDetail: null,
    mergeRunEvents: jest.fn(),
    refreshLiveRunToolAuthorizations: jest.fn()
  };

  methods.handleRunWsEvent.call(context, {
    event_type: "terminal.event.appended",
    payload: runEvent
  });
  methods.handleRunWsEvent.call(context, {
    event_type: "trace.event.appended",
    payload: runTrace
  });

  expect(context.mergeRunEvents).toHaveBeenCalledWith([runEvent]);
  expect(context.liveRunTraceEvents).toEqual([runTrace]);
});

test("run live websocket events update replay timeline evidence incrementally", () => {
  const methods = loadRuntimeMethods();
  const runEvent = {
    id: "run-event-1",
    run_id: "run-1",
    direction: "output",
    event_kind: "agent_output",
    seq_no: 2,
    payload_inline: "请检查连接件。",
    parts: [],
    occurred_at: "2026-06-08T00:00:02.000Z"
  };
  const traceEvent = {
    id: "trace-1",
    run_id: "run-1",
    phase: "instruct_collect_context",
    event_type: "runtime.wait_checkpoint.entered",
    seq_no: 3,
    payload: {
      wait: { checkpoint_id: "collect_context_evidence" },
      summary: "等待用户提交现场证据。"
    },
    agent_run_id: "agent-run-1",
    occurred_at: "2026-06-08T00:00:03.000Z"
  };
  const binding = {
    id: "binding-1",
    requirement_key: "capture.image",
    capability: "terminal.upload",
    target_kind: "web"
  };
  const snapshot = {
    id: "snapshot-2",
    run_id: "run-1",
    seq_no: 2,
    token_payload: { phase: "instruct_collect_context" },
    enabled_set: ["instruct_collect_context"],
    selection_summary: { selected: "collect_context", next_phase: "instruct_collect_context" },
    snapshot_hash: "hash-2",
    created_at: "2026-06-08T00:00:04.000Z"
  };
  const updatedRun = {
    id: "run-1",
    status: "succeeded",
    runtime_phase: "completed",
    latest_snapshot_seq: 2,
    latest_run_event_seq: 2,
    latest_terminal_seq: 2,
    latest_trace_seq: 3,
    current_step: "",
    wait_reason: "",
    expected_inputs: [],
    final_output: "测试任务已完成",
    updated_at: "2026-06-08T00:00:05.000Z"
  };
  const context = {
    ...methods,
    liveRun: { id: "run-1", status: "waiting_input", latest_snapshot_seq: 1 },
    liveRunEvents: [],
    liveRunTraceEvents: [],
    liveRunBindings: [],
    liveRunToolAuthorizations: [],
    replayDetail: {
      run: { id: "run-1" },
      timeline: [],
      run_events: [],
      run_traces: [],
      eg_node_path: [],
      bindings: [],
      snapshots: [
        {
          id: "snapshot-1",
          run_id: "run-1",
          seq_no: 1,
          token_payload: { phase: "collect_context" },
          enabled_set: ["collect_context"],
          selection_summary: { selected: "start", next_phase: "collect_context" },
          snapshot_hash: "hash-1",
          created_at: "2026-06-08T00:00:01.000Z"
        }
      ]
    },
    selectedLiveRunSnapshotBaseSeq: "",
    selectedLiveRunSnapshotTargetSeq: "",
    ensureLiveRunProcessSelection: jest.fn(),
    scrollRunEventTranscriptToBottom: jest.fn()
  };

  methods.handleRunWsEvent.call(context, {
    event_type: "run.event.appended",
    payload: runEvent
  });
  methods.handleRunWsEvent.call(context, {
    event_type: "run.trace.appended",
    payload: traceEvent
  });
  methods.handleRunWsEvent.call(context, {
    event_type: "binding.updated",
    payload: { bindings: [binding] }
  });
  methods.handleRunWsEvent.call(context, {
    event_type: "session_token.snapshot.appended",
    payload: snapshot
  });
  methods.handleRunWsEvent.call(context, {
    event_type: "run.updated",
    payload: updatedRun
  });

  expect(context.liveRunEvents).toEqual([runEvent]);
  expect(context.liveRunTraceEvents).toEqual([traceEvent]);
  expect(methods.liveRunReplayRunEventCount.call(context)).toBe(1);
  expect(methods.liveRunReplayTraceCount.call(context)).toBe(1);
  expect(context.replayDetail.run_events).toEqual([runEvent]);
  expect(context.replayDetail.terminal_events).toBeUndefined();
  expect(context.replayDetail.run_traces).toEqual([traceEvent]);
  expect(context.replayDetail.trace_events).toBeUndefined();
  expect(context.replayDetail.bindings).toEqual([binding]);
  expect(methods.liveRunReplaySnapshots.call(context).map((item) => item.seq_no)).toEqual([1, 2]);
  expect(context.liveRun.latest_snapshot_seq).toBe(2);
  expect(context.liveRun.status).toBe("succeeded");
  expect(context.liveRun.final_output).toBe("测试任务已完成");
  expect(context.replayDetail.run.status).toBe("succeeded");
  expect(context.selectedLiveRunSnapshotBaseSeq).toBe("1");
  expect(context.selectedLiveRunSnapshotTargetSeq).toBe("2");
  expect(methods.liveRunReplayTimeline.call(context).map((item) => item.source_kind)).toEqual([
    "run_event",
    "run_trace"
  ]);
  expect(methods.liveRunReplayTimeline.call(context).map((item) => item.title)).toEqual([
    "终端输出",
    "等待现场证据"
  ]);
  expect(methods.liveRunReplayTimeline.call(context)[1].agent_run_id).toBe("agent-run-1");
  expect(methods.liveRunReplayEgNodePathCount.call(context)).toBe(1);
  expect(methods.liveRunReplayEgNodePath.call(context)[0]).toMatchObject({
    trace_id: "trace-1",
    node_id: "instruct_collect_context",
    checkpoint_id: "collect_context_evidence",
    agent_run_id: "agent-run-1"
  });
});

test("run live listens for executed tool authorization updates scoped to the current run", async () => {
  const { methods, FakeWebSocket } = loadRuntimeHarness();
  const pending = {
    id: "auth-1",
    run_id: "run-1",
    status: "pending",
    tool_name: "psop.repository.commit_patch"
  };
  const executed = {
    ...pending,
    status: "executed",
    executed_at: "2026-06-08T00:00:00Z"
  };
  const refreshedReplayDetail = {
    run: { id: "run-1" },
    agent_tool_authorizations: [executed],
    agent_events: [
      {
        id: "agent-event-1",
        agent_run_id: "agent-run-1",
        event_type: "tool.authorization_executed",
        phase: "tool_authorization",
        seq_no: 7
      }
    ]
  };
  const context = {
    ...methods,
    apiBaseUrl: "/api/v1",
    liveRun: { id: "run-1" },
    liveRunToolAuthorizations: [pending],
    replayDetail: {
      run: { id: "run-1" },
      agent_tool_authorizations: [pending],
      agent_events: []
    },
    liveRunToolAuthorizationWs: null,
    liveRunToolAuthorizationWsRunId: "",
    liveRunToolAuthorizationWsStatus: "idle",
    selectedLiveRunSnapshotBaseSeq: "",
    selectedLiveRunSnapshotTargetSeq: "",
    toolAuthorizations: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/replay/runs/run-1") {
        return refreshedReplayDetail;
      }
      return null;
    }),
    replaceToolAuthorization: jest.fn()
  };

  expect(methods.connectLiveRunToolAuthorizationWebSocket.call(context, "run-1")).toBe(true);

  expect(FakeWebSocket.instances).toHaveLength(1);
  expect(FakeWebSocket.instances[0].url).toBe("ws://localhost/ws/tool-authorizations");
  expect(context.liveRunToolAuthorizationWsStatus).toBe("connecting");

  FakeWebSocket.instances[0].open();
  expect(context.liveRunToolAuthorizationWsStatus).toBe("open");

  FakeWebSocket.instances[0].message({
    event_type: "tool.authorization_executed",
    run_id: "other-run",
    payload: { ...executed, id: "auth-other", run_id: "other-run" }
  });
  expect(context.liveRunToolAuthorizations).toEqual([pending]);
  expect(context.apiRequest).not.toHaveBeenCalled();

  FakeWebSocket.instances[0].message({
    event_type: "tool.authorization_executed",
    run_id: "run-1",
    payload: executed
  });

  expect(context.liveRunToolAuthorizations).toEqual([executed]);
  expect(methods.liveRunReplayToolAuthorizations.call(context)).toEqual([executed]);
  expect(context.replaceToolAuthorization).toHaveBeenCalledWith(executed);
  expect(context.apiRequest).toHaveBeenCalledWith("/replay/runs/run-1");

  await methods.refreshLiveRunReplayDetail.call(context, "run-1");

  expect(context.replayDetail).toEqual(refreshedReplayDetail);
  expect(methods.liveRunReplayAgentEventCount.call(context)).toBe(1);

  methods.disconnectLiveRunToolAuthorizationWebSocket.call(context);

  expect(context.liveRunToolAuthorizationWs).toBeNull();
  expect(context.liveRunToolAuthorizationWsStatus).toBe("idle");
});

test("loadReplayDetail accepts replay detail without a live run context", async () => {
  const methods = loadRuntimeMethods();
  const replayDetail = { run: { id: "run-standalone" }, timeline: [] };
  const context = {
    ...methods,
    busy: { replayDetail: false },
    liveRun: null,
    replayDetail: null,
    selectedLiveRunSnapshotBaseSeq: "",
    selectedLiveRunSnapshotTargetSeq: "",
    apiRequest: jest.fn(async (url) => {
      if (url === "/replay/runs/run-standalone") {
        return replayDetail;
      }
      return null;
    })
  };

  await methods.loadReplayDetail.call(context, "run-standalone");

  expect(context.apiRequest).toHaveBeenCalledWith("/replay/runs/run-standalone");
  expect(context.replayDetail).toEqual(replayDetail);
  expect(context.busy.replayDetail).toBe(false);
});

test("run replay selection follows event id from location", () => {
  const methods = loadRuntimeMethods("?event_id=run-event-1");
  const replayEvent = {
    seq_no: 4,
    event_type: "run.event.appended",
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

test("run replay selection follows trace source id from location", () => {
  const methods = loadRuntimeMethods("?trace_id=trace-1");
  const traceEvent = {
    seq_no: 1,
    event_type: "runtime.failed",
    occurred_at: "2026-01-01T00:00:01.000Z",
    source_kind: "run_trace",
    source_id: "trace-1",
    payload: { error: "provider failed" }
  };
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    replayDetail: {
      run: { id: "run-1" },
      timeline: [traceEvent]
    },
    selectedLiveRunReplayItemKey: ""
  };

  methods.syncLiveRunReplaySelectionFromLocation.call(context);

  expect(context.selectedLiveRunReplayItemKey).toBe(methods.liveRunReplayItemKey(traceEvent));
});

test("run replay exposes closed-loop evidence counts", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    replayDetail: {
      run: { id: "run-1" },
      agent_runs: [{ id: "agent-run-1", agent_key: "pskill.runner", status: "succeeded", owner_type: "runtime" }],
      agent_events: [
        {
          id: "agent-event-1",
          agent_run_id: "agent-run-1",
          event_type: "tool.authorization_executed",
          phase: "tool_authorization",
          seq_no: 6
        }
      ],
      agent_model_calls: [
        {
          id: "model-call-1",
          agent_run_id: "agent-run-1",
          provider: "deterministic",
          route_key: "runner",
          status: "succeeded",
          usage_json: { total_tokens: 42 }
        }
      ],
      agent_tool_calls: [
        {
          id: "tool-call-1",
          agent_run_id: "agent-run-1",
          tool_name: "psop.runtime.read",
          status: "succeeded",
          side_effect_level: "read"
        }
      ],
      agent_tool_authorizations: [
        {
          id: "auth-1",
          agent_run_id: "agent-run-1",
          tool_name: "psop.repository.commit_patch",
          status: "pending"
        }
      ],
      run_evaluations: [
        {
          id: "evaluation-1",
          agent_run_id: "agent-run-1",
          overall_outcome: "success",
          quality_score: 94,
          findings: []
        }
      ],
      run_evaluation_findings: [
        {
          id: "finding-1",
          run_id: "run-1",
          category: "runner_issue",
          severity: "high",
          status: "open"
        }
      ]
    }
  };

  expect(methods.liveRunReplayAgentRunCount.call(context)).toBe(1);
  expect(methods.liveRunReplayAgentEventCount.call(context)).toBe(1);
  expect(methods.liveRunReplayModelCallCount.call(context)).toBe(1);
  expect(methods.liveRunReplayToolCallCount.call(context)).toBe(1);
  expect(methods.liveRunReplayToolAuthorizationCount.call(context)).toBe(1);
  expect(methods.liveRunReplayEvaluationCount.call(context)).toBe(1);
  expect(methods.liveRunReplayFindingCount.call(context)).toBe(1);
  expect(methods.liveRunReplayAgentRuns.call(context)).toEqual(context.replayDetail.agent_runs);
  expect(methods.liveRunReplayAgentEvents.call(context)).toEqual(context.replayDetail.agent_events);
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
  expect(methods.liveRunReplayAgentEventSummary(context.replayDetail.agent_events[0])).toBe(
    "tool_authorization · agent-run-1 · #6"
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
  expect(methods.liveRunReplayAgentRunPath.call(context, "agent-run-1", { tab: "events" })).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=events"
  );
  expect(methods.liveRunReplayAgentEventPath(context.replayDetail.agent_events[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=events&event_id=agent-event-1"
  );
  expect(methods.liveRunReplayModelCallPath(context.replayDetail.agent_model_calls[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=model&model_call_id=model-call-1"
  );
  expect(methods.liveRunReplayToolCallPath(context.replayDetail.agent_tool_calls[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=tools&tool_call_id=tool-call-1"
  );
  expect(methods.liveRunReplayToolAuthorizationPath(context.replayDetail.agent_tool_authorizations[0])).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=authorizations&authorization_id=auth-1"
  );
  expect(methods.liveRunReplayEvaluationPath(context.replayDetail.run_evaluations[0])).toBe(
    "/admin/evaluations/evaluation-1"
  );
  expect(methods.liveRunReplayFindingPath.call(context, context.replayDetail.run_evaluation_findings[0])).toBe(
    "/admin/evaluations/findings?run_id=run-1"
  );

  const navigationContext = { ...context, navigate: jest.fn() };
  methods.openLiveRunReplayAgentRun.call(navigationContext, context.replayDetail.agent_runs[0], { tab: "events" });
  expect(navigationContext.navigate).toHaveBeenCalledWith("/admin/platform/agent-runs/agent-run-1?tab=events");
  methods.openLiveRunReplayEvaluation.call(navigationContext, context.replayDetail.run_evaluations[0]);
  methods.openLiveRunReplayFinding.call(navigationContext, context.replayDetail.run_evaluation_findings[0]);
  expect(navigationContext.navigate).toHaveBeenCalledWith("/admin/evaluations/evaluation-1");
  expect(navigationContext.navigate).toHaveBeenCalledWith("/admin/evaluations/findings?run_id=run-1");

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
  expect(html).toContain("liveRunReplayAgentEventCount()");
  expect(html).toContain("liveRunReplayAgentEventSummary(event)");
  expect(html).toContain("liveRunReplayModelCallCount()");
  expect(html).toContain("liveRunReplayToolAuthorizationCount()");
  expect(html).toContain("liveRunReplayEvaluationCount()");
  expect(html).toContain("liveRunReplayFindingCount()");
  expect(html).toContain("liveRunReplayAgentRunSummary(agentRun)");
  expect(html).toContain("liveRunReplayFindingSummary(finding)");
  expect(html).toContain("openLiveRunReplayAgentRun(agentRun, { tab: 'events' })");
  expect(html).toContain("liveRunReplayAgentEventPath(event)");
  expect(html).toContain("liveRunReplayModelCallPath(call)");
  expect(html).toContain("liveRunReplayToolCallPath(call)");
  expect(html).toContain("liveRunReplayToolAuthorizationPath(authorization)");
  expect(html).toContain("openLiveRunReplayEvaluation(evaluation)");
  expect(html).toContain("openLiveRunReplayFinding(finding)");
  expect(html).toContain("liveRunReplayAgentRunPath(selectedLiveRunReplayItem().agent_run_id");
});

test("run replay finding evidence refs select matching timeline item", () => {
  const methods = loadRuntimeMethods();
  const traceItem = {
    seq_no: 7,
    phase: "runtime",
    event_type: "runtime.failed",
    occurred_at: "2026-01-01T00:00:07.000Z",
    source_kind: "run_trace",
    source_id: "trace-1",
    payload: { summary: "provider failed" }
  };
  const eventItem = {
    seq_no: 8,
    phase: "terminal",
    event_type: "run.event.appended",
    occurred_at: "2026-01-01T00:00:08.000Z",
    source_kind: "run_event",
    source_id: "event-1",
    payload: { id: "event-1", event_kind: "agent_output" }
  };
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    replayDetail: {
      run: { id: "run-1" },
      timeline: [eventItem, traceItem]
    },
    selectedLiveRunReplayItemKey: "",
    showNotice: jest.fn()
  };

  const traceRef = { kind: "run_trace", id: "trace-1", event_type: "runtime.failed" };
  const eventRef = { kind: "run_event", id: "event-1", event_kind: "agent_output" };
  const legacyEventRef = { kind: "terminal_event", id: "event-1", event_kind: "agent_output" };

  expect(methods.liveRunReplayEvidenceRefs({ evidence_refs: [traceRef] })).toEqual([traceRef]);
  expect(methods.liveRunReplayEvidenceRefLabel(traceRef)).toBe("run_trace:runtime.failed");
  expect(methods.liveRunReplayTraceTitle({ event_type: "gateway.inference.failed" })).toBe("LLM 失败");
  expect(methods.liveRunReplayEvidenceRefLabel(legacyEventRef)).toBe("run_event:agent_output");
  expect(methods.liveRunReplayFindEvidenceItem.call(context, traceRef)).toBe(traceItem);
  expect(methods.liveRunReplayFindEvidenceItem.call(context, eventRef)).toBe(eventItem);
  expect(methods.liveRunReplayFindEvidenceItem.call(context, legacyEventRef)).toBe(eventItem);
  expect(methods.liveRunReplayEvidenceRefClass.call(context, traceRef)).toContain("text-sky-200");

  expect(methods.selectLiveRunReplayEvidenceRef.call(context, traceRef)).toBe(traceItem);
  expect(context.selectedLiveRunReplayItemKey).toBe(methods.liveRunReplayItemKey(traceItem));

  expect(methods.selectLiveRunReplayEvidenceRef.call(context, { kind: "run_trace", id: "missing" })).toBeNull();
  expect(context.showNotice).toHaveBeenCalledWith("error", "未找到对应 Replay 证据。");

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  expect(html).toContain("liveRunReplayEvidenceRefs(finding)");
  expect(html).toContain("liveRunReplayEvidenceRefLabel(ref)");
  expect(html).toContain("selectLiveRunReplayEvidenceRef(ref)");
});

test("run replay compares arbitrary session token snapshots", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    replayDetail: {
      run: { id: "run-1" },
      snapshots: [
        {
          id: "snapshot-1",
          seq_no: 1,
          created_at: "2026-01-01T00:00:01.000Z",
          enabled_set: ["a"],
          selection_summary: { checkpoint: { id: "collect" }, phase: "input", retained: "same" },
          token_payload: { phase: "input" },
          snapshot_hash: "hash-1"
        },
        {
          id: "snapshot-2",
          seq_no: 2,
          created_at: "2026-01-01T00:00:02.000Z",
          enabled_set: ["a", "b"],
          selection_summary: { checkpoint: { id: "collect" }, phase: "waiting", retained: "same" },
          token_payload: { phase: "waiting" },
          snapshot_hash: "hash-2"
        },
        {
          id: "snapshot-3",
          seq_no: 3,
          created_at: "2026-01-01T00:00:03.000Z",
          enabled_set: ["a", "b", "c"],
          selection_summary: { checkpoint: { id: "finish" }, phase: "complete", retained: "same", result: "ok" },
          token_payload: { phase: "complete" },
          snapshot_hash: "hash-3"
        }
      ]
    },
    selectedLiveRunSnapshotBaseSeq: "",
    selectedLiveRunSnapshotTargetSeq: "",
    formatDateTime: (value) => value || ""
  };

  methods.ensureLiveRunSnapshotCompareSelection.call(context);

  expect(context.selectedLiveRunSnapshotBaseSeq).toBe("2");
  expect(context.selectedLiveRunSnapshotTargetSeq).toBe("3");

  context.selectedLiveRunSnapshotBaseSeq = "1";
  context.selectedLiveRunSnapshotTargetSeq = "3";
  const diff = methods.liveRunReplaySnapshotCompare.call(context);

  expect(diff.enabled_added).toEqual(["b", "c"]);
  expect(diff.enabled_removed).toEqual([]);
  expect(diff.summary_added_keys).toEqual(["result"]);
  expect(diff.summary_removed_keys).toEqual([]);
  expect(diff.summary_changed_keys).toEqual(["checkpoint", "phase"]);
  expect(diff.token_payload_changed).toBe(true);
  expect(diff.snapshot_hash_changed).toBe(true);
  expect(methods.liveRunReplaySnapshotDiffStats.call(context)).toEqual([
    { label: "Enabled Added", value: 2 },
    { label: "Enabled Removed", value: 0 },
    { label: "Summary Keys", value: 3 },
    { label: "Token Payload", value: "changed" }
  ]);
  expect(methods.liveRunReplaySnapshotCompareSummary.call(context)).toContain("enabled +2/-0");

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  expect(appJs).toContain("selectedLiveRunSnapshotBaseSeq");
  expect(appJs).toContain("selectedLiveRunSnapshotTargetSeq");
  expect(html).toContain("Base Snapshot");
  expect(html).toContain("Target Snapshot");
  expect(html).toContain("liveRunReplaySnapshotCompare()");
  expect(html).toContain("liveRunReplaySnapshotDiffStats()");
});

test("run replay exposes clickable eg node execution path", () => {
  const methods = loadRuntimeMethods();
  const traceItem = {
    seq_no: 3,
    phase: "instruct_collect_context",
    event_type: "runtime.wait_checkpoint.entered",
    occurred_at: "2026-01-01T00:00:03.000Z",
    source_kind: "run_trace",
    source_id: "trace-node-1",
    payload: { wait: { checkpoint_id: "collect_context_evidence" } }
  };
  const pathItem = {
    seq_no: 3,
    trace_id: "trace-node-1",
    node_id: "instruct_collect_context",
    node_kind: "llm",
    phase: "instruct_collect_context",
    event_type: "runtime.wait_checkpoint.entered",
    title: "等待现场证据",
    summary: "等待用户提交现场证据。",
    checkpoint_id: "collect_context_evidence",
    agent_run_id: "agent-run-1",
    occurred_at: "2026-01-01T00:00:03.000Z"
  };
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    replayDetail: {
      run: { id: "run-1" },
      timeline: [traceItem],
      eg_node_path: [pathItem]
    },
    selectedLiveRunReplayItemKey: "",
    showNotice: jest.fn()
  };

  expect(methods.liveRunReplayEgNodePathCount.call(context)).toBe(1);
  expect(methods.liveRunReplayEgNodePathItemKey(pathItem)).toBe("3:trace-node-1:instruct_collect_context");
  expect(methods.liveRunReplayEgNodePathSummary(pathItem)).toBe(
    "runtime.wait_checkpoint.entered · llm · checkpoint collect_context_evidence"
  );
  expect(methods.liveRunReplayEgNodePathItemClass.call(context, pathItem)).toContain("hover:bg-slate-900/60");
  expect(methods.selectLiveRunReplayEgNodePathItem.call(context, pathItem)).toBe(traceItem);
  expect(context.selectedLiveRunReplayItemKey).toBe(methods.liveRunReplayItemKey(traceItem));

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  expect(html).toContain("EG Node Path");
  expect(html).toContain("liveRunReplayEgNodePathCount()");
  expect(html).toContain("liveRunReplayEgNodePathSummary(item)");
  expect(html).toContain("selectLiveRunReplayEgNodePathItem(item)");
});

test("run live raw events tab filters and exports original run events", () => {
  const methods = loadRuntimeMethods();
  const context = {
    ...methods,
    liveRun: { id: "run-1" },
    liveRunEventFilters: {
      q: "",
      direction: "",
      event_kind: ""
    },
    liveRunEvents: [
      {
        id: "event-2",
        run_id: "run-1",
        seq_no: 2,
        direction: "output",
        event_kind: "terminal.text.output.v1",
        mime_type: "text/plain",
        payload_inline: "请上传现场照片。",
        source_ref: { kind: "runtime", node_id: "instruct_collect_context" },
        parts: [],
        occurred_at: "2026-01-01T00:00:02.000Z"
      },
      {
        id: "event-1",
        run_id: "run-1",
        seq_no: 1,
        direction: "input",
        event_kind: "terminal.multimodal.input.v1",
        mime_type: "multipart/mixed",
        payload_inline: "现场证据",
        source_ref: { kind: "web", connection_id: "terminal" },
        parts: [{ part_id: "image_1", kind: "image", mime_type: "image/png", metadata: { filename: "fault.png" } }],
        occurred_at: "2026-01-01T00:00:01.000Z"
      }
    ],
    formatJson: (value) => JSON.stringify(value, null, 2)
  };

  expect(methods.liveRunRawEvents.call(context).map((event) => event.id)).toEqual(["event-1", "event-2"]);
  expect(methods.liveRunRawEventKinds.call(context)).toEqual([
    "terminal.multimodal.input.v1",
    "terminal.text.output.v1"
  ]);
  expect(methods.liveRunRawEventPartsSummary(context.liveRunEvents[1])).toBe("1 parts · image:image/png");

  context.liveRunEventFilters.direction = "input";
  context.liveRunEventFilters.q = "fault.png";

  expect(methods.liveRunRawFilteredEvents.call(context).map((event) => event.id)).toEqual(["event-1"]);
  expect(methods.liveRunRawEventSourceLabel(context.liveRunEvents[1])).toBe("web · terminal");
  expect(methods.liveRunRawEventJsonText.call(context, context.liveRunEvents[1])).toContain("fault.png");
  expect(methods.liveRunProcessEventMetadata.call(context, context.liveRunEvents[1])).toEqual(
    expect.arrayContaining([{ label: "RunEvent 序号", value: "#1" }])
  );

  const payload = methods.liveRunRawEventsDownloadPayload.call(context);
  expect(payload.schema).toBe("psop-run-events-export/v1");
  expect(payload.run_id).toBe("run-1");
  expect(payload.filters).toEqual({
    q: "fault.png",
    direction: "input",
    event_kind: ""
  });
  expect(payload.event_count).toBe(1);
  expect(payload.events[0].id).toBe("event-1");

  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  expect(appJs).toContain("liveRunEventFilters");
  expect(html).toContain("liveRunInteractionTab === 'events'");
  expect(html).toContain("liveRunRawFilteredEvents()");
  expect(html).toContain("downloadLiveRunRawEvents()");
  expect(html).toContain("liveRunRawEventJsonText(rawEvent)");
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
