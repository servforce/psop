(function () {
  const {
    buildEvaluationReportPath,
    buildGovernanceProposalPath,
    buildPlatformMemoryEntryPath,
    buildPlatformSkillsPath,
    buildPlatformSkillPath,
    buildRunLivePath,
    buildSkillDetailPath,
    buildSkillTestScenarioRunReviewPath,
    buildCompilerArtifactPath,
    buildTasksPath
  } = window.PSOPConsoleHelpers || {};

  const KNOWN_JOB_TYPES = [
    { value: "material_analysis", label: "PSkill 素材解析" },
    { value: "pskill_build", label: "PSkill 智能体构建" },
    { value: "pskill_compile", label: "PSkill 编译" },
    { value: "pskill_test", label: "PSkill 测试" },
    { value: "runtime_step", label: "Runtime 推进" },
    { value: "run_evaluation", label: "Run 评估" },
    { value: "governance_proposal", label: "治理提案生成" },
    { value: "memory_compaction", label: "记忆压缩" },
    { value: "skill_sync", label: "Skill 包同步" }
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
    { value: "retryable_failed", label: "等待重试" },
    { value: "dead_letter", label: "死信" }
  ];

  window.PSOPConsoleTasksMethods = {
    async loadTasksPage() {
      this.syncTaskFiltersFromLocation();
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
      this.replaceTaskFilterLocation();
      return this.loadTasks();
    },

    resetTaskFilters() {
      this.taskFilters = this.emptyTaskFilters();
      this.replaceTaskFilterLocation();
      return this.loadTasks();
    },

    emptyTaskFilters() {
      return {
        job_type: "",
        status: "",
        q: "",
        created_from: "",
        created_to: ""
      };
    },

    syncTaskFiltersFromLocation() {
      const search = this.taskLocationSearch();
      if (search === (this.taskFiltersLocationSearch || "")) {
        return;
      }
      if (!search) {
        if (this.taskFiltersLocationSearch) {
          this.taskFilters = this.emptyTaskFilters();
        }
        this.taskFiltersLocationSearch = "";
        return;
      }
      const params = new URLSearchParams(search);
      this.taskFilters = {
        ...this.emptyTaskFilters(),
        job_type: params.get("job_type") || "",
        status: params.get("status") || "",
        q: params.get("q") || "",
        created_from: params.get("created_from") || "",
        created_to: params.get("created_to") || ""
      };
      this.taskFiltersLocationSearch = search;
    },

    replaceTaskFilterLocation() {
      if (typeof window === "undefined" || !window.history?.replaceState) {
        return;
      }
      const path = this.taskFilterPath(this.taskFilters);
      window.history.replaceState({}, "", path);
      this.taskFiltersLocationSearch = this.taskLocationSearch();
    },

    taskLocationSearch() {
      if (typeof window === "undefined") {
        return "";
      }
      return window.location.search || "";
    },

    taskFilterPath(filters = {}) {
      if (typeof buildTasksPath === "function") {
        return buildTasksPath(filters);
      }
      const params = new URLSearchParams();
      for (const key of ["job_type", "status", "q", "created_from", "created_to"]) {
        this.appendTaskFilterParam(params, key, filters[key]);
      }
      const query = params.toString();
      return query ? `/admin/tasks?${query}` : "/admin/tasks";
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
      return this.taskRelatedEntity(task).label;
    },

    taskRelatedHref(task) {
      return this.taskRelatedEntity(task).href;
    },

    taskRelatedTitle(task) {
      const entity = this.taskRelatedEntity(task);
      return entity.id ? `${entity.kind}: ${entity.id}` : entity.label;
    },

    taskRelatedEntity(task) {
      if (!task) {
        return this.emptyTaskEntity();
      }
      const jobType = this.normalizeTaskJobType(task.job_type);
      if (jobType === "run_evaluation") {
        return (
          this.taskEntity("Evaluation", this.taskValue(task, "evaluation_id"), buildEvaluationReportPath)
          || this.taskEntity("Run", this.taskValue(task, "run_id"), buildRunLivePath)
          || this.emptyTaskEntity()
        );
      }
      if (jobType === "governance_proposal") {
        return (
          this.taskEntity("Proposal", this.taskValue(task, "proposal_id"), buildGovernanceProposalPath)
          || this.taskEntity("Evaluation", this.taskValue(task, "source_evaluation_id"), buildEvaluationReportPath)
          || this.taskEntity("Run", this.taskValue(task, "source_run_id", "run_id"), buildRunLivePath)
          || this.taskEntity("Finding", this.taskValue(task, "finding_id"))
          || this.emptyTaskEntity()
        );
      }
      if (jobType === "memory_compaction") {
        return (
          this.taskEntity("Memory", this.taskValue(task, "compacted_memory_id"), buildPlatformMemoryEntryPath)
          || this.taskEntity("Namespace", this.taskValue(task, "target_namespace", "namespace"))
          || this.emptyTaskEntity()
        );
      }
      if (jobType === "skill_sync") {
        const packageName = this.taskValue(task, "package_name", "skill_package_name");
        if (packageName) {
          return this.taskEntity("Skill Package", packageName, buildPlatformSkillPath);
        }
        return {
          kind: "Skill Packages",
          id: "",
          label: "Skill Packages",
          href: this.taskPath(buildPlatformSkillsPath)
        };
      }
      if (jobType === "runtime_step") {
        return this.taskEntity("Run", this.taskValue(task, "run_id"), buildRunLivePath) || this.emptyTaskEntity();
      }
      if (jobType === "pskill_compile") {
        return (
          this.taskEntity("Artifact", this.taskValue(task, "artifact_id", "compile_artifact_id"), buildCompilerArtifactPath)
          || this.taskEntity("PSkill", this.taskValue(task, "pskill_definition_id", "skill_id"), buildSkillDetailPath)
          || this.taskEntity("Compile", this.taskValue(task, "compile_request_id"))
          || this.emptyTaskEntity()
        );
      }
      if (jobType === "pskill_test") {
        return this.taskTestEntity(task) || this.taskEntity("Run", this.taskValue(task, "run_id"), buildRunLivePath) || this.emptyTaskEntity();
      }
      if (jobType === "pskill_build") {
        return (
          this.taskEntity("PSkill", this.taskValue(task, "pskill_definition_id", "skill_id"), buildSkillDetailPath)
          || this.taskEntity("Generation", this.taskValue(task, "generation_id"))
          || this.emptyTaskEntity()
        );
      }
      if (jobType === "material_analysis") {
        return (
          this.taskEntity("PSkill", this.taskValue(task, "pskill_definition_id", "skill_id"), buildSkillDetailPath)
          || this.taskEntity("Analysis", this.taskValue(task, "analysis_id"))
          || this.taskEntity("Material", this.taskValue(task, "material_id"))
          || this.emptyTaskEntity()
        );
      }
      return (
        this.taskEntity("Run", this.taskValue(task, "run_id"), buildRunLivePath)
        || this.taskEntity("Entity", this.taskValue(task, "entity_id", "owner_id"))
        || this.emptyTaskEntity()
      );
    },

    taskTestEntity(task) {
      const scenarioRunId = this.taskValue(task, "scenario_run_id");
      const scenarioId = this.taskValue(task, "scenario_id");
      const pskillId = this.taskValue(task, "pskill_definition_id", "pskill_id", "skill_id");
      if (scenarioRunId && scenarioId && pskillId) {
        return this.taskEntity("Scenario Run", scenarioRunId, buildSkillTestScenarioRunReviewPath, pskillId, scenarioId);
      }
      if (this.taskValue(task, "run_id")) {
        return null;
      }
      return this.taskEntity("Scenario", scenarioRunId || scenarioId);
    },

    taskValue(task, ...keys) {
      const payload = task?.payload || {};
      const metrics = task?.metrics || {};
      for (const key of keys) {
        const value = task?.[key] ?? payload[key] ?? metrics[key];
        const normalized = String(value || "").trim();
        if (normalized) {
          return normalized;
        }
      }
      return "";
    },

    taskEntity(kind, id, pathBuilder, ...pathArgs) {
      const normalizedId = String(id || "").trim();
      if (!normalizedId) {
        return null;
      }
      return {
        kind,
        id: normalizedId,
        label: `${kind} ${this.formatShortId(normalizedId)}`,
        href: this.taskPath(pathBuilder, ...pathArgs, normalizedId)
      };
    },

    taskPath(pathBuilder, ...args) {
      if (typeof pathBuilder !== "function") {
        return "";
      }
      const normalizedArgs = args.map((value) => String(value || "").trim());
      if (normalizedArgs.some((value) => !value)) {
        return "";
      }
      return pathBuilder(...normalizedArgs);
    },

    emptyTaskEntity() {
      return { kind: "", id: "", label: "N/A", href: "" };
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
