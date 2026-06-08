(function () {
  const {
    buildPlatformAgentsPath,
    buildPlatformAgentPath,
    buildPlatformAgentRunsPath,
    buildPlatformAgentRunPath,
    buildPlatformSkillsPath,
    buildPlatformSkillPath,
    buildToolAuthorizationsPath
  } = window.PSOPConsoleHelpers;

  const AGENT_DETAIL_TABS = [
    { value: "spec", label: "Spec" },
    { value: "versions", label: "Versions" },
    { value: "bindings", label: "Bindings" },
    { value: "runs", label: "Runs" },
    { value: "authorizations", label: "Authorizations" }
  ];

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function agentSpec(agent) {
    return agent?.active_version?.spec_json || {};
  }

  function shortHash(value) {
    const text = String(value || "");
    return text ? text.slice(0, 12) : "N/A";
  }

  window.PSOPConsolePlatformAgentMethods = {
    async loadPlatformAgentsPage() {
      await this.loadPlatformAgents();
      if (!this.currentPlatformAgent && this.platformAgents.length) {
        await this.loadPlatformAgentDetail(this.platformAgents[0].key);
      }
    },

    async loadPlatformAgentPage(agentKey) {
      await this.loadPlatformAgents();
      await this.loadPlatformAgentDetail(agentKey);
    },

    async loadPlatformAgents() {
      this.busy.platformAgents = true;
      try {
        const agents = await this.apiRequest("/agents");
        this.platformAgents = asArray(agents);
        if (this.currentPlatformAgent?.key) {
          const refreshed = this.platformAgents.find((agent) => agent.key === this.currentPlatformAgent.key);
          this.currentPlatformAgent = refreshed ? { ...this.currentPlatformAgent, ...refreshed } : this.currentPlatformAgent;
        }
      } catch (error) {
        this.showNotice("error", error.message || "Agents 加载失败。");
      } finally {
        this.busy.platformAgents = false;
      }
    },

    async loadPlatformAgentDetail(agentKey) {
      const key = String(agentKey || "").trim();
      if (!key) {
        return;
      }
      this.busy.platformAgentDetail = true;
      try {
        const [agent, runs] = await Promise.all([
          this.apiRequest(`/agents/${encodeURIComponent(key)}`),
          this.apiRequest(`/agent-runs?agent_key=${encodeURIComponent(key)}`)
        ]);
        this.currentPlatformAgent = agent;
        this.replacePlatformAgent(agent);
        this.platformAgentRuns = asArray(runs);
        this.ensurePlatformAgentVersionSelection();
        const runIds = this.platformAgentRuns
          .slice(0, 10)
          .map((run) => run.id)
          .filter(Boolean);
        const authorizationGroups = await Promise.all(
          runIds.map((runId) =>
            this.apiRequest(`/agent-runs/${encodeURIComponent(runId)}/tool-authorizations`).catch(() => [])
          )
        );
        this.platformAgentToolAuthorizations = authorizationGroups.flatMap((items) => asArray(items));
      } catch (error) {
        this.showNotice("error", error.message || "Agent 详情加载失败。");
      } finally {
        this.busy.platformAgentDetail = false;
      }
    },

    replacePlatformAgent(agent) {
      if (!agent?.key) {
        return;
      }
      const index = this.platformAgents.findIndex((item) => item.key === agent.key);
      if (index >= 0) {
        this.platformAgents.splice(index, 1, { ...this.platformAgents[index], ...agent });
      } else {
        this.platformAgents.unshift(agent);
      }
    },

    async requestPlatformAgentVersionAction(action, version = null) {
      const agentKey = String(this.currentPlatformAgent?.key || "").trim();
      if (!agentKey) {
        this.showNotice("error", "请先选择 Agent。");
        return;
      }
      this.busy.platformAgentAction = true;
      try {
        if (action === "create_draft") {
          const nextVersionNo = Number(this.currentPlatformAgent?.version_count || 0) + 1;
          const detail = await this.apiRequest(`/agents/${encodeURIComponent(agentKey)}/versions`, {
            method: "POST",
            body: JSON.stringify({
              version_label: `draft-v${nextVersionNo}`,
              spec_json: agentSpec(this.currentPlatformAgent)
            })
          });
          this.currentPlatformAgent = detail;
          this.replacePlatformAgent(detail);
          this.selectedPlatformAgentVersionId = detail.active_version_id || "";
          this.platformAgentDetailTab = "versions";
          this.showNotice("success", "AgentVersion draft 已创建。");
          return;
        }
        if (!version?.id) {
          this.showNotice("error", "请选择 AgentVersion。");
          return;
        }
        if (action === "publish") {
          await this.apiRequest(`/agents/${encodeURIComponent(agentKey)}/versions/${encodeURIComponent(version.id)}/publish`, {
            method: "POST"
          });
          await this.loadPlatformAgentDetail(agentKey);
          this.platformAgentDetailTab = "versions";
          this.showNotice("success", "AgentVersion 已发布。");
          return;
        }
        if (action === "activate" || action === "rollback") {
          const detail = await this.apiRequest(
            `/agents/${encodeURIComponent(agentKey)}/versions/${encodeURIComponent(version.id)}/activate`,
            {
              method: "POST",
              body: JSON.stringify({ update_bindings: true })
            }
          );
          this.currentPlatformAgent = detail;
          this.replacePlatformAgent(detail);
          this.selectedPlatformAgentVersionId = version.id;
          await this.loadPlatformAgents();
          this.platformAgentDetailTab = "versions";
          this.showNotice("success", action === "rollback" ? "AgentVersion 已回滚。" : "AgentVersion 已激活。");
          return;
        }
        this.showNotice("error", `未知 AgentVersion 操作：${action}`);
      } catch (error) {
        this.showNotice("error", error.message || "AgentVersion 操作失败。");
      } finally {
        this.busy.platformAgentAction = false;
      }
    },

    platformAgentsPath() {
      return buildPlatformAgentsPath();
    },

    platformAgentPath(agentKey) {
      return buildPlatformAgentPath(agentKey);
    },

    platformAgentsRunListPath() {
      return buildPlatformAgentRunsPath();
    },

    platformAgentsRunPath(agentRunId, focus = {}) {
      return buildPlatformAgentRunPath(agentRunId, focus);
    },

    platformAgentWaitingAuthorizationPath(run) {
      const agentRunId = String(run?.id || run || "").trim();
      return agentRunId
        ? buildPlatformAgentRunPath(agentRunId, { tab: "authorizations" })
        : buildToolAuthorizationsPath({ status: "pending" });
    },

    platformAgentAuthorizationPath(authorization) {
      const agentRunId = String(authorization?.agent_run_id || "").trim();
      const authorizationId = String(authorization?.id || "").trim();
      if (agentRunId) {
        return buildPlatformAgentRunPath(agentRunId, {
          tab: "authorizations",
          authorization_id: authorizationId
        });
      }
      return this.platformAgentsToolAuthorizationsPath(authorization);
    },

    platformAgentsSkillsPath() {
      return buildPlatformSkillsPath();
    },

    platformAgentsSkillPath(packageName) {
      return buildPlatformSkillPath(packageName);
    },

    platformAgentsToolAuthorizationsPath(filters = {}) {
      const toolName = typeof filters === "string" ? filters : filters?.tool_name;
      const status = typeof filters === "object" ? filters?.status : "";
      return buildToolAuthorizationsPath({
        status,
        tool_name: toolName
      });
    },

    platformAgentDetailTabs() {
      return AGENT_DETAIL_TABS;
    },

    platformAgentStatusTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (normalized === "active") {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (normalized === "draft") {
        return "border-sky-500/25 bg-sky-500/10 text-sky-200";
      }
      if (normalized === "disabled" || normalized === "archived") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      return "border-slate-600 bg-slate-900/70 text-slate-300";
    },

    platformAgentVersionStatusTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (["published", "active"].includes(normalized)) {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (normalized === "draft") {
        return "border-sky-500/25 bg-sky-500/10 text-sky-200";
      }
      if (["failed", "invalid"].includes(normalized)) {
        return "border-rose-500/25 bg-rose-500/10 text-rose-200";
      }
      if (["archived", "deprecated"].includes(normalized)) {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      return "border-slate-600 bg-slate-900/70 text-slate-300";
    },

    platformAgentVersionActionLabel(action) {
      const labels = {
        create_draft: "创建 Draft",
        publish: "发布",
        activate: "激活",
        rollback: "回滚"
      };
      return labels[action] || action;
    },

    platformAgentCountByStatus(status) {
      return (this.platformAgents || []).filter((agent) => agent.status === status).length;
    },

    platformAgentVersionTotal() {
      return (this.platformAgents || []).reduce((total, agent) => total + Number(agent.version_count || 0), 0);
    },

    platformAgentBindingTotal() {
      return (this.platformAgents || []).reduce((total, agent) => total + asArray(agent.bindings).length, 0);
    },

    platformAgentRunCountByStatus(status) {
      return (this.platformAgentRuns || []).filter((run) => run.status === status).length;
    },

    platformAgentAuthorizationCountByStatus(status) {
      return (this.platformAgentToolAuthorizations || []).filter((authorization) => authorization.status === status).length;
    },

    platformAgentAllowedTools(agent = this.currentPlatformAgent) {
      return asArray(agentSpec(agent).allowed_tools);
    },

    platformAgentAllowedSkills(agent = this.currentPlatformAgent) {
      return asArray(agentSpec(agent).allowed_skill_names);
    },

    platformAgentOutputSchemaName(agent = this.currentPlatformAgent) {
      const outputSchema = agentSpec(agent).output_schema || {};
      return outputSchema.name || "N/A";
    },

    platformAgentGoal(agent = this.currentPlatformAgent) {
      return agentSpec(agent).goal || agent?.description || "N/A";
    },

    platformAgentSpecPreview(agent = this.currentPlatformAgent) {
      return JSON.stringify(agentSpec(agent), null, 2);
    },

    platformAgentVersionLabel(version) {
      if (!version) {
        return "N/A";
      }
      return version.version_label || `v${version.version_no}`;
    },

    platformAgentActiveVersionLabel(agent = this.currentPlatformAgent) {
      return agent?.active_version_label || this.platformAgentVersionLabel(agent?.active_version);
    },

    platformAgentVersionById(versionId, agent = this.currentPlatformAgent) {
      return asArray(agent?.versions).find((version) => version.id === versionId) || null;
    },

    ensurePlatformAgentVersionSelection(agent = this.currentPlatformAgent) {
      const versions = asArray(agent?.versions);
      const selected = this.platformAgentVersionById(this.selectedPlatformAgentVersionId, agent);
      if (selected) {
        return selected;
      }
      const fallback = this.platformAgentVersionById(agent?.active_version_id, agent) || versions[0] || null;
      this.selectedPlatformAgentVersionId = fallback?.id || "";
      return fallback;
    },

    selectPlatformAgentVersion(version) {
      if (!version?.id) {
        return;
      }
      this.selectedPlatformAgentVersionId = version.id;
      this.platformAgentDetailTab = "versions";
    },

    selectedPlatformAgentVersion(agent = this.currentPlatformAgent) {
      return this.platformAgentVersionById(this.selectedPlatformAgentVersionId, agent)
        || this.ensurePlatformAgentVersionSelection(agent);
    },

    platformAgentBindingVersionLabel(binding, agent = this.currentPlatformAgent) {
      const version = this.platformAgentVersionById(binding?.active_version_id, agent);
      return version ? this.platformAgentVersionLabel(version) : shortHash(binding?.active_version_id);
    },

    platformAgentBindingRows(agent = this.currentPlatformAgent) {
      return asArray(agent?.bindings).map((binding) => ({
        ...binding,
        active_version_label: this.platformAgentBindingVersionLabel(binding, agent)
      }));
    },

    platformAgentVersionChanged(version) {
      const activeSpec = this.currentPlatformAgent?.active_version?.spec_json || {};
      const candidateSpec = version?.spec_json || {};
      return JSON.stringify(activeSpec) !== JSON.stringify(candidateSpec);
    },

    platformAgentSpecDiffPreview(version) {
      const activeVersion = this.currentPlatformAgent?.active_version || null;
      return JSON.stringify(
        {
          active_version_id: activeVersion?.id || null,
          candidate_version_id: version?.id || null,
          changed: this.platformAgentVersionChanged(version),
          active_spec: activeVersion?.spec_json || {},
          candidate_spec: version?.spec_json || {}
        },
        null,
        2
      );
    },

    platformAgentSpecDiffRows(version = this.selectedPlatformAgentVersion()) {
      const activeSpec = this.currentPlatformAgent?.active_version?.spec_json || {};
      const candidateSpec = version?.spec_json || {};
      return this.platformAgentSpecDiffRowsForValues(activeSpec, candidateSpec);
    },

    platformAgentSpecDiffRowsForValues(activeValue, candidateValue, prefix = "") {
      if (this.platformAgentDiffIsPlainObject(activeValue) && this.platformAgentDiffIsPlainObject(candidateValue)) {
        const keys = Array.from(new Set([...Object.keys(activeValue), ...Object.keys(candidateValue)])).sort();
        return keys.flatMap((key) => {
          const path = prefix ? `${prefix}.${key}` : key;
          const activeChild = activeValue[key];
          const candidateChild = candidateValue[key];
          if (this.platformAgentDiffIsPlainObject(activeChild) && this.platformAgentDiffIsPlainObject(candidateChild)) {
            return this.platformAgentSpecDiffRowsForValues(activeChild, candidateChild, path);
          }
          return [this.platformAgentSpecDiffRow(path, activeChild, candidateChild)];
        });
      }
      return [this.platformAgentSpecDiffRow(prefix || "$", activeValue, candidateValue)];
    },

    platformAgentSpecDiffRow(path, activeValue, candidateValue) {
      const activeText = this.platformAgentDiffValueText(activeValue);
      const candidateText = this.platformAgentDiffValueText(candidateValue);
      const missingActive = activeValue === undefined;
      const missingCandidate = candidateValue === undefined;
      return {
        path,
        active: missingActive ? "N/A" : activeText,
        candidate: missingCandidate ? "N/A" : candidateText,
        change_type: missingActive ? "added" : missingCandidate ? "removed" : activeText === candidateText ? "same" : "changed"
      };
    },

    platformAgentDiffIsPlainObject(value) {
      return value !== null && typeof value === "object" && !Array.isArray(value);
    },

    platformAgentDiffValueText(value) {
      if (value === undefined) {
        return "";
      }
      if (value === null || typeof value !== "object") {
        return String(value);
      }
      return JSON.stringify(value);
    },

    platformAgentSpecDiffCount(version = this.selectedPlatformAgentVersion(), changeType = "") {
      const rows = this.platformAgentSpecDiffRows(version);
      if (!changeType) {
        return rows.filter((row) => row.change_type !== "same").length;
      }
      return rows.filter((row) => row.change_type === changeType).length;
    },

    platformAgentSpecDiffTone(row) {
      if (row?.change_type === "added") {
        return "bg-emerald-500/5 text-emerald-100";
      }
      if (row?.change_type === "removed") {
        return "bg-rose-500/5 text-rose-100";
      }
      if (row?.change_type === "changed") {
        return "bg-orange-500/5 text-orange-100";
      }
      return "bg-slate-950/20 text-slate-400";
    },

    platformAgentSpecDiffBadgeTone(changeType) {
      if (changeType === "added") {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (changeType === "removed") {
        return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      }
      if (changeType === "changed") {
        return "border-orange-500/30 bg-orange-500/10 text-orange-200";
      }
      return "border-slate-700 bg-slate-950/40 text-slate-400";
    },

    platformAgentShortHash(value) {
      return shortHash(value);
    }
  };
})();
