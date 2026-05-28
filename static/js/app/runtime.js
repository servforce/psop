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
          const [run, bindings, terminalSession, terminalEvents, traceEvents] = await Promise.all([
            this.apiRequest(`/runs/${runId}`),
            this.apiRequest(`/runs/${runId}/bindings`),
            this.apiRequest(`/terminal/sessions/${runId}`),
            this.apiRequest(`/terminal/sessions/${runId}/events`),
            this.apiRequest(`/runs/${runId}/trace-events`)
          ]);
          this.liveRun = run;
          this.liveRunBindings = bindings;
          this.liveRunTerminalSession = terminalSession.terminal_session;
          this.liveRunTerminalEvents = window.PSOPRuntimeEvents.mergeBySeq([], terminalEvents);
          this.updateLiveRunLatestTerminalSeq();
          this.scrollTerminalTranscriptToBottom();
          this.liveRunTraceEvents = window.PSOPRuntimeEvents.mergeBySeq([], traceEvents);
          this.connectRunWebSocket(runId);
        } finally {
          this.busy.liveRun = false;
        }
      },


      async sendTerminalInput() {
        const runId = this.liveRun?.id;
        const textPayload = this.terminalInputText();
        const filePayload = this.terminalInputForm.file;
        if (!runId || !this.canSendTerminalInput()) {
          return;
        }
        this.busy.terminalInput = true;
        const optimisticEvent = this.buildOptimisticTerminalInputEvent(runId, textPayload, filePayload);
        let acceptedByServer = false;
        this.mergeTerminalEvents([optimisticEvent]);
        this.terminalInputForm.payload = "";
        this.clearTerminalInputFile();
        try {
          if (filePayload) {
            const result = await this.uploadTerminalRuntimeFile(
              runId,
              filePayload,
              textPayload,
              optimisticEvent.external_event_id
            );
            acceptedByServer = true;
            this.mergeTerminalEvents([result.event]);
          } else {
            const response = await this.apiRequest(`/terminal/sessions/${runId}/events`, {
              method: "POST",
              body: JSON.stringify({
                direction: "input",
                event_kind: "terminal.text.input.v1",
                mime_type: "text/plain",
                payload_inline: textPayload,
                source: {
                  kind: "web"
                },
                external_event_id: optimisticEvent.external_event_id
              })
            });
            acceptedByServer = true;
            this.mergeTerminalEvents([response.event]);
          }
          await this.loadRunLive(runId);
        } catch (error) {
          if (!acceptedByServer) {
            acceptedByServer = await this.reconcileTerminalInputAcceptance(runId, optimisticEvent.external_event_id);
          }
          if (!acceptedByServer) {
            this.removeOptimisticTerminalEvent(optimisticEvent.id);
            this.terminalInputForm.payload = textPayload;
            this.terminalInputForm.file = filePayload;
          } else {
            try {
              await this.loadRunLive(runId);
            } catch {
              // The accepted terminal event remains the source of truth; a later refresh can recover run state.
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
        this.terminalInputForm.file = event.target.files?.[0] || null;
      },


      clearTerminalInputFile() {
        this.terminalInputForm.file = null;
        if (this.$refs?.terminalInputFile) {
          this.$refs.terminalInputFile.value = "";
        }
      },


      async uploadTerminalRuntimeFile(runId, file, caption = "", externalEventId = "") {
        if (!runId || !file) {
          throw new Error("当前运行不可上传多模态数据。");
        }

        const formData = new FormData();
        formData.append("file", file);
        if (caption) {
          formData.append("caption", caption);
        }
        return this.apiRequest(`/terminal/sessions/${runId}/files`, {
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


      handleRunWsEvent(event) {
        if (!event || event.event_type === "ws.connected") {
          return;
        }
        if (event.event_type === "terminal.event.appended" && event.payload) {
          this.mergeTerminalEvents([event.payload]);
        }
        if (event.event_type === "trace.event.appended" && event.payload) {
          this.liveRunTraceEvents = window.PSOPRuntimeEvents.mergeBySeq(this.liveRunTraceEvents, [event.payload]);
        }
        if (["binding.resolved", "binding.updated"].includes(event.event_type) && event.payload?.bindings) {
          this.liveRunBindings = window.PSOPRuntimeEvents.mergeById(this.liveRunBindings, event.payload.bindings);
        }
      },


      mergeTerminalEvents(events) {
        const incoming = events || [];
        const realIncomingSeqs = new Set(
          incoming
            .filter((event) => event && !event._optimistic && Number.isFinite(Number(event.seq_no)))
            .map((event) => Number(event.seq_no))
        );
        const baseEvents = realIncomingSeqs.size
          ? this.liveRunTerminalEvents.filter((event) => !realIncomingSeqs.has(Number(event.seq_no)))
          : this.liveRunTerminalEvents;
        this.liveRunTerminalEvents = window.PSOPRuntimeEvents.mergeBySeq(baseEvents, incoming);
        this.updateLiveRunLatestTerminalSeq();
        this.scrollTerminalTranscriptToBottom();
      },


      updateLiveRunLatestTerminalSeq() {
        if (!this.liveRun) {
          return;
        }
        const eventSeqs = this.liveRunTerminalEvents.map((event) => Number(event.seq_no) || 0);
        const latestSeq = eventSeqs.length ? Math.max(...eventSeqs) : Number(this.liveRun.latest_terminal_seq || 0);
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


      buildOptimisticTerminalInputEvent(runId, textPayload, filePayload = null) {
        const now = new Date().toISOString();
        const id = `local-terminal-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        const mimeType = filePayload ? filePayload.type || "application/octet-stream" : "text/plain";
        return {
          id,
          terminal_session_id: this.liveRun?.terminal_session_id || this.liveRunTerminalSession?.id || "",
          run_id: runId,
          direction: "input",
          event_kind: filePayload ? this.terminalInputEventKindForMime(mimeType) : "terminal.text.input.v1",
          mime_type: mimeType,
          payload_inline: filePayload
            ? {
                filename: filePayload.name,
                name: filePayload.name,
                description: textPayload || "",
                caption: textPayload || "",
                size_bytes: filePayload.size || 0,
                status: "sent"
              }
            : textPayload,
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


      removeOptimisticTerminalEvent(eventId) {
        this.liveRunTerminalEvents = this.liveRunTerminalEvents.filter((event) => event.id !== eventId);
        this.updateLiveRunLatestTerminalSeq();
      },


      async reconcileTerminalInputAcceptance(runId, externalEventId) {
        if (!runId || !externalEventId) {
          return false;
        }
        try {
          const events = await this.apiRequest(`/terminal/sessions/${runId}/events`);
          const acceptedEvent = events.find((event) => event.external_event_id === externalEventId);
          if (!acceptedEvent) {
            return false;
          }
          this.mergeTerminalEvents([acceptedEvent]);
          return true;
        } catch {
          return false;
        }
      },


      terminalRunEnded() {
        return ["succeeded", "failed", "cancelled", "canceled"].includes(String(this.liveRun?.status || "").toLowerCase());
      },


      terminalSessionClosed() {
        const status = String(this.liveRunTerminalSession?.status || "").toLowerCase();
        return Boolean(status && status !== "open");
      },


      terminalInputText() {
        return String(this.terminalInputForm.payload || "").trim();
      },


      terminalInputHasContent() {
        return Boolean(this.terminalInputText() || this.terminalInputForm.file);
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


      terminalEventDisplayText(event) {
        const payload = event?.payload_inline;
        if (typeof payload === "string") {
          return payload;
        }
        if (payload === null || payload === undefined) {
          return "";
        }
        return this.terminalEventPayloadTextValue(event, [
          "caption",
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
        return this.terminalEventPresentationMimeType(event).startsWith("image/");
      },


      terminalEventIsAudio(event) {
        return this.terminalEventPresentationMimeType(event).startsWith("audio/");
      },


      terminalEventIsVideo(event) {
        return this.terminalEventPresentationMimeType(event).startsWith("video/");
      },


      terminalEventIsJson(event) {
        const mimeType = this.terminalEventPresentationMimeType(event);
        return mimeType === "application/json" || mimeType.endsWith("+json");
      },


      terminalEventIsPdf(event) {
        return this.terminalEventPresentationMimeType(event) === "application/pdf";
      },


      terminalEventIsGenericFile(event) {
        const mimeType = this.terminalEventPresentationMimeType(event);
        const eventKind = String(event?.event_kind || "").toLowerCase();
        return Boolean(
          this.terminalEventMediaUrl(event) &&
            !this.terminalEventIsImage(event) &&
            !this.terminalEventIsAudio(event) &&
            !this.terminalEventIsVideo(event) &&
            !this.terminalEventIsPdf(event) &&
            !this.terminalEventIsJson(event) &&
            (event?.artifact_object_id || eventKind.includes(".file.") || ["application/pdf", "application/octet-stream"].includes(mimeType))
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
        return String(event?.direction || "").toLowerCase() === "output" ? "Runtime" : "用户";
      },


      terminalEventRowClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "justify-end" : "justify-start";
      },


      terminalEventMessageShellClass(event) {
        return String(event?.direction || "").toLowerCase() === "input"
          ? "w-fit max-w-3xl"
          : "w-full max-w-3xl";
      },


      terminalEventMetaClass(event) {
        return String(event?.direction || "").toLowerCase() === "input" ? "justify-end text-right" : "justify-start";
      },


      terminalEventBubbleClass(event) {
        return "bg-[#262626]";
      },


      openTerminalMediaPreview(event) {
        const src = this.terminalEventMediaUrl(event);
        if (!src || !this.terminalEventIsImage(event)) {
          return;
        }
        this.terminalMediaPreview = {
          open: true,
          kind: "image",
          src,
          title: this.terminalEventFileName(event),
          caption: this.terminalEventDisplayText(event)
        };
      },


      closeTerminalMediaPreview() {
        this.terminalMediaPreview = {
          open: false,
          kind: "",
          src: "",
          title: "",
          caption: ""
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
