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

test("run replay selection follows trace source id from location", () => {
  const methods = loadRuntimeMethods("?event_id=trace-1");
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
    event_type: "terminal.event.appended",
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
  const eventRef = { kind: "terminal_event", id: "event-1", event_kind: "agent_output" };

  expect(methods.liveRunReplayEvidenceRefs({ evidence_refs: [traceRef] })).toEqual([traceRef]);
  expect(methods.liveRunReplayEvidenceRefLabel(traceRef)).toBe("run_trace:runtime.failed");
  expect(methods.liveRunReplayFindEvidenceItem.call(context, traceRef)).toBe(traceItem);
  expect(methods.liveRunReplayFindEvidenceItem.call(context, eventRef)).toBe(eventItem);
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
    liveRunTerminalEvents: [
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
  expect(methods.liveRunRawEventPartsSummary(context.liveRunTerminalEvents[1])).toBe("1 parts · image:image/png");

  context.liveRunEventFilters.direction = "input";
  context.liveRunEventFilters.q = "fault.png";

  expect(methods.liveRunRawFilteredEvents.call(context).map((event) => event.id)).toEqual(["event-1"]);
  expect(methods.liveRunRawEventSourceLabel(context.liveRunTerminalEvents[1])).toBe("web · terminal");
  expect(methods.liveRunRawEventJsonText.call(context, context.liveRunTerminalEvents[1])).toContain("fault.png");

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
