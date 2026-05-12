(function () {
  const {
    normalizePath,
    resolveAdminRoute,
    buildSkillDetailPath,
    buildRunLivePath,
    buildSkillRunLivePath,
    buildSkillDebugRunLivePath,
    buildReplayPath,
    buildSkillReplayPath,
    buildSkillTestScenarioPath,
    buildSkillTestScenarioNewPath,
    buildSkillTestScenarioRunReviewPath,
    buildCompilerArtifactPath,
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

  window.PSOPConsoleCoreMethods = {

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
          ["skill-test-scenario-page", "/pages/skill-test-scenario-detail.html"],
          ["skill-test-scenario-review-page", "/pages/skill-test-scenario-review.html"],
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
        if (!["run-live", "skill-run-live", "skill-debug-live"].includes(this.route.name)) {
          this.disconnectRunWebSocket();
        }
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

          if (this.route.name === "skill-run-live") {
            this.activeDetailTab = "runtime";
            await this.loadSkillDetail(this.route.params.skillId);
            await this.loadRunLive(this.route.params.runId);
            return;
          }

          if (this.route.name === "skill-debug-live") {
            this.activeDetailTab = "debug";
            await this.loadSkillDetail(this.route.params.skillId);
            await this.loadRunLive(this.route.params.runId);
            return;
          }

          if (this.route.name === "skill-replay-detail") {
            this.activeDetailTab = "runtime";
            await this.loadSkillDetail(this.route.params.skillId);
            await this.loadReplayDetail(this.route.params.runId);
            return;
          }

          if (this.route.name === "skill-test-scenario") {
            this.activeDetailTab = "test";
            await this.loadSkillDetail(this.route.params.skillId);
            await this.loadSkillTestCaseDetail(this.route.params.skillId, this.route.params.scenarioId);
            return;
          }

          if (this.route.name === "skill-test-scenario-new") {
            this.activeDetailTab = "test";
            await this.loadSkillDetail(this.route.params.skillId);
            this.skillTestCase = null;
            this.skillTestDataObjects = [];
            this.resetSkillTestCaseForm();
            return;
          }

          if (this.route.name === "skill-test-scenario-review") {
            this.activeDetailTab = "test";
            await this.loadSkillDetail(this.route.params.skillId);
            await this.loadSkillTestRunReview(
              this.route.params.skillId,
              this.route.params.scenarioId,
              this.route.params.scenarioRunId
            );
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
        const requestOptions = options || {};
        const isFormData = requestOptions.body instanceof FormData;
        const headers = {
          ...(isFormData ? {} : { "Content-Type": "application/json" }),
          ...(requestOptions.headers || {})
        };
        let response;
        try {
          response = await fetch(`${this.apiBaseUrl}${pathname}`, {
            ...requestOptions,
            headers
          });
        } catch (error) {
          throw new Error(`网络请求失败：${pathname}。请确认后端服务可访问。`);
        }

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


      showNotice(kind, text) {
        this.notice = { kind, text };
      },


      clearNotice() {
        this.notice = null;
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


      routeTitle() {
        if (
          [
            "skill-detail",
            "skill-run-live",
            "skill-debug-live",
            "skill-replay-detail",
            "skill-test-scenario-new",
            "skill-test-scenario",
            "skill-test-scenario-review"
          ].includes(this.route.name) &&
          this.currentSkill
        ) {
          return this.currentSkill.name;
        }
        if (this.route.name === "skill-test-scenario") {
          return "测试场景";
        }
        if (this.route.name === "skill-test-scenario-review") {
          return "时序测试回放";
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
      },
  };
})();
