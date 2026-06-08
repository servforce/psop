(function () {
  const {
    normalizePath,
    resolveAdminRoute,
    buildSkillDetailPath,
    buildRunLivePath,
    buildRunEventsPath,
    buildSkillRunLivePath,
    buildSkillRunEventsPath,
    buildSkillDebugRunLivePath,
    buildReplayPath,
    buildSkillReplayPath,
    buildSkillTestScenarioPath,
    buildSkillTestScenarioNewPath,
    buildSkillTestScenarioRunReviewPath,
    buildCompilerArtifactPath,
    buildCompilerRequestPath,
    buildPlatformAgentRunPath,
    buildEvaluationReportPath,
    buildEvaluationFindingsPath,
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
          await this.loadInvocations(this.route.name === "skill-detail" ? this.currentSkill?.key : null);
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
        try {
          const isSameRun = this.liveRunLoadedRunId === runId;
          if (!isSameRun) {
            this.selectedLiveRunReplayItemKey = "";
            this.selectedLiveRunProcessEventKey = "";
            this.selectedLiveRunSnapshotBaseSeq = "";
            this.selectedLiveRunSnapshotTargetSeq = "";
          }
          const [run, bindings, runEvents, traceEvents, replayDetail, toolAuthorizations] = await Promise.all([
            this.apiRequest(`/runs/${runId}`),
            this.apiRequest(`/runs/${runId}/bindings`),
            this.apiRequest(`/runs/${runId}/events`),
            this.apiRequest(`/runs/${runId}/traces`),
            this.apiRequest(`/replay/runs/${runId}`),
            this.apiRequest(`/runs/${runId}/tool-authorizations`).catch(() => [])
          ]);
          this.liveRun = run;
          this.liveRunLoadedRunId = runId;
          this.liveRunBindings = bindings;
          this.liveRunTerminalSession = this.liveRunTerminalSessionFromRun(run);
          this.liveRunEvents = window.PSOPRuntimeEvents.mergeBySeq([], runEvents);
          this.liveRunToolAuthorizations = Array.isArray(toolAuthorizations) ? toolAuthorizations : [];
          this.updateLiveRunLatestTerminalSeq();
          this.ensureLiveRunProcessSelection();
          this.scrollRunEventTranscriptToBottom();
          this.liveRunTraceEvents = window.PSOPRuntimeEvents.mergeBySeq([], traceEvents);
          this.replayDetail = replayDetail;
          this.ensureLiveRunSnapshotCompareSelection();
          this.syncLiveRunInteractionTabFromRoute(isSameRun);
          this.syncLiveRunReplaySelectionFromLocation();
          this.connectRunWebSocket(runId);
          this.connectLiveRunToolAuthorizationWebSocket(runId);
        } finally {
          this.busy.liveRun = false;
        }
      },


      syncLiveRunInteractionTabFromRoute(isSameRun = false) {
        const allowedTabs = new Set(["run-events", "events", "io", "replay", "authorizations"]);
        const requestedView = String(this.route?.params?.view || "");
        const normalizedView = requestedView === "terminal" ? "run-events" : requestedView;
        if (allowedTabs.has(normalizedView)) {
          this.liveRunInteractionTab = normalizedView;
          return;
        }
        if (!isSameRun || !allowedTabs.has(this.liveRunInteractionTab)) {
          this.liveRunInteractionTab = "run-events";
        }
      },


      async sendTerminalInput() {
        const runId = this.liveRun?.id;
        const textPayload = this.terminalInputText();
        const attachments = this.terminalInputAttachments();
        if (!runId || !this.canSendTerminalInput()) {
          return;
        }
        this.busy.terminalInput = true;
        const optimisticEvent = this.buildOptimisticTerminalInputEvent(runId, textPayload, attachments);
        let acceptedByServer = false;
        this.mergeRunEvents([optimisticEvent]);
        this.terminalInputForm.payload = "";
        this.clearTerminalInputAttachments();
        try {
          if (attachments.length) {
            const result = await this.sendTerminalRuntimeMultipartEvent(runId, textPayload, attachments, optimisticEvent.external_event_id);
            acceptedByServer = true;
            this.mergeRunEvents([result.event]);
          } else {
            const response = await this.apiRequest(`/runs/${runId}/events`, {
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
            this.mergeRunEvents([response.event]);
          }
          await this.loadRunLive(runId);
        } catch (error) {
          if (!acceptedByServer) {
            acceptedByServer = await this.reconcileTerminalInputAcceptance(runId, optimisticEvent.external_event_id);
          }
          if (!acceptedByServer) {
            this.removeOptimisticRunEvent(optimisticEvent.id);
            this.terminalInputForm.payload = textPayload;
            this.terminalInputForm.attachments = attachments;
          } else {
            try {
              await this.loadRunLive(runId);
            } catch {
              // The accepted RunEvent remains the source of truth; a later refresh can recover run state.
            }
          }
          if (!acceptedByServer) {
            this.showNotice("error", error.message || "终端输入发送失败。");
          }
        } finally {
          this.busy.terminalInput = false;
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


      clearTerminalInputAttachments() {
        this.terminalInputForm.attachments = [];
        if (this.$refs?.terminalInputFile) {
          this.$refs.terminalInputFile.value = "";
        }
      },


      removeTerminalInputAttachment(attachmentId) {
        const removed = this.terminalInputAttachments().find((attachment) => attachment.id === attachmentId);
        if (removed?.preview_url) {
          URL.revokeObjectURL(removed.preview_url);
        }
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
        return this.apiRequest(`/runs/${runId}/events`, {
          method: "POST",
          headers: externalEventId ? { "Idempotency-Key": externalEventId } : {},
          body: formData
        });
      },


      connectRunWebSocket(runId) {
        if (this.liveRunWs && this.liveRunWsRunId === runId && this.liveRunWs.readyState === WebSocket.OPEN) {
          return;
        }
        this.disconnectRunWebSocket();
        this.liveRunWsRunId = runId;
        this.liveRunWsStatus = "connecting";
        const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, `/ws/runs/${runId}`));
        this.liveRunWs = socket;
        socket.addEventListener("open", () => {
          this.liveRunWsStatus = "open";
        });
        socket.addEventListener("message", (event) => {
          try {
            this.handleRunWsEvent(JSON.parse(event.data));
          } catch {
            // Ignore malformed runtime stream payloads; REST remains the recovery path.
          }
        });
        socket.addEventListener("close", () => {
          if (this.liveRunWs === socket) {
            this.liveRunWsStatus = "closed";
          }
        });
        socket.addEventListener("error", () => {
          if (this.liveRunWs === socket) {
            this.liveRunWsStatus = "error";
          }
        });
      },


      disconnectRunWebSocket() {
        if (this.liveRunWs) {
          this.liveRunWs.close();
        }
        this.liveRunWs = null;
        this.liveRunWsRunId = "";
        this.liveRunWsStatus = "idle";
      },


      connectLiveRunToolAuthorizationWebSocket(runId = this.liveRun?.id) {
        const id = String(runId || "").trim();
        if (!id || typeof WebSocket === "undefined" || typeof resolveWsUrl !== "function") {
          return false;
        }
        if (
          this.liveRunToolAuthorizationWs &&
          this.liveRunToolAuthorizationWsRunId === id &&
          [WebSocket.CONNECTING, WebSocket.OPEN].includes(this.liveRunToolAuthorizationWs.readyState)
        ) {
          return true;
        }

        this.disconnectLiveRunToolAuthorizationWebSocket();
        const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, "/ws/tool-authorizations"));
        this.liveRunToolAuthorizationWs = socket;
        this.liveRunToolAuthorizationWsRunId = id;
        this.liveRunToolAuthorizationWsStatus = "connecting";
        socket.addEventListener("open", () => {
          if (this.liveRunToolAuthorizationWs === socket) {
            this.liveRunToolAuthorizationWsStatus = "open";
          }
        });
        socket.addEventListener("message", (event) => {
          try {
            this.handleLiveRunToolAuthorizationWsEvent(JSON.parse(event.data));
          } catch {
            // Ignore malformed authorization events; REST refresh remains the recovery path.
          }
        });
        socket.addEventListener("close", () => {
          if (this.liveRunToolAuthorizationWs === socket) {
            this.liveRunToolAuthorizationWsStatus = "closed";
          }
        });
        socket.addEventListener("error", () => {
          if (this.liveRunToolAuthorizationWs === socket) {
            this.liveRunToolAuthorizationWsStatus = "error";
          }
        });
        return true;
      },


      disconnectLiveRunToolAuthorizationWebSocket() {
        if (this.liveRunToolAuthorizationWs) {
          this.liveRunToolAuthorizationWs.close();
        }
        this.liveRunToolAuthorizationWs = null;
        this.liveRunToolAuthorizationWsRunId = "";
        this.liveRunToolAuthorizationWsStatus = "idle";
      },


      handleLiveRunToolAuthorizationWsEvent(message) {
        if (!String(message?.event_type || "").startsWith("tool.authorization_") || !message?.payload) {
          return;
        }
        const currentRunId = String(this.liveRun?.id || this.liveRunToolAuthorizationWsRunId || "").trim();
        const messageRunId = String(message.run_id || message.payload?.run_id || "").trim();
        if (!currentRunId || messageRunId !== currentRunId) {
          return;
        }
        this.replaceLiveRunToolAuthorization(message.payload);
        this.replaceLiveRunReplayToolAuthorization(message.payload);
        this.refreshLiveRunReplayDetail(currentRunId);
      },


      handleRunWsEvent(event) {
        if (!event || event.event_type === "ws.connected") {
          return;
        }
        if (event.event_type === "run.updated" && event.payload) {
          this.applyLiveRunUpdate(event.payload);
        }
        if (["run.event.appended", "terminal.event.appended"].includes(event.event_type) && event.payload) {
          this.mergeRunEvents([event.payload]);
          this.mergeLiveRunReplayRunEvents([event.payload]);
          if (this.isToolAuthorizationRunEvent(event.payload)) {
            this.refreshLiveRunToolAuthorizations();
          }
        }
        if (["run.trace.appended", "trace.event.appended"].includes(event.event_type) && event.payload) {
          this.liveRunTraceEvents = window.PSOPRuntimeEvents.mergeBySeq(this.liveRunTraceEvents, [event.payload]);
          this.mergeLiveRunReplayRunTraces([event.payload]);
        }
        if (event.event_type === "session_token.snapshot.appended" && event.payload) {
          this.mergeLiveRunReplaySnapshots([event.payload]);
        }
        if (["binding.resolved", "binding.updated"].includes(event.event_type) && event.payload?.bindings) {
          this.liveRunBindings = window.PSOPRuntimeEvents.mergeById(this.liveRunBindings, event.payload.bindings);
          this.mergeLiveRunReplayBindings(event.payload.bindings);
        }
      },

      applyLiveRunUpdate(run) {
        if (!run?.id || this.liveRun?.id !== run.id) {
          return;
        }
        this.liveRun = { ...this.liveRun, ...run };
        if (this.replayDetail?.run?.id === run.id) {
          this.replayDetail.run = { ...this.replayDetail.run, ...run };
        }
        this.liveRunTerminalSession = this.liveRunTerminalSessionFromRun(this.liveRun);
        this.updateLiveRunLatestTerminalSeq();
      },


      mergeRunEvents(events) {
        const incoming = events || [];
        const realIncomingSeqs = new Set(
          incoming
            .filter((event) => event && !event._optimistic && Number.isFinite(Number(event.seq_no)))
            .map((event) => Number(event.seq_no))
        );
        const baseEvents = realIncomingSeqs.size
          ? this.liveRunEvents.filter((event) => !realIncomingSeqs.has(Number(event.seq_no)))
          : this.liveRunEvents;
        this.liveRunEvents = window.PSOPRuntimeEvents.mergeBySeq(baseEvents, incoming);
        this.updateLiveRunLatestTerminalSeq();
        this.ensureLiveRunProcessSelection();
        this.scrollRunEventTranscriptToBottom();
      },

      isToolAuthorizationRunEvent(event) {
        return ["tool_authorization_request", "tool_authorization_response"].includes(event?.event_kind);
      },

      async refreshLiveRunToolAuthorizations() {
        if (!this.liveRun?.id) {
          return;
        }
        try {
          const toolAuthorizations = await this.apiRequest(`/runs/${this.liveRun.id}/tool-authorizations`);
          this.liveRunToolAuthorizations = Array.isArray(toolAuthorizations) ? toolAuthorizations : [];
        } catch {
          // REST remains the recovery path on the next full Run Live refresh.
        }
      },

      liveRunAuthorizationCountByStatus(status) {
        return (this.liveRunToolAuthorizations || []).filter((authorization) => authorization.status === status).length;
      },

      liveRunPendingToolAuthorizations() {
        return (this.liveRunToolAuthorizations || []).filter((authorization) => authorization.status === "pending");
      },

      replaceLiveRunToolAuthorization(authorization) {
        if (!authorization?.id) {
          return;
        }
        const index = this.liveRunToolAuthorizations.findIndex((item) => item.id === authorization.id);
        if (index >= 0) {
          this.liveRunToolAuthorizations.splice(index, 1, authorization);
        } else {
          this.liveRunToolAuthorizations.unshift(authorization);
        }
        if (typeof this.replaceToolAuthorization === "function") {
          this.replaceToolAuthorization(authorization);
        }
      },

      async decideLiveRunToolAuthorization(authorization, decision) {
        if (!authorization?.id || !["approve", "reject"].includes(decision)) {
          return;
        }
        this.busy.toolAuthorizationAction = true;
        try {
          const updated = await this.apiRequest(`/tool-authorizations/${encodeURIComponent(authorization.id)}/${decision}`, {
            method: "POST",
            body: JSON.stringify({
              response_payload: {
                decision_source: "run_live_ui"
              }
            })
          });
          this.replaceLiveRunToolAuthorization(updated);
          this.showNotice("success", decision === "approve" ? "工具授权已批准。" : "工具授权已拒绝。");
          if (this.liveRun?.id) {
            await this.loadRunLive(this.liveRun.id);
          }
        } catch (error) {
          this.showNotice("error", error.message || "工具授权处理失败。");
        } finally {
          this.busy.toolAuthorizationAction = false;
        }
      },


      updateLiveRunLatestTerminalSeq() {
        if (!this.liveRun) {
          return;
        }
        const eventSeqs = this.liveRunEvents.map((event) => Number(event.seq_no) || 0);
        const latestSeq = eventSeqs.length
          ? Math.max(...eventSeqs)
          : Number(this.liveRun.latest_run_event_seq || this.liveRun.latest_terminal_seq || 0);
        this.liveRun.latest_run_event_seq = latestSeq || 0;
        this.liveRun.latest_terminal_seq = latestSeq || 0;
      },


      nextOptimisticTerminalSeq() {
        return (
          Math.max(
            Number(this.liveRun?.latest_run_event_seq || this.liveRun?.latest_terminal_seq || 0),
            ...this.liveRunEvents.map((event) => Number(event.seq_no) || 0)
          ) + 1
        );
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
          const previewUrl = URL.createObjectURL(file);
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


      removeOptimisticRunEvent(eventId) {
        this.liveRunEvents = this.liveRunEvents.filter((event) => event.id !== eventId);
        this.updateLiveRunLatestTerminalSeq();
      },


      async reconcileTerminalInputAcceptance(runId, externalEventId) {
        if (!runId || !externalEventId) {
          return false;
        }
        try {
          const events = await this.apiRequest(`/runs/${runId}/events`);
          const acceptedEvent = events.find((event) => event.external_event_id === externalEventId);
          if (!acceptedEvent) {
            return false;
          }
          this.mergeRunEvents([acceptedEvent]);
          return true;
        } catch {
          return false;
        }
      },


      liveRunTerminalSessionFromRun(run) {
        const sessionId = String(run?.terminal_session_id || "").trim();
        if (!sessionId) {
          return null;
        }
        return {
          id: sessionId,
          run_id: run.id,
          status: this.terminalRunEnded(run) ? "closed" : "open",
          opened_at: run.started_at || run.created_at || null,
          closed_at: run.ended_at || null,
          created_at: run.created_at || null
        };
      },


      terminalRunEnded(run = this.liveRun) {
        return ["succeeded", "failed", "cancelled", "canceled"].includes(String(run?.status || "").toLowerCase());
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


      runEventParts(event) {
        return Array.isArray(event?.parts) ? event.parts : [];
      },


      runEventHasParts(event) {
        return this.runEventParts(event).length > 0;
      },


      runEventPartMimeType(part) {
        return String(part?.mime_type || "").toLowerCase();
      },


      runEventPartIsText(part) {
        return String(part?.kind || "").toLowerCase() === "text" || this.runEventPartMimeType(part).startsWith("text/");
      },


      runEventPartIsImage(part) {
        return String(part?.kind || "").toLowerCase() === "image" || this.runEventPartMimeType(part).startsWith("image/");
      },


      runEventPartIsAudio(part) {
        return String(part?.kind || "").toLowerCase() === "audio" || this.runEventPartMimeType(part).startsWith("audio/");
      },


      runEventPartIsVideo(part) {
        return String(part?.kind || "").toLowerCase() === "video" || this.runEventPartMimeType(part).startsWith("video/");
      },


      runEventPartDisplayText(part) {
        return String(part?.text || "").trim();
      },


      runEventPartFileName(part) {
        const metadata = part?.metadata && typeof part.metadata === "object" ? part.metadata : {};
        const value = metadata.filename || metadata.name || part?.part_id || "run-event-attachment";
        return String(value).split("/").filter(Boolean).pop() || "run-event-attachment";
      },


      runEventPartMediaUrl(event, part) {
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
        return `${this.apiBaseUrl}/runs/${encodeURIComponent(runId)}/events/${encodeURIComponent(event.id)}/parts/${encodeURIComponent(part.part_id)}/content`;
      },


      runEventMimeType(event) {
        return String(event?.mime_type || "").toLowerCase();
      },


      runEventFileExtension(event) {
        const fileName = this.runEventFileName(event).toLowerCase();
        const match = fileName.match(/\.([a-z0-9]+)$/);
        return match ? match[1] : "";
      },


      runEventInferredMimeType(event) {
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
        const extension = this.runEventFileExtension(event);
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


      runEventPresentationMimeType(event) {
        const mimeType = this.runEventMimeType(event);
        if (mimeType && mimeType !== "application/octet-stream") {
          return mimeType;
        }
        return this.runEventInferredMimeType(event) || mimeType;
      },


      runEventPayloadObject(event) {
        const payload = event?.payload_inline;
        return payload && typeof payload === "object" && !Array.isArray(payload) ? payload : null;
      },


      runEventPayloadTextValue(event, keys) {
        const payload = this.runEventPayloadObject(event);
        if (!payload) {
          return "";
        }
        const match = keys.find((key) => {
          const value = payload[key];
          return value !== null && value !== undefined && typeof value !== "object" && String(value).trim();
        });
        return match ? String(payload[match]).trim() : "";
      },


      runEventDisplayText(event) {
        const payload = event?.payload_inline;
        if (typeof payload === "string") {
          return payload;
        }
        if (payload === null || payload === undefined) {
          return "";
        }
        return this.runEventPayloadTextValue(event, [
          "description",
          "message",
          "text",
          "content",
          "summary",
          "user_input",
          "final_response",
          "output"
        ]);
      },


      runEventJsonText(event) {
        const payload = event?.payload_inline;
        if (payload === null || payload === undefined) {
          return "";
        }
        if (typeof payload === "string") {
          return payload;
        }
        return JSON.stringify(payload, null, 2);
      },


      runEventSourceUrl(event) {
        const payload = this.runEventPayloadObject(event);
        if (!payload) {
          return "";
        }
        const key = ["url", "src", "content_url", "preview_url", "data_url"].find(
          (candidate) => typeof payload[candidate] === "string" && payload[candidate].trim()
        );
        return key ? payload[key].trim() : "";
      },


      runEventMediaUrl(event) {
        const inlineUrl = this.runEventSourceUrl(event);
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
        return `${this.apiBaseUrl}/runs/${encodeURIComponent(runId)}/events/${encodeURIComponent(event.id)}/content`;
      },


      runEventIsImage(event) {
        return this.runEventPresentationMimeType(event).startsWith("image/");
      },


      runEventIsAudio(event) {
        return this.runEventPresentationMimeType(event).startsWith("audio/");
      },


      runEventIsVideo(event) {
        return this.runEventPresentationMimeType(event).startsWith("video/");
      },


      runEventIsJson(event) {
        const mimeType = this.runEventPresentationMimeType(event);
        return mimeType === "application/json" || mimeType.endsWith("+json");
      },


      runEventIsPdf(event) {
        return this.runEventPresentationMimeType(event) === "application/pdf";
      },


      runEventIsGenericFile(event) {
        const mimeType = this.runEventPresentationMimeType(event);
        const eventKind = String(event?.event_kind || "").toLowerCase();
        return Boolean(
          this.runEventMediaUrl(event) &&
            !this.runEventIsImage(event) &&
            !this.runEventIsAudio(event) &&
            !this.runEventIsVideo(event) &&
            !this.runEventIsPdf(event) &&
            !this.runEventIsJson(event) &&
            (event?.artifact_object_id || eventKind.includes(".file.") || ["application/pdf", "application/octet-stream"].includes(mimeType))
        );
      },


      runEventShouldShowJson(event) {
        const payload = event?.payload_inline;
        if (!payload || typeof payload !== "object") {
          return false;
        }
        if (this.runEventIsJson(event)) {
          return true;
        }
        if (
          this.runEventIsImage(event) ||
          this.runEventIsAudio(event) ||
          this.runEventIsVideo(event) ||
          this.runEventIsPdf(event) ||
          this.runEventIsGenericFile(event)
        ) {
          return false;
        }
        return !this.runEventDisplayText(event);
      },


      runEventShouldShowPlainText(event) {
        return Boolean(
          this.runEventDisplayText(event) &&
            !this.runEventShouldShowJson(event) &&
            !this.runEventIsImage(event) &&
            !this.runEventIsAudio(event) &&
            !this.runEventIsVideo(event) &&
            !this.runEventIsPdf(event)
        );
      },


      runEventFileName(event) {
        const payload = this.runEventPayloadObject(event);
        const value =
          payload?.filename ||
          payload?.name ||
          payload?.title ||
          payload?.object_key ||
          event?.event_kind ||
          "run-event-attachment";
        return String(value).split("/").filter(Boolean).pop() || "run-event-attachment";
      },


      runEventFileSize(event) {
        const payload = this.runEventPayloadObject(event);
        const size = Number(payload?.size_bytes ?? payload?.size ?? 0);
        return Number.isFinite(size) && size > 0 ? size : 0;
      },


      runEventFileMeta(event) {
        const size = this.runEventFileSize(event);
        return size ? this.formatBytes(size) : "";
      },


      runEventFileIcon(event) {
        const mimeType = this.runEventPresentationMimeType(event);
        if (mimeType === "application/pdf") {
          return "picture_as_pdf";
        }
        if (mimeType.startsWith("text/") || mimeType === "application/json") {
          return "description";
        }
        return "draft";
      },


      runEventActorLabel(event) {
        return String(event?.direction || "").toLowerCase() === "output" ? "Runtime" : "用户";
      },


      runEventRowClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "justify-end" : "justify-start";
      },


      runEventMessageShellClass(event) {
        return "w-fit";
      },


      runEventMessageShellStyle(event) {
        return "max-width: 70%;";
      },


      runEventContentClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "items-end" : "items-start";
      },


      runEventMetaClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "justify-end text-right" : "justify-start";
      },


      runEventBubbleClass(event) {
        return String(event?.direction || "").toLowerCase() === "input"
          ? "w-fit max-w-full bg-[#262626]"
          : "w-fit max-w-full bg-[#262626]";
      },


      liveRunRawEvents() {
        return (this.liveRunEvents || [])
          .slice()
          .sort((left, right) => {
            const leftSeq = Number(left?.seq_no || 0);
            const rightSeq = Number(right?.seq_no || 0);
            if (leftSeq !== rightSeq) {
              return leftSeq - rightSeq;
            }
            return String(left?.id || "").localeCompare(String(right?.id || ""));
          });
      },


      liveRunRawEventKinds() {
        return [...new Set(this.liveRunRawEvents().map((item) => item?.event_kind).filter(Boolean))].sort();
      },


      liveRunRawEventSearchText(rawEvent) {
        return [
          rawEvent?.id,
          rawEvent?.direction,
          rawEvent?.event_kind,
          rawEvent?.mime_type,
          rawEvent?.external_event_id,
          rawEvent?.source_ref,
          rawEvent?.payload_inline,
          rawEvent?.parts
        ]
          .map((value) => {
            if (value === null || value === undefined) {
              return "";
            }
            return typeof value === "string" ? value : JSON.stringify(value);
          })
          .join("\n")
          .toLowerCase();
      },


      liveRunRawEventMatchesFilters(rawEvent) {
        const filters = this.liveRunEventFilters || {};
        const direction = String(filters.direction || "").trim().toLowerCase();
        const eventKind = String(filters.event_kind || "").trim();
        const query = String(filters.q || "").trim().toLowerCase();
        if (direction && String(rawEvent?.direction || "").toLowerCase() !== direction) {
          return false;
        }
        if (eventKind && rawEvent?.event_kind !== eventKind) {
          return false;
        }
        if (query && !this.liveRunRawEventSearchText(rawEvent).includes(query)) {
          return false;
        }
        return true;
      },


      liveRunRawFilteredEvents() {
        return this.liveRunRawEvents().filter((rawEvent) => this.liveRunRawEventMatchesFilters(rawEvent));
      },


      liveRunRawEventSourceLabel(rawEvent) {
        const source = rawEvent?.source_ref && typeof rawEvent.source_ref === "object" ? rawEvent.source_ref : {};
        return [source.kind, source.connection_id, source.node_id].filter(Boolean).join(" · ") || "source:N/A";
      },


      liveRunRawEventPartsSummary(rawEvent) {
        const parts = this.runEventParts(rawEvent);
        if (!parts.length) {
          return "0 parts";
        }
        const labels = parts.map((part) => [part.kind, part.mime_type].filter(Boolean).join(":") || part.part_id || "part");
        return `${parts.length} parts · ${labels.join(", ")}`;
      },


      liveRunRawEventJsonText(rawEvent) {
        return typeof this.formatJson === "function"
          ? this.formatJson(rawEvent || {})
          : JSON.stringify(rawEvent || {}, null, 2);
      },


      liveRunRawEventsDownloadPayload() {
        const filters = this.liveRunEventFilters || {};
        const events = this.liveRunRawFilteredEvents();
        return {
          schema: "psop-run-events-export/v1",
          run_id: this.liveRun?.id || "",
          exported_at: new Date().toISOString(),
          filters: {
            q: String(filters.q || ""),
            direction: String(filters.direction || ""),
            event_kind: String(filters.event_kind || "")
          },
          event_count: events.length,
          events
        };
      },


      downloadLiveRunRawEvents() {
        const payload = this.liveRunRawEventsDownloadPayload();
        const filename = `psop-run-events-${payload.run_id || "run"}.json`;
        const content = JSON.stringify(payload, null, 2);
        if (typeof document === "undefined" || typeof Blob === "undefined" || typeof URL === "undefined") {
          return payload;
        }
        const blob = new Blob([content], { type: "application/json" });
        const href = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = href;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(href);
        if (typeof this.showNotice === "function") {
          this.showNotice("success", "RunEvent 数据已导出。");
        }
        return payload;
      },


      openTerminalMediaPreview(event, part = null) {
        const src = part ? this.runEventPartMediaUrl(event, part) : this.runEventMediaUrl(event);
        const isImage = part ? this.runEventPartIsImage(part) : this.runEventIsImage(event);
        if (!src || !isImage) {
          return;
        }
        this.terminalMediaPreview = {
          open: true,
          kind: "image",
          src,
          title: part ? this.runEventPartFileName(part) : this.runEventFileName(event),
          description: part ? this.runEventPartDisplayText(part) : this.runEventDisplayText(event)
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


      scrollRunEventTranscriptToBottom() {
        this.$nextTick(() => {
          const element = this.$refs?.runEventTranscriptScroll;
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
          await this.refreshLiveRunReplayDetail(runId);
        } finally {
          this.busy.replayDetail = false;
        }
      },

      async refreshLiveRunReplayDetail(runId = this.liveRun?.id) {
        const id = String(runId || "").trim();
        if (!id || typeof this.apiRequest !== "function") {
          return null;
        }
        try {
          const replayDetail = await this.apiRequest(`/replay/runs/${encodeURIComponent(id)}`);
          const liveRunId = String(this.liveRun?.id || "").trim();
          const replayRunId = String(replayDetail?.run?.id || "").trim();
          if (!replayDetail || replayRunId !== id || (liveRunId && replayRunId !== liveRunId)) {
            return null;
          }
          this.replayDetail = replayDetail;
          this.ensureLiveRunSnapshotCompareSelection();
          this.syncLiveRunReplaySelectionFromLocation();
          return replayDetail;
        } catch {
          // Replay can recover on the next explicit Run Live refresh.
          return null;
        }
      },

      liveRunReplayCanMerge() {
        return Boolean(this.replayDetail?.run?.id && this.liveRun?.id && this.replayDetail.run.id === this.liveRun.id);
      },


      liveRunReplayEventBelongsToCurrentRun(event) {
        const runId = String(event?.run_id || "").trim();
        return !runId || runId === this.liveRun?.id;
      },


      mergeLiveRunReplayRunEvents(events = []) {
        if (!this.liveRunReplayCanMerge()) {
          return;
        }
        const incoming = (events || []).filter((event) => event?.id && this.liveRunReplayEventBelongsToCurrentRun(event));
        if (!incoming.length) {
          return;
        }
        const merged = window.PSOPRuntimeEvents.mergeBySeq(
          this.replayDetail.run_events || [],
          incoming
        );
        this.replayDetail.run_events = merged;
        this.mergeLiveRunReplayTimeline(incoming.map((event) => this.liveRunReplayRunEventTimelineItem(event)));
      },


      mergeLiveRunReplayRunTraces(traces = []) {
        if (!this.liveRunReplayCanMerge()) {
          return;
        }
        const incoming = (traces || []).filter((trace) => trace?.id && this.liveRunReplayEventBelongsToCurrentRun(trace));
        if (!incoming.length) {
          return;
        }
        const merged = window.PSOPRuntimeEvents.mergeBySeq(
          this.replayDetail.run_traces || [],
          incoming
        );
        this.replayDetail.run_traces = merged;
        this.mergeLiveRunReplayTimeline(incoming.map((trace) => this.liveRunReplayTraceTimelineItem(trace)));
        this.mergeLiveRunReplayEgNodePath(incoming);
      },


      mergeLiveRunReplayBindings(bindings = []) {
        if (!this.liveRunReplayCanMerge() || !Array.isArray(bindings) || !bindings.length) {
          return;
        }
        this.replayDetail.bindings = window.PSOPRuntimeEvents.mergeById(this.replayDetail.bindings || [], bindings);
      },


      mergeLiveRunReplaySnapshots(snapshots = []) {
        if (!this.liveRunReplayCanMerge()) {
          return;
        }
        const incoming = (snapshots || []).filter((snapshot) => (
          snapshot?.id &&
          (!snapshot.run_id || snapshot.run_id === this.liveRun?.id)
        ));
        if (!incoming.length) {
          return;
        }
        this.replayDetail.snapshots = window.PSOPRuntimeEvents.mergeBySeq(this.replayDetail.snapshots || [], incoming);
        if (this.liveRun) {
          const latestSeq = Math.max(
            Number(this.liveRun.latest_snapshot_seq || 0),
            ...incoming.map((snapshot) => Number(snapshot.seq_no || 0))
          );
          this.liveRun.latest_snapshot_seq = latestSeq;
        }
        this.ensureLiveRunSnapshotCompareSelection();
      },


      mergeLiveRunReplayTimeline(items = []) {
        if (!this.liveRunReplayCanMerge()) {
          return;
        }
        const map = new Map();
        for (const item of this.replayDetail.timeline || []) {
          map.set(this.liveRunReplayTimelineMergeKey(item), item);
        }
        for (const item of items || []) {
          if (!item) {
            continue;
          }
          const key = this.liveRunReplayTimelineMergeKey(item);
          map.set(key, { ...(map.get(key) || {}), ...item });
        }
        this.replayDetail.timeline = Array.from(map.values()).sort((left, right) => this.compareLiveRunReplayItems(left, right));
      },


      liveRunReplayTimelineMergeKey(item) {
        if (item?.source_kind && item?.source_id) {
          return `${item.source_kind}:${item.source_id}`;
        }
        return [item?.seq_no ?? "", item?.event_type || "", item?.occurred_at || ""].join(":");
      },


      compareLiveRunReplayItems(left, right) {
        const leftTime = Date.parse(left?.occurred_at || "") || 0;
        const rightTime = Date.parse(right?.occurred_at || "") || 0;
        if (leftTime !== rightTime) {
          return leftTime - rightTime;
        }
        const leftSeq = Number(left?.seq_no || 0);
        const rightSeq = Number(right?.seq_no || 0);
        if (leftSeq !== rightSeq) {
          return leftSeq - rightSeq;
        }
        return String(left?.event_type || "").localeCompare(String(right?.event_type || ""));
      },


      liveRunReplayRunEventTimelineItem(event) {
        return {
          seq_no: Number(event?.seq_no || 0),
          phase: "terminal",
          event_type: "run.event.appended",
          title: event?.direction === "input" ? "终端输入" : "终端输出",
          summary: this.liveRunReplayRunEventSummary(event),
          payload: event || {},
          occurred_at: event?.occurred_at || event?.created_at || "",
          source_kind: "run_event",
          source_id: event?.id || ""
        };
      },


      liveRunReplayRunEventSummary(event) {
        const parts = Array.isArray(event?.parts) ? event.parts : [];
        if (parts.length) {
          return parts
            .map((part) => part?.text || part?.metadata?.filename || part?.part_id || "")
            .filter(Boolean)
            .join("\n");
        }
        if (typeof event?.payload_inline === "string") {
          return event.payload_inline;
        }
        if (event?.payload_inline === null || event?.payload_inline === undefined) {
          return event?.event_kind || "";
        }
        try {
          return JSON.stringify(event.payload_inline);
        } catch {
          return String(event.payload_inline);
        }
      },


      liveRunReplayTraceTimelineItem(trace) {
        const payload = trace?.payload && typeof trace.payload === "object" ? trace.payload : {};
        const observation = payload.observation && typeof payload.observation === "object" ? payload.observation : {};
        const summary = (
          observation.final_response ||
          observation.content ||
          observation.result ||
          observation.user_input ||
          payload.summary ||
          trace?.event_type ||
          ""
        );
        return {
          seq_no: Number(trace?.seq_no || 0),
          phase: trace?.phase || "",
          event_type: trace?.event_type || "",
          title: this.liveRunReplayTraceTitle(trace),
          summary: String(summary),
          payload,
          occurred_at: trace?.occurred_at || trace?.created_at || "",
          source_kind: "run_trace",
          source_id: trace?.id || "",
          agent_run_id: trace?.agent_run_id || payload.agent_run_id || ""
        };
      },


      liveRunReplayTraceTitle(trace) {
        const titles = {
          "binding.resolved": "绑定解析",
          "binding.updated": "绑定更新",
          "runtime.input.accepted": "输入",
          "runtime.wait_checkpoint.entered": "等待现场证据",
          "gateway.inference.completed": "LLM 输出",
          "gateway.inference.failed": "LLM 失败",
          "gateway.tool.completed": "工具调用",
          "runtime.final.completed": "最终结果",
          "runtime.aborted": "已中止",
          "runtime.message_processing.failed": "消息处理失败",
          "runtime.failed": "运行失败"
        };
        return titles[trace?.event_type] || trace?.event_type || "";
      },


      mergeLiveRunReplayEgNodePath(traces = []) {
        const incoming = (traces || [])
          .map((trace) => this.liveRunReplayEgNodePathItemFromTrace(trace))
          .filter(Boolean);
        if (!incoming.length) {
          return;
        }
        const map = new Map();
        for (const item of this.replayDetail.eg_node_path || []) {
          map.set(`${item.trace_id}:${item.node_id}`, item);
        }
        for (const item of incoming) {
          const key = `${item.trace_id}:${item.node_id}`;
          map.set(key, { ...(map.get(key) || {}), ...item });
        }
        this.replayDetail.eg_node_path = Array.from(map.values()).sort((left, right) => this.compareLiveRunReplayItems(left, right));
      },


      liveRunReplayEgNodePathItemFromTrace(trace) {
        const nodeId = this.liveRunReplayEgNodeIdFromTrace(trace);
        if (!nodeId) {
          return null;
        }
        const timelineItem = this.liveRunReplayTraceTimelineItem(trace);
        const payload = trace?.payload && typeof trace.payload === "object" ? trace.payload : {};
        const wait = payload.wait && typeof payload.wait === "object" ? payload.wait : {};
        return {
          seq_no: Number(trace?.seq_no || 0),
          trace_id: trace?.id || "",
          node_id: nodeId,
          node_kind: String(payload.node_kind || ""),
          phase: trace?.phase || "",
          event_type: trace?.event_type || "",
          title: timelineItem.title,
          summary: timelineItem.summary,
          checkpoint_id: String(payload.checkpoint_id || wait.checkpoint_id || ""),
          agent_run_id: trace?.agent_run_id || null,
          occurred_at: trace?.occurred_at || trace?.created_at || ""
        };
      },


      liveRunReplayEgNodeIdFromTrace(trace) {
        const payload = trace?.payload && typeof trace.payload === "object" ? trace.payload : {};
        const wait = payload.wait && typeof payload.wait === "object" ? payload.wait : {};
        if (typeof payload.node_id === "string" && payload.node_id) {
          return payload.node_id;
        }
        if (typeof wait.entered_by_node === "string" && wait.entered_by_node) {
          return wait.entered_by_node;
        }
        const phase = String(trace?.phase || "");
        const eventType = String(trace?.event_type || "");
        if (phase && !["binding", "fork", "failed", "cancelled"].includes(phase) && /^(runtime|gateway)\./.test(eventType)) {
          return phase;
        }
        return "";
      },


      skillRunLivePath(runId) {
        return this.currentSkill?.id
          ? buildSkillRunLivePath(this.currentSkill.id, runId)
          : buildRunLivePath(runId);
      },


      runEventsPath(runId) {
        return this.currentSkill?.id
          ? buildSkillRunEventsPath(this.currentSkill.id, runId)
          : buildRunEventsPath(runId);
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

      liveRunCompileRequestId() {
        return String(this.liveRun?.compile_request_id || this.replayDetail?.provenance?.compile_request_id || "").trim();
      },


      liveRunCompileRequestPath() {
        return buildCompilerRequestPath(this.liveRunCompileRequestId());
      },


      openLiveRunCompileRequest() {
        const compileRequestId = this.liveRunCompileRequestId();
        if (!compileRequestId) {
          return;
        }
        this.navigate(this.liveRunCompileRequestPath());
      },


      liveRunReplayAgentRunPath(agentRunId, focus = {}) {
        const normalized = String(agentRunId || "").trim();
        return normalized ? buildPlatformAgentRunPath(normalized, focus) : "";
      },


      openLiveRunReplayAgentRun(agentRunOrId, focus = {}) {
        const agentRunId = typeof agentRunOrId === "string"
          ? agentRunOrId
          : (agentRunOrId?.id || agentRunOrId?.agent_run_id || "");
        const path = this.liveRunReplayAgentRunPath(agentRunId, focus);
        if (!path) {
          return;
        }
        this.navigate(path);
      },


      liveRunReplayAgentEventPath(event) {
        return this.liveRunReplayAgentRunPath(event?.agent_run_id, {
          tab: "events",
          event_id: event?.id || ""
        });
      },


      liveRunReplayModelCallPath(call) {
        return this.liveRunReplayAgentRunPath(call?.agent_run_id, {
          tab: "model",
          model_call_id: call?.id || ""
        });
      },


      liveRunReplayToolCallPath(call) {
        return this.liveRunReplayAgentRunPath(call?.agent_run_id, {
          tab: "tools",
          tool_call_id: call?.id || ""
        });
      },


      liveRunReplayToolAuthorizationPath(authorization) {
        return this.liveRunReplayAgentRunPath(authorization?.agent_run_id, {
          tab: "authorizations",
          authorization_id: authorization?.id || ""
        });
      },


      liveRunReplayEvaluationPath(evaluation) {
        const evaluationId = String(typeof evaluation === "string" ? evaluation : evaluation?.id || "").trim();
        return evaluationId ? buildEvaluationReportPath(evaluationId) : "";
      },


      liveRunReplayFindingPath(finding) {
        const runId = String(finding?.run_id || this.replayDetail?.run?.id || this.liveRun?.id || "").trim();
        const basePath = buildEvaluationFindingsPath();
        return runId ? `${basePath}?run_id=${encodeURIComponent(runId)}` : basePath;
      },


      openLiveRunReplayEvaluation(evaluation) {
        const path = this.liveRunReplayEvaluationPath(evaluation);
        if (!path) {
          return;
        }
        this.navigate(path);
      },


      openLiveRunReplayFinding(finding) {
        const path = this.liveRunReplayFindingPath(finding);
        if (!path) {
          return;
        }
        this.navigate(path);
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


      syncLiveRunReplaySelectionFromLocation() {
        if (typeof window === "undefined" || !window.location) {
          return;
        }
        const params = new URLSearchParams(window.location.search || "");
        const eventId = String(params.get("event_id") || "").trim();
        const traceId = String(params.get("trace_id") || "").trim();
        const seqNo = String(params.get("seq_no") || "").trim();
        if (!eventId && !traceId && !seqNo) {
          return;
        }
        const timeline = this.liveRunReplayTimeline();
        const selected = timeline.find((item) => {
          const payload = item?.payload || {};
          const payloadEventId = String(
            item?.source_id ||
              payload.id ||
              payload.event_id ||
              payload.run_event_id ||
              payload.run_trace_id ||
              payload.trace_event_id ||
              payload.trace_id ||
              ""
          ).trim();
          return Boolean((eventId && payloadEventId === eventId) || (traceId && payloadEventId === traceId));
        }) || timeline.find((item) => Boolean(seqNo && String(item?.seq_no ?? "") === seqNo));
        if (selected) {
          this.selectedLiveRunReplayItemKey = this.liveRunReplayItemKey(selected);
        }
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
        return (this.replayDetail.snapshots || [])
          .slice()
          .sort((left, right) => Number(left?.seq_no || 0) - Number(right?.seq_no || 0));
      },


      liveRunReplaySnapshotKey(snapshot) {
        return String(snapshot?.seq_no ?? snapshot?.id ?? "");
      },


      liveRunReplaySnapshotLabel(snapshot) {
        const seq = snapshot?.seq_no ?? "N/A";
        return [`#${seq}`, this.formatDateTime(snapshot?.created_at)].filter(Boolean).join(" · ");
      },


      liveRunReplaySnapshotByKey(key) {
        const normalized = String(key || "");
        return this.liveRunReplaySnapshots().find(
          (snapshot) => this.liveRunReplaySnapshotKey(snapshot) === normalized
        ) || null;
      },


      ensureLiveRunSnapshotCompareSelection() {
        const snapshots = this.liveRunReplaySnapshots();
        if (snapshots.length < 2) {
          this.selectedLiveRunSnapshotBaseSeq = "";
          this.selectedLiveRunSnapshotTargetSeq = "";
          return;
        }
        const keys = snapshots.map((snapshot) => this.liveRunReplaySnapshotKey(snapshot));
        if (!keys.includes(String(this.selectedLiveRunSnapshotBaseSeq || ""))) {
          this.selectedLiveRunSnapshotBaseSeq = keys[Math.max(0, keys.length - 2)];
        }
        if (!keys.includes(String(this.selectedLiveRunSnapshotTargetSeq || ""))) {
          this.selectedLiveRunSnapshotTargetSeq = keys[keys.length - 1];
        }
        if (this.selectedLiveRunSnapshotBaseSeq === this.selectedLiveRunSnapshotTargetSeq) {
          const targetIndex = keys.indexOf(this.selectedLiveRunSnapshotTargetSeq);
          this.selectedLiveRunSnapshotBaseSeq = keys[Math.max(0, targetIndex - 1)];
          if (this.selectedLiveRunSnapshotBaseSeq === this.selectedLiveRunSnapshotTargetSeq) {
            this.selectedLiveRunSnapshotTargetSeq = keys[Math.min(keys.length - 1, targetIndex + 1)];
          }
        }
      },


      liveRunReplayStableJson(value) {
        if (Array.isArray(value)) {
          return `[${value.map((item) => this.liveRunReplayStableJson(item)).join(",")}]`;
        }
        if (value && typeof value === "object") {
          return `{${Object.keys(value)
            .sort()
            .map((key) => `${JSON.stringify(key)}:${this.liveRunReplayStableJson(value[key])}`)
            .join(",")}}`;
        }
        return JSON.stringify(value);
      },


      liveRunReplaySnapshotCompare() {
        const snapshots = this.liveRunReplaySnapshots();
        if (snapshots.length < 2) {
          return null;
        }
        let base = this.liveRunReplaySnapshotByKey(this.selectedLiveRunSnapshotBaseSeq) || snapshots[Math.max(0, snapshots.length - 2)];
        let target = this.liveRunReplaySnapshotByKey(this.selectedLiveRunSnapshotTargetSeq) || snapshots[snapshots.length - 1];
        if (this.liveRunReplaySnapshotKey(base) === this.liveRunReplaySnapshotKey(target)) {
          const targetIndex = snapshots.findIndex(
            (snapshot) => this.liveRunReplaySnapshotKey(snapshot) === this.liveRunReplaySnapshotKey(target)
          );
          base = snapshots[Math.max(0, targetIndex - 1)];
          if (this.liveRunReplaySnapshotKey(base) === this.liveRunReplaySnapshotKey(target)) {
            target = snapshots[Math.min(snapshots.length - 1, targetIndex + 1)];
          }
        }
        const baseEnabled = new Set(Array.isArray(base?.enabled_set) ? base.enabled_set : []);
        const targetEnabled = new Set(Array.isArray(target?.enabled_set) ? target.enabled_set : []);
        const baseSummary = base?.selection_summary && typeof base.selection_summary === "object" && !Array.isArray(base.selection_summary)
          ? base.selection_summary
          : {};
        const targetSummary = target?.selection_summary && typeof target.selection_summary === "object" && !Array.isArray(target.selection_summary)
          ? target.selection_summary
          : {};
        const baseKeys = new Set(Object.keys(baseSummary));
        const targetKeys = new Set(Object.keys(targetSummary));
        const sharedKeys = [...baseKeys].filter((key) => targetKeys.has(key));
        return {
          base,
          target,
          enabled_added: [...targetEnabled].filter((item) => !baseEnabled.has(item)).sort(),
          enabled_removed: [...baseEnabled].filter((item) => !targetEnabled.has(item)).sort(),
          summary_added_keys: [...targetKeys].filter((key) => !baseKeys.has(key)).sort(),
          summary_removed_keys: [...baseKeys].filter((key) => !targetKeys.has(key)).sort(),
          summary_changed_keys: sharedKeys
            .filter((key) => this.liveRunReplayStableJson(baseSummary[key]) !== this.liveRunReplayStableJson(targetSummary[key]))
            .sort(),
          token_payload_changed:
            this.liveRunReplayStableJson(base?.token_payload || {}) !== this.liveRunReplayStableJson(target?.token_payload || {}),
          snapshot_hash_changed: String(base?.snapshot_hash || "") !== String(target?.snapshot_hash || "")
        };
      },


      liveRunReplaySnapshotCompareSummary() {
        const diff = this.liveRunReplaySnapshotCompare();
        if (!diff) {
          return "需要至少两个 Session Token 快照。";
        }
        const changedKeys =
          diff.summary_added_keys.length + diff.summary_removed_keys.length + diff.summary_changed_keys.length;
        return [
          `${this.liveRunReplaySnapshotLabel(diff.base)} -> ${this.liveRunReplaySnapshotLabel(diff.target)}`,
          `enabled +${diff.enabled_added.length}/-${diff.enabled_removed.length}`,
          `${changedKeys} summary keys changed`,
          diff.token_payload_changed ? "token payload changed" : "token payload unchanged"
        ].join(" · ");
      },


      liveRunReplaySnapshotDiffStats() {
        const diff = this.liveRunReplaySnapshotCompare();
        if (!diff) {
          return [];
        }
        const changedKeys =
          diff.summary_added_keys.length + diff.summary_removed_keys.length + diff.summary_changed_keys.length;
        return [
          { label: "Enabled Added", value: diff.enabled_added.length },
          { label: "Enabled Removed", value: diff.enabled_removed.length },
          { label: "Summary Keys", value: changedKeys },
          { label: "Token Payload", value: diff.token_payload_changed ? "changed" : "same" }
        ];
      },


      liveRunReplayRunEventCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.run_events || []).length
          : 0;
      },


      liveRunReplayTraceCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.run_traces || []).length
          : 0;
      },


      liveRunReplayEgNodePath() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.eg_node_path || [])
          : [];
      },


      liveRunReplayEgNodePathCount() {
        return this.liveRunReplayEgNodePath().length;
      },


      liveRunReplayEgNodePathItemKey(item) {
        return [item?.seq_no ?? "", item?.trace_id || "", item?.node_id || ""].join(":");
      },


      liveRunReplayEgNodePathSummary(item) {
        return [
          item?.event_type || "event:N/A",
          item?.node_kind || "kind:N/A",
          item?.checkpoint_id ? `checkpoint ${item.checkpoint_id}` : ""
        ].filter(Boolean).join(" · ");
      },


      liveRunReplayEgNodePathItemClass(item) {
        return this.liveRunReplayFindEvidenceItem({ kind: "run_trace", id: item?.trace_id })
          ? "border-slate-800 hover:bg-slate-900/60"
          : "border-slate-800 opacity-60";
      },


      selectLiveRunReplayEgNodePathItem(item) {
        return this.selectLiveRunReplayEvidenceRef({
          kind: "run_trace",
          id: item?.trace_id,
          event_type: item?.event_type
        });
      },


      liveRunReplayAgentRunCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.agent_runs || []).length
          : 0;
      },

      liveRunReplayAgentEventCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.agent_events || []).length
          : 0;
      },


      liveRunReplayModelCallCount() {
        if (this.replayDetail?.run?.id !== this.liveRun?.id) {
          return 0;
        }
        return (this.replayDetail.agent_model_calls || this.replayDetail.model_calls || []).length;
      },


      liveRunReplayToolCallCount() {
        if (this.replayDetail?.run?.id !== this.liveRun?.id) {
          return 0;
        }
        return (this.replayDetail.agent_tool_calls || this.replayDetail.tool_calls || []).length;
      },


      liveRunReplayToolAuthorizationCount() {
        if (this.replayDetail?.run?.id !== this.liveRun?.id) {
          return 0;
        }
        return (this.replayDetail.agent_tool_authorizations || this.replayDetail.tool_authorizations || []).length;
      },


      liveRunReplayEvaluationCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.run_evaluations || []).length
          : 0;
      },


      liveRunReplayFindingCount() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.run_evaluation_findings || []).length
          : 0;
      },


      liveRunReplayAgentRuns() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.agent_runs || [])
          : [];
      },

      liveRunReplayAgentEvents() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.agent_events || [])
          : [];
      },


      liveRunReplayModelCalls() {
        if (this.replayDetail?.run?.id !== this.liveRun?.id) {
          return [];
        }
        return this.replayDetail.agent_model_calls || this.replayDetail.model_calls || [];
      },


      liveRunReplayToolCalls() {
        if (this.replayDetail?.run?.id !== this.liveRun?.id) {
          return [];
        }
        return this.replayDetail.agent_tool_calls || this.replayDetail.tool_calls || [];
      },


      liveRunReplayToolAuthorizations() {
        if (this.replayDetail?.run?.id !== this.liveRun?.id) {
          return [];
        }
        return this.replayDetail.agent_tool_authorizations || this.replayDetail.tool_authorizations || [];
      },

      replaceLiveRunReplayToolAuthorization(authorization) {
        if (!authorization?.id || this.replayDetail?.run?.id !== this.liveRun?.id) {
          return;
        }
        const listKeys = ["agent_tool_authorizations", "tool_authorizations"];
        let mergedAny = false;
        for (const key of listKeys) {
          if (!Array.isArray(this.replayDetail[key])) {
            continue;
          }
          const index = this.replayDetail[key].findIndex((item) => item.id === authorization.id);
          if (index >= 0) {
            this.replayDetail[key].splice(index, 1, authorization);
          } else {
            this.replayDetail[key].unshift(authorization);
          }
          mergedAny = true;
        }
        if (!mergedAny) {
          this.replayDetail.agent_tool_authorizations = [authorization];
        }
      },


      liveRunReplayEvaluations() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.run_evaluations || [])
          : [];
      },


      liveRunReplayFindings() {
        return this.replayDetail?.run?.id === this.liveRun?.id
          ? (this.replayDetail.run_evaluation_findings || [])
          : [];
      },


      liveRunReplayAgentRunSummary(agentRun) {
        return [
          agentRun?.agent_key || "agent",
          agentRun?.status || "unknown",
          agentRun?.owner_type || "owner:N/A"
        ].join(" · ");
      },

      liveRunReplayAgentEventSummary(event) {
        return [
          event?.phase || "phase:N/A",
          event?.agent_run_id || "agent_run:N/A",
          event?.seq_no !== undefined && event?.seq_no !== null ? `#${event.seq_no}` : ""
        ].filter(Boolean).join(" · ");
      },


      liveRunReplayModelCallSummary(call) {
        const usage = call?.usage_json || {};
        const tokens = Number(usage.total_tokens || 0);
        return [
          call?.route_key || call?.provider || "model",
          call?.status || "unknown",
          Number.isFinite(tokens) && tokens > 0 ? `${tokens} tokens` : "tokens:N/A"
        ].join(" · ");
      },


      liveRunReplayToolCallSummary(call) {
        return [
          call?.tool_name || "tool",
          call?.status || "unknown",
          call?.side_effect_level || "effect:N/A"
        ].join(" · ");
      },


      liveRunReplayEvaluationSummary(evaluation) {
        const score = Number(evaluation?.quality_score ?? 0);
        return [
          evaluation?.overall_outcome || "outcome:N/A",
          `score ${Number.isFinite(score) ? score : 0}`,
          `${(evaluation?.findings || []).length} findings`
        ].join(" · ");
      },


      liveRunReplayFindingSummary(finding) {
        return [
          finding?.category || "category:N/A",
          finding?.severity || "severity:N/A",
          finding?.status || "status:N/A"
        ].join(" · ");
      },


      liveRunReplayEvidenceRefs(finding) {
        return Array.isArray(finding?.evidence_refs) ? finding.evidence_refs : [];
      },


      liveRunReplayEvidenceRefLabel(ref) {
        if (!ref || typeof ref !== "object") {
          return "evidence:N/A";
        }
        const kind = this.liveRunReplayNormalizeEvidenceKind(ref.kind || ref.source_kind) || "evidence";
        const descriptor =
          ref.event_type ||
          ref.event_kind ||
          ref.id ||
          ref.seq_no ||
          ref.agent_run_id ||
          "N/A";
        return `${kind}:${descriptor}`;
      },


      liveRunReplayEvidenceRefClass(ref) {
        return this.liveRunReplayFindEvidenceItem(ref)
          ? "border-sky-500/30 bg-sky-500/10 text-sky-200 hover:bg-sky-500/15"
          : "cursor-not-allowed border-slate-800 bg-slate-900/40 text-slate-500";
      },


      liveRunReplayNormalizeEvidenceKind(kind) {
        const value = String(kind || "").trim().toLowerCase();
        if (value === "trace_event") {
          return "run_trace";
        }
        if (value === "terminal_event") {
          return "run_event";
        }
        return value;
      },


      liveRunReplayEvidenceItemKind(item) {
        const sourceKind = this.liveRunReplayNormalizeEvidenceKind(item?.source_kind);
        if (sourceKind) {
          return sourceKind;
        }
        const eventType = String(item?.event_type || "").toLowerCase();
        if (eventType.includes("terminal")) {
          return "run_event";
        }
        return "run_trace";
      },


      liveRunReplayEvidenceCandidateIds(item) {
        const payload = item?.payload && typeof item.payload === "object" ? item.payload : {};
        return [
          item?.source_id,
          payload.id,
          payload.event_id,
          payload.run_event_id,
          payload.run_trace_id,
          payload.trace_event_id,
          payload.trace_id
        ]
          .map((value) => String(value || "").trim())
          .filter(Boolean);
      },


      liveRunReplayFindEvidenceItem(ref) {
        if (!ref || typeof ref !== "object") {
          return null;
        }
        const refKind = this.liveRunReplayNormalizeEvidenceKind(ref.kind || ref.source_kind);
        const refId = String(ref.id || ref.source_id || ref.run_trace_id || ref.run_event_id || ref.event_id || "").trim();
        const refSeqNo = String(ref.seq_no ?? "").trim();
        const refEventType = String(ref.event_type || "").trim();
        const refEventKind = String(ref.event_kind || "").trim();
        const isKindCompatible = (item) => {
          const itemKind = this.liveRunReplayEvidenceItemKind(item);
          return !refKind || !itemKind || itemKind === refKind;
        };
        const timeline = this.liveRunReplayTimeline();
        if (refId) {
          const byId = timeline.find(
            (item) => isKindCompatible(item) && this.liveRunReplayEvidenceCandidateIds(item).includes(refId)
          );
          if (byId) {
            return byId;
          }
        }
        if (refSeqNo) {
          const bySeq = timeline.find(
            (item) => isKindCompatible(item) && String(item?.seq_no ?? "") === refSeqNo
          );
          if (bySeq) {
            return bySeq;
          }
        }
        if (refEventType) {
          const byEventType = timeline.find(
            (item) => isKindCompatible(item) && String(item?.event_type || "") === refEventType
          );
          if (byEventType) {
            return byEventType;
          }
        }
        if (refEventKind) {
          return (
            timeline.find((item) => {
              const payload = item?.payload && typeof item.payload === "object" ? item.payload : {};
              return isKindCompatible(item) && String(payload.event_kind || "") === refEventKind;
            }) || null
          );
        }
        return null;
      },


      selectLiveRunReplayEvidenceRef(ref) {
        const item = this.liveRunReplayFindEvidenceItem(ref);
        if (item) {
          this.selectLiveRunReplayItem(item);
          return item;
        }
        if (typeof this.showNotice === "function") {
          this.showNotice("error", "未找到对应 Replay 证据。");
        }
        return null;
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


      liveRunProcessRunEvents() {
        return (this.liveRunEvents || [])
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
        const eventTimes = this.liveRunProcessRunEvents()
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
        const eventOffsets = this.liveRunProcessRunEvents().map((event) => this.liveRunProcessEventAtMs(event));
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
        const kind = String(part?.kind || "").toLowerCase();
        const mimeType = this.runEventPartMimeType(part);
        if (kind === "image" || mimeType.startsWith("image/")) {
          return "image";
        }
        if (kind === "audio" || mimeType.startsWith("audio/")) {
          return "audio";
        }
        if (kind === "video" || mimeType.startsWith("video/")) {
          return "video";
        }
        if (kind === "text" || mimeType.startsWith("text/")) {
          return "text";
        }
        if (mimeType === "application/json" || mimeType.endsWith("+json")) {
          return "data";
        }
        return "file";
      },


      liveRunProcessEventKind(event) {
        const parts = this.runEventParts(event);
        if (parts.length) {
          const kinds = Array.from(new Set(parts.map((part) => this.liveRunProcessPartKind(part))));
          return kinds.length === 1 ? kinds[0] : "mixed";
        }
        if (this.runEventMimeType(event).startsWith("multipart/")) {
          return "mixed";
        }
        if (this.runEventIsImage(event)) {
          return "image";
        }
        if (this.runEventIsAudio(event)) {
          return "audio";
        }
        if (this.runEventIsVideo(event)) {
          return "video";
        }
        if (this.runEventIsPdf(event) || this.runEventIsGenericFile(event)) {
          return "file";
        }
        if (this.runEventShouldShowJson(event)) {
          return "data";
        }
        if (this.runEventDisplayText(event)) {
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
        this.liveRunProcessRunEvents().forEach((event) => {
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
        return this.liveRunProcessRunEvents()
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
        const direction = this.runEventDirectionLabel?.(event?.direction) || (event?.direction === "output" ? "输出" : "输入");
        const seq = event?.seq_no === undefined || event?.seq_no === null ? "" : ` #${event.seq_no}`;
        return `${direction}${seq}`;
      },


      liveRunProcessEventSummary(event) {
        if (!event) {
          return "";
        }
        const parts = this.runEventParts(event);
        if (parts.length) {
          const labels = parts.map((part) => {
            if (this.runEventPartIsText(part)) {
              return this.runEventPartDisplayText(part);
            }
            return this.runEventPartFileName(part);
          }).filter(Boolean);
          return labels.join(" + ") || event.event_kind || "multipart";
        }
        if (this.runEventDisplayText(event)) {
          return this.runEventDisplayText(event);
        }
        if (this.runEventIsImage(event) || this.runEventIsAudio(event) || this.runEventIsVideo(event) || this.runEventIsPdf(event) || this.runEventIsGenericFile(event)) {
          return this.runEventFileName(event);
        }
        if (this.runEventShouldShowJson(event)) {
          return this.runEventJsonText(event);
        }
        return event.event_kind || "RunEvent";
      },


      liveRunProcessEventDetailText(event) {
        if (!event) {
          return "";
        }
        const parts = this.runEventParts(event);
        if (parts.length) {
          return parts
            .map((part, index) => {
              const label = this.runEventPartIsText(part)
                ? this.runEventPartDisplayText(part)
                : this.runEventPartFileName(part);
              return `part ${index + 1}: ${part.kind || "file"} ${part.mime_type || ""}\n${label || part.part_id || ""}`.trim();
            })
            .join("\n\n");
        }
        if (this.runEventDisplayText(event)) {
          return this.runEventDisplayText(event);
        }
        if (this.runEventShouldShowJson(event)) {
          return this.runEventJsonText(event);
        }
        if (this.runEventIsImage(event) || this.runEventIsAudio(event) || this.runEventIsVideo(event) || this.runEventIsPdf(event) || this.runEventIsGenericFile(event)) {
          return [this.runEventFileName(event), this.runEventFileMeta(event)].filter(Boolean).join("\n");
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
          ["方向", this.runEventDirectionLabel?.(event.direction) || event.direction],
          ["内容", this.liveRunProcessLaneLabel({ kind: this.liveRunProcessEventKind(event) })],
          ["RunEvent 序号", event.seq_no === undefined || event.seq_no === null ? "" : `#${event.seq_no}`],
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
        return this.liveRunProcessRunEvents().find((event) => this.liveRunProcessEventKey(event) === this.selectedLiveRunProcessEventKey) || null;
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
        const events = this.liveRunProcessRunEvents();
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
        return this.invocations.filter((invocation) => invocation.pskill_definition_id === this.currentSkill.id);
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
