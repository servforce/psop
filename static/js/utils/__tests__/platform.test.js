const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadPlatformMethods() {
  const code = fs.readFileSync(path.join(__dirname, "../../app/platform.js"), "utf8");
  const sandbox = {
    window: {
      PSOPConsoleHelpers: {
        buildPlatformToolsPath: () => "/admin/platform/tools",
        buildPlatformToolPath: (toolName) => `/admin/platform/tools/${toolName}`,
        buildPlatformMemoryPath: () => "/admin/platform/memory",
        buildPlatformMemoryEntryPath: (memoryId) => `/admin/platform/memory/${memoryId}`,
        buildToolAuthorizationsPath: () => "/admin/platform/tool-authorizations"
      }
    },
    URLSearchParams,
    JSON,
    String,
    Array,
    Object,
    Number,
    Math
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.PSOPConsolePlatformMethods;
}

test("platform methods build filters, labels, and paths", () => {
  const methods = loadPlatformMethods();
  const context = {
    ...methods,
    platformToolFilters: {
      side_effect_level: "high_write",
      requires_authorization: "true"
    },
    memoryFilters: {
      namespace: "evaluation",
      memory_type: "episodic",
      status: "pending_review",
      agent_key: "psop.evaluator",
      q: "regression"
    },
    optionLabel: (options, value) => options.find((item) => item.value === value)?.label || value
  };

  expect(methods.platformToolQueryString.call(context)).toBe("side_effect_level=high_write&requires_authorization=true");
  expect(methods.memoryQueryString.call(context)).toBe(
    "namespace=evaluation&memory_type=episodic&status=pending_review&agent_key=psop.evaluator&q=regression&limit=100"
  );
  expect(methods.platformToolSideEffectLabel.call(context, "low_write")).toBe("Low Write");
  expect(methods.memoryStatusLabel.call(context, "pending_review")).toBe("待审核");
  expect(methods.platformToolPath("psop.memory.search")).toBe("/admin/platform/tools/psop.memory.search");
  expect(methods.platformMemoryEntryPath("mem-1")).toBe("/admin/platform/memory/mem-1");
});

test("platform methods load tools and select the first row", async () => {
  const methods = loadPlatformMethods();
  const tools = [
    {
      name: "psop.memory.search",
      side_effect_level: "read",
      requires_authorization: false,
      failure_rate: 0
    }
  ];
  const context = {
    ...methods,
    busy: { platformTools: false },
    platformToolFilters: { side_effect_level: "read", requires_authorization: "false" },
    platformTools: [],
    currentPlatformTool: null,
    apiRequest: jest.fn(async () => tools),
    showNotice: jest.fn()
  };

  await methods.loadPlatformToolsPage.call(context);

  expect(context.apiRequest).toHaveBeenCalledWith("/tools?side_effect_level=read&requires_authorization=false");
  expect(context.platformTools).toEqual(tools);
  expect(context.currentPlatformTool.name).toBe("psop.memory.search");
  expect(context.busy.platformTools).toBe(false);
});

test("platform methods search and save memory entries", async () => {
  const methods = loadPlatformMethods();
  const entry = {
    id: "mem-1",
    namespace: "evaluation",
    memory_type: "episodic",
    agent_key: "psop.evaluator",
    status: "pending_review",
    confidence: 62,
    title: "Finding pattern",
    content: "A regression finding pattern.",
    source_refs: [],
    tags: ["finding"],
    metadata: {}
  };
  const updated = {
    ...entry,
    status: "active",
    confidence: 88,
    title: "Updated pattern",
    content: "Updated content.",
    tags: ["finding", "runtime"]
  };
  const context = {
    ...methods,
    busy: { memoryEntries: false, memoryUpdate: false },
    memoryFilters: {
      namespace: "evaluation",
      memory_type: "episodic",
      status: "pending_review",
      agent_key: "",
      q: "finding"
    },
    memoryEntries: [],
    currentMemoryEntry: null,
    memoryEditForm: {},
    apiRequest: jest.fn(async (url) => {
      if (url === "/memory/search") {
        return [entry];
      }
      return updated;
    }),
    showNotice: jest.fn()
  };

  await methods.searchMemoryEntries.call(context);
  context.memoryEditForm = {
    status: "active",
    title: "Updated pattern",
    content: "Updated content.",
    confidence: 88,
    tags: "finding, runtime"
  };
  await methods.saveMemoryEntry.call(context);

  expect(context.apiRequest).toHaveBeenNthCalledWith(1, "/memory/search", {
    method: "POST",
    body: JSON.stringify({
      query: "finding",
      namespace: "evaluation",
      memory_type: "episodic",
      status: "pending_review",
      agent_key: null,
      limit: 100
    })
  });
  expect(context.apiRequest).toHaveBeenNthCalledWith(2, "/memory/mem-1", {
    method: "PATCH",
    body: JSON.stringify({
      status: "active",
      title: "Updated pattern",
      content: "Updated content.",
      confidence: 88,
      tags: ["finding", "runtime"]
    })
  });
  expect(context.memoryEntries[0].status).toBe("active");
  expect(context.currentMemoryEntry.title).toBe("Updated pattern");
  expect(context.busy.memoryUpdate).toBe(false);
});
