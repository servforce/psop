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
    skillTestTimelineDragState: null,
    skillTestReviewExpandedEventKey: "",
    skillTestReviewPanelTab: "transcript",
    selectedSkillTestReviewExpectationId: "",
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

test("review timeline renders runtime outputs on the actual output lane", () => {
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
        }
      ]
    }
  };

  const laneIds = app.skillTestReviewTimelineLanes().map((lane) => lane.id);
  expect(laneIds.indexOf("actual.output")).toBe(laneIds.indexOf("expected.semantic") + 1);
  expect(app.skillTestTimelineLaneLabel({ id: "actual.output" })).toBe("真实");
  expect(app.skillTestTimelineLaneGroup("actual.output")).toBe("output");

  const runtimeOutputs = app.skillTestReviewEventsForLane("actual.output");
  expect(runtimeOutputs).toHaveLength(2);
  expect(runtimeOutputs[0].event.at_ms).toBe(45000);
  expect(runtimeOutputs[0].event.seq_no).toBe(2);
  expect(app.skillTestTimelineEventLabel(runtimeOutputs[0].event)).toContain("第一步");
  expect(app.skillTestTimelineEventLabel(runtimeOutputs[1].event)).toContain("第二步");

  app.updateSkillTestReviewPlayhead(44000);
  expect(app.skillTestReviewStepStatus(runtimeOutputs[0].event)).toBe("not_occurred");

  app.updateSkillTestReviewPlayhead(45000);
  expect(app.skillTestReviewStepStatus(runtimeOutputs[0].event)).toBe("output");
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

  expect(app.skillTestReviewPanelTab).toBe("judge");
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
  expect(app.skillTestReviewEventPrimaryContent(app.skillTestReviewExpandedEvent())).toContain("已准备就绪，可以开始");
  expect(app.skillTestReviewEventTooltip(inputEvent)).toContain("点击查看完整内容");
  expect(app.skillTestReviewEventMetadata(inputEvent).map((item) => item.label)).toEqual(
    expect.arrayContaining(["信道", "时间", "状态", "Event ID", "Event Kind", "MIME", "Required"])
  );

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
  expect(app.skillTestReviewPanelTab).toBe("judge");
  expect(app.skillTestReviewExpandedEvent().id).toBe("expected_9");
  expect(app.skillTestReviewEventPrimaryContent(app.skillTestReviewExpandedEvent())).toContain("引导用户进行下一步操作");
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
