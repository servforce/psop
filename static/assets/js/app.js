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

  function resolveApiBaseUrl() {
    if (window.__PSOP_API_BASE_URL) {
      return window.__PSOP_API_BASE_URL;
    }

    if (window.location.port === "4173") {
      return "http://127.0.0.1:8000/api/v1";
    }

    return "/api/v1";
  }

  function createInitialState() {
    return {
      apiBaseUrl: resolveApiBaseUrl(),
      route: { name: "skills-list", params: {} },
      loadingPage: false,
      skills: [],
      currentSkill: null,
      notice: null,
      createForm: {
        key: "",
        name: "",
        description: ""
      },
      filters: {
        search: "",
        status: ""
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
        publish: false
      }
    };
  }

  function createSkillsConsole() {
    return {
      ...createInitialState(),

      boot() {
        this.syncRoute();
        window.addEventListener("popstate", () => {
          this.syncRoute();
          this.loadCurrentRoute();
        });
        this.loadCurrentRoute();
      },

      syncRoute() {
        this.route = resolveAdminRoute(window.location.pathname);
      },

      navigate(pathname) {
        if (pathname !== window.location.pathname) {
          window.history.pushState({}, "", pathname);
        }
        this.syncRoute();
        this.loadCurrentRoute();
      },

      async loadCurrentRoute() {
        this.loadingPage = true;
        this.clearNotice();

        try {
          if (this.route.name === "skills-list") {
            this.currentSkill = null;
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
          const created = await this.request("/skills", {
            method: "POST",
            body: JSON.stringify(this.createForm)
          });
          this.createForm = { key: "", name: "", description: "" };
          this.navigate(buildSkillDetailPath(created.id));
          this.showNotice("success", "Skill 已创建，并已在 GitLab 中初始化。");
        } catch (error) {
          this.showNotice("error", error.message || "创建 Skill 失败。");
        } finally {
          this.busy.create = false;
        }
      },

      async loadSkillDetail(skillId) {
        this.busy.detail = true;
        try {
          const [detail, source] = await Promise.all([
            this.request(`/skills/${skillId}`),
            this.request(`/skills/${skillId}/source`)
          ]);

          this.currentSkill = detail;
          this.metadataForm = {
            name: detail.name,
            description: detail.description
          };
          this.sourceForm = {
            readme_content: source.readme_content,
            skill_md_content: source.skill_md_content,
            skill_yaml_content: source.skill_yaml_content,
            base_commit_sha: source.head_commit_sha
          };
          if (!this.publishForm.publish_reason) {
            this.publishForm.publish_reason = "Initial MVP publish.";
          }
        } finally {
          this.busy.detail = false;
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

        this.busy.source = true;
        this.clearNotice();

        try {
          const saved = await this.request(`/skills/${this.currentSkill.id}/source`, {
            method: "PUT",
            body: JSON.stringify(this.sourceForm)
          });
          this.sourceForm.base_commit_sha = saved.head_commit_sha;
          await this.loadSkillDetail(this.currentSkill.id);
          this.showNotice("success", "Skill source 已提交到 GitLab。");
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

      openSkill(skillId) {
        this.navigate(buildSkillDetailPath(skillId));
      },

      copyText(value) {
        if (!value || !navigator.clipboard) {
          return;
        }

        navigator.clipboard.writeText(value).then(() => {
          this.showNotice("success", "已复制到剪贴板。");
        });
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
          ? "border-rose-500/30 bg-rose-500/12 text-rose-100"
          : "border-emerald-500/30 bg-emerald-500/12 text-emerald-100";
      }
    };
  }

  document.addEventListener("alpine:init", function () {
    window.Alpine.data("skillsConsole", createSkillsConsole);
  });
})();
