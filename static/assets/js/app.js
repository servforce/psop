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

    return { name: "skills-list", params: {} };
  }

  function buildSkillDetailPath(skillId) {
    return `/admin/skills/${skillId}`;
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
        delete: false
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
          ["create-skill-modal-page", "/pages/create-skill-modal.html"],
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
        this.createModalOpen = true;
      },

      closeCreateModal() {
        if (this.busy.create) {
          return;
        }

        this.createModalOpen = false;
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
          }
        } catch (error) {
          this.showNotice("error", error.message || "页面加载失败。");
        } finally {
          this.loadingPage = false;
        }
      },

      async request(pathname, options) {
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
          this.skills = await this.request(`/skills${suffix}`);
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
          const created = await this.request("/skills", {
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
          await this.request(`/skills/${deletedSkill.id}`, {
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
          const detail = await this.request(`/skills/${skillId}`);

          this.currentSkill = detail;
          this.metadataForm = {
            name: detail.name,
            description: detail.description
          };
          this.resetLazyDetailState(skillId);
          if (!this.publishForm.publish_reason) {
            this.publishForm.publish_reason = "MVP 初始发布。";
          }
          if (!["overview", "source", "publish"].includes(this.activeDetailTab)) {
            this.activeDetailTab = "overview";
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
          const source = await this.request(`/skills/${skillId}/source`);
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
          this.publishRecords = await this.request(`/skills/${skillId}/publishes`);
          this.publishRecordsLoadedSkillId = skillId;
        } finally {
          this.busy.publishRecords = false;
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
          const tree = await this.request(`/skills/${skillId}/repository/tree${suffix}`);
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
          const file = await this.request(`/skills/${this.currentSkill.id}/repository/files?${params}`);
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
          const saved = await this.request(`/skills/${skillId}/repository/files`, {
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
          const created = await this.request(endpoint, {
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
          await this.request(`/skills/${this.currentSkill.id}`, {
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
          const saved = await this.request(`/skills/${this.currentSkill.id}/source`, {
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

      async publishSkill() {
        if (!this.currentSkill) {
          return;
        }

        this.busy.publish = true;
        this.clearNotice();

        try {
          const result = await this.request(`/skills/${this.currentSkill.id}/publish`, {
            method: "POST",
            body: JSON.stringify(this.publishForm)
          });
          await this.loadSkillDetail(this.currentSkill.id);
          this.publishRecordsLoadedSkillId = null;
          await this.loadPublishRecords(this.currentSkill.id);
          this.showNotice("success", `发布成功，冻结 commit：${result.published_commit_sha}`);
        } catch (error) {
          this.showNotice("error", error.message || "发布 Skill 失败。");
        } finally {
          this.busy.publish = false;
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
          ? "border-rose-200 bg-rose-50 text-rose-800"
          : "border-teal-200 bg-teal-50 text-teal-800";
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
        return entry.type === "tree" ? "文件夹" : "文件";
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
          failed: "失败"
        };
        return statusMap[value] || value || "未知";
      },

      routeTitle() {
        if (this.route.name === "skill-detail" && this.currentSkill) {
          return this.currentSkill.name;
        }

        return "Skills";
      },

      noticeClass() {
        if (!this.notice) {
          return "";
        }

        return this.notice.kind === "error"
          ? "border-rose-200 bg-rose-50 text-rose-800"
          : "border-teal-200 bg-teal-50 text-teal-800";
      }
    };
  }

  document.addEventListener("alpine:init", function () {
    window.Alpine.data("skillsConsole", createSkillsConsole);
  });
})();
