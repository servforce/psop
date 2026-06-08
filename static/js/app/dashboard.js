(function () {
  const {
    buildDashboardPath,
    buildEvaluationReportsPath,
    buildEvaluationFindingsPath,
    buildGovernanceProposalsPath,
    buildPlatformAgentRunsPath,
    buildPlatformSkillsPath,
    buildPlatformToolsPath,
    buildPlatformMemoryPath,
    buildToolAuthorizationsPath
  } = window.PSOPConsoleHelpers;

  const DASHBOARD_WINDOW_OPTIONS = [
    { value: 1, label: "1 小时" },
    { value: 6, label: "6 小时" },
    { value: 24, label: "24 小时" },
    { value: 72, label: "3 天" },
    { value: 168, label: "7 天" },
    { value: 720, label: "30 天" }
  ];

  const DASHBOARD_AGENT_LABELS = {
    "pskill.builder": "Builder",
    "pskill.compiler": "Compiler",
    "pskill.tester": "Tester",
    "pskill.runner": "Runner",
    "pskill.evaluator": "Evaluator",
    "psop.governance": "Governance"
  };

  const DASHBOARD_AGENT_KEYS = Object.keys(DASHBOARD_AGENT_LABELS);

  function normalizedWindowHours(value) {
    const hours = Number(value || 24);
    if (!Number.isFinite(hours)) {
      return 24;
    }
    return Math.max(1, Math.min(720, Math.round(hours)));
  }

  window.PSOPConsoleDashboardMethods = {
    async loadDashboardPage() {
      await this.loadDashboardMetrics();
    },

    async loadDashboardMetrics() {
      this.busy.dashboard = true;
      try {
        const query = this.dashboardQueryString();
        const suffix = query ? `?${query}` : "";
        this.dashboardMetrics = await this.apiRequest(`/observability/dashboard${suffix}`);
      } catch (error) {
        this.showNotice("error", error.message || "Dashboard 加载失败。");
      } finally {
        this.busy.dashboard = false;
      }
    },

    dashboardQueryString() {
      const params = new URLSearchParams();
      params.set("window_hours", String(normalizedWindowHours(this.dashboardFilters.window_hours)));
      return params.toString();
    },

    dashboardWindowOptions() {
      return DASHBOARD_WINDOW_OPTIONS;
    },

    dashboardPath() {
      return buildDashboardPath();
    },

    dashboardSkillsPath() {
      return "/admin/skills";
    },

    dashboardEvaluationReportsPath() {
      return buildEvaluationReportsPath();
    },

    dashboardEvaluationFindingsPath() {
      return buildEvaluationFindingsPath();
    },

    dashboardGovernanceProposalsPath() {
      return buildGovernanceProposalsPath();
    },

    dashboardAgentRunsPath(filters = {}) {
      return buildPlatformAgentRunsPath(filters);
    },

    dashboardAgentRunsForAgentPath(agentKey) {
      return buildPlatformAgentRunsPath({ agent_key: agentKey });
    },

    dashboardWaitingAuthorizationsForAgentPath(agentKey) {
      return buildPlatformAgentRunsPath({
        agent_key: agentKey,
        status: "waiting_tool_authorization"
      });
    },

    dashboardSkillPackagesPath() {
      return buildPlatformSkillsPath();
    },

    dashboardToolsPath() {
      return buildPlatformToolsPath();
    },

    dashboardMemoryPath() {
      return buildPlatformMemoryPath();
    },

    dashboardToolAuthorizationsPath(filters = {}) {
      return buildToolAuthorizationsPath(filters);
    },

    dashboardGeneratedAt() {
      const value = this.dashboardMetrics?.generated_at;
      if (!value) {
        return "N/A";
      }
      return typeof this.formatDateTime === "function" ? this.formatDateTime(value) : value;
    },

    dashboardAgentRows() {
      const rows = Array.isArray(this.dashboardMetrics?.agents) ? this.dashboardMetrics.agents : [];
      const byKey = new Map(rows.map((row) => [row.agent_key, row]));
      const fixedRows = DASHBOARD_AGENT_KEYS.map((agentKey) => ({
        agent_key: agentKey,
        recent_run_count: 0,
        succeeded_count: 0,
        failed_count: 0,
        waiting_tool_authorization_count: 0,
        success_rate: 0,
        average_duration_ms: 0,
        model_call_count: 0,
        failed_model_call_count: 0,
        model_failure_rate: 0,
        tool_call_count: 0,
        failed_tool_call_count: 0,
        tool_failure_rate: 0,
        ...(byKey.get(agentKey) || {})
      }));
      const extraRows = rows.filter((row) => row?.agent_key && !DASHBOARD_AGENT_LABELS[row.agent_key]);
      return [...fixedRows, ...extraRows];
    },

    dashboardAgentLabel(agentKey) {
      return DASHBOARD_AGENT_LABELS[agentKey] || agentKey || "Unknown";
    },

    dashboardNumber(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) {
        return "0";
      }
      return new Intl.NumberFormat("zh-CN").format(number);
    },

    dashboardPercent(value) {
      const rate = Number(value || 0);
      if (!Number.isFinite(rate) || rate <= 0) {
        return "0%";
      }
      const percentage = Math.min(100, Math.max(0, rate * 100));
      if (percentage === 100 || percentage >= 10) {
        return `${percentage.toFixed(0)}%`;
      }
      return `${percentage.toFixed(1)}%`;
    },

    dashboardDuration(value) {
      if (typeof this.formatDuration === "function") {
        return this.formatDuration(value);
      }
      const milliseconds = Number(value || 0);
      if (!Number.isFinite(milliseconds) || milliseconds <= 0) {
        return "N/A";
      }
      return `${Math.round(milliseconds)} ms`;
    },

    dashboardStatusCount(metrics, status) {
      return Number(metrics?.status_counts?.[status] || 0);
    },

    dashboardFindingStatusCount(status) {
      return Number(this.dashboardMetrics?.evaluations?.finding_status_counts?.[status] || 0);
    },

    dashboardOutcomeCount(outcome) {
      return Number(this.dashboardMetrics?.evaluations?.outcome_counts?.[outcome] || 0);
    },

    dashboardOtelTone(enabled) {
      return enabled
        ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-200"
        : "border-amber-500/25 bg-amber-500/10 text-amber-200";
    },

    dashboardScoreTone(value) {
      const score = Number(value || 0);
      if (score >= 85) {
        return "text-emerald-200";
      }
      if (score >= 70) {
        return "text-amber-200";
      }
      if (score > 0) {
        return "text-rose-200";
      }
      return "text-slate-100";
    },

    dashboardSuccessTone(value) {
      const rate = Number(value || 0);
      if (rate >= 0.9) {
        return "text-emerald-200";
      }
      if (rate >= 0.7) {
        return "text-amber-200";
      }
      if (rate > 0) {
        return "text-rose-200";
      }
      return "text-slate-100";
    },

    dashboardFailureTone(value) {
      const rate = Number(value || 0);
      if (rate <= 0) {
        return "text-emerald-200";
      }
      if (rate <= 0.05) {
        return "text-amber-200";
      }
      return "text-rose-200";
    }
  };
})();
