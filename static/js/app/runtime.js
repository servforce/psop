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
    resolveApiBaseUrl,
    resolveWsUrl,
    escapeHtml,
    highlightJson,
    highlightYamlScalar,
    highlightYaml,
    renderInlineMarkdown,
    renderMarkdown
  } = window.PSOPConsoleHelpers;

  window.PSOPConsoleRuntimeMethods = {

      async loadInvocations(skillKey = null) {
        this.busy.invocations = true;
        try {
          const suffix = skillKey ? `?skill_key=${encodeURIComponent(skillKey)}` : "";
          this.invocations = await this.apiRequest(`/gateway/invocations${suffix}`);
        } finally {
          this.busy.invocations = false;
        }
      },


      async loadSkillRuns(skillId, status = this.runtimeFilters.status) {
        this.busy.skillRuns = true;
        try {
          const params = new URLSearchParams({ skill_id: skillId });
          if (status) {
            params.set("status", status);
          }
          this.skillRuns = await this.apiRequest(`/runs?${params.toString()}`);
        } finally {
          this.busy.skillRuns = false;
        }
      },


      async createInvocation() {
        if (!this.invocationForm.skill_key) {
          this.showNotice("error", "请选择 Skill。");
          return;
        }

        this.busy.createInvocation = true;
        this.clearNotice();
        try {
          const userInput = this.invocationForm.user_input.trim();
          const invocation = await this.apiRequest("/gateway/invocations", {
            method: "POST",
            body: JSON.stringify({
              skill_key: this.invocationForm.skill_key,
              gateway_type: "terminal",
              terminal_context: {
                terminal_kind: "web"
              },
              input_envelope: userInput ? { user_input: userInput } : {}
            })
          });
          this.invocationForm.user_input = "";
          if (this.route.name === "skill-detail" && this.currentSkill?.id) {
            await this.loadSkillRuns(this.currentSkill.id);
          } else {
            await this.loadInvocations();
          }
          if (invocation.run_id) {
            const livePath = this.currentSkill?.id
              ? buildSkillRunLivePath(this.currentSkill.id, invocation.run_id)
              : buildRunLivePath(invocation.run_id);
            await this.navigate(livePath);
          }
        } catch (error) {
          this.showNotice("error", error.message || "发起运行失败。");
        } finally {
          this.busy.createInvocation = false;
        }
      },


      async createSkillDebugInvocation() {
        if (!this.currentSkill?.key) {
          this.showNotice("error", "请选择 Skill。");
          return;
        }

        this.busy.createInvocation = true;
        this.clearNotice();
        try {
          const userInput = this.skillDebugForm.user_input.trim();
          const invocation = await this.apiRequest("/gateway/invocations", {
            method: "POST",
            body: JSON.stringify({
              skill_key: this.currentSkill.key,
              version_selector: "latest",
              gateway_type: "terminal",
              terminal_context: {
                terminal_kind: "web",
                operator_mode: "debug",
                debug_context: {
                  kind: "skill_debug",
                  skill_id: this.currentSkill.id
                }
              },
              input_envelope: userInput ? { user_input: userInput } : {}
            })
          });
          this.skillDebugForm.user_input = "";
          await this.loadInvocations(this.currentSkill.key);
          if (invocation.run_id) {
            await this.navigate(buildSkillDebugRunLivePath(this.currentSkill.id, invocation.run_id));
          }
        } catch (error) {
          this.showNotice("error", error.message || "启动调试失败。");
        } finally {
          this.busy.createInvocation = false;
        }
      },


      async loadRunLive(runId) {
        this.busy.liveRun = true;
        let loadGeneration = null;
        let taskStatusError = "";
        try {
          const isSameRun = this.liveRunLoadedRunId === runId;
          if (isSameRun && this.liveRun?.id === runId) {
            loadGeneration = this._liveRunLoadGeneration;
            this.syncLiveRunInteractionTabFromRoute(true);
            await this.ensureLiveRunInteractionTabData(this.liveRunInteractionTab, runId);
            if (this._liveRunLoadGeneration !== loadGeneration || this.liveRun?.id !== runId) {
              return;
            }
            this.connectRunWebSocket(runId);
            return;
          }

          this.destroyLiveRunView();
          this.busy.liveRun = true;
          loadGeneration = this._liveRunLoadGeneration;
          this.syncLiveRunInteractionTabFromRoute(false);
          if (!isSameRun) {
            this.selectedLiveRunReplayItemKey = "";
            this.selectedLiveRunProcessEventKey = "";
          }
          const [run, taskStatus, bindings, terminalSession] = await Promise.all([
            this.apiRequest(`/runs/${runId}`),
            this.apiRequest(`/runs/${runId}/task-status`).catch((error) => {
              taskStatusError = error?.message || "任务状态加载失败。";
              return null;
            }),
            this.apiRequest(`/runs/${runId}/bindings`),
            this.apiRequest(`/terminal/sessions/${runId}`)
          ]);
          if (this._liveRunLoadGeneration !== loadGeneration) {
            return;
          }
          this.liveRun = run;
          this.liveRunTaskStatus = taskStatus;
          this.liveRunTaskStatusError = taskStatusError;
          this.liveRunLoadedRunId = runId;
          this.liveRunTaskStatusLoadedRunId = runId;
          this.liveRunBindings = bindings;
          this.liveRunTerminalSession = terminalSession.terminal_session;
          await this.ensureLiveRunInteractionTabData(this.liveRunInteractionTab, runId);
          if (this._liveRunLoadGeneration !== loadGeneration || this.liveRun?.id !== runId) {
            return;
          }
          this.connectRunWebSocket(runId);
        } finally {
          if (loadGeneration === null || this._liveRunLoadGeneration === loadGeneration) {
            this.busy.liveRun = false;
          }
        }
      },


      syncLiveRunInteractionTabFromRoute(isSameRun = false) {
        const allowedTabs = new Set(["terminal", "io", "replay"]);
        if (this.route?.params?.view === "replay") {
          this.liveRunInteractionTab = "replay";
        } else if (!isSameRun || !allowedTabs.has(this.liveRunInteractionTab)) {
          this.liveRunInteractionTab = "terminal";
        }
        this.markLiveRunInteractionTabMounted(this.liveRunInteractionTab);
      },


      markLiveRunInteractionTabMounted(tabName) {
        if (!["terminal", "io", "replay"].includes(tabName)) {
          return;
        }
        this.liveRunMountedTabs = {
          ...this.liveRunMountedTabs,
          [tabName]: true
        };
      },


      async selectLiveRunInteractionTab(tabName) {
        if (!["terminal", "io", "replay"].includes(tabName)) {
          return;
        }
        if (this.liveRunInteractionTab !== tabName) {
          this.pauseLiveRunMedia();
          this.closeTerminalMediaPreview();
          this.closeLiveRunProcessEventDrawer();
        }
        this.liveRunInteractionTab = tabName;
        this.markLiveRunInteractionTabMounted(tabName);
        try {
          await this.ensureLiveRunInteractionTabData(tabName, this.liveRun?.id || "");
        } catch (error) {
          this.showNotice("error", error.message || "运行数据加载失败。");
        }
        if (tabName === "terminal") {
          this.scrollTerminalTranscriptToBottom();
        }
      },


      async ensureLiveRunInteractionTabData(tabName, runId) {
        if (!runId) {
          return;
        }
        if (["terminal", "io"].includes(tabName)) {
          await this.loadLiveRunTerminalEvents(runId);
          return;
        }
        if (tabName === "replay") {
          await this.loadLiveRunReplay(runId);
        }
      },


      async loadLiveRunTerminalEvents(runId) {
        if (!runId || this.liveRunTerminalEventsLoadedRunId === runId) {
          return;
        }
        if (this._liveRunTerminalEventsRequest?.runId === runId) {
          await this._liveRunTerminalEventsRequest.promise;
          return;
        }
        const promise = this.apiRequest(`/terminal/sessions/${runId}/events`);
        this._liveRunTerminalEventsRequest = { runId, promise };
        try {
          const terminalEvents = await promise;
          if (this.liveRun?.id !== runId) {
            return;
          }
          this.mergeTerminalEvents(terminalEvents);
          this.liveRunTerminalEventsLoadedRunId = runId;
        } finally {
          if (this._liveRunTerminalEventsRequest?.promise === promise) {
            this._liveRunTerminalEventsRequest = null;
          }
        }
      },


      async loadLiveRunReplay(runId) {
        if (!runId || this.liveRunReplayLoadedRunId === runId) {
          return;
        }
        if (this._liveRunReplayRequest?.runId === runId) {
          const currentRequest = this._liveRunReplayRequest;
          await currentRequest.promise;
          if (
            this.liveRun?.id === runId &&
            this.liveRunReplayLoadedRunId !== runId &&
            Number(this._liveRunReplayGeneration || 0) !== currentRequest.generation
          ) {
            if (this._liveRunReplayRequest === currentRequest) {
              this._liveRunReplayRequest = null;
            }
            await this.loadLiveRunReplay(runId);
          }
          return;
        }
        const generation = Number(this._liveRunReplayGeneration || 0);
        const promise = (async () => {
          const replayDetail = await this.apiRequest(`/replay/runs/${runId}`);
          if (
            this.liveRun?.id !== runId ||
            Number(this._liveRunReplayGeneration || 0) !== generation
          ) {
            return;
          }
          this.replayDetail = replayDetail;
          this.liveRunReplayLoadedRunId = runId;
        })();
        this._liveRunReplayRequest = { runId, promise, generation };
        try {
          await promise;
        } finally {
          if (this._liveRunReplayRequest?.promise === promise) {
            this._liveRunReplayRequest = null;
          }
        }
      },


      async refreshLiveRunSummary(runId) {
        if (!runId || this.liveRun?.id !== runId) {
          return;
        }
        const [run, terminalSession] = await Promise.all([
          this.apiRequest(`/runs/${runId}`),
          this.apiRequest(`/terminal/sessions/${runId}`)
        ]);
        if (this.liveRun?.id !== runId) {
          return;
        }
        this.liveRun = run;
        this.liveRunTerminalSession = terminalSession.terminal_session;
        this.updateLiveRunLatestTerminalSeq();
      },


      async refreshLiveRunTaskStatus(runId) {
        if (!runId || this.liveRun?.id !== runId) {
          return;
        }
        let taskStatus;
        try {
          taskStatus = await this.apiRequest(`/runs/${runId}/task-status`);
        } catch (error) {
          if (this.liveRun?.id === runId) {
            this.liveRunTaskStatusError = error?.message || "任务状态加载失败。";
          }
          throw error;
        }
        if (this.liveRun?.id !== runId) {
          return;
        }
        this.applyLiveRunTaskStatus(taskStatus);
        this.liveRunTaskStatusLoadedRunId = runId;
      },


      destroyLiveRunView() {
        this._liveRunLoadGeneration = Number(this._liveRunLoadGeneration || 0) + 1;
        this._liveRunReplayGeneration = Number(this._liveRunReplayGeneration || 0) + 1;
        if (this.busy) {
          this.busy.liveRun = false;
          this.busy.terminalInput = false;
        }
        if (this._liveRunReplayRefreshTimer) {
          clearTimeout(this._liveRunReplayRefreshTimer);
          this._liveRunReplayRefreshTimer = null;
        }
        this.pauseLiveRunMedia();
        this.releaseTerminalEventObjectUrls(this.liveRunTerminalEvents);
        this.releaseTerminalInputAttachmentObjectUrls(this.terminalInputAttachments());
        this.disconnectRunWebSocket();
        this.closeTerminalMediaPreview();
        this.liveRun = null;
        this.liveRunTaskStatus = null;
        this.liveRunTaskStatusError = "";
        this.liveRunBindings = [];
        this.liveRunTerminalSession = null;
        this.liveRunTerminalEvents = [];
        this.liveRunTraceEvents = [];
        this.liveRunInteractionTab = "terminal";
        this.liveRunMountedTabs = { terminal: false, io: false, replay: false };
        this.liveRunLoadedRunId = "";
        this.liveRunTaskStatusLoadedRunId = "";
        this.liveRunTerminalEventsLoadedRunId = "";
        this.liveRunReplayLoadedRunId = "";
        this.selectedLiveRunReplayItemKey = "";
        this.selectedLiveRunProcessEventKey = "";
        this.replayDetail = null;
        this.terminalInputForm.payload = "";
        this.terminalInputForm.attachments = [];
        this._liveRunTerminalEventsRequest = null;
        this._liveRunReplayRequest = null;
        this._liveRunTerminalReconcileRequest = null;
        this.liveRunTaskPanelOpen = false;
      },


      pauseLiveRunMedia() {
        if (typeof document === "undefined") {
          return;
        }
        document.querySelectorAll("[data-run-live-view] audio, [data-run-live-view] video").forEach((element) => {
          if (typeof element.pause === "function") {
            element.pause();
          }
        });
      },


      async sendTerminalInput() {
        const runId = this.liveRun?.id;
        const viewGeneration = Number(this._liveRunLoadGeneration || 0);
        const textPayload = this.terminalInputText();
        const attachments = this.terminalInputAttachments();
        if (!runId || !this.canSendTerminalInput()) {
          return;
        }
        this.busy.terminalInput = true;
        const reconcileFromSeq = this.nextTerminalReconcileFromSeq();
        const optimisticEvent = this.buildOptimisticTerminalInputEvent(runId, textPayload, attachments);
        let acceptedByServer = false;
        this.mergeTerminalEvents([optimisticEvent]);
        this.terminalInputForm.payload = "";
        this.clearTerminalInputAttachments({ revokeObjectUrls: false });
        try {
          if (attachments.length) {
            const result = await this.sendTerminalRuntimeMultipartEvent(runId, textPayload, attachments, optimisticEvent.external_event_id);
            acceptedByServer = true;
            if (this.liveRun?.id === runId && Number(this._liveRunLoadGeneration || 0) === viewGeneration) {
              this.mergeTerminalEvents([result.event]);
            }
          } else {
            const response = await this.apiRequest(`/terminal/sessions/${runId}/events`, {
              method: "POST",
              body: JSON.stringify({
                direction: "input",
                text: textPayload,
                source: {
                  kind: "web"
                },
                external_event_id: optimisticEvent.external_event_id
              })
            });
            acceptedByServer = true;
            if (this.liveRun?.id === runId && Number(this._liveRunLoadGeneration || 0) === viewGeneration) {
              this.mergeTerminalEvents([response.event]);
            }
          }
          if (this.liveRun?.id !== runId || Number(this._liveRunLoadGeneration || 0) !== viewGeneration) {
            return;
          }
          await this.refreshLiveRunSummary(runId).catch(() => {});
        } catch (error) {
          if (this.liveRun?.id !== runId || Number(this._liveRunLoadGeneration || 0) !== viewGeneration) {
            return;
          }
          if (!acceptedByServer) {
            acceptedByServer = await this.reconcileTerminalInputAcceptance(
              runId,
              optimisticEvent.external_event_id,
              reconcileFromSeq
            );
          }
          if (!acceptedByServer) {
            this.removeOptimisticTerminalEvent(optimisticEvent.id, { revokeObjectUrls: false });
            this.terminalInputForm.payload = textPayload;
            this.terminalInputForm.attachments = attachments;
          } else {
            await this.refreshLiveRunSummary(runId).catch(() => {});
          }
          if (!acceptedByServer) {
            this.showNotice("error", error.message || "终端输入发送失败。");
          }
        } finally {
          if (this.liveRun?.id === runId && Number(this._liveRunLoadGeneration || 0) === viewGeneration) {
            this.busy.terminalInput = false;
          }
        }
      },


      handleTerminalInputFile(event) {
        const files = Array.from(event.target.files || []);
        const nextAttachments = files.map((file) => ({
          id: `attachment-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
          file
        }));
        this.terminalInputForm.attachments = [...this.terminalInputAttachments(), ...nextAttachments];
        if (this.$refs?.terminalInputFile) {
          this.$refs.terminalInputFile.value = "";
        }
      },


      clearTerminalInputAttachments({ revokeObjectUrls = true } = {}) {
        if (revokeObjectUrls) {
          this.releaseTerminalInputAttachmentObjectUrls(this.terminalInputAttachments());
        }
        this.terminalInputForm.attachments = [];
        if (this.$refs?.terminalInputFile) {
          this.$refs.terminalInputFile.value = "";
        }
      },


      removeTerminalInputAttachment(attachmentId) {
        const removed = this.terminalInputAttachments().find((attachment) => attachment.id === attachmentId);
        this.releaseTerminalInputAttachmentObjectUrls(removed ? [removed] : []);
        this.terminalInputForm.attachments = this.terminalInputAttachments().filter((attachment) => attachment.id !== attachmentId);
      },


      async sendTerminalRuntimeMultipartEvent(runId, textPayload = "", attachments = [], externalEventId = "") {
        if (!runId || !attachments.length) {
          throw new Error("当前运行不可上传多模态数据。");
        }

        const formData = new FormData();
        attachments.forEach((attachment) => {
          formData.append("files", attachment.file);
        });
        formData.append(
          "event",
          JSON.stringify({
            direction: "input",
            text: textPayload,
            source: { kind: "web" },
            external_event_id: externalEventId
          })
        );
        return this.apiRequest(`/terminal/sessions/${runId}/events`, {
          method: "POST",
          headers: externalEventId ? { "Idempotency-Key": externalEventId } : {},
          body: formData
        });
      },


      connectRunWebSocket(runId) {
        if (this.liveRunWs && this.liveRunWsRunId === runId && this.liveRunWs.readyState === WebSocket.OPEN) {
          return;
        }
        if (this._liveRunWsReconnectTimer) {
          clearTimeout(this._liveRunWsReconnectTimer);
          this._liveRunWsReconnectTimer = null;
        }
        const previousSocket = this.liveRunWs;
        this.liveRunWs = null;
        if (previousSocket) {
          previousSocket.close();
        }
        this.liveRunWsRunId = runId;
        this.liveRunWsStatus = "connecting";
        const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, `/ws/runs/${runId}`));
        this.liveRunWs = socket;
        socket.addEventListener("open", () => {
          if (this.liveRunWs !== socket) {
            return;
          }
          this.liveRunWsStatus = "open";
          const isReconnect = Boolean(this._liveRunWsHasOpened);
          this._liveRunWsHasOpened = true;
          this._liveRunWsReconnectAttempt = 0;
          if (this.liveRunMountedTabs.terminal || this.liveRunMountedTabs.io) {
            this.reconcileLiveRunTerminalEvents(runId, this.nextTerminalReconcileFromSeq()).catch(() => {});
          }
          if (this.liveRunInteractionTab === "replay") {
            this.reconcileLiveRunReplayAfterSocketOpen(runId).catch(() => {});
          } else if (isReconnect) {
            this.refreshLiveRunSummary(runId).catch(() => {});
          }
          if (isReconnect) {
            this.refreshLiveRunTaskStatus(runId).catch(() => {});
          }
        });
        socket.addEventListener("message", (event) => {
          if (this.liveRunWs !== socket || this.liveRunWsRunId !== runId || this.liveRun?.id !== runId) {
            return;
          }
          try {
            this.handleRunWsEvent(JSON.parse(event.data), runId);
          } catch {
            // Ignore malformed runtime stream payloads; REST remains the recovery path.
          }
        });
        socket.addEventListener("close", () => {
          if (this.liveRunWs === socket) {
            this.liveRunWs = null;
            this.liveRunWsStatus = "closed";
            this.scheduleRunWebSocketReconnect(runId);
          }
        });
        socket.addEventListener("error", () => {
          if (this.liveRunWs === socket) {
            this.liveRunWsStatus = "error";
          }
        });
      },


      disconnectRunWebSocket() {
        if (this._liveRunWsReconnectTimer) {
          clearTimeout(this._liveRunWsReconnectTimer);
          this._liveRunWsReconnectTimer = null;
        }
        const socket = this.liveRunWs;
        this.liveRunWs = null;
        this.liveRunWsRunId = "";
        this.liveRunWsStatus = "idle";
        this._liveRunWsReconnectAttempt = 0;
        this._liveRunWsHasOpened = false;
        if (socket) {
          socket.close();
        }
      },


      scheduleRunWebSocketReconnect(runId) {
        if (!runId || this.liveRun?.id !== runId || this.liveRunWsRunId !== runId) {
          return;
        }
        const attempt = Number(this._liveRunWsReconnectAttempt || 0);
        const delay = Math.min(10000, 500 * (2 ** attempt));
        this._liveRunWsReconnectAttempt = attempt + 1;
        this._liveRunWsReconnectTimer = setTimeout(() => {
          this._liveRunWsReconnectTimer = null;
          if (this.liveRun?.id === runId && this.liveRunWsRunId === runId) {
            this.connectRunWebSocket(runId);
          }
        }, delay);
      },


      handleRunWsEvent(event, expectedRunId = "") {
        if (!event || event.event_type === "ws.connected") {
          return;
        }
        const activeRunId = String(this.liveRun?.id || "");
        const eventRunIds = [expectedRunId, event.run_id, event.payload?.run_id]
          .filter(Boolean)
          .map((value) => String(value));
        if (!activeRunId || eventRunIds.some((runId) => runId !== activeRunId)) {
          return;
        }
        if (event.event_type === "terminal.event.appended" && event.payload) {
          const expectedSeq = this.nextTerminalReconcileFromSeq();
          const incomingSeq = Number(event.payload.seq_no) || 0;
          if (
            incomingSeq > expectedSeq &&
            (this.liveRunMountedTabs.terminal || this.liveRunMountedTabs.io)
          ) {
            this.reconcileLiveRunTerminalEvents(this.liveRun?.id || "", expectedSeq).catch(() => {});
          }
          this.mergeTerminalEvents([event.payload]);
          this.liveRunReplayLoadedRunId = "";
          if (this.liveRunInteractionTab === "replay") {
            this.scheduleLiveRunReplayRefresh(activeRunId);
          }
        }
        if (event.event_type === "trace.event.appended" && event.payload) {
          this.liveRunTraceEvents = window.PSOPRuntimeEvents.mergeBySeq(this.liveRunTraceEvents, [event.payload]);
          this.liveRunReplayLoadedRunId = "";
          if (this.liveRunInteractionTab === "replay") {
            this.scheduleLiveRunReplayRefresh(activeRunId);
          }
        }
        if (["binding.resolved", "binding.updated"].includes(event.event_type) && event.payload?.bindings) {
          this.liveRunBindings = window.PSOPRuntimeEvents.mergeById(this.liveRunBindings, event.payload.bindings);
        }
        if (event.event_type === "run.task_status.updated" && event.payload) {
          this.applyLiveRunTaskStatus(event.payload);
        }
      },


      applyLiveRunTaskStatus(taskStatus) {
        if (!taskStatus || String(taskStatus.run_id || "") !== String(this.liveRun?.id || "")) {
          return false;
        }
        if (!window.PSOPRuntimeEvents.shouldReplaceTaskStatus(this.liveRunTaskStatus, taskStatus)) {
          return false;
        }
        this.liveRunTaskStatus = taskStatus;
        this.liveRunTaskStatusError = "";
        this.liveRunTaskStatusLoadedRunId = String(taskStatus.run_id || "");
        if (this.liveRun) {
          this.liveRun = { ...this.liveRun, status: taskStatus.run_status || this.liveRun.status };
        }
        if (["succeeded", "failed", "aborted", "cancelled", "canceled"].includes(String(taskStatus.run_status || "").toLowerCase())) {
          if (this.liveRunTerminalSession) {
            this.liveRunTerminalSession = { ...this.liveRunTerminalSession, status: "closed" };
          }
          this.refreshLiveRunSummary(taskStatus.run_id).catch(() => {});
        }
        return true;
      },


      scheduleLiveRunReplayRefresh(runId) {
        if (!runId || this.liveRun?.id !== runId) {
          return;
        }
        this._liveRunReplayGeneration = Number(this._liveRunReplayGeneration || 0) + 1;
        this.liveRunReplayLoadedRunId = "";
        if (this._liveRunReplayRefreshTimer) {
          clearTimeout(this._liveRunReplayRefreshTimer);
        }
        this._liveRunReplayRefreshTimer = setTimeout(() => {
          this._liveRunReplayRefreshTimer = null;
          if (this.liveRun?.id === runId && this.liveRunInteractionTab === "replay") {
            this.loadLiveRunReplay(runId).catch(() => {});
          }
        }, 100);
      },


      async reconcileLiveRunReplayAfterSocketOpen(runId) {
        if (!runId || this.liveRun?.id !== runId || this.liveRunInteractionTab !== "replay") {
          return;
        }
        const replayRun = this.replayDetail?.run?.id === runId ? this.replayDetail.run : null;
        await this.refreshLiveRunSummary(runId);
        if (!replayRun || this.liveRun?.id !== runId || this.liveRunInteractionTab !== "replay") {
          return;
        }
        const changed = ["latest_terminal_seq", "latest_trace_seq", "latest_snapshot_seq", "status"]
          .some((key) => String(replayRun[key] ?? "") !== String(this.liveRun?.[key] ?? ""));
        if (changed) {
          this.scheduleLiveRunReplayRefresh(runId);
        }
      },


      mergeTerminalEvents(events) {
        const incoming = events || [];
        const realIncomingSeqs = new Set(
          incoming
            .filter((event) => event && !event._optimistic && Number.isFinite(Number(event.seq_no)))
            .map((event) => Number(event.seq_no))
        );
        const realIncomingExternalIds = new Set(
          incoming
            .filter((event) => event && !event._optimistic && event.external_event_id)
            .map((event) => String(event.external_event_id))
        );
        const replacedOptimisticEvents = this.liveRunTerminalEvents.filter(
          (event) =>
            event?._optimistic &&
            (realIncomingSeqs.has(Number(event.seq_no)) || realIncomingExternalIds.has(String(event.external_event_id || "")))
        );
        this.releaseTerminalEventObjectUrls(replacedOptimisticEvents);
        const replacedOptimisticIds = new Set(replacedOptimisticEvents.map((event) => event.id));
        const baseEvents = this.liveRunTerminalEvents.filter(
          (event) => !realIncomingSeqs.has(Number(event.seq_no)) && !replacedOptimisticIds.has(event.id)
        );
        this.liveRunTerminalEvents = window.PSOPRuntimeEvents.mergeBySeq(baseEvents, incoming);
        this.updateLiveRunLatestTerminalSeq();
        this.ensureLiveRunProcessSelection();
        this.scrollTerminalTranscriptToBottom();
      },


      updateLiveRunLatestTerminalSeq() {
        if (!this.liveRun) {
          return;
        }
        const eventSeqs = this.liveRunTerminalEvents.map((event) => Number(event.seq_no) || 0);
        const latestSeq = Math.max(
          Number(this.liveRun.latest_terminal_seq || 0),
          ...eventSeqs
        );
        this.liveRun.latest_terminal_seq = latestSeq || 0;
      },


      nextOptimisticTerminalSeq() {
        return (
          Math.max(
            Number(this.liveRun?.latest_terminal_seq || 0),
            ...this.liveRunTerminalEvents.map((event) => Number(event.seq_no) || 0)
          ) + 1
        );
      },


      nextTerminalReconcileFromSeq() {
        const realSeqs = new Set(this.liveRunTerminalEvents
          .filter((event) => !event?._optimistic)
          .map((event) => Number(event.seq_no) || 0)
          .filter((seqNo) => seqNo > 0));
        let expectedSeq = 1;
        while (realSeqs.has(expectedSeq)) {
          expectedSeq += 1;
        }
        return expectedSeq;
      },


      terminalInputEventKindForMime(mimeType = "") {
        if (mimeType.startsWith("image/")) {
          return "terminal.image.input.v1";
        }
        if (mimeType.startsWith("audio/")) {
          return "terminal.audio.input.v1";
        }
        if (mimeType.startsWith("video/")) {
          return "terminal.video.input.v1";
        }
        return "terminal.file.input.v1";
      },


      terminalInputPartKindForMime(mimeType = "") {
        if (mimeType.startsWith("image/")) {
          return "image";
        }
        if (mimeType.startsWith("audio/")) {
          return "audio";
        }
        if (mimeType.startsWith("video/")) {
          return "video";
        }
        return "file";
      },


      terminalInputAttachments() {
        return Array.isArray(this.terminalInputForm.attachments) ? this.terminalInputForm.attachments : [];
      },


      terminalInputAttachmentIcon(attachment) {
        const kind = this.terminalInputPartKindForMime(attachment?.file?.type || "");
        if (kind === "image") {
          return "image";
        }
        if (kind === "audio") {
          return "graphic_eq";
        }
        if (kind === "video") {
          return "movie";
        }
        return "draft";
      },


      terminalInputAttachmentMeta(attachment) {
        const file = attachment?.file;
        if (!file) {
          return "";
        }
        return [file.type || "application/octet-stream", this.formatBytes(file.size || 0)].filter(Boolean).join(" · ");
      },


      buildOptimisticTerminalInputEvent(runId, textPayload, attachments = []) {
        const now = new Date().toISOString();
        const id = `local-terminal-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        const parts = [];
        if (textPayload) {
          parts.push({
            part_id: "text_1",
            order_index: 1,
            kind: "text",
            mime_type: "text/plain",
            text: textPayload,
            metadata: {}
          });
        }
        attachments.forEach((attachment, index) => {
          const file = attachment.file;
          const previewUrl = String(attachment.preview_url || "").startsWith("blob:")
            ? attachment.preview_url
            : URL.createObjectURL(file);
          attachment.preview_url = previewUrl;
          parts.push({
            part_id: `file_${index + 1}`,
            order_index: parts.length + 1,
            kind: this.terminalInputPartKindForMime(file.type || ""),
            mime_type: file.type || "application/octet-stream",
            text: "",
            size_bytes: file.size || 0,
            metadata: {
              filename: file.name,
              name: file.name,
              preview_url: previewUrl
            },
            _local_url: previewUrl
          });
        });
        return {
          id,
          terminal_session_id: this.liveRun?.terminal_session_id || this.liveRunTerminalSession?.id || "",
          run_id: runId,
          direction: "input",
          event_kind: "terminal.multimodal.input.v1",
          mime_type: "multipart/mixed",
          payload_inline: textPayload || attachments.map((attachment) => attachment.file?.name || "").filter(Boolean).join("\n"),
          parts,
          seq_no: this.nextOptimisticTerminalSeq(),
          external_event_id: id,
          source_ref: {
            kind: "web",
            optimistic: true
          },
          occurred_at: now,
          created_at: now,
          _optimistic: true,
          _optimistic_status: "sent"
        };
      },


      removeOptimisticTerminalEvent(eventId, { revokeObjectUrls = true } = {}) {
        const removedEvents = this.liveRunTerminalEvents.filter((event) => event.id === eventId);
        if (revokeObjectUrls) {
          this.releaseTerminalEventObjectUrls(removedEvents);
        }
        this.liveRunTerminalEvents = this.liveRunTerminalEvents.filter((event) => event.id !== eventId);
        this.updateLiveRunLatestTerminalSeq();
      },


      async reconcileTerminalInputAcceptance(runId, externalEventId, fromSeq = 1) {
        if (!runId || !externalEventId) {
          return false;
        }
        try {
          const normalizedFromSeq = Math.max(1, Number(fromSeq) || 1);
          const events = await this.apiRequest(`/terminal/sessions/${runId}/events?from_seq=${normalizedFromSeq}`);
          const acceptedEvent = events.find((event) => event.external_event_id === externalEventId);
          if (!acceptedEvent) {
            return false;
          }
          if (this.liveRun?.id === runId) {
            this.mergeTerminalEvents([acceptedEvent]);
          }
          return true;
        } catch {
          return false;
        }
      },


      async reconcileLiveRunTerminalEvents(runId, fromSeq = 1) {
        if (!runId || this.liveRun?.id !== runId) {
          return;
        }
        const normalizedFromSeq = Math.max(1, Number(fromSeq) || 1);
        const requestKey = `${runId}:${normalizedFromSeq}`;
        if (this._liveRunTerminalReconcileRequest?.key === requestKey) {
          await this._liveRunTerminalReconcileRequest.promise;
          return;
        }
        const promise = this.apiRequest(
          `/terminal/sessions/${runId}/events?from_seq=${normalizedFromSeq}`
        );
        this._liveRunTerminalReconcileRequest = { key: requestKey, promise };
        try {
          const events = await promise;
          if (this.liveRun?.id === runId) {
            this.mergeTerminalEvents(events);
          }
        } finally {
          if (this._liveRunTerminalReconcileRequest?.promise === promise) {
            this._liveRunTerminalReconcileRequest = null;
          }
        }
      },


      releaseTerminalInputAttachmentObjectUrls(attachments) {
        const urls = (attachments || []).map((attachment) => attachment?.preview_url).filter(Boolean);
        this.revokeTerminalObjectUrls(urls);
      },


      releaseTerminalEventObjectUrls(events) {
        const urls = [];
        (events || []).forEach((event) => {
          this.terminalEventParts(event).forEach((part) => {
            const metadata = part?.metadata && typeof part.metadata === "object" ? part.metadata : {};
            if (part?._local_url) {
              urls.push(part._local_url);
            }
            if (metadata.preview_url) {
              urls.push(metadata.preview_url);
            }
          });
        });
        this.revokeTerminalObjectUrls(urls);
      },


      revokeTerminalObjectUrls(urls) {
        if (typeof URL === "undefined" || typeof URL.revokeObjectURL !== "function") {
          return;
        }
        new Set(urls || []).forEach((url) => {
          if (String(url).startsWith("blob:")) {
            URL.revokeObjectURL(url);
          }
        });
      },


      terminalRunEnded() {
        return ["succeeded", "failed", "aborted", "cancelled", "canceled"].includes(String(this.liveRun?.status || "").toLowerCase());
      },


      openLiveRunTaskPanel() {
        this.liveRunTaskPanelOpen = true;
      },


      closeLiveRunTaskPanel() {
        this.liveRunTaskPanelOpen = false;
      },


      liveRunCurrentTaskStage() {
        const currentStageId = String(this.liveRunTaskStatus?.current_stage_id || "");
        return (this.liveRunTaskStatus?.stages || []).find((stage) => String(stage.id || "") === currentStageId) || null;
      },


      taskActivityLabel(status) {
        return {
          initializing: "正在准备任务",
          running: "任务执行中",
          waiting_input: "等待现场输入",
          finalizing: "正在完成核验",
          succeeded: "任务已完成",
          failed: "任务执行失败",
          aborted: "任务已中止",
          cancelled: "任务已取消",
          canceled: "任务已取消"
        }[String(status || "").toLowerCase()] || "状态未知";
      },


      taskActivityTone(status) {
        const normalized = String(status || "").toLowerCase();
        if (normalized === "succeeded") {
          return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
        }
        if (["failed", "aborted", "cancelled", "canceled"].includes(normalized)) {
          return "border-rose-500/25 bg-rose-500/10 text-rose-200";
        }
        if (normalized === "waiting_input") {
          return "border-amber-500/25 bg-amber-500/10 text-amber-200";
        }
        return "border-sky-500/25 bg-sky-500/10 text-sky-200";
      },


      taskStageLabel(status) {
        return {
          pending: "未开始",
          in_progress: "进行中",
          waiting_input: "等待输入",
          completed: "已完成",
          failed: "失败",
          aborted: "已中止",
          cancelled: "已取消"
        }[String(status || "").toLowerCase()] || "未知";
      },


      taskStageTone(status) {
        const normalized = String(status || "").toLowerCase();
        if (normalized === "completed") {
          return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
        }
        if (["failed", "aborted", "cancelled"].includes(normalized)) {
          return "border-rose-500/30 bg-rose-500/10 text-rose-200";
        }
        if (normalized === "waiting_input") {
          return "border-amber-500/30 bg-amber-500/10 text-amber-200";
        }
        if (normalized === "in_progress") {
          return "border-sky-500/30 bg-sky-500/10 text-sky-200";
        }
        return "border-slate-700 bg-slate-900/70 text-slate-400";
      },


      taskStageIcon(status) {
        return {
          completed: "check",
          in_progress: "play_arrow",
          waiting_input: "hourglass_top",
          failed: "error",
          aborted: "block",
          cancelled: "close"
        }[String(status || "").toLowerCase()] || "circle";
      },


      taskEvidenceTone(status) {
        const normalized = String(status || "").toLowerCase();
        if (normalized === "accepted") {
          return "text-emerald-300";
        }
        if (normalized === "rejected") {
          return "text-rose-300";
        }
        if (normalized === "ambiguous") {
          return "text-amber-300";
        }
        return "text-slate-500";
      },


      taskEvidenceIcon(status) {
        return {
          accepted: "check_circle",
          rejected: "cancel",
          ambiguous: "help"
        }[String(status || "").toLowerCase()] || "radio_button_unchecked";
      },


      taskExpectedInputLabel(item) {
        const value = String(item?.kind || item?.event_kind || item?.mime_type || "evidence");
        return {
          text: "文字",
          image: "图片",
          audio: "音频",
          video: "视频",
          file: "文件"
        }[value.toLowerCase()] || value;
      },


      terminalSessionClosed() {
        const status = String(this.liveRunTerminalSession?.status || "").toLowerCase();
        return Boolean(status && status !== "open");
      },


      terminalInputText() {
        return String(this.terminalInputForm.payload || "").trim();
      },


      terminalInputHasContent() {
        return Boolean(this.terminalInputText() || this.terminalInputAttachments().length);
      },


      terminalInputContextOpen() {
        return true;
      },


      canUseTerminalInput() {
        return Boolean(this.liveRun?.id && this.terminalInputContextOpen() && !this.terminalRunEnded() && !this.terminalSessionClosed());
      },


      canAttachTerminalInputFile() {
        return !this.busy.terminalInput && this.canUseTerminalInput();
      },


      canSendTerminalInput() {
        return !this.busy.terminalInput && this.canUseTerminalInput() && this.terminalInputHasContent();
      },


      terminalInputDisabledReason() {
        if (this.busy.terminalInput) {
          return "消息发送中。";
        }
        if (!this.liveRun?.id) {
          return "运行现场尚未加载完成。";
        }
        if (!this.terminalInputContextOpen()) {
          return "当前测试运行已结束，不能继续发送输入。";
        }
        if (this.liveRun.status === "failed") {
          return "当前运行已失败，Terminal Session 已关闭。请开启一次新的运行后继续输入。";
        }
        if (this.liveRun.status === "succeeded") {
          return "当前运行已成功结束，不能继续发送输入。";
        }
        if (this.liveRun.status === "aborted") {
          return "当前运行已中止，不能继续发送输入。";
        }
        if (["cancelled", "canceled"].includes(String(this.liveRun.status || "").toLowerCase())) {
          return "当前运行已取消，不能继续发送输入。";
        }
        if (this.terminalSessionClosed()) {
          return "Terminal Session 已关闭，不能继续发送输入。";
        }
        if (!this.terminalInputHasContent()) {
          return "输入文本或添加文件后可发送。";
        }
        return "";
      },


      terminalEventParts(event) {
        return Array.isArray(event?.parts) ? event.parts : [];
      },


      terminalEventHasParts(event) {
        return this.terminalEventParts(event).length > 0;
      },


      terminalEventPartMimeType(part) {
        return String(part?.mime_type || "").toLowerCase();
      },


      terminalPresentationKind(mimeType = "", fallbackKind = "") {
        const normalizedMimeType = String(mimeType || "").split(";", 1)[0].trim().toLowerCase();
        const normalizedFallbackKind = String(fallbackKind || "").trim().toLowerCase();
        const genericMimeTypes = new Set(["", "application/octet-stream", "binary/octet-stream", "multipart/mixed"]);

        if (!genericMimeTypes.has(normalizedMimeType)) {
          if (normalizedMimeType.startsWith("image/")) {
            return "image";
          }
          if (normalizedMimeType.startsWith("audio/")) {
            return "audio";
          }
          if (normalizedMimeType.startsWith("video/")) {
            return "video";
          }
          if (normalizedMimeType === "application/pdf") {
            return "pdf";
          }
          if (
            normalizedMimeType.startsWith("text/") ||
            normalizedMimeType === "application/json" ||
            normalizedMimeType.endsWith("+json")
          ) {
            return "text";
          }
          return "file";
        }

        if (["text", "image", "audio", "video", "pdf", "file"].includes(normalizedFallbackKind)) {
          return normalizedFallbackKind;
        }
        if (normalizedFallbackKind.includes(".image.")) {
          return "image";
        }
        if (normalizedFallbackKind.includes(".audio.")) {
          return "audio";
        }
        if (normalizedFallbackKind.includes(".video.")) {
          return "video";
        }
        if (normalizedFallbackKind.includes(".text.")) {
          return "text";
        }
        if (normalizedFallbackKind.includes(".pdf.")) {
          return "pdf";
        }
        return "file";
      },


      terminalEventPartPresentationKind(part) {
        return this.terminalPresentationKind(this.terminalEventPartMimeType(part), part?.kind);
      },


      terminalEventPartIsText(part) {
        return this.terminalEventPartPresentationKind(part) === "text";
      },


      terminalEventPartIsImage(part) {
        return this.terminalEventPartPresentationKind(part) === "image";
      },


      terminalEventPartIsAudio(part) {
        return this.terminalEventPartPresentationKind(part) === "audio";
      },


      terminalEventPartIsVideo(part) {
        return this.terminalEventPartPresentationKind(part) === "video";
      },


      terminalEventPartIsPdf(part) {
        return this.terminalEventPartPresentationKind(part) === "pdf";
      },


      terminalEventPartIsFile(part) {
        return this.terminalEventPartPresentationKind(part) === "file";
      },


      terminalEventPartDisplayText(part) {
        return this.normalizeTerminalMessageText(part?.text || "").trim();
      },


      terminalEventPartMarkdownHtml(part) {
        return renderMarkdown(this.terminalEventPartDisplayText(part));
      },


      terminalEventPartFileName(part) {
        const metadata = part?.metadata && typeof part.metadata === "object" ? part.metadata : {};
        const value = metadata.filename || metadata.name || part?.part_id || "terminal-attachment";
        return String(value).split("/").filter(Boolean).pop() || "terminal-attachment";
      },


      terminalEventPartMediaUrl(event, part) {
        if (part?._local_url) {
          return part._local_url;
        }
        const metadata = part?.metadata && typeof part.metadata === "object" ? part.metadata : {};
        if (metadata.preview_url) {
          return metadata.preview_url;
        }
        if (!part?.artifact_object_id || !event?.id || !part?.part_id) {
          return "";
        }
        const runId = event.run_id || this.liveRun?.id || "";
        if (!runId) {
          return "";
        }
        return `${this.apiBaseUrl}/terminal/sessions/${encodeURIComponent(runId)}/events/${encodeURIComponent(event.id)}/parts/${encodeURIComponent(part.part_id)}/content`;
      },


      terminalEventMimeType(event) {
        return String(event?.mime_type || "").toLowerCase();
      },


      terminalEventFileExtension(event) {
        const fileName = this.terminalEventFileName(event).toLowerCase();
        const match = fileName.match(/\.([a-z0-9]+)$/);
        return match ? match[1] : "";
      },


      terminalEventInferredMimeType(event) {
        const eventKind = String(event?.event_kind || "").toLowerCase();
        if (eventKind.includes(".image.")) {
          return "image/*";
        }
        if (eventKind.includes(".audio.")) {
          return "audio/*";
        }
        if (eventKind.includes(".video.")) {
          return "video/*";
        }
        const extension = this.terminalEventFileExtension(event);
        const mimeTypes = {
          apng: "image/apng",
          avif: "image/avif",
          gif: "image/gif",
          jpeg: "image/jpeg",
          jpg: "image/jpeg",
          png: "image/png",
          svg: "image/svg+xml",
          webp: "image/webp",
          mp3: "audio/mpeg",
          m4a: "audio/mp4",
          ogg: "audio/ogg",
          wav: "audio/wav",
          weba: "audio/webm",
          mp4: "video/mp4",
          m4v: "video/mp4",
          mov: "video/quicktime",
          ogv: "video/ogg",
          webm: "video/webm",
          pdf: "application/pdf",
          json: "application/json",
          md: "text/markdown",
          txt: "text/plain"
        };
        return mimeTypes[extension] || "";
      },


      terminalEventPresentationMimeType(event) {
        const mimeType = this.terminalEventMimeType(event);
        if (mimeType && mimeType !== "application/octet-stream") {
          return mimeType;
        }
        return this.terminalEventInferredMimeType(event) || mimeType;
      },


      terminalEventPresentationKind(event) {
        return this.terminalPresentationKind(
          this.terminalEventPresentationMimeType(event),
          event?.kind || event?.event_kind
        );
      },


      terminalEventPayloadObject(event) {
        const payload = event?.payload_inline;
        return payload && typeof payload === "object" && !Array.isArray(payload) ? payload : null;
      },


      terminalEventPayloadTextValue(event, keys) {
        const payload = this.terminalEventPayloadObject(event);
        if (!payload) {
          return "";
        }
        const match = keys.find((key) => {
          const value = payload[key];
          return value !== null && value !== undefined && typeof value !== "object" && String(value).trim();
        });
        return match ? String(payload[match]).trim() : "";
      },


      normalizeTerminalMessageText(value) {
        return String(value ?? "")
          .replace(/\r\n/g, "\n")
          .replace(/\\r\\n/g, "\n")
          .replace(/\\n/g, "\n");
      },


      terminalEventDisplayText(event) {
        const payload = event?.payload_inline;
        if (typeof payload === "string") {
          return this.normalizeTerminalMessageText(payload);
        }
        if (payload === null || payload === undefined) {
          return "";
        }
        return this.normalizeTerminalMessageText(this.terminalEventPayloadTextValue(event, [
          "description",
          "message",
          "text",
          "content",
          "summary",
          "user_input",
          "final_response",
          "output"
        ]));
      },


      terminalEventMarkdownHtml(event) {
        return renderMarkdown(this.terminalEventDisplayText(event));
      },


      terminalEventJsonText(event) {
        const payload = event?.payload_inline;
        if (payload === null || payload === undefined) {
          return "";
        }
        if (typeof payload === "string") {
          return payload;
        }
        return JSON.stringify(payload, null, 2);
      },


      terminalEventSourceUrl(event) {
        const payload = this.terminalEventPayloadObject(event);
        if (!payload) {
          return "";
        }
        const key = ["url", "src", "content_url", "preview_url", "data_url"].find(
          (candidate) => typeof payload[candidate] === "string" && payload[candidate].trim()
        );
        return key ? payload[key].trim() : "";
      },


      terminalEventMediaUrl(event) {
        const inlineUrl = this.terminalEventSourceUrl(event);
        if (inlineUrl) {
          return inlineUrl;
        }
        if (!event?.artifact_object_id || !event?.id) {
          return "";
        }
        const runId = event.run_id || this.liveRun?.id || "";
        if (!runId) {
          return "";
        }
        return `${this.apiBaseUrl}/terminal/sessions/${encodeURIComponent(runId)}/events/${encodeURIComponent(event.id)}/content`;
      },


      terminalEventIsImage(event) {
        return this.terminalEventPresentationKind(event) === "image";
      },


      terminalEventIsAudio(event) {
        return this.terminalEventPresentationKind(event) === "audio";
      },


      terminalEventIsVideo(event) {
        return this.terminalEventPresentationKind(event) === "video";
      },


      terminalEventIsJson(event) {
        const mimeType = this.terminalEventPresentationMimeType(event);
        return mimeType === "application/json" || mimeType.endsWith("+json");
      },


      terminalEventIsPdf(event) {
        return this.terminalEventPresentationKind(event) === "pdf";
      },


      terminalEventIsGenericFile(event) {
        return Boolean(
          this.terminalEventMediaUrl(event) &&
            this.terminalEventPresentationKind(event) === "file" &&
            !this.terminalEventIsJson(event)
        );
      },


      terminalEventShouldShowJson(event) {
        const payload = event?.payload_inline;
        if (!payload || typeof payload !== "object") {
          return false;
        }
        if (this.terminalEventIsJson(event)) {
          return true;
        }
        if (
          this.terminalEventIsImage(event) ||
          this.terminalEventIsAudio(event) ||
          this.terminalEventIsVideo(event) ||
          this.terminalEventIsPdf(event) ||
          this.terminalEventIsGenericFile(event)
        ) {
          return false;
        }
        return !this.terminalEventDisplayText(event);
      },


      terminalEventShouldShowPlainText(event) {
        return Boolean(
          this.terminalEventDisplayText(event) &&
            !this.terminalEventShouldShowJson(event) &&
            !this.terminalEventIsImage(event) &&
            !this.terminalEventIsAudio(event) &&
            !this.terminalEventIsVideo(event) &&
            !this.terminalEventIsPdf(event)
        );
      },


      terminalEventFileName(event) {
        const payload = this.terminalEventPayloadObject(event);
        const value =
          payload?.filename ||
          payload?.name ||
          payload?.title ||
          payload?.object_key ||
          event?.event_kind ||
          "terminal-attachment";
        return String(value).split("/").filter(Boolean).pop() || "terminal-attachment";
      },


      terminalEventFileSize(event) {
        const payload = this.terminalEventPayloadObject(event);
        const size = Number(payload?.size_bytes ?? payload?.size ?? 0);
        return Number.isFinite(size) && size > 0 ? size : 0;
      },


      terminalEventFileMeta(event) {
        const size = this.terminalEventFileSize(event);
        return size ? this.formatBytes(size) : "";
      },


      terminalEventFileIcon(event) {
        const mimeType = this.terminalEventPresentationMimeType(event);
        if (mimeType === "application/pdf") {
          return "picture_as_pdf";
        }
        if (mimeType.startsWith("text/") || mimeType === "application/json") {
          return "description";
        }
        return "draft";
      },


      terminalEventActorLabel(event) {
        if (String(event?.direction || "").toLowerCase() !== "output") {
          return "用户";
        }
        return event?.source_ref?.agent_key === "psop.runner" ? "执行助手" : "Runtime";
      },


      terminalEventRowClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "justify-end" : "justify-start";
      },


      terminalEventMessageShellClass(event) {
        return "w-fit";
      },


      terminalEventMessageShellStyle(event) {
        return "max-width: 70%;";
      },


      terminalEventContentClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "items-end" : "items-start";
      },


      terminalEventMetaClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "justify-end text-right" : "justify-start";
      },


      terminalEventBubbleClass(event) {
        if (this.terminalEventIsImageOnly(event)) {
          return "w-fit max-w-full bg-transparent p-0";
        }
        return String(event?.direction || "").toLowerCase() === "input"
          ? "w-fit max-w-full bg-[#262626] px-2.5 py-2"
          : "w-fit max-w-full bg-[#262626] px-2.5 py-2";
      },


      terminalEventIsImageOnly(event) {
        const parts = this.terminalEventParts(event);
        if (parts.length) {
          return parts.every((part) => (
            this.terminalEventPartPresentationKind(part) === "image" &&
            !this.terminalEventPartDisplayText(part)
          ));
        }
        return this.terminalEventIsImage(event) && !this.terminalEventDisplayText(event);
      },


      openTerminalMediaPreview(event, part = null) {
        const src = part ? this.terminalEventPartMediaUrl(event, part) : this.terminalEventMediaUrl(event);
        const isImage = part ? this.terminalEventPartIsImage(part) : this.terminalEventIsImage(event);
        if (!src || !isImage) {
          return;
        }
        this.terminalMediaPreview = {
          open: true,
          kind: "image",
          src,
          title: part ? this.terminalEventPartFileName(part) : this.terminalEventFileName(event),
          description: part ? this.terminalEventPartDisplayText(part) : this.terminalEventDisplayText(event)
        };
      },


      closeTerminalMediaPreview() {
        this.terminalMediaPreview = {
          open: false,
          kind: "",
          src: "",
          title: "",
          description: ""
        };
      },


      scrollTerminalTranscriptToBottom() {
        this.$nextTick(() => {
          const element = this.$refs?.terminalTranscriptScroll;
          if (element) {
            element.scrollTop = element.scrollHeight;
          }
        });
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


      skillRunLivePath(runId) {
        return this.currentSkill?.id
          ? buildSkillRunLivePath(this.currentSkill.id, runId)
          : buildRunLivePath(runId);
      },


      skillDebugRunLivePath(runId) {
        return this.currentSkill?.id
          ? buildSkillDebugRunLivePath(this.currentSkill.id, runId)
          : buildRunLivePath(runId);
      },


      runReplayPath(runId) {
        return this.currentSkill?.id
          ? buildSkillReplayPath(this.currentSkill.id, runId)
          : buildReplayPath(runId);
      },


      liveRunReplayTimeline() {
        if (!this.replayDetail || this.replayDetail.run?.id !== this.liveRun?.id) {
          return [];
        }
        return this.replayDetail.timeline || [];
      },


      liveRunReplayItemKey(item) {
        return [
          item?.seq_no ?? "",
          item?.event_type || "",
          item?.occurred_at || ""
        ].join(":");
      },


      selectLiveRunReplayItem(item) {
        this.selectedLiveRunReplayItemKey = this.liveRunReplayItemKey(item);
      },


      selectedLiveRunReplayItem() {
        const timeline = this.liveRunReplayTimeline();
        if (!timeline.length) {
          return null;
        }
        return (
          timeline.find((item) => this.liveRunReplayItemKey(item) === this.selectedLiveRunReplayItemKey) ||
          timeline[0]
        );
      },


      isLiveRunReplayItemSelected(item) {
        const selected = this.selectedLiveRunReplayItem();
        return Boolean(selected) && this.liveRunReplayItemKey(item) === this.liveRunReplayItemKey(selected);
      },


      liveRunReplayItemClass(item) {
        return this.isLiveRunReplayItemSelected(item)
          ? "border-l-2 border-orange-500 bg-orange-500/10"
          : "border-l-2 border-transparent hover:bg-slate-900/60";
      },


      liveRunReplayEventIcon(item) {
        const eventType = String(item?.event_type || "").toLowerCase();
        if (eventType.includes("terminal")) {
          return "forum";
        }
        if (eventType.includes("snapshot") || eventType.includes("token")) {
          return "account_tree";
        }
        if (eventType.includes("llm") || eventType.includes("model")) {
          return "psychology";
        }
        if (eventType.includes("tool") || eventType.includes("capability")) {
          return "handyman";
        }
        if (eventType.includes("wait")) {
          return "hourglass_empty";
        }
        return "timeline";
      },


      liveRunReplayEventTone(item) {
        const eventType = String(item?.event_type || "").toLowerCase();
        if (eventType.includes("terminal")) {
          return "bg-orange-500/15 text-orange-200";
        }
        if (eventType.includes("snapshot") || eventType.includes("token")) {
          return "bg-emerald-500/15 text-emerald-200";
        }
        if (eventType.includes("llm") || eventType.includes("model")) {
          return "bg-violet-500/15 text-violet-200";
        }
        if (eventType.includes("wait")) {
          return "bg-amber-500/15 text-amber-200";
        }
        return "bg-sky-500/15 text-sky-200";
      },


      liveRunReplaySelectedPayloadText() {
        const selected = this.selectedLiveRunReplayItem();
        return selected ? this.formatJson(selected.payload || {}) : "{}";
      },


      liveRunReplaySnapshots() {
        if (!this.replayDetail || this.replayDetail.run?.id !== this.liveRun?.id) {
          return [];
        }
        return this.replayDetail.snapshots || [];
      },


      liveRunReplayTerminalCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id ? this.replayDetail.terminal_events?.length || 0 : 0;
      },


      liveRunReplayTraceCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id ? this.replayDetail.trace_events?.length || 0 : 0;
      },


      replaySnapshotSummary(snapshot) {
        const summary = snapshot?.selection_summary;
        if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
          return "无 selection summary";
        }
        const enabled = Array.isArray(snapshot?.enabled_set) ? snapshot.enabled_set.length : 0;
        const keys = Object.keys(summary);
        return [`${keys.length} summary keys`, `${enabled} enabled items`].join(" · ");
      },


      liveRunProcessTerminalEvents() {
        return (this.liveRunTerminalEvents || [])
          .filter((event) => ["input", "output"].includes(String(event?.direction || "").toLowerCase()))
          .slice()
          .sort((left, right) => this.compareLiveRunProcessEvents(left, right));
      },


      compareLiveRunProcessEvents(left, right) {
        const leftTime = this.liveRunProcessEventTimestamp(left);
        const rightTime = this.liveRunProcessEventTimestamp(right);
        if (leftTime !== rightTime) {
          return leftTime - rightTime;
        }
        const leftSeq = Number(left?.seq_no || 0);
        const rightSeq = Number(right?.seq_no || 0);
        if (leftSeq !== rightSeq) {
          return leftSeq - rightSeq;
        }
        return this.liveRunProcessEventKey(left).localeCompare(this.liveRunProcessEventKey(right));
      },


      liveRunProcessEventTimestamp(event) {
        const value = new Date(event?.occurred_at || "").getTime();
        return Number.isFinite(value) ? value : 0;
      },


      liveRunProcessOriginTime() {
        const eventTimes = this.liveRunProcessTerminalEvents()
          .map((event) => this.liveRunProcessEventTimestamp(event))
          .filter((value) => Number.isFinite(value) && value > 0);
        if (eventTimes.length) {
          return Math.min(...eventTimes);
        }
        const fallback = new Date(this.liveRun?.started_at || this.liveRun?.created_at || "").getTime();
        return Number.isFinite(fallback) ? fallback : 0;
      },


      liveRunProcessEventAtMs(event) {
        const occurredAt = this.liveRunProcessEventTimestamp(event);
        const origin = this.liveRunProcessOriginTime();
        if (!Number.isFinite(occurredAt) || occurredAt <= 0 || !Number.isFinite(origin) || origin <= 0) {
          return 0;
        }
        return Math.max(0, occurredAt - origin);
      },


      liveRunProcessDurationMs() {
        const eventOffsets = this.liveRunProcessTerminalEvents().map((event) => this.liveRunProcessEventAtMs(event));
        return Math.max(1000, ...eventOffsets);
      },


      liveRunProcessEventPercent(event) {
        return Math.min(100, Math.max(0, (this.liveRunProcessEventAtMs(event) / this.liveRunProcessDurationMs()) * 100));
      },


      liveRunProcessTimelineEventLeftStyle(event) {
        return `left: clamp(4rem, ${this.liveRunProcessEventPercent(event)}%, calc(100% - 4rem))`;
      },


      liveRunProcessTicks() {
        const duration = this.liveRunProcessDurationMs();
        const tickCount = 5;
        return Array.from({ length: tickCount + 1 }, (_, index) => ({
          ms: Math.round((duration * index) / tickCount),
          percent: (index / tickCount) * 100
        }));
      },


      formatLiveRunProcessMs(value) {
        const milliseconds = Math.max(0, Number(value || 0));
        if (milliseconds < 1000) {
          return `${Math.round(milliseconds)} ms`;
        }
        const totalSeconds = Math.round(milliseconds / 1000);
        if (totalSeconds < 60) {
          return `${totalSeconds} s`;
        }
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return seconds ? `${minutes} m ${seconds} s` : `${minutes} m`;
      },


      liveRunProcessEventKey(event) {
        if (!event) {
          return "";
        }
        return [
          event.id || "",
          event.seq_no ?? "",
          event.occurred_at || "",
          event.direction || ""
        ].join(":");
      },


      liveRunProcessPartKind(part) {
        const presentationKind = this.terminalEventPartPresentationKind(part);
        if (presentationKind === "pdf") {
          return "file";
        }
        const mimeType = this.terminalEventPartMimeType(part);
        if (mimeType === "application/json" || mimeType.endsWith("+json")) {
          return "data";
        }
        return presentationKind;
      },


      liveRunProcessEventKind(event) {
        const parts = this.terminalEventParts(event);
        if (parts.length) {
          const kinds = Array.from(new Set(parts.map((part) => this.liveRunProcessPartKind(part))));
          return kinds.length === 1 ? kinds[0] : "mixed";
        }
        if (this.terminalEventMimeType(event).startsWith("multipart/")) {
          return "mixed";
        }
        if (this.terminalEventIsImage(event)) {
          return "image";
        }
        if (this.terminalEventIsAudio(event)) {
          return "audio";
        }
        if (this.terminalEventIsVideo(event)) {
          return "video";
        }
        if (this.terminalEventIsPdf(event) || this.terminalEventIsGenericFile(event)) {
          return "file";
        }
        if (this.terminalEventShouldShowJson(event)) {
          return "data";
        }
        if (this.terminalEventDisplayText(event)) {
          return "text";
        }
        if (event?.artifact_object_id) {
          return "file";
        }
        return "data";
      },


      liveRunProcessLaneIdForEvent(event) {
        const direction = String(event?.direction || "").toLowerCase() === "output" ? "output" : "input";
        return `${direction}.${this.liveRunProcessEventKind(event)}`;
      },


      liveRunProcessLaneSortValue(lane) {
        const directionOrder = lane?.direction === "output" ? 1 : 0;
        const kindOrder = {
          text: 0,
          mixed: 1,
          image: 2,
          audio: 3,
          video: 4,
          file: 5,
          data: 6
        };
        return directionOrder * 100 + (kindOrder[lane?.kind] ?? 99);
      },


      liveRunProcessLanes() {
        const lanes = new Map();
        this.liveRunProcessTerminalEvents().forEach((event) => {
          const id = this.liveRunProcessLaneIdForEvent(event);
          if (lanes.has(id)) {
            return;
          }
          const [direction, kind] = id.split(".");
          lanes.set(id, { id, direction, kind });
        });
        return Array.from(lanes.values()).sort((left, right) => this.liveRunProcessLaneSortValue(left) - this.liveRunProcessLaneSortValue(right));
      },


      liveRunProcessLaneGroup(laneId) {
        return String(laneId || "").startsWith("output.") ? "output" : "input";
      },


      liveRunProcessLaneGroupLabel(laneId) {
        return this.liveRunProcessLaneGroup(laneId) === "output" ? "输出" : "输入";
      },


      shouldShowLiveRunProcessLaneGroup(lane, laneIndex) {
        if (!lane) {
          return false;
        }
        const lanes = this.liveRunProcessLanes();
        const group = this.liveRunProcessLaneGroup(lane.id);
        return lanes.findIndex((item) => this.liveRunProcessLaneGroup(item.id) === group) === laneIndex;
      },


      liveRunProcessLaneLabel(lane) {
        const labels = {
          text: "文本",
          mixed: "多模态",
          image: "图片",
          audio: "音频",
          video: "视频",
          file: "文件",
          data: "数据"
        };
        return labels[lane?.kind] || lane?.id || "";
      },


      liveRunProcessLaneIcon(laneOrKind) {
        const kind = typeof laneOrKind === "string" ? laneOrKind : laneOrKind?.kind;
        const icons = {
          text: "text_fields",
          mixed: "dynamic_feed",
          image: "image",
          audio: "graphic_eq",
          video: "movie",
          file: "draft",
          data: "data_object"
        };
        return icons[kind] || "timeline";
      },


      liveRunProcessSkillTestToneKey(laneIdOrEvent) {
        const laneId = typeof laneIdOrEvent === "string"
          ? laneIdOrEvent
          : this.liveRunProcessLaneIdForEvent(laneIdOrEvent);
        const group = this.liveRunProcessLaneGroup(laneId);
        if (group === "output") {
          return "actual.output";
        }
        const kind = String(laneId || "").split(".")[1] || "text";
        return ["image", "audio", "video"].includes(kind) ? `input.${kind}` : "input.text";
      },


      liveRunProcessLaneTone(laneId) {
        const toneKey = this.liveRunProcessSkillTestToneKey(laneId);
        if (toneKey === "actual.output") {
          return "border-cyan-500/30 bg-cyan-500/10 text-cyan-200";
        }
        if (toneKey === "input.image") {
          return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
        }
        if (toneKey === "input.audio") {
          return "border-amber-500/25 bg-amber-500/10 text-amber-200";
        }
        if (toneKey === "input.video") {
          return "border-violet-500/25 bg-violet-500/10 text-violet-200";
        }
        return "border-orange-500/30 bg-orange-500/10 text-orange-200";
      },


      liveRunProcessEventsForLane(laneId) {
        return this.liveRunProcessTerminalEvents()
          .map((event, index) => ({ event, index }))
          .filter((item) => this.liveRunProcessLaneIdForEvent(item.event) === laneId)
          .map((item) => ({
            ...item,
            render_key: `run-process:${this.liveRunProcessEventKey(item.event)}:${item.index}`
          }));
      },


      liveRunProcessLaneEventCount(laneId) {
        return this.liveRunProcessEventsForLane(laneId).length;
      },


      liveRunProcessEventIcon(event) {
        return this.liveRunProcessLaneIcon(this.liveRunProcessEventKind(event));
      },


      liveRunProcessEventFrameTone(event) {
        const toneKey = this.liveRunProcessSkillTestToneKey(event);
        if (toneKey === "actual.output") {
          return "border-cyan-500/45 bg-cyan-500/10 text-cyan-100 ring-1 ring-cyan-500/20";
        }
        if (toneKey === "input.image") {
          return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100 ring-1 ring-emerald-500/20";
        }
        if (toneKey === "input.audio") {
          return "border-amber-500/45 bg-amber-500/10 text-amber-100 ring-1 ring-amber-500/20";
        }
        if (toneKey === "input.video") {
          return "border-violet-500/45 bg-violet-500/10 text-violet-100 ring-1 ring-violet-500/20";
        }
        return "border-orange-500/50 bg-orange-500/10 text-orange-100 ring-1 ring-orange-500/20";
      },


      liveRunProcessEventBadgeTone(event) {
        return this.liveRunProcessLaneTone(this.liveRunProcessLaneIdForEvent(event));
      },


      liveRunProcessEventTitle(event) {
        const direction = this.terminalDirectionLabel?.(event?.direction) || (event?.direction === "output" ? "输出" : "输入");
        const seq = event?.seq_no === undefined || event?.seq_no === null ? "" : ` #${event.seq_no}`;
        return `${direction}${seq}`;
      },


      liveRunProcessEventSummary(event) {
        if (!event) {
          return "";
        }
        const parts = this.terminalEventParts(event);
        if (parts.length) {
          const labels = parts.map((part) => {
            if (this.terminalEventPartIsText(part)) {
              return this.terminalEventPartDisplayText(part);
            }
            return this.terminalEventPartFileName(part);
          }).filter(Boolean);
          return labels.join(" + ") || event.event_kind || "multipart";
        }
        if (this.terminalEventDisplayText(event)) {
          return this.terminalEventDisplayText(event);
        }
        if (this.terminalEventIsImage(event) || this.terminalEventIsAudio(event) || this.terminalEventIsVideo(event) || this.terminalEventIsPdf(event) || this.terminalEventIsGenericFile(event)) {
          return this.terminalEventFileName(event);
        }
        if (this.terminalEventShouldShowJson(event)) {
          return this.terminalEventJsonText(event);
        }
        return event.event_kind || "terminal event";
      },


      liveRunProcessEventDetailText(event) {
        if (!event) {
          return "";
        }
        const parts = this.terminalEventParts(event);
        if (parts.length) {
          return parts
            .map((part, index) => {
              const label = this.terminalEventPartIsText(part)
                ? this.terminalEventPartDisplayText(part)
                : this.terminalEventPartFileName(part);
              return `part ${index + 1}: ${part.kind || "file"} ${part.mime_type || ""}\n${label || part.part_id || ""}`.trim();
            })
            .join("\n\n");
        }
        if (this.terminalEventDisplayText(event)) {
          return this.terminalEventDisplayText(event);
        }
        if (this.terminalEventShouldShowJson(event)) {
          return this.terminalEventJsonText(event);
        }
        if (this.terminalEventIsImage(event) || this.terminalEventIsAudio(event) || this.terminalEventIsVideo(event) || this.terminalEventIsPdf(event) || this.terminalEventIsGenericFile(event)) {
          return [this.terminalEventFileName(event), this.terminalEventFileMeta(event)].filter(Boolean).join("\n");
        }
        return event.event_kind || "";
      },


      liveRunProcessEventTooltip(event) {
        return [
          this.liveRunProcessEventTitle(event),
          this.formatLiveRunProcessMs(this.liveRunProcessEventAtMs(event)),
          this.liveRunProcessLaneLabel({ kind: this.liveRunProcessEventKind(event) }),
          this.liveRunProcessEventSummary(event)
        ].filter(Boolean).join(" | ");
      },


      liveRunProcessEventMetadata(event) {
        if (!event) {
          return [];
        }
        const pairs = [
          ["方向", this.terminalDirectionLabel?.(event.direction) || event.direction],
          ["内容", this.liveRunProcessLaneLabel({ kind: this.liveRunProcessEventKind(event) })],
          ["终端序号", event.seq_no === undefined || event.seq_no === null ? "" : `#${event.seq_no}`],
          ["相对时间", this.formatLiveRunProcessMs(this.liveRunProcessEventAtMs(event))],
          ["发生时间", this.formatDateTime?.(event.occurred_at) || event.occurred_at],
          ["事件类型", event.event_kind],
          ["MIME 类型", event.mime_type],
          ["事件 ID", event.id],
          ["Artifact 对象", event.artifact_object_id]
        ];
        return pairs
          .filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== "")
          .map(([label, value]) => ({ label, value: String(value) }));
      },


      selectLiveRunProcessEvent(event) {
        this.selectedLiveRunProcessEventKey = this.liveRunProcessEventKey(event);
      },


      openLiveRunProcessEventDrawer(event) {
        this.selectLiveRunProcessEvent(event);
      },


      closeLiveRunProcessEventDrawer() {
        this.selectedLiveRunProcessEventKey = "";
      },


      selectedLiveRunProcessEvent() {
        if (!this.selectedLiveRunProcessEventKey) {
          return null;
        }
        return this.liveRunProcessTerminalEvents().find((event) => this.liveRunProcessEventKey(event) === this.selectedLiveRunProcessEventKey) || null;
      },


      isLiveRunProcessEventSelected(event) {
        return Boolean(this.selectedLiveRunProcessEventKey && this.liveRunProcessEventKey(event) === this.selectedLiveRunProcessEventKey);
      },


      ensureLiveRunProcessSelection() {
        if (this.selectedLiveRunProcessEventKey && !this.selectedLiveRunProcessEvent()) {
          this.selectedLiveRunProcessEventKey = "";
        }
      },


      liveRunProcessNeighborItems() {
        const selected = this.selectedLiveRunProcessEvent();
        if (!selected) {
          return [];
        }
        const events = this.liveRunProcessTerminalEvents();
        const selectedKey = this.liveRunProcessEventKey(selected);
        const index = events.findIndex((event) => this.liveRunProcessEventKey(event) === selectedKey);
        return [
          { key: "previous", label: "上一个", event: index > 0 ? events[index - 1] : null, current: false },
          { key: "current", label: "当前", event: selected, current: true },
          { key: "next", label: "下一个", event: index >= 0 && index < events.length - 1 ? events[index + 1] : null, current: false }
        ];
      },


      liveRunProcessNeighborItemClass(item) {
        return item?.current
          ? "border-orange-500/35 bg-orange-500/10 ring-1 ring-orange-500/25"
          : "border-slate-800 bg-slate-950/45";
      },


      currentSkillInvocations() {
        if (!this.currentSkill) {
          return [];
        }
        return this.invocations.filter((invocation) => invocation.skill_definition_id === this.currentSkill.id);
      },


      currentSkillFilteredRuns() {
        return this.skillRuns.filter((run) =>
          this.inDateRange(run.created_at, this.runtimeFilters.created_from, this.runtimeFilters.created_to)
        );
      },


      isSkillDebugInvocation(invocation) {
        return invocation?.terminal_context?.debug_context?.kind === "skill_debug";
      },


      skillDebugInvocations() {
        return this.currentSkillInvocations()
          .filter((invocation) => this.isSkillDebugInvocation(invocation))
          .sort((left, right) => {
            const leftTime = new Date(left.created_at || 0).getTime();
            const rightTime = new Date(right.created_at || 0).getTime();
            return rightTime - leftTime;
          });
      },
  };
})();
