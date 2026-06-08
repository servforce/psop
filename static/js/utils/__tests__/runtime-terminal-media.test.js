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
  const context = {
    window: {
      PSOPConsoleHelpers: Object.fromEntries(helperNames.map((name) => [name, jest.fn()]))
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
    liveRunEvents: [],
    selectedLiveRunProcessEventKey: "",
    formatBytes(value) {
      return `${value} B`;
    },
    formatDateTime(value) {
      return value || "N/A";
    },
    runEventDirectionLabel(value) {
      return value === "output" ? "输出" : "输入";
    }
  };
}

test("run event image events expose media URL and description", () => {
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

  expect(app.runEventIsImage(event)).toBe(true);
  expect(app.runEventMediaUrl(event)).toBe("/api/v1/runs/run-1/events/event-1/content");
  expect(app.runEventDisplayText(event)).toBe("已拍摄接线端子。");
  expect(app.runEventShouldShowPlainText(event)).toBe(false);
});

test("RunEvent multipart events expose server-generated part media URLs", () => {
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

  expect(app.runEventHasParts(event)).toBe(true);
  expect(app.runEventParts(event).map((part) => part.part_id)).toEqual(["text_1", "image_1"]);
  expect(app.runEventPartIsText(event.parts[0])).toBe(true);
  expect(app.runEventPartIsImage(event.parts[1])).toBe(true);
  expect(app.runEventPartDisplayText(event.parts[1])).toBe("");
  expect(app.runEventPartFileName(event.parts[1])).toBe("panel.png");
  expect(app.runEventPartMediaUrl(event, event.parts[1])).toBe(
    "/api/v1/runs/run-1/events/event-4/parts/image_1/content"
  );
});

test("RunEvent JSON events render as structured payload", () => {
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

  expect(app.runEventShouldShowJson(event)).toBe(true);
  expect(app.runEventJsonText(event)).toContain("\"lat\": 31.2");
  expect(app.runEventActorLabel(event)).toBe("Runtime");
});

test("RunEvent transcript message widths separate metadata from bubble sizing", () => {
  const app = createRuntimeHarness();
  const runtimeEvent = { direction: "output" };
  const userEvent = { direction: "input" };

  expect(app.runEventMessageShellClass(runtimeEvent)).toBe("w-fit");
  expect(app.runEventMessageShellStyle(runtimeEvent)).toBe("max-width: 70%;");
  expect(app.runEventContentClass(runtimeEvent)).toBe("items-start");
  expect(app.runEventBubbleClass(runtimeEvent)).toContain("w-fit");
  expect(app.runEventMessageShellClass(userEvent)).toBe("w-fit");
  expect(app.runEventMessageShellStyle(userEvent)).toBe("max-width: 70%;");
  expect(app.runEventContentClass(userEvent)).toBe("items-end");
  expect(app.runEventBubbleClass(userEvent)).toContain("w-fit");
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

  expect(app.runEventIsPdf(event)).toBe(true);
  expect(app.runEventIsGenericFile(event)).toBe(false);
  expect(app.runEventFileName(event)).toBe("manual.pdf");
  expect(app.runEventFileMeta(event)).toBe("2048 B");
  expect(app.runEventFileIcon(event)).toBe("picture_as_pdf");
});

test("live run process lanes infer terminal input and output content kinds", () => {
  const app = createRuntimeHarness();
  app.liveRun = { id: "run-1", started_at: "2026-05-13T00:00:00Z" };
  app.liveRunEvents = [
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
  expect(app.liveRunProcessEventKind(app.liveRunEvents[2])).toBe("mixed");
  expect(app.liveRunProcessEventSummary(app.liveRunEvents[2])).toBe("补充照片 + panel.png");
  expect(app.liveRunProcessEventKind(app.liveRunEvents[3])).toBe("image");
  expect(app.liveRunProcessEventAtMs(app.liveRunEvents[0])).toBe(0);
  expect(app.liveRunProcessEventAtMs(app.liveRunEvents[1])).toBe(25000);
  expect(app.liveRunProcessTimelineEventLeftStyle(app.liveRunEvents[4])).toBe("left: clamp(4rem, 100%, calc(100% - 4rem))");
  expect(app.liveRunProcessLaneIcon({ kind: "text" })).toBe("text_fields");
  expect(app.liveRunProcessLaneTone("input.text")).toContain("border-orange-500/30");
  expect(app.liveRunProcessLaneTone("input.image")).toContain("border-emerald-500/25");
  expect(app.liveRunProcessLaneTone("output.text")).toContain("border-cyan-500/30");
  expect(app.liveRunProcessEventFrameTone(app.liveRunEvents[1])).toContain("border-cyan-500/45");
});

test("live run process selection exposes global previous and next nodes", () => {
  const app = createRuntimeHarness();
  app.liveRunEvents = [
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

  app.openLiveRunProcessEventDrawer(app.liveRunEvents[1]);

  expect(app.selectedLiveRunProcessEvent().id).toBe("output-1");
  expect(app.liveRunProcessNeighborItems().map((item) => item.event?.id || null)).toEqual(["input-1", "output-1", "input-2"]);
  expect(app.liveRunProcessNeighborItems().map((item) => item.label)).toEqual(["上一个", "当前", "下一个"]);
  expect(app.liveRunProcessEventDetailText(app.liveRunEvents[1])).toBe("Runtime 输出");

  app.closeLiveRunProcessEventDrawer();
  expect(app.selectedLiveRunProcessEventKey).toBe("");
  app.selectLiveRunProcessEvent(app.liveRunEvents[1]);

  app.liveRunEvents = [app.liveRunEvents[0]];
  app.ensureLiveRunProcessSelection();
  expect(app.selectedLiveRunProcessEventKey).toBe("");
});
