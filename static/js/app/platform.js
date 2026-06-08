(function () {
  const {
    buildTasksPath,
    buildPlatformAgentPath,
    buildPlatformAgentRunsPath,
    buildPlatformAgentRunPath,
    buildPlatformSkillsPath,
    buildPlatformSkillPath,
    buildPlatformToolsPath,
    buildPlatformToolPath,
    buildPlatformMemoryPath,
    buildPlatformMemoryEntryPath,
    buildToolAuthorizationsPath,
    buildGovernanceProposalPath,
    buildRunLivePath,
    buildReplayPath,
    resolveWsUrl
  } = window.PSOPConsoleHelpers;

  const TOOL_SIDE_EFFECT_OPTIONS = [
    { value: "read", label: "Read" },
    { value: "compute", label: "Compute" },
    { value: "low_write", label: "Low Write" },
    { value: "high_write", label: "High Write" },
    { value: "external_action", label: "External" },
    { value: "physical_action", label: "Physical" }
  ];

  const TOOL_AUTH_OPTIONS = [
    { value: "true", label: "需要授权" },
    { value: "false", label: "无需授权" }
  ];

  const MEMORY_TYPE_OPTIONS = [
    { value: "short_term", label: "Short Term" },
    { value: "semantic", label: "Semantic" },
    { value: "episodic", label: "Episodic" },
    { value: "procedural", label: "Procedural" },
    { value: "artifact", label: "Artifact" }
  ];

  const MEMORY_STATUS_OPTIONS = [
    { value: "pending_review", label: "待审核" },
    { value: "active", label: "启用" },
    { value: "rejected", label: "已拒绝" },
    { value: "archived", label: "已归档" }
  ];

  const AGENT_RUN_STATUS_OPTIONS = [
    { value: "queued", label: "排队中" },
    { value: "running", label: "运行中" },
    { value: "waiting_tool_authorization", label: "等待授权" },
    { value: "succeeded", label: "成功" },
    { value: "failed", label: "失败" },
    { value: "cancelled", label: "已取消" }
  ];

  const AGENT_KEYS = [
    "pskill.builder",
    "pskill.compiler",
    "pskill.tester",
    "pskill.runner",
    "pskill.evaluator",
    "psop.governance"
  ];

  const SKILL_PACKAGE_SCOPE_OPTIONS = [
    { value: "psop", label: "PSOP" },
    { value: "public", label: "Public" }
  ];

  const SKILL_PACKAGE_STATUS_OPTIONS = [
    { value: "active", label: "启用" },
    { value: "archived", label: "已归档" }
  ];

  window.PSOPConsolePlatformMethods = {
    async loadPlatformAgentRunsPage() {
      this.syncAgentRunFiltersFromLocation();
      await this.loadAgentRuns();
      if (!this.currentAgentRun && this.agentRuns.length) {
        await this.loadPlatformAgentRunDetail(this.agentRuns[0].id);
      }
    },

    async loadPlatformAgentRunPage(agentRunId) {
      this.syncAgentRunDetailFromLocation();
      await this.loadAgentRuns();
      await this.loadPlatformAgentRunDetail(agentRunId);
      this.syncAgentRunDetailFromLocation();
    },

    async loadAgentRuns() {
      this.busy.agentRuns = true;
      try {
        const query = this.agentRunQueryString();
        const suffix = query ? `?${query}` : "";
        const runs = await this.apiRequest(`/agent-runs${suffix}`);
        this.agentRuns = Array.isArray(runs) ? runs : [];
        if (this.currentAgentRun) {
          const refreshed = this.agentRuns.find((item) => item.id === this.currentAgentRun.id);
          this.currentAgentRun = refreshed || this.currentAgentRun;
        }
      } catch (error) {
        this.showNotice("error", error.message || "AgentRun 加载失败。");
      } finally {
        this.busy.agentRuns = false;
      }
    },

    async loadPlatformAgentRunDetail(agentRunId) {
      const id = String(agentRunId || "").trim();
      if (!id) {
        return;
      }
      this.busy.agentRunDetail = true;
      try {
        const [run, events, modelCalls, toolCalls, skillActivations, toolAuthorizations, memoryEntries] = await Promise.all([
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/events`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/model-calls`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/tool-calls`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/skill-activations`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/tool-authorizations`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/memory-entries`)
        ]);
        this.applyAgentRunActivitySnapshot({
          agent_run: run,
          events,
          model_calls: modelCalls,
          tool_calls: toolCalls,
          skill_activations: skillActivations,
          tool_authorizations: toolAuthorizations,
          memory_entries: memoryEntries
        });
        this.connectAgentRunActivityWebSocket(id);
      } catch (error) {
        this.showNotice("error", error.message || "AgentRun 详情加载失败。");
      } finally {
        this.busy.agentRunDetail = false;
      }
    },

    connectAgentRunActivityWebSocket(agentRunId) {
      const id = String(agentRunId || "").trim();
      if (!id || typeof WebSocket === "undefined") {
        return false;
      }
      if (
        this.agentRunActivityWs &&
        this.agentRunActivityWsAgentRunId === id &&
        [WebSocket.CONNECTING, WebSocket.OPEN].includes(this.agentRunActivityWs.readyState)
      ) {
        return true;
      }

      this.disconnectAgentRunActivityWebSocket();
      const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, `/ws/agent-runs/${encodeURIComponent(id)}`));
      this.agentRunActivityWs = socket;
      this.agentRunActivityWsAgentRunId = id;
      this.agentRunActivityWsStatus = "connecting";
      socket.addEventListener("open", () => {
        if (this.agentRunActivityWs === socket) {
          this.agentRunActivityWsStatus = "open";
        }
      });
      socket.addEventListener("message", (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.event_type === "agent_run.activity.snapshot" && message.payload) {
            this.applyAgentRunActivitySnapshot(message.payload);
          }
          if (message.event_type === "agent_run.activity.error") {
            this.showNotice("error", message.payload?.message || "获取 AgentRun 活动流失败。");
            this.disconnectAgentRunActivityWebSocket();
          }
        } catch {
          // Ignore malformed activity messages; the REST detail remains available.
        }
      });
      socket.addEventListener("close", () => {
        if (this.agentRunActivityWs === socket) {
          this.agentRunActivityWsStatus = "closed";
        }
      });
      socket.addEventListener("error", () => {
        if (this.agentRunActivityWs === socket) {
          this.agentRunActivityWsStatus = "error";
        }
      });
      return true;
    },

    disconnectAgentRunActivityWebSocket() {
      if (this.agentRunActivityWs) {
        this.agentRunActivityWs.close();
      }
      this.agentRunActivityWs = null;
      this.agentRunActivityWsAgentRunId = "";
      this.agentRunActivityWsStatus = "idle";
    },

    applyAgentRunActivitySnapshot(snapshot) {
      if (!snapshot || typeof snapshot !== "object") {
        return;
      }
      if (snapshot.agent_run?.id) {
        this.currentAgentRun = snapshot.agent_run;
        this.replaceAgentRun(snapshot.agent_run);
      }
      if (Array.isArray(snapshot.events)) {
        this.currentAgentRunEvents = snapshot.events;
      }
      if (Array.isArray(snapshot.model_calls)) {
        this.currentAgentRunModelCalls = snapshot.model_calls;
      }
      if (Array.isArray(snapshot.tool_calls)) {
        this.currentAgentRunToolCalls = snapshot.tool_calls;
      }
      if (Array.isArray(snapshot.skill_activations)) {
        this.currentAgentRunSkillActivations = snapshot.skill_activations;
      }
      if (Array.isArray(snapshot.tool_authorizations)) {
        this.currentAgentRunToolAuthorizations = snapshot.tool_authorizations;
      }
      if (Array.isArray(snapshot.memory_entries)) {
        this.currentAgentRunMemoryEntries = snapshot.memory_entries;
      }
    },

    replaceAgentRun(run) {
      if (!run?.id) {
        return;
      }
      const index = this.agentRuns.findIndex((item) => item.id === run.id);
      if (index >= 0) {
        this.agentRuns.splice(index, 1, run);
      } else {
        this.agentRuns.unshift(run);
      }
    },

    agentRunQueryString() {
      const params = new URLSearchParams();
      const fields = ["agent_key", "status", "owner_type", "owner_id"];
      for (const field of fields) {
        const value = String(this.agentRunFilters[field] || "").trim();
        if (value) {
          params.set(field, value);
        }
      }
      return params.toString();
    },

    applyAgentRunFilters() {
      this.disconnectAgentRunActivityWebSocket();
      this.currentAgentRun = null;
      this.currentAgentRunEvents = [];
      this.currentAgentRunModelCalls = [];
      this.currentAgentRunToolCalls = [];
      this.currentAgentRunSkillActivations = [];
      this.currentAgentRunToolAuthorizations = [];
      this.currentAgentRunMemoryEntries = [];
      return this.loadPlatformAgentRunsPage();
    },

    resetAgentRunFilters() {
      this.agentRunFilters = {
        agent_key: "",
        status: "",
        owner_type: "",
        owner_id: ""
      };
      return this.applyAgentRunFilters();
    },

    async loadPlatformSkillsPage() {
      await Promise.all([
        this.loadSkillPackages(),
        this.loadPlatformAgentDefinitions()
      ]);
      if (!this.currentSkillPackage && this.skillPackages.length) {
        await this.loadSkillPackageDetail(this.skillPackages[0].name);
      }
    },

    async loadPlatformSkillPage(packageName) {
      await Promise.all([
        this.loadSkillPackages(),
        this.loadPlatformAgentDefinitions()
      ]);
      await this.loadSkillPackageDetail(packageName);
    },

    async loadSkillPackages() {
      this.busy.skillPackages = true;
      try {
        const query = this.skillPackageQueryString();
        const suffix = query ? `?${query}` : "";
        const packages = await this.apiRequest(`/skills${suffix}`);
        this.skillPackages = Array.isArray(packages) ? packages : [];
        if (this.currentSkillPackage) {
          const refreshed = this.skillPackages.find((item) => item.name === this.currentSkillPackage.name);
          this.currentSkillPackage = refreshed ? { ...this.currentSkillPackage, ...refreshed } : this.currentSkillPackage;
        }
      } catch (error) {
        this.showNotice("error", error.message || "Skill packages 加载失败。");
      } finally {
        this.busy.skillPackages = false;
      }
    },

    async loadSkillPackageDetail(packageName) {
      const name = String(packageName || "").trim();
      if (!name) {
        return;
      }
      this.busy.skillPackageDetail = true;
      try {
        this.currentSkillPackage = await this.apiRequest(`/skills/${encodeURIComponent(name)}`);
        this.replaceSkillPackage(this.currentSkillPackage);
      } catch (error) {
        this.showNotice("error", error.message || "Skill package 详情加载失败。");
      } finally {
        this.busy.skillPackageDetail = false;
      }
    },

    async syncSkillPackages() {
      this.busy.skillPackageAction = true;
      try {
        this.skillPackageSyncResult = await this.apiRequest("/skills/sync", { method: "POST" });
        await this.loadPlatformSkillsPage();
        this.showNotice("success", "Skill packages 已同步。");
      } catch (error) {
        this.showNotice("error", error.message || "Skill packages 同步失败。");
      } finally {
        this.busy.skillPackageAction = false;
      }
    },

    async queueSkillPackageSync() {
      this.busy.skillPackageAction = true;
      try {
        this.skillPackageSyncJob = await this.apiRequest("/skills/sync/queue", {
          method: "POST",
          body: JSON.stringify({
            idempotency_key: `ui-skill-sync-${Date.now()}`
          })
        });
        this.showNotice("success", "Skill package 同步任务已排队。");
      } catch (error) {
        this.showNotice("error", error.message || "Skill package 同步任务排队失败。");
      } finally {
        this.busy.skillPackageAction = false;
      }
    },

    async createSkillPackageVersion() {
      if (!this.currentSkillPackage?.name) {
        return;
      }
      const parent = this.currentSkillPackage.active_version || (this.currentSkillPackage.versions || [])[0] || null;
      const defaultLabel = this.nextSkillPackageVersionLabel();
      const versionLabel = String(this.promptSkillPackageVersionLabel(defaultLabel) || "").trim();
      if (!versionLabel) {
        return;
      }
      const manifestText = this.promptSkillPackageVersionManifest(parent?.manifest_json || {
        name: this.currentSkillPackage.name,
        description: this.currentSkillPackage.description || ""
      });
      if (manifestText === null || manifestText === undefined) {
        return;
      }
      let manifest;
      try {
        manifest = JSON.parse(String(manifestText));
      } catch (error) {
        this.showNotice("error", "Skill package manifest 必须是有效 JSON。");
        return;
      }
      this.busy.skillPackageAction = true;
      try {
        const payload = {
          version_label: versionLabel,
          parent_version_id: parent?.id || null,
          manifest_json: manifest,
          body_object_key: this.skillPackageUploadBodyObjectKey(versionLabel),
          resource_index: this.skillPackageVersionResourceIndex(parent),
          allowed_tools: this.skillPackageAllowedToolsFromManifest(manifest, parent?.allowed_tools || [])
        };
        this.currentSkillPackage = await this.apiRequest(
          `/skills/${encodeURIComponent(this.currentSkillPackage.name)}/versions`,
          {
            method: "POST",
            body: JSON.stringify(payload)
          }
        );
        this.replaceSkillPackage(this.currentSkillPackage);
        await this.loadSkillPackages();
        this.showNotice("success", "Skill package version 已创建。");
      } catch (error) {
        this.showNotice("error", error.message || "Skill package version 创建失败。");
      } finally {
        this.busy.skillPackageAction = false;
      }
    },

    async validateSkillPackageVersion(version) {
      if (!this.currentSkillPackage?.name || !version?.id) {
        return;
      }
      this.busy.skillPackageAction = true;
      try {
        const updated = await this.apiRequest(
          `/skills/${encodeURIComponent(this.currentSkillPackage.name)}/versions/${encodeURIComponent(version.id)}/validate`,
          { method: "POST" }
        );
        this.replaceSkillPackageVersion(updated);
        this.showNotice("success", "Skill package version 已校验。");
      } catch (error) {
        this.showNotice("error", error.message || "Skill package version 校验失败。");
      } finally {
        this.busy.skillPackageAction = false;
      }
    },

    async activateSkillPackageVersion(version) {
      if (!this.currentSkillPackage?.name || !version?.id) {
        return;
      }
      this.busy.skillPackageAction = true;
      try {
        this.currentSkillPackage = await this.apiRequest(
          `/skills/${encodeURIComponent(this.currentSkillPackage.name)}/versions/${encodeURIComponent(version.id)}/activate`,
          { method: "POST" }
        );
        this.replaceSkillPackage(this.currentSkillPackage);
        await this.loadSkillPackages();
        this.showNotice("success", "Skill package version 已激活。");
      } catch (error) {
        this.showNotice("error", error.message || "Skill package version 激活失败。");
      } finally {
        this.busy.skillPackageAction = false;
      }
    },

    async loadPlatformAgentDefinitions() {
      if (this.platformAgentDefinitions.length) {
        return;
      }
      this.busy.platformAgentDefinitions = true;
      try {
        const summaries = await this.apiRequest("/agents");
        const agents = Array.isArray(summaries) ? summaries : [];
        this.platformAgentDefinitions = await Promise.all(
          agents.map((agent) => this.apiRequest(`/agents/${encodeURIComponent(agent.key)}`))
        );
      } catch (error) {
        this.showNotice("error", error.message || "Agent 定义加载失败。");
      } finally {
        this.busy.platformAgentDefinitions = false;
      }
    },

    skillPackageQueryString() {
      const params = new URLSearchParams();
      const scope = String(this.skillPackageFilters.scope || "").trim();
      const status = String(this.skillPackageFilters.status || "").trim();
      if (scope) {
        params.set("scope", scope);
      }
      if (status) {
        params.set("status", status);
      }
      return params.toString();
    },

    applySkillPackageFilters() {
      this.currentSkillPackage = null;
      return this.loadPlatformSkillsPage();
    },

    resetSkillPackageFilters() {
      this.skillPackageFilters = { scope: "", status: "" };
      return this.applySkillPackageFilters();
    },

    replaceSkillPackage(skillPackage) {
      if (!skillPackage?.name) {
        return;
      }
      const index = this.skillPackages.findIndex((item) => item.name === skillPackage.name);
      if (index >= 0) {
        this.skillPackages.splice(index, 1, { ...this.skillPackages[index], ...skillPackage });
      } else {
        this.skillPackages.unshift(skillPackage);
      }
    },

    replaceSkillPackageVersion(version) {
      if (!this.currentSkillPackage?.versions || !version?.id) {
        return;
      }
      const index = this.currentSkillPackage.versions.findIndex((item) => item.id === version.id);
      if (index >= 0) {
        this.currentSkillPackage.versions.splice(index, 1, version);
      }
      if (this.currentSkillPackage.active_version?.id === version.id) {
        this.currentSkillPackage.active_version = version;
      }
    },

    async loadPlatformToolsPage() {
      await this.loadPlatformTools();
      if (!this.currentPlatformTool && this.platformTools.length) {
        this.currentPlatformTool = this.platformTools[0];
      }
      if (this.currentPlatformTool?.name) {
        await this.loadPlatformToolCalls(this.currentPlatformTool.name);
      }
    },

    async loadPlatformTools() {
      this.busy.platformTools = true;
      try {
        const query = this.platformToolQueryString();
        const suffix = query ? `?${query}` : "";
        const tools = await this.apiRequest(`/tools${suffix}`);
        this.platformTools = Array.isArray(tools) ? tools : [];
        if (this.currentPlatformTool) {
          const refreshed = this.platformTools.find((item) => item.name === this.currentPlatformTool.name);
          this.currentPlatformTool = refreshed || this.currentPlatformTool;
        }
      } catch (error) {
        this.showNotice("error", error.message || "工具注册表加载失败。");
      } finally {
        this.busy.platformTools = false;
      }
    },

    async loadPlatformToolDetail(toolName) {
      const name = String(toolName || "").trim();
      if (!name) {
        return;
      }
      this.busy.platformTools = true;
      try {
        const [tool, calls] = await Promise.all([
          this.apiRequest(`/tools/${encodeURIComponent(name)}`),
          this.apiRequest(`/tools/${encodeURIComponent(name)}/calls?limit=10`)
        ]);
        this.currentPlatformTool = tool;
        this.currentPlatformToolCalls = Array.isArray(calls) ? calls : [];
        this.platformToolTestResult = null;
      } catch (error) {
        this.showNotice("error", error.message || "工具详情加载失败。");
      } finally {
        this.busy.platformTools = false;
      }
    },

    async loadPlatformToolCalls(toolName) {
      const name = String(toolName || "").trim();
      if (!name) {
        this.currentPlatformToolCalls = [];
        return;
      }
      try {
        const calls = await this.apiRequest(`/tools/${encodeURIComponent(name)}/calls?limit=10`);
        this.currentPlatformToolCalls = Array.isArray(calls) ? calls : [];
      } catch {
        this.currentPlatformToolCalls = [];
      }
    },

    platformToolQueryString() {
      const params = new URLSearchParams();
      const sideEffectLevel = String(this.platformToolFilters.side_effect_level || "").trim();
      const requiresAuthorization = String(this.platformToolFilters.requires_authorization || "").trim();
      if (sideEffectLevel) {
        params.set("side_effect_level", sideEffectLevel);
      }
      if (requiresAuthorization) {
        params.set("requires_authorization", requiresAuthorization);
      }
      return params.toString();
    },

    applyPlatformToolFilters() {
      return this.loadPlatformToolsPage();
    },

    resetPlatformToolFilters() {
      this.platformToolFilters = {
        side_effect_level: "",
        requires_authorization: ""
      };
      return this.loadPlatformToolsPage();
    },

    selectPlatformTool(tool) {
      if (!tool?.name) {
        return;
      }
      this.currentPlatformTool = tool;
      this.loadPlatformToolCalls(tool.name);
      this.platformToolTestResult = null;
    },

    async testPlatformTool(tool) {
      if (!tool?.name) {
        return;
      }
      this.busy.platformToolAction = true;
      try {
        this.platformToolTestResult = await this.apiRequest(`/tools/${encodeURIComponent(tool.name)}/test`, {
          method: "POST",
          body: JSON.stringify({
            arguments_summary: this.platformToolTestArguments(tool),
            requested_side_effect_level: tool.side_effect_level
          })
        });
        this.showNotice(
          this.platformToolTestResult.executable ? "success" : "error",
          this.platformToolTestResult.executable
            ? `${tool.name} dry-run 通过。`
            : `${tool.name} dry-run 未执行：${this.platformToolTestResult.policy_reason}。`
        );
      } catch (error) {
        this.showNotice("error", error.message || "工具 dry-run 失败。");
      } finally {
        this.busy.platformToolAction = false;
      }
    },

    platformToolTestArguments(tool) {
      if (!tool?.name) {
        return {};
      }
      if (tool.name === "psop.memory.search") {
        return { query: "runtime findings", limit: 3 };
      }
      if (tool.name === "psop.compiler.validate_formal_v5") {
        return { artifact: { graph_version: "formal-v5", nodes: [], edges: [] } };
      }
      return { sample: true };
    },

    async loadPlatformMemoryPage(memoryId) {
      await this.loadMemoryEntries();
      const requestedId = String(memoryId || "").trim();
      if (requestedId) {
        const selected = this.memoryEntries.find((item) => item.id === requestedId);
        if (selected) {
          this.selectMemoryEntry(selected);
        } else {
          await this.loadMemoryEntry(requestedId);
        }
        return;
      }
      if (!this.currentMemoryEntry && this.memoryEntries.length) {
        this.selectMemoryEntry(this.memoryEntries[0]);
      }
    },

    async loadMemoryEntries() {
      this.busy.memoryEntries = true;
      try {
        const query = this.memoryQueryString();
        const suffix = query ? `?${query}` : "";
        const entries = await this.apiRequest(`/memory${suffix}`);
        this.memoryEntries = Array.isArray(entries) ? entries : [];
        if (this.currentMemoryEntry) {
          const refreshed = this.memoryEntries.find((item) => item.id === this.currentMemoryEntry.id);
          if (refreshed) {
            this.selectMemoryEntry(refreshed);
          }
        }
      } catch (error) {
        this.showNotice("error", error.message || "Memory 加载失败。");
      } finally {
        this.busy.memoryEntries = false;
      }
    },

    async loadMemoryEntry(memoryId) {
      const id = String(memoryId || "").trim();
      if (!id) {
        return null;
      }
      this.busy.memoryEntries = true;
      try {
        const entry = await this.apiRequest(`/memory/${encodeURIComponent(id)}`);
        this.replaceMemoryEntry(entry);
        this.selectMemoryEntry(entry);
        return entry;
      } catch (error) {
        this.showNotice("error", error.message || "Memory 详情加载失败。");
        return null;
      } finally {
        this.busy.memoryEntries = false;
      }
    },

    async searchMemoryEntries() {
      this.busy.memoryEntries = true;
      try {
        const entries = await this.apiRequest("/memory/search", {
          method: "POST",
          body: JSON.stringify({
            query: String(this.memoryFilters.q || "").trim(),
            namespace: this.normalizedMemoryFilter("namespace"),
            memory_type: this.normalizedMemoryFilter("memory_type"),
            status: this.normalizedMemoryFilter("status"),
            agent_key: this.normalizedMemoryFilter("agent_key"),
            limit: 100
          })
        });
        this.memoryEntries = Array.isArray(entries) ? entries : [];
        if (this.memoryEntries.length) {
          this.selectMemoryEntry(this.memoryEntries[0]);
        } else {
          this.currentMemoryEntry = null;
        }
      } catch (error) {
        this.showNotice("error", error.message || "Memory 搜索失败。");
      } finally {
        this.busy.memoryEntries = false;
      }
    },

    memoryQueryString() {
      const params = new URLSearchParams();
      const fields = ["namespace", "memory_type", "status", "agent_key", "q"];
      for (const field of fields) {
        const value = String(this.memoryFilters[field] || "").trim();
        if (value) {
          params.set(field, value);
        }
      }
      params.set("limit", "100");
      return params.toString();
    },

    normalizedMemoryFilter(field) {
      const value = String(this.memoryFilters[field] || "").trim();
      return value || null;
    },

    resetMemoryFilters() {
      this.memoryFilters = {
        namespace: "",
        memory_type: "",
        status: "pending_review",
        agent_key: "",
        q: ""
      };
      return this.loadPlatformMemoryPage();
    },

    selectMemoryEntry(entry) {
      if (!entry?.id) {
        return;
      }
      this.currentMemoryEntry = entry;
      this.prepareMemoryEditForm(entry);
    },

    prepareMemoryEditForm(entry) {
      this.memoryEditForm = {
        status: entry?.status || "pending_review",
        title: entry?.title || "",
        content: entry?.content || "",
        confidence: Number.isFinite(Number(entry?.confidence)) ? Number(entry.confidence) : 50,
        tags: Array.isArray(entry?.tags) ? entry.tags.join(", ") : ""
      };
    },

    async saveMemoryEntry() {
      const entry = this.currentMemoryEntry;
      if (!entry?.id) {
        return;
      }
      const content = String(this.memoryEditForm.content || "").trim();
      if (!content) {
        this.showNotice("error", "Memory content 不能为空。");
        return;
      }
      this.busy.memoryUpdate = true;
      try {
        const updated = await this.apiRequest(`/memory/${encodeURIComponent(entry.id)}`, {
          method: "PATCH",
          body: JSON.stringify({
            status: this.memoryEditForm.status || null,
            title: String(this.memoryEditForm.title || "").trim(),
            content,
            confidence: this.normalizedMemoryConfidence(this.memoryEditForm.confidence),
            tags: this.parseMemoryTags(this.memoryEditForm.tags)
          })
        });
        this.replaceMemoryEntry(updated);
        this.selectMemoryEntry(updated);
        this.showNotice("success", "Memory 已保存。");
      } catch (error) {
        this.showNotice("error", error.message || "Memory 保存失败。");
      } finally {
        this.busy.memoryUpdate = false;
      }
    },

    async updateMemoryEntryStatus(entry, status) {
      if (!entry?.id || !status) {
        return;
      }
      this.busy.memoryUpdate = true;
      try {
        const updated = await this.apiRequest(`/memory/${encodeURIComponent(entry.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ status })
        });
        this.replaceMemoryEntry(updated);
        this.selectMemoryEntry(updated);
        this.showNotice("success", "Memory 状态已更新。");
      } catch (error) {
        this.showNotice("error", error.message || "Memory 状态更新失败。");
      } finally {
        this.busy.memoryUpdate = false;
      }
    },

    async queueMemoryCompaction() {
      this.busy.memoryCompaction = true;
      try {
        const status = this.normalizedMemoryFilter("status") || "active";
        this.memoryCompactionJob = await this.apiRequest("/memory/compactions/queue", {
          method: "POST",
          body: JSON.stringify({
            namespace: this.normalizedMemoryFilter("namespace"),
            memory_type: this.normalizedMemoryFilter("memory_type"),
            status,
            agent_key: this.normalizedMemoryFilter("agent_key"),
            target_namespace: this.normalizedMemoryFilter("namespace"),
            target_memory_type: "artifact",
            target_status: "pending_review",
            title: "Compacted platform memory",
            archive_source_entries: status === "active",
            idempotency_key: `ui-memory-compaction-${Date.now()}`
          })
        });
        this.showNotice("success", "Memory compaction 任务已排队。");
      } catch (error) {
        this.showNotice("error", error.message || "Memory compaction 任务排队失败。");
      } finally {
        this.busy.memoryCompaction = false;
      }
    },

    replaceMemoryEntry(entry) {
      if (!entry?.id) {
        return;
      }
      const index = this.memoryEntries.findIndex((item) => item.id === entry.id);
      if (index >= 0) {
        this.memoryEntries.splice(index, 1, entry);
      } else {
        this.memoryEntries.unshift(entry);
      }
    },

    parseMemoryTags(value) {
      return String(value || "")
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    },

    normalizedMemoryConfidence(value) {
      const confidence = Number(value);
      if (!Number.isFinite(confidence)) {
        return 50;
      }
      return Math.max(0, Math.min(100, Math.round(confidence)));
    },

    platformAgentRunsPath(filters = {}) {
      return buildPlatformAgentRunsPath(filters);
    },

    platformAgentDefinitionPath(agentKey) {
      return buildPlatformAgentPath(agentKey);
    },

    skillPackageAgentRunsPath(agentKey) {
      return buildPlatformAgentRunsPath({ agent_key: agentKey });
    },

    platformTasksPath() {
      return buildTasksPath();
    },

    platformTaskJobPath(job) {
      return buildTasksPath({
        job_type: job?.job_type || "",
        q: job?.id || ""
      });
    },

    platformAgentRunPath(agentRunId) {
      return buildPlatformAgentRunPath(agentRunId);
    },

    agentRunToolCallPath(agentRunId, toolCallId) {
      return buildPlatformAgentRunPath(agentRunId, { tab: "tools", tool_call_id: toolCallId });
    },

    agentRunAuthorizationPath(agentRunId, authorizationId) {
      return buildPlatformAgentRunPath(agentRunId, { tab: "authorizations", authorization_id: authorizationId });
    },

    platformSkillsPath() {
      return buildPlatformSkillsPath();
    },

    platformSkillPath(packageName) {
      return buildPlatformSkillPath(packageName);
    },

    platformRunLivePath(runId) {
      return buildRunLivePath(runId);
    },

    platformToolsPath() {
      return buildPlatformToolsPath();
    },

    platformToolPath(toolName) {
      return buildPlatformToolPath(toolName);
    },

    platformToolCallPath(call) {
      return buildPlatformAgentRunPath(call?.agent_run_id || "", { tab: "tools", tool_call_id: call?.id || "" });
    },

    platformMemoryPath() {
      return buildPlatformMemoryPath();
    },

    platformMemoryEntryPath(memoryId) {
      return buildPlatformMemoryEntryPath(memoryId);
    },

    platformToolAuthorizationsPath(toolName) {
      const normalized = String(toolName || "").trim();
      return normalized
        ? buildToolAuthorizationsPath({ tool_name: normalized })
        : buildToolAuthorizationsPath({ status: "pending" });
    },

    platformToolSideEffectOptions() {
      return TOOL_SIDE_EFFECT_OPTIONS;
    },

    platformToolAuthOptions() {
      return TOOL_AUTH_OPTIONS;
    },

    agentRunStatusOptions() {
      return AGENT_RUN_STATUS_OPTIONS;
    },

    agentKeyOptions() {
      return AGENT_KEYS.map((key) => ({ value: key, label: key }));
    },

    skillPackageScopeOptions() {
      return SKILL_PACKAGE_SCOPE_OPTIONS;
    },

    skillPackageStatusOptions() {
      return SKILL_PACKAGE_STATUS_OPTIONS;
    },

    memoryTypeOptions() {
      return MEMORY_TYPE_OPTIONS;
    },

    memoryStatusOptions() {
      return MEMORY_STATUS_OPTIONS;
    },

    platformToolSideEffectLabel(value) {
      return this.optionLabel(TOOL_SIDE_EFFECT_OPTIONS, value);
    },

    platformToolAuthLabel(value) {
      return value ? "需要授权" : "无需授权";
    },

    agentRunStatusLabel(value) {
      return this.optionLabel(AGENT_RUN_STATUS_OPTIONS, value);
    },

    skillPackageScopeLabel(value) {
      return this.optionLabel(SKILL_PACKAGE_SCOPE_OPTIONS, value);
    },

    skillPackageStatusLabel(value) {
      return this.optionLabel(SKILL_PACKAGE_STATUS_OPTIONS, value);
    },

    memoryTypeLabel(value) {
      return this.optionLabel(MEMORY_TYPE_OPTIONS, value);
    },

    memoryStatusLabel(value) {
      return this.optionLabel(MEMORY_STATUS_OPTIONS, value);
    },

    platformToolSideEffectTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (["read", "compute"].includes(normalized)) {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (normalized === "low_write") {
        return "border-sky-500/25 bg-sky-500/10 text-sky-200";
      }
      if (normalized === "high_write") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      return "border-rose-500/25 bg-rose-500/10 text-rose-200";
    },

    platformToolAuthTone(value) {
      return value
        ? "border-amber-500/25 bg-amber-500/10 text-amber-200"
        : "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
    },

    agentRunStatusTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (["succeeded", "success"].includes(normalized)) {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (["queued", "running"].includes(normalized)) {
        return "border-sky-500/25 bg-sky-500/10 text-sky-200";
      }
      if (normalized === "waiting_tool_authorization") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      if (["failed", "cancelled", "canceled"].includes(normalized)) {
        return "border-rose-500/25 bg-rose-500/10 text-rose-200";
      }
      return "border-slate-600 bg-slate-900/70 text-slate-300";
    },

    agentRunDurationLabel(run) {
      const milliseconds = this.agentRunDurationMs(run);
      if (milliseconds === null) {
        return "N/A";
      }
      if (typeof this.formatDuration === "function") {
        return this.formatDuration(milliseconds);
      }
      if (milliseconds < 1000) {
        return `${Math.round(milliseconds)} ms`;
      }
      const seconds = Math.round(milliseconds / 1000);
      return `${seconds} s`;
    },

    agentRunDurationMs(run) {
      if (!run?.started_at) {
        return null;
      }
      const start = new Date(run.started_at).getTime();
      const end = run.ended_at ? new Date(run.ended_at).getTime() : new Date(run.updated_at || run.created_at || run.started_at).getTime();
      if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
        return null;
      }
      return end - start;
    },

    agentRunCountByStatus(status) {
      return (this.agentRuns || []).filter((run) => run.status === status).length;
    },

    agentRunToolFailureCount() {
      return (this.currentAgentRunToolCalls || []).filter((call) => ![
        "planned",
        "running",
        "waiting_authorization",
        "authorized",
        "succeeded"
      ].includes(call.status)).length;
    },

    agentRunModelTokenUsage(call) {
      const usage = call?.usage_json || {};
      if (usage.total_tokens !== null && usage.total_tokens !== undefined) {
        return usage.total_tokens;
      }
      const prompt = Number(usage.prompt_tokens || 0);
      const completion = Number(usage.completion_tokens || 0);
      return prompt + completion || "N/A";
    },

    skillPackageUsedByAgents(skillPackage) {
      const packageName = skillPackage?.name;
      if (!packageName) {
        return [];
      }
      return (this.platformAgentDefinitions || []).filter((agent) => {
        const allowed = agent?.active_version?.spec_json?.allowed_skill_names || [];
        return Array.isArray(allowed) && allowed.includes(packageName);
      });
    },

    skillPackageResourceCountByKind(skillPackage, kind) {
      const resources = skillPackage?.resources || skillPackage?.active_version?.resource_index || [];
      return (resources || []).filter((item) => item.resource_kind === kind || item.kind === kind).length;
    },

    nextSkillPackageVersionLabel() {
      const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
      return `candidate-${stamp}`;
    },

    promptSkillPackageVersionLabel(defaultLabel) {
      if (typeof window === "undefined" || typeof window.prompt !== "function") {
        return defaultLabel;
      }
      return window.prompt("Version label", defaultLabel);
    },

    promptSkillPackageVersionManifest(manifest) {
      const text = JSON.stringify(manifest || {}, null, 2);
      if (typeof window === "undefined" || typeof window.prompt !== "function") {
        return text;
      }
      return window.prompt("Manifest JSON", text);
    },

    skillPackageUploadBodyObjectKey(versionLabel) {
      const packageName = String(this.currentSkillPackage?.name || "skill-package").trim();
      const slug = String(versionLabel || "candidate")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9._-]+/g, "-")
        .replace(/^-+|-+$/g, "") || "candidate";
      return `uploads/${packageName}/${slug}/SKILL.md`;
    },

    skillPackageVersionResourceIndex(parentVersion) {
      const source = Array.isArray(parentVersion?.resource_index) && parentVersion.resource_index.length
        ? parentVersion.resource_index
        : (this.currentSkillPackage?.resources || []);
      const normalized = source.map((item) => ({
        path: item.path || item.resource_path,
        kind: item.kind || item.resource_kind,
        content_hash: item.content_hash || "",
        size_bytes: Number(item.size_bytes || 0)
      })).filter((item) => item.path);
      if (normalized.some((item) => item.path === "SKILL.md")) {
        return normalized;
      }
      return [
        {
          path: "SKILL.md",
          kind: "skill",
          content_hash: "",
          size_bytes: 0
        },
        ...normalized
      ];
    },

    skillPackageAllowedToolsFromManifest(manifest, fallback) {
      const tools = manifest?.["allowed-tools"] || manifest?.allowed_tools;
      if (Array.isArray(tools)) {
        return tools.map((tool) => String(tool).trim()).filter(Boolean);
      }
      return (fallback || []).map((tool) => String(tool).trim()).filter(Boolean);
    },

    skillPackageValidationTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (normalized === "valid") {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (normalized === "warning") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      if (normalized === "invalid") {
        return "border-rose-500/25 bg-rose-500/10 text-rose-200";
      }
      return "border-slate-600 bg-slate-900/70 text-slate-300";
    },

    skillPackageDiagnostics(version) {
      return Array.isArray(version?.validation_diagnostics) ? version.validation_diagnostics : [];
    },

    skillPackageManifestLabel(version, field) {
      const manifest = version?.manifest_json || {};
      return manifest[field] || "N/A";
    },

    memoryStatusTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (normalized === "active") {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (normalized === "pending_review") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      if (normalized === "rejected") {
        return "border-rose-500/25 bg-rose-500/10 text-rose-200";
      }
      return "border-slate-600 bg-slate-900/70 text-slate-300";
    },

    memoryConfidenceTone(value) {
      const confidence = this.normalizedMemoryConfidence(value);
      if (confidence >= 80) {
        return "bg-emerald-400";
      }
      if (confidence >= 50) {
        return "bg-sky-400";
      }
      return "bg-amber-400";
    },

    memoryConfidenceWidth(value) {
      return `${this.normalizedMemoryConfidence(value)}%`;
    },

    memorySourceRefCount(entry) {
      return Array.isArray(entry?.source_refs) ? entry.source_refs.length : 0;
    },

    memorySourceLinks(entry) {
      const links = [];
      const createdBy = String(entry?.created_by_agent_run_id || "").trim();
      if (createdBy) {
        links.push({
          key: `created-by-${createdBy}`,
          label: `AgentRun ${createdBy}`,
          href: this.platformAgentRunPath(createdBy)
        });
      }
      for (const ref of Array.isArray(entry?.source_refs) ? entry.source_refs : []) {
        const link = this.memorySourceRefLink(ref);
        if (link) {
          links.push(link);
        }
      }
      return links;
    },

    memorySourceRefLink(ref) {
      if (!ref || typeof ref !== "object") {
        return null;
      }
      const kind = String(ref.kind || ref.type || "").trim().toLowerCase().replace(/-/g, "_");
      const id = String(
        ref.id ||
        ref.authorization_id ||
        ref.agent_tool_call_id ||
        ref.tool_call_id ||
        ref.run_trace_id ||
        ref.trace_id ||
        ref.run_event_id ||
        ref.event_id ||
        ref.memory_entry_id ||
        ref.memory_id ||
        ref.run_id ||
        ref.agent_run_id ||
        ref.proposal_id ||
        ""
      ).trim();
      if (!id && !ref.run_id && !ref.agent_run_id && !ref.proposal_id && !ref.memory_entry_id && !ref.memory_id) {
        return null;
      }
      if (["agent_memory_entry", "memory_entry", "memory"].includes(kind) || ref.memory_entry_id || ref.memory_id) {
        const memoryId = String(ref.memory_entry_id || ref.memory_id || id).trim();
        return { key: `memory-${memoryId}`, label: `Memory ${memoryId}`, href: this.platformMemoryEntryPath(memoryId) };
      }
      if (["agent_tool_call", "tool_call"].includes(kind)) {
        const toolCallId = String(ref.agent_tool_call_id || ref.tool_call_id || ref.id || "").trim();
        const agentRunId = String(ref.agent_run_id || "").trim();
        const href = agentRunId
          ? buildPlatformAgentRunPath(agentRunId, { tab: "tools", tool_call_id: toolCallId })
          : buildPlatformAgentRunsPath();
        return { key: `tool-call-${toolCallId}`, label: `ToolCall ${toolCallId}`, href };
      }
      if (["agent_tool_authorization", "tool_authorization"].includes(kind)) {
        const authorizationId = String(ref.authorization_id || ref.id || "").trim();
        const agentRunId = String(ref.agent_run_id || "").trim();
        const href = agentRunId
          ? buildPlatformAgentRunPath(agentRunId, { tab: "authorizations", authorization_id: authorizationId })
          : buildToolAuthorizationsPath({ status: ref.status || "", tool_name: ref.tool_name || "" });
        return { key: `tool-auth-${authorizationId}`, label: `ToolAuth ${authorizationId}`, href };
      }
      if (["run_trace", "trace"].includes(kind)) {
        const runId = String(ref.run_id || "").trim();
        const seqNo = String(ref.seq_no || ref.trace_seq_no || "").trim();
        const href = runId
          ? buildReplayPath(runId, { seq_no: seqNo })
          : this.platformMemoryPath();
        return { key: `run-trace-${id || `${runId}-${seqNo}`}`, label: `RunTrace ${id || seqNo || runId}`, href };
      }
      if (["run_event", "event"].includes(kind)) {
        const runId = String(ref.run_id || "").trim();
        const eventId = String(ref.run_event_id || ref.event_id || id).trim();
        const href = runId
          ? buildReplayPath(runId, { event_id: eventId })
          : this.platformMemoryPath();
        return { key: `run-event-${eventId}`, label: `RunEvent ${eventId}`, href };
      }
      if (["agent_run", "agentrun"].includes(kind) || ref.agent_run_id) {
        const agentRunId = String(ref.agent_run_id || id).trim();
        return { key: `agent-run-${agentRunId}`, label: `AgentRun ${agentRunId}`, href: this.platformAgentRunPath(agentRunId) };
      }
      if (["run", "runtime_run"].includes(kind) || ref.run_id) {
        const runId = String(ref.run_id || id).trim();
        return { key: `run-${runId}`, label: `Run ${runId}`, href: this.platformRunLivePath(runId) };
      }
      if (["governance_proposal", "proposal"].includes(kind) || ref.proposal_id) {
        const proposalId = String(ref.proposal_id || id).trim();
        return { key: `proposal-${proposalId}`, label: `Proposal ${proposalId}`, href: buildGovernanceProposalPath(proposalId) };
      }
      return null;
    },

    platformFailureRateLabel(tool) {
      const rate = Number(tool?.failure_rate || 0);
      if (!Number.isFinite(rate)) {
        return "0.00%";
      }
      return `${(rate * 100).toFixed(2)}%`;
    },

    platformJsonPreview(value) {
      return JSON.stringify(value ?? null, null, 2);
    },

    syncAgentRunDetailFromLocation() {
      if (typeof window === "undefined" || !window.location) {
        return;
      }
      const params = new URLSearchParams(window.location.search || "");
      const tab = String(params.get("tab") || "").trim();
      if (["events", "model", "tools", "authorizations", "skills", "memory", "payload"].includes(tab)) {
        this.agentRunDetailTab = tab;
        return;
      }
      if (params.get("tool_call_id")) {
        this.agentRunDetailTab = "tools";
        return;
      }
      if (params.get("authorization_id")) {
        this.agentRunDetailTab = "authorizations";
        return;
      }
      if (params.get("event_id")) {
        this.agentRunDetailTab = "events";
        return;
      }
      if (params.get("model_call_id")) {
        this.agentRunDetailTab = "model";
      }
    },

    syncAgentRunFiltersFromLocation() {
      if (typeof window === "undefined" || !window.location) {
        return;
      }
      const params = new URLSearchParams(window.location.search || "");
      const fields = ["agent_key", "status", "owner_type", "owner_id"];
      if (!fields.some((field) => params.has(field))) {
        return;
      }
      this.agentRunFilters = {
        ...this.agentRunFilters,
        agent_key: params.get("agent_key") || "",
        status: params.get("status") || "",
        owner_type: params.get("owner_type") || "",
        owner_id: params.get("owner_id") || ""
      };
    },

    agentRunFocusedToolCallId() {
      if (typeof window === "undefined" || !window.location) {
        return "";
      }
      return String(new URLSearchParams(window.location.search || "").get("tool_call_id") || "").trim();
    },

    agentRunFocusedAuthorizationId() {
      if (typeof window === "undefined" || !window.location) {
        return "";
      }
      return String(new URLSearchParams(window.location.search || "").get("authorization_id") || "").trim();
    },

    agentRunFocusedEventId() {
      if (typeof window === "undefined" || !window.location) {
        return "";
      }
      return String(new URLSearchParams(window.location.search || "").get("event_id") || "").trim();
    },

    isAgentRunEventFocused(event) {
      const focusedId = this.agentRunFocusedEventId();
      return Boolean(focusedId && event?.id === focusedId);
    },

    agentRunFocusedModelCallId() {
      if (typeof window === "undefined" || !window.location) {
        return "";
      }
      return String(new URLSearchParams(window.location.search || "").get("model_call_id") || "").trim();
    },

    isAgentRunModelCallFocused(call) {
      const focusedId = this.agentRunFocusedModelCallId();
      return Boolean(focusedId && call?.id === focusedId);
    },

    isAgentRunToolCallFocused(call) {
      const focusedId = this.agentRunFocusedToolCallId();
      return Boolean(focusedId && call?.id === focusedId);
    },

    isAgentRunAuthorizationFocused(authorization) {
      const focusedId = this.agentRunFocusedAuthorizationId();
      return Boolean(focusedId && authorization?.id === focusedId);
    }
  };
})();
