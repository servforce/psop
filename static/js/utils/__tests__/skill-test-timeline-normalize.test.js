const fs = require("fs");
const path = require("path");
const vm = require("vm");

const skillTestAppPath = path.join(__dirname, "../../app/skill-test.js");

function loadSkillTestMethods() {
  const source = fs.readFileSync(skillTestAppPath, "utf8");
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
      PSOPConsoleHelpers: Object.fromEntries(helperNames.map((name) => [name, jest.fn()])),
      PSOPRuntimeEvents: {
        mergeById: (existing = [], incoming = []) => incoming.reduce((items, item) => {
          const index = items.findIndex((existingItem) => existingItem.id === item.id);
          if (index >= 0) {
            items[index] = { ...items[index], ...item };
          } else {
            items.push(item);
          }
          return items;
        }, [...existing]),
        mergeBySeq: (existing = [], incoming = []) => [...existing, ...incoming].sort((left, right) => Number(left.seq_no || 0) - Number(right.seq_no || 0))
      }
    }
  };
  vm.runInNewContext(source, context);
  return context.window.PSOPConsoleSkillTestMethods;
}

function createTimelineHarness() {
  const methods = loadSkillTestMethods();
  return {
    ...methods,
    skillTestCaseForm: {
      duration_ms: 600000,
      timeline_json: ""
    },
    selectedSkillTestTimelineEventId: "",
    selectedSkillTestTimelineEventIds: [],
    skillTestTimelineEventDraft: null,
    skillTestScenarioDetailPanel: "info",
    selectedSkillTestTimelineLaneId: "",
    skillTestTimelineDragState: null,
    skillTestReviewExpandedEventKey: "",
    skillTestReviewPanelTab: "transcript",
    skillTestReviewDetailTab: "transcript",
    skillTestScenarioInfoTab: "basic",
    selectedSkillTestReviewExpectationId: "",
    selectedSkillTestReviewLaneId: "",
    confirmDangerAction: jest.fn(() => true),
    formatStatus(value) {
      return value || "未知";
    },
    formatDateTime(value) {
      return value || "N/A";
    },
    formatTerminalPayload(value) {
      if (typeof value === "string") {
        return value;
      }
      if (value === null || value === undefined) {
        return "";
      }
      return JSON.stringify(value, null, 2);
    },
    terminalDirectionLabel(value) {
      return value === "output" ? "输出" : "输入";
    },
    statusBadgeTone() {
      return "";
    }
  };
}

function duplicateSemanticTimeline() {
  return {
    schema_version: "psop-skill-test-timeline/v1",
    duration_ms: 600000,
    lanes: [
      { id: "input.text", kind: "input", label: "文本" },
      { id: "expected.semantic", kind: "output", label: "文本" }
    ],
    events: [
      {
        id: "input_1",
        lane_id: "input.text",
        at_ms: 0,
        payload_inline: "已准备就绪，可以开始"
      },
      {
        id: "expected_9",
        lane_id: "expected.semantic",
        at_ms: 190000,
        expectation: "引导用户进行下一步操作"
      },
      {
        id: "expected_10",
        lane_id: "expected.semantic",
        at_ms: 362000,
        expectation: "描述该时间点以前应满足的文本输出"
      },
      {
        id: "expected_9",
        lane_id: "expected.semantic",
        at_ms: 355000,
        expectation: "重复 ID 的历史语义输出"
      }
    ]
  };
}

test("timeline normalization repairs duplicate semantic event ids for rendering", () => {
  const app = createTimelineHarness();
  const timeline = duplicateSemanticTimeline();

  const normalized = app.normalizeSkillTestTimelineDraft(timeline);
  const semanticEvents = normalized.events.filter((event) => event.lane_id === "expected.semantic");
  const semanticIds = semanticEvents.map((event) => event.id);

  expect(semanticEvents).toHaveLength(3);
  expect(new Set(semanticIds).size).toBe(semanticIds.length);
  expect(semanticIds).toContain("expected_9");
  expect(semanticIds).toContain("expected_10");
  expect(semanticIds.some((id) => id !== "expected_9" && id !== "expected_10")).toBe(true);

  app.skillTestCaseForm.timeline_json = JSON.stringify(timeline);
  const laneItems = app.skillTestTimelineEventsForLane("expected.semantic");
  const renderKeys = laneItems.map((item) => item.render_key);

  expect(laneItems).toHaveLength(3);
  expect(new Set(renderKeys).size).toBe(renderKeys.length);
  expect(laneItems.map((item) => app.skillTestTimelineEventLabel(item.event))).toEqual([
    "引导用户进行下一步操作",
    "重复 ID 的历史语义输出",
    "描述该时间点以前应满足的文本输出"
  ]);
});

test("saving a timeline writes repaired unique ids back into advanced json", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());

  const parsed = app.parseSkillTestTimeline();
  const parsedIds = parsed.events.map((event) => event.id);
  const formIds = JSON.parse(app.skillTestCaseForm.timeline_json).events.map((event) => event.id);

  expect(new Set(parsedIds).size).toBe(parsedIds.length);
  expect(formIds).toEqual(parsedIds);
});

test("timeline event icons use lane defaults and schedule icon for trigger events", () => {
  const app = createTimelineHarness();

  expect(app.skillTestTimelineEventIcon({ lane_id: "input.text", event_kind: "terminal.text.input.v1" })).toBe("text_fields");
  expect(app.skillTestTimelineEventIcon({ lane_id: "sensor.gps", event_kind: "sensor.gps.reading.v1" })).toBe("location_on");
  expect(app.skillTestTimelineEventIcon({ lane_id: "sensor.pose3d", event_kind: "sensor.pose3d.reading.v1" })).toBe("view_in_ar");
  expect(app.skillTestTimelineEventIcon({ lane_id: "expected.semantic" })).toBe("fact_check");
  expect(app.skillTestTimelineEventIcon({ lane_id: "input.text", event_kind: "terminal.schedule.trigger.v1" })).toBe("schedule");
  expect(app.skillTestTimelineEventIcon({ lane_id: "input.text", trigger_event_id: "expected_1" })).toBe("schedule");
});

test("default timeline and sensor events normalize structured payloads", () => {
  const app = createTimelineHarness();
  const defaultLaneIds = app.defaultSkillTestTimeline().lanes.map((lane) => lane.id);

  expect(defaultLaneIds.slice(0, 2)).toEqual(["sensor.gps", "sensor.pose3d"]);
  expect(app.defaultSkillTestTimeline().lanes.find((lane) => lane.id === "expected.semantic").label).toBe("文本");
  expect(app.skillTestTimelineLaneLabel({ id: "expected.semantic", label: "语义" })).toBe("文本");

  app.skillTestCaseForm.timeline_json = JSON.stringify({
    schema_version: "psop-skill-test-timeline/v1",
    duration_ms: 600000,
    lanes: [
      { id: "input.text", kind: "input", label: "文本" },
      { id: "expected.semantic", kind: "output", label: "语义" },
      { id: "custom.telemetry", kind: "input", label: "自定义" }
    ],
    events: [
      {
        id: "gps_1",
        lane_id: "sensor.gps",
        at_ms: 1000,
        payload_inline: { latitude: "31.23", longitude: "121.47", accuracy_m: "2.5" }
      },
      {
        id: "pose_1",
        lane_id: "sensor.pose3d",
        at_ms: 2000,
        payload_inline: { x: "1", y: "2", z: "3", yaw: "90" }
      }
    ]
  });

  const laneIds = app.skillTestTimelineLanes().map((lane) => lane.id);
  expect(laneIds.slice(0, 2)).toEqual(["sensor.gps", "sensor.pose3d"]);
  expect(laneIds).toContain("custom.telemetry");
  expect(app.skillTestTimelineLanes().find((lane) => lane.id === "expected.semantic").label).toBe("文本");
  const events = app.skillTestTimelineEvents();
  expect(events[0]).toEqual(
    expect.objectContaining({
      lane_id: "sensor.gps",
      event_kind: "sensor.gps.reading.v1",
      mime_type: "application/json",
      payload_inline: expect.objectContaining({ latitude: 31.23, longitude: 121.47, accuracy_m: 2.5 })
    })
  );
  expect(events[1].payload_inline).toEqual(expect.objectContaining({ x: 1, y: 2, z: 3, yaw: 90 }));
  expect(app.skillTestTimelineEventLabel(events[0])).toBe("31.23, 121.47");
  expect(app.skillTestTimelineEventLabel(events[1])).toBe("x 1 / y 2 / z 3");
});

test("sensor event editor updates payload fields without converting the lane", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify({
    schema_version: "psop-skill-test-timeline/v1",
    duration_ms: 600000,
    events: [
      {
        id: "gps_1",
        lane_id: "sensor.gps",
        at_ms: 1000,
        payload_inline: { latitude: 0, longitude: 0 }
      }
    ]
  });

  app.selectSkillTestTimelineEvent("gps_1");
  app.updateSkillTestTimelineSensorField("latitude", "31.2304");
  app.updateSkillTestTimelineSensorField("longitude", "121.4737");
  app.updateSkillTestTimelineSensorField("accuracy_m", "2.5");
  app.flushSkillTestTimelineEventDraft();

  const event = app.skillTestTimelineEvents()[0];
  expect(event.lane_id).toBe("sensor.gps");
  expect(event.payload_inline).toEqual({ latitude: 31.2304, longitude: 121.4737, accuracy_m: 2.5 });
});

test("right-panel event editor keeps blank track clicks available", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());
  app.selectedSkillTestTimelineEventId = "expected_9";
  app.selectedSkillTestTimelineEventIds = ["expected_9"];
  app.skillTestScenarioDetailPanel = "event";
  app.skillTestCase = { id: "scenario_1" };
  app.route = { name: "skill-test-scenario" };

  const beforeCount = app.skillTestTimelineEvents().length;

  expect(app.canAddSkillTestTimelineEventToLane("expected.semantic")).toBe(true);
  expect(app.canAddSkillTestTimelineEventToLane("input.text")).toBe(true);

  app.addSkillTestTimelineEventFromLane({}, "expected.semantic");

  expect(app.skillTestTimelineEvents()).toHaveLength(beforeCount + 1);
  expect(app.selectedSkillTestTimelineEventId).not.toBe("expected_9");
  expect(app.skillTestScenarioDetailPanel).toBe("event");
});

test("inline new-scenario editor still blocks blank track clicks on the expanded lane", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());
  app.selectedSkillTestTimelineEventId = "expected_9";
  app.selectedSkillTestTimelineEventIds = ["expected_9"];
  app.route = { name: "skill-test-scenario-new" };

  const beforeCount = app.skillTestTimelineEvents().length;

  expect(app.canAddSkillTestTimelineEventToLane("expected.semantic")).toBe(false);
  expect(app.canAddSkillTestTimelineEventToLane("input.text")).toBe(true);

  app.addSkillTestTimelineEventFromLane({}, "expected.semantic");

  expect(app.skillTestTimelineEvents()).toHaveLength(beforeCount);
});

test("selected timeline events move together while dragging", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());
  app.selectedSkillTestTimelineEventIds = ["input_1", "expected_9"];
  app.skillTestTimelineDragState = {
    eventId: "expected_9",
    eventIds: ["input_1", "expected_9"],
    anchorStartAtMs: 190000,
    initialEvents: [
      { id: "input_1", at_ms: 0 },
      { id: "expected_9", at_ms: 190000 }
    ]
  };

  app.setSkillTestTimelineDragGroupAtFromTrack(
    { clientX: 50 },
    {
      getBoundingClientRect: () => ({ left: 0, width: 100 })
    }
  );

  const events = app.skillTestTimelineEvents();
  expect(events.find((event) => event.id === "input_1").at_ms).toBe(110000);
  expect(events.find((event) => event.id === "expected_9").at_ms).toBe(300000);
});

test("modifier timeline clicks toggle multi-selection without opening the editor", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());

  app.handleSkillTestTimelineEventClick({ ctrlKey: true }, "input_1");
  app.handleSkillTestTimelineEventClick({ shiftKey: true }, "expected_9");

  expect(app.selectedSkillTestTimelineEventId).toBe("");
  expect(app.selectedSkillTestTimelineEventIds).toEqual(["input_1", "expected_9"]);

  app.handleSkillTestTimelineEventClick({}, "input_1");

  expect(app.selectedSkillTestTimelineEventId).toBe("input_1");
  expect(app.selectedSkillTestTimelineEventIds).toEqual(["input_1"]);
});

test("backspace key confirms and removes selected timeline events outside editable fields", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());
  app.route = { name: "skill-test-scenario" };
  app.selectedSkillTestTimelineEventId = "input_1";
  app.selectedSkillTestTimelineEventIds = ["input_1", "expected_9"];
  app.skillTestTimelineEventDraft = { id: "input_1", payload_inline: "draft text" };
  app.skillTestScenarioDetailPanel = "event";

  const keyboardEvent = {
    key: "Backspace",
    target: { nodeType: 1, tagName: "DIV", closest: jest.fn(() => null) },
    preventDefault: jest.fn(),
    stopPropagation: jest.fn()
  };

  expect(app.handleSkillTestTimelineKeyboardShortcut(keyboardEvent)).toBe(true);
  expect(keyboardEvent.preventDefault).toHaveBeenCalled();
  expect(keyboardEvent.stopPropagation).toHaveBeenCalled();
  expect(app.confirmDangerAction).toHaveBeenCalledWith("确认删除事件？此操作可能无法撤销。");
  expect(app.skillTestTimelineEvents().map((event) => event.id)).not.toEqual(expect.arrayContaining(["input_1", "expected_9"]));
  expect(app.selectedSkillTestTimelineEventId).toBe("");
  expect(app.selectedSkillTestTimelineEventIds).toEqual([]);
  expect(app.skillTestTimelineEventDraft).toBeNull();
  expect(app.skillTestScenarioDetailPanel).toBe("info");
});

test("backspace key keeps timeline events when deletion is canceled", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());
  app.route = { name: "skill-test-scenario" };
  app.selectedSkillTestTimelineEventId = "input_1";
  app.selectedSkillTestTimelineEventIds = ["input_1"];
  app.confirmDangerAction = jest.fn(() => false);

  const keyboardEvent = {
    key: "Backspace",
    target: { nodeType: 1, tagName: "DIV", closest: jest.fn(() => null) },
    preventDefault: jest.fn(),
    stopPropagation: jest.fn()
  };

  expect(app.handleSkillTestTimelineKeyboardShortcut(keyboardEvent)).toBe(false);
  expect(keyboardEvent.preventDefault).toHaveBeenCalled();
  expect(keyboardEvent.stopPropagation).toHaveBeenCalled();
  expect(app.confirmDangerAction).toHaveBeenCalledWith("确认删除事件？此操作可能无法撤销。");
  expect(app.skillTestTimelineEvents().map((event) => event.id)).toContain("input_1");
});

test("backspace key keeps timeline events while editing form fields", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());
  app.route = { name: "skill-test-scenario" };
  app.selectedSkillTestTimelineEventId = "input_1";
  app.selectedSkillTestTimelineEventIds = ["input_1"];

  const keyboardEvent = {
    key: "Backspace",
    target: { nodeType: 1, tagName: "TEXTAREA", closest: jest.fn(() => null) },
    preventDefault: jest.fn(),
    stopPropagation: jest.fn()
  };

  expect(app.handleSkillTestTimelineKeyboardShortcut(keyboardEvent)).toBe(false);
  expect(keyboardEvent.preventDefault).not.toHaveBeenCalled();
  expect(app.confirmDangerAction).not.toHaveBeenCalled();
  expect(app.skillTestTimelineEvents().map((event) => event.id)).toContain("input_1");
});

test("timeline event editor keeps the original channel immutable", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());

  app.selectSkillTestTimelineEvent("input_1");
  app.updateSkillTestTimelineEventDraft("lane_id", "input.image");

  expect(app.skillTestTimelineEventDraft.lane_id).toBe("input.text");

  app.skillTestTimelineEventDraft = {
    ...app.skillTestTimelineEventDraft,
    lane_id: "input.image"
  };
  app.flushSkillTestTimelineEventDraft();

  expect(app.skillTestTimelineEvents().find((event) => event.id === "input_1").lane_id).toBe("input.text");

  const eventIndex = app.skillTestTimelineEvents().findIndex((event) => event.id === "input_1");
  app.updateSkillTestTimelineEvent(eventIndex, "lane_id", "input.audio");

  expect(app.skillTestTimelineEvents().find((event) => event.id === "input_1").lane_id).toBe("input.text");
});

test("multimodal timeline events resolve preview urls from bound assets", () => {
  const app = createTimelineHarness();
  app.apiBaseUrl = "/api/v1";
  app.currentSkill = { id: "skill-1" };
  app.skillTestCase = { id: "scenario-1", skill_definition_id: "skill-1" };
  app.skillTestDataObjects = [
    {
      id: "asset-1",
      skill_definition_id: "skill-1",
      scenario_id: "scenario-1",
      name: "现场图片",
      filename: "site.png",
      mime_type: "image/png",
      size_bytes: 9
    }
  ];
  app.formatBytes = (value) => `${value} B`;

  const event = { id: "image_1", lane_id: "input.image", asset_id: "asset-1", mime_type: "image/*" };

  expect(app.skillTestTimelineEventAsset(event).filename).toBe("site.png");
  expect(app.skillTestTimelineAssetPreviewKind(event)).toBe("image");
  expect(app.skillTestTimelineAssetPreviewUrl(event)).toBe(
    "/api/v1/skills/skill-1/test-scenarios/scenario-1/assets/asset-1/content"
  );
  expect(app.skillTestTimelineAssetPreviewMeta(event)).toBe("image/png · 9 B");
});

test("timeline input events can hold ordered text and asset parts", () => {
  const app = createTimelineHarness();
  app.apiBaseUrl = "/api/v1";
  app.currentSkill = { id: "skill-1" };
  app.skillTestCase = { id: "scenario-1", skill_definition_id: "skill-1" };
  app.skillTestDataObjects = [
    {
      id: "asset-image",
      skill_definition_id: "skill-1",
      scenario_id: "scenario-1",
      name: "面板照片",
      filename: "panel.png",
      mime_type: "image/png",
      size_bytes: 9
    },
    {
      id: "asset-video",
      skill_definition_id: "skill-1",
      scenario_id: "scenario-1",
      name: "启动视频",
      filename: "startup.mp4",
      mime_type: "video/mp4",
      size_bytes: 12
    }
  ];

  const timeline = app.normalizeSkillTestTimelineDraft({
    schema_version: "psop-skill-test-timeline/v1",
    duration_ms: 600000,
    events: [
      {
        id: "site_bundle",
        lane_id: "input.text",
        at_ms: 0,
        parts: [
          { part_id: "text_1", kind: "text", text: "现场说明" },
          { part_id: "image_1", kind: "image", asset_id: "asset-image" },
          { part_id: "video_1", kind: "video", asset_id: "asset-video" }
        ]
      }
    ]
  });
  const event = timeline.events[0];

  expect(event.event_kind).toBe("terminal.multimodal.input.v1");
  expect(event.mime_type).toBe("multipart/mixed");
  expect(app.skillTestTimelineEventParts(event).map((part) => part.part_id)).toEqual(["text_1", "image_1", "video_1"]);
  expect(app.skillTestTimelineEventLabel(event)).toBe("现场说明 + 面板照片 + 启动视频");
  expect(app.skillTestTimelineEventTextValue(event)).toBe("现场说明");
  expect(app.skillTestTimelinePartPreviewUrl(event.parts[1])).toBe(
    "/api/v1/skills/skill-1/test-scenarios/scenario-1/assets/asset-image/content"
  );

  const remapped = app.skillTestTimelineWithAssetIdMap(timeline, {
    "asset-image": "server-image",
    "asset-video": "server-video"
  });
  expect(remapped.events[0].parts.map((part) => part.asset_id).filter(Boolean)).toEqual(["server-image", "server-video"]);
});

test("review playback marks events as they reach the playhead", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    driver_events: [
      {
        event_id: "input_1",
        status: "sent"
      }
    ],
    expectation_evaluations: [
      {
        expectation_id: "expected_9",
        status: "passed"
      }
    ]
  };
  app.skillTestReview.scenario_timeline.events.push({
    id: "input_future",
    lane_id: "input.text",
    at_ms: 300000,
    payload_inline: "稍后的文本输入"
  });
  app.skillTestReviewPlayheadMs = 0;

  expect(app.skillTestReviewDurationMs()).toBe(600000);
  expect(app.skillTestReviewEventsForLane("expected.semantic")).toHaveLength(3);
  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("not_occurred");
  expect(app.skillTestReviewEventStatusLabel(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("未判定");
  expect(app.skillTestReviewEventStatusLabel(app.skillTestReviewEventsForLane("input.text")[1].event)).toBe("未发送");

  app.updateSkillTestReviewPlayhead(190000);

  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("input.text")[0].event)).toBe("sent");
  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("passed");
  expect(app.skillTestReviewAssertionVerdictLabel(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("符合预期");
  expect(app.skillTestReviewEventStatusLabel(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("符合预期");

  app.skillTestReview.expectation_evaluations[0].status = "inconclusive";

  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("inconclusive");
  expect(app.skillTestReviewAssertionVerdictLabel(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("未能判定");
  expect(app.skillTestReviewEventStatusLabel(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("未能判定");
});

test("review fork cursor keeps the selected playhead time", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    cursor_anchors: [
      { time_ms: 0, terminal_seq: 1, snapshot_seq: 1 },
      { time_ms: 190000, terminal_seq: 4, snapshot_seq: 3 },
      { time_ms: 362000, terminal_seq: 9, snapshot_seq: 5 }
    ]
  };

  app.updateSkillTestReviewPlayhead(300000);

  expect(app.currentSkillTestForkCursor()).toEqual({
    time_ms: 300000,
    terminal_seq: 4,
    snapshot_seq: 3
  });

  app.updateSkillTestReviewPlayhead(600000);

  expect(app.currentSkillTestForkCursor()).toEqual({
    time_ms: 600000,
    terminal_seq: 9,
    snapshot_seq: 5
  });
});

test("running skill test review can be cancelled without local state guesswork", async () => {
  const app = createTimelineHarness();
  app.busy = { skillTestCancel: false };
  app.skillTestRun = { id: "run-1", status: "running" };
  app.skillTestRuns = [];
  app.skillTestReview = { scenario_run: app.skillTestRun };
  app.confirmDangerAction = jest.fn(() => true);
  app.stopSkillTestReviewPlayback = jest.fn();
  app.stopSkillTestReviewPolling = jest.fn();
  app.showNotice = jest.fn();
  app.refreshSkillTestRunReview = jest.fn(async () => ({ scenario_run: { id: "run-1", status: "cancelled" } }));
  app.apiRequest = jest.fn(async () => ({ id: "run-1", status: "cancelled", driver_status: "cancelled" }));

  await app.cancelSkillTestRun();

  expect(app.apiRequest).toHaveBeenCalledWith(
    "/skill-test-scenario-runs/run-1/cancel",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ reason: "用户终止测试" })
    })
  );
  expect(app.skillTestRun.status).toBe("cancelled");
  expect(app.refreshSkillTestRunReview).toHaveBeenCalledWith("run-1", { force: true });
  expect(app.stopSkillTestReviewPolling).toHaveBeenCalled();
  expect(app.showNotice).toHaveBeenCalledWith("success", "已终止测试运行。");
  expect(app.busy.skillTestCancel).toBe(false);
});

test("review stage output drives expanded details and fork cursor", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    cursor_anchors: [{ time_ms: 190000, terminal_seq: 4, snapshot_seq: 3 }],
    stage_outputs: [
      {
        stage_id: "expected_9",
        event_id: "expected_9",
        time_ms: 190000,
        expectation: "引导用户进行下一步操作",
        actual_outputs: [
          {
            id: "stage_output_1",
            at_ms: 45000,
            seq_no: 2,
            event_kind: "terminal.text.output.v1",
            mime_type: "text/plain",
            payload_inline: "第一步：请检查伞骨。"
          }
        ],
        judge_result: {
          status: "passed",
          confidence: 0.93,
          reason: "满足阶段期望。"
        },
        human_review: {
          status: "pending",
          reviewer: null,
          reason: "",
          updated_at: null
        },
        cursor: { time_ms: 190000, terminal_seq: 4, snapshot_seq: 3 }
      }
    ]
  };

  app.updateSkillTestReviewPlayhead(50000);
  const event = app.skillTestReviewEventsForLane("expected.semantic")[0].event;
  app.openSkillTestReviewEvent(event);

  expect(app.skillTestReviewPlayheadMsValue()).toBe(50000);
  expect(app.currentSkillTestForkCursor()).toEqual({ time_ms: 190000, terminal_seq: 4, snapshot_seq: 3 });
  expect(app.skillTestReviewRuntimeOutputsForExpectation(event)).toHaveLength(1);
  expect(app.skillTestReviewEventContentSections(event).map((section) => section.title)).toEqual([
    "阶段期望",
    "阶段切面",
    "真实输出 #1",
    "智能体判定",
    "人工判定"
  ]);
  expect(app.skillTestReviewEventMetadata(event)).toEqual(
    expect.arrayContaining([
      { label: "阶段 ID", value: "expected_9" },
      { label: "人工判定", value: "pending" }
    ])
  );
});

test("review timeline keeps edge events inside the track lane", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline()
  };

  const earlyEvent = app.skillTestReviewEventsForLane("input.text")[0].event;
  const latestEvent = {
    id: "expected_end",
    lane_id: "expected.semantic",
    at_ms: 600000,
    expectation: "结束时应满足的输出"
  };

  expect(app.skillTestReviewTimelineEventLeftStyle(earlyEvent)).toBe("left: clamp(4rem, 0%, calc(100% - 4rem))");
  expect(app.skillTestReviewTimelineEventLeftStyle(latestEvent)).toBe("left: clamp(4rem, 100%, calc(100% - 4rem))");
});

test("review timeline exposes elapsed time as a lane background fill", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline()
  };
  app.skillTestReviewPlayheadMs = 300000;

  expect(app.skillTestReviewLaneProgressStyle()).toBe("width: 50%");
});

test("review timeline binds runtime outputs to the next semantic expectation", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    scenario_run: {
      status: "running",
      time_origin: "2026-05-13T00:00:00Z"
    },
    replay: {
      terminal_events: [
        {
          id: "input-1",
          seq_no: 1,
          direction: "input",
          event_kind: "terminal.text.input.v1",
          occurred_at: "2026-05-13T00:00:20Z",
          payload_inline: "用户输入"
        },
        {
          id: "output-1",
          seq_no: 2,
          direction: "output",
          event_kind: "terminal.text.output.v1",
          occurred_at: "2026-05-13T00:00:45Z",
          payload_inline: "第一步：请检查伞骨。"
        },
        {
          id: "output-2",
          seq_no: 3,
          direction: "output",
          event_kind: "terminal.markdown.output.v1",
          occurred_at: "2026-05-13T00:01:30Z",
          payload_inline: { text: "第二步：确认连接件。" }
        },
        {
          id: "output-3",
          seq_no: 4,
          direction: "output",
          event_kind: "terminal.text.output.v1",
          occurred_at: "2026-05-13T00:04:10Z",
          payload_inline: "第三步：继续检查伞面。"
        }
      ]
    }
  };

  const laneIds = app.skillTestReviewTimelineLanes().map((lane) => lane.id);
  expect(laneIds).not.toContain("actual.output");
  expect(app.skillTestReviewEventsForLane("actual.output")).toHaveLength(0);
  expect(app.skillTestTimelineLaneLabel({ id: "actual.output" })).toBe("真实");
  expect(app.skillTestTimelineLaneGroup("actual.output")).toBe("output");

  const runtimeOutputs = app.skillTestReviewRuntimeOutputEvents();
  expect(runtimeOutputs).toHaveLength(3);
  expect(runtimeOutputs[0].at_ms).toBe(45000);
  expect(runtimeOutputs[0].seq_no).toBe(2);
  expect(app.skillTestTimelineEventLabel(runtimeOutputs[0])).toContain("第一步");
  expect(app.skillTestTimelineEventLabel(runtimeOutputs[1])).toContain("第二步");

  const semanticEvents = app.skillTestReviewEventsForLane("expected.semantic").map((item) => item.event);
  const firstBoundOutputs = app.skillTestReviewRuntimeOutputsForExpectation(semanticEvents[0]);
  const secondBoundOutputs = app.skillTestReviewRuntimeOutputsForExpectation(semanticEvents[1]);
  expect(firstBoundOutputs.map((event) => event.id)).toEqual(["runtime_output_output-1", "runtime_output_output-2"]);
  expect(secondBoundOutputs.map((event) => event.id)).toEqual(["runtime_output_output-3"]);
  expect(app.skillTestReviewEventTooltip(semanticEvents[0])).toContain("2 个真实输出");
  const contentSections = app.skillTestReviewEventContentSections(semanticEvents[0]);
  expect(contentSections.map((section) => section.title)).toEqual(["阶段期望", "真实输出 #1", "真实输出 #2"]);
  expect(contentSections[0].content).toContain("引导用户进行下一步操作");
  expect(contentSections[1].content).toContain("第一步：请检查伞骨。");
  expect(contentSections[2].content).toContain("第二步：确认连接件。");
  expect(app.skillTestReviewEventMetadata(semanticEvents[0])).toEqual(
    expect.arrayContaining([{ label: "真实输出", value: "2 个" }])
  );

  app.updateSkillTestReviewPlayhead(44000);
  expect(app.skillTestReviewStepStatus(runtimeOutputs[0])).toBe("not_occurred");
  expect(app.skillTestReviewEventStatusLabel(runtimeOutputs[0])).toBe("未输出");

  app.updateSkillTestReviewPlayhead(45000);
  expect(app.skillTestReviewStepStatus(runtimeOutputs[0])).toBe("output");
  expect(app.skillTestReviewEventStatusLabel(runtimeOutputs[0])).toBe("已输出");
});

test("review lane header opens lane time details and event clicks replace it", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    scenario_run: {
      status: "running",
      time_origin: "2026-05-13T00:00:00Z"
    },
    driver_events: [{ event_id: "input_1", status: "sent" }],
    replay: {
      terminal_events: [
        {
          id: "output-1",
          seq_no: 1,
          direction: "output",
          event_kind: "terminal.text.output.v1",
          occurred_at: "2026-05-13T00:00:45Z",
          payload_inline: "第一步输出"
        }
      ]
    }
  };
  app.updateSkillTestReviewPlayhead(190000);

  app.openSkillTestReviewLaneDetail("expected.semantic");

  expect(app.isSkillTestReviewLaneSelected("expected.semantic")).toBe(true);
  expect(app.skillTestReviewSelectedLane().id).toBe("expected.semantic");
  expect(app.skillTestReviewSelectedLaneEvents()).toHaveLength(3);
  expect(app.skillTestReviewLaneRangeLabel("expected.semantic")).toBe("3m 10s - 6m 2s");
  expect(app.skillTestReviewLaneReachedCount("expected.semantic")).toBe(1);
  expect(app.skillTestReviewLaneRuntimeOutputCount("expected.semantic")).toBe(1);

  app.updateSkillTestReviewPlayhead(120000);
  const event = app.skillTestReviewSelectedLaneEvents()[0].event;
  app.openSkillTestReviewEvent(event);

  expect(app.selectedSkillTestReviewLaneId).toBe("");
  expect(app.skillTestReviewExpandedEvent().id).toBe("expected_9");
  expect(app.skillTestReviewPlayheadMsValue()).toBe(120000);
});

test("review judge debug exposes saved request and model response", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    expectation_evaluations: [
      {
        expectation_id: "expected_9",
        status: "inconclusive",
        confidence: 0.42,
        reason: "证据不足。",
        judge_provider: "fake",
        judge_model: "judge-test",
        prompt_hash: "hash-1",
        raw_response: {
          request: {
            route_key: "skill-test-judge",
            system_prompt: "system",
            user_prompt: "{\"expectation\":\"引导用户进行下一步操作\"}",
            prompt_payload: {
              expectation: "引导用户进行下一步操作",
              run_status: "succeeded"
            }
          },
          content: "{\"status\":\"inconclusive\"}",
          parsed: {
            status: "inconclusive",
            confidence: 0.42,
            reason: "证据不足。"
          },
          usage: {
            total_tokens: 99
          },
          raw: {
            id: "completion-1"
          }
        }
      }
    ]
  };

  const event = app.skillTestReviewEventsForLane("expected.semantic")[0].event;
  app.selectSkillTestReviewEvent(event);

  expect(app.skillTestReviewPanelTab).toBe("transcript");
  expect(app.selectedSkillTestReviewExpectationId).toBe("expected_9");
  expect(app.isSkillTestReviewExpectationSelected(event)).toBe(true);
  expect(app.selectedSkillTestReviewEvaluation().judge_model).toBe("judge-test");
  expect(app.selectedSkillTestReviewJudgeRequest().route_key).toBe("skill-test-judge");
  expect(app.selectedSkillTestReviewJudgePromptPayload().expectation).toBe("引导用户进行下一步操作");
  expect(app.selectedSkillTestReviewJudgeRawOutput()).toBe("{\"status\":\"inconclusive\"}");
  expect(app.selectedSkillTestReviewJudgeParsedOutput().status).toBe("inconclusive");
  expect(app.selectedSkillTestReviewJudgeUsage().total_tokens).toBe(99);
  expect(app.selectedSkillTestReviewJudgeProviderRaw().id).toBe("completion-1");
  expect(app.selectedSkillTestReviewJudgeInputNotice()).toBe("已保存本次 Judge 调用的真实输入。");
});

test("review timeline events open expanded full content", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    driver_events: [{ event_id: "input_1", status: "sent" }]
  };
  app.updateSkillTestReviewPlayhead(200000);

  const inputEvent = app.skillTestReviewEventsForLane("input.text")[0].event;
  app.openSkillTestReviewEvent(inputEvent);

  expect(app.skillTestReviewExpandedEvent().id).toBe("input_1");
  expect(app.skillTestReviewEventContentSections(app.skillTestReviewExpandedEvent())).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ title: "输入内容", content: "已准备就绪，可以开始" })
    ])
  );
  expect(app.skillTestReviewEventTooltip(inputEvent)).toContain("点击查看完整内容");
  const metadataLabels = app.skillTestReviewEventMetadata(inputEvent).map((item) => item.label);
  expect(metadataLabels).toEqual(expect.arrayContaining(["状态", "事件 ID", "事件类型", "MIME 类型", "必填"]));
  expect(metadataLabels).not.toEqual(expect.arrayContaining(["信道", "时间"]));

  app.closeSkillTestReviewEvent();
  expect(app.skillTestReviewExpandedEvent()).toBeNull();
});

test("opening a semantic review event also selects judge context", () => {
  const app = createTimelineHarness();
  app.skillTestReview = {
    scenario_timeline: duplicateSemanticTimeline(),
    expectation_evaluations: [{ expectation_id: "expected_9", status: "passed", confidence: 0.9 }]
  };

  const event = app.skillTestReviewEventsForLane("expected.semantic")[0].event;
  app.openSkillTestReviewEvent(event);

  expect(app.selectedSkillTestReviewExpectationId).toBe("expected_9");
  expect(app.skillTestReviewPanelTab).toBe("transcript");
  expect(app.skillTestReviewDetailTab).toBe("content");
  expect(app.skillTestReviewExpandedEvent().id).toBe("expected_9");
  expect(app.skillTestReviewEventContentSections(app.skillTestReviewExpandedEvent())[0]).toEqual(
    expect.objectContaining({ title: "阶段期望", content: "引导用户进行下一步操作" })
  );
  expect(app.skillTestReviewExpandedEvaluation(app.skillTestReviewExpandedEvent()).status).toBe("passed");
});

test("review refresh replaces pending semantic judgement with saved judge output", async () => {
  const app = createTimelineHarness();
  const timeline = {
    schema_version: "psop-skill-test-timeline/v1",
    duration_ms: 600000,
    lanes: [
      { id: "input.text", kind: "input", label: "文本" },
      { id: "expected.semantic", kind: "output", label: "语义" }
    ],
    events: [
      {
        id: "input_1",
        lane_id: "input.text",
        at_ms: 0,
        payload_inline: "已准备就绪"
      },
      {
        id: "expect_final",
        lane_id: "expected.semantic",
        at_ms: 1000,
        expectation: "系统应给出下一步指导。"
      }
    ]
  };
  const initialReview = {
    scenario: { id: "scenario-1" },
    scenario_run: {
      id: "scenario-run-1",
      status: "running",
      time_origin: "2026-05-13T00:00:00Z"
    },
    scenario_timeline: timeline,
    replay: {
      terminal_events: []
    },
    expectation_evaluations: []
  };
  const refreshedReview = {
    ...initialReview,
    scenario_run: {
      ...initialReview.scenario_run,
      status: "passed"
    },
    expectation_evaluations: [
      {
        expectation_id: "expect_final",
        status: "passed",
        raw_response: {
          content: "{\"status\":\"passed\",\"confidence\":0.91}",
          parsed: {
            status: "passed",
            confidence: 0.91
          }
        }
      }
    ]
  };

  app.skillTestReview = initialReview;
  app.skillTestRun = initialReview.scenario_run;
  app.skillTestRuns = [];
  app.liveRunTerminalEvents = [];
  app.liveRunTraceEvents = [];
  app.liveRunBindings = [];
  app.route = { name: "skill-test-scenario-review" };
  app.apiRequest = jest.fn().mockResolvedValue(refreshedReview);
  app.selectedSkillTestReviewExpectationId = "expect_final";
  app.updateSkillTestReviewPlayhead(1000);

  expect(app.shouldPollSkillTestRunReview(initialReview)).toBe(true);
  expect(app.skillTestReviewStepStatus(app.selectedSkillTestReviewExpectationEvent())).toBe("triggered");
  expect(app.selectedSkillTestReviewJudgeRawOutput()).toBe("");

  await app.refreshSkillTestRunReview("scenario-run-1", { force: true });

  expect(app.apiRequest).toHaveBeenCalledWith("/skill-test-scenario-runs/scenario-run-1/review");
  expect(app.skillTestReviewStepStatus(app.selectedSkillTestReviewExpectationEvent())).toBe("passed");
  expect(app.selectedSkillTestReviewJudgeRawOutput()).toBe("{\"status\":\"passed\",\"confidence\":0.91}");
  expect(app.shouldPollSkillTestRunReview(refreshedReview)).toBe(false);
});

test("completed review records open at the end without autoplay", () => {
  const app = createTimelineHarness();
  const review = {
    scenario_timeline: duplicateSemanticTimeline(),
    scenario_run: {
      status: "succeeded",
      time_origin: "2026-05-13T00:00:00Z"
    }
  };
  app.skillTestReview = review;
  app.skillTestRun = review.scenario_run;

  app.applySkillTestReviewInitialPlayhead(review);

  expect(app.skillTestReviewPlayheadMsValue()).toBe(600000);
  expect(app.skillTestReviewPlaybackRunning).toBe(false);
  expect(app.skillTestReviewAutoFollow).toBe(false);
});

test("running review records open at the current run time", () => {
  const app = createTimelineHarness();
  const origin = new Date(Date.now() - 70000).toISOString();
  const review = {
    scenario_timeline: duplicateSemanticTimeline(),
    scenario_run: {
      status: "running",
      time_origin: origin
    },
    cursor_anchors: [{ time_ms: 40000 }],
    driver_events: [{ at_ms: 55000 }]
  };
  app.skillTestReview = review;
  app.skillTestRun = review.scenario_run;

  app.applySkillTestReviewInitialPlayhead(review);

  expect(app.skillTestReviewPlayheadMsValue()).toBeGreaterThanOrEqual(70000);
  expect(app.skillTestReviewPlayheadMsValue()).toBeLessThan(72000);
  expect(app.skillTestReviewPlaybackRunning).toBe(false);
});

test("terminal transcript keeps all events visible at the end of a historical review", () => {
  const app = createTimelineHarness();
  const review = {
    scenario_timeline: duplicateSemanticTimeline(),
    scenario_run: {
      status: "failed",
      time_origin: "2026-05-13T00:00:00Z"
    },
    replay: {
      terminal_events: [
        {
          id: "early",
          occurred_at: "2026-05-13T00:00:10Z"
        },
        {
          id: "late",
          occurred_at: "2026-05-13T00:12:00Z"
        }
      ]
    }
  };
  app.skillTestReview = review;
  app.skillTestRun = review.scenario_run;
  app.updateSkillTestReviewPlayhead(600000);

  expect(app.filteredSkillTestReviewTerminalEvents().map((event) => event.id)).toEqual(["early", "late"]);
});
