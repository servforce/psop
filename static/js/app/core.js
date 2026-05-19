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
    buildAgentPromptPath,
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
        this.installButtonTooltips();
        this.installDangerActionConfirmations();
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

      installButtonTooltips() {
        if (this.buttonTooltipInstalled || typeof document === "undefined") {
          return;
        }

        const ensureTooltip = (event) => {
          this.ensureButtonTooltip(event.target);
        };

        document.addEventListener("pointerover", ensureTooltip, true);
        document.addEventListener("focusin", ensureTooltip, true);
        this.buttonTooltipInstalled = true;
      },


      installDangerActionConfirmations() {
        if (this.dangerActionConfirmationInstalled || typeof document === "undefined") {
          return;
        }

        document.addEventListener("click", (event) => this.handleDangerActionClick(event), true);
        this.dangerActionConfirmationInstalled = true;
      },


      handleDangerActionClick(event) {
        if (event?.defaultPrevented) {
          return;
        }
        const button = event?.target?.closest?.("button,[role='button']");
        if (!button || this.isButtonInteractionDisabled(button)) {
          return;
        }

        const message = this.describeDangerActionConfirmation(button);
        if (!message || this.confirmDangerAction(message, button)) {
          return;
        }

        event.preventDefault?.();
        event.stopImmediatePropagation?.();
      },


      isButtonInteractionDisabled(button) {
        return (
          button.disabled ||
          button.getAttribute?.("disabled") !== null && button.getAttribute?.("disabled") !== "" ||
          button.getAttribute?.("aria-disabled") === "true"
        );
      },


      describeDangerActionConfirmation(button) {
        if (!button || button.dataset?.confirmDisabled === "true") {
          return "";
        }
        const explicitMessage = this.normalizeTooltipText(button.dataset?.dangerConfirm || button.dataset?.confirmMessage || "");
        if (explicitMessage && explicitMessage !== "true") {
          return explicitMessage;
        }
        if (!this.isDangerActionButton(button)) {
          return "";
        }
        const actionLabel = this.describeButtonAction(button) || "执行此操作";
        const normalizedAction = actionLabel.startsWith("确认") ? actionLabel : `确认${actionLabel}`;
        return `${normalizedAction}？此操作可能无法撤销。`;
      },


      isDangerActionButton(button) {
        const clickAction = this.buttonClickAction(button);
        if (/\bopenDeleteModal\b/.test(clickAction)) {
          return false;
        }
        if (button.dataset?.dangerConfirm === "true") {
          return true;
        }
        if (button.classList?.contains("button-danger")) {
          return true;
        }
        return /\b(delete|remove|archive)[A-Z_]/.test(clickAction);
      },


      confirmDangerAction(message) {
        if (typeof window === "undefined" || typeof window.confirm !== "function") {
          return true;
        }
        return window.confirm(message);
      },

      refreshButtonTooltips(root = (typeof document === "undefined" ? null : document)) {
        if (!root?.querySelectorAll) {
          return;
        }
        root.querySelectorAll("button,[role='button']").forEach((button) => {
          this.ensureButtonTooltip(button);
        });
      },

      scheduleButtonTooltipRefresh() {
        if (typeof window !== "undefined" && window.requestAnimationFrame) {
          window.requestAnimationFrame(() => this.refreshButtonTooltips());
          return;
        }
        this.refreshButtonTooltips();
      },

      ensureButtonTooltip(target) {
        const button = target?.closest?.("button,[role='button']");
        if (!button || button.dataset?.tooltipDisabled === "true") {
          return;
        }

        const tooltip = this.describeButtonAction(button);
        if (!tooltip) {
          return;
        }

        const titleIsAuto = button.dataset?.autoTitle === "true";
        const ariaIsAuto = button.dataset?.autoAria === "true";
        if (titleIsAuto || !String(button.getAttribute("title") || "").trim()) {
          button.setAttribute("title", tooltip);
          if (button.dataset) {
            button.dataset.autoTitle = "true";
          }
        }
        if (ariaIsAuto || !String(button.getAttribute("aria-label") || "").trim()) {
          button.setAttribute("aria-label", tooltip);
          if (button.dataset) {
            button.dataset.autoAria = "true";
          }
        }
      },

      describeButtonAction(button) {
        const explicitDescription =
          button.dataset?.tooltip ||
          (button.dataset?.autoAria === "true" ? "" : button.getAttribute("aria-label")) ||
          (button.dataset?.autoTitle === "true" ? "" : button.getAttribute("title"));
        if (String(explicitDescription || "").trim()) {
          return this.normalizeTooltipText(explicitDescription);
        }

        const visibleText = this.extractButtonVisibleText(button);
        if (visibleText) {
          if (button.classList?.contains("breadcrumb-link")) {
            return `打开 ${visibleText}`;
          }
          if (button.classList?.contains("detail-tab") || button.getAttribute("role") === "tab") {
            return `切换到${visibleText}`;
          }
          return visibleText;
        }

        const iconDescription = this.describeButtonIcon(button);
        if (iconDescription) {
          return iconDescription;
        }

        return this.describeButtonClickAction(button);
      },

      normalizeTooltipText(value) {
        return String(value || "").replace(/\s+/g, " ").trim();
      },

      extractButtonVisibleText(button) {
        const readText = (node) => {
          if (!node) {
            return "";
          }
          if (node.nodeType === 3) {
            return node.textContent || "";
          }
          if (node.nodeType !== 1) {
            return "";
          }
          if (
            node.classList?.contains("material-symbols-outlined") ||
            node.classList?.contains("material-symbols-rounded") ||
            node.classList?.contains("material-symbols-sharp") ||
            node.getAttribute?.("aria-hidden") === "true"
          ) {
            return "";
          }
          return Array.from(node.childNodes || []).map(readText).join(" ");
        };

        return this.normalizeTooltipText(Array.from(button.childNodes || []).map(readText).join(" "));
      },

      describeButtonIcon(button) {
        const icon = button.querySelector?.(".material-symbols-outlined, .material-symbols-rounded, .material-symbols-sharp");
        const iconName = this.normalizeTooltipText(icon?.textContent || icon?.getAttribute?.("x-text") || "");
        const iconTooltips = {
          account_tree: "查看图预览",
          add: "新增",
          add_circle: "创建",
          archive: "归档",
          arrow_back: "返回",
          attach_file: "添加附件",
          badge: "查看概览",
          call_split: "Fork 场景",
          check: "保存",
          check_circle: "完成",
          close: "关闭",
          code_blocks: "查看源码",
          content_copy: "复制",
          create_new_folder: "新建文件夹",
          data_object: "查看 JSON",
          delete: "删除",
          done: "完成",
          drive_folder_upload: "返回上级目录",
          edit: "编辑",
          fact_check: "编辑语义事件",
          format_indent_increase: "格式化",
          history: "查看历史",
          hub: "切换菜单",
          note_add: "新建文件",
          open_in_new: "打开",
          pause: "暂停",
          play_arrow: "运行",
          play_circle: "运行",
          refresh: "刷新",
          replay: "重新播放",
          restart_alt: "重置",
          rocket_launch: "发布",
          schedule: "时钟事件",
          save: "保存",
          science: "测试",
          send: "发送",
          smart_toy: "基本原则",
          terminal: "调试",
          upload_file: "上传文件"
        };
        return iconTooltips[iconName] || "";
      },

      describeButtonClickAction(button) {
        const clickAction = this.buttonClickAction(button);
        const actionTooltips = [
          [/forkSkillTestScenario/, "Fork 测试场景"],
          [/forkSkillDebug/, "Fork 调试运行"],
          [/\bcopyText\b/, "复制"],
          [/\bsave[A-Z_]/, "保存"],
          [/\bdelete[A-Z_]/, "删除"],
          [/\bremove[A-Z_]/, "移除"],
          [/\bclose[A-Z_]/, "关闭"],
          [/\breset[A-Z_]/, "重置"],
          [/\bformat[A-Z_]/, "格式化"],
          [/\bstart[A-Z_]/, "启动"],
          [/\bopen[A-Z_]/, "打开"],
          [/\bnavigate\b/, "打开"],
          [/\bload[A-Z_]/, "刷新"]
        ];
        const matched = actionTooltips.find(([pattern]) => pattern.test(clickAction));
        return matched ? matched[1] : "";
      },


      buttonClickAction(button) {
        return (
          button?.getAttribute?.("@click") ||
          button?.getAttribute?.("x-on:click") ||
          button?.getAttribute?.("x-on:click.prevent") ||
          button?.getAttribute?.("@click.stop") ||
          button?.getAttribute?.("x-on:click.stop") ||
          ""
        );
      },


      async loadPageFragments() {
        const fragments = [
          ["skills-list-page", "/pages/skills-list.html"],
          ["skill-detail-page", "/pages/skill-detail.html"],
          ["compiler-list-page", "/pages/compiler-list.html"],
          ["compiler-artifact-page", "/pages/compiler-artifact-detail.html"],
          ["agent-prompts-list-page", "/pages/agent-prompts-list.html"],
          ["agent-prompt-detail-page", "/pages/agent-prompt-detail.html"],
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
        this.refreshButtonTooltips();
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
        if (this.route.name !== "skill-test-scenario-review") {
          this.stopSkillTestReviewPlayback?.();
          this.stopSkillTestReviewPolling?.();
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

          if (this.route.name === "agent-prompts-list") {
            this.currentSkill = null;
            await this.loadAgentPrompts();
            return;
          }

          if (this.route.name === "agent-prompt-detail") {
            this.currentSkill = null;
            await this.loadAgentPromptDetail(this.route.params.definitionId);
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
          this.scheduleButtonTooltipRefresh();
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

        const contentType = response.headers.get("content-type") || "";
        if (!/\bjson\b|\+json\b/i.test(contentType)) {
          throw new Error(
            `API 返回了非 JSON 响应：${pathname}。当前 API 地址为 ${this.apiBaseUrl}，请确认前端配置指向后端服务。`
          );
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
        if (this.route.name === "agent-prompts-list") {
          return "基本原则";
        }
        if (this.route.name === "agent-prompt-detail") {
          return this.agentPromptDetail?.name || "Agent Prompt Pack";
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

      agentPromptPath(definitionId) {
        return buildAgentPromptPath(definitionId);
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
