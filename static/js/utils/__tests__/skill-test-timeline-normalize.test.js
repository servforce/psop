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
      PSOPConsoleHelpers: Object.fromEntries(helperNames.map((name) => [name, jest.fn()]))
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
    skillTestTimelineDragState: null
  };
}

function duplicateSemanticTimeline() {
  return {
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
        expectation: "描述该时间点以前应满足的语义输出"
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
    "描述该时间点以前应满足的语义输出"
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

test("expanded timeline lane does not add events from blank track clicks", () => {
  const app = createTimelineHarness();
  app.skillTestCaseForm.timeline_json = JSON.stringify(duplicateSemanticTimeline());
  app.selectedSkillTestTimelineEventId = "expected_9";
  app.selectedSkillTestTimelineEventIds = ["expected_9"];

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
  app.skillTestReviewPlayheadMs = 0;

  expect(app.skillTestReviewDurationMs()).toBe(600000);
  expect(app.skillTestReviewEventsForLane("expected.semantic")).toHaveLength(3);
  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("not_occurred");

  app.updateSkillTestReviewPlayhead(190000);

  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("input.text")[0].event)).toBe("sent");
  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("passed");
  expect(app.skillTestReviewAssertionVerdictLabel(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("符合预期");

  app.skillTestReview.expectation_evaluations[0].status = "inconclusive";

  expect(app.skillTestReviewStepStatus(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("inconclusive");
  expect(app.skillTestReviewAssertionVerdictLabel(app.skillTestReviewEventsForLane("expected.semantic")[0].event)).toBe("未能判定");
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
