(function () {
  const {
    resolveWsUrl,
    buildEvaluationReportsPath,
    buildEvaluationFindingsPath,
    buildReplayPath,
    buildPlatformAgentRunPath,
    buildPlatformMemoryEntryPath,
    buildGovernanceProposalPath,
    buildGovernanceExperimentsPath,
    buildTasksPath,
    buildToolAuthorizationsPath
  } = window.PSOPConsoleHelpers || {};

  const EVALUATION_OUTCOME_OPTIONS = [
    { value: "success", label: "成功" },
    { value: "completed_with_issues", label: "完成但有问题" },
    { value: "failed", label: "失败" },
    { value: "aborted", label: "已中止" },
    { value: "cancelled", label: "已取消" }
  ];

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

  const FINDING_CATEGORY_PROPOSAL_TYPES = {
    pskill_build_issue: "pskill_template_update",
    compile_issue: "validator_update",
    test_gap: "test_suite_update",
    runner_issue: "agent_skill_update",
    human_operation_issue: "pskill_template_update",
    evidence_quality_issue: "pskill_template_update",
    tool_issue: "tool_policy_update",
    environment_issue: "test_suite_update"
  };

  const FINDING_SEVERITY_RANK = {
    low: 1,
    medium: 2,
    high: 3,
    critical: 4
  };

  const FINDING_SEVERITY_RISK = {
    low: "low",
    medium: "medium",
    high: "high",
    critical: "high"
  };

  window.PSOPConsoleEvaluationMethods = {
    async loadEvaluationReportsPage() {
      this.disconnectEvaluationActivityWebSocket?.();
      this.currentEvaluation = null;
      this.syncEvaluationReportFiltersFromLocation();
      await this.loadEvaluationReports();
    },

    async loadEvaluationReports() {
      this.busy.evaluationReports = true;
      try {
        const reports = await this.apiRequest(`/evaluations?${this.evaluationReportsQueryString()}`);
        this.evaluationReports = Array.isArray(reports) ? reports : [];
      } catch (error) {
        this.showNotice("error", error.message || "Run 评估报告加载失败。");
      } finally {
        this.busy.evaluationReports = false;
      }
    },

    applyEvaluationReportFilters() {
      this.replaceEvaluationReportFilterLocation();
      return this.loadEvaluationReports();
    },

    resetEvaluationReportFilters() {
      this.evaluationReportFilters = this.emptyEvaluationReportFilters();
      this.replaceEvaluationReportFilterLocation();
      return this.loadEvaluationReports();
    },

    emptyEvaluationReportFilters() {
      return {
        run_id: "",
        pskill_definition_id: "",
        overall_outcome: ""
      };
    },

    syncEvaluationReportFiltersFromLocation() {
      const search = this.evaluationReportLocationSearch();
      if (search === (this.evaluationReportFiltersLocationSearch || "")) {
        return;
      }
      if (!search) {
        if (this.evaluationReportFiltersLocationSearch) {
          this.evaluationReportFilters = this.emptyEvaluationReportFilters();
        }
        this.evaluationReportFiltersLocationSearch = "";
        return;
      }
      const params = new URLSearchParams(search);
      this.evaluationReportFilters = {
        ...this.emptyEvaluationReportFilters(),
        run_id: params.get("run_id") || "",
        pskill_definition_id: params.get("pskill_definition_id") || "",
        overall_outcome: params.get("overall_outcome") || ""
      };
      this.evaluationReportFiltersLocationSearch = search;
    },

    replaceEvaluationReportFilterLocation() {
      if (typeof window === "undefined" || !window.history?.replaceState) {
        return;
      }
      const path = this.evaluationReportsPath(this.evaluationReportFilters);
      window.history.replaceState({}, "", path);
      this.evaluationReportFiltersLocationSearch = this.evaluationReportLocationSearch();
    },

    evaluationReportLocationSearch() {
      if (typeof window === "undefined") {
        return "";
      }
      return window.location?.search || "";
    },

    evaluationReportsQueryString() {
      const params = new URLSearchParams();
      this.appendEvaluationFilterParam(params, "run_id", this.evaluationReportFilters?.run_id);
      this.appendEvaluationFilterParam(params, "pskill_definition_id", this.evaluationReportFilters?.pskill_definition_id);
      this.appendEvaluationFilterParam(params, "overall_outcome", this.evaluationReportFilters?.overall_outcome);
      params.set("limit", "50");
      return params.toString();
    },

    evaluationReportsPath(filters = this.evaluationReportFilters) {
      if (typeof buildEvaluationReportsPath === "function") {
        return buildEvaluationReportsPath(filters);
      }
      const params = new URLSearchParams();
      this.appendEvaluationFilterParam(params, "run_id", filters?.run_id);
      this.appendEvaluationFilterParam(params, "pskill_definition_id", filters?.pskill_definition_id);
      this.appendEvaluationFilterParam(params, "overall_outcome", filters?.overall_outcome);
      const query = params.toString();
      return query ? `/admin/evaluations?${query}` : "/admin/evaluations";
    },

    evaluationReportHasFilters() {
      return Boolean(
        String(this.evaluationReportFilters?.run_id || "").trim() ||
        String(this.evaluationReportFilters?.pskill_definition_id || "").trim() ||
        String(this.evaluationReportFilters?.overall_outcome || "").trim()
      );
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
        this.upsertEvaluationReport(evaluation);
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
        this.upsertEvaluationReport(evaluation);
        this.applyEvaluationActivitySnapshot({ evaluation });
        this.connectEvaluationActivityWebSocket(evaluation.id);
      } finally {
        this.busy.evaluationReport = false;
      }
    },

    connectEvaluationActivityWebSocket(evaluationId = this.currentEvaluation?.id) {
      const id = String(evaluationId || "").trim();
      if (!id || typeof WebSocket === "undefined" || typeof resolveWsUrl !== "function") {
        return false;
      }
      if (
        this.evaluationActivityWs &&
        this.evaluationActivityWsId === id &&
        [WebSocket.CONNECTING, WebSocket.OPEN].includes(this.evaluationActivityWs.readyState)
      ) {
        return true;
      }

      this.disconnectEvaluationActivityWebSocket();
      const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, `/ws/evaluations/${encodeURIComponent(id)}`));
      this.evaluationActivityWs = socket;
      this.evaluationActivityWsId = id;
      this.evaluationActivityWsStatus = "connecting";
      socket.addEventListener("open", () => {
        if (this.evaluationActivityWs === socket) {
          this.evaluationActivityWsStatus = "open";
        }
      });
      socket.addEventListener("message", (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.event_type === "evaluation.activity.snapshot" && message.payload) {
            this.applyEvaluationActivitySnapshot(message.payload);
          }
          if (message.event_type === "evaluation.activity.error") {
            this.showNotice("error", message.payload?.message || "获取 Evaluation 活动流失败。");
            this.disconnectEvaluationActivityWebSocket();
          }
        } catch {
          // Ignore malformed activity messages; REST actions remain the recovery path.
        }
      });
      socket.addEventListener("close", () => {
        if (this.evaluationActivityWs === socket) {
          this.evaluationActivityWsStatus = "closed";
        }
      });
      socket.addEventListener("error", () => {
        if (this.evaluationActivityWs === socket) {
          this.evaluationActivityWsStatus = "error";
        }
      });
      return true;
    },

    disconnectEvaluationActivityWebSocket() {
      if (this.evaluationActivityWs) {
        this.evaluationActivityWs.close();
      }
      this.evaluationActivityWs = null;
      this.evaluationActivityWsId = "";
      this.evaluationActivityWsStatus = "idle";
    },

    applyEvaluationActivitySnapshot(snapshot) {
      if (!snapshot || typeof snapshot !== "object") {
        return;
      }
      const evaluation = snapshot.evaluation || null;
      if (evaluation?.id) {
        this.currentEvaluation = evaluation;
        this.evaluationForm.evaluation_id = evaluation.id;
        this.evaluationForm.run_id = evaluation.run_id;
        this.upsertEvaluationReport(evaluation);
      }
      if (snapshot.agent_run) {
        this.evaluationAgentRun = snapshot.agent_run;
      }
      if (Array.isArray(snapshot.agent_events)) {
        this.evaluationAgentEvents = snapshot.agent_events;
      }
      if (Array.isArray(snapshot.model_calls)) {
        this.evaluationModelCalls = snapshot.model_calls;
      }
      if (Array.isArray(snapshot.memory_entries)) {
        this.evaluationMemoryEntries = snapshot.memory_entries;
      }
      const findings = Array.isArray(snapshot.findings)
        ? snapshot.findings
        : Array.isArray(evaluation?.findings)
          ? evaluation.findings
          : null;
      if (findings && this.currentEvaluation?.id) {
        this.currentEvaluation.findings = findings;
        this.evaluationFindings = (this.evaluationFindings || []).map((item) => {
          const updated = findings.find((finding) => finding.id === item.id);
          return updated || item;
        });
        this.syncEvaluationFindingSelection();
      }
    },

    upsertEvaluationReport(evaluation) {
      if (!evaluation?.id) {
        return;
      }
      const existing = Array.isArray(this.evaluationReports) ? this.evaluationReports : [];
      this.evaluationReports = [
        evaluation,
        ...existing.filter((item) => item.id !== evaluation.id)
      ].slice(0, 50);
    },

    evaluationAgentRunPath(evaluationOrAgentRun = this.currentEvaluation, focus = { tab: "events" }) {
      const agentRunId = String(
        typeof evaluationOrAgentRun === "string"
          ? evaluationOrAgentRun
          : (evaluationOrAgentRun?.agent_run_id || evaluationOrAgentRun?.id || "")
      ).trim();
      if (!agentRunId || typeof buildPlatformAgentRunPath !== "function") {
        return "";
      }
      return buildPlatformAgentRunPath(agentRunId, focus);
    },

    openEvaluationAgentRun(evaluationOrAgentRun = this.currentEvaluation, focus = { tab: "events" }) {
      const path = this.evaluationAgentRunPath(evaluationOrAgentRun, focus);
      if (!path) {
        return;
      }
      this.navigate(path);
    },

    evaluationMemoryEntryPath(memory) {
      const memoryId = String(memory?.id || memory || "").trim();
      if (!memoryId || typeof buildPlatformMemoryEntryPath !== "function") {
        return "";
      }
      return buildPlatformMemoryEntryPath(memoryId);
    },

    openEvaluationMemoryEntry(memory) {
      const path = this.evaluationMemoryEntryPath(memory);
      if (!path) {
        return;
      }
      this.navigate(path);
    },

    evaluationGovernanceQueueItems(evaluation = this.currentEvaluation) {
      const events = Array.isArray(this.evaluationAgentEvents) ? this.evaluationAgentEvents : [];
      const queueEvents = events.filter((event) => event?.event_type === "evaluation.governance_proposals.queued");
      const items = [];
      const seen = new Set();
      for (const event of queueEvents) {
        const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
        const queuedItems = Array.isArray(payload.queued_items) ? payload.queued_items : [];
        if (queuedItems.length) {
          for (const queued of queuedItems) {
            const item = this.normalizeEvaluationGovernanceQueueItem(queued, payload, event, evaluation);
            if (!item || seen.has(item.key)) {
              continue;
            }
            seen.add(item.key);
            items.push(item);
          }
          continue;
        }

        const jobIds = Array.isArray(payload.governance_proposal_job_ids)
          ? payload.governance_proposal_job_ids
          : [];
        const findingIds = Array.isArray(payload.source_finding_ids) ? payload.source_finding_ids : [];
        jobIds.forEach((jobId, index) => {
          const item = this.normalizeEvaluationGovernanceQueueItem(
            {
              job_id: jobId,
              finding_id: findingIds[index] || findingIds[0] || ""
            },
            payload,
            event,
            evaluation
          );
          if (!item || seen.has(item.key)) {
            return;
          }
          seen.add(item.key);
          items.push(item);
        });
      }
      return items;
    },

    normalizeEvaluationGovernanceQueueItem(queued, payload = {}, event = {}, evaluation = this.currentEvaluation) {
      const jobId = String(queued?.job_id || queued?.governance_proposal_job_id || "").trim();
      if (!jobId) {
        return null;
      }
      const findingId = String(queued?.finding_id || queued?.source_finding_id || "").trim();
      const finding = this.evaluationFindingById(findingId, evaluation);
      return {
        key: `${jobId}:${findingId || "finding"}`,
        job_id: jobId,
        finding_id: findingId,
        category: queued?.category || finding?.category || "",
        severity: queued?.severity || finding?.severity || "",
        event_id: event?.id || "",
        queued_by: payload.queued_by || "",
        non_hitl_business_state: payload.non_hitl_business_state === true,
        tool_authorization_created: payload.tool_authorization_created === true,
        path: this.evaluationGovernanceQueueJobPath(jobId)
      };
    },

    evaluationFindingById(findingId, evaluation = this.currentEvaluation) {
      const id = String(findingId || "").trim();
      if (!id) {
        return null;
      }
      const findings = Array.isArray(evaluation?.findings)
        ? evaluation.findings
        : Array.isArray(this.evaluationFindings)
          ? this.evaluationFindings
          : [];
      return findings.find((finding) => finding?.id === id) || null;
    },

    evaluationGovernanceQueueJobPath(jobId) {
      const id = String(jobId || "").trim();
      if (!id || typeof buildTasksPath !== "function") {
        return "";
      }
      return buildTasksPath({ job_type: "governance_proposal", q: id });
    },

    openEvaluationGovernanceQueueJob(item) {
      const path = item?.path || this.evaluationGovernanceQueueJobPath(item?.job_id || item);
      if (!path) {
        return;
      }
      this.navigate(path);
    },

    async loadEvaluationFindingsPage() {
      this.syncEvaluationFindingFiltersFromLocation();
      await this.loadEvaluationFindings();
    },

    async loadEvaluationFindings() {
      this.busy.evaluationFindings = true;
      try {
        const query = this.evaluationFindingsQueryString();
        const suffix = query ? `?${query}` : "";
        const findings = await this.apiRequest(`/evaluations/findings${suffix}`);
        this.evaluationFindings = Array.isArray(findings) ? findings : [];
        this.syncEvaluationFindingSelection();
      } catch (error) {
        this.showNotice("error", error.message || "Findings 加载失败。");
      } finally {
        this.busy.evaluationFindings = false;
      }
    },

    applyEvaluationFindingFilters() {
      this.replaceEvaluationFindingFilterLocation();
      return this.loadEvaluationFindings();
    },

    resetEvaluationFindingFilters() {
      this.evaluationFindingFilters = this.emptyEvaluationFindingFilters();
      this.clearEvaluationFindingSelection();
      this.replaceEvaluationFindingFilterLocation();
      return this.loadEvaluationFindings();
    },

    emptyEvaluationFindingFilters() {
      return {
        status: "open",
        category: "",
        severity: "",
        run_id: "",
        pskill_definition_id: ""
      };
    },

    syncEvaluationFindingFiltersFromLocation() {
      const search = this.evaluationFindingLocationSearch();
      if (search === (this.evaluationFindingFiltersLocationSearch || "")) {
        return;
      }
      if (!search) {
        if (this.evaluationFindingFiltersLocationSearch) {
          this.evaluationFindingFilters = this.emptyEvaluationFindingFilters();
        }
        this.evaluationFindingFiltersLocationSearch = "";
        return;
      }
      const params = new URLSearchParams(search);
      this.evaluationFindingFilters = {
        ...this.emptyEvaluationFindingFilters(),
        status: params.get("status") || "",
        category: params.get("category") || "",
        severity: params.get("severity") || "",
        run_id: params.get("run_id") || "",
        pskill_definition_id: params.get("pskill_definition_id") || ""
      };
      this.evaluationFindingFiltersLocationSearch = search;
    },

    replaceEvaluationFindingFilterLocation() {
      if (typeof window === "undefined" || !window.history?.replaceState) {
        return;
      }
      const path = this.evaluationFindingsPath(this.evaluationFindingFilters);
      window.history.replaceState({}, "", path);
      this.evaluationFindingFiltersLocationSearch = this.evaluationFindingLocationSearch();
    },

    evaluationFindingLocationSearch() {
      if (typeof window === "undefined") {
        return "";
      }
      return window.location?.search || "";
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
        this.applyEvaluationFindingUpdate(updated);
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
        this.applyEvaluationFindingUpdate(convertedFinding);
        this.showNotice("success", "已创建治理提案。");
        await this.navigate(this.governanceProposalPath(proposal.id));
      } catch (error) {
        this.showNotice("error", error.message || "创建治理提案失败。");
      } finally {
        this.busy.evaluationFindingUpdate = false;
      }
    },

    applyEvaluationFindingUpdate(updated) {
      if (!updated?.id) {
        return;
      }
      this.evaluationFindings = (this.evaluationFindings || []).map((item) => item.id === updated.id ? updated : item);
      if (this.currentEvaluation?.findings) {
        this.currentEvaluation.findings = this.currentEvaluation.findings.map((item) => item.id === updated.id ? updated : item);
      }
      this.syncEvaluationFindingSelection();
    },

    evaluationFindingId(finding) {
      return String(finding?.id || "").trim();
    },

    selectedEvaluationFindingIdSet() {
      return new Set((this.selectedEvaluationFindingIds || []).map((id) => String(id || "").trim()).filter(Boolean));
    },

    syncEvaluationFindingSelection() {
      const visibleIds = new Set((this.evaluationFindings || []).map((finding) => this.evaluationFindingId(finding)).filter(Boolean));
      this.selectedEvaluationFindingIds = (this.selectedEvaluationFindingIds || [])
        .map((id) => String(id || "").trim())
        .filter((id, index, ids) => id && ids.indexOf(id) === index && visibleIds.has(id));
    },

    isEvaluationFindingSelected(finding) {
      const id = this.evaluationFindingId(finding);
      return Boolean(id && this.selectedEvaluationFindingIdSet().has(id));
    },

    toggleEvaluationFindingSelection(finding) {
      const id = this.evaluationFindingId(finding);
      if (!id) {
        return;
      }
      const selected = this.selectedEvaluationFindingIdSet();
      if (selected.has(id)) {
        selected.delete(id);
      } else {
        selected.add(id);
      }
      this.selectedEvaluationFindingIds = Array.from(selected);
    },

    evaluationFindingsAllVisibleSelected() {
      const visibleIds = (this.evaluationFindings || []).map((finding) => this.evaluationFindingId(finding)).filter(Boolean);
      if (!visibleIds.length) {
        return false;
      }
      const selected = this.selectedEvaluationFindingIdSet();
      return visibleIds.every((id) => selected.has(id));
    },

    toggleAllVisibleEvaluationFindings() {
      const visibleIds = (this.evaluationFindings || []).map((finding) => this.evaluationFindingId(finding)).filter(Boolean);
      if (!visibleIds.length) {
        return;
      }
      if (this.evaluationFindingsAllVisibleSelected()) {
        this.selectedEvaluationFindingIds = [];
        return;
      }
      this.selectedEvaluationFindingIds = Array.from(new Set(visibleIds));
    },

    clearEvaluationFindingSelection() {
      this.selectedEvaluationFindingIds = [];
    },

    selectedEvaluationFindings() {
      const selected = this.selectedEvaluationFindingIdSet();
      return (this.evaluationFindings || []).filter((finding) => selected.has(this.evaluationFindingId(finding)));
    },

    selectedEvaluationFindingCount() {
      return this.selectedEvaluationFindings().length;
    },

    async bulkUpdateSelectedEvaluationFindingsStatus(status) {
      const findings = this.selectedEvaluationFindings();
      if (!findings.length || !status) {
        return;
      }
      this.busy.evaluationFindingUpdate = true;
      let updatedCount = 0;
      try {
        for (const finding of findings) {
          const updated = await this.apiRequest(`/evaluations/findings/${encodeURIComponent(finding.id)}`, {
            method: "PATCH",
            body: JSON.stringify({ status })
          });
          updatedCount += 1;
          this.applyEvaluationFindingUpdate(updated);
        }
        this.clearEvaluationFindingSelection();
        this.showNotice("success", `已将 ${updatedCount} 个 finding 标记为${this.findingStatusLabel(status)}。`);
      } catch (error) {
        this.showNotice("error", error.message || "批量更新 finding 状态失败。");
      } finally {
        this.busy.evaluationFindingUpdate = false;
      }
    },

    async createProposalFromSelectedEvaluationFindings() {
      const findings = this.selectedEvaluationFindings();
      if (!findings.length) {
        this.showNotice("error", "请先选择 findings。");
        return;
      }
      this.busy.evaluationFindingUpdate = true;
      try {
        const payload = this.evaluationFindingProposalPayload(findings);
        const proposal = await this.apiRequest("/governance/proposals", {
          method: "POST",
          body: JSON.stringify(payload)
        });
        for (const finding of findings) {
          this.applyEvaluationFindingUpdate({ ...finding, status: "converted_to_proposal" });
        }
        this.clearEvaluationFindingSelection();
        this.showNotice("success", "已从选中 findings 创建治理提案。");
        await this.navigate(this.governanceProposalPath(proposal.id));
      } catch (error) {
        this.showNotice("error", error.message || "从选中 findings 创建治理提案失败。");
      } finally {
        this.busy.evaluationFindingUpdate = false;
      }
    },

    evaluationFindingProposalPayload(findings) {
      const selected = Array.isArray(findings) ? findings : [];
      const findingIds = this.evaluationFindingUniqueValues(selected, "id");
      const runIds = this.evaluationFindingUniqueValues(selected, "run_id");
      const evaluationIds = this.evaluationFindingUniqueValues(selected, "evaluation_id");
      const pskillDefinitionIds = this.evaluationFindingUniqueValues(selected, "pskill_definition_id");
      const categories = this.evaluationFindingUniqueValues(selected, "category");
      const severities = this.evaluationFindingUniqueValues(selected, "severity");
      const proposalType = this.proposalTypeForEvaluationFindings(selected);
      return {
        proposal_type: proposalType,
        target: {
          kind: "run_evaluation_findings",
          finding_ids: findingIds,
          evaluation_ids: evaluationIds,
          run_ids: runIds,
          pskill_definition_ids: pskillDefinitionIds,
          categories
        },
        problem_statement: this.evaluationFindingProblemStatement(selected),
        evidence_refs: this.evaluationFindingFlattenEvidenceRefs(selected),
        proposed_changes: [
          ...selected.map((finding) => ({
            kind: "recommended_action",
            description: finding.recommended_action || finding.description || "复核 finding 并制定修复措施。",
            source_finding_id: finding.id
          })),
          {
            kind: "governance_boundary",
            description: "仅生成提案和验证计划，不直接修改 Runtime Kernel、发布版本或工具权限。",
            direct_activation_allowed: false
          }
        ],
        risk_assessment: {
          risk_level: this.riskLevelForEvaluationFindings(selected),
          finding_count: selected.length,
          severities,
          requires_human_review: true,
          requires_rollback_plan: true
        },
        required_tests: [
          {
            kind: "regression",
            scope: proposalType,
            description: "覆盖选中 findings 的回归验证。"
          },
          {
            kind: "replay",
            run_ids: runIds,
            description: "使用 Replay / OTel 证据链复核变更前后的运行行为。"
          }
        ],
        source_finding_ids: findingIds,
        source_evaluation_id: evaluationIds.length === 1 ? evaluationIds[0] : null,
        source_run_id: runIds.length === 1 ? runIds[0] : null
      };
    },

    evaluationFindingUniqueValues(findings, key) {
      const values = [];
      for (const finding of findings || []) {
        const value = String(finding?.[key] || "").trim();
        if (value && !values.includes(value)) {
          values.push(value);
        }
      }
      return values;
    },

    proposalTypeForEvaluationFindings(findings) {
      const proposalTypes = this.evaluationFindingUniqueValues(findings, "category")
        .map((category) => FINDING_CATEGORY_PROPOSAL_TYPES[category] || "pskill_template_update");
      const uniqueTypes = Array.from(new Set(proposalTypes));
      return uniqueTypes.length === 1 ? uniqueTypes[0] : "pskill_template_update";
    },

    riskLevelForEvaluationFindings(findings) {
      let topSeverity = "medium";
      for (const finding of findings || []) {
        const severity = String(finding?.severity || "").toLowerCase();
        if ((FINDING_SEVERITY_RANK[severity] || 0) > (FINDING_SEVERITY_RANK[topSeverity] || 0)) {
          topSeverity = severity;
        }
      }
      return FINDING_SEVERITY_RISK[topSeverity] || "medium";
    },

    evaluationFindingProblemStatement(findings) {
      const selected = Array.isArray(findings) ? findings : [];
      const descriptions = selected
        .map((finding) => String(finding?.description || "").trim())
        .filter(Boolean);
      if (selected.length === 1) {
        return descriptions[0] || `处理 RunEvaluationFinding ${selected[0]?.id || ""}`.trim();
      }
      const summary = descriptions.slice(0, 3).join("；");
      const suffix = descriptions.length > 3 ? `；另有 ${descriptions.length - 3} 个 finding。` : "";
      return `基于 ${selected.length} 个 RunEvaluationFinding 生成治理提案：${summary || "复核选中 findings 并制定改进措施。"}${suffix}`;
    },

    evaluationFindingFlattenEvidenceRefs(findings) {
      const refs = [];
      const seen = new Set();
      for (const finding of findings || []) {
        for (const ref of finding?.evidence_refs || []) {
          const normalized = { ...ref, source_finding_id: finding.id };
          if (!normalized.source_evaluation_id && finding?.evaluation_id) {
            normalized.source_evaluation_id = finding.evaluation_id;
          }
          if (!normalized.source_run_id && finding?.run_id) {
            normalized.source_run_id = finding.run_id;
          }
          const key = JSON.stringify([
            normalized.source_finding_id || "",
            normalized.kind || "",
            normalized.id || normalized.source_id || normalized.run_trace_id || normalized.run_event_id || "",
            normalized.seq_no ?? ""
          ]);
          if (!seen.has(key)) {
            seen.add(key);
            refs.push(normalized);
          }
        }
      }
      return refs;
    },

    evaluationFindingSummary(findings = this.evaluationFindings || []) {
      const items = Array.isArray(findings) ? findings : [];
      const total = items.length;
      const qualityScores = items
        .map((finding) => Number(finding?.quality_score))
        .filter((score) => Number.isFinite(score));
      const evidenceQualityCount = items.filter((finding) => finding.category === "evidence_quality_issue").length;
      const unresolvedCount = items.filter((finding) => !["resolved", "dismissed"].includes(finding.status)).length;
      const highSeverityCount = items.filter((finding) => ["high", "critical"].includes(finding.severity)).length;
      return {
        total,
        open_count: items.filter((finding) => finding.status === "open").length,
        unresolved_count: unresolvedCount,
        resolved_count: items.filter((finding) => finding.status === "resolved").length,
        dismissed_count: items.filter((finding) => finding.status === "dismissed").length,
        high_severity_count: highSeverityCount,
        evidence_quality_count: evidenceQualityCount,
        evidence_insufficiency_rate: total ? Math.round((evidenceQualityCount / total) * 100) : 0,
        avg_quality_score: qualityScores.length
          ? Math.round(qualityScores.reduce((sum, score) => sum + score, 0) / qualityScores.length)
          : null,
        run_count: this.evaluationFindingUniqueValues(items, "run_id").length,
        pskill_count: this.evaluationFindingUniqueValues(items, "pskill_definition_id").length
      };
    },

    evaluationFindingDateKey(finding) {
      const value = String(finding?.evaluation_created_at || finding?.created_at || "").trim();
      return /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : "unknown";
    },

    evaluationFindingTrendBuckets(findings = this.evaluationFindings || []) {
      const buckets = new Map();
      for (const finding of findings || []) {
        const key = this.evaluationFindingDateKey(finding);
        if (!buckets.has(key)) {
          buckets.set(key, {
            date: key,
            count: 0,
            evidence_quality_count: 0,
            quality_scores: []
          });
        }
        const bucket = buckets.get(key);
        bucket.count += 1;
        if (finding.category === "evidence_quality_issue") {
          bucket.evidence_quality_count += 1;
        }
        const qualityScore = Number(finding?.quality_score);
        if (Number.isFinite(qualityScore)) {
          bucket.quality_scores.push(qualityScore);
        }
      }
      return Array.from(buckets.values())
        .sort((left, right) => left.date.localeCompare(right.date))
        .slice(-8)
        .map((bucket) => ({
          ...bucket,
          avg_quality_score: bucket.quality_scores.length
            ? Math.round(bucket.quality_scores.reduce((sum, score) => sum + score, 0) / bucket.quality_scores.length)
            : null,
          evidence_insufficiency_rate: bucket.count
            ? Math.round((bucket.evidence_quality_count / bucket.count) * 100)
            : 0
        }));
    },

    evaluationFindingTrendDateLabel(date) {
      const value = String(date || "");
      return value === "unknown" ? "未知" : value.slice(5);
    },

    evaluationFindingTrendCountWidth(bucket) {
      const maxCount = Math.max(1, ...this.evaluationFindingTrendBuckets().map((item) => item.count));
      const count = Number(bucket?.count || 0);
      return count ? `${Math.max(10, Math.round((count / maxCount) * 100))}%` : "0%";
    },

    evaluationFindingTrendEvidenceWidth(bucket) {
      const rate = Number(bucket?.evidence_insufficiency_rate || 0);
      return `${Math.max(0, Math.min(100, Math.round(rate)))}%`;
    },

    evaluationFindingPercentLabel(value) {
      const number = Number(value);
      return Number.isFinite(number) ? `${Math.round(number)}%` : "N/A";
    },

    evaluationReportPath(evaluationId) {
      return `/admin/evaluations/${evaluationId}`;
    },

    evaluationFindingsPath(filters = {}) {
      if (typeof buildEvaluationFindingsPath === "function") {
        return buildEvaluationFindingsPath(filters);
      }
      const params = new URLSearchParams();
      this.appendEvaluationFilterParam(params, "status", filters?.status);
      this.appendEvaluationFilterParam(params, "category", filters?.category);
      this.appendEvaluationFilterParam(params, "severity", filters?.severity);
      this.appendEvaluationFilterParam(params, "run_id", filters?.run_id);
      this.appendEvaluationFilterParam(params, "pskill_definition_id", filters?.pskill_definition_id);
      const query = params.toString();
      return query ? `/admin/evaluations/findings?${query}` : "/admin/evaluations/findings";
    },

    evaluationOutcomeOptions() {
      return EVALUATION_OUTCOME_OPTIONS;
    },

    evaluationRunReplayPath(evaluation, focus = {}) {
      if (!evaluation?.run_id) {
        return "/admin/evaluations";
      }
      if (typeof buildReplayPath === "function") {
        return buildReplayPath(evaluation.run_id, focus);
      }
      const params = new URLSearchParams();
      for (const key of ["event_id", "trace_id", "seq_no", "snapshot_seq"]) {
        const value = String(focus?.[key] || "").trim();
        if (value) {
          params.set(key, value);
        }
      }
      const query = params.toString();
      return query ? `/admin/runs/${evaluation.run_id}/live/replay?${query}` : `/admin/runs/${evaluation.run_id}/live/replay`;
    },

    findingRunReplayPath(finding, ref = null, evaluation = this.currentEvaluation) {
      const runId = String(ref?.run_id || ref?.source_run_id || finding?.run_id || evaluation?.run_id || "").trim();
      if (!runId) {
        return "";
      }
      return this.evaluationRunReplayPath({ run_id: runId }, this.findingEvidenceReplayFocus(ref));
    },

    evaluationNormalizeEvidenceKind(kind) {
      const value = String(kind || "").trim().toLowerCase();
      return value;
    },

    findingEvidenceReplayFocus(ref) {
      if (!ref || typeof ref !== "object") {
        return {};
      }
      const kind = this.evaluationNormalizeEvidenceKind(ref.kind || ref.source_kind);
      const traceId = String(
        ref.trace_id ||
        ref.run_trace_id ||
        ref.source_trace_id ||
        ref.source_run_trace_id ||
        ""
      ).trim();
      if (traceId) {
        return { trace_id: traceId };
      }
      const eventId = String(
        ref.run_event_id ||
        ref.event_id ||
        ref.source_event_id ||
        ref.source_run_event_id ||
        ""
      ).trim();
      if (eventId) {
        return { event_id: eventId };
      }
      const id = String(ref.id || ref.source_id || "").trim();
      if (id && kind === "run_trace") {
        return { trace_id: id };
      }
      if (id && kind === "run_event") {
        return { event_id: id };
      }
      const seqNo = String(ref.seq_no ?? "").trim();
      if (["session_token_snapshot", "snapshot"].includes(kind)) {
        const snapshotSeq = String(ref.snapshot_seq || ref.session_token_seq || seqNo).trim();
        return snapshotSeq ? { snapshot_seq: snapshotSeq } : {};
      }
      return seqNo ? { seq_no: seqNo } : {};
    },

    canOpenFindingEvidenceReplay(finding, evaluation = this.currentEvaluation) {
      return Boolean(finding?.run_id || evaluation?.run_id);
    },

    openFindingEvidenceReplay(finding, ref = null, evaluation = this.currentEvaluation) {
      const path = this.findingRunReplayPath(finding, ref, evaluation);
      if (!path) {
        this.showNotice?.("error", "Finding 缺少 Run 关联，无法打开 Replay。");
        return;
      }
      return this.navigate(path);
    },

    findingEvidencePath(finding, ref = null, evaluation = this.currentEvaluation) {
      if (!ref || typeof ref !== "object") {
        return this.findingRunReplayPath(finding, ref, evaluation);
      }
      const kind = this.evaluationNormalizeEvidenceKind(ref.kind || ref.source_kind).replace(/-/g, "_");
      const id = this.findingEvidenceRefId(ref);
      if (["run_trace", "run_event", "run", "session_token_snapshot", "snapshot"].includes(kind)) {
        return this.findingRunReplayPath(finding, ref, evaluation);
      }
      if (["agent_run", "agentrun"].includes(kind)) {
        const agentRunId = String(ref.agent_run_id || id).trim();
        return agentRunId && typeof buildPlatformAgentRunPath === "function"
          ? buildPlatformAgentRunPath(agentRunId, { tab: "events" })
          : "";
      }
      if (["agent_event", "agent_run_event"].includes(kind)) {
        const agentRunId = this.findingEvidenceAgentRunId(finding, ref, evaluation);
        return agentRunId && id && typeof buildPlatformAgentRunPath === "function"
          ? buildPlatformAgentRunPath(agentRunId, { tab: "events", event_id: id })
          : "";
      }
      if (["agent_model_call", "model_call"].includes(kind)) {
        const agentRunId = this.findingEvidenceAgentRunId(finding, ref, evaluation);
        return agentRunId && id && typeof buildPlatformAgentRunPath === "function"
          ? buildPlatformAgentRunPath(agentRunId, { tab: "model", model_call_id: id })
          : "";
      }
      if (["agent_tool_call", "tool_call"].includes(kind)) {
        const agentRunId = this.findingEvidenceAgentRunId(finding, ref, evaluation);
        return agentRunId && id && typeof buildPlatformAgentRunPath === "function"
          ? buildPlatformAgentRunPath(agentRunId, { tab: "tools", tool_call_id: id })
          : "";
      }
      if (["agent_tool_authorization", "tool_authorization"].includes(kind)) {
        const authorizationId = String(ref.authorization_id || ref.tool_authorization_id || id).trim();
        const agentRunId = this.findingEvidenceAgentRunId(finding, ref, evaluation);
        if (agentRunId && authorizationId && typeof buildPlatformAgentRunPath === "function") {
          return buildPlatformAgentRunPath(agentRunId, { tab: "authorizations", authorization_id: authorizationId });
        }
        return typeof buildToolAuthorizationsPath === "function"
          ? buildToolAuthorizationsPath(this.findingToolAuthorizationFilters(finding, ref, evaluation))
          : "";
      }
      if (["run_evaluation", "evaluation"].includes(kind)) {
        const evaluationId = String(ref.evaluation_id || ref.source_evaluation_id || id).trim();
        return evaluationId ? this.evaluationReportPath(evaluationId) : "";
      }
      if (["run_evaluation_finding", "evaluation_finding", "finding"].includes(kind)) {
        const evaluationId = String(ref.evaluation_id || ref.source_evaluation_id || finding?.evaluation_id || evaluation?.id || "").trim();
        return evaluationId ? this.evaluationReportPath(evaluationId) : this.evaluationFindingsPath({
          run_id: ref.run_id || ref.source_run_id || finding?.run_id || evaluation?.run_id || "",
          status: ref.status || "",
          category: ref.category || finding?.category || "",
          severity: ref.severity || finding?.severity || "",
          pskill_definition_id: ref.pskill_definition_id || finding?.pskill_definition_id || ""
        });
      }
      if (["psop_improvement_proposal", "governance_proposal", "proposal"].includes(kind)) {
        const proposalId = String(ref.proposal_id || id).trim();
        return proposalId && typeof buildGovernanceProposalPath === "function"
          ? buildGovernanceProposalPath(proposalId)
          : "";
      }
      if (["psop_improvement_experiment", "governance_experiment", "experiment"].includes(kind)) {
        const experimentId = String(ref.experiment_id || id).trim();
        return experimentId && typeof buildGovernanceExperimentsPath === "function"
          ? buildGovernanceExperimentsPath({ experiment_id: experimentId })
          : "";
      }
      if (["agent_memory_entry", "memory_entry", "memory"].includes(kind)) {
        const memoryId = String(ref.memory_entry_id || ref.memory_id || id).trim();
        return memoryId && typeof buildPlatformMemoryEntryPath === "function"
          ? buildPlatformMemoryEntryPath(memoryId)
          : "";
      }
      return "";
    },

    findingEvidenceRefId(ref) {
      return String(
        ref?.id ||
        ref?.source_id ||
        ref?.run_trace_id ||
        ref?.trace_id ||
        ref?.source_run_trace_id ||
        ref?.source_trace_id ||
        ref?.run_event_id ||
        ref?.event_id ||
        ref?.source_run_event_id ||
        ref?.source_event_id ||
        ref?.agent_event_id ||
        ref?.model_call_id ||
        ref?.agent_model_call_id ||
        ref?.tool_call_id ||
        ref?.agent_tool_call_id ||
        ref?.authorization_id ||
        ref?.tool_authorization_id ||
        ref?.evaluation_id ||
        ref?.source_evaluation_id ||
        ref?.finding_id ||
        ref?.source_finding_id ||
        ref?.proposal_id ||
        ref?.experiment_id ||
        ref?.memory_entry_id ||
        ref?.memory_id ||
        ref?.run_id ||
        ref?.source_run_id ||
        ref?.agent_run_id ||
        ""
      ).trim();
    },

    findingEvidenceAgentRunId(finding, ref = {}, evaluation = this.currentEvaluation) {
      return String(
        ref?.agent_run_id ||
        ref?.owner_agent_run_id ||
        finding?.agent_run_id ||
        evaluation?.agent_run_id ||
        ""
      ).trim();
    },

    findingToolAuthorizationFilters(finding, ref = {}, evaluation = this.currentEvaluation) {
      const filters = {
        status: ref?.status || "",
        tool_name: ref?.tool_name || "",
        agent_run_id: ref?.agent_run_id || ref?.owner_agent_run_id || "",
        run_id: ref?.authorization_run_id || ref?.tool_authorization_run_id || "",
        agent_key: ref?.agent_key || "",
        proposal_id: ref?.proposal_id || ref?.governance_proposal_id || "",
        source_run_id: ref?.source_run_id || ref?.run_id || finding?.run_id || evaluation?.run_id || "",
        source_evaluation_id: ref?.source_evaluation_id || ref?.evaluation_id || finding?.evaluation_id || evaluation?.id || "",
        source_finding_id: ref?.source_finding_id || ref?.finding_id || finding?.id || ""
      };
      return Object.fromEntries(
        Object.entries(filters)
          .map(([key, value]) => [key, String(value || "").trim()])
          .filter(([, value]) => value)
      );
    },

    canOpenFindingEvidence(finding, ref = null, evaluation = this.currentEvaluation) {
      return Boolean(this.findingEvidencePath(finding, ref, evaluation));
    },

    openFindingEvidence(finding, ref = null, evaluation = this.currentEvaluation) {
      const path = this.findingEvidencePath(finding, ref, evaluation);
      if (!path) {
        this.showNotice?.("error", "Finding evidence 缺少可跳转上下文。");
        return;
      }
      return this.navigate(path);
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
      const kind = this.evaluationNormalizeEvidenceKind(ref.kind || ref.source_kind) || "evidence";
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
