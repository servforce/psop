(function () {
  const {
    normalizePath,
    resolveAdminRoute,
    buildRunLivePath,
    buildSkillRunLivePath,
    buildSkillDebugRunLivePath,
    buildReplayPath,
    buildSkillReplayPath,
    buildSkillTestScenarioPath,
    buildSkillTestScenarioNewPath,
    buildSkillTestScenarioRunReviewPath,
    buildCompilerArtifactPath,
    buildSkillCompilerArtifactPath,
    generateSkillKey,
    resolveApiBaseUrl,
    resolveWsUrl,
    escapeHtml,
    highlightJson,
    highlightYamlScalar,
    highlightYaml,
    renderInlineMarkdown,
    renderMarkdown
  } = window.PSOPConsoleHelpers;

  window.PSOPConsoleCompilerMethods = {

      async loadCompilerRequests(skillId = null) {
        this.busy.compilerRequests = true;
        try {
          const [compilerRequests, skills] = await Promise.all([
            this.apiRequest(`/compiler/requests${skillId ? `?skill_id=${encodeURIComponent(skillId)}` : ""}`),
            this.apiRequest("/skills")
          ]);
          this.compilerRequests = compilerRequests;
          this.skills = skills;
        } finally {
          this.busy.compilerRequests = false;
        }
      },


      async startManualCompile() {
        if (!this.currentSkill) {
          return;
        }

        this.busy.manualCompile = true;
        this.clearNotice();
        try {
          const compileRequest = await this.apiRequest(`/compiler/skills/${this.currentSkill.id}/compile`, {
            method: "POST"
          });
          await this.loadCompilerRequests(this.currentSkill.id);
          this.showNotice("success", `编译任务已创建：${this.formatShortId(compileRequest.id)}`);
        } catch (error) {
          this.showNotice("error", error.message || "创建编译任务失败。");
        } finally {
          this.busy.manualCompile = false;
        }
      },


      async loadCompilerArtifact(artifactId) {
        this.busy.compilerArtifact = true;
        this.compilerArtifactGraphError = "";
        this.compilerArtifactGraphModel = null;
        this.selectedArtifactNodeId = "";
        this.closeCompilerArtifactNodeDrawer();
        try {
          this.compilerArtifact = await this.apiRequest(`/compiler/artifacts/${artifactId}`);
          this.compilerArtifactView = "graph";
          this.resetCompilerArtifactJsonDraft();
          this.queueCompilerArtifactGraphRender();
        } finally {
          this.busy.compilerArtifact = false;
        }
      },


      compilerArtifactPayload() {
        return this.compilerArtifact?.artifact || {};
      },


      compilerArtifactNodeCount() {
        const nodes = this.compilerArtifactPayload().nodes;
        return Array.isArray(nodes) ? nodes.length : 0;
      },


      compilerArtifactWorkflowCount() {
        const steps = this.compilerArtifactPayload().runtime_contract?.workflow_steps;
        return Array.isArray(steps) ? steps.length : 0;
      },


      compilerArtifactJsonText() {
        return this.formatJson(this.compilerArtifactPayload());
      },


      resetCompilerArtifactJsonDraft() {
        this.compilerArtifactJsonDraft = this.compilerArtifactJsonText();
        this.compilerArtifactJsonError = "";
      },


      parseCompilerArtifactJsonDraft() {
        try {
          const parsed = JSON.parse(this.compilerArtifactJsonDraft);
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
            throw new Error("EG artifact JSON 顶层必须是对象。");
          }
          this.compilerArtifactJsonError = "";
          return parsed;
        } catch (error) {
          this.compilerArtifactJsonError = error.message || "JSON 格式不正确。";
          return null;
        }
      },


      formatCompilerArtifactJsonDraft() {
        const parsed = this.parseCompilerArtifactJsonDraft();
        if (!parsed) {
          return;
        }
        this.compilerArtifactJsonDraft = this.formatJson(parsed);
      },


      async saveCompilerArtifactJson() {
        if (!this.compilerArtifact?.id) {
          return;
        }

        const parsed = this.parseCompilerArtifactJsonDraft();
        if (!parsed) {
          this.showCenterToast("error", "JSON 格式不正确，无法保存。");
          return;
        }

        this.busy.compilerArtifactSave = true;
        try {
          this.compilerArtifact = await this.apiRequest(`/compiler/artifacts/${this.compilerArtifact.id}`, {
            method: "PUT",
            body: JSON.stringify({ artifact: parsed })
          });
          this.resetCompilerArtifactJsonDraft();
          this.compilerArtifactGraphModel = null;
          this.selectedArtifactNodeId = "";
          this.closeCompilerArtifactNodeDrawer();
          this.showCenterToast("success", "EG artifact 已保存。");
        } catch (error) {
          const diagnostic = error.payload?.details?.diagnostics?.[0]?.message;
          this.compilerArtifactJsonError = diagnostic || error.message || "EG artifact 保存失败。";
          this.showCenterToast("error", this.compilerArtifactJsonError);
        } finally {
          this.busy.compilerArtifactSave = false;
        }
      },


      selectedArtifactNode() {
        const nodes = this.compilerArtifactPayload().nodes;
        if (!Array.isArray(nodes) || !this.selectedArtifactNodeId) {
          return null;
        }
        return nodes.find((node) => node?.id === this.selectedArtifactNodeId) || null;
      },


      selectedArtifactWorkflowStep() {
        const steps = this.compilerArtifactPayload().runtime_contract?.workflow_steps;
        if (!Array.isArray(steps) || !this.selectedArtifactNodeId) {
          return null;
        }
        return steps.find((step) => step?.id === this.selectedArtifactNodeId) || null;
      },


      selectedArtifactNodeJsonText() {
        return this.formatJson(this.selectedArtifactNode() || {});
      },


      emptyCompilerArtifactNodeForm() {
        return {
          id: "",
          kind: "",
          label: "",
          actor_name: "",
          workflow_title: "",
          workflow_goal: "",
          guard_phase_is: "",
          projection_system_template: "",
          projection_user_template: "",
          merge_path: "",
          merge_from: "",
          merge_value: ""
        };
      },


      resetCompilerArtifactNodeForm() {
        const node = this.selectedArtifactNode();
        if (!node) {
          this.compilerArtifactNodeForm = this.emptyCompilerArtifactNodeForm();
          return;
        }

        const workflowStep = this.selectedArtifactWorkflowStep();
        const guard = node.guard && typeof node.guard === "object" && !Array.isArray(node.guard) ? node.guard : {};
        const projection =
          node.projection && typeof node.projection === "object" && !Array.isArray(node.projection)
            ? node.projection
            : {};
        const mergeOperation = Array.isArray(node.merge) && node.merge.length > 0 ? node.merge[0] : {};
        this.compilerArtifactNodeForm = {
          id: node.id || "",
          kind: node.kind || "",
          label: node.label || "",
          actor_name: this.artifactNodeActorName(node) === "N/A" ? "" : this.artifactNodeActorName(node),
          workflow_title: workflowStep?.title || node.label || node.id || "",
          workflow_goal: workflowStep?.goal || "",
          guard_phase_is: typeof guard.phase_is === "string" ? guard.phase_is : "",
          projection_system_template:
            typeof projection.system_template === "string" ? projection.system_template : "",
          projection_user_template:
            typeof projection.user_template === "string" ? projection.user_template : "",
          merge_path: typeof mergeOperation.path === "string" ? mergeOperation.path : "",
          merge_from: typeof mergeOperation.from === "string" ? mergeOperation.from : "",
          merge_value:
            Object.prototype.hasOwnProperty.call(mergeOperation, "value")
              ? typeof mergeOperation.value === "string"
                ? mergeOperation.value
                : this.formatJson(mergeOperation.value)
              : ""
        };
      },


      resetCompilerArtifactNodeJsonDraft() {
        this.compilerArtifactNodeJsonDraft = this.selectedArtifactNodeJsonText();
        this.compilerArtifactNodeJsonError = "";
      },


      openCompilerArtifactNodeDrawer(nodeId) {
        this.selectedArtifactNodeId = nodeId;
        this.compilerArtifactNodeDrawerOpen = true;
        this.compilerArtifactNodeEditorTab = "form";
        this.resetCompilerArtifactNodeForm();
        this.resetCompilerArtifactNodeJsonDraft();
        this.syncCompilerArtifactGraphSelection();
      },


      closeCompilerArtifactNodeDrawer(clearSelection = true) {
        this.compilerArtifactNodeDrawerOpen = false;
        this.compilerArtifactNodeEditorTab = "form";
        this.compilerArtifactNodeForm = this.emptyCompilerArtifactNodeForm();
        this.compilerArtifactNodeJsonDraft = "";
        this.compilerArtifactNodeJsonError = "";
        if (clearSelection) {
          this.selectedArtifactNodeId = "";
          this.syncCompilerArtifactGraphSelection();
        }
      },


      selectCompilerArtifactNodeEditorTab(tabName) {
        this.compilerArtifactNodeEditorTab = tabName;
        if (tabName === "form") {
          this.resetCompilerArtifactNodeForm();
        }
        if (tabName === "json") {
          this.resetCompilerArtifactNodeJsonDraft();
        }
      },


      parseCompilerArtifactNodeJsonDraft() {
        try {
          const parsed = JSON.parse(this.compilerArtifactNodeJsonDraft);
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
            throw new Error("节点 JSON 顶层必须是对象。");
          }
          if (parsed.id !== this.selectedArtifactNodeId) {
            throw new Error("暂不支持修改节点 ID。");
          }
          this.compilerArtifactNodeJsonError = "";
          return parsed;
        } catch (error) {
          this.compilerArtifactNodeJsonError = error.message || "节点 JSON 格式不正确。";
          return null;
        }
      },


      formatCompilerArtifactNodeJsonDraft() {
        const parsed = this.parseCompilerArtifactNodeJsonDraft();
        if (!parsed) {
          return;
        }
        this.compilerArtifactNodeJsonDraft = this.formatJson(parsed);
      },


      async saveCompilerArtifactNode() {
        if (!this.compilerArtifact?.id || !this.selectedArtifactNodeId) {
          return;
        }

        const parsed = this.parseCompilerArtifactNodeJsonDraft();
        if (!parsed) {
          this.showCenterToast("error", "节点 JSON 格式不正确，无法保存。");
          return;
        }

        const nextArtifact = JSON.parse(JSON.stringify(this.compilerArtifactPayload()));
        if (!Array.isArray(nextArtifact.nodes)) {
          this.compilerArtifactNodeJsonError = "EG artifact 缺少 nodes 数组。";
          this.showCenterToast("error", this.compilerArtifactNodeJsonError);
          return;
        }

        const nodeIndex = nextArtifact.nodes.findIndex((node) => node?.id === this.selectedArtifactNodeId);
        if (nodeIndex < 0) {
          this.compilerArtifactNodeJsonError = "未找到当前节点。";
          this.showCenterToast("error", this.compilerArtifactNodeJsonError);
          return;
        }

        nextArtifact.nodes[nodeIndex] = parsed;
        this.busy.compilerArtifactSave = true;
        try {
          this.compilerArtifact = await this.apiRequest(`/compiler/artifacts/${this.compilerArtifact.id}`, {
            method: "PUT",
            body: JSON.stringify({ artifact: nextArtifact })
          });
          this.resetCompilerArtifactJsonDraft();
          this.resetCompilerArtifactNodeJsonDraft();
          this.compilerArtifactGraphModel = null;
          this.queueCompilerArtifactGraphRender();
          this.showCenterToast("success", "节点信息已保存。");
        } catch (error) {
          const diagnostic = error.payload?.details?.diagnostics?.[0]?.message;
          this.compilerArtifactNodeJsonError = diagnostic || error.message || "节点保存失败。";
          this.showCenterToast("error", this.compilerArtifactNodeJsonError);
        } finally {
          this.busy.compilerArtifactSave = false;
        }
      },


      parseMergeValue(value) {
        const trimmed = String(value || "").trim();
        if (!trimmed) {
          return "";
        }
        try {
          return JSON.parse(trimmed);
        } catch (_error) {
          return trimmed;
        }
      },


      async persistCompilerArtifactNode(nextArtifact, successMessage, errorSetter) {
        this.busy.compilerArtifactSave = true;
        try {
          this.compilerArtifact = await this.apiRequest(`/compiler/artifacts/${this.compilerArtifact.id}`, {
            method: "PUT",
            body: JSON.stringify({ artifact: nextArtifact })
          });
          this.resetCompilerArtifactJsonDraft();
          this.resetCompilerArtifactNodeForm();
          this.resetCompilerArtifactNodeJsonDraft();
          this.compilerArtifactGraphModel = null;
          this.queueCompilerArtifactGraphRender();
          this.showCenterToast("success", successMessage);
        } catch (error) {
          const diagnostic = error.payload?.details?.diagnostics?.[0]?.message;
          const message = diagnostic || error.message || "节点保存失败。";
          errorSetter(message);
          this.showCenterToast("error", message);
        } finally {
          this.busy.compilerArtifactSave = false;
        }
      },


      async saveCompilerArtifactNodeForm() {
        if (!this.compilerArtifact?.id || !this.selectedArtifactNodeId) {
          return;
        }

        const nextArtifact = JSON.parse(JSON.stringify(this.compilerArtifactPayload()));
        if (!Array.isArray(nextArtifact.nodes)) {
          this.compilerArtifactNodeJsonError = "EG artifact 缺少 nodes 数组。";
          this.showCenterToast("error", this.compilerArtifactNodeJsonError);
          return;
        }

        const nodeIndex = nextArtifact.nodes.findIndex((node) => node?.id === this.selectedArtifactNodeId);
        if (nodeIndex < 0) {
          this.compilerArtifactNodeJsonError = "未找到当前节点。";
          this.showCenterToast("error", this.compilerArtifactNodeJsonError);
          return;
        }

        const form = this.compilerArtifactNodeForm;
        const nextNode = { ...nextArtifact.nodes[nodeIndex] };
        nextNode.kind = String(form.kind || "").trim();
        nextNode.label = String(form.label || "").trim();

        const actorName = String(form.actor_name || "").trim();
        const currentActor = nextNode.actor;
        if (actorName) {
          nextNode.actor =
            currentActor && typeof currentActor === "object" && !Array.isArray(currentActor)
              ? { ...currentActor, name: actorName }
              : { name: actorName };
        }

        const guard =
          nextNode.guard && typeof nextNode.guard === "object" && !Array.isArray(nextNode.guard)
            ? { ...nextNode.guard }
            : {};
        const phase = String(form.guard_phase_is || "").trim();
        if (phase) {
          guard.phase_is = phase;
        } else {
          delete guard.phase_is;
        }
        nextNode.guard = guard;

        const projection =
          nextNode.projection && typeof nextNode.projection === "object" && !Array.isArray(nextNode.projection)
            ? { ...nextNode.projection }
            : {};
        const systemTemplate = String(form.projection_system_template || "");
        const userTemplate = String(form.projection_user_template || "");
        if (systemTemplate.trim()) {
          projection.system_template = systemTemplate;
        } else {
          delete projection.system_template;
        }
        if (userTemplate.trim()) {
          projection.user_template = userTemplate;
        } else {
          delete projection.user_template;
        }
        nextNode.projection = projection;

        const mergePath = String(form.merge_path || "").trim();
        const mergeFrom = String(form.merge_from || "").trim();
        const mergeValue = String(form.merge_value || "").trim();
        const nextMerge = Array.isArray(nextNode.merge) ? [...nextNode.merge] : [];
        if (mergePath || mergeFrom || mergeValue) {
          const operation =
            nextMerge[0] && typeof nextMerge[0] === "object" && !Array.isArray(nextMerge[0])
              ? { ...nextMerge[0] }
              : {};
          operation.op = operation.op || "set";
          operation.path = mergePath;
          if (mergeFrom) {
            operation.from = mergeFrom;
            delete operation.value;
          } else {
            operation.value = this.parseMergeValue(mergeValue);
            delete operation.from;
          }
          nextMerge[0] = operation;
          nextNode.merge = nextMerge;
        }

        nextArtifact.nodes[nodeIndex] = nextNode;
        nextArtifact.runtime_contract =
          nextArtifact.runtime_contract && typeof nextArtifact.runtime_contract === "object"
            ? nextArtifact.runtime_contract
            : {};
        const steps = Array.isArray(nextArtifact.runtime_contract.workflow_steps)
          ? [...nextArtifact.runtime_contract.workflow_steps]
          : [];
        let stepIndex = steps.findIndex((step) => step?.id === this.selectedArtifactNodeId);
        const workflowTitle = String(form.workflow_title || "").trim();
        const workflowGoal = String(form.workflow_goal || "").trim();
        const shouldWriteWorkflowStep =
          stepIndex >= 0 ||
          Boolean(workflowGoal) ||
          (Boolean(workflowTitle) && workflowTitle !== (nextNode.label || nextNode.id || ""));
        if (shouldWriteWorkflowStep && stepIndex < 0) {
          stepIndex = steps.length;
          steps.push({ id: this.selectedArtifactNodeId });
        }
        if (shouldWriteWorkflowStep) {
          const nextStep = {
            ...steps[stepIndex],
            id: this.selectedArtifactNodeId,
            title: workflowTitle,
            goal: workflowGoal
          };
          steps[stepIndex] = nextStep;
          nextArtifact.runtime_contract.workflow_steps = steps;
        }

        await this.persistCompilerArtifactNode(
          nextArtifact,
          "节点表单已保存。",
          (message) => {
            this.compilerArtifactNodeJsonError = message;
          }
        );
      },


      artifactNodeActorName(node) {
        const actor = node?.actor;
        if (typeof actor === "string") {
          return actor;
        }
        if (!actor || typeof actor !== "object") {
          return "N/A";
        }
        return actor.name || actor.type || "N/A";
      },


      artifactNodeKindClass(kind) {
        const classes = {
          start: "border-orange-500/30 bg-orange-500/10 text-orange-200",
          input: "border-violet-500/30 bg-violet-500/10 text-violet-200",
          llm: "border-sky-500/30 bg-sky-500/10 text-sky-200",
          tool: "border-amber-500/30 bg-amber-500/10 text-amber-200",
          terminal: "border-rose-500/30 bg-rose-500/10 text-rose-200"
        };
        return classes[kind] || "border-slate-700 bg-slate-900 text-slate-300";
      },


      queueCompilerArtifactGraphRender() {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => this.renderCompilerArtifactGraph());
        });
      },


      compilerArtifactCanvasId() {
        return "eg-bpmn-canvas";
      },


      async renderCompilerArtifactGraph() {
        const canRender = ["compiler-artifact", "skill-compiler-artifact"].includes(this.route.name);
        if (!canRender || this.compilerArtifactView !== "graph" || !this.compilerArtifact) {
          return;
        }

        const mount = document.getElementById(this.compilerArtifactCanvasId());
        if (!mount) {
          return;
        }

        const converter = window.PSOPEgBpmn;
        const BpmnViewer = window.BpmnJS;
        if (!converter || !BpmnViewer) {
          this.compilerArtifactGraphError = "BPMN viewer 资源未加载。";
          return;
        }

        try {
          const { xml, viewModel } = converter.buildBpmnXml(this.compilerArtifactPayload());
          this.compilerArtifactGraphModel = viewModel;

          this.destroyCompilerArtifactViewer();
          this.bpmnViewer = new BpmnViewer({ container: mount });
          await this.bpmnViewer.importXML(xml);

          const canvas = this.bpmnViewer.get("canvas");
          const eventBus = this.bpmnViewer.get("eventBus");
          for (const node of viewModel.nodes) {
            canvas.addMarker(node.bpmnId, `eg-kind-${node.kind}`);
            if (node.id === this.selectedArtifactNodeId) {
              canvas.addMarker(node.bpmnId, "eg-node-selected");
            }
          }
          eventBus.on("element.click", (event) => {
            const nodeId = viewModel.bpmnIdToNodeId[event.element.id];
            if (!nodeId) {
              return;
            }
            this.openCompilerArtifactNodeDrawer(nodeId);
          });
          canvas.zoom("fit-viewport", "auto");
          this.compilerArtifactGraphError = "";
        } catch (error) {
          this.compilerArtifactGraphError = error.message || "EG 图预览渲染失败。";
        }
      },


      syncCompilerArtifactGraphSelection() {
        if (!this.bpmnViewer || !this.compilerArtifactGraphModel) {
          return;
        }
        const canvas = this.bpmnViewer.get("canvas");
        for (const node of this.compilerArtifactGraphModel.nodes) {
          canvas.removeMarker(node.bpmnId, "eg-node-selected");
          if (node.id === this.selectedArtifactNodeId) {
            canvas.addMarker(node.bpmnId, "eg-node-selected");
          }
        }
      },


      selectCompilerArtifactView(viewName) {
        this.compilerArtifactView = viewName;
        if (viewName === "graph") {
          this.queueCompilerArtifactGraphRender();
        } else {
          this.closeCompilerArtifactNodeDrawer();
          if (viewName === "json" && !this.compilerArtifactJsonDraft) {
            this.resetCompilerArtifactJsonDraft();
          }
        }
      },


      destroyCompilerArtifactViewer() {
        if (this.bpmnViewer) {
          this.bpmnViewer.destroy();
          this.bpmnViewer = null;
        }
      },


      resetCompilerArtifactState() {
        this.destroyCompilerArtifactViewer();
        this.compilerArtifact = null;
        this.compilerArtifactGraphModel = null;
        this.compilerArtifactGraphError = "";
        this.compilerArtifactJsonDraft = "";
        this.compilerArtifactJsonError = "";
        this.selectedArtifactNodeId = "";
        this.closeCompilerArtifactNodeDrawer();
      },


      async openCompilerArtifact(artifactId) {
        if (!artifactId) {
          return;
        }
        if (this.currentSkill && this.activeDetailTab === "compiler") {
          await this.navigate(buildSkillCompilerArtifactPath(this.currentSkill.id, artifactId));
          return;
        }
        await this.navigate(buildCompilerArtifactPath(artifactId));
      },


      skillForCompileRequest(compileRequest) {
        return this.skills.find((skill) => skill.id === compileRequest.skill_definition_id) || null;
      },


      skillNameForCompileRequest(compileRequest) {
        if (this.currentSkill?.id === compileRequest.skill_definition_id) {
          return this.currentSkill.name;
        }
        return this.skillForCompileRequest(compileRequest)?.name || "未知 Skill";
      },


      currentSkillCompilerRequests() {
        if (!this.currentSkill) {
          return [];
        }
        return this.compilerRequests.filter((compileRequest) => compileRequest.skill_definition_id === this.currentSkill.id);
      },


      currentSkillFilteredCompilerRequests() {
        return this.currentSkillCompilerRequests().filter((compileRequest) => {
          const statusMatched =
            !this.skillCompilerFilters.status || compileRequest.status === this.skillCompilerFilters.status;
          return (
            statusMatched &&
            this.inDateRange(
              compileRequest.requested_at,
              this.skillCompilerFilters.requested_from,
              this.skillCompilerFilters.requested_to
            )
          );
        });
      },


      filteredCompilerRequests() {
        return this.compilerRequests.filter((compileRequest) => {
          const skillQuery = this.compilerFilters.skill_search.trim().toLowerCase();
          const skillName = this.skillNameForCompileRequest(compileRequest).toLowerCase();
          const skillMatched = !skillQuery || skillName.includes(skillQuery);
          const statusMatched = !this.compilerFilters.status || compileRequest.status === this.compilerFilters.status;

          return (
            skillMatched &&
            statusMatched &&
            this.inDateRange(
              compileRequest.requested_at,
              this.compilerFilters.requested_from,
              this.compilerFilters.requested_to
            )
          );
        });
      },


      clearCompilerFilters() {
        this.compilerFilters = {
          skill_search: "",
          status: "",
          requested_from: "",
          requested_to: ""
        };
      },


      hasActiveCompilerFilters() {
        return Object.values(this.compilerFilters).some((value) => Boolean(String(value || "").trim()));
      },
  };
})();
