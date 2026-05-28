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

test("terminal image events expose media URL and caption", () => {
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
      caption: "已拍摄接线端子。"
    }
  };

  expect(app.terminalEventIsImage(event)).toBe(true);
  expect(app.terminalEventMediaUrl(event)).toBe("/api/v1/terminal/sessions/run-1/events/event-1/content");
  expect(app.terminalEventDisplayText(event)).toBe("已拍摄接线端子。");
  expect(app.terminalEventShouldShowPlainText(event)).toBe(false);
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
