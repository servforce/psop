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
        buildPlatformAgentRunPath: (agentRunId, focus = {}) => {
          const params = new URLSearchParams();
          for (const key of ["tab", "tool_call_id", "authorization_id", "event_id"]) {
            if (focus[key]) {
              params.set(key, focus[key]);
            }
          }
          const query = params.toString();
          return query ? `/admin/platform/agent-runs/${agentRunId}?${query}` : `/admin/platform/agent-runs/${agentRunId}`;
        },
        buildPlatformSkillsPath: () => "/admin/platform/skills",
        buildPlatformSkillPath: (packageName) => `/admin/platform/skills/${packageName}`,
        buildToolAuthorizationsPath: (filters = {}) => {
          const params = new URLSearchParams();
          if (filters.status) {
            params.set("status", filters.status);
          }
          if (filters.tool_name) {
            params.set("tool_name", filters.tool_name);
          }
          const query = params.toString();
          return query ? `/admin/platform/tool-authorizations?${query}` : "/admin/platform/tool-authorizations";
        }
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
          allowed_tools: ["psop.runtime.read"],
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
  expect(methods.platformAgentsRunPath("agent-run-1", { tab: "authorizations" })).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=authorizations"
  );
  expect(methods.platformAgentWaitingAuthorizationPath({ id: "agent-run-1" })).toBe(
    "/admin/platform/agent-runs/agent-run-1?tab=authorizations"
  );
  expect(
    methods.platformAgentAuthorizationPath.call(context, {
      id: "auth-1",
      agent_run_id: "agent-run-1",
      tool_name: "psop.agent_version.activate",
      status: "pending"
    })
  ).toBe("/admin/platform/agent-runs/agent-run-1?tab=authorizations&authorization_id=auth-1");
  expect(
    methods.platformAgentsToolAuthorizationsPath({
      status: "pending",
      tool_name: "psop.agent_version.activate"
    })
  ).toBe("/admin/platform/tool-authorizations?status=pending&tool_name=psop.agent_version.activate");
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

test("platform agents page exposes precise waiting authorization navigation", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../../pages/platform-agents.html"), "utf8");

  expect(html).toContain("platformAgentWaitingAuthorizationPath(run)");
  expect(html).toContain("platformAgentAuthorizationPath(authorization)");
  expect(html).toContain("platformAgentsToolAuthorizationsPath(authorization)");
});

test("platform agent actions create drafts and activate published versions", async () => {
  const methods = loadPlatformAgentMethods();
  const detail = agentDetail();
  const createdDraftDetail = {
    ...detail,
    version_count: 3,
    versions: [
      {
        id: "ver-3",
        version_no: 3,
        version_label: "draft-v3",
        status: "draft",
        spec_json: detail.active_version.spec_json
      },
      ...detail.versions
    ]
  };
  const activatedDetail = {
    ...detail,
    active_version_id: "ver-2",
    active_version_label: "draft-2",
    active_version: detail.versions[0],
    bindings: detail.bindings.map((binding) => ({ ...binding, active_version_id: "ver-2" }))
  };
  const context = {
    ...methods,
    busy: { platformAgentAction: false, platformAgentDetail: false, platformAgents: false },
    currentPlatformAgent: detail,
    platformAgents: [detail],
    platformAgentRuns: [],
    platformAgentToolAuthorizations: [],
    apiRequest: jest.fn(async (url, options) => {
      if (url === "/agents/pskill.runner/versions" && options?.method === "POST") {
        return createdDraftDetail;
      }
      if (url === "/agents/pskill.runner/versions/ver-2/activate" && options?.method === "POST") {
        return activatedDetail;
      }
      if (url === "/agents") {
        return [activatedDetail];
      }
      return null;
    }),
    showNotice: jest.fn()
  };

  await methods.requestPlatformAgentVersionAction.call(context, "create_draft");
  await methods.requestPlatformAgentVersionAction.call(context, "activate", detail.versions[0]);

  const createBody = JSON.parse(context.apiRequest.mock.calls[0][1].body);
  const activateBody = JSON.parse(context.apiRequest.mock.calls[1][1].body);
  expect(createBody.version_label).toBe("draft-v3");
  expect(createBody.spec_json.allowed_tools).toEqual(["psop.runtime.read"]);
  expect(activateBody).toEqual({ update_bindings: true });
  expect(context.currentPlatformAgent.active_version_id).toBe("ver-2");
  expect(context.platformAgentDetailTab).toBe("versions");
  expect(context.showNotice).toHaveBeenCalledWith("success", "AgentVersion draft 已创建。");
  expect(context.showNotice).toHaveBeenCalledWith("success", "AgentVersion 已激活。");
});
