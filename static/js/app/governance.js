(function () {
  const {
    buildGovernanceProposalsPath,
    buildGovernanceProposalPath,
    buildGovernanceExperimentsPath,
    buildToolAuthorizationsPath
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
    { value: "rejected", label: "已拒绝" }
  ];

  window.PSOPConsoleGovernanceMethods = {
    async loadGovernanceProposalsPage() {
      await this.loadGovernanceProposals();
      if (!this.currentGovernanceProposal && this.governanceProposals.length) {
        this.currentGovernanceProposal = this.governanceProposals[0];
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
        this.currentGovernanceProposal = await this.apiRequest(`/governance/proposals/${encodeURIComponent(id)}`);
      } catch (error) {
        this.showNotice("error", error.message || "治理提案详情加载失败。");
      } finally {
        this.busy.governanceProposals = false;
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
        const proposals = await this.apiRequest("/governance/proposals");
        this.governanceProposals = Array.isArray(proposals) ? proposals : [];
        this.refreshGovernanceExperimentRows();
      } catch (error) {
        this.showNotice("error", error.message || "治理实验加载失败。");
      } finally {
        this.busy.governanceExperiments = false;
      }
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
      } catch (error) {
        this.showNotice("error", error.message || "治理实验详情加载失败。");
      } finally {
        this.busy.governanceExperimentLookup = false;
      }
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
            problem_statement: proposal.problem_statement
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
      if (status) {
        params.set("status", status);
      }
      return params.toString();
    },

    applyToolAuthorizationFilters() {
      return this.loadToolAuthorizations();
    },

    resetToolAuthorizationFilters() {
      this.toolAuthorizationFilters = { status: "pending" };
      return this.loadToolAuthorizations();
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
    },

    governanceProposalsPath() {
      return buildGovernanceProposalsPath();
    },

    governanceProposalPath(proposalId) {
      return buildGovernanceProposalPath(proposalId);
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

    governanceProposalTypeLabel(value) {
      return this.optionLabel(PROPOSAL_TYPE_OPTIONS, value);
    },

    governanceProposalStatusLabel(value) {
      return this.optionLabel(PROPOSAL_STATUS_OPTIONS, value);
    },

    toolAuthorizationStatusLabel(value) {
      return this.optionLabel(TOOL_AUTH_STATUS_OPTIONS, value);
    },

    governanceProposalSourceLabel(proposal) {
      const findingCount = Array.isArray(proposal?.source_finding_ids) ? proposal.source_finding_ids.length : 0;
      if (findingCount) {
        return `${findingCount} finding`;
      }
      return proposal?.source_run_id ? "Run evaluation" : "Manual";
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
