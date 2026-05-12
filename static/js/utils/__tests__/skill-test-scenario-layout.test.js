const fs = require("fs");
const path = require("path");

const scenarioPath = path.join(__dirname, "../../../pages/skill-test-scenario-detail.html");
const reviewPath = path.join(__dirname, "../../../pages/skill-test-scenario-review.html");
const skillDetailPath = path.join(__dirname, "../../../pages/skill-detail.html");
const indexPath = path.join(__dirname, "../../../index.html");
const appPath = path.join(__dirname, "../../app.js");
const skillTestAppPath = path.join(__dirname, "../../app/skill-test.js");

test("skill test tab exposes timeline scenario management", () => {
  const skillDetailHtml = fs.readFileSync(skillDetailPath, "utf8");
  const appJs = fs.readFileSync(skillTestAppPath, "utf8");
  const shellJs = fs.readFileSync(appPath, "utf8");

  expect(skillDetailHtml).toContain("新增时序测试场景");
  expect(skillDetailHtml).toContain("skillTestTimelineLanes()");
  expect(skillDetailHtml).toContain("skillTestTimelineEventsForLane(lane.id)");
  expect(skillDetailHtml).toContain("data-skill-test-timeline-track");
  expect(skillDetailHtml).toContain("addSkillTestTimelineEventFromLane($event, lane.id)");
  expect(skillDetailHtml).toContain("skillTestTimelineDurationValue()");
  expect(skillDetailHtml).toContain(">分</span>");
  expect(skillDetailHtml).toContain("grid-cols-[3.5rem_5.5rem_minmax(0,1fr)]");
  expect(skillDetailHtml).toContain("时间点 s");
  expect(skillDetailHtml).toContain("skillTestTimelineEventSeconds");
  expect(skillDetailHtml).toContain("updateSelectedSkillTestTimelineEventSeconds");
  expect(skillDetailHtml).not.toContain("skillTestDurationUnit");
  expect(skillDetailHtml).not.toContain("updateSkillTestTimelineDurationUnit");
  expect(skillDetailHtml).not.toContain('option value="minutes"');
  expect(skillDetailHtml).not.toContain("时间点 ms");
  expect(skillDetailHtml).toContain("skillTestTimelineEventLeftStyle(item.event)");
  expect(skillDetailHtml).toContain("number-no-spinner");
  expect(skillDetailHtml).toContain("w-20");
  expect(skillDetailHtml).toContain("skillTestTimelineEventUsesAsset");
  expect(skillDetailHtml).toContain("skillTestAssetsForLane");
  expect(skillDetailHtml).toContain("handleSkillTestTimelineEventFile");
  expect(skillDetailHtml).toContain("上传并绑定");
  expect(skillDetailHtml).toContain("暂存资源");
  expect(skillDetailHtml).toContain(">时间</span>");
  expect(skillDetailHtml).not.toContain(">时间轴</span>");
  expect(skillDetailHtml).not.toContain('x-text="lane.id"');
  expect(skillDetailHtml).not.toContain("黑盒时序编排");
  expect(skillDetailHtml).not.toContain("总时长 s");
  expect(skillDetailHtml).not.toContain("总时长 ms");
  expect(skillDetailHtml).not.toContain("版本选择");
  expect(skillDetailHtml).not.toContain("指定 Artifact");
  expect(skillDetailHtml).not.toContain("新增自动化 Case");
  expect(skillDetailHtml).not.toContain("输入脚本步骤");
  expect(appJs).toContain("/test-scenarios");
  expect(shellJs).toContain("skill-test-scenario-review");
  expect(appJs).toContain('label: "语义"');
  expect(appJs).toContain('target_version_selector: "latest"');
  expect(appJs).toContain("target_compile_artifact_id: null");
  expect(appJs).not.toContain("/test-cases");
  expect(appJs).not.toContain("/skill-test-runs");
});

test("scenario detail page provides lanes, assets, and runs", () => {
  const html = fs.readFileSync(scenarioPath, "utf8");

  expect(html).toContain("运行场景");
  expect(html).toContain("input.text");
  expect(html).toContain("input.image");
  expect(html).toContain("skillTestTimelineTicks()");
  expect(html).toContain("startSkillTestTimelineEventDrag");
  expect(html).toContain("addSkillTestTimelineEventFromLane($event, lane.id)");
  expect(html).toContain("skillTestTimelineDurationValue()");
  expect(html).toContain(">分</span>");
  expect(html).toContain("grid-cols-[3.5rem_5.5rem_minmax(0,1fr)]");
  expect(html).toContain("时间点 s");
  expect(html).toContain("skillTestTimelineEventSeconds");
  expect(html).toContain("updateSelectedSkillTestTimelineEventSeconds");
  expect(html).not.toContain("skillTestDurationUnit");
  expect(html).not.toContain("updateSkillTestTimelineDurationUnit");
  expect(html).not.toContain('option value="minutes"');
  expect(html).not.toContain("时间点 ms");
  expect(html).toContain("skillTestTimelineEventLeftStyle(item.event)");
  expect(html).toContain("number-no-spinner");
  expect(html).toContain("w-20");
  expect(html).toContain("skillTestTimelineEventUsesAsset");
  expect(html).toContain("skillTestAssetsForLane");
  expect(html).toContain("skillTestTimelineAcceptForLane");
  expect(html).toContain("handleSkillTestTimelineEventFile");
  expect(html).toContain("上传并绑定");
  expect(html).toContain("input.audio");
  expect(html).toContain("input.video");
  expect(html).toContain(">时间</span>");
  expect(html).not.toContain(">时间轴</span>");
  expect(html).not.toContain('x-text="lane.id"');
  expect(html).not.toContain("黑盒时序编排");
  expect(html).not.toContain("总时长 s");
  expect(html).not.toContain("总时长 ms");
  expect(html).not.toContain("版本选择");
  expect(html).not.toContain("指定 Artifact");
  expect(html).toContain("uploadSkillTestData()");
  expect(html).toContain('accept="image/*,audio/*,video/*"');
  expect(html).toContain("result_summary");
  expect(html).not.toContain("cancelSkillTestRun");
});

test("scenario review provides scrub and fork actions", () => {
  const html = fs.readFileSync(reviewPath, "utf8");
  const indexHtml = fs.readFileSync(indexPath, "utf8");
  const appJs = fs.readFileSync(skillTestAppPath, "utf8");

  expect(html).toContain('type="range"');
  expect(html).toContain("filteredSkillTestReviewTerminalEvents()");
  expect(html).toContain("Fork Scenario");
  expect(html).toContain("Fork Debug");
  expect(appJs).toContain("/fork-scenario");
  expect(appJs).toContain("/fork-debug");
  expect(indexHtml).toContain("skill-test-scenario-page");
  expect(indexHtml).toContain("skill-test-scenario-review-page");
  expect(indexHtml).not.toContain("skill-test-live-page");
});

test("frontend scripts are split and no longer reference the assets layer", () => {
  const indexHtml = fs.readFileSync(indexPath, "utf8");
  const appJs = fs.readFileSync(appPath, "utf8");

  expect(indexHtml).toContain('/js/app/core.js');
  expect(indexHtml).toContain('/js/app/skill-detail.js');
  expect(indexHtml).toContain('/js/app/compiler.js');
  expect(indexHtml).toContain('/js/app/skill-test.js');
  expect(indexHtml).toContain('/js/app/runtime.js');
  expect(indexHtml).toContain('/js/app/formatters.js');
  expect(indexHtml).toContain('/css/style.compiled.css');
  expect(indexHtml).not.toContain('/assets/js');
  expect(indexHtml).not.toContain('/assets/css');
  expect(indexHtml).not.toContain('/assets/fonts');
  expect(appJs).toContain("window.PSOPConsoleHelpers");
  expect(appJs).toContain("window.PSOPConsoleSkillTestMethods");
});
