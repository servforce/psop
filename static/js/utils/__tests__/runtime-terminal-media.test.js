const fs = require("fs");
const path = require("path");
const vm = require("vm");

const runtimePath = path.join(__dirname, "../../app/runtime.js");

function loadRuntimeMethods() {
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
      PSOPConsoleHelpers: helpers
    }
  };
  vm.runInNewContext(source, context);
  return context.window.PSOPConsoleRuntimeMethods;
}

function createRuntimeHarness() {
  return {
    ...loadRuntimeMethods(),
    apiBaseUrl: "/api/v1",
    liveRun: { id: "run-1" },
    liveRunTerminalEvents: [],
    selectedLiveRunProcessEventKey: "",
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
