(function () {
  const KNOWN_JOB_TYPES = [
    { value: "material_analysis", label: "PSkill 素材解析" },
    { value: "pskill_build", label: "PSkill 智能体构建" },
    { value: "pskill_compile", label: "PSkill 编译" },
    { value: "pskill_test", label: "PSkill 测试" },
    { value: "runtime_step", label: "Runtime 推进" }
  ];

  const JOB_TYPE_ALIASES = {
    compile: "pskill_compile",
    runtime: "runtime_step",
    skill_test_timeline_driver: "pskill_test",
    material_analysis: "material_analysis"
  };

  const STATUS_OPTIONS = [
    { value: "pending", label: "待处理" },
    { value: "running", label: "运行中" },
    { value: "succeeded", label: "成功" },
    { value: "failed", label: "失败" },
    { value: "cancelled", label: "已取消" },
    { value: "retryable_failed", label: "等待重试" }
  ];

  window.PSOPConsoleTasksMethods = {
    async loadTasksPage() {
      await this.loadTasks();
      this.startTaskPolling();
    },

    async loadTasks(options = {}) {
      const silent = Boolean(options.silent);
      if (!silent) {
        this.busy.tasks = true;
      }
      try {
        const [tasks, stats] = await Promise.all([
          this.apiRequest(`/runtime/jobs?${this.taskQueryString()}`),
          this.apiRequest("/runtime/jobs/stats?window_hours=24")
        ]);
        this.tasks = Array.isArray(tasks) ? tasks : [];
        this.taskStats = stats || null;
        this.taskLastLoadedAt = new Date().toISOString();
      } catch (error) {
        if (!silent) {
          this.showNotice("error", error.message || "任务列表加载失败。");
        }
      } finally {
        if (!silent) {
          this.busy.tasks = false;
        }
      }
    },

    startTaskPolling() {
      this.stopTaskPolling();
      if (typeof window === "undefined") {
        return;
      }
      this.taskPollTimer = window.setInterval(() => {
        if (this.route.name === "tasks-list") {
          this.loadTasks({ silent: true });
        }
      }, 5000);
    },

    stopTaskPolling() {
      if (this.taskPollTimer && typeof window !== "undefined") {
        window.clearInterval(this.taskPollTimer);
      }
      this.taskPollTimer = null;
    },

    refreshTasks() {
      return this.loadTasks();
    },

    applyTaskFilters() {
      return this.loadTasks();
    },

    resetTaskFilters() {
      this.taskFilters = {
        job_type: "",
        status: "",
        q: "",
        created_from: "",
        created_to: ""
      };
      return this.loadTasks();
    },

    taskQueryString() {
      const params = new URLSearchParams();
      params.set("limit", "100");
      params.set("offset", "0");
      this.appendTaskFilterParam(params, "job_type", this.normalizeTaskJobType(this.taskFilters.job_type));
      this.appendTaskFilterParam(params, "status", this.taskFilters.status);
      this.appendTaskFilterParam(params, "q", this.taskFilters.q);
      this.appendTaskFilterParam(params, "created_from", this.taskDateStart(this.taskFilters.created_from));
      this.appendTaskFilterParam(params, "created_to", this.taskDateEnd(this.taskFilters.created_to));
      return params.toString();
    },

    appendTaskFilterParam(params, key, value) {
      const text = String(value || "").trim();
      if (text) {
        params.set(key, text);
      }
    },

    taskDateStart(value) {
      return value ? new Date(`${value}T00:00:00`).toISOString() : "";
    },

    taskDateEnd(value) {
      return value ? new Date(`${value}T23:59:59`).toISOString() : "";
    },

    normalizeTaskJobType(value) {
      return JOB_TYPE_ALIASES[value] || value || "";
    },

    taskTypeOptions() {
      const known = new Map(KNOWN_JOB_TYPES.map((item) => [item.value, item.label]));
      for (const task of this.tasks || []) {
        const normalizedJobType = this.normalizeTaskJobType(task?.job_type);
        if (normalizedJobType && !known.has(normalizedJobType)) {
          known.set(normalizedJobType, normalizedJobType);
        }
      }
      return Array.from(known, ([value, label]) => ({ value, label }));
    },

    taskStatusOptions() {
      return STATUS_OPTIONS;
    },

    jobTypeLabel(value) {
      const normalizedJobType = this.normalizeTaskJobType(value);
      const found = KNOWN_JOB_TYPES.find((item) => item.value === normalizedJobType);
      return found ? found.label : normalizedJobType || "未知任务";
    },

    jobProgressLabel(task) {
      return task?.progress?.label || this.formatStatus(task?.status);
    },

    jobProgressDetail(task) {
      return task?.progress?.detail || "";
    },

    jobProgressPercent(task) {
      const percent = Number(task?.progress?.percent);
      if (!Number.isFinite(percent)) {
        return null;
      }
      return Math.max(0, Math.min(100, Math.round(percent)));
    },

    jobProgressPercentLabel(task) {
      const percent = this.jobProgressPercent(task);
      return percent === null ? "N/A" : `${percent}%`;
    },

    jobProgressBarWidth(task) {
      const percent = this.jobProgressPercent(task);
      return `${percent === null ? 0 : percent}%`;
    },

    taskJobIdLabel(task) {
      return this.formatShortId(task?.id);
    },

    taskRelatedLabel(task) {
      if (!task) {
        return "N/A";
      }
      if (task.run_id) {
        return `Run ${this.formatShortId(task.run_id)}`;
      }
      if (task.compile_request_id) {
        return `Compile ${this.formatShortId(task.compile_request_id)}`;
      }
      const payload = task.payload || {};
      if (payload.analysis_id) {
        return `Analysis ${this.formatShortId(payload.analysis_id)}`;
      }
      if (payload.scenario_run_id) {
        return `Scenario ${this.formatShortId(payload.scenario_run_id)}`;
      }
      if (payload.pskill_definition_id) {
        return `Skill ${this.formatShortId(payload.pskill_definition_id)}`;
      }
      return "N/A";
    },

    taskErrorSummary(task) {
      const text = String(task?.last_error || task?.payload?.error_message || "").trim();
      if (!text) {
        return "";
      }
      return text.length > 120 ? `${text.slice(0, 120)}...` : text;
    },

    taskTokenLabel(task) {
      return this.formatTokenUsage(task?.token_usage);
    },

    taskDurationLabel(task) {
      return this.formatDuration(task?.duration_ms);
    },

    taskStatsNumber(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) {
        return "N/A";
      }
      return new Intl.NumberFormat("zh-CN").format(number);
    },

    taskStatsDuration(value) {
      return this.formatDuration(value);
    },

    taskStatsTokenLabel() {
      return this.formatTokenUsage(this.taskStats?.token_usage);
    },

    taskSuccessRateLabel() {
      const value = Number(this.taskStats?.success_rate);
      if (!Number.isFinite(value)) {
        return "N/A";
      }
      return `${Math.round(value * 1000) / 10}%`;
    },

    taskUpdatedLabel() {
      return this.taskLastLoadedAt ? this.formatDateTime(this.taskLastLoadedAt) : "N/A";
    }
  };
})();
