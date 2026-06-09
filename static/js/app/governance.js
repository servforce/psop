(function () {
  const {
    buildSkillDetailPath,
    buildEvaluationReportPath,
    buildEvaluationFindingsPath,
    buildGovernanceProposalsPath,
    buildGovernanceProposalPath,
    buildGovernanceExperimentsPath,
    buildToolAuthorizationsPath,
    buildPlatformAgentPath,
    buildPlatformAgentRunPath,
    buildPlatformSkillPath,
    buildPlatformMemoryEntryPath,
    buildReplayPath,
    buildReplayTracePath,
    resolveWsUrl
  } = window.PSOPConsoleHelpers;

  const PROPOSAL_TYPE_OPTIONS = [
    { value: "agent_skill_update", label: "Agent Skill" },
    { value: "agent_spec_update", label: "Agent Spec" },
    { value: "tool_policy_update", label: "Tool Policy" },
    { value: "validator_update", label: "Validator" },
    { value: "test_suite_update", label: "Test Suite" },
    { value: "pskill_template_update", label: "PSkill Template" }
  ];

  const PROPOSAL_STATUS_OPTIONS = [
    { value: "draft", label: "草稿" },
    { value: "testing", label: "测试中" },
    { value: "reviewing", label: "Review 中" },
    { value: "approved", label: "已批准" },
    { value: "rejected", label: "已拒绝" },
    { value: "canary", label: "灰度中" },
    { value: "activated", label: "已激活" },
    { value: "rolled_back", label: "已回滚" }
  ];

  const TOOL_AUTH_STATUS_OPTIONS = [
    { value: "pending", label: "待处理" },
    { value: "approved", label: "已批准" },
    { value: "rejected", label: "已拒绝" },
    { value: "expired", label: "已过期" },
    { value: "cancelled", label: "已取消" },
    { value: "executed", label: "已执行" }
  ];

  const TOOL_AUTH_FILTER_KEYS = [
    "status",
    "tool_name",
    "agent_run_id",
    "run_id",
    "agent_key",
    "proposal_id",
    "source_run_id",
    "source_evaluation_id",
    "source_finding_id"
  ];

  const EXPERIMENT_STATUS_OPTIONS = [
    { value: "planned", label: "已计划" },
    { value: "running", label: "运行中" },
    { value: "succeeded", label: "成功" },
    { value: "failed", label: "失败" },
    { value: "rolled_back", label: "已回滚" }
  ];

  const EXPERIMENT_TYPE_OPTIONS = [
    { value: "regression", label: "Regression" },
    { value: "canary", label: "Canary" },
    { value: "rollback", label: "Rollback" }
  ];

  window.PSOPConsoleGovernanceMethods = {
    async loadGovernanceProposalsPage() {
      this.syncGovernanceProposalFiltersFromLocation();
      await this.loadGovernanceProposals();
      if (!this.currentGovernanceProposal && this.governanceProposals.length) {
        this.applyGovernanceProposalActivitySnapshot({ proposal: this.governanceProposals[0] });
        this.connectGovernanceProposalActivityWebSocket(this.governanceProposals[0].id);
      }
    },

    async loadGovernanceProposals() {
      this.busy.governanceProposals = true;
      try {
        const query = this.governanceProposalQueryString();
        const suffix = query ? `?${query}` : "";
        const proposals = await this.apiRequest(`/governance/proposals${suffix}`);
        this.governanceProposals = Array.isArray(proposals) ? proposals : [];
        if (this.currentGovernanceProposal) {
          const refreshed = this.governanceProposals.find((item) => item.id === this.currentGovernanceProposal.id);
          if (refreshed) {
            this.currentGovernanceProposal = refreshed;
          }
        }
      } catch (error) {
        this.showNotice("error", error.message || "治理提案加载失败。");
      } finally {
        this.busy.governanceProposals = false;
      }
    },

    async loadGovernanceProposalDetail(proposalId) {
      const id = String(proposalId || "").trim();
      if (!id) {
        return;
      }
      this.busy.governanceProposals = true;
      try {
        const proposal = await this.apiRequest(`/governance/proposals/${encodeURIComponent(id)}`);
        this.applyGovernanceProposalActivitySnapshot({ proposal });
        this.connectGovernanceProposalActivityWebSocket(proposal.id);
      } catch (error) {
        this.showNotice("error", error.message || "治理提案详情加载失败。");
      } finally {
        this.busy.governanceProposals = false;
      }
    },

    connectGovernanceProposalActivityWebSocket(proposalId = this.currentGovernanceProposal?.id) {
      const id = String(proposalId || "").trim();
      if (!id || typeof WebSocket === "undefined" || typeof resolveWsUrl !== "function") {
        return false;
      }
      if (
        this.governanceProposalActivityWs &&
        this.governanceProposalActivityWsId === id &&
        [WebSocket.CONNECTING, WebSocket.OPEN].includes(this.governanceProposalActivityWs.readyState)
      ) {
        return true;
      }

      this.disconnectGovernanceProposalActivityWebSocket();
      const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, `/ws/governance/proposals/${encodeURIComponent(id)}`));
      this.governanceProposalActivityWs = socket;
      this.governanceProposalActivityWsId = id;
      this.governanceProposalActivityWsStatus = "connecting";
      socket.addEventListener("open", () => {
        if (this.governanceProposalActivityWs === socket) {
          this.governanceProposalActivityWsStatus = "open";
        }
      });
      socket.addEventListener("message", (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.event_type === "governance_proposal.activity.snapshot" && message.payload) {
            this.applyGovernanceProposalActivitySnapshot(message.payload);
          }
          if (message.event_type === "governance_proposal.activity.error") {
            this.showNotice("error", message.payload?.message || "获取 Governance Proposal 活动流失败。");
            this.disconnectGovernanceProposalActivityWebSocket();
          }
        } catch {
          // Ignore malformed activity messages; REST actions remain the recovery path.
        }
      });
      socket.addEventListener("close", () => {
        if (this.governanceProposalActivityWs === socket) {
          this.governanceProposalActivityWsStatus = "closed";
        }
      });
      socket.addEventListener("error", () => {
        if (this.governanceProposalActivityWs === socket) {
          this.governanceProposalActivityWsStatus = "error";
        }
      });
      return true;
    },

    disconnectGovernanceProposalActivityWebSocket() {
      if (this.governanceProposalActivityWs) {
        this.governanceProposalActivityWs.close();
      }
      this.governanceProposalActivityWs = null;
      this.governanceProposalActivityWsId = "";
      this.governanceProposalActivityWsStatus = "idle";
    },

    applyGovernanceProposalActivitySnapshot(snapshot) {
      if (!snapshot || typeof snapshot !== "object") {
        return;
      }
      if (snapshot.proposal?.id) {
        if (this.currentGovernanceProposal?.id && this.currentGovernanceProposal.id !== snapshot.proposal.id) {
          this.closeGovernanceProposalEdit();
        }
        this.currentGovernanceProposal = snapshot.proposal;
        this.replaceGovernanceProposal(snapshot.proposal);
      }
      if (snapshot.agent_run) {
        this.governanceProposalAgentRun = snapshot.agent_run;
      }
      if (Array.isArray(snapshot.agent_events)) {
        this.governanceProposalAgentEvents = snapshot.agent_events;
      }
      if (Array.isArray(snapshot.model_calls)) {
        this.governanceProposalModelCalls = snapshot.model_calls;
      }
      if (Array.isArray(snapshot.tool_calls)) {
        this.governanceProposalToolCalls = snapshot.tool_calls;
      }
      if (Array.isArray(snapshot.skill_activations)) {
        this.governanceProposalSkillActivations = snapshot.skill_activations;
      }
      if (Array.isArray(snapshot.tool_authorizations)) {
        this.governanceProposalToolAuthorizations = snapshot.tool_authorizations;
      }
      if (Array.isArray(snapshot.memory_entries)) {
        this.governanceProposalMemoryEntries = snapshot.memory_entries;
      }
    },

    governanceProposalQueryString() {
      const params = new URLSearchParams();
      const status = String(this.governanceProposalFilters.status || "").trim();
      if (status) {
        params.set("status", status);
      }
      return params.toString();
    },

    applyGovernanceProposalFilters() {
      this.replaceGovernanceProposalFilterLocation();
      return this.loadGovernanceProposalsPage();
    },

    resetGovernanceProposalFilters() {
      this.governanceProposalFilters = this.emptyGovernanceProposalFilters();
      this.replaceGovernanceProposalFilterLocation();
      return this.loadGovernanceProposalsPage();
    },

    emptyGovernanceProposalFilters() {
      return { status: "" };
    },

    syncGovernanceProposalFiltersFromLocation() {
      const search = this.governanceProposalLocationSearch();
      if (search === (this.governanceProposalFiltersLocationSearch || "")) {
        return;
      }
      if (!search) {
        if (this.governanceProposalFiltersLocationSearch) {
          this.governanceProposalFilters = this.emptyGovernanceProposalFilters();
        }
        this.governanceProposalFiltersLocationSearch = "";
        return;
      }
      const params = new URLSearchParams(search);
      this.governanceProposalFilters = {
        ...this.emptyGovernanceProposalFilters(),
        status: params.get("status") || ""
      };
      this.governanceProposalFiltersLocationSearch = search;
    },

    replaceGovernanceProposalFilterLocation() {
      if (typeof window === "undefined" || !window.history?.replaceState) {
        return;
      }
      const path = this.governanceProposalsPath(this.governanceProposalFilters);
      window.history.replaceState({}, "", path);
      this.governanceProposalFiltersLocationSearch = this.governanceProposalLocationSearch();
    },

    governanceProposalLocationSearch() {
      if (typeof window === "undefined") {
        return "";
      }
      return window.location?.search || "";
    },

    async createGovernanceProposal() {
      const problemStatement = String(this.governanceProposalForm.problem_statement || "").trim();
      if (!problemStatement) {
        this.showNotice("error", "请输入 problem statement。");
        return;
      }

      let target;
      try {
        target = JSON.parse(this.governanceProposalForm.target_json || "{}");
      } catch (error) {
        this.showNotice("error", "target JSON 格式无效。");
        return;
      }

      this.busy.governanceProposalCreate = true;
      try {
        const proposal = await this.apiRequest("/governance/proposals", {
          method: "POST",
          body: JSON.stringify({
            proposal_type: this.governanceProposalForm.proposal_type,
            problem_statement: problemStatement,
            target
          })
        });
        this.currentGovernanceProposal = proposal;
        this.governanceProposalForm.problem_statement = "";
        await this.loadGovernanceProposals();
        await this.navigate(this.governanceProposalPath(proposal.id));
      } catch (error) {
        this.showNotice("error", error.message || "治理提案创建失败。");
      } finally {
        this.busy.governanceProposalCreate = false;
      }
    },

    openGovernanceProposalEdit(proposal = this.currentGovernanceProposal) {
      if (!proposal?.id || !this.governanceCanEditProposal(proposal)) {
        return;
      }
      this.governanceProposalEditOpen = true;
      this.governanceProposalEditForm = {
        proposal_type: proposal.proposal_type || "pskill_template_update",
        problem_statement: proposal.problem_statement || "",
        target_json: this.governanceJsonPreview(proposal.target || {}),
        evidence_refs_json: this.governanceJsonPreview(proposal.evidence_refs || []),
        proposed_changes_json: this.governanceJsonPreview(proposal.proposed_changes || []),
        risk_assessment_json: this.governanceJsonPreview(proposal.risk_assessment || {}),
        required_tests_json: this.governanceJsonPreview(proposal.required_tests || []),
        activation_plan_json: this.governanceJsonPreview(proposal.activation_plan || {})
      };
    },

    closeGovernanceProposalEdit() {
      this.governanceProposalEditOpen = false;
    },

    governanceCanEditProposal(proposal) {
      return ["draft", "rejected"].includes(String(proposal?.status || ""));
    },

    async saveGovernanceProposalEdit(proposal = this.currentGovernanceProposal) {
      if (!proposal?.id) {
        return;
      }
      const problemStatement = String(this.governanceProposalEditForm.problem_statement || "").trim();
      if (!problemStatement) {
        this.showNotice("error", "请输入 problem statement。");
        return;
      }
      const payload = this.buildGovernanceProposalEditPayload(problemStatement);
      if (!payload) {
        return;
      }
      this.busy.governanceProposalSave = true;
      try {
        const updated = await this.apiRequest(`/governance/proposals/${encodeURIComponent(proposal.id)}`, {
          method: "PATCH",
          body: JSON.stringify(payload)
        });
        this.replaceGovernanceProposal(updated);
        this.currentGovernanceProposal = updated;
        this.closeGovernanceProposalEdit();
        this.showNotice("success", "治理提案已保存。");
      } catch (error) {
        this.showNotice("error", error.message || "治理提案保存失败。");
      } finally {
        this.busy.governanceProposalSave = false;
      }
    },

    buildGovernanceProposalEditPayload(problemStatement) {
      const parsed = {
        target: this.parseGovernanceProposalJsonField("target_json", "Target JSON"),
        evidence_refs: this.parseGovernanceProposalJsonField("evidence_refs_json", "Evidence refs"),
        proposed_changes: this.parseGovernanceProposalJsonField("proposed_changes_json", "Proposed changes"),
        risk_assessment: this.parseGovernanceProposalJsonField("risk_assessment_json", "Risk assessment"),
        required_tests: this.parseGovernanceProposalJsonField("required_tests_json", "Required tests"),
        activation_plan: this.parseGovernanceProposalJsonField("activation_plan_json", "Activation plan")
      };
      if (Object.values(parsed).some((value) => value === undefined)) {
        return null;
      }
      return {
        proposal_type: this.governanceProposalEditForm.proposal_type,
        problem_statement: problemStatement,
        ...parsed
      };
    },

    parseGovernanceProposalJsonField(fieldName, label) {
      try {
        return JSON.parse(this.governanceProposalEditForm[fieldName] || "null");
      } catch (error) {
        this.showNotice("error", `${label} 格式无效。`);
        return undefined;
      }
    },

    async runGovernanceProposalTests(proposal) {
      if (!proposal?.id) {
        return;
      }
      await this.performGovernanceProposalAction(
        proposal,
        "run-tests",
        { method: "POST" },
        "回归测试已运行。"
      );
    },

    async submitGovernanceProposalReview(proposal, decision = "") {
      if (!proposal?.id) {
        return;
      }
      const resolvedDecision = decision || this.governanceReviewForm.decision || "";
      await this.performGovernanceProposalAction(
        proposal,
        "submit-review",
        {
          method: "POST",
          body: JSON.stringify({
            decision: resolvedDecision || null,
            review_notes: this.governanceReviewForm.review_notes || ""
          })
        },
        resolvedDecision === "rejected" ? "治理提案已拒绝。" : "治理提案 review 已提交。"
      );
    },

    async activateGovernanceProposalCanary(proposal) {
      if (!proposal?.id) {
        return;
      }
      await this.performGovernanceProposalAction(
        proposal,
        "activate-canary",
        { method: "POST" },
        "灰度已激活。"
      );
    },

    async rollbackGovernanceProposal(proposal) {
      if (!proposal?.id) {
        return;
      }
      await this.performGovernanceProposalAction(
        proposal,
        "rollback",
        { method: "POST" },
        "治理提案已回滚。"
      );
    },

    async performGovernanceProposalAction(proposal, action, options, successMessage) {
      this.busy.governanceProposalAction = true;
      try {
        const updated = await this.apiRequest(
          `/governance/proposals/${encodeURIComponent(proposal.id)}/${action}`,
          options
        );
        this.replaceGovernanceProposal(updated);
        this.currentGovernanceProposal = updated;
        this.showNotice("success", successMessage);
      } catch (error) {
        this.showNotice("error", error.message || "治理提案操作失败。");
      } finally {
        this.busy.governanceProposalAction = false;
      }
    },

    replaceGovernanceProposal(proposal) {
      if (!proposal?.id) {
        return;
      }
      const index = this.governanceProposals.findIndex((item) => item.id === proposal.id);
      if (index >= 0) {
        this.governanceProposals.splice(index, 1, proposal);
      } else {
        this.governanceProposals.unshift(proposal);
      }
      this.refreshGovernanceExperimentRows();
    },

    async loadGovernanceExperiments() {
      this.busy.governanceExperiments = true;
      try {
        const query = this.governanceExperimentQueryString();
        const suffix = query ? `?${query}` : "";
        const experiments = await this.apiRequest(`/governance/experiments${suffix}`);
        this.governanceExperimentRows = Array.isArray(experiments) ? experiments : [];
        if (this.governanceExperimentDetail) {
          const refreshed = this.governanceExperimentRows.find((item) => item.id === this.governanceExperimentDetail.id);
          if (refreshed) {
            this.governanceExperimentDetail = refreshed;
            await this.loadGovernanceExperimentProposal(refreshed.proposal_id, { silent: true });
          }
        }
      } catch (error) {
        this.showNotice("error", error.message || "治理实验加载失败。");
      } finally {
        this.busy.governanceExperiments = false;
      }
    },

    async loadGovernanceExperimentsPage() {
      this.syncGovernanceExperimentFiltersFromLocation();
      await this.loadGovernanceExperiments();
      if (this.governanceExperimentLookupId) {
        await this.openGovernanceExperiment({ replaceLocation: false });
      }
    },

    governanceExperimentQueryString() {
      const params = new URLSearchParams();
      const proposalId = String(this.governanceExperimentFilters?.proposal_id || "").trim();
      const status = String(this.governanceExperimentFilters?.status || "").trim();
      const experimentType = String(this.governanceExperimentFilters?.experiment_type || "").trim();
      if (proposalId) {
        params.set("proposal_id", proposalId);
      }
      if (status) {
        params.set("status", status);
      }
      if (experimentType) {
        params.set("experiment_type", experimentType);
      }
      return params.toString();
    },

    applyGovernanceExperimentFilters() {
      this.replaceGovernanceExperimentFilterLocation();
      return this.loadGovernanceExperiments();
    },

    resetGovernanceExperimentFilters() {
      this.governanceExperimentFilters = this.emptyGovernanceExperimentFilters();
      this.replaceGovernanceExperimentFilterLocation();
      return this.loadGovernanceExperiments();
    },

    emptyGovernanceExperimentFilters() {
      return { proposal_id: "", status: "", experiment_type: "" };
    },

    syncGovernanceExperimentFiltersFromLocation() {
      const search = this.governanceExperimentLocationSearch();
      if (search === (this.governanceExperimentFiltersLocationSearch || "")) {
        return;
      }
      if (!search) {
        if (this.governanceExperimentFiltersLocationSearch) {
          this.governanceExperimentFilters = this.emptyGovernanceExperimentFilters();
        }
        this.governanceExperimentLookupId = "";
        this.governanceExperimentFiltersLocationSearch = "";
        return;
      }
      const params = new URLSearchParams(search);
      this.governanceExperimentFilters = {
        ...this.emptyGovernanceExperimentFilters(),
        proposal_id: params.get("proposal_id") || "",
        status: params.get("status") || "",
        experiment_type: params.get("experiment_type") || ""
      };
      this.governanceExperimentLookupId = params.get("experiment_id") || "";
      this.governanceExperimentFiltersLocationSearch = search;
    },

    replaceGovernanceExperimentFilterLocation() {
      if (typeof window === "undefined" || !window.history?.replaceState) {
        return;
      }
      const path = this.governanceExperimentsPath(this.governanceExperimentFilters);
      window.history.replaceState({}, "", path);
      this.governanceExperimentFiltersLocationSearch = this.governanceExperimentLocationSearch();
    },

    governanceExperimentLocationSearch() {
      if (typeof window === "undefined") {
        return "";
      }
      return window.location?.search || "";
    },

    replaceGovernanceExperimentLookupLocation(experimentId) {
      const id = String(experimentId || "").trim();
      if (!id || typeof window === "undefined" || !window.history?.replaceState) {
        return;
      }
      const path = this.governanceExperimentsPath({ experiment_id: id });
      window.history.replaceState({}, "", path);
      this.governanceExperimentFiltersLocationSearch = this.governanceExperimentLocationSearch();
    },

    async openGovernanceExperiment(options = {}) {
      const experimentId = String(this.governanceExperimentLookupId || "").trim();
      if (!experimentId) {
        this.showNotice("error", "请输入 Experiment ID。");
        return;
      }
      this.busy.governanceExperimentLookup = true;
      try {
        this.governanceExperimentDetail = await this.apiRequest(`/governance/experiments/${encodeURIComponent(experimentId)}`);
        this.governanceExperimentLookupId = this.governanceExperimentDetail.id;
        if (options.replaceLocation !== false) {
          this.replaceGovernanceExperimentLookupLocation(this.governanceExperimentDetail.id);
        }
        await this.loadGovernanceExperimentProposal(this.governanceExperimentDetail.proposal_id, { silent: true });
      } catch (error) {
        this.showNotice("error", error.message || "治理实验详情加载失败。");
      } finally {
        this.busy.governanceExperimentLookup = false;
      }
    },

    async selectGovernanceExperiment(experiment) {
      if (!experiment?.id) {
        return;
      }
      this.governanceExperimentDetail = experiment;
      this.governanceExperimentLookupId = experiment.id;
      await this.loadGovernanceExperimentProposal(experiment.proposal_id, { silent: true });
    },

    async loadGovernanceExperimentProposal(proposalId, options = {}) {
      const id = String(proposalId || "").trim();
      if (!id) {
        this.governanceExperimentProposal = null;
        return null;
      }
      try {
        const proposal = await this.apiRequest(`/governance/proposals/${encodeURIComponent(id)}`);
        this.governanceExperimentProposal = proposal;
        this.replaceGovernanceProposal(proposal);
        return proposal;
      } catch (error) {
        if (!options.silent) {
          this.showNotice("error", error.message || "治理提案详情加载失败。");
        }
        return null;
      }
    },

    governanceExperimentProposalContext(experiment = this.governanceExperimentDetail) {
      if (!experiment?.proposal_id) {
        return null;
      }
      if (this.governanceExperimentProposal?.id === experiment.proposal_id) {
        return this.governanceExperimentProposal;
      }
      return {
        id: experiment.proposal_id,
        agent_run_id: experiment.agent_run_id || "",
        status: experiment.proposal_status || "",
        proposal_type: experiment.proposal_type || "",
        problem_statement: experiment.problem_statement || "",
        source_finding_ids: Array.isArray(experiment.source_finding_ids) ? experiment.source_finding_ids : [],
        source_findings: Array.isArray(experiment.source_findings) ? experiment.source_findings : [],
        source_evaluation_id: experiment.source_evaluation_id || "",
        source_run_id: experiment.source_run_id || "",
        evidence_refs: Array.isArray(experiment.evidence_refs) ? experiment.evidence_refs : [],
        experiments: []
      };
    },

    governanceExperimentSourceRunId(experiment = this.governanceExperimentDetail) {
      return String(
        experiment?.source_run_id ||
          this.governanceExperimentProposalContext(experiment)?.source_run_id ||
          ""
      ).trim();
    },

    governanceExperimentReplayPath(experiment = this.governanceExperimentDetail) {
      const runId = this.governanceExperimentSourceRunId(experiment);
      return runId ? buildReplayPath(runId) : "";
    },

    openGovernanceExperimentReplay(experiment = this.governanceExperimentDetail) {
      const path = this.governanceExperimentReplayPath(experiment);
      if (!path) {
        this.showNotice?.("error", "Experiment 缺少来源 Run，无法打开 Replay。");
        return;
      }
      return this.navigate(path);
    },

    governanceExperimentEvidenceLinks(experiment = this.governanceExperimentDetail) {
      const proposal = this.governanceExperimentProposalContext(experiment);
      const refs = [];
      if (experiment?.id) {
        refs.push({ kind: "psop_improvement_experiment", id: experiment.id });
      }
      if (proposal?.id) {
        refs.push({ kind: "psop_improvement_proposal", id: proposal.id });
      }
      if (proposal?.agent_run_id) {
        refs.push({ kind: "agent_run", id: proposal.agent_run_id });
      }
      if (proposal?.source_run_id || experiment?.source_run_id) {
        refs.push({ kind: "run_replay", run_id: proposal?.source_run_id || experiment.source_run_id });
      }
      if (proposal?.source_evaluation_id) {
        refs.push({ kind: "run_evaluation", id: proposal.source_evaluation_id });
      }
      for (const finding of this.governanceProposalSourceFindings(proposal)) {
        refs.push({
          kind: "run_evaluation_finding",
          id: finding.id,
          evaluation_id: finding.evaluation_id || proposal?.source_evaluation_id || "",
          run_id: finding.run_id || proposal?.source_run_id || "",
          status: finding.status || "",
          category: finding.category || "",
          severity: finding.severity || "",
          pskill_definition_id: finding.pskill_definition_id || ""
        });
      }
      refs.push(...(Array.isArray(proposal?.evidence_refs) ? proposal.evidence_refs : []));
      refs.push(...(Array.isArray(experiment?.evidence_refs) ? experiment.evidence_refs : []));
      refs.push(...(Array.isArray(experiment?.result?.evidence_refs) ? experiment.result.evidence_refs : []));
      const links = refs
        .map((ref) => this.governanceProposalEvidenceLink(proposal, ref))
        .filter(Boolean);
      return this.uniqueToolAuthorizationLinks(links);
    },

    async runTestsFromGovernanceExperiment(experiment = this.governanceExperimentDetail) {
      await this.performGovernanceExperimentProposalAction(experiment, "run-tests", "回归测试已运行。");
    },

    async activateCanaryFromGovernanceExperiment(experiment = this.governanceExperimentDetail) {
      await this.performGovernanceExperimentProposalAction(experiment, "activate-canary", "灰度已激活。");
    },

    async rollbackFromGovernanceExperiment(experiment = this.governanceExperimentDetail) {
      await this.performGovernanceExperimentProposalAction(experiment, "rollback", "治理提案已回滚。");
    },

    async performGovernanceExperimentProposalAction(experiment, action, successMessage) {
      const proposalId = String(experiment?.proposal_id || "").trim();
      if (!proposalId) {
        return;
      }
      this.busy.governanceProposalAction = true;
      try {
        const updated = await this.apiRequest(`/governance/proposals/${encodeURIComponent(proposalId)}/${action}`, {
          method: "POST"
        });
        this.governanceExperimentProposal = updated;
        this.replaceGovernanceProposal(updated);
        this.mergeGovernanceExperimentRowsFromProposal(updated);
        const latest = this.flattenGovernanceExperiments([updated])[0];
        if (latest) {
          this.governanceExperimentDetail = latest;
          this.governanceExperimentLookupId = latest.id;
        }
        this.showNotice("success", successMessage);
      } catch (error) {
        this.showNotice("error", error.message || "治理实验操作失败。");
      } finally {
        this.busy.governanceProposalAction = false;
      }
    },

    mergeGovernanceExperimentRowsFromProposal(proposal) {
      const rows = this.flattenGovernanceExperiments([proposal]);
      for (const row of rows) {
        const index = this.governanceExperimentRows.findIndex((item) => item.id === row.id);
        if (index >= 0) {
          this.governanceExperimentRows.splice(index, 1, row);
        } else {
          this.governanceExperimentRows.unshift(row);
        }
      }
      this.governanceExperimentRows.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    },

    refreshGovernanceExperimentRows() {
      this.governanceExperimentRows = this.flattenGovernanceExperiments(this.governanceProposals);
    },

    flattenGovernanceExperiments(proposals) {
      const rows = [];
      for (const proposal of proposals || []) {
        for (const experiment of proposal.experiments || []) {
          rows.push({
            ...experiment,
            proposal_id: experiment.proposal_id || proposal.id,
            proposal_status: proposal.status,
            proposal_type: proposal.proposal_type,
            problem_statement: proposal.problem_statement,
            source_run_id: proposal.source_run_id || experiment.source_run_id || ""
          });
        }
      }
      return rows.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    },

    async loadToolAuthorizationsPage() {
      this.syncToolAuthorizationFiltersFromLocation?.();
      await this.loadToolAuthorizations();
      this.connectToolAuthorizationWebSocket();
    },

    async loadToolAuthorizations() {
      this.busy.toolAuthorizations = true;
      try {
        const query = this.toolAuthorizationQueryString();
        const suffix = query ? `?${query}` : "";
        const authorizations = await this.apiRequest(`/tool-authorizations${suffix}`);
        this.toolAuthorizations = Array.isArray(authorizations) ? authorizations : [];
      } catch (error) {
        this.showNotice("error", error.message || "工具授权加载失败。");
      } finally {
        this.busy.toolAuthorizations = false;
      }
    },

    connectToolAuthorizationWebSocket() {
      if (typeof WebSocket === "undefined" || typeof resolveWsUrl !== "function") {
        return false;
      }
      if (
        this.toolAuthorizationWs &&
        [WebSocket.CONNECTING, WebSocket.OPEN].includes(this.toolAuthorizationWs.readyState)
      ) {
        return true;
      }

      this.disconnectToolAuthorizationWebSocket();
      const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, "/ws/tool-authorizations"));
      this.toolAuthorizationWs = socket;
      this.toolAuthorizationWsStatus = "connecting";
      socket.addEventListener("open", () => {
        if (this.toolAuthorizationWs === socket) {
          this.toolAuthorizationWsStatus = "open";
        }
      });
      socket.addEventListener("message", (event) => {
        try {
          const message = JSON.parse(event.data);
          if (String(message.event_type || "").startsWith("tool.authorization_") && message.payload) {
            this.applyToolAuthorizationRealtimeUpdate(message.payload);
          }
        } catch {
          // Ignore malformed authorization events; REST refresh remains available.
        }
      });
      socket.addEventListener("close", () => {
        if (this.toolAuthorizationWs === socket) {
          this.toolAuthorizationWsStatus = "closed";
        }
      });
      socket.addEventListener("error", () => {
        if (this.toolAuthorizationWs === socket) {
          this.toolAuthorizationWsStatus = "error";
        }
      });
      return true;
    },

    disconnectToolAuthorizationWebSocket() {
      if (this.toolAuthorizationWs) {
        this.toolAuthorizationWs.close();
      }
      this.toolAuthorizationWs = null;
      this.toolAuthorizationWsStatus = "idle";
    },

    applyToolAuthorizationRealtimeUpdate(authorization) {
      if (!authorization?.id) {
        return;
      }
      const index = this.toolAuthorizations.findIndex((item) => item.id === authorization.id);
      if (index >= 0 || this.toolAuthorizationMatchesFilters(authorization)) {
        this.replaceToolAuthorization(authorization);
      }
    },

    toolAuthorizationMatchesFilters(authorization) {
      const status = String(this.toolAuthorizationFilters?.status || "").trim();
      const toolName = String(this.toolAuthorizationFilters?.tool_name || "").trim();
      const agentRunId = String(this.toolAuthorizationFilters?.agent_run_id || "").trim();
      const runId = String(this.toolAuthorizationFilters?.run_id || "").trim();
      const agentKey = String(this.toolAuthorizationFilters?.agent_key || "").trim();
      const proposalId = String(this.toolAuthorizationFilters?.proposal_id || "").trim();
      const sourceRunId = String(this.toolAuthorizationFilters?.source_run_id || "").trim();
      const sourceEvaluationId = String(this.toolAuthorizationFilters?.source_evaluation_id || "").trim();
      const sourceFindingId = String(this.toolAuthorizationFilters?.source_finding_id || "").trim();
      if (status && authorization.status !== status) {
        return false;
      }
      if (toolName && authorization.tool_name !== toolName) {
        return false;
      }
      if (agentRunId && authorization.agent_run_id !== agentRunId) {
        return false;
      }
      if (runId && authorization.run_id !== runId) {
        return false;
      }
      if (agentKey && !this.toolAuthorizationContextHasValue(authorization, ["agent_key"], agentKey)) {
        return false;
      }
      if (proposalId && !this.toolAuthorizationContextHasValue(authorization, ["proposal_id", "governance_proposal_id"], proposalId)) {
        return false;
      }
      if (sourceRunId && !this.toolAuthorizationContextHasValue(authorization, ["source_run_id", "run_id"], sourceRunId, authorization.run_id)) {
        return false;
      }
      if (
        sourceEvaluationId &&
        !this.toolAuthorizationContextHasValue(
          authorization,
          ["source_evaluation_id", "evaluation_id", "evaluation_report_id", "run_evaluation_id"],
          sourceEvaluationId
        )
      ) {
        return false;
      }
      if (
        sourceFindingId &&
        !this.toolAuthorizationContextHasValue(
          authorization,
          ["source_finding_id", "source_finding_ids", "finding_id", "finding_ids", "run_evaluation_finding_id", "run_evaluation_finding_ids"],
          sourceFindingId
        )
      ) {
        return false;
      }
      return true;
    },

    toolAuthorizationQueryString() {
      const params = new URLSearchParams();
      for (const key of TOOL_AUTH_FILTER_KEYS) {
        const value = String(this.toolAuthorizationFilters?.[key] || "").trim();
        if (value) {
          params.set(key, value);
        }
      }
      return params.toString();
    },

    emptyToolAuthorizationFilters() {
      return {
        status: "pending",
        tool_name: "",
        agent_run_id: "",
        run_id: "",
        agent_key: "",
        proposal_id: "",
        source_run_id: "",
        source_evaluation_id: "",
        source_finding_id: ""
      };
    },

    syncToolAuthorizationFiltersFromLocation() {
      if (typeof window === "undefined" || !window.location) {
        return;
      }
      const search = window.location.search || "";
      if (search === this.toolAuthorizationLocationSearch) {
        return;
      }
      this.toolAuthorizationLocationSearch = search;
      if (!search) {
        this.toolAuthorizationFilters = this.emptyToolAuthorizationFilters();
        return;
      }
      const params = new URLSearchParams(search);
      const next = this.emptyToolAuthorizationFilters();
      next.status = params.has("status") ? params.get("status") || "" : "";
      for (const key of TOOL_AUTH_FILTER_KEYS) {
        if (key !== "status") {
          next[key] = params.get(key) || "";
        }
      }
      this.toolAuthorizationFilters = next;
    },

    applyToolAuthorizationFilters() {
      this.replaceToolAuthorizationFilterLocation();
      return this.loadToolAuthorizations();
    },

    resetToolAuthorizationFilters() {
      this.toolAuthorizationFilters = this.emptyToolAuthorizationFilters();
      this.replaceToolAuthorizationFilterLocation();
      return this.loadToolAuthorizations();
    },

    replaceToolAuthorizationFilterLocation() {
      if (typeof window === "undefined" || !window.history?.replaceState) {
        return;
      }
      window.history.replaceState({}, "", buildToolAuthorizationsPath(this.toolAuthorizationFilters));
      this.toolAuthorizationLocationSearch = window.location.search || "";
    },

    async decideToolAuthorization(authorization, decision) {
      if (!authorization?.id || !["approve", "reject"].includes(decision)) {
        return;
      }
      this.busy.toolAuthorizationAction = true;
      try {
        const updated = await this.apiRequest(`/tool-authorizations/${encodeURIComponent(authorization.id)}/${decision}`, {
          method: "POST",
          body: JSON.stringify({
            response_payload: {
              decision_source: "platform_tool_authorizations_ui"
            }
          })
        });
        this.replaceToolAuthorization(updated);
        this.showNotice("success", decision === "approve" ? "工具授权已批准。" : "工具授权已拒绝。");
      } catch (error) {
        this.showNotice("error", error.message || "工具授权处理失败。");
      } finally {
        this.busy.toolAuthorizationAction = false;
      }
    },

    replaceToolAuthorization(authorization) {
      const index = this.toolAuthorizations.findIndex((item) => item.id === authorization.id);
      if (index >= 0) {
        this.toolAuthorizations.splice(index, 1, authorization);
      } else {
        this.toolAuthorizations.unshift(authorization);
      }
      if (Array.isArray(this.governanceProposalToolAuthorizations)) {
        const proposalIndex = this.governanceProposalToolAuthorizations.findIndex((item) => item.id === authorization.id);
        if (proposalIndex >= 0) {
          this.governanceProposalToolAuthorizations.splice(proposalIndex, 1, authorization);
        }
      }
    },

    governanceProposalsPath(filters = {}) {
      return buildGovernanceProposalsPath(filters);
    },

    governanceProposalPath(proposalId) {
      return buildGovernanceProposalPath(proposalId);
    },

    governanceEvaluationReportPath(evaluationId) {
      return buildEvaluationReportPath(evaluationId);
    },

    governanceProposalAgentRunPath(proposalOrAgentRun = this.currentGovernanceProposal, focus = { tab: "events" }) {
      const agentRunId = String(
        typeof proposalOrAgentRun === "string"
          ? proposalOrAgentRun
          : (proposalOrAgentRun?.agent_run_id || proposalOrAgentRun?.id || "")
      ).trim();
      return agentRunId ? buildPlatformAgentRunPath(agentRunId, focus) : "";
    },

    openGovernanceProposalAgentRun(proposalOrAgentRun = this.currentGovernanceProposal, focus = { tab: "events" }) {
      const path = this.governanceProposalAgentRunPath(proposalOrAgentRun, focus);
      if (!path) {
        return;
      }
      this.navigate(path);
    },

    governanceProposalMemoryEntryPath(memory) {
      const memoryId = String(typeof memory === "string" ? memory : memory?.id || "").trim();
      return memoryId ? buildPlatformMemoryEntryPath(memoryId) : "";
    },

    openGovernanceProposalMemoryEntry(memory) {
      const path = this.governanceProposalMemoryEntryPath(memory);
      if (!path) {
        return;
      }
      this.navigate(path);
    },

    governanceExperimentsPath(filters = {}) {
      return buildGovernanceExperimentsPath(filters);
    },

    toolAuthorizationsPath() {
      return buildToolAuthorizationsPath();
    },

    governanceProposalTypeOptions() {
      return PROPOSAL_TYPE_OPTIONS;
    },

    governanceProposalStatusOptions() {
      return PROPOSAL_STATUS_OPTIONS;
    },

    toolAuthorizationStatusOptions() {
      return TOOL_AUTH_STATUS_OPTIONS;
    },

    governanceExperimentStatusOptions() {
      return EXPERIMENT_STATUS_OPTIONS;
    },

    governanceExperimentTypeOptions() {
      return EXPERIMENT_TYPE_OPTIONS;
    },

    governanceProposalTypeLabel(value) {
      return this.optionLabel(PROPOSAL_TYPE_OPTIONS, value);
    },

    governanceProposalStatusLabel(value) {
      return this.optionLabel(PROPOSAL_STATUS_OPTIONS, value);
    },

    governanceExperimentStatusLabel(value) {
      return this.optionLabel(EXPERIMENT_STATUS_OPTIONS, value);
    },

    governanceExperimentTypeLabel(value) {
      return this.optionLabel(EXPERIMENT_TYPE_OPTIONS, value);
    },

    governanceExperimentMetricRows(experiment) {
      const before = experiment?.before_metrics && typeof experiment.before_metrics === "object"
        ? experiment.before_metrics
        : {};
      const after = experiment?.after_metrics && typeof experiment.after_metrics === "object"
        ? experiment.after_metrics
        : {};
      const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
      return keys.map((key) => ({
        key,
        before: before[key],
        after: after[key],
        changed: this.governanceMetricValueLabel(before[key]) !== this.governanceMetricValueLabel(after[key])
      }));
    },

    governanceMetricValueLabel(value) {
      if (value === undefined) {
        return "N/A";
      }
      if (value === null || typeof value !== "object") {
        return String(value);
      }
      return this.governanceJsonPreview(value);
    },

    governanceExperimentRegressionChecks(experiment) {
      const result = experiment?.result || {};
      const regression = result.regression || {};
      if (Array.isArray(regression.checks)) {
        return regression.checks;
      }
      return Array.isArray(result.checks) ? result.checks : [];
    },

    governanceExperimentCanaryScope(experiment) {
      if (experiment?.canary_scope && typeof experiment.canary_scope === "object") {
        return experiment.canary_scope;
      }
      const scope = experiment?.result?.canary_scope;
      return scope && typeof scope === "object" ? scope : {};
    },

    governanceExperimentRollbackConditions(experiment) {
      if (Array.isArray(experiment?.rollback_conditions)) {
        return experiment.rollback_conditions;
      }
      const conditions = experiment?.result?.rollback_conditions;
      return Array.isArray(conditions) ? conditions : [];
    },

    toolAuthorizationStatusLabel(value) {
      return this.optionLabel(TOOL_AUTH_STATUS_OPTIONS, value);
    },

    toolAuthorizationContextLinks(authorization) {
      const links = [];
      const authorizationId = String(authorization?.id || "").trim();
      const agentRunId = String(authorization?.agent_run_id || "").trim();
      const toolCallId = String(authorization?.agent_tool_call_id || "").trim();
      const runId = String(authorization?.run_id || "").trim();
      const runEventId = String(authorization?.run_event_id || "").trim();

      if (agentRunId) {
        links.push({
          key: `agent-run-${agentRunId}`,
          label: "AgentRun",
          value: agentRunId,
          href: buildPlatformAgentRunPath(agentRunId, { tab: "events" }),
          icon: "timeline"
        });
      }
      if (agentRunId && authorizationId) {
        links.push({
          key: `authorization-${authorizationId}`,
          label: "Authorization",
          value: authorizationId,
          href: buildPlatformAgentRunPath(agentRunId, { tab: "authorizations", authorization_id: authorizationId }),
          icon: "admin_panel_settings"
        });
      }
      if (agentRunId && toolCallId) {
        links.push({
          key: `tool-call-${toolCallId}`,
          label: "ToolCall",
          value: toolCallId,
          href: buildPlatformAgentRunPath(agentRunId, { tab: "tools", tool_call_id: toolCallId }),
          icon: "build"
        });
      }
      if (runId) {
        links.push({
          key: `run-replay-${runId}`,
          label: "Run Replay",
          value: runId,
          href: buildReplayPath(runId),
          icon: "history"
        });
      }
      if (runId && runEventId) {
        links.push({
          key: `run-event-${runEventId}`,
          label: "RunEvent",
          value: runEventId,
          href: buildReplayPath(runId, { event_id: runEventId }),
          icon: "receipt_long"
        });
      }

      return this.uniqueToolAuthorizationLinks([
        ...links,
        ...this.toolAuthorizationBusinessLinks(authorization)
      ]);
    },

    toolAuthorizationBusinessLinks(authorization) {
      const links = [];
      const proposalId = this.toolAuthorizationFirstNestedValue(authorization, [
        "proposal_id",
        "governance_proposal_id"
      ]);
      const experimentId = this.toolAuthorizationFirstNestedValue(authorization, [
        "experiment_id",
        "governance_experiment_id"
      ]);
      const evaluationId = this.toolAuthorizationFirstNestedValue(authorization, [
        "evaluation_id",
        "evaluation_report_id",
        "run_evaluation_id",
        "source_evaluation_id"
      ]);
      const findingIds = this.toolAuthorizationNestedValues(authorization, [
        "finding_id",
        "run_evaluation_finding_id",
        "source_finding_id",
        "source_finding_ids",
        "finding_ids",
        "run_evaluation_finding_ids"
      ]);
      const runId = String(authorization?.run_id || this.toolAuthorizationFirstNestedValue(authorization, [
        "run_id",
        "source_run_id"
      ]) || "").trim();
      const runTraceId = this.toolAuthorizationFirstNestedValue(authorization, [
        "run_trace_id",
        "trace_id",
        "trace_event_id"
      ]);
      const snapshotSeq = this.toolAuthorizationFirstNestedValue(authorization, [
        "snapshot_seq",
        "session_token_seq",
        "seq_no"
      ]);
      const skillId = this.toolAuthorizationFirstNestedValue(authorization, [
        "skill_id",
        "pskill_definition_id",
        "pskill_id"
      ]);
      const packageName = this.toolAuthorizationFirstNestedValue(authorization, [
        "package_name",
        "skill_package",
        "skill_package_name"
      ]);
      const agentKey = this.toolAuthorizationFirstNestedValue(authorization, ["agent_key"]);
      const memoryId = this.toolAuthorizationFirstNestedValue(authorization, [
        "memory_id",
        "memory_entry_id"
      ]);

      if (proposalId) {
        links.push({
          key: `proposal-${proposalId}`,
          label: "Governance Proposal",
          value: proposalId,
          href: buildGovernanceProposalPath(proposalId),
          icon: "account_tree"
        });
      }
      if (experimentId) {
        links.push({
          key: `experiment-${experimentId}`,
          label: "Experiment",
          value: experimentId,
          href: buildGovernanceExperimentsPath({ experiment_id: experimentId }),
          icon: "science"
        });
      }
      if (evaluationId) {
        links.push({
          key: `evaluation-${evaluationId}`,
          label: "Evaluation",
          value: evaluationId,
          href: buildEvaluationReportPath(evaluationId),
          icon: "fact_check"
        });
      }
      for (const findingId of findingIds) {
        const href = evaluationId
          ? buildEvaluationReportPath(evaluationId)
          : buildEvaluationFindingsPath({ run_id: runId });
        links.push({
          key: `finding-${findingId}`,
          label: "Finding",
          value: findingId,
          href,
          icon: "find_in_page"
        });
      }
      if (runTraceId) {
        links.push({
          key: `run-trace-${runTraceId}`,
          label: "RunTrace",
          value: runTraceId,
          href: runId ? buildReplayPath(runId, { trace_id: runTraceId }) : buildReplayTracePath(runTraceId),
          icon: "timeline"
        });
      }
      if (runId && snapshotSeq) {
        links.push({
          key: `snapshot-${snapshotSeq}`,
          label: "Snapshot",
          value: snapshotSeq,
          href: buildReplayPath(runId, { snapshot_seq: snapshotSeq }),
          icon: "difference"
        });
      }
      if (skillId) {
        links.push({
          key: `skill-${skillId}`,
          label: "PSkill",
          value: skillId,
          href: buildSkillDetailPath(skillId),
          icon: "hub"
        });
      }
      if (packageName) {
        links.push({
          key: `skill-package-${packageName}`,
          label: "Skill Package",
          value: packageName,
          href: buildPlatformSkillPath(packageName),
          icon: "inventory_2"
        });
      }
      if (agentKey) {
        links.push({
          key: `agent-${agentKey}`,
          label: "Agent",
          value: agentKey,
          href: buildPlatformAgentPath(agentKey),
          icon: "smart_toy"
        });
      }
      if (memoryId) {
        links.push({
          key: `memory-${memoryId}`,
          label: "Memory",
          value: memoryId,
          href: buildPlatformMemoryEntryPath(memoryId),
          icon: "database"
        });
      }
      return links;
    },

    uniqueToolAuthorizationLinks(links) {
      const seen = new Set();
      return (links || []).filter((link) => {
        const key = `${link?.key || ""}:${link?.href || ""}`;
        if (!link?.href || seen.has(key)) {
          return false;
        }
        seen.add(key);
        return true;
      });
    },

    toolAuthorizationFirstNestedValue(authorization, keys) {
      return this.toolAuthorizationNestedValues(authorization, keys)[0] || "";
    },

    toolAuthorizationContextHasValue(authorization, keys, expected, fallback = "") {
      const normalizedExpected = String(expected || "").trim();
      if (!normalizedExpected) {
        return true;
      }
      const values = this.toolAuthorizationNestedValues(authorization, keys);
      const normalizedFallback = String(fallback || "").trim();
      if (normalizedFallback) {
        values.push(normalizedFallback);
      }
      return values.includes(normalizedExpected);
    },

    toolAuthorizationNestedValues(authorization, keys) {
      const keySet = new Set(keys);
      const sources = [
        authorization?.business_context,
        authorization?.tool_arguments_summary,
        authorization?.request_payload,
        authorization?.request_payload?.decision,
        authorization?.request_payload?.decision?.arguments_summary,
        authorization?.request_payload?.proposal,
        authorization?.response_payload,
        authorization?.response_payload?.result,
        authorization?.response_payload?.proposal
      ];
      const seen = new Set();
      const values = [];

      const collect = (item, depth) => {
        if (item === null || item === undefined || depth > 8) {
          return;
        }
        if (Array.isArray(item)) {
          for (const nested of item) {
            collect(nested, depth + 1);
          }
          return;
        }
        if (typeof item === "object") {
          walk(item, depth + 1);
          return;
        }
        const normalized = String(item || "").trim();
        if (normalized && !values.includes(normalized)) {
          values.push(normalized);
        }
      };

      const walk = (value, depth = 0) => {
        if (value === null || value === undefined || depth > 8) {
          return;
        }
        if (typeof value !== "object") {
          return;
        }
        if (seen.has(value)) {
          return;
        }
        seen.add(value);
        if (Array.isArray(value)) {
          for (const item of value) {
            walk(item, depth + 1);
          }
          return;
        }
        for (const [key, item] of Object.entries(value)) {
          if (keySet.has(key)) {
            collect(item, depth + 1);
          }
        }
        for (const item of Object.values(value)) {
          walk(item, depth + 1);
        }
      };

      for (const source of sources) {
        walk(source);
      }
      return values;
    },

    governanceProposalSourceLabel(proposal) {
      const findingCount = Array.isArray(proposal?.source_finding_ids) ? proposal.source_finding_ids.length : 0;
      if (findingCount) {
        return `${findingCount} finding`;
      }
      return proposal?.source_run_id ? "Run evaluation" : "Manual";
    },

    governanceProposalSourceFindings(proposal) {
      if (Array.isArray(proposal?.source_findings) && proposal.source_findings.length) {
        return proposal.source_findings;
      }
      return (proposal?.source_finding_ids || []).map((id) => ({
        id,
        evaluation_id: proposal?.source_evaluation_id || "",
        run_id: proposal?.source_run_id || "",
        evidence_refs: []
      }));
    },

    governanceProposalEvidenceLinks(proposal = this.currentGovernanceProposal) {
      const refs = Array.isArray(proposal?.evidence_refs) ? proposal.evidence_refs : [];
      const links = refs
        .map((ref) => this.governanceProposalEvidenceLink(proposal, ref))
        .filter(Boolean);
      return this.uniqueToolAuthorizationLinks(links);
    },

    governanceProposalEvidenceLink(proposal, ref) {
      if (!ref || typeof ref !== "object") {
        return null;
      }
      const kind = this.governanceNormalizeEvidenceKind(ref.kind || ref.source_kind || ref.type);
      const id = this.governanceEvidenceRefId(ref);
      const runId = this.governanceEvidenceRunId(proposal, ref);
      const evaluationId = String(ref.evaluation_id || ref.run_evaluation_id || "").trim();
      const agentRunId = String(ref.agent_run_id || ref.agentRunId || "").trim();

      if (["run_evaluation", "evaluation"].includes(kind)) {
        const value = id || evaluationId || String(proposal?.source_evaluation_id || "").trim();
        return value ? this.governanceEvidenceLink("evaluation", "Evaluation", value, buildEvaluationReportPath(value), "fact_check") : null;
      }
      if (["run_evaluation_finding", "evaluation_finding", "finding"].includes(kind)) {
        const value = String(ref.finding_id || id || "").trim();
        const href = evaluationId || proposal?.source_evaluation_id
          ? buildEvaluationReportPath(evaluationId || proposal.source_evaluation_id)
          : buildEvaluationFindingsPath({
            run_id: runId,
            status: ref.status || "",
            category: ref.category || ""
          });
        return value || href
          ? this.governanceEvidenceLink("finding", "Finding", value || "Findings", href, "find_in_page")
          : null;
      }
      if (["run", "run_replay", "replay"].includes(kind)) {
        const value = runId || id || String(ref.run_id || "").trim();
        return value ? this.governanceEvidenceLink("run-replay", "Run Replay", value, buildReplayPath(value), "history") : null;
      }
      if (kind === "run_trace") {
        const value = String(ref.trace_id || ref.run_trace_id || id || "").trim();
        if (!value) {
          return null;
        }
        const href = runId ? buildReplayPath(runId, { trace_id: value }) : buildReplayTracePath(value);
        return this.governanceEvidenceLink("run-trace", "RunTrace", value, href, "timeline");
      }
      if (kind === "run_event") {
        const value = String(ref.run_event_id || ref.event_id || id || "").trim();
        if (!runId || !value) {
          return null;
        }
        return this.governanceEvidenceLink("run-event", "RunEvent", value, buildReplayPath(runId, { event_id: value }), "receipt_long");
      }
      if (kind === "session_token_snapshot") {
        const value = String(ref.snapshot_seq || ref.seq_no || id || "").trim();
        if (!runId || !value) {
          return null;
        }
        return this.governanceEvidenceLink("snapshot", "Snapshot", value, buildReplayPath(runId, { snapshot_seq: value }), "difference");
      }
      if (["agent_run", "agentrun"].includes(kind)) {
        const value = id || agentRunId;
        return value ? this.governanceEvidenceLink("agent-run", "AgentRun", value, buildPlatformAgentRunPath(value, { tab: "events" }), "smart_toy") : null;
      }
      if (["agent_event", "agent_run_event"].includes(kind)) {
        const value = String(ref.event_id || id || "").trim();
        const ownerAgentRunId = agentRunId || String(proposal?.agent_run_id || "").trim();
        if (!ownerAgentRunId || !value) {
          return null;
        }
        return this.governanceEvidenceLink("agent-event", "AgentEvent", value, buildPlatformAgentRunPath(ownerAgentRunId, { tab: "events", event_id: value }), "event_note");
      }
      if (["agent_model_call", "model_call"].includes(kind)) {
        const value = String(ref.model_call_id || id || "").trim();
        const ownerAgentRunId = agentRunId || String(proposal?.agent_run_id || "").trim();
        if (!ownerAgentRunId || !value) {
          return null;
        }
        return this.governanceEvidenceLink("model-call", "ModelCall", value, buildPlatformAgentRunPath(ownerAgentRunId, { tab: "model", model_call_id: value }), "psychology");
      }
      if (["agent_tool_call", "tool_call"].includes(kind)) {
        const value = String(ref.tool_call_id || ref.agent_tool_call_id || id || "").trim();
        const ownerAgentRunId = agentRunId || String(proposal?.agent_run_id || "").trim();
        if (!ownerAgentRunId || !value) {
          return null;
        }
        return this.governanceEvidenceLink("tool-call", "ToolCall", value, buildPlatformAgentRunPath(ownerAgentRunId, { tab: "tools", tool_call_id: value }), "build");
      }
      if (["agent_tool_authorization", "tool_authorization"].includes(kind)) {
        const value = String(ref.authorization_id || ref.tool_authorization_id || id || "").trim();
        const ownerAgentRunId = agentRunId || String(proposal?.agent_run_id || "").trim();
        const href = ownerAgentRunId && value
          ? buildPlatformAgentRunPath(ownerAgentRunId, { tab: "authorizations", authorization_id: value })
          : buildToolAuthorizationsPath();
        return this.governanceEvidenceLink("authorization", "Authorization", value || "Tool Authorizations", href, "admin_panel_settings");
      }
      if (["psop_improvement_proposal", "governance_proposal", "proposal"].includes(kind)) {
        const value = String(ref.proposal_id || id || "").trim();
        return value ? this.governanceEvidenceLink("proposal", "Proposal", value, buildGovernanceProposalPath(value), "account_tree") : null;
      }
      if (["psop_improvement_experiment", "governance_experiment", "experiment"].includes(kind)) {
        const value = String(ref.experiment_id || id || "").trim();
        return value ? this.governanceEvidenceLink("experiment", "Experiment", value, buildGovernanceExperimentsPath({ experiment_id: value }), "science") : null;
      }
      if (["agent_memory_entry", "memory"].includes(kind)) {
        const value = String(ref.memory_id || ref.memory_entry_id || id || "").trim();
        return value ? this.governanceEvidenceLink("memory", "Memory", value, buildPlatformMemoryEntryPath(value), "database") : null;
      }
      if (["pskill", "pskill_definition", "skill"].includes(kind)) {
        const value = String(ref.pskill_definition_id || ref.skill_id || id || "").trim();
        return value ? this.governanceEvidenceLink("pskill", "PSkill", value, buildSkillDetailPath(value), "hub") : null;
      }
      if (["skill_package", "package"].includes(kind)) {
        const value = String(ref.package_name || ref.skill_package || id || "").trim();
        return value ? this.governanceEvidenceLink("skill-package", "Skill Package", value, buildPlatformSkillPath(value), "inventory_2") : null;
      }
      if (kind === "agent") {
        const value = String(ref.agent_key || id || "").trim();
        return value ? this.governanceEvidenceLink("agent", "Agent", value, buildPlatformAgentPath(value), "smart_toy") : null;
      }
      return null;
    },

    governanceEvidenceLink(prefix, label, value, href, icon) {
      if (!href) {
        return null;
      }
      const normalizedValue = String(value || "").trim();
      return {
        key: `${prefix}-${normalizedValue || href}`,
        label,
        value: normalizedValue,
        href,
        icon
      };
    },

    governanceEvidenceRefId(ref) {
      return String(
        ref?.id ||
          ref?.source_id ||
          ref?.evaluation_id ||
          ref?.finding_id ||
          ref?.run_id ||
          ref?.trace_id ||
          ref?.event_id ||
          ref?.agent_run_id ||
          ref?.authorization_id ||
          ref?.proposal_id ||
          ref?.experiment_id ||
          ref?.memory_id ||
          ""
      ).trim();
    },

    governanceEvidenceRunId(proposal, ref = {}) {
      return String(
        ref.run_id ||
          ref.source_run_id ||
          proposal?.source_run_id ||
          ""
      ).trim();
    },

    governanceSourceFindingReplayPath(finding, ref = null) {
      const runId = String(finding?.run_id || "").trim();
      if (!runId) {
        return "";
      }
      const focus = this.governanceSourceFindingReplayFocus(ref);
      return buildReplayPath(runId, focus);
    },

    governanceNormalizeEvidenceKind(kind) {
      const value = String(kind || "").trim().toLowerCase();
      if (value === "terminal_event") {
        return "run_event";
      }
      if (value === "trace_event") {
        return "run_trace";
      }
      return value;
    },

    governanceSourceFindingReplayFocus(ref) {
      if (!ref || typeof ref !== "object") {
        return {};
      }
      const kind = this.governanceNormalizeEvidenceKind(ref.kind || ref.source_kind);
      const traceId = String(ref.trace_id || ref.run_trace_id || ref.trace_event_id || "").trim();
      if (traceId) {
        return { trace_id: traceId };
      }
      const eventId = String(ref.run_event_id || ref.event_id || "").trim();
      if (eventId) {
        return { event_id: eventId };
      }
      const id = String(ref.id || ref.source_id || "").trim();
      if (id && kind === "run_trace") {
        return { trace_id: id };
      }
      if (id) {
        return { event_id: id };
      }
      const seqNo = String(ref.seq_no ?? "").trim();
      return seqNo ? { seq_no: seqNo } : {};
    },

    openGovernanceSourceFindingReplay(finding, ref = null) {
      const path = this.governanceSourceFindingReplayPath(finding, ref);
      if (!path) {
        this.showNotice?.("error", "Finding 缺少 Run 关联，无法打开 Replay。");
        return;
      }
      return this.navigate(path);
    },

    governanceFindingEvidenceLabel(ref) {
      if (!ref) {
        return "N/A";
      }
      const kind = this.governanceNormalizeEvidenceKind(ref.kind || ref.source_kind) || "evidence";
      const seq = ref.seq_no === null || ref.seq_no === undefined ? "" : ` #${ref.seq_no}`;
      const type = ref.event_type || ref.event_kind || "";
      return `${kind}${seq}${type ? ` · ${type}` : ""}`;
    },

    governanceProposalHasPatchDiff(proposal) {
      return Boolean(this.governanceProposalPatchDiffText(proposal));
    },

    governanceProposalPatchDiffText(proposal) {
      const direct = this.firstToolAuthorizationDiffValue([
        proposal?.target?.patch_diff,
        proposal?.target?.patchDiff,
        proposal?.target?.unified_diff,
        proposal?.target?.unifiedDiff,
        proposal?.target?.diff,
        proposal?.target?.patch,
        proposal?.patch_diff,
        proposal?.diff,
        proposal?.patch
      ]);
      if (direct) {
        return direct;
      }
      const changeDiffs = (proposal?.proposed_changes || [])
        .map((change) => this.governanceProposalChangeDiffText(change))
        .filter(Boolean);
      return changeDiffs.join("\n\n");
    },

    governanceProposalChangeDiffText(change) {
      const direct = this.firstToolAuthorizationDiffValue([
        change?.patch_diff,
        change?.patchDiff,
        change?.unified_diff,
        change?.unifiedDiff,
        change?.diff,
        change?.patch
      ]);
      if (direct) {
        return direct;
      }
      if (change && Object.prototype.hasOwnProperty.call(change, "before") && Object.prototype.hasOwnProperty.call(change, "after")) {
        return this.toolAuthorizationBeforeAfterDiff(change.before, change.after);
      }
      return "";
    },

    governanceJsonPreview(value) {
      return JSON.stringify(value ?? null, null, 2);
    },

    governanceCanRunTests(proposal) {
      return ["draft", "testing", "reviewing", "rejected"].includes(String(proposal?.status || ""));
    },

    governanceCanReview(proposal) {
      return ["draft", "testing", "reviewing", "rejected"].includes(String(proposal?.status || ""));
    },

    governanceCanActivateCanary(proposal) {
      return String(proposal?.status || "") === "approved";
    },

    governanceCanRollback(proposal) {
      return ["canary", "activated"].includes(String(proposal?.status || ""));
    },

    toolAuthorizationReversibleLabel(authorization) {
      const state = authorization?.reversible ? "可回滚" : "不可回滚";
      const summary = this.toolAuthorizationRollbackSummary(authorization);
      return summary ? `${state} · ${summary}` : state;
    },

    toolAuthorizationRollbackSummary(authorization) {
      const summary = authorization?.tool_arguments_summary || {};
      const requestPayload = authorization?.request_payload || {};
      const responsePayload = authorization?.response_payload || {};
      return this.firstToolAuthorizationTextValue([
        authorization?.rollback_summary,
        authorization?.rollbackSummary,
        authorization?.rollback_plan,
        authorization?.rollbackPlan,
        authorization?.rollback_strategy,
        authorization?.rollbackStrategy,
        authorization?.irreversible_reason,
        authorization?.non_reversible_reason,
        summary.rollback_summary,
        summary.rollbackSummary,
        summary.rollback_plan,
        summary.rollbackPlan,
        summary.rollback_strategy,
        summary.rollbackStrategy,
        summary.rollback,
        summary.irreversible_reason,
        summary.non_reversible_reason,
        requestPayload.rollback_summary,
        requestPayload.rollbackSummary,
        requestPayload.rollback_plan,
        requestPayload.rollbackPlan,
        requestPayload.rollback_strategy,
        requestPayload.rollbackStrategy,
        requestPayload.rollback,
        requestPayload.irreversible_reason,
        requestPayload.non_reversible_reason,
        responsePayload.rollback_summary,
        responsePayload.rollbackSummary,
        responsePayload.rollback_plan,
        responsePayload.rollbackPlan,
        responsePayload.rollback,
        responsePayload.irreversible_reason,
        responsePayload.non_reversible_reason
      ]);
    },

    toolAuthorizationHasDiff(authorization) {
      return Boolean(this.toolAuthorizationDiffText(authorization));
    },

    toolAuthorizationDiffText(authorization) {
      const summary = authorization?.tool_arguments_summary || {};
      const requestPayload = authorization?.request_payload || {};
      const direct = this.firstToolAuthorizationDiffValue([
        summary.patch_diff,
        summary.patchDiff,
        summary.unified_diff,
        summary.unifiedDiff,
        summary.diff,
        summary.patch,
        requestPayload.patch_diff,
        requestPayload.patchDiff,
        requestPayload.unified_diff,
        requestPayload.unifiedDiff,
        requestPayload.diff,
        requestPayload.patch
      ]);
      if (direct) {
        return direct;
      }
      const changes = Array.isArray(summary.changes) ? summary.changes : requestPayload.changes;
      if (Array.isArray(changes) && changes.length) {
        const changeDiffs = changes
          .map((item) => this.firstToolAuthorizationDiffValue([
            item?.patch_diff,
            item?.patchDiff,
            item?.unified_diff,
            item?.unifiedDiff,
            item?.diff,
            item?.patch
          ]) || this.governanceJsonPreview(item))
          .filter(Boolean);
        return changeDiffs.join("\n\n");
      }
      const before = summary.before ?? requestPayload.before;
      const after = summary.after ?? requestPayload.after;
      if (before !== undefined && after !== undefined) {
        return this.toolAuthorizationBeforeAfterDiff(before, after);
      }
      return "";
    },

    firstToolAuthorizationDiffValue(values) {
      for (const value of values) {
        if (typeof value === "string" && value.trim()) {
          return value.trim();
        }
      }
      return "";
    },

    firstToolAuthorizationTextValue(values) {
      for (const value of values) {
        if (value === null || value === undefined || value === "") {
          continue;
        }
        if (typeof value === "string") {
          const normalized = value.trim();
          if (normalized) {
            return normalized;
          }
          continue;
        }
        if (typeof value === "number" || typeof value === "boolean") {
          return String(value);
        }
        if (typeof value === "object") {
          const text = this.governanceJsonPreview(value);
          if (text && text !== "null" && text !== "{}" && text !== "[]") {
            return text;
          }
        }
      }
      return "";
    },

    toolAuthorizationBeforeAfterDiff(before, after) {
      const beforeLines = this.governanceJsonPreview(before).split("\n").map((line) => `- ${line}`);
      const afterLines = this.governanceJsonPreview(after).split("\n").map((line) => `+ ${line}`);
      return ["--- before", "+++ after", ...beforeLines, ...afterLines].join("\n");
    },

    toolAuthorizationRiskTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (normalized === "high") {
        return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      }
      if (normalized === "medium") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      return "border-slate-700 bg-slate-950/40 text-slate-400";
    },

    toolAuthorizationSideEffectTone(value) {
      const normalized = String(value || "").toLowerCase();
      if (["high_write", "external_action", "physical_action"].includes(normalized)) {
        return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      }
      if (normalized === "low_write") {
        return "border-amber-500/25 bg-amber-500/10 text-amber-200";
      }
      return "border-sky-500/25 bg-sky-500/10 text-sky-200";
    }
  };
})();
