(function () {
  const {
    buildPlatformAgentRunsPath,
    buildPlatformAgentRunPath,
    buildPlatformSkillsPath,
    buildPlatformSkillPath,
    buildPlatformToolsPath,
    buildPlatformToolPath,
    buildPlatformMemoryPath,
    buildPlatformMemoryEntryPath,
    buildToolAuthorizationsPath,
    buildRunLivePath
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
      await this.loadAgentRuns();
      if (!this.currentAgentRun && this.agentRuns.length) {
        await this.loadPlatformAgentRunDetail(this.agentRuns[0].id);
      }
    },

    async loadPlatformAgentRunPage(agentRunId) {
      await this.loadAgentRuns();
      await this.loadPlatformAgentRunDetail(agentRunId);
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
        const [run, events, modelCalls, toolCalls, skillActivations, toolAuthorizations] = await Promise.all([
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/events`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/model-calls`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/tool-calls`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/skill-activations`),
          this.apiRequest(`/agent-runs/${encodeURIComponent(id)}/tool-authorizations`)
        ]);
        this.currentAgentRun = run;
        this.currentAgentRunEvents = Array.isArray(events) ? events : [];
        this.currentAgentRunModelCalls = Array.isArray(modelCalls) ? modelCalls : [];
        this.currentAgentRunToolCalls = Array.isArray(toolCalls) ? toolCalls : [];
        this.currentAgentRunSkillActivations = Array.isArray(skillActivations) ? skillActivations : [];
        this.currentAgentRunToolAuthorizations = Array.isArray(toolAuthorizations) ? toolAuthorizations : [];
        this.replaceAgentRun(run);
      } catch (error) {
        this.showNotice("error", error.message || "AgentRun 详情加载失败。");
      } finally {
        this.busy.agentRunDetail = false;
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
      this.currentAgentRun = null;
      this.currentAgentRunEvents = [];
      this.currentAgentRunModelCalls = [];
      this.currentAgentRunToolCalls = [];
      this.currentAgentRunSkillActivations = [];
      this.currentAgentRunToolAuthorizations = [];
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
        this.currentPlatformTool = await this.apiRequest(`/tools/${encodeURIComponent(name)}`);
      } catch (error) {
        this.showNotice("error", error.message || "工具详情加载失败。");
      } finally {
        this.busy.platformTools = false;
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
    },

    testPlatformTool(tool) {
      if (!tool?.name) {
        return;
      }
      if (["read", "compute"].includes(tool.side_effect_level) && !tool.requires_authorization) {
        this.showNotice("success", `${tool.name} 可由 Agent Harness 自动执行，无需工具授权。`);
        return;
      }
      const message = tool.requires_authorization
        ? "该工具需要 Tool Authorization，不能从控制台直接测试。"
        : "当前只支持 read/compute 工具的控制台 dry-run 判断。";
      this.showNotice("error", message);
    },

    async loadPlatformMemoryPage(memoryId) {
      await this.loadMemoryEntries();
      const requestedId = String(memoryId || "").trim();
      if (requestedId) {
        const selected = this.memoryEntries.find((item) => item.id === requestedId);
        if (selected) {
          this.selectMemoryEntry(selected);
        } else {
          this.showNotice("error", "未在当前筛选结果中找到该 Memory。");
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

    platformAgentRunsPath() {
      return buildPlatformAgentRunsPath();
    },

    platformAgentRunPath(agentRunId) {
      return buildPlatformAgentRunPath(agentRunId);
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

    platformMemoryPath() {
      return buildPlatformMemoryPath();
    },

    platformMemoryEntryPath(memoryId) {
      return buildPlatformMemoryEntryPath(memoryId);
    },

    platformToolAuthorizationsPath() {
      return buildToolAuthorizationsPath();
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

    platformFailureRateLabel(tool) {
      const rate = Number(tool?.failure_rate || 0);
      if (!Number.isFinite(rate)) {
        return "0.00%";
      }
      return `${(rate * 100).toFixed(2)}%`;
    },

    platformJsonPreview(value) {
      return JSON.stringify(value ?? null, null, 2);
    }
  };
})();
