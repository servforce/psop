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
    formatBytes(value) {
      return `${value} B`;
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
