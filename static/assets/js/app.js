(function () {
  function normalizePath(pathname) {
    if (!pathname || pathname === "/") {
      return "/";
    }

    return pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  }

  function resolveAdminRoute(pathname) {
    const normalized = normalizePath(pathname);
    if (normalized === "/" || normalized === "/admin" || normalized === "/admin/skills") {
      return { name: "skills-list", params: {} };
    }

    const detailMatch = normalized.match(/^\/admin\/skills\/([^/]+)$/);
    if (detailMatch) {
      return {
        name: "skill-detail",
        params: { skillId: detailMatch[1] }
      };
    }

    if (normalized === "/admin/compiler") {
      return { name: "compiler-list", params: {} };
    }

    const compilerArtifactMatch = normalized.match(/^\/admin\/compiler\/artifacts\/([^/]+)$/);
    if (compilerArtifactMatch) {
      return {
        name: "compiler-artifact",
        params: { artifactId: compilerArtifactMatch[1] }
      };
    }

    if (normalized === "/admin/invocations") {
      return { name: "invocations-list", params: {} };
    }

    const runLiveMatch = normalized.match(/^\/admin\/runs\/([^/]+)\/live$/);
    if (runLiveMatch) {
      return { name: "run-live", params: { runId: runLiveMatch[1] } };
    }

    if (normalized === "/admin/replay") {
      return { name: "replay-list", params: {} };
    }

    const replayRunMatch = normalized.match(/^\/admin\/replay\/runs\/([^/]+)$/);
    if (replayRunMatch) {
      return { name: "replay-detail", params: { runId: replayRunMatch[1] } };
    }

    return { name: "skills-list", params: {} };
  }

  function buildSkillDetailPath(skillId) {
    return `/admin/skills/${skillId}`;
  }

  function buildRunLivePath(runId) {
    return `/admin/runs/${runId}/live`;
  }

  function buildReplayPath(runId) {
    return `/admin/replay/runs/${runId}`;
  }

  function buildCompilerArtifactPath(artifactId) {
    return `/admin/compiler/artifacts/${artifactId}`;
  }

  function generateSkillKey(name) {
    return window.PSOPSkillKey.generateSkillKey(name);
  }

  function resolveApiBaseUrl() {
    if (window.__PSOP_API_BASE_URL) {
      return window.__PSOP_API_BASE_URL;
    }

    if (window.location.port === "4173") {
      return "http://127.0.0.1:8001/api/v1";
    }

    return "/api/v1";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function highlightJson(value) {
    const text = String(value ?? "");
    if (!text) {
      return "";
    }

    const tokenPattern =
      /("(?:\\u[a-fA-F0-9]{4}|\\["\\/bfnrt]|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
    let html = "";
    let lastIndex = 0;
    let match = tokenPattern.exec(text);
    while (match) {
      const token = match[0];
      html += escapeHtml(text.slice(lastIndex, match.index));

      let tokenClass = "json-token-number";
      if (token.startsWith("\"")) {
        tokenClass = /:\s*$/.test(token) ? "json-token-key" : "json-token-string";
      } else if (token === "true" || token === "false") {
        tokenClass = "json-token-boolean";
      } else if (token === "null") {
        tokenClass = "json-token-null";
      }

      html += `<span class="${tokenClass}">${escapeHtml(token)}</span>`;
      lastIndex = match.index + token.length;
      match = tokenPattern.exec(text);
    }

    html += escapeHtml(text.slice(lastIndex));
    return html;
  }

  function renderInlineMarkdown(value) {
    return escapeHtml(value)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>'
      );
  }

  function renderMarkdown(value) {
    const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let inCodeBlock = false;
    let codeLines = [];
    let listType = null;

    function closeList() {
      if (listType) {
        html.push(`</${listType}>`);
        listType = null;
      }
    }

    function closeCodeBlock() {
      html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      codeLines = [];
      inCodeBlock = false;
    }

    for (const line of lines) {
      if (line.trim().startsWith("```")) {
        if (inCodeBlock) {
          closeCodeBlock();
        } else {
          closeList();
          inCodeBlock = true;
          codeLines = [];
        }
        continue;
      }

      if (inCodeBlock) {
        codeLines.push(line);
        continue;
      }

      const trimmed = line.trim();
      if (!trimmed) {
        closeList();
        continue;
      }

      const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        closeList();
        const level = heading[1].length;
        html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        continue;
      }

      const quote = trimmed.match(/^>\s?(.+)$/);
      if (quote) {
        closeList();
        html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
        continue;
      }

      const unordered = trimmed.match(/^[-*]\s+(.+)$/);
      if (unordered) {
        if (listType !== "ul") {
          closeList();
          listType = "ul";
          html.push("<ul>");
        }
        html.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
        continue;
      }

      const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
      if (ordered) {
        if (listType !== "ol") {
          closeList();
          listType = "ol";
          html.push("<ol>");
        }
        html.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
        continue;
      }

      closeList();
      html.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
    }

    if (inCodeBlock) {
      closeCodeBlock();
    }
    closeList();

    return html.join("");
  }

  function createInitialState() {
    return {
      apiBaseUrl: resolveApiBaseUrl(),
      route: { name: "skills-list", params: {} },
      sidebarCollapsed: false,
      createModalOpen: false,
      publishDrawerOpen: false,
      publishWorkspaceOpen: false,
      deleteModalOpen: false,
      deleteTargetSkill: null,
      loadingPage: false,
      skills: [],
      currentSkill: null,
      activeDetailTab: "overview",
      sourceLoadedSkillId: null,
      repositoryLoadedSkillId: null,
      repositoryPath: "",
      repositoryEntries: [],
      selectedRepositoryFile: null,
      repositoryEditing: false,
      publishRecordsLoadedSkillId: null,
      publishRecords: [],
      publishEventSource: null,
      publishPollTimer: null,
      publishProgress: {
        active: false,
        compile_request_id: null,
        terminal: false,
        terminal_status: null,
        error_message: "",
        stages: []
      },
      compilerRequests: [],
      compilerArtifact: null,
      compilerArtifactView: "graph",
      compilerArtifactGraphError: "",
      compilerArtifactGraphModel: null,
      compilerArtifactJsonDraft: "",
      compilerArtifactJsonError: "",
      selectedArtifactNodeId: "",
      compilerArtifactNodeDrawerOpen: false,
      compilerArtifactNodeEditorTab: "form",
      compilerArtifactNodeForm: {
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
      },
      compilerArtifactNodeJsonDraft: "",
      compilerArtifactNodeJsonError: "",
      bpmnViewer: null,
      compilerArtifactWorkspaceOpen: false,
      compilerFilters: {
        skill_search: "",
        status: "",
        requested_from: "",
        requested_to: ""
      },
      publishFilters: {
        status: "",
        published_from: "",
        published_to: ""
      },
      skillCompilerFilters: {
        status: "",
        requested_from: "",
        requested_to: ""
      },
      runtimeFilters: {
        created_from: "",
        created_to: ""
      },
      invocations: [],
      replayRuns: [],
      liveRun: null,
      liveRunTraceEvents: [],
      replayDetail: null,
      invocationForm: {
        skill_key: "",
        user_input: ""
      },
      copyFeedback: {},
      centerToast: null,
      centerToastTimer: null,
      notice: null,
      createForm: {
        name: "",
        description: ""
      },
      deleteForm: {
        confirmation_name: ""
      },
      filters: {
        search: "",
        status: "",
        created_from: "",
        created_to: "",
        published_from: "",
        published_to: ""
      },
      metadataForm: {
        name: "",
        description: ""
      },
      sourceForm: {
        readme_content: "",
        skill_md_content: "",
        skill_yaml_content: "",
        base_commit_sha: ""
      },
      repositoryFileForm: {
        path: "",
        content: "",
        base_commit_sha: ""
      },
      sourceCreateModalOpen: false,
      sourceActionMenuOpen: false,
      sourceCreateMode: "file",
      sourceCreateForm: {
        path: "",
        content: ""
      },
      publishForm: {
        publish_reason: ""
      },
      activeSourceTab: "skill.yaml",
      busy: {
        list: false,
        create: false,
        detail: false,
        metadata: false,
        source: false,
        repositoryTree: false,
        repositoryFile: false,
        repositorySave: false,
        repositoryCreate: false,
        publishRecords: false,
        publish: false,
        delete: false,
        compilerRequests: false,
        compilerArtifact: false,
        compilerArtifactSave: false,
        manualCompile: false,
        invocations: false,
        createInvocation: false,
        liveRun: false,
        replayRuns: false,
        replayDetail: false
      }
    };
  }

  function createSkillsConsole() {
    return {
      ...createInitialState(),

      async boot() {
        try {
          await this.loadPageFragments();
        } catch (error) {
          this.showNotice("error", error.message || "页面片段加载失败。");
        }

        this.syncRoute();
        window.addEventListener("popstate", async () => {
          this.syncRoute();
          await this.loadCurrentRoute();
        });
        await this.loadCurrentRoute();
      },

      async loadPageFragments() {
        const fragments = [
          ["skills-list-page", "/pages/skills-list.html"],
          ["skill-detail-page", "/pages/skill-detail.html"],
          ["compiler-list-page", "/pages/compiler-list.html"],
          ["compiler-artifact-page", "/pages/compiler-artifact-detail.html"],
          ["invocations-list-page", "/pages/invocations-list.html"],
          ["run-live-page", "/pages/run-live.html"],
          ["replay-list-page", "/pages/replay-list.html"],
          ["replay-detail-page", "/pages/replay-detail.html"],
          ["create-skill-modal-page", "/pages/create-skill-modal.html"],
          ["publish-skill-drawer-page", "/pages/publish-skill-drawer.html"],
          ["delete-skill-modal-page", "/pages/delete-skill-modal.html"]
        ];

        await Promise.all(
          fragments.map(async ([elementId, fragmentPath]) => {
            const element = document.getElementById(elementId);
            if (!element) {
              throw new Error(`页面挂载点不存在：${elementId}`);
            }

            const response = await fetch(fragmentPath);
            if (!response.ok) {
              throw new Error(`页面片段加载失败：${fragmentPath}`);
            }

            element.innerHTML = await response.text();
            window.Alpine.initTree(element);
          })
        );
      },

      syncRoute() {
        this.route = resolveAdminRoute(window.location.pathname);
      },

      async navigate(pathname) {
        if (pathname !== window.location.pathname) {
          window.history.pushState({}, "", pathname);
        }
        this.syncRoute();
        await this.loadCurrentRoute();
      },

      toggleSidebar() {
        this.sidebarCollapsed = !this.sidebarCollapsed;
      },

      openCreateModal() {
        this.createForm = { name: "", description: "" };
        this.createModalOpen = true;
      },

      closeCreateModal() {
        if (this.busy.create) {
          return;
        }

        this.createModalOpen = false;
      },

      openPublishDrawer() {
        if (!this.currentSkill) {
          return;
        }

        this.stopPublishProgressWatchers();
        this.publishForm = { publish_reason: "" };
        this.publishProgress = this.emptyPublishProgress();
        this.publishDrawerOpen = false;
        this.publishWorkspaceOpen = true;
      },

      closePublishDrawer() {
        if (this.busy.publish || this.isPublishInProgress()) {
          return;
        }

        this.stopPublishProgressWatchers();
        this.publishDrawerOpen = false;
        this.publishWorkspaceOpen = false;
      },

      openDeleteModal(skill) {
        this.deleteTargetSkill = skill;
        this.deleteForm = { confirmation_name: "" };
        this.deleteModalOpen = true;
      },

      closeDeleteModal() {
        if (this.busy.delete) {
          return;
        }

        this.deleteModalOpen = false;
        this.deleteTargetSkill = null;
        this.deleteForm = { confirmation_name: "" };
      },

      async loadCurrentRoute() {
        this.loadingPage = true;
        this.clearNotice();
        if (this.route.name !== "compiler-artifact") {
          this.destroyCompilerArtifactViewer();
          this.compilerArtifact = null;
          this.compilerArtifactGraphModel = null;
          this.selectedArtifactNodeId = "";
          this.closeCompilerArtifactNodeDrawer();
        }

        try {
          if (this.route.name === "skills-list") {
            this.currentSkill = null;
            this.activeDetailTab = "overview";
            this.resetLazyDetailState();
            await this.loadSkills();
            return;
          }

          if (this.route.name === "skill-detail") {
            await this.loadSkillDetail(this.route.params.skillId);
            return;
          }

          if (this.route.name === "compiler-list") {
            this.currentSkill = null;
            await this.loadCompilerRequests();
            return;
          }

          if (this.route.name === "compiler-artifact") {
            this.currentSkill = null;
            await this.loadCompilerArtifact(this.route.params.artifactId);
            return;
          }

          if (this.route.name === "invocations-list") {
            this.currentSkill = null;
            await Promise.all([this.loadSkills(), this.loadInvocations()]);
            if (!this.invocationForm.skill_key && this.skills.length > 0) {
              this.invocationForm.skill_key = this.skills[0].key;
            }
            return;
          }

          if (this.route.name === "run-live") {
            this.currentSkill = null;
            await this.loadRunLive(this.route.params.runId);
            return;
          }

          if (this.route.name === "replay-list") {
            this.currentSkill = null;
            await this.loadReplayRuns();
            return;
          }

          if (this.route.name === "replay-detail") {
            this.currentSkill = null;
            await this.loadReplayDetail(this.route.params.runId);
          }
        } catch (error) {
          this.showNotice("error", error.message || "页面加载失败。");
        } finally {
          this.loadingPage = false;
        }
      },

      async apiRequest(pathname, options) {
        const response = await fetch(`${this.apiBaseUrl}${pathname}`, {
          headers: {
            "Content-Type": "application/json"
          },
          ...options
        });

        if (!response.ok) {
          let payload;
          try {
            payload = await response.json();
          } catch {
            payload = null;
          }

          const message =
            payload?.message ||
            payload?.detail ||
            `请求失败（${response.status}）`;
          const error = new Error(message);
          error.payload = payload;
          throw error;
        }

        if (response.status === 204) {
          return null;
        }

        return response.json();
      },

      async loadSkills() {
        this.busy.list = true;
        try {
          const params = new URLSearchParams();
          if (this.filters.search.trim()) {
            params.set("search", this.filters.search.trim());
          }
          if (this.filters.status) {
            params.set("status", this.filters.status);
          }
          const suffix = params.toString() ? `?${params}` : "";
          this.skills = await this.apiRequest(`/skills${suffix}`);
        } finally {
          this.busy.list = false;
        }
      },

      async createSkill() {
        this.busy.create = true;
        this.clearNotice();

        try {
          const payload = {
            ...this.createForm,
            key: generateSkillKey(this.createForm.name)
          };
          const created = await this.apiRequest("/skills", {
            method: "POST",
            body: JSON.stringify(payload)
          });
          this.createForm = { name: "", description: "" };
          this.createModalOpen = false;
          await this.navigate(buildSkillDetailPath(created.id));
          this.showNotice("success", "Skill 已创建，并已在 GitLab 中初始化。");
        } catch (error) {
          this.showNotice("error", error.message || "创建 Skill 失败。");
        } finally {
          this.busy.create = false;
        }
      },

      async deleteSkill() {
        if (!this.deleteTargetSkill || !this.isDeleteConfirmationValid()) {
          return;
        }

        const deletedSkill = this.deleteTargetSkill;
        this.busy.delete = true;
        this.clearNotice();

        try {
          await this.apiRequest(`/skills/${deletedSkill.id}`, {
            method: "DELETE",
            body: JSON.stringify(this.deleteForm)
          });
          this.deleteModalOpen = false;
          this.deleteTargetSkill = null;
          this.deleteForm = { confirmation_name: "" };

          if (this.currentSkill?.id === deletedSkill.id) {
            await this.navigate("/admin/skills");
          } else {
            await this.loadSkills();
          }

          this.showCenterToast("success", "Skill 已删除，对应 GitLab 仓库项目已归档。");
        } catch (error) {
          this.showCenterToast("error", error.message || "删除 Skill 失败。");
        } finally {
          this.busy.delete = false;
        }
      },

      async loadSkillDetail(skillId) {
        this.busy.detail = true;
        try {
          const detail = await this.apiRequest(`/skills/${skillId}`);

          this.currentSkill = detail;
          this.metadataForm = {
            name: detail.name,
            description: detail.description
          };
          this.resetLazyDetailState(skillId);
          if (!["overview", "source", "publish", "compiler", "runtime"].includes(this.activeDetailTab)) {
            this.activeDetailTab = "overview";
          }
          if (this.activeDetailTab === "compiler") {
            await this.loadCompilerRequests(detail.id);
          }
          if (this.activeDetailTab === "runtime") {
            this.invocationForm.skill_key = detail.key;
            await this.loadInvocations(detail.key);
          }
        } finally {
          this.busy.detail = false;
        }
      },

      resetLazyDetailState(skillId) {
        this.sourceLoadedSkillId = null;
        this.repositoryLoadedSkillId = null;
        this.repositoryPath = "";
        this.repositoryEntries = [];
        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.publishRecordsLoadedSkillId = null;
        this.publishRecords = [];
        this.sourceForm = {
          readme_content: "",
          skill_md_content: "",
          skill_yaml_content: "",
          base_commit_sha: ""
        };
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
        this.sourceCreateModalOpen = false;
        this.sourceActionMenuOpen = false;
        this.sourceCreateMode = "file";
        this.sourceCreateForm = {
          path: "",
          content: ""
        };
        this.closeCompilerArtifactWorkspace();
        if (skillId) {
          this.activeSourceTab = "skill.yaml";
        }
      },

      async loadSkillSource(skillId) {
        if (this.sourceLoadedSkillId === skillId) {
          return;
        }

        this.busy.source = true;
        try {
          const source = await this.apiRequest(`/skills/${skillId}/source`);
          this.sourceForm = {
            readme_content: source.readme_content,
            skill_md_content: source.skill_md_content,
            skill_yaml_content: source.skill_yaml_content,
            base_commit_sha: source.head_commit_sha
          };
          this.sourceLoadedSkillId = skillId;
        } finally {
          this.busy.source = false;
        }
      },

      async loadPublishRecords(skillId) {
        if (this.publishRecordsLoadedSkillId === skillId) {
          return;
        }

        this.busy.publishRecords = true;
        try {
          this.publishRecords = await this.apiRequest(`/skills/${skillId}/publishes`);
          this.publishRecordsLoadedSkillId = skillId;
        } finally {
          this.busy.publishRecords = false;
        }
      },

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

      isSkillCompilerArtifactWorkspace() {
        return (
          this.route.name === "skill-detail" &&
          this.activeDetailTab === "compiler" &&
          this.compilerArtifactWorkspaceOpen
        );
      },

      compilerArtifactCanvasId() {
        return this.isSkillCompilerArtifactWorkspace() ? "skill-eg-bpmn-canvas" : "eg-bpmn-canvas";
      },

      async renderCompilerArtifactGraph() {
        const canRender =
          this.route.name === "compiler-artifact" || this.isSkillCompilerArtifactWorkspace();
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

      closeCompilerArtifactWorkspace() {
        this.destroyCompilerArtifactViewer();
        this.compilerArtifactWorkspaceOpen = false;
        this.compilerArtifact = null;
        this.compilerArtifactGraphModel = null;
        this.compilerArtifactGraphError = "";
        this.compilerArtifactJsonDraft = "";
        this.compilerArtifactJsonError = "";
        this.selectedArtifactNodeId = "";
        this.closeCompilerArtifactNodeDrawer();
      },

      async loadInvocations(skillKey = null) {
        this.busy.invocations = true;
        try {
          const suffix = skillKey ? `?skill_key=${encodeURIComponent(skillKey)}` : "";
          this.invocations = await this.apiRequest(`/gateway/invocations${suffix}`);
        } finally {
          this.busy.invocations = false;
        }
      },

      async createInvocation() {
        if (!this.invocationForm.skill_key || !this.invocationForm.user_input.trim()) {
          this.showNotice("error", "请选择 Skill 并填写运行输入。");
          return;
        }

        this.busy.createInvocation = true;
        this.clearNotice();
        try {
          const invocation = await this.apiRequest("/gateway/invocations", {
            method: "POST",
            body: JSON.stringify({
              skill_key: this.invocationForm.skill_key,
              gateway_type: "web",
              input_envelope: {
                user_input: this.invocationForm.user_input.trim()
              }
            })
          });
          this.invocationForm.user_input = "";
          await this.loadInvocations(this.route.name === "skill-detail" ? this.currentSkill?.key : null);
          if (invocation.run_id) {
            await this.navigate(buildRunLivePath(invocation.run_id));
          }
        } catch (error) {
          this.showNotice("error", error.message || "发起运行失败。");
        } finally {
          this.busy.createInvocation = false;
        }
      },

      async loadRunLive(runId) {
        this.busy.liveRun = true;
        try {
          const [run, traceEvents] = await Promise.all([
            this.apiRequest(`/runs/${runId}`),
            this.apiRequest(`/runs/${runId}/trace-events`)
          ]);
          this.liveRun = run;
          this.liveRunTraceEvents = traceEvents;
        } finally {
          this.busy.liveRun = false;
        }
      },

      async loadReplayRuns() {
        this.busy.replayRuns = true;
        try {
          this.replayRuns = await this.apiRequest("/replay/runs");
        } finally {
          this.busy.replayRuns = false;
        }
      },

      async loadReplayDetail(runId) {
        this.busy.replayDetail = true;
        try {
          this.replayDetail = await this.apiRequest(`/replay/runs/${runId}`);
        } finally {
          this.busy.replayDetail = false;
        }
      },

      async loadRepositoryTree(skillId, path = this.repositoryPath || "") {
        this.busy.repositoryTree = true;
        try {
          const params = new URLSearchParams();
          const normalizedPath = this.normalizeRepositoryPath(path, true);
          if (normalizedPath) {
            params.set("path", normalizedPath);
          }
          const suffix = params.toString() ? `?${params}` : "";
          const tree = await this.apiRequest(`/skills/${skillId}/repository/tree${suffix}`);
          this.repositoryPath = tree.path || "";
          this.repositoryEntries = tree.entries || [];
          this.repositoryLoadedSkillId = skillId;
          await this.ensureDefaultRepositoryPreview(skillId);
        } finally {
          this.busy.repositoryTree = false;
        }
      },

      async ensureDefaultRepositoryPreview(skillId) {
        if (this.selectedRepositoryFile || this.repositoryPath) {
          return;
        }

        const readmeEntry = this.repositoryEntries.find(
          (entry) => entry.type === "blob" && entry.name.toLowerCase() === "readme.md"
        );
        if (readmeEntry) {
          await this.loadRepositoryFile(readmeEntry.path);
        }
      },

      async openRepositoryFolder(entry) {
        if (!this.currentSkill || entry.type !== "tree") {
          return;
        }

        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
        await this.loadRepositoryTree(this.currentSkill.id, entry.path);
      },

      async openRepositoryPath(path) {
        if (!this.currentSkill) {
          return;
        }

        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
        await this.loadRepositoryTree(this.currentSkill.id, path);
      },

      async openRepositoryFile(entry) {
        if (!this.currentSkill || entry.type !== "blob") {
          return;
        }

        await this.loadRepositoryFile(entry.path);
      },

      async loadRepositoryFile(path) {
        if (!this.currentSkill) {
          return;
        }

        this.busy.repositoryFile = true;
        try {
          const params = new URLSearchParams({ path });
          const file = await this.apiRequest(`/skills/${this.currentSkill.id}/repository/files?${params}`);
          this.selectedRepositoryFile = file;
          this.repositoryEditing = false;
          this.repositoryFileForm = {
            path: file.file_path,
            content: file.content,
            base_commit_sha: file.head_commit_sha
          };
        } finally {
          this.busy.repositoryFile = false;
        }
      },

      closeRepositoryFile() {
        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
      },

      startRepositoryEdit() {
        if (!this.selectedRepositoryFile) {
          return;
        }
        if (this.isSystemManifestFile()) {
          this.showCenterToast("info", "skill.yaml 为系统生成预览，请通过结构化配置修改。");
          return;
        }
        this.repositoryEditing = true;
      },

      cancelRepositoryEdit() {
        if (!this.selectedRepositoryFile) {
          return;
        }
        this.repositoryFileForm.content = this.selectedRepositoryFile.content;
        this.repositoryEditing = false;
      },

      async saveRepositoryFile() {
        if (!this.currentSkill || !this.repositoryFileForm.path) {
          return;
        }

        const skillId = this.currentSkill.id;
        const currentPath = this.repositoryPath;
        const filePath = this.repositoryFileForm.path;
        this.busy.repositorySave = true;
        this.clearNotice();

        try {
          const saved = await this.apiRequest(`/skills/${skillId}/repository/files`, {
            method: "PUT",
            body: JSON.stringify(this.repositoryFileForm)
          });
          await this.loadSkillDetail(skillId);
          await this.loadRepositoryTree(skillId, currentPath);
          await this.loadRepositoryFile(saved.file_path || filePath);
          this.repositoryEditing = false;
          this.showNotice("success", "文件已提交到 GitLab。");
        } catch (error) {
          this.showNotice("error", error.message || "保存文件失败。");
        } finally {
          this.busy.repositorySave = false;
        }
      },

      openSourceCreateModal(mode) {
        this.sourceCreateMode = mode;
        this.sourceCreateForm = {
          path: this.repositoryPath ? `${this.repositoryPath}/` : "",
          content: ""
        };
        this.sourceActionMenuOpen = false;
        this.sourceCreateModalOpen = true;
      },

      toggleSourceActionMenu() {
        this.sourceActionMenuOpen = !this.sourceActionMenuOpen;
      },

      closeSourceActionMenu() {
        this.sourceActionMenuOpen = false;
      },

      closeSourceCreateModal() {
        if (this.busy.repositoryCreate) {
          return;
        }

        this.sourceCreateModalOpen = false;
        this.sourceCreateForm = {
          path: "",
          content: ""
        };
      },

      async createRepositoryEntry() {
        if (!this.currentSkill) {
          return;
        }

        const skillId = this.currentSkill.id;
        const currentPath = this.repositoryPath;
        const mode = this.sourceCreateMode;
        const path = this.normalizeRepositoryPath(this.sourceCreateForm.path);
        if (!path) {
          this.showNotice("error", "请填写仓库路径。");
          return;
        }

        this.busy.repositoryCreate = true;
        this.clearNotice();

        try {
          const endpoint =
            mode === "folder"
              ? `/skills/${skillId}/repository/folders`
              : `/skills/${skillId}/repository/files`;
          const body =
            mode === "folder"
              ? { path }
              : { path, content: this.sourceCreateForm.content };
          const created = await this.apiRequest(endpoint, {
            method: "POST",
            body: JSON.stringify(body)
          });
          this.sourceCreateModalOpen = false;
          await this.loadSkillDetail(skillId);
          await this.loadRepositoryTree(skillId, currentPath);
          if (mode === "file") {
            await this.loadRepositoryFile(created.file_path);
          }
          this.showNotice("success", mode === "folder" ? "文件夹已创建。" : "文件已创建。");
        } catch (error) {
          this.showNotice("error", error.message || "创建失败。");
        } finally {
          this.busy.repositoryCreate = false;
        }
      },

      async saveMetadata() {
        if (!this.currentSkill) {
          return;
        }

        this.busy.metadata = true;
        this.clearNotice();

        try {
          await this.apiRequest(`/skills/${this.currentSkill.id}`, {
            method: "PATCH",
            body: JSON.stringify(this.metadataForm)
          });
          await this.loadSkillDetail(this.currentSkill.id);
          this.showNotice("success", "Skill 基本信息已更新。");
        } catch (error) {
          this.showNotice("error", error.message || "更新 Skill 基本信息失败。");
        } finally {
          this.busy.metadata = false;
        }
      },

      async saveSource() {
        if (!this.currentSkill) {
          return;
        }
        if (this.sourceLoadedSkillId !== this.currentSkill.id) {
          await this.loadSkillSource(this.currentSkill.id);
        }

        this.busy.source = true;
        this.clearNotice();

        try {
          const saved = await this.apiRequest(`/skills/${this.currentSkill.id}/source`, {
            method: "PUT",
            body: JSON.stringify(this.sourceForm)
          });
          this.sourceForm.base_commit_sha = saved.head_commit_sha;
          await this.loadSkillDetail(this.currentSkill.id);
          await this.loadSkillSource(this.currentSkill.id);
          this.showNotice("success", "Skill 源码已提交到 GitLab。");
        } catch (error) {
          this.showNotice("error", error.message || "保存 Skill source 失败。");
        } finally {
          this.busy.source = false;
        }
      },

      emptyPublishProgress() {
        return {
          active: false,
          compile_request_id: null,
          terminal: false,
          terminal_status: null,
          error_message: "",
          stages: this.defaultPublishStages()
        };
      },

      defaultPublishStages() {
        return [
          { key: "source_frozen", label: "冻结源码", status: "pending", message: "等待提交发布请求。" },
          { key: "compile_request_created", label: "创建编译任务", status: "pending", message: "等待创建编译任务。" },
          { key: "source_loaded", label: "读取冻结源码", status: "pending", message: "等待读取冻结 commit 下的源码。" },
          { key: "manifest_checked", label: "校验 manifest", status: "pending", message: "等待校验发布版本 manifest snapshot。" },
          { key: "agent_compiling", label: "智能体编译 EG", status: "pending", message: "等待调用 SKILL 编译智能体。" },
          { key: "artifact_validating", label: "校验 EG artifact", status: "pending", message: "等待执行 formal-v5 校验。" },
          { key: "artifact_emitting", label: "写入编译产物", status: "pending", message: "等待写入 EG 编译产物。" },
          { key: "publish_finalizing", label: "完成发布", status: "pending", message: "等待写入发布终态。" }
        ];
      },

      isPublishInProgress() {
        return this.publishProgress.active && !this.publishProgress.terminal;
      },

      stopPublishProgressWatchers() {
        if (this.publishEventSource) {
          this.publishEventSource.close();
          this.publishEventSource = null;
        }
        if (this.publishPollTimer) {
          window.clearInterval(this.publishPollTimer);
          this.publishPollTimer = null;
        }
      },

      startPublishEventStream(compileRequestId) {
        this.stopPublishProgressWatchers();
        const url = `${this.apiBaseUrl}/compiler/requests/${encodeURIComponent(compileRequestId)}/events`;
        const eventSource = new EventSource(url);
        this.publishEventSource = eventSource;

        const handleEvent = (event) => {
          this.applyPublishProgress(JSON.parse(event.data));
        };
        eventSource.addEventListener("publish.progress", handleEvent);
        eventSource.addEventListener("publish.terminal", handleEvent);
        eventSource.onerror = () => {
          eventSource.close();
          if (this.publishEventSource === eventSource) {
            this.publishEventSource = null;
            this.startPublishProgressPolling(compileRequestId);
          }
        };
      },

      startPublishProgressPolling(compileRequestId) {
        if (this.publishPollTimer) {
          return;
        }

        const poll = async () => {
          try {
            const progress = await this.apiRequest(`/compiler/requests/${compileRequestId}/progress`);
            this.applyPublishProgress(progress);
          } catch (error) {
            this.showNotice("error", error.message || "获取发布进度失败。");
          }
        };
        poll();
        this.publishPollTimer = window.setInterval(poll, 1500);
      },

      async applyPublishProgress(progress) {
        this.publishProgress = {
          active: true,
          compile_request_id: progress.compile_request?.id || this.publishProgress.compile_request_id,
          terminal: Boolean(progress.terminal),
          terminal_status: progress.terminal_status,
          error_message: progress.error_message || "",
          stages: progress.stages || []
        };

        if (!progress.terminal) {
          return;
        }

        this.stopPublishProgressWatchers();
        this.busy.publish = false;
        if (this.currentSkill) {
          await this.loadSkillDetail(this.currentSkill.id);
          this.publishRecordsLoadedSkillId = null;
          await this.loadPublishRecords(this.currentSkill.id);
        }

        if (progress.terminal_status === "succeeded") {
          this.showNotice("success", "发布并编译成功。");
        } else {
          this.showNotice("error", progress.error_message || "发布失败。");
        }
      },

      async publishSkill() {
        if (!this.currentSkill) {
          return;
        }
        if (!this.publishForm.publish_reason) {
          this.showCenterToast("error", "请输入发布说明。");
          return;
        }

        this.busy.publish = true;
        this.clearNotice();
        this.publishProgress = {
          active: true,
          compile_request_id: null,
          terminal: false,
          terminal_status: null,
          error_message: "",
          stages: this.defaultPublishStages().map((stage, index) => index === 0
            ? { ...stage, status: "running", message: "正在提交发布请求..." }
            : stage)
        };

        try {
          const result = await this.apiRequest(`/skills/${this.currentSkill.id}/publish`, {
            method: "POST",
            body: JSON.stringify(this.publishForm)
          });
          this.publishForm = { publish_reason: "" };
          const compileRequestId = result.compile_request?.id;
          if (!compileRequestId) {
            throw new Error("发布任务缺少 compile request。");
          }
          this.publishProgress.compile_request_id = compileRequestId;
          this.startPublishEventStream(compileRequestId);
        } catch (error) {
          const errorMessage = error.message || "发布 Skill 失败。";
          const stages = this.publishProgress.stages.length > 0
            ? this.publishProgress.stages
            : [{ key: "source_frozen", label: "冻结源码", status: "running", message: "" }];
          this.publishProgress = {
            ...this.publishProgress,
            terminal: true,
            terminal_status: "failed",
            error_message: errorMessage,
            stages: stages.map((stage, index) => index === 0
              ? { ...stage, status: "failed", message: errorMessage, finished_at: new Date().toISOString() }
              : stage)
          };
          if (this.currentSkill) {
            this.publishRecordsLoadedSkillId = null;
            await this.loadPublishRecords(this.currentSkill.id);
          }
          this.showNotice("error", errorMessage);
          this.busy.publish = false;
        } finally {
          if (!this.isPublishInProgress()) {
            this.busy.publish = false;
          }
        }
      },

      async refreshCurrentSkill() {
        if (!this.currentSkill) {
          return;
        }

        try {
          await this.loadSkillDetail(this.currentSkill.id);
          if (this.activeDetailTab === "source") {
            await this.loadRepositoryTree(this.currentSkill.id, this.repositoryPath);
          }
          if (this.activeDetailTab === "publish") {
            await this.loadPublishRecords(this.currentSkill.id);
          }
          this.showNotice("success", "已刷新当前 Skill。");
        } catch (error) {
          this.showNotice("error", error.message || "刷新 Skill 失败。");
        }
      },

      showNotice(kind, text) {
        this.notice = { kind, text };
      },

      clearNotice() {
        this.notice = null;
      },

      async openSkill(skillId) {
        this.activeDetailTab = "overview";
        await this.navigate(buildSkillDetailPath(skillId));
      },

      async openCompilerArtifact(artifactId) {
        if (!artifactId) {
          return;
        }
        if (this.route.name === "skill-detail") {
          this.compilerArtifactWorkspaceOpen = true;
          await this.loadCompilerArtifact(artifactId);
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

      filteredPublishRecords() {
        return this.publishRecords.filter((record) => {
          const statusMatched = !this.publishFilters.status || record.publish_status === this.publishFilters.status;
          return (
            statusMatched &&
            this.inDateRange(
              record.published_at || record.created_at,
              this.publishFilters.published_from,
              this.publishFilters.published_to
            )
          );
        });
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

      currentSkillInvocations() {
        if (!this.currentSkill) {
          return [];
        }
        return this.invocations.filter((invocation) => invocation.skill_definition_id === this.currentSkill.id);
      },

      currentSkillFilteredInvocations() {
        return this.currentSkillInvocations().filter((invocation) =>
          this.inDateRange(
            invocation.created_at,
            this.runtimeFilters.created_from,
            this.runtimeFilters.created_to
          )
        );
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

      async selectDetailTab(tabName) {
        this.activeDetailTab = tabName;
        if (!this.currentSkill) {
          return;
        }

        try {
          if (tabName === "source") {
            await this.loadRepositoryTree(this.currentSkill.id, this.repositoryPath);
          }
          if (tabName === "publish") {
            await this.loadPublishRecords(this.currentSkill.id);
          }
          if (tabName === "compiler") {
            await this.loadCompilerRequests(this.currentSkill.id);
          }
          if (tabName === "runtime") {
            this.invocationForm.skill_key = this.currentSkill.key;
            await this.loadInvocations(this.currentSkill.key);
          }
        } catch (error) {
          this.showNotice("error", error.message || "数据加载失败。");
        }
      },

      isDeleteConfirmationValid() {
        return (
          Boolean(this.deleteTargetSkill) &&
          this.deleteForm.confirmation_name === this.deleteTargetSkill.name
        );
      },

      filteredSkills() {
        return this.skills.filter((skill) => {
          const nameQuery = this.filters.search.trim().toLowerCase();
          const nameMatched =
            !nameQuery ||
            skill.name.toLowerCase().includes(nameQuery);

          return (
            nameMatched &&
            this.inDateRange(skill.created_at, this.filters.created_from, this.filters.created_to) &&
            this.inDateRange(skill.latest_published_at, this.filters.published_from, this.filters.published_to)
          );
        });
      },

      inDateRange(value, start, end) {
        if (!start && !end) {
          return true;
        }
        if (!value) {
          return false;
        }

        const timestamp = new Date(value).getTime();
        const startTimestamp = start ? new Date(`${start}T00:00:00`).getTime() : Number.NEGATIVE_INFINITY;
        const endTimestamp = end ? new Date(`${end}T23:59:59`).getTime() : Number.POSITIVE_INFINITY;
        return timestamp >= startTimestamp && timestamp <= endTimestamp;
      },

      clearFilters() {
        this.filters = {
          search: "",
          status: "",
          created_from: "",
          created_to: "",
          published_from: "",
          published_to: ""
        };
        this.loadSkills();
      },

      hasActiveFilters() {
        return Object.values(this.filters).some((value) => Boolean(String(value || "").trim()));
      },

      async copyText(value, feedbackKey) {
        if (!value) {
          this.showCenterToast("error", "没有可复制的内容。");
          return;
        }

        try {
          if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(value);
          } else {
            this.copyTextWithFallback(value);
          }
          if (feedbackKey) {
            this.markCopied(feedbackKey);
          }
          this.showCenterToast("success", "已复制到剪贴板。");
        } catch (error) {
          this.showCenterToast("error", "复制失败，请手动复制。");
        }
      },

      showCenterToast(kind, text) {
        if (this.centerToastTimer) {
          window.clearTimeout(this.centerToastTimer);
        }

        this.centerToast = { kind, text };
        this.centerToastTimer = window.setTimeout(() => {
          this.centerToast = null;
          this.centerToastTimer = null;
        }, 1500);
      },

      centerToastClass() {
        if (!this.centerToast) {
          return "";
        }

        return this.centerToast.kind === "error"
          ? "border-rose-500/30 bg-rose-500/15 text-rose-100"
          : "border-orange-500/30 bg-orange-500/15 text-orange-100";
      },

      copyTextWithFallback(value) {
        const textarea = document.createElement("textarea");
        textarea.value = value;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.select();

        const copied = document.execCommand("copy");
        document.body.removeChild(textarea);
        if (!copied) {
          throw new Error("fallback copy failed");
        }
      },

      markCopied(feedbackKey) {
        this.copyFeedback = {
          ...this.copyFeedback,
          [feedbackKey]: true
        };
        window.setTimeout(() => {
          this.copyFeedback = {
            ...this.copyFeedback,
            [feedbackKey]: false
          };
        }, 1400);
      },

      copyIcon(feedbackKey) {
        return this.copyFeedback[feedbackKey] ? "check" : "content_copy";
      },

      selectSourceTab(tabName) {
        this.activeSourceTab = tabName;
      },

      currentSourceValue() {
        if (this.activeSourceTab === "README.md") {
          return this.sourceForm.readme_content;
        }

        if (this.activeSourceTab === "SKILL.md") {
          return this.sourceForm.skill_md_content;
        }

        return this.sourceForm.skill_yaml_content;
      },

      updateSourceValue(event) {
        if (this.activeSourceTab === "README.md") {
          this.sourceForm.readme_content = event.target.value;
          return;
        }

        if (this.activeSourceTab === "SKILL.md") {
          this.sourceForm.skill_md_content = event.target.value;
          return;
        }

        this.sourceForm.skill_yaml_content = event.target.value;
      },

      normalizeRepositoryPath(value, allowEmpty = false) {
        const normalized = String(value || "")
          .trim()
          .replace(/\\/g, "/")
          .replace(/\/+/g, "/")
          .replace(/^\/+|\/+$/g, "");
        return normalized || (allowEmpty ? "" : "");
      },

      repositoryBreadcrumbs() {
        const parts = this.repositoryPath ? this.repositoryPath.split("/") : [];
        const breadcrumbs = [{ label: "根目录", path: "" }];
        parts.forEach((part, index) => {
          breadcrumbs.push({
            label: part,
            path: parts.slice(0, index + 1).join("/")
          });
        });
        return breadcrumbs;
      },

      parentRepositoryPath() {
        if (!this.repositoryPath) {
          return "";
        }

        const parts = this.repositoryPath.split("/");
        parts.pop();
        return parts.join("/");
      },

      repositoryEntryIcon(entry) {
        if (entry.type === "tree") {
          return "folder";
        }

        if (entry.name.endsWith(".md")) {
          return "markdown";
        }

        if (entry.name.endsWith(".yaml") || entry.name.endsWith(".yml")) {
          return "description";
        }

        return "draft";
      },

      repositoryEntryLabel(entry) {
        if (entry.type !== "tree" && this.isSystemManifestFile(entry.path)) {
          return "系统";
        }
        return entry.type === "tree" ? "文件夹" : "文件";
      },

      isSystemManifestFile(path = this.repositoryFileForm.path) {
        return this.normalizeRepositoryPath(path).toLowerCase() === "skill.yaml";
      },

      isMarkdownPreview() {
        return this.repositoryFileForm.path.toLowerCase().endsWith(".md");
      },

      repositoryPreviewHtml() {
        if (this.isMarkdownPreview()) {
          return renderMarkdown(this.repositoryFileForm.content);
        }

        return `<pre><code>${escapeHtml(this.repositoryFileForm.content)}</code></pre>`;
      },

      publishStageIcon(stage) {
        if (stage.status === "succeeded") {
          return "check";
        }
        if (stage.status === "failed") {
          return "close";
        }
        if (stage.status === "running") {
          return "progress_activity";
        }
        return "radio_button_unchecked";
      },

      publishStageTone(stage) {
        if (stage.status === "succeeded") {
          return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
        }
        if (stage.status === "failed") {
          return "border-rose-500/30 bg-rose-500/10 text-rose-200";
        }
        if (stage.status === "running") {
          return "border-sky-500/30 bg-sky-500/10 text-sky-200";
        }
        return "border-slate-700 bg-slate-950/40 text-slate-500";
      },

      formatJson(value) {
        return JSON.stringify(value ?? null, null, 2);
      },

      jsonHighlightHtml(value) {
        return highlightJson(value);
      },

      syncJsonHighlightScroll(event) {
        const textarea = event?.target;
        const highlightLayer = textarea?.previousElementSibling;
        if (!textarea || !highlightLayer) {
          return;
        }
        highlightLayer.scrollTop = textarea.scrollTop;
        highlightLayer.scrollLeft = textarea.scrollLeft;
      },

      formatDateTime(value) {
        if (!value) {
          return "N/A";
        }

        return new Date(value).toLocaleString("zh-CN", {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit"
        });
      },

      formatShortId(value) {
        if (!value) {
          return "N/A";
        }

        return value.length > 12 ? `${value.slice(0, 12)}...` : value;
      },

      formatStatus(value) {
        const statusMap = {
          active: "启用",
          archived: "已归档",
          draft: "草稿",
          published: "已发布",
          requested: "已请求",
          compiling: "编译中",
          pending: "待处理",
          running: "运行中",
          queued: "排队中",
          accepted: "已接受",
          succeeded: "成功",
          failed: "失败",
          rejected: "已拒绝",
          cancelled: "已取消",
          canceled: "已取消",
          timeout: "已超时",
          timed_out: "已超时",
          skipped: "已跳过"
        };
        return statusMap[value] || value || "未知";
      },

      statusBadgeTone(value) {
        const normalized = String(value || "").toLowerCase();
        if (["active", "published", "succeeded", "success", "accepted"].includes(normalized)) {
          return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
        }
        if (["compiling", "running", "in_progress", "processing"].includes(normalized)) {
          return "border-sky-500/25 bg-sky-500/10 text-sky-200";
        }
        if (["requested", "pending", "queued", "draft", "retrying"].includes(normalized)) {
          return "border-amber-500/25 bg-amber-500/10 text-amber-200";
        }
        if (["failed", "error", "rejected", "cancelled", "canceled", "timeout", "timed_out"].includes(normalized)) {
          return "border-rose-500/30 bg-rose-500/10 text-rose-200";
        }
        if (["archived", "skipped", "unknown"].includes(normalized)) {
          return "border-slate-700 bg-slate-950/40 text-slate-400";
        }
        return "border-slate-700 bg-slate-950/40 text-slate-400";
      },

      routeTitle() {
        if (this.route.name === "skill-detail" && this.currentSkill) {
          return this.currentSkill.name;
        }
        if (this.route.name === "compiler-list") {
          return "编译";
        }
        if (this.route.name === "compiler-artifact") {
          return "EG Artifact";
        }
        if (this.route.name === "invocations-list") {
          return "运行";
        }
        if (this.route.name === "run-live") {
          return "运行现场";
        }
        if (this.route.name === "replay-list" || this.route.name === "replay-detail") {
          return "运行回放";
        }

        return "Skills";
      },

      noticeClass() {
        if (!this.notice) {
          return "";
        }

        return this.notice.kind === "error"
          ? "border-rose-500/30 bg-rose-500/15 text-rose-100"
          : "border-orange-500/30 bg-orange-500/15 text-orange-100";
      }
    };
  }

  document.addEventListener("alpine:init", function () {
    window.Alpine.data("skillsConsole", createSkillsConsole);
  });
})();
