const fs = require("fs");
const path = require("path");
const vm = require("vm");

const corePath = path.join(__dirname, "../../app/core.js");

function loadCoreMethods(fetchMock) {
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
    "buildAgentPromptPath",
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
  class TestFormData {}
  const context = {
    FormData: TestFormData,
    fetch: fetchMock,
    window: {
      PSOPConsoleHelpers: Object.fromEntries(helperNames.map((name) => [name, jest.fn()]))
    }
  };
  vm.runInNewContext(fs.readFileSync(corePath, "utf8"), context);
  return context.window.PSOPConsoleCoreMethods;
}

function jsonResponse(payload = {}) {
  return {
    ok: true,
    status: 200,
    headers: {
      get(name) {
        return name.toLowerCase() === "content-type" ? "application/json" : "";
      }
    },
    async json() {
      return payload;
    }
  };
}

test("apiRequest does not add JSON content type to bodyless requests", async () => {
  const fetchMock = jest.fn(async () => jsonResponse([]));
  const methods = loadCoreMethods(fetchMock);
  const app = { apiBaseUrl: "http://api.example/api/v1" };

  await methods.apiRequest.call(app, "/skills");

  expect(fetchMock).toHaveBeenCalledWith("http://api.example/api/v1/skills", { headers: {} });
});

test("apiRequest keeps JSON content type when a JSON body is present", async () => {
  const fetchMock = jest.fn(async () => jsonResponse({ ok: true }));
  const methods = loadCoreMethods(fetchMock);
  const app = { apiBaseUrl: "http://api.example/api/v1" };

  await methods.apiRequest.call(app, "/skills", {
    method: "POST",
    body: JSON.stringify({ name: "Skill" })
  });

  expect(fetchMock).toHaveBeenCalledWith("http://api.example/api/v1/skills", {
    method: "POST",
    body: JSON.stringify({ name: "Skill" }),
    headers: { "Content-Type": "application/json" }
  });
});
