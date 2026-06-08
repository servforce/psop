const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadRuntimeMethods() {
  const source = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");
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
        mergeBySeq: (_existing, incoming) => incoming || [],
        mergeById: (existing, incoming) => [...(existing || []), ...(incoming || [])]
      }
    },
    Promise,
    Set,
    Date,
    Math,
    Number,
    String,
    Array,
    Object,
    JSON,
    URLSearchParams
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.PSOPConsoleRuntimeMethods;
}

test("run live page exposes embedded tool authorization tab", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/run-live.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "../../app.js"), "utf8");
  const runtimeJs = fs.readFileSync(path.join(__dirname, "../../app/runtime.js"), "utf8");

  expect(html).toContain("liveRunInteractionTab === 'authorizations'");
  expect(html).toContain("liveRunToolAuthorizations.length");
  expect(html).toContain("decideLiveRunToolAuthorization(authorization, 'approve')");
  expect(html).toContain("decideLiveRunToolAuthorization(authorization, 'reject')");
  expect(appJs).toContain("liveRunToolAuthorizations");
  expect(runtimeJs).toContain("/runs/${runId}/tool-authorizations");
});

test("loadRunLive loads run-scoped tool authorizations", async () => {
  const methods = loadRuntimeMethods();
  const authorization = {
    id: "auth-1",
    status: "pending",
    tool_name: "psop.agent_version.activate"
  };
  const context = {
    ...methods,
    busy: { liveRun: false },
    liveRunLoadedRunId: "",
    liveRunTerminalEvents: [],
    liveRunTraceEvents: [],
    liveRunToolAuthorizations: [],
    selectedLiveRunReplayItemKey: "",
    selectedLiveRunProcessEventKey: "",
    route: { params: {} },
    apiRequest: jest.fn(async (url) => {
      if (url === "/runs/run-1") {
        return { id: "run-1", status: "waiting_input" };
      }
      if (url === "/runs/run-1/bindings") {
        return [];
      }
      if (url === "/terminal/sessions/run-1") {
        return { terminal_session: { id: "terminal-1", status: "open" } };
      }
      if (url === "/runs/run-1/events") {
        return [];
      }
      if (url === "/runs/run-1/traces") {
        return [];
      }
      if (url === "/replay/runs/run-1") {
        return { timeline: [], snapshots: [], run_events: [], run_traces: [] };
      }
      if (url === "/runs/run-1/tool-authorizations") {
        return [authorization];
      }
      return null;
    }),
    ensureLiveRunProcessSelection: jest.fn(),
    scrollTerminalTranscriptToBottom: jest.fn(),
    connectRunWebSocket: jest.fn()
  };

  await methods.loadRunLive.call(context, "run-1");

  expect(context.apiRequest).toHaveBeenCalledWith("/runs/run-1/tool-authorizations");
  expect(context.liveRunToolAuthorizations).toEqual([authorization]);
  expect(context.liveRunAuthorizationCountByStatus("pending")).toBe(1);
});

test("run live authorization decisions update local list and refresh run", async () => {
  const methods = loadRuntimeMethods();
  const pending = { id: "auth-1", status: "pending", tool_name: "psop.repository.commit_patch" };
  const approved = { ...pending, status: "approved" };
  const context = {
    ...methods,
    busy: { toolAuthorizationAction: false },
    liveRun: { id: "run-1" },
    liveRunToolAuthorizations: [pending],
    toolAuthorizations: [],
    apiRequest: jest.fn(async (url, options) => {
      if (url === "/tool-authorizations/auth-1/approve" && options?.method === "POST") {
        return approved;
      }
      return null;
    }),
    loadRunLive: jest.fn(),
    showNotice: jest.fn(),
    replaceToolAuthorization: jest.fn()
  };

  await methods.decideLiveRunToolAuthorization.call(context, pending, "approve");

  expect(context.apiRequest).toHaveBeenCalledWith("/tool-authorizations/auth-1/approve", {
    method: "POST",
    body: JSON.stringify({
      response_payload: {
        decision_source: "run_live_ui"
      }
    })
  });
  expect(context.liveRunToolAuthorizations).toEqual([approved]);
  expect(context.replaceToolAuthorization).toHaveBeenCalledWith(approved);
  expect(context.loadRunLive).toHaveBeenCalledWith("run-1");
  expect(context.showNotice).toHaveBeenCalledWith("success", "工具授权已批准。");
});
