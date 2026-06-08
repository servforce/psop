(function () {
  const {
    buildSkillDetailPath,
    buildEvaluationReportPath,
    buildGovernanceProposalsPath,
    buildGovernanceProposalPath,
    buildGovernanceExperimentsPath,
    buildToolAuthorizationsPath,
    buildPlatformAgentPath,
    buildPlatformAgentRunPath,
    buildPlatformSkillPath,
    buildPlatformMemoryEntryPath,
    buildReplayPath,
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
      return this.loadGovernanceProposalsPage();
    },

    resetGovernanceProposalFilters() {
      this.governanceProposalFilters = { status: "" };
      return this.loadGovernanceProposalsPage();
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
      return this.loadGovernanceExperiments();
    },

    resetGovernanceExperimentFilters() {
      this.governanceExperimentFilters = { proposal_id: "", status: "", experiment_type: "" };
      return this.loadGovernanceExperiments();
    },

    async openGovernanceExperiment() {
      const experimentId = String(this.governanceExperimentLookupId || "").trim();
      if (!experimentId) {
        this.showNotice("error", "请输入 Experiment ID。");
        return;
      }
      this.busy.governanceExperimentLookup = true;
      try {
        this.governanceExperimentDetail = await this.apiRequest(`/governance/experiments/${encodeURIComponent(experimentId)}`);
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
        status: experiment.proposal_status || "",
        proposal_type: experiment.proposal_type || "",
        problem_statement: experiment.problem_statement || "",
        source_run_id: experiment.source_run_id || "",
        experiments: []
      };
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

    toolAuthorizationQueryString() {
      const params = new URLSearchParams();
      const status = String(this.toolAuthorizationFilters.status || "").trim();
      const toolName = String(this.toolAuthorizationFilters.tool_name || "").trim();
      if (status) {
        params.set("status", status);
      }
      if (toolName) {
        params.set("tool_name", toolName);
      }
      return params.toString();
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
        this.toolAuthorizationFilters = { status: "pending", tool_name: "" };
        return;
      }
      const params = new URLSearchParams(search);
      this.toolAuthorizationFilters = {
        ...this.toolAuthorizationFilters,
        status: params.has("status") ? params.get("status") || "" : "",
        tool_name: params.get("tool_name") || ""
      };
    },

    applyToolAuthorizationFilters() {
      this.replaceToolAuthorizationFilterLocation();
      return this.loadToolAuthorizations();
    },

    resetToolAuthorizationFilters() {
      this.toolAuthorizationFilters = { status: "pending", tool_name: "" };
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

    governanceProposalsPath() {
      return buildGovernanceProposalsPath();
    },

    governanceProposalPath(proposalId) {
      return buildGovernanceProposalPath(proposalId);
    },

    governanceEvaluationReportPath(evaluationId) {
      return buildEvaluationReportPath(evaluationId);
    },

    governanceExperimentsPath() {
      return buildGovernanceExperimentsPath();
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
      const evaluationId = this.toolAuthorizationFirstNestedValue(authorization, [
        "evaluation_id",
        "evaluation_report_id"
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
      if (evaluationId) {
        links.push({
          key: `evaluation-${evaluationId}`,
          label: "Evaluation",
          value: evaluationId,
          href: buildEvaluationReportPath(evaluationId),
          icon: "fact_check"
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
      const keySet = new Set(keys);
      const sources = [
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

      const walk = (value, depth = 0) => {
        if (value === null || value === undefined || depth > 8) {
          return "";
        }
        if (typeof value !== "object") {
          return "";
        }
        if (seen.has(value)) {
          return "";
        }
        seen.add(value);
        if (Array.isArray(value)) {
          for (const item of value) {
            const nested = walk(item, depth + 1);
            if (nested) {
              return nested;
            }
          }
          return "";
        }
        for (const [key, item] of Object.entries(value)) {
          if (keySet.has(key)) {
            const normalized = String(item || "").trim();
            if (normalized && typeof item !== "object") {
              return normalized;
            }
          }
        }
        for (const item of Object.values(value)) {
          const nested = walk(item, depth + 1);
          if (nested) {
            return nested;
          }
        }
        return "";
      };

      for (const source of sources) {
        const value = walk(source);
        if (value) {
          return value;
        }
      }
      return "";
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

    governanceSourceFindingReplayPath(finding, ref = null) {
      const runId = String(finding?.run_id || "").trim();
      if (!runId) {
        return "";
      }
      const focus = this.governanceSourceFindingReplayFocus(ref);
      return buildReplayPath(runId, focus);
    },

    governanceSourceFindingReplayFocus(ref) {
      if (!ref || typeof ref !== "object") {
        return {};
      }
      const eventId = String(ref.id || ref.source_id || ref.run_trace_id || ref.run_event_id || ref.event_id || "").trim();
      if (eventId) {
        return { event_id: eventId };
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
      const kind = ref.kind || "evidence";
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
      return authorization?.reversible ? "可回滚" : "不可回滚";
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
