const fs = require("fs");
const path = require("path");

const guardedFragments = [
  ["skill-detail.html", '<template x-if="currentSkill">'],
  ["run-live.html", '<template x-if="liveRun">'],
  ["replay-detail.html", '<template x-if="replayDetail">'],
  ["skill-test-scenario-detail.html", '<template x-if="currentSkill && skillTestCase">'],
  ["skill-test-scenario-review.html", '<template x-if="currentSkill && skillTestCase && skillTestReview">'],
  ["compiler-artifact-detail.html", '<template x-if="compilerArtifact">'],
  ["agent-prompt-detail.html", '<template x-if="agentPromptDetail">']
];

test("data-dependent page fragments are not instantiated before their data exists", () => {
  for (const [fileName, guard] of guardedFragments) {
    const html = fs.readFileSync(path.join(__dirname, "../../../pages", fileName), "utf8");

    expect(html.trimStart().startsWith(guard)).toBe(true);
    expect(html.trimEnd().endsWith("</template>")).toBe(true);
  }
});
