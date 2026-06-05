const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadPlatformAgentMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/platform-agents.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        buildPlatformAgentsPath: () => "/admin/platform/agents",
        buildPlatformAgentPath: (agentKey) => `/admin/platform/agents/${agentKey}`,
        buildPlatformAgentRunsPath: () => "/admin/platform/agent-runs",
        buildPlatformAgentRunPath: (agentRunId) => `/admin/platform/agent-runs/${agentRunId}`,
        buildPlatformSkillsPath: () => "/admin/platform/skills",
        buildPlatformSkillPath: (packageName) => `/admin/platform/skills/${packageName}`,
        buildToolAuthorizationsPath: () => "/admin/platform/tool-authorizations"
      }
    },
    Promise,
    URLSearchParams,
    JSON,
    String,
    Array,
    Object,
    Number
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsolePlatformAgentMethods;
}

function agentDetail() {
  return {
    id: "agent-1",
    key: "pskill.runner",
    name: "PSkill Runner",
    role: "runner",
    description: "Observe runtime runs.",
    status: "active",
    active_version_id: "ver-1",
    active_version_label: "seed-1",
    version_count: 2,
    bindings: [
      {
        id: "binding-1",
        usage_key: "pskill.runtime.observe",
        active_version_id: "ver-1",
        updated_at: "2026-06-05T00:00:00Z"
      }
    ],
    active_version: {
      id: "ver-1",
      version_no: 1,
      version_label: "seed-1",
      status: "published",
      content_hash: "abcdef1234567890",
      spec_json: {
        goal: "Observe runtime state.",
        allowed_tools: ["psop.runtime.read"],
        allowed_skill_names: ["pskill-runner-field-assistant"],
        output_schema: { name: "RuntimeAgentObservation" }
      }
    },
    versions: [
      {
        id: "ver-2",
        version_no: 2,
        version_label: "draft-2",
        status: "draft",
        content_hash: "fedcba9876543210",
        spec_json: {
          goal: "Observe runtime state with extra context.",
          allowed_tools: ["psop.runtime.read", "psop.run_events.write_low"],
          allowed_skill_names: ["pskill-runner-field-assistant"],
          output_schema: { name: "RuntimeAgentObservation" }
        }
      },
      {
        id: "ver-1",
        version_no: 1,
        version_label: "seed-1",
        status: "published",
        content_hash: "abcdef1234567890",
        spec_json: {
          goal: "Observe runtime state.",
          allowed_tools: ["psop.runtime.read"],
          allowed_skill_names: ["pskill-runner-field-assistant"],
          output_schema: { name: "RuntimeAgentObservation" }
        }
      }
    ]
  };
}

test("platform agent methods load definitions, recent runs, and linked authorizations", async () => {
  const methods = loadPlatformAgentMethods();
  const detail = agentDetail();
  const run = {
    id: "agent-run-1",
    agent_key: "pskill.runner",
    status: "waiting_tool_authorization",
    started_at: "2026-06-05T00:00:00Z",
    updated_at: "2026-06-05T00:00:05Z"
  };
  const authorization = {
    id: "auth-1",
    agent_run_id: "agent-run-1",
    tool_name: "psop.agent_version.activate",
    status: "pending",
    side_effect_level: "high_write"
  };
  const context = {
    ...methods,
    busy: { platformAgents: false, platformAgentDetail: false },
    platformAgents: [],
    currentPlatformAgent: null,
    platformAgentRuns: [],
    platformAgentToolAuthorizations: [],
    apiRequest: jest.fn(async (url) => {
      if (url === "/agents") {
        return [{ key: "pskill.runner", status: "active", version_count: 2, bindings: [] }];
      }
      if (url === "/agents/pskill.runner") {
        return detail;
      }
      if (url === "/agent-runs?agent_key=pskill.runner") {
        return [run];
      }
      if (url === "/agent-runs/agent-run-1/tool-authorizations") {
        return [authorization];
      }
      return null;
    }),
    showNotice: jest.fn()
  };

  await methods.loadPlatformAgentsPage.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/agents");
  expect(context.apiRequest).toHaveBeenCalledWith("/agents/pskill.runner");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs?agent_key=pskill.runner");
  expect(context.apiRequest).toHaveBeenCalledWith("/agent-runs/agent-run-1/tool-authorizations");
  expect(context.currentPlatformAgent.key).toBe("pskill.runner");
  expect(context.platformAgentRuns).toEqual([run]);
  expect(context.platformAgentToolAuthorizations).toEqual([authorization]);
  expect(methods.platformAgentRunCountByStatus.call(context, "waiting_tool_authorization")).toBe(1);
  expect(methods.platformAgentAuthorizationCountByStatus.call(context, "pending")).toBe(1);
});

test("platform agent methods derive spec labels, bindings, paths, and diff previews", () => {
  const methods = loadPlatformAgentMethods();
  const detail = agentDetail();
  const context = {
    ...methods,
    platformAgents: [detail],
    currentPlatformAgent: detail,
    platformAgentRuns: [],
    platformAgentToolAuthorizations: []
  };

  expect(methods.platformAgentsPath()).toBe("/admin/platform/agents");
  expect(methods.platformAgentPath("pskill.runner")).toBe("/admin/platform/agents/pskill.runner");
  expect(methods.platformAgentsRunPath("agent-run-1")).toBe("/admin/platform/agent-runs/agent-run-1");
  expect(methods.platformAgentsSkillPath("pskill-runner-field-assistant")).toBe(
    "/admin/platform/skills/pskill-runner-field-assistant"
  );
  expect(methods.platformAgentAllowedTools(detail)).toEqual(["psop.runtime.read"]);
  expect(methods.platformAgentAllowedSkills(detail)).toEqual(["pskill-runner-field-assistant"]);
  expect(methods.platformAgentOutputSchemaName(detail)).toBe("RuntimeAgentObservation");
  expect(methods.platformAgentBindingRows.call(context, detail)[0].active_version_label).toBe("seed-1");
  expect(methods.platformAgentVersionChanged.call(context, detail.versions[0])).toBe(true);
  expect(methods.platformAgentVersionChanged.call(context, detail.versions[1])).toBe(false);
  expect(methods.platformAgentSpecDiffPreview.call(context, detail.versions[0])).toContain('"changed": true');
  expect(methods.platformAgentShortHash("abcdef1234567890")).toBe("abcdef123456");
});

test("platform agent write actions route through governance notice", () => {
  const methods = loadPlatformAgentMethods();
  const context = {
    ...methods,
    showNotice: jest.fn()
  };

  methods.requestPlatformAgentVersionAction.call(context, "activate", { version_label: "seed-1" });

  expect(context.showNotice).toHaveBeenCalledWith(
    "error",
    "激活 seed-1 需要通过 Governance Agent 与 Tool Authorization 执行。"
  );
});
