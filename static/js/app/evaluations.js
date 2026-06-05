(function () {
  const FINDING_STATUS_OPTIONS = [
    { value: "open", label: "未处理" },
    { value: "accepted", label: "已接受" },
    { value: "dismissed", label: "已驳回" },
    { value: "converted_to_proposal", label: "已转提案" },
    { value: "resolved", label: "已解决" }
  ];

  const FINDING_CATEGORY_OPTIONS = [
    { value: "pskill_build_issue", label: "PSkill 构建" },
    { value: "compile_issue", label: "编译" },
    { value: "test_gap", label: "测试缺口" },
    { value: "runner_issue", label: "运行智能体" },
    { value: "human_operation_issue", label: "人工操作" },
    { value: "evidence_quality_issue", label: "证据质量" },
    { value: "tool_issue", label: "工具" },
    { value: "environment_issue", label: "环境" }
  ];

  const FINDING_SEVERITY_OPTIONS = [
    { value: "low", label: "低" },
    { value: "medium", label: "中" },
    { value: "high", label: "高" },
    { value: "critical", label: "严重" }
  ];

  window.PSOPConsoleEvaluationMethods = {
    async loadEvaluationReportsPage() {
      if (this.evaluationForm.evaluation_id && !this.currentEvaluation) {
        await this.loadEvaluationReport(this.evaluationForm.evaluation_id);
      }
    },

    async createRunEvaluation() {
      const runId = String(this.evaluationForm.run_id || "").trim();
      if (!runId) {
        this.showNotice("error", "请输入 Run ID。");
        return;
      }

      this.busy.evaluationReport = true;
      this.clearNotice();
      try {
        const evaluation = await this.apiRequest(`/evaluations/runs/${encodeURIComponent(runId)}`, {
          method: "POST"
        });
        this.currentEvaluation = evaluation;
        this.evaluationForm.evaluation_id = evaluation.id;
        this.evaluationForm.run_id = evaluation.run_id;
        await this.navigate(this.evaluationReportPath(evaluation.id));
      } catch (error) {
        this.showNotice("error", error.message || "创建 Run 评估失败。");
      } finally {
        this.busy.evaluationReport = false;
      }
    },

    async openEvaluationFromForm() {
      const evaluationId = String(this.evaluationForm.evaluation_id || "").trim();
      if (!evaluationId) {
        this.showNotice("error", "请输入 Evaluation ID。");
        return;
      }
      await this.navigate(this.evaluationReportPath(evaluationId));
    },

    async loadEvaluationReport(evaluationId) {
      const id = String(evaluationId || "").trim();
      if (!id) {
        return;
      }
      this.busy.evaluationReport = true;
      try {
        const evaluation = await this.apiRequest(`/evaluations/${encodeURIComponent(id)}`);
        this.currentEvaluation = evaluation;
        this.evaluationForm.evaluation_id = evaluation.id;
        this.evaluationForm.run_id = evaluation.run_id;
      } finally {
        this.busy.evaluationReport = false;
      }
    },

    async loadEvaluationFindings() {
      this.busy.evaluationFindings = true;
      try {
        const query = this.evaluationFindingsQueryString();
        const suffix = query ? `?${query}` : "";
        const findings = await this.apiRequest(`/evaluations/findings${suffix}`);
        this.evaluationFindings = Array.isArray(findings) ? findings : [];
      } catch (error) {
        this.showNotice("error", error.message || "Findings 加载失败。");
      } finally {
        this.busy.evaluationFindings = false;
      }
    },

    applyEvaluationFindingFilters() {
      return this.loadEvaluationFindings();
    },

    resetEvaluationFindingFilters() {
      this.evaluationFindingFilters = {
        status: "open",
        category: "",
        severity: "",
        run_id: "",
        pskill_definition_id: ""
      };
      return this.loadEvaluationFindings();
    },

    evaluationFindingsQueryString() {
      const params = new URLSearchParams();
      this.appendEvaluationFilterParam(params, "status", this.evaluationFindingFilters.status);
      this.appendEvaluationFilterParam(params, "category", this.evaluationFindingFilters.category);
      this.appendEvaluationFilterParam(params, "severity", this.evaluationFindingFilters.severity);
      this.appendEvaluationFilterParam(params, "run_id", this.evaluationFindingFilters.run_id);
      this.appendEvaluationFilterParam(params, "pskill_definition_id", this.evaluationFindingFilters.pskill_definition_id);
      return params.toString();
    },

    appendEvaluationFilterParam(params, key, value) {
      const text = String(value || "").trim();
      if (text) {
        params.set(key, text);
      }
    },

    async updateEvaluationFindingStatus(finding, status) {
      if (!finding?.id || !status) {
        return;
      }
      this.busy.evaluationFindingUpdate = true;
      try {
        const updated = await this.apiRequest(`/evaluations/findings/${encodeURIComponent(finding.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ status })
        });
        this.evaluationFindings = this.evaluationFindings.map((item) => item.id === updated.id ? updated : item);
        if (this.currentEvaluation?.findings) {
          this.currentEvaluation.findings = this.currentEvaluation.findings.map((item) => item.id === updated.id ? updated : item);
        }
      } catch (error) {
        this.showNotice("error", error.message || "更新 finding 状态失败。");
      } finally {
        this.busy.evaluationFindingUpdate = false;
      }
    },

    async createProposalFromEvaluationFinding(finding) {
      if (!finding?.id) {
        return;
      }
      this.busy.evaluationFindingUpdate = true;
      try {
        const proposal = await this.apiRequest(`/evaluations/findings/${encodeURIComponent(finding.id)}/create-proposal`, {
          method: "POST"
        });
        const convertedFinding = { ...finding, status: "converted_to_proposal" };
        this.evaluationFindings = this.evaluationFindings.map((item) => item.id === finding.id ? convertedFinding : item);
        if (this.currentEvaluation?.findings) {
          this.currentEvaluation.findings = this.currentEvaluation.findings.map((item) => item.id === finding.id ? convertedFinding : item);
        }
        this.showNotice("success", "已创建治理提案。");
        await this.navigate(this.governanceProposalPath(proposal.id));
      } catch (error) {
        this.showNotice("error", error.message || "创建治理提案失败。");
      } finally {
        this.busy.evaluationFindingUpdate = false;
      }
    },

    evaluationReportPath(evaluationId) {
      return `/admin/evaluations/${evaluationId}`;
    },

    evaluationFindingsPath() {
      return "/admin/evaluations/findings";
    },

    evaluationRunReplayPath(evaluation) {
      return evaluation?.run_id ? `/admin/replay/runs/${evaluation.run_id}` : "/admin/replay";
    },

    findingStatusOptions() {
      return FINDING_STATUS_OPTIONS;
    },

    findingCategoryOptions() {
      return FINDING_CATEGORY_OPTIONS;
    },

    findingSeverityOptions() {
      return FINDING_SEVERITY_OPTIONS;
    },

    findingStatusLabel(value) {
      return this.optionLabel(FINDING_STATUS_OPTIONS, value);
    },

    findingCategoryLabel(value) {
      return this.optionLabel(FINDING_CATEGORY_OPTIONS, value);
    },

    findingSeverityLabel(value) {
      return this.optionLabel(FINDING_SEVERITY_OPTIONS, value);
    },

    optionLabel(options, value) {
      const found = options.find((item) => item.value === value);
      return found ? found.label : value || "未知";
    },

    evaluationOutcomeLabel(value) {
      const labels = {
        success: "成功",
        completed_with_issues: "完成但有问题",
        failed: "失败",
        aborted: "中止",
        cancelled: "取消"
      };
      return labels[value] || value || "未知";
    },

    evaluationOutcomeTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (normalized === "success") {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (normalized === "completed_with_issues") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      if (["failed", "aborted", "cancelled"].includes(normalized)) {
        return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      }
      return "border-slate-700 bg-slate-950/40 text-slate-400";
    },

    evaluationScoreTone(score) {
      const value = Number(score);
      if (!Number.isFinite(value)) {
        return "text-slate-300";
      }
      if (value >= 85) {
        return "text-emerald-200";
      }
      if (value >= 65) {
        return "text-amber-200";
      }
      return "text-rose-200";
    },

    evaluationScoreBarWidth(score) {
      const value = Number(score);
      if (!Number.isFinite(value)) {
        return "0%";
      }
      return `${Math.max(0, Math.min(100, Math.round(value)))}%`;
    },

    findingSeverityTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (normalized === "low") {
        return "border-slate-600 bg-slate-950/40 text-slate-300";
      }
      if (normalized === "medium") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      if (normalized === "high") {
        return "border-orange-500/30 bg-orange-500/10 text-orange-200";
      }
      if (normalized === "critical") {
        return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      }
      return "border-slate-700 bg-slate-950/40 text-slate-400";
    },

    findingStatusTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (["accepted", "resolved"].includes(normalized)) {
        return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      }
      if (normalized === "converted_to_proposal") {
        return "border-sky-500/25 bg-sky-500/10 text-sky-200";
      }
      if (normalized === "dismissed") {
        return "border-slate-700 bg-slate-950/40 text-slate-400";
      }
      return "border-amber-500/25 bg-amber-500/10 text-amber-200";
    },

    findingEvidenceLabel(ref) {
      if (!ref) {
        return "N/A";
      }
      const kind = ref.kind || "evidence";
      const seq = ref.seq_no === null || ref.seq_no === undefined ? "" : ` #${ref.seq_no}`;
      const type = ref.event_type || ref.event_kind || "";
      return `${kind}${seq}${type ? ` · ${type}` : ""}`;
    },

    evaluationFindingCount(evaluation) {
      return Array.isArray(evaluation?.findings) ? evaluation.findings.length : 0;
    },

    evaluationOpenFindingCount(evaluation) {
      return (evaluation?.findings || []).filter((finding) => finding.status === "open").length;
    },

    evaluationAttributionCategories(evaluation) {
      const categories = evaluation?.attribution?.categories;
      if (!categories || typeof categories !== "object") {
        return [];
      }
      return Object.entries(categories).map(([category, payload]) => ({
        category,
        count: Number(payload?.count || 0),
        penalty: Number(payload?.penalty || 0)
      }));
    }
  };
})();
