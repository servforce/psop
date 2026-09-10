const fs = require("fs");
const path = require("path");

const guardedFragments = [
  ["skill-detail.html", '<template x-if="currentSkill">'],
  ["run-live.html", '<template x-if="liveRun">'],
  ["skill-test-scenario-detail.html", '<template x-if="currentSkill && (skillTestCase || route.name === \'skill-test-scenario-new\')">'],
  ["skill-test-scenario-review.html", '<template x-if="currentSkill && skillTestCase && skillTestReview">'],
  ["compiler-artifact-detail.html", '<template x-if="compilerArtifact">'],
  ["agent-prompt-detail.html", '<template x-if="agentPromptDetail">']
];

test("app shell uses the local PSOP brand icon", () => {
  const indexHtml = fs.readFileSync(path.join(__dirname, "../../../index.html"), "utf8");
  const iconPath = path.join(__dirname, "../../../img/psop-icon.png");

  expect(indexHtml).toContain('<link rel="icon" type="image/png" href="/img/psop-icon.png" />');
  expect(indexHtml).toContain('<img src="/img/psop-icon.png" alt="PSOP"');
  expect(indexHtml).not.toContain('>hub</span>');
  expect(fs.existsSync(iconPath)).toBe(true);
});

test("data-dependent page fragments are not instantiated before their data exists", () => {
  for (const [fileName, guard] of guardedFragments) {
    const html = fs.readFileSync(path.join(__dirname, "../../../pages", fileName), "utf8");

    expect(html.trimStart().startsWith(guard)).toBe(true);
    expect(html.trimEnd().endsWith("</template>")).toBe(true);
  }
});

test("page overlays do not repeat the same close-icon action in multiple buttons", () => {
  const pagesDirectory = path.join(__dirname, "../../../pages");
  const duplicates = [];

  for (const fileName of fs.readdirSync(pagesDirectory).filter((name) => name.endsWith(".html"))) {
    const html = fs.readFileSync(path.join(pagesDirectory, fileName), "utf8");
    const closeActionCounts = new Map();

    for (const button of html.match(/<button\b[\s\S]*?<\/button>/g) || []) {
      if (!button.includes(">close</span>")) continue;
      const closeAction = button.match(/@click="(close[A-Za-z0-9_]+\([^"]*\))"/)?.[1];
      if (!closeAction) continue;
      closeActionCounts.set(closeAction, (closeActionCounts.get(closeAction) || 0) + 1);
    }

    for (const [action, count] of closeActionCounts) {
      if (count > 1) duplicates.push({ fileName, action, count });
    }
  }

  expect(duplicates).toEqual([]);
});

test("run live page is read-only and uses interaction data tabs", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");

  expect(html).not.toContain("event.event_kind");
  expect(html).not.toContain("event.mime_type");
  expect(html).not.toContain("event.artifact_object_id");
  expect(html).not.toContain("terminalEventActorIcon");
  expect(html).not.toContain("terminalEventAvatarClass");
  expect(html).toContain("liveRunInteractionTab === 'terminal'");
  expect(html).toContain("liveRunInteractionTab === 'replay'");
  expect(html).toContain("<span>terminal</span>");
  expect(html).toContain("<span>replay</span>");
  expect(html).not.toContain("liveRunInteractionTab === 'trace'");
  expect(html).not.toContain("Trace Events");
  expect(html).toContain("liveRunReplayTimeline()");
  expect(html).toContain("liveRunReplaySnapshots()");
  expect(html).toContain("selectLiveRunReplayItem(item)");
  expect(html).toContain("selectedLiveRunReplayItem()");
  expect(html).toContain("Event Details");
  expect(html).not.toContain("terminalInputForm");
  expect(html).not.toContain("terminalInputAttachments()");
  expect(html).not.toContain("handleTerminalInputFile($event)");
  expect(html).not.toContain("sendTerminalInput()");
  expect(html).toContain("terminalEventParts(event)");
  expect(html).toContain("terminalEventPartMediaUrl(event, part)");
  const terminalTranscript = html.slice(
    html.indexOf('x-ref="terminalTranscriptScroll"'),
    html.indexOf('x-show="liveRunInteractionTab === \'replay\'"')
  );
  expect(terminalTranscript).not.toContain('`#${event.seq_no}`');
  expect(html).toContain('`#${selectedLiveRunReplayItem().seq_no}`');
});

test("run live page provides a responsive task status panel", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  const runtimeJs = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");

  expect(html).toContain('aria-label="任务状态"');
  expect(html).toContain("liveRunTaskPanelOpen ? 'flex' : 'hidden xl:flex'");
  expect(html).toContain('class="flex shrink-0 items-center gap-3 border-b border-slate-800 bg-slate-950/80 px-3 py-2 text-left xl:hidden"');
  expect(html).toContain("liveRunTaskStatus.current_checkpoint.requirements");
  expect(html).toContain("任务状态加载失败");
  expect(html).not.toContain(">当前任务</p>");
  expect(html).not.toContain("task?.version_no");
  expect(html).toContain("<summary");
  expect(html).toContain("运行信息");
  expect(runtimeJs).toContain("/runs/${runId}/task-status");
  expect(runtimeJs).toContain('"run.task_status.updated"');
  expect(runtimeJs).toContain('"succeeded", "failed", "aborted", "cancelled", "canceled"');
});

test("run live page exposes a copyable run id", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");

  expect(html).toContain('<span class="field-label">运行 ID</span>');
  expect(html).toContain(':value="liveRun.id || \'\'" readonly aria-label="运行 ID"');
  expect(html).toContain('aria-label="复制运行 ID"');
  expect(html).toContain('copyText(liveRun.id, `run-id-${liveRun.id}`)');
  expect(html).toContain('copyIcon(`run-id-${liveRun.id}`)');
});

test("skill detail page does not expose the debug tab", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/skill-detail.html"), "utf8");

  expect(html).not.toContain("selectDetailTab('debug')");
  expect(html).not.toContain("activeDetailTab === 'debug'");
  expect(html).not.toContain("启动调试");
  expect(html).not.toContain("调试历史");
});

test("skill compiler artifact opens as an independent detail page", () => {
  const indexHtml = fs.readFileSync(path.join(__dirname, "../../../index.html"), "utf8");
  const compilerHtml = fs.readFileSync(path.join(__dirname, "../../../pages/compiler-artifact-detail.html"), "utf8");
  const skillDetailHtml = fs.readFileSync(path.join(__dirname, "../../../pages/skill-detail.html"), "utf8");
  const compilerJs = fs.readFileSync(path.join(__dirname, "../../app/compiler.js"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  const coreJs = fs.readFileSync(path.join(__dirname, "../../app/core.js"), "utf8");

  expect(indexHtml).toContain("skill-compiler-artifact");
  expect(indexHtml).toContain("['compiler-artifact', 'skill-compiler-artifact'].includes(route.name)");
  expect(compilerHtml).toContain("route.name === 'skill-compiler-artifact'");
  expect(compilerHtml).toContain("openCurrentSkillCompiler()");
  expect(compilerJs).toContain("buildSkillCompilerArtifactPath");
  expect(compilerJs).toContain("navigate(buildSkillCompilerArtifactPath");
  expect(compilerJs).not.toContain("compilerArtifactWorkspaceOpen");
  expect(appJs).not.toContain("compilerArtifactWorkspaceOpen");
  expect(skillDetailHtml).not.toContain("compilerArtifactWorkspaceOpen");
  expect(coreJs).toContain('this.route.name === "skill-compiler-artifact"');
});
