(function () {
  const {
    buildPlatformObservabilityPath,
    buildPlatformAgentRunsPath,
    buildPlatformAgentRunPath,
    buildToolAuthorizationsPath,
    buildRunLivePath,
    buildReplayPath
  } = window.PSOPConsoleHelpers;

  const OBSERVABILITY_WINDOW_OPTIONS = [
    { value: 1, label: "1 小时" },
    { value: 6, label: "6 小时" },
    { value: 24, label: "24 小时" },
    { value: 72, label: "3 天" },
    { value: 168, label: "7 天" },
    { value: 720, label: "30 天" }
  ];

  function normalizedWindowHours(value) {
    const hours = Number(value || 24);
    if (!Number.isFinite(hours)) {
      return 24;
    }
    return Math.max(1, Math.min(720, Math.round(hours)));
  }

  window.PSOPConsoleObservabilityMethods = {
    async loadPlatformObservabilityPage() {
      await this.loadObservabilityMetrics();
      if (this.observabilityFilters.run_id) {
        await this.loadObservabilityRunTraces();
      }
      if (this.observabilityFilters.agent_run_id) {
        await this.loadObservabilityAgentRun();
      }
    },

    async loadObservabilityMetrics() {
      this.busy.observabilityMetrics = true;
      try {
        const query = this.observabilityMetricsQueryString();
        const suffix = query ? `?${query}` : "";
        this.observabilityMetrics = await this.apiRequest(`/observability/metrics${suffix}`);
      } catch (error) {
        this.showNotice("error", error.message || "Observability 指标加载失败。");
      } finally {
        this.busy.observabilityMetrics = false;
      }
    },

    observabilityMetricsQueryString() {
      const params = new URLSearchParams();
      params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
      return params.toString();
    },

    observabilityWindowOptions() {
      return OBSERVABILITY_WINDOW_OPTIONS;
    },

    async loadObservabilityRunTraces() {
      const runId = String(this.observabilityFilters.run_id || "").trim();
      if (!runId) {
        this.observabilityRunTraces = [];
        this.observabilityTraceLookupRunId = "";
        return;
      }

      this.busy.observabilityTraceLookup = true;
      try {
        const params = new URLSearchParams();
        const eventType = String(this.observabilityFilters.trace_event_type || "").trim();
        if (eventType) {
          params.set("event_type", eventType);
        }
        const suffix = params.toString() ? `?${params.toString()}` : "";
        const traces = await this.apiRequest(`/runs/${encodeURIComponent(runId)}/traces${suffix}`);
        this.observabilityRunTraces = Array.isArray(traces) ? traces : [];
        this.observabilityTraceLookupRunId = runId;
      } catch (error) {
        this.showNotice("error", error.message || "RunTrace 查询失败。");
      } finally {
        this.busy.observabilityTraceLookup = false;
      }
    },

    resetObservabilityTraceQuery() {
      this.observabilityFilters.run_id = "";
      this.observabilityFilters.trace_event_type = "";
      this.observabilityRunTraces = [];
      this.observabilityTraceLookupRunId = "";
    },

    async loadObservabilityAgentRun() {
      const agentRunId = String(this.observabilityFilters.agent_run_id || "").trim();
      if (!agentRunId) {
        this.resetObservabilityAgentRunQuery();
        return;
      }

      this.busy.observabilityAgentRunLookup = true;
      try {
        const encoded = encodeURIComponent(agentRunId);
        const [run, events, modelCalls, toolCalls, skillActivations, toolAuthorizations, memoryEntries] = await Promise.all([
          this.apiRequest(`/agent-runs/${encoded}`),
          this.apiRequest(`/agent-runs/${encoded}/events`),
          this.apiRequest(`/agent-runs/${encoded}/model-calls`),
          this.apiRequest(`/agent-runs/${encoded}/tool-calls`),
          this.apiRequest(`/agent-runs/${encoded}/skill-activations`),
          this.apiRequest(`/agent-runs/${encoded}/tool-authorizations`),
          this.apiRequest(`/agent-runs/${encoded}/memory-entries`)
        ]);
        this.observabilityAgentRunDetail = run;
        this.observabilityAgentEvents = Array.isArray(events) ? events : [];
        this.observabilityModelCalls = Array.isArray(modelCalls) ? modelCalls : [];
        this.observabilityToolCalls = Array.isArray(toolCalls) ? toolCalls : [];
        this.observabilitySkillActivations = Array.isArray(skillActivations) ? skillActivations : [];
        this.observabilityToolAuthorizations = Array.isArray(toolAuthorizations) ? toolAuthorizations : [];
        this.observabilityMemoryEntries = Array.isArray(memoryEntries) ? memoryEntries : [];
      } catch (error) {
        this.showNotice("error", error.message || "AgentRun 可观测数据查询失败。");
      } finally {
        this.busy.observabilityAgentRunLookup = false;
      }
    },

    resetObservabilityAgentRunQuery() {
      this.observabilityFilters.agent_run_id = "";
      this.observabilityAgentRunDetail = null;
      this.observabilityAgentEvents = [];
      this.observabilityModelCalls = [];
      this.observabilityToolCalls = [];
      this.observabilitySkillActivations = [];
      this.observabilityToolAuthorizations = [];
      this.observabilityMemoryEntries = [];
    },

    platformObservabilityPath() {
      return buildPlatformObservabilityPath();
    },

    observabilityAgentRunsPath() {
      return buildPlatformAgentRunsPath();
    },

    observabilityAgentRunsAgentPath(agentKey) {
      return buildPlatformAgentRunsPath({ agent_key: agentKey });
    },

    observabilityAgentRunsStatusPath(status) {
      return buildPlatformAgentRunsPath({ status });
    },

    observabilityToolAuthorizationsPath() {
      return buildToolAuthorizationsPath();
    },

    observabilityToolAuthorizationsStatusPath(status) {
      return buildToolAuthorizationsPath({ status });
    },

    observabilityToolAuthorizationHistoryPath(authorization) {
      return buildToolAuthorizationsPath({
        status: authorization?.status || "",
        tool_name: authorization?.tool_name || ""
      });
    },

    observabilityRunLivePath(runId) {
      return buildRunLivePath(runId);
    },

    observabilityRunReplayPath(trace) {
      const runId = String(trace?.run_id || this.observabilityTraceLookupRunId || "").trim();
      if (!runId) {
        return buildPlatformObservabilityPath();
      }
      return buildReplayPath(runId, { seq_no: trace?.seq_no });
    },

    observabilityAgentRunPath(agentRunId) {
      return `${buildPlatformAgentRunsPath()}/${encodeURIComponent(agentRunId)}`;
    },

    observabilityAgentRunToolCallPath(call) {
      const agentRunId = String(call?.agent_run_id || this.observabilityAgentRunDetail?.id || "").trim();
      if (!agentRunId) {
        return buildPlatformAgentRunsPath();
      }
      return buildPlatformAgentRunPath(agentRunId, { tab: "tools", tool_call_id: call?.id || "" });
    },

    observabilityAgentRunAuthorizationPath(authorization) {
      const agentRunId = String(authorization?.agent_run_id || this.observabilityAgentRunDetail?.id || "").trim();
      if (!agentRunId) {
        return this.observabilityToolAuthorizationHistoryPath(authorization);
      }
      return buildPlatformAgentRunPath(agentRunId, {
        tab: "authorizations",
        authorization_id: authorization?.id || ""
      });
    },

    observabilityGeneratedAt() {
      const value = this.observabilityMetrics?.generated_at;
      if (!value) {
        return "N/A";
      }
      return typeof this.formatDateTime === "function" ? this.formatDateTime(value) : value;
    },

    observabilitySince() {
      const value = this.observabilityMetrics?.since;
      if (!value) {
        return "N/A";
      }
      return typeof this.formatDateTime === "function" ? this.formatDateTime(value) : value;
    },

    observabilityNumber(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) {
        return "0";
      }
      return new Intl.NumberFormat("zh-CN").format(number);
    },

    observabilityTopEntries(counts, limit = 6) {
      return Object.entries(counts || {})
        .map(([key, value]) => ({ key, value: Number(value || 0) }))
        .filter((item) => item.key)
        .sort((a, b) => b.value - a.value || a.key.localeCompare(b.key))
        .slice(0, limit);
    },

    observabilityTraceEventTypeOptions() {
      return Object.keys(this.observabilityMetrics?.runtime?.run_trace_event_type_counts || {}).sort();
    },

    observabilityOtelTone(value) {
      return value
        ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-200"
        : "border-amber-500/25 bg-amber-500/10 text-amber-200";
    },

    observabilityTracePayloadPreview(trace) {
      if (!trace?.payload) {
        return "{}";
      }
      if (typeof this.platformJsonPreview === "function") {
        return this.platformJsonPreview(trace.payload);
      }
      return JSON.stringify(trace.payload, null, 2);
    },

    observabilityPayloadPreview(value) {
      if (typeof this.platformJsonPreview === "function") {
        return this.platformJsonPreview(value || {});
      }
      return JSON.stringify(value || {}, null, 2);
    }
  };
})();
