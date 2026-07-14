const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { mergeById, mergeBySeq } = require("../runtime-events.node.cjs");

const runtimePath = path.join(__dirname, "../../app/runtime.js");
const runLivePagePath = path.join(__dirname, "../../../pages/run-live.html");

function loadRuntimeMethods({ urlApi, documentApi } = {}) {
  const source = fs.readFileSync(runtimePath, "utf8");
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
  const helpers = Object.fromEntries(helperNames.map((name) => [name, jest.fn()]));
  helpers.renderMarkdown = jest.fn((value) => `<md>${String(value)}</md>`);
  const context = {
    window: {
      PSOPConsoleHelpers: helpers,
      PSOPRuntimeEvents: { mergeById, mergeBySeq }
    }
  };
  if (urlApi) {
    context.URL = urlApi;
  }
  if (documentApi) {
    context.document = documentApi;
  }
  context.setTimeout = setTimeout;
  context.clearTimeout = clearTimeout;
  vm.runInNewContext(source, context);
  return context.window.PSOPConsoleRuntimeMethods;
}

function createRuntimeHarness(options = {}) {
  return {
    ...loadRuntimeMethods(options),
    apiBaseUrl: "/api/v1",
    liveRun: { id: "run-1" },
    liveRunBindings: [],
    liveRunTerminalSession: null,
    liveRunTerminalEvents: [],
    liveRunTraceEvents: [],
    liveRunInteractionTab: "terminal",
    liveRunMountedTabs: { terminal: false, io: false, replay: false },
    liveRunLoadedRunId: "",
    liveRunTerminalEventsLoadedRunId: "",
    liveRunReplayLoadedRunId: "",
    replayDetail: null,
    selectedLiveRunProcessEventKey: "",
    selectedLiveRunReplayItemKey: "",
    terminalInputForm: { payload: "", attachments: [] },
    terminalMediaPreview: { open: false, kind: "", src: "", title: "", description: "" },
    route: { params: {} },
    busy: { liveRun: false, terminalInput: false },
    _liveRunLoadGeneration: 0,
    _liveRunWsHasOpened: false,
    _liveRunReplayRefreshTimer: null,
    _liveRunReplayGeneration: 0,
    $nextTick(callback) {
      callback();
    },
    connectRunWebSocket: jest.fn(),
    formatBytes(value) {
      return `${value} B`;
    },
    formatDateTime(value) {
      return value || "N/A";
    },
    terminalDirectionLabel(value) {
      return value === "output" ? "输出" : "输入";
    }
  };
}

test("terminal presentation kind is mutually exclusive and gives concrete MIME precedence", () => {
  const app = createRuntimeHarness();

  expect(app.terminalEventPartPresentationKind({ kind: "video", mime_type: "image/png" })).toBe("image");
  expect(app.terminalEventPartPresentationKind({ kind: "image", mime_type: "text/plain" })).toBe("text");
  expect(app.terminalEventPartPresentationKind({ kind: "audio", mime_type: "application/octet-stream" })).toBe("audio");
  expect(app.terminalEventPartPresentationKind({ kind: "file", mime_type: "application/pdf" })).toBe("pdf");
  expect(app.terminalEventPartPresentationKind({ kind: "unknown", mime_type: "application/zip" })).toBe("file");
  expect(app.terminalEventPresentationKind({ event_kind: "terminal.video.input.v1", mime_type: "image/jpeg" })).toBe("image");
  expect(app.terminalEventPresentationKind({ event_kind: "terminal.audio.input.v1", mime_type: "application/octet-stream" })).toBe("audio");

  const conflictedPart = { kind: "video", mime_type: "image/png" };
  expect([
    app.terminalEventPartIsText(conflictedPart),
    app.terminalEventPartIsImage(conflictedPart),
    app.terminalEventPartIsAudio(conflictedPart),
    app.terminalEventPartIsVideo(conflictedPart),
    app.terminalEventPartIsPdf(conflictedPart),
    app.terminalEventPartIsFile(conflictedPart)
  ].filter(Boolean)).toHaveLength(1);
});

test("run live media nodes are lazy, mutually gated, and use no metadata preload", () => {
  const html = fs.readFileSync(runLivePagePath, "utf8");
  const imageTags = html.match(/<img\b[^>]*>/g) || [];
  const audioVideoTags = html.match(/<(?:audio|video)\b[^>]*>/g) || [];

  expect(html).toContain('<template x-if="liveRunMountedTabs.terminal">');
  expect(html).toContain('<template x-if="liveRunMountedTabs.io">');
  expect(html).toContain('<template x-if="liveRunMountedTabs.replay">');
  expect(html).toContain('<template x-if="terminalMediaPreview.open">');
  expect(html).toContain('<template x-if="item.current">');
  expect(html).toContain('<template x-if="!item.current">');
  expect(html).not.toMatch(/x-show="[^"]*terminalEvent(?:Part)?(?:IsImage|IsAudio|IsVideo|IsPdf|MediaUrl)/);
  expect(html).not.toContain('preload="metadata"');
  expect(imageTags.length).toBeGreaterThan(0);
  imageTags.forEach((tag) => {
    expect(tag).toContain('loading="lazy"');
    expect(tag).toContain('decoding="async"');
  });
  audioVideoTags.forEach((tag) => expect(tag).toContain('preload="none"'));
});

test("direct replay loads replay on demand without loading terminal history or trace list", async () => {
  const app = createRuntimeHarness();
  const requests = [];
  app.liveRun = null;
  app.route = { params: { view: "replay" } };
  app.apiRequest = jest.fn(async (pathname) => {
    requests.push(pathname);
    if (pathname === "/runs/run-1") {
      return { id: "run-1", latest_terminal_seq: 0 };
    }
    if (pathname === "/runs/run-1/bindings") {
      return [];
    }
    if (pathname === "/terminal/sessions/run-1") {
      return { terminal_session: { id: "session-1", status: "open" } };
    }
    if (pathname === "/terminal/sessions/run-1/events") {
      return [{ id: "event-1", seq_no: 1, direction: "input", mime_type: "text/plain", payload_inline: "hello" }];
    }
    if (pathname === "/replay/runs/run-1") {
      return { run: { id: "run-1" }, timeline: [], terminal_events: [], trace_events: [], snapshots: [] };
    }
    throw new Error(`unexpected request: ${pathname}`);
  });

  await app.loadRunLive("run-1");

  expect(requests).toContain("/replay/runs/run-1");
  expect(requests).not.toContain("/terminal/sessions/run-1/events");
  expect(requests).not.toContain("/runs/run-1/trace-events");
  expect(app.liveRunMountedTabs).toEqual({ terminal: false, io: false, replay: true });

  await app.selectLiveRunInteractionTab("terminal");
  await app.selectLiveRunInteractionTab("replay");
  await app.loadRunLive("run-1");

  expect(requests.filter((pathname) => pathname === "/terminal/sessions/run-1/events")).toHaveLength(1);
  expect(requests.filter((pathname) => pathname === "/replay/runs/run-1")).toHaveLength(1);
  expect(requests.filter((pathname) => pathname === "/runs/run-1")).toHaveLength(1);
  expect(app.liveRunMountedTabs).toEqual({ terminal: true, io: false, replay: true });
});

test("terminal send path reconciles incrementally and never reloads the full run view", () => {
  const source = fs.readFileSync(runtimePath, "utf8");
  const sendMethod = source.slice(source.indexOf("async sendTerminalInput()"), source.indexOf("handleTerminalInputFile(event)"));

  expect(sendMethod).not.toContain("loadRunLive(");
  expect(sendMethod).toContain("refreshLiveRunSummary(runId)");
  expect(source).toContain("events?from_seq=${normalizedFromSeq}");
  expect(source).toContain("releaseTerminalEventObjectUrls");
  expect(source).toContain("URL.revokeObjectURL(url)");
});

test("a terminal response from a destroyed run cannot merge into the next run", async () => {
  const app = createRuntimeHarness();
  app.liveRun = { id: "run-1", latest_terminal_seq: 0, status: "waiting_input" };
  app.liveRunTerminalSession = { id: "session-1", status: "open" };
  app.terminalInputForm.payload = "旧运行输入";
  app.refreshLiveRunSummary = jest.fn(async () => {});
  let resolveRequest;
  app.apiRequest = jest.fn(() => new Promise((resolve) => { resolveRequest = resolve; }));

  const send = app.sendTerminalInput();
  await Promise.resolve();
  app.destroyLiveRunView();
  app.liveRun = { id: "run-2", latest_terminal_seq: 0, status: "waiting_input" };
  app.liveRunTerminalSession = { id: "session-2", status: "open" };
  resolveRequest({ event: { id: "event-old", run_id: "run-1", seq_no: 1, direction: "input" } });
  await send;

  expect(app.liveRunTerminalEvents).toEqual([]);
  expect(app.liveRun.id).toBe("run-2");
});

test("websocket sequence gaps trigger from-seq reconciliation for mounted terminal views", () => {
  const app = createRuntimeHarness();
  app.liveRunMountedTabs.terminal = true;
  app.liveRunTerminalEvents = [{ id: "event-1", seq_no: 1, direction: "input" }];
  app.reconcileLiveRunTerminalEvents = jest.fn(async () => {});

  app.handleRunWsEvent({
    event_type: "terminal.event.appended",
    payload: { id: "event-3", seq_no: 3, direction: "output" }
  });

  expect(app.reconcileLiveRunTerminalEvents).toHaveBeenCalledWith("run-1", 2);
  expect(app.liveRunTerminalEvents.map((event) => event.seq_no)).toEqual([1, 3]);
  expect(app.nextTerminalReconcileFromSeq()).toBe(2);
});

test("tab switches pause retained media and close transient overlays", async () => {
  const players = [{ pause: jest.fn() }, { pause: jest.fn() }];
  const documentApi = { querySelectorAll: jest.fn(() => players) };
  const app = createRuntimeHarness({ documentApi });
  app.closeTerminalMediaPreview = jest.fn();
  app.closeLiveRunProcessEventDrawer = jest.fn();
  app.ensureLiveRunInteractionTabData = jest.fn(async () => {});

  await app.selectLiveRunInteractionTab("io");

  players.forEach((player) => expect(player.pause).toHaveBeenCalledTimes(1));
  expect(app.closeTerminalMediaPreview).toHaveBeenCalledTimes(1);
  expect(app.closeLiveRunProcessEventDrawer).toHaveBeenCalledTimes(1);
});

test("websocket events from a stale run are ignored", () => {
  const app = createRuntimeHarness();
  app.mergeTerminalEvents = jest.fn();

  app.handleRunWsEvent(
    {
      event_type: "terminal.event.appended",
      run_id: "run-old",
      payload: { id: "event-old", run_id: "run-old", seq_no: 2 }
    },
    "run-old"
  );

  expect(app.mergeTerminalEvents).not.toHaveBeenCalled();
});

test("active replay schedules a debounced refresh when websocket data changes", () => {
  const app = createRuntimeHarness();
  app.liveRunInteractionTab = "replay";
  app.scheduleLiveRunReplayRefresh = jest.fn();

  app.handleRunWsEvent({
    event_type: "trace.event.appended",
    run_id: "run-1",
    payload: { id: "trace-2", run_id: "run-1", seq_no: 2 }
  });

  expect(app.scheduleLiveRunReplayRefresh).toHaveBeenCalledWith("run-1");
});

test("socket open reconciliation refreshes replay only when authoritative cursors changed", async () => {
  const app = createRuntimeHarness();
  app.liveRunInteractionTab = "replay";
  app.liveRun = { id: "run-1", latest_terminal_seq: 1, latest_trace_seq: 1, latest_snapshot_seq: 1, status: "running" };
  app.replayDetail = { run: { ...app.liveRun } };
  app.refreshLiveRunSummary = jest.fn(async () => {
    app.liveRun = { ...app.liveRun, latest_trace_seq: 2 };
  });
  app.scheduleLiveRunReplayRefresh = jest.fn();

  await app.reconcileLiveRunReplayAfterSocketOpen("run-1");

  expect(app.scheduleLiveRunReplayRefresh).toHaveBeenCalledWith("run-1");
});

test("replacing optimistic media and destroying the view revoke local object URLs", () => {
  const urlApi = { revokeObjectURL: jest.fn() };
  const app = createRuntimeHarness({ urlApi });
  app.liveRunTerminalEvents = [
    {
      id: "local-event",
      seq_no: 1,
      external_event_id: "web-1",
      _optimistic: true,
      parts: [
        {
          part_id: "image_1",
          kind: "image",
          mime_type: "image/png",
          _local_url: "blob:optimistic-image",
          metadata: { preview_url: "blob:optimistic-image" }
        }
      ]
    }
  ];

  app.mergeTerminalEvents([
    {
      id: "server-event",
      seq_no: 2,
      external_event_id: "web-1",
      direction: "input",
      mime_type: "image/png"
    }
  ]);

  expect(urlApi.revokeObjectURL).toHaveBeenCalledTimes(1);
  expect(urlApi.revokeObjectURL).toHaveBeenCalledWith("blob:optimistic-image");
  expect(app.liveRunTerminalEvents.map((event) => event.id)).toEqual(["server-event"]);

  app.terminalInputForm.attachments = [{ preview_url: "blob:pending-image" }];
  app.destroyLiveRunView();
  expect(urlApi.revokeObjectURL).toHaveBeenCalledWith("blob:pending-image");
  expect(app.liveRun).toBeNull();
  expect(app.liveRunMountedTabs).toEqual({ terminal: false, io: false, replay: false });
});

test("failed send can restore attachments without revoking their preview URLs", () => {
  const urlApi = { revokeObjectURL: jest.fn() };
  const app = createRuntimeHarness({ urlApi });
  app.liveRunTerminalEvents = [
    {
      id: "local-event",
      _optimistic: true,
      parts: [{ part_id: "image_1", kind: "image", _local_url: "blob:retry-image" }]
    }
  ];

  app.removeOptimisticTerminalEvent("local-event", { revokeObjectUrls: false });

  expect(urlApi.revokeObjectURL).not.toHaveBeenCalled();
  expect(app.liveRunTerminalEvents).toEqual([]);
});

test("retrying an attachment reuses its existing blob URL", () => {
  const urlApi = { createObjectURL: jest.fn(() => "blob:retry-image") };
  const app = createRuntimeHarness({ urlApi });
  const attachment = { file: { name: "photo.png", type: "image/png", size: 5 } };

  app.buildOptimisticTerminalInputEvent("run-1", "", [attachment]);
  app.buildOptimisticTerminalInputEvent("run-1", "", [attachment]);

  expect(urlApi.createObjectURL).toHaveBeenCalledTimes(1);
  expect(attachment.preview_url).toBe("blob:retry-image");
});

test("late run loads cannot overwrite a newer run", async () => {
  const app = createRuntimeHarness();
  app.liveRun = null;
  const deferred = new Map();
  const makeDeferred = (pathname) => {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    deferred.set(pathname, resolve);
    return promise;
  };
  app.apiRequest = jest.fn((pathname) => makeDeferred(pathname));
  app.ensureLiveRunInteractionTabData = jest.fn(async () => {});

  const oldLoad = app.loadRunLive("run-old");
  const newLoad = app.loadRunLive("run-new");
  deferred.get("/runs/run-new")({ id: "run-new" });
  deferred.get("/runs/run-new/bindings")([]);
  deferred.get("/terminal/sessions/run-new")({ terminal_session: { id: "session-new" } });
  await newLoad;
  deferred.get("/runs/run-old")({ id: "run-old" });
  deferred.get("/runs/run-old/bindings")([]);
  deferred.get("/terminal/sessions/run-old")({ terminal_session: { id: "session-old" } });
  await oldLoad;

  expect(app.liveRun.id).toBe("run-new");
  expect(app.liveRunLoadedRunId).toBe("run-new");
  expect(app.connectRunWebSocket).toHaveBeenCalledWith("run-new");
  expect(app.connectRunWebSocket).not.toHaveBeenCalledWith("run-old");
});

test("terminal image events expose media URL and description", () => {
  const app = createRuntimeHarness();
  const event = {
    id: "event-1",
    run_id: "run-1",
    artifact_object_id: "object-1",
    direction: "input",
    event_kind: "terminal.file.input.v1",
    mime_type: "application/octet-stream",
    payload_inline: {
      filename: "现场照片.jpg",
      description: "已拍摄接线端子。"
    }
  };

  expect(app.terminalEventIsImage(event)).toBe(true);
  expect(app.terminalEventMediaUrl(event)).toBe("/api/v1/terminal/sessions/run-1/events/event-1/content");
  expect(app.terminalEventDisplayText(event)).toBe("已拍摄接线端子。");
  expect(app.terminalEventShouldShowPlainText(event)).toBe(false);
});

test("terminal multipart events expose server-generated part media URLs", () => {
  const app = createRuntimeHarness();
  const event = {
    id: "event-4",
    run_id: "run-1",
    direction: "input",
    event_kind: "terminal.multimodal.input.v1",
    mime_type: "multipart/mixed",
    parts: [
      {
        part_id: "text_1",
        order_index: 1,
        kind: "text",
        mime_type: "text/plain",
        text: "现场说明"
      },
      {
        part_id: "image_1",
        order_index: 2,
        kind: "image",
        mime_type: "image/png",
        artifact_object_id: "object-4",
        metadata: { filename: "panel.png" }
      }
    ]
  };

  expect(app.terminalEventHasParts(event)).toBe(true);
  expect(app.terminalEventParts(event).map((part) => part.part_id)).toEqual(["text_1", "image_1"]);
  expect(app.terminalEventPartIsText(event.parts[0])).toBe(true);
  expect(app.terminalEventPartIsImage(event.parts[1])).toBe(true);
  expect(app.terminalEventPartDisplayText(event.parts[1])).toBe("");
  expect(app.terminalEventPartFileName(event.parts[1])).toBe("panel.png");
  expect(app.terminalEventPartMediaUrl(event, event.parts[1])).toBe(
    "/api/v1/terminal/sessions/run-1/events/event-4/parts/image_1/content"
  );
});

test("terminal text messages normalize escaped newlines before markdown rendering", () => {
  const app = createRuntimeHarness();
  const event = {
    id: "event-markdown",
    run_id: "run-1",
    direction: "output",
    event_kind: "terminal.text.output.v1",
    mime_type: "text/markdown",
    payload_inline: "请提交现场证据：\\n1. **配置清单**\\n2. 上传照片"
  };
  const part = {
    part_id: "text_1",
    kind: "text",
    mime_type: "text/markdown",
    text: "确认事项：\\n- CPU\\n- 电源"
  };

  expect(app.terminalEventDisplayText(event)).toBe("请提交现场证据：\n1. **配置清单**\n2. 上传照片");
  expect(app.terminalEventMarkdownHtml(event)).toBe("<md>请提交现场证据：\n1. **配置清单**\n2. 上传照片</md>");
  expect(app.terminalEventPartDisplayText(part)).toBe("确认事项：\n- CPU\n- 电源");
  expect(app.terminalEventPartMarkdownHtml(part)).toBe("<md>确认事项：\n- CPU\n- 电源</md>");
});

test("terminal JSON events render as structured payload", () => {
  const app = createRuntimeHarness();
  const event = {
    id: "event-2",
    run_id: "run-1",
    direction: "output",
    event_kind: "sensor.gps.reading.v1",
    mime_type: "application/json",
    payload_inline: {
      lat: 31.2,
      lng: 121.5
    }
  };

  expect(app.terminalEventShouldShowJson(event)).toBe(true);
  expect(app.terminalEventJsonText(event)).toContain("\"lat\": 31.2");
  expect(app.terminalEventActorLabel(event)).toBe("Runtime");
});

test("terminal transcript message widths separate metadata from bubble sizing", () => {
  const app = createRuntimeHarness();
  const runtimeEvent = { direction: "output" };
  const userEvent = { direction: "input" };

  expect(app.terminalEventMessageShellClass(runtimeEvent)).toBe("w-fit");
  expect(app.terminalEventMessageShellStyle(runtimeEvent)).toBe("max-width: 70%;");
  expect(app.terminalEventContentClass(runtimeEvent)).toBe("items-start");
  expect(app.terminalEventBubbleClass(runtimeEvent)).toContain("w-fit");
  expect(app.terminalEventMessageShellClass(userEvent)).toBe("w-fit");
  expect(app.terminalEventMessageShellStyle(userEvent)).toBe("max-width: 70%;");
  expect(app.terminalEventContentClass(userEvent)).toBe("items-end");
  expect(app.terminalEventBubbleClass(userEvent)).toContain("w-fit");
});

test("terminal PDF events render inline instead of as generic downloads", () => {
  const app = createRuntimeHarness();
  const event = {
    id: "event-3",
    run_id: "run-1",
    artifact_object_id: "object-3",
    direction: "input",
    event_kind: "terminal.file.input.v1",
    mime_type: "application/pdf",
    payload_inline: {
      filename: "manual.pdf",
      size_bytes: 2048
    }
  };

  expect(app.terminalEventIsPdf(event)).toBe(true);
  expect(app.terminalEventIsGenericFile(event)).toBe(false);
  expect(app.terminalEventFileName(event)).toBe("manual.pdf");
  expect(app.terminalEventFileMeta(event)).toBe("2048 B");
  expect(app.terminalEventFileIcon(event)).toBe("picture_as_pdf");
});

test("live run process lanes infer terminal input and output content kinds", () => {
  const app = createRuntimeHarness();
  app.liveRun = { id: "run-1", started_at: "2026-05-13T00:00:00Z" };
  app.liveRunTerminalEvents = [
    {
      id: "input-1",
      seq_no: 1,
      direction: "input",
      event_kind: "terminal.text.input.v1",
      mime_type: "text/plain",
      occurred_at: "2026-05-13T00:00:20Z",
      payload_inline: "现场已准备"
    },
    {
      id: "output-1",
      seq_no: 2,
      direction: "output",
      event_kind: "terminal.text.output.v1",
      mime_type: "text/plain",
      occurred_at: "2026-05-13T00:00:45Z",
      payload_inline: "第一步：检查连接件。"
    },
    {
      id: "input-2",
      seq_no: 3,
      direction: "input",
      event_kind: "terminal.multimodal.input.v1",
      mime_type: "multipart/mixed",
      occurred_at: "2026-05-13T00:01:00Z",
      parts: [
        { part_id: "text_1", kind: "text", mime_type: "text/plain", text: "补充照片" },
        { part_id: "image_1", kind: "image", mime_type: "image/png", artifact_object_id: "object-1", metadata: { filename: "panel.png" } }
      ]
    },
    {
      id: "output-2",
      seq_no: 4,
      direction: "output",
      event_kind: "terminal.image.output.v1",
      mime_type: "image/png",
      artifact_object_id: "object-2",
      occurred_at: "2026-05-13T00:01:30Z",
      payload_inline: { filename: "annotated.png" }
    },
    {
      id: "output-3",
      seq_no: 5,
      direction: "output",
      event_kind: "runtime.data.output.v1",
      mime_type: "application/json",
      occurred_at: "2026-05-13T00:02:00Z",
      payload_inline: { status: "ok" }
    }
  ];

  expect(app.liveRunProcessLanes().map((lane) => lane.id)).toEqual([
    "input.text",
    "input.mixed",
    "output.text",
    "output.image",
    "output.data"
  ]);
  expect(app.liveRunProcessEventKind(app.liveRunTerminalEvents[2])).toBe("mixed");
  expect(app.liveRunProcessEventSummary(app.liveRunTerminalEvents[2])).toBe("补充照片 + panel.png");
  expect(app.liveRunProcessEventKind(app.liveRunTerminalEvents[3])).toBe("image");
  expect(app.liveRunProcessEventAtMs(app.liveRunTerminalEvents[0])).toBe(0);
  expect(app.liveRunProcessEventAtMs(app.liveRunTerminalEvents[1])).toBe(25000);
  expect(app.liveRunProcessTimelineEventLeftStyle(app.liveRunTerminalEvents[4])).toBe("left: clamp(4rem, 100%, calc(100% - 4rem))");
  expect(app.liveRunProcessLaneIcon({ kind: "text" })).toBe("text_fields");
  expect(app.liveRunProcessLaneTone("input.text")).toContain("border-orange-500/30");
  expect(app.liveRunProcessLaneTone("input.image")).toContain("border-emerald-500/25");
  expect(app.liveRunProcessLaneTone("output.text")).toContain("border-cyan-500/30");
  expect(app.liveRunProcessEventFrameTone(app.liveRunTerminalEvents[1])).toContain("border-cyan-500/45");
});

test("live run process selection exposes global previous and next nodes", () => {
  const app = createRuntimeHarness();
  app.liveRunTerminalEvents = [
    {
      id: "input-1",
      seq_no: 1,
      direction: "input",
      event_kind: "terminal.text.input.v1",
      mime_type: "text/plain",
      occurred_at: "2026-05-13T00:00:20Z",
      payload_inline: "用户输入"
    },
    {
      id: "output-1",
      seq_no: 2,
      direction: "output",
      event_kind: "terminal.text.output.v1",
      mime_type: "text/plain",
      occurred_at: "2026-05-13T00:00:45Z",
      payload_inline: "Runtime 输出"
    },
    {
      id: "input-2",
      seq_no: 3,
      direction: "input",
      event_kind: "terminal.text.input.v1",
      mime_type: "text/plain",
      occurred_at: "2026-05-13T00:01:00Z",
      payload_inline: "继续输入"
    }
  ];

  app.openLiveRunProcessEventDrawer(app.liveRunTerminalEvents[1]);

  expect(app.selectedLiveRunProcessEvent().id).toBe("output-1");
  expect(app.liveRunProcessNeighborItems().map((item) => item.event?.id || null)).toEqual(["input-1", "output-1", "input-2"]);
  expect(app.liveRunProcessNeighborItems().map((item) => item.label)).toEqual(["上一个", "当前", "下一个"]);
  expect(app.liveRunProcessEventDetailText(app.liveRunTerminalEvents[1])).toBe("Runtime 输出");

  app.closeLiveRunProcessEventDrawer();
  expect(app.selectedLiveRunProcessEventKey).toBe("");
  app.selectLiveRunProcessEvent(app.liveRunTerminalEvents[1]);

  app.liveRunTerminalEvents = [app.liveRunTerminalEvents[0]];
  app.ensureLiveRunProcessSelection();
  expect(app.selectedLiveRunProcessEventKey).toBe("");
});
