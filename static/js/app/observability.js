(function () {
  const {
    buildPlatformObservabilityPath,
    buildPlatformAgentRunsPath,
    buildPlatformAgentRunPath,
    buildToolAuthorizationsPath,
    buildEvaluationReportsPath,
    buildEvaluationFindingsPath,
    buildGovernanceProposalsPath,
    buildGovernanceExperimentsPath,
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

  const TOOL_AUTHORIZATION_STATUS_OPTIONS = [
    "pending",
    "approved",
    "executed",
    "rejected",
    "expired",
    "cancelled"
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
      if (this.observabilityFilters.event_run_id || this.observabilityFilters.run_event_kind) {
        await this.loadObservabilityRunEvents();
      }
      if (
        this.observabilityFilters.agent_event_agent_key ||
        this.observabilityFilters.agent_event_run_id ||
        this.observabilityFilters.agent_event_type
      ) {
        await this.loadObservabilityAgentEvents();
      }
      if (
        this.observabilityFilters.tool_call_agent_key ||
        this.observabilityFilters.tool_call_run_id ||
        this.observabilityFilters.tool_call_status ||
        this.observabilityFilters.tool_call_tool_name
      ) {
        await this.loadObservabilityToolCalls();
      }
      if (
        this.observabilityFilters.model_call_agent_key ||
        this.observabilityFilters.model_call_run_id ||
        this.observabilityFilters.model_call_provider ||
        this.observabilityFilters.model_call_status
      ) {
        await this.loadObservabilityModelCalls();
      }
      if (
        this.observabilityFilters.skill_activation_agent_key ||
        this.observabilityFilters.skill_activation_run_id ||
        this.observabilityFilters.skill_activation_package_id ||
        this.observabilityFilters.skill_activation_version_id
      ) {
        await this.loadObservabilitySkillActivations();
      }
      if (
        this.observabilityFilters.tool_authorization_agent_key ||
        this.observabilityFilters.tool_authorization_run_id ||
        this.observabilityFilters.tool_authorization_status ||
        this.observabilityFilters.tool_authorization_risk_level ||
        this.observabilityFilters.tool_authorization_tool_name
      ) {
        await this.loadObservabilityToolAuthorizations();
      }
      if (this.observabilityFilters.run_id || this.observabilityFilters.trace_event_type) {
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

    async loadObservabilityRunEvents() {
      const runId = String(this.observabilityFilters.event_run_id || "").trim();
      const eventKind = String(this.observabilityFilters.run_event_kind || "").trim();

      this.busy.observabilityEventLookup = true;
      try {
        const params = new URLSearchParams();
        if (eventKind) {
          params.set("event_kind", eventKind);
        }
        if (runId) {
          params.set("run_id", runId);
        }
        params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
        params.set("limit", "50");
        const events = await this.apiRequest(`/observability/run-events?${params.toString()}`);
        this.observabilityRunEvents = Array.isArray(events) ? events : [];
        this.observabilityEventLookupRunId = runId;
      } catch (error) {
        this.showNotice("error", error.message || "RunEvent 查询失败。");
      } finally {
        this.busy.observabilityEventLookup = false;
      }
    },

    resetObservabilityEventQuery() {
      this.observabilityFilters.event_run_id = "";
      this.observabilityFilters.run_event_kind = "";
      this.observabilityRunEvents = [];
      this.observabilityEventLookupRunId = "";
    },

    async selectObservabilityRunEventKind(eventKind) {
      this.observabilityFilters.event_run_id = "";
      this.observabilityFilters.run_event_kind = eventKind || "";
      await this.loadObservabilityRunEvents();
    },

    async loadObservabilityAgentEvents() {
      const agentKey = String(this.observabilityFilters.agent_event_agent_key || "").trim();
      const runId = String(this.observabilityFilters.agent_event_run_id || "").trim();
      const eventType = String(this.observabilityFilters.agent_event_type || "").trim();

      this.busy.observabilityAgentEventLookup = true;
      try {
        const params = new URLSearchParams();
        if (agentKey) {
          params.set("agent_key", agentKey);
        }
        if (runId) {
          params.set("run_id", runId);
        }
        if (eventType) {
          params.set("event_type", eventType);
        }
        params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
        params.set("limit", "50");
        const events = await this.apiRequest(`/observability/agent-events?${params.toString()}`);
        this.observabilityAgentEventResults = Array.isArray(events) ? events : [];
      } catch (error) {
        this.showNotice("error", error.message || "AgentEvent 查询失败。");
      } finally {
        this.busy.observabilityAgentEventLookup = false;
      }
    },

    resetObservabilityAgentEventQuery() {
      this.observabilityFilters.agent_event_agent_key = "";
      this.observabilityFilters.agent_event_run_id = "";
      this.observabilityFilters.agent_event_type = "";
      this.observabilityAgentEventResults = [];
    },

    async selectObservabilityAgentEventType(eventType) {
      this.observabilityFilters.agent_event_agent_key = "";
      this.observabilityFilters.agent_event_run_id = "";
      this.observabilityFilters.agent_event_type = eventType || "";
      await this.loadObservabilityAgentEvents();
    },

    async loadObservabilityToolCalls() {
      const agentKey = String(this.observabilityFilters.tool_call_agent_key || "").trim();
      const runId = String(this.observabilityFilters.tool_call_run_id || "").trim();
      const status = String(this.observabilityFilters.tool_call_status || "").trim();
      const toolName = String(this.observabilityFilters.tool_call_tool_name || "").trim();

      this.busy.observabilityToolCallLookup = true;
      try {
        const params = new URLSearchParams();
        if (agentKey) {
          params.set("agent_key", agentKey);
        }
        if (runId) {
          params.set("run_id", runId);
        }
        if (status) {
          params.set("status", status);
        }
        if (toolName) {
          params.set("tool_name", toolName);
        }
        params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
        params.set("limit", "50");
        const calls = await this.apiRequest(`/observability/tool-calls?${params.toString()}`);
        this.observabilityToolCallResults = Array.isArray(calls) ? calls : [];
      } catch (error) {
        this.showNotice("error", error.message || "ToolCall 查询失败。");
      } finally {
        this.busy.observabilityToolCallLookup = false;
      }
    },

    resetObservabilityToolCallQuery() {
      this.observabilityFilters.tool_call_agent_key = "";
      this.observabilityFilters.tool_call_run_id = "";
      this.observabilityFilters.tool_call_status = "";
      this.observabilityFilters.tool_call_tool_name = "";
      this.observabilityToolCallResults = [];
    },

    async selectObservabilityToolCallStatus(status) {
      this.observabilityFilters.tool_call_agent_key = "";
      this.observabilityFilters.tool_call_run_id = "";
      this.observabilityFilters.tool_call_status = status || "";
      this.observabilityFilters.tool_call_tool_name = "";
      await this.loadObservabilityToolCalls();
    },

    async loadObservabilityModelCalls() {
      const agentKey = String(this.observabilityFilters.model_call_agent_key || "").trim();
      const runId = String(this.observabilityFilters.model_call_run_id || "").trim();
      const provider = String(this.observabilityFilters.model_call_provider || "").trim();
      const status = String(this.observabilityFilters.model_call_status || "").trim();

      this.busy.observabilityModelCallLookup = true;
      try {
        const params = new URLSearchParams();
        if (agentKey) {
          params.set("agent_key", agentKey);
        }
        if (runId) {
          params.set("run_id", runId);
        }
        if (provider) {
          params.set("provider", provider);
        }
        if (status) {
          params.set("status", status);
        }
        params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
        params.set("limit", "50");
        const calls = await this.apiRequest(`/observability/model-calls?${params.toString()}`);
        this.observabilityModelCallResults = Array.isArray(calls) ? calls : [];
      } catch (error) {
        this.showNotice("error", error.message || "ModelCall 查询失败。");
      } finally {
        this.busy.observabilityModelCallLookup = false;
      }
    },

    resetObservabilityModelCallQuery() {
      this.observabilityFilters.model_call_agent_key = "";
      this.observabilityFilters.model_call_run_id = "";
      this.observabilityFilters.model_call_provider = "";
      this.observabilityFilters.model_call_status = "";
      this.observabilityModelCallResults = [];
    },

    async selectObservabilityModelCallProvider(provider) {
      this.observabilityFilters.model_call_agent_key = "";
      this.observabilityFilters.model_call_run_id = "";
      this.observabilityFilters.model_call_provider = provider || "";
      this.observabilityFilters.model_call_status = "";
      await this.loadObservabilityModelCalls();
    },

    async loadObservabilitySkillActivations() {
      const agentKey = String(this.observabilityFilters.skill_activation_agent_key || "").trim();
      const runId = String(this.observabilityFilters.skill_activation_run_id || "").trim();
      const packageId = String(this.observabilityFilters.skill_activation_package_id || "").trim();
      const versionId = String(this.observabilityFilters.skill_activation_version_id || "").trim();

      this.busy.observabilitySkillActivationLookup = true;
      try {
        const params = new URLSearchParams();
        if (agentKey) {
          params.set("agent_key", agentKey);
        }
        if (runId) {
          params.set("run_id", runId);
        }
        if (packageId) {
          params.set("package_id", packageId);
        }
        if (versionId) {
          params.set("version_id", versionId);
        }
        params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
        params.set("limit", "50");
        const activations = await this.apiRequest(`/observability/skill-activations?${params.toString()}`);
        this.observabilitySkillActivationResults = Array.isArray(activations) ? activations : [];
      } catch (error) {
        this.showNotice("error", error.message || "SkillActivation 查询失败。");
      } finally {
        this.busy.observabilitySkillActivationLookup = false;
      }
    },

    resetObservabilitySkillActivationQuery() {
      this.observabilityFilters.skill_activation_agent_key = "";
      this.observabilityFilters.skill_activation_run_id = "";
      this.observabilityFilters.skill_activation_package_id = "";
      this.observabilityFilters.skill_activation_version_id = "";
      this.observabilitySkillActivationResults = [];
    },

    async selectObservabilitySkillActivationPackage(packageId) {
      this.observabilityFilters.skill_activation_agent_key = "";
      this.observabilityFilters.skill_activation_run_id = "";
      this.observabilityFilters.skill_activation_package_id = packageId || "";
      this.observabilityFilters.skill_activation_version_id = "";
      await this.loadObservabilitySkillActivations();
    },

    async loadObservabilityToolAuthorizations() {
      const agentKey = String(this.observabilityFilters.tool_authorization_agent_key || "").trim();
      const runId = String(this.observabilityFilters.tool_authorization_run_id || "").trim();
      const status = String(this.observabilityFilters.tool_authorization_status || "").trim();
      const riskLevel = String(this.observabilityFilters.tool_authorization_risk_level || "").trim();
      const toolName = String(this.observabilityFilters.tool_authorization_tool_name || "").trim();

      this.busy.observabilityToolAuthorizationLookup = true;
      try {
        const params = new URLSearchParams();
        if (agentKey) {
          params.set("agent_key", agentKey);
        }
        if (runId) {
          params.set("run_id", runId);
        }
        if (status) {
          params.set("status", status);
        }
        if (riskLevel) {
          params.set("risk_level", riskLevel);
        }
        if (toolName) {
          params.set("tool_name", toolName);
        }
        params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
        params.set("limit", "50");
        const authorizations = await this.apiRequest(`/observability/tool-authorizations?${params.toString()}`);
        this.observabilityToolAuthorizationResults = Array.isArray(authorizations) ? authorizations : [];
      } catch (error) {
        this.showNotice("error", error.message || "ToolAuthorization 查询失败。");
      } finally {
        this.busy.observabilityToolAuthorizationLookup = false;
      }
    },

    resetObservabilityToolAuthorizationQuery() {
      this.observabilityFilters.tool_authorization_agent_key = "";
      this.observabilityFilters.tool_authorization_run_id = "";
      this.observabilityFilters.tool_authorization_status = "";
      this.observabilityFilters.tool_authorization_risk_level = "";
      this.observabilityFilters.tool_authorization_tool_name = "";
      this.observabilityToolAuthorizationResults = [];
    },

    async selectObservabilityToolAuthorizationStatus(status) {
      this.observabilityFilters.tool_authorization_agent_key = "";
      this.observabilityFilters.tool_authorization_run_id = "";
      this.observabilityFilters.tool_authorization_status = status || "";
      this.observabilityFilters.tool_authorization_risk_level = "";
      this.observabilityFilters.tool_authorization_tool_name = "";
      await this.loadObservabilityToolAuthorizations();
    },

    async loadObservabilityRunTraces() {
      const runId = String(this.observabilityFilters.run_id || "").trim();
      const eventType = String(this.observabilityFilters.trace_event_type || "").trim();

      this.busy.observabilityTraceLookup = true;
      try {
        const params = new URLSearchParams();
        if (eventType) {
          params.set("event_type", eventType);
        }
        let path = "";
        if (runId) {
          const suffix = params.toString() ? `?${params.toString()}` : "";
          path = `/runs/${encodeURIComponent(runId)}/traces${suffix}`;
        } else {
          params.set("window_hours", String(normalizedWindowHours(this.observabilityFilters.window_hours)));
          params.set("limit", "50");
          path = `/observability/run-traces?${params.toString()}`;
        }
        const traces = await this.apiRequest(path);
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

    async selectObservabilityTraceEventType(eventType) {
      this.observabilityFilters.run_id = "";
      this.observabilityFilters.trace_event_type = eventType || "";
      await this.loadObservabilityRunTraces();
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

    observabilityEvaluationReportsPath() {
      return buildEvaluationReportsPath();
    },

    observabilityEvaluationFindingsPath() {
      return buildEvaluationFindingsPath();
    },

    observabilityGovernanceProposalsPath() {
      return buildGovernanceProposalsPath();
    },

    observabilityGovernanceExperimentsPath() {
      return buildGovernanceExperimentsPath();
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

    observabilityToolAuthorizationPath(authorization) {
      const agentRunId = String(authorization?.agent_run_id || "").trim();
      if (!agentRunId) {
        return this.observabilityToolAuthorizationHistoryPath(authorization);
      }
      return buildPlatformAgentRunPath(agentRunId, {
        tab: "authorizations",
        authorization_id: authorization?.id || ""
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
      const traceId = String(trace?.id || trace?.trace_id || "").trim();
      return traceId ? buildReplayPath(runId, { trace_id: traceId }) : buildReplayPath(runId, { seq_no: trace?.seq_no });
    },

    observabilityRunEventReplayPath(event) {
      const runId = String(event?.run_id || this.observabilityEventLookupRunId || "").trim();
      if (!runId) {
        return buildPlatformObservabilityPath();
      }
      return buildReplayPath(runId, { event_id: event?.id });
    },

    observabilityAgentRunPath(agentRunId) {
      return `${buildPlatformAgentRunsPath()}/${encodeURIComponent(agentRunId)}`;
    },

    observabilityAgentEventPath(event) {
      const agentRunId = String(event?.agent_run_id || "").trim();
      if (!agentRunId) {
        return buildPlatformAgentRunsPath();
      }
      return buildPlatformAgentRunPath(agentRunId, { tab: "events", event_id: event?.id || "" });
    },

    observabilityAgentRunToolCallPath(call) {
      const agentRunId = String(call?.agent_run_id || this.observabilityAgentRunDetail?.id || "").trim();
      if (!agentRunId) {
        return buildPlatformAgentRunsPath();
      }
      return buildPlatformAgentRunPath(agentRunId, { tab: "tools", tool_call_id: call?.id || "" });
    },

    observabilityToolCallPath(call) {
      const agentRunId = String(call?.agent_run_id || "").trim();
      if (!agentRunId) {
        return buildPlatformAgentRunsPath();
      }
      return buildPlatformAgentRunPath(agentRunId, { tab: "tools", tool_call_id: call?.id || "" });
    },

    observabilityModelCallPath(call) {
      const agentRunId = String(call?.agent_run_id || "").trim();
      if (!agentRunId) {
        return buildPlatformAgentRunsPath();
      }
      return buildPlatformAgentRunPath(agentRunId, { tab: "model" });
    },

    observabilitySkillActivationPath(activation) {
      const agentRunId = String(activation?.agent_run_id || "").trim();
      if (!agentRunId) {
        return buildPlatformAgentRunsPath();
      }
      return buildPlatformAgentRunPath(agentRunId, { tab: "skills" });
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

    observabilityRunEventKindOptions() {
      return Object.keys(this.observabilityMetrics?.runtime?.run_event_kind_counts || {}).sort();
    },

    observabilityAgentEventTypeOptions() {
      return Object.keys(this.observabilityMetrics?.agents?.agent_event_type_counts || {}).sort();
    },

    observabilityAgentKeyOptions() {
      return Object.keys(this.observabilityMetrics?.agents?.agent_run_key_counts || {}).sort();
    },

    observabilityToolCallStatusOptions() {
      return Object.keys(this.observabilityMetrics?.agents?.tool_call_status_counts || {}).sort();
    },

    observabilityModelCallProviderOptions() {
      return Object.keys(this.observabilityMetrics?.agents?.model_call_provider_counts || {}).sort();
    },

    observabilityModelCallStatusOptions() {
      return Object.keys(this.observabilityMetrics?.agents?.model_call_status_counts || {}).sort();
    },

    observabilitySkillActivationPackageOptions() {
      return Object.keys(this.observabilityMetrics?.agents?.skill_activation_package_counts || {}).sort();
    },

    observabilityToolAuthorizationStatusOptions() {
      return Array.from(new Set([
        ...TOOL_AUTHORIZATION_STATUS_OPTIONS,
        ...Object.keys(this.observabilityMetrics?.agents?.tool_authorization_status_counts || {})
      ]));
    },

    observabilityToolAuthorizationRiskOptions() {
      return Object.keys(this.observabilityMetrics?.agents?.tool_authorization_risk_counts || {}).sort();
    },

    observabilityEvaluationOutcomeOptions() {
      return Object.keys(this.observabilityMetrics?.evaluations?.outcome_counts || {}).sort();
    },

    observabilityFindingStatusOptions() {
      return Object.keys(this.observabilityMetrics?.evaluations?.finding_status_counts || {}).sort();
    },

    observabilityFindingCategoryOptions() {
      return Object.keys(this.observabilityMetrics?.evaluations?.finding_category_counts || {}).sort();
    },

    observabilityGovernanceStatusOptions() {
      return Object.keys(this.observabilityMetrics?.governance?.status_counts || {}).sort();
    },

    observabilityGovernanceTypeOptions() {
      return Object.keys(this.observabilityMetrics?.governance?.proposal_type_counts || {}).sort();
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
