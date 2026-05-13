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

  window.PSOPConsoleSkillTestMethods = {

      resetSkillTestCaseForm(caseDetail = null) {
        const timeline = caseDetail?.timeline || this.defaultSkillTestTimeline();
        const judgePolicy = caseDetail?.judge_policy || this.defaultSkillTestJudgePolicy();
        this.skillTestCaseForm = {
          name: caseDetail?.name || "",
          description: caseDetail?.description || "",
          target_version_selector: caseDetail?.target_version_selector || "latest",
          target_compile_artifact_id: caseDetail?.target_compile_artifact_id || "",
          duration_ms: caseDetail?.duration_ms || timeline.duration_ms || 1800000,
          timeline_json: JSON.stringify(timeline, null, 2),
          judge_policy_json: JSON.stringify(judgePolicy, null, 2),
          event_lane_id: "input.text",
          event_at_ms: 0,
          event_payload_inline: "",
          event_asset_id: "",
          expectation_at_ms: 0,
          expectation_text: ""
        };
        this.selectedSkillTestTimelineEventId = "";
        this.selectedSkillTestTimelineEventIds = [];
        this.skillTestTimelineEventDraft = null;
      },


      skillTestInitialEventPreview(caseDetail = this.skillTestCase) {
        const event = (caseDetail?.timeline?.events || []).find((item) => item?.payload_inline || item?.expectation);
        return event?.payload_inline || event?.expectation || "尚未配置时间轴事件";
      },


      defaultSkillTestTimeline() {
        return {
          schema_version: "psop-skill-test-timeline/v1",
          duration_ms: 1800000,
          lanes: [
            { id: "input.text", kind: "input", label: "文本" },
            { id: "input.image", kind: "input", label: "图片" },
            { id: "input.audio", kind: "input", label: "音频" },
            { id: "input.video", kind: "input", label: "视频" },
            { id: "expected.semantic", kind: "output", label: "语义" }
          ],
          events: []
        };
      },


      defaultSkillTestJudgePolicy() {
        return {
          route_key: "skill-test-judge",
          confidence_threshold: 0.5,
          inconclusive_counts_as_failure: true
        };
      },


      parseSkillTestJsonField(fieldName, fallbackValue, errorMessage) {
        const raw = this.skillTestCaseForm[fieldName] || "";
        if (!raw.trim()) {
          return fallbackValue;
        }
        try {
          return JSON.parse(raw);
        } catch {
          throw new Error(errorMessage);
        }
      },


      parseSkillTestTimeline() {
        this.flushSkillTestTimelineEventDraft();
        const timeline = this.normalizeSkillTestTimelineDraft(
          this.parseSkillTestJsonField("timeline_json", this.defaultSkillTestTimeline(), "时间轴 JSON 必须是对象。")
        );
        this.validateSkillTestTimelineForSave(timeline);
        this.skillTestCaseForm.duration_ms = timeline.duration_ms;
        this.skillTestCaseForm.timeline_json = JSON.stringify(timeline, null, 2);
        return timeline;
      },

      validateSkillTestTimelineForSave(timeline) {
        const missingExpectation = (timeline?.events || []).find(
          (event) => this.isSkillTestTimelineExpectationEvent(event) && !String(event.expectation || "").trim()
        );
        if (missingExpectation) {
          throw new Error("请填写语义输出事件的期望内容。");
        }
      },


      parseSkillTestJudgePolicy() {
        return this.parseSkillTestJsonField("judge_policy_json", this.defaultSkillTestJudgePolicy(), "Judge 策略 JSON 必须是对象。");
      },


      isSkillTestExpectationLane(laneId) {
        return laneId === "expected.semantic";
      },

      isSkillTestTimelineExpectationEvent(event) {
        return this.isSkillTestExpectationLane(event?.lane_id) || Object.prototype.hasOwnProperty.call(event || {}, "expectation");
      },


      skillTestEventDefaultsForLane(laneId) {
        if (this.isSkillTestExpectationLane(laneId)) {
          return {};
        }
        if (laneId === "input.image") {
          return { event_kind: "terminal.image.input.v1", mime_type: "image/*" };
        }
        if (laneId === "input.audio") {
          return { event_kind: "terminal.audio.input.v1", mime_type: "audio/*" };
        }
        if (laneId === "input.video") {
          return { event_kind: "terminal.video.input.v1", mime_type: "video/*" };
        }
        return { event_kind: "terminal.text.input.v1", mime_type: "text/plain" };
      },

      skillTestTimelineEventIdPrefix(laneId) {
        return this.isSkillTestExpectationLane(laneId) ? "expected" : "input";
      },

      skillTestTimelineEventIdCounts(events) {
        return (events || []).reduce((counts, event) => {
          const eventId = typeof event?.id === "string" ? event.id.trim() : "";
          if (eventId) {
            counts.set(eventId, (counts.get(eventId) || 0) + 1);
          }
          return counts;
        }, new Map());
      },

      nextSkillTestTimelineEventId(events, prefix, blockedIds = null) {
        const usedIds = new Set(
          (events || [])
            .map((event) => (typeof event?.id === "string" ? event.id.trim() : ""))
            .filter(Boolean)
        );
        const reservedIds = blockedIds || usedIds;
        const prefixPattern = new RegExp(`^${prefix}_(\\d+)$`);
        let nextNumber = 1;
        for (const eventId of usedIds) {
          const match = eventId.match(prefixPattern);
          if (match) {
            nextNumber = Math.max(nextNumber, Number(match[1]) + 1);
          }
        }
        let candidate = `${prefix}_${nextNumber}`;
        while (usedIds.has(candidate) || reservedIds.has(candidate)) {
          nextNumber += 1;
          candidate = `${prefix}_${nextNumber}`;
        }
        return candidate;
      },


      normalizeSkillTestTimelineDraft(timeline) {
        if (!timeline || typeof timeline !== "object" || Array.isArray(timeline)) {
          return this.defaultSkillTestTimeline();
        }
        const defaults = this.defaultSkillTestTimeline();
        const draft = JSON.parse(JSON.stringify(timeline));
        const durationMs = Math.max(1, Number(draft.duration_ms || this.skillTestCaseForm.duration_ms || defaults.duration_ms));
        const lanes = Array.isArray(draft.lanes) && draft.lanes.length ? draft.lanes : defaults.lanes;
        const events = Array.isArray(draft.events) ? draft.events : [];
        const originalIdCounts = this.skillTestTimelineEventIdCounts(events);
        const reservedIds = new Set(originalIdCounts.keys());
        const normalizedEvents = [];
        return {
          ...draft,
          schema_version: draft.schema_version || "psop-skill-test-timeline/v1",
          duration_ms: durationMs,
          lanes,
          events: events
            .filter((event) => event && typeof event === "object" && !Array.isArray(event))
            .map((event, index) => {
              const isExpectation = this.isSkillTestTimelineExpectationEvent(event);
              const laneId = isExpectation ? "expected.semantic" : event.lane_id || "input.text";
              const defaultsForLane = this.skillTestEventDefaultsForLane(laneId);
              const eventId = typeof event.id === "string" ? event.id.trim() : "";
              const canReuseEventId = eventId && !normalizedEvents.some((item) => item.id === eventId);
              const normalizedId = canReuseEventId
                ? eventId
                : this.nextSkillTestTimelineEventId(
                    normalizedEvents,
                    this.skillTestTimelineEventIdPrefix(laneId),
                    reservedIds
                  );
              const normalized = {
                ...defaultsForLane,
                ...event,
                id: normalizedId,
                lane_id: laneId,
                at_ms: Math.min(durationMs, Math.max(0, Number(event.at_ms || 0))),
                required: event.required !== false
              };
              if (isExpectation) {
                const expectationText = typeof event.expectation === "string" ? event.expectation : "";
                const fallbackExpectation = typeof event.payload_inline === "string" ? event.payload_inline : "";
                normalized.expectation =
                  expectationText.trim()
                    ? expectationText
                    : fallbackExpectation;
                delete normalized.payload_inline;
                delete normalized.asset_id;
                delete normalized.event_kind;
                delete normalized.mime_type;
              }
              normalizedEvents.push(normalized);
              return normalized;
            })
            .sort((left, right) => Number(left.at_ms || 0) - Number(right.at_ms || 0))
        };
      },


      skillTestTimelineDraft() {
        try {
          return this.normalizeSkillTestTimelineDraft(JSON.parse(this.skillTestCaseForm.timeline_json || "{}"));
        } catch {
          return this.defaultSkillTestTimeline();
        }
      },


      writeSkillTestTimelineDraft(timeline) {
        const normalized = this.normalizeSkillTestTimelineDraft(timeline);
        this.skillTestCaseForm.duration_ms = normalized.duration_ms;
        this.skillTestCaseForm.timeline_json = JSON.stringify(normalized, null, 2);
      },


      skillTestTimelineEvents() {
        return this.skillTestTimelineDraft().events || [];
      },


      skillTestTimelineLanes() {
        const timeline = this.skillTestTimelineDraft();
        const defaultLaneIds = this.defaultSkillTestTimeline().lanes.map((lane) => lane.id);
        return (timeline.lanes || [])
          .slice()
          .sort((left, right) => {
            const leftIndex = defaultLaneIds.indexOf(left.id);
            const rightIndex = defaultLaneIds.indexOf(right.id);
            return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
          });
      },


      skillTestTimelineEventsForLane(laneId) {
        return this.skillTestTimelineEvents()
          .map((event, index) => ({ event, index }))
          .filter((item) => item.event.lane_id === laneId)
          .map((item) => ({
            ...item,
            render_key: `${item.event.lane_id}:${item.event.id}:${item.index}`
          }));
      },


      skillTestTimelineDurationMs() {
        return Math.max(1, Number(this.skillTestTimelineDraft().duration_ms || this.skillTestCaseForm.duration_ms || 1800000));
      },


      skillTestTimelineDurationValue() {
        const minutes = Math.round(this.skillTestTimelineDurationMs() / 1000) / 60;
        return Number((Number.isInteger(minutes) ? minutes : minutes.toFixed(2)));
      },


      skillTestTimelineDurationStep() {
        return "0.1";
      },


      skillTestTimelineDurationMin() {
        return "0.1";
      },


      updateSkillTestTimelineDurationValue(value) {
        const numberValue = Math.max(Number(this.skillTestTimelineDurationMin()), Number(value || this.skillTestTimelineDurationMin()));
        const seconds = Math.max(1, Math.round(numberValue * 60));
        this.writeSkillTestTimelineDraft({
          ...this.skillTestTimelineDraft(),
          duration_ms: seconds * 1000
        });
      },


      skillTestTimelineTicks() {
        const duration = this.skillTestTimelineDurationMs();
        const tickCount = 6;
        return Array.from({ length: tickCount + 1 }, (_, index) => {
          const percent = (index / tickCount) * 100;
          return {
            ms: Math.round((duration * index) / tickCount),
            percent
          };
        });
      },


      formatSkillTestTimelineMs(value) {
        const ms = Math.max(0, Number(value || 0));
        const totalSeconds = Math.round(ms / 1000);
        if (totalSeconds < 60) {
          return `${totalSeconds}s`;
        }
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
      },


      skillTestTimelineEventPercent(event) {
        const duration = this.skillTestTimelineDurationMs();
        return Math.min(100, Math.max(0, (Number(event?.at_ms || 0) / duration) * 100));
      },


      skillTestTimelineEventLeftStyle(event) {
        return `left: clamp(2.5rem, ${this.skillTestTimelineEventPercent(event)}%, calc(100% - 2.5rem))`;
      },

      skillTestTimelineExpandedEventLeftStyle(event) {
        return `left: clamp(0.75rem, calc(${this.skillTestTimelineEventPercent(event)}% - 10rem), calc(100% - 20rem - 0.75rem))`;
      },

      skillTestTimelineLaneHasExpandedEvent(laneId) {
        const selected = this.skillTestTimelineSelectedEvent();
        return Boolean(selected?.event?.lane_id === laneId);
      },

      canAddSkillTestTimelineEventToLane(laneId) {
        return !this.skillTestTimelineLaneHasExpandedEvent(laneId);
      },


      skillTestTimelineLaneLabel(lane) {
        return lane?.id === "expected.semantic" ? "语义" : lane?.label || lane?.id || "";
      },


      skillTestTimelineLaneIcon(laneId) {
        if (laneId === "input.image") {
          return "image";
        }
        if (laneId === "input.audio") {
          return "graphic_eq";
        }
        if (laneId === "input.video") {
          return "movie";
        }
        if (laneId === "expected.semantic") {
          return "fact_check";
        }
        return "text_fields";
      },


      skillTestTimelineLaneGroup(laneId) {
        return laneId === "expected.semantic" ? "output" : "input";
      },


      skillTestTimelineLaneGroupLabel(laneId) {
        return this.skillTestTimelineLaneGroup(laneId) === "output" ? "输出" : "输入";
      },


      shouldShowSkillTestTimelineLaneGroup(lane, laneIndex) {
        if (!lane) {
          return false;
        }
        const lanes = this.skillTestTimelineLanes();
        const group = this.skillTestTimelineLaneGroup(lane.id);
        return lanes.findIndex((item) => this.skillTestTimelineLaneGroup(item.id) === group) === laneIndex;
      },


      skillTestTimelineLaneTone(laneId) {
        if (laneId === "expected.semantic") {
          return "border-sky-500/30 bg-sky-500/10 text-sky-200";
        }
        if (laneId === "input.image") {
          return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
        }
        if (laneId === "input.audio") {
          return "border-amber-500/25 bg-amber-500/10 text-amber-200";
        }
        if (laneId === "input.video") {
          return "border-violet-500/25 bg-violet-500/10 text-violet-200";
        }
        return "border-orange-500/30 bg-orange-500/10 text-orange-200";
      },


      skillTestTimelineEventTone(event) {
        return this.skillTestTimelineLaneTone(event?.lane_id || "input.text");
      },

      isSkillTestTimelineEventSelected(event) {
        return Boolean(event?.id && (this.selectedSkillTestTimelineEventIds || []).includes(event.id));
      },

      skillTestTimelineEventFrameClass(event) {
        const selectedTone = this.isSkillTestTimelineEventSelected(event)
          ? " ring-2 ring-orange-400/70 border-orange-400/80"
          : "";
        return `${this.skillTestTimelineEventTone(event)}${selectedTone}`;
      },

      skillTestTimelineExpandedEventTone(event) {
        const laneId = event?.lane_id || "input.text";
        if (laneId === "expected.semantic") {
          return "border-sky-500/60 bg-slate-950 text-slate-100 ring-1 ring-sky-500/25";
        }
        if (laneId === "input.image") {
          return "border-emerald-500/60 bg-slate-950 text-slate-100 ring-1 ring-emerald-500/25";
        }
        if (laneId === "input.audio") {
          return "border-amber-500/60 bg-slate-950 text-slate-100 ring-1 ring-amber-500/25";
        }
        if (laneId === "input.video") {
          return "border-violet-500/60 bg-slate-950 text-slate-100 ring-1 ring-violet-500/25";
        }
        return "border-orange-500/60 bg-slate-950 text-slate-100 ring-1 ring-orange-500/25";
      },


      skillTestTimelineEventUsesAsset(event) {
        return ["input.image", "input.audio", "input.video"].includes(event?.lane_id || "");
      },


      skillTestTimelineAcceptForLane(laneId) {
        if (laneId === "input.audio") {
          return "audio/*";
        }
        if (laneId === "input.video") {
          return "video/*";
        }
        return "image/*";
      },


      skillTestAssetById(assetId) {
        return (this.skillTestDataObjects || []).find((asset) => asset.id === assetId) || null;
      },


      skillTestAssetLabel(asset) {
        if (!asset) {
          return "";
        }
        return asset.name || asset.filename || asset.id;
      },

      skillTestTimelineEventAssetLabel(event) {
        if (!event?.asset_id) {
          return "未上传文件";
        }
        const asset = this.skillTestAssetById(event.asset_id);
        return asset ? this.skillTestAssetLabel(asset) : "文件已绑定";
      },


      skillTestAssetMatchesLane(asset, laneId) {
        if (!asset || !laneId) {
          return false;
        }
        const mimeType = asset.mime_type || "";
        if (mimeType) {
          return this.skillTestMimeMatchesLane(mimeType, laneId);
        }
        if (asset.lane_id === laneId) {
          return true;
        }
        return false;
      },


      skillTestMimeMatchesLane(mimeType, laneId) {
        if (!mimeType) {
          return true;
        }
        if (laneId === "input.image") {
          return mimeType.startsWith("image/");
        }
        if (laneId === "input.audio") {
          return mimeType.startsWith("audio/");
        }
        if (laneId === "input.video") {
          return mimeType.startsWith("video/");
        }
        return false;
      },


      skillTestAssetsForLane(laneId) {
        return (this.skillTestDataObjects || []).filter((asset) => this.skillTestAssetMatchesLane(asset, laneId));
      },


      skillTestTimelineClickAtMs(pointerEvent, trackElement = null) {
        const track = trackElement || pointerEvent.currentTarget;
        if (!track || !track.getBoundingClientRect) {
          return 0;
        }
        const rect = track.getBoundingClientRect();
        const percent = rect.width > 0 ? (pointerEvent.clientX - rect.left) / rect.width : 0;
        const seconds = Math.round((this.skillTestTimelineDurationMs() * Math.min(1, Math.max(0, percent))) / 1000);
        return seconds * 1000;
      },


      skillTestTimelineEventSeconds(event) {
        return Math.max(0, Math.round(Number(event?.at_ms || 0) / 1000));
      },

      cloneSkillTestTimelineEvent(event) {
        return JSON.parse(JSON.stringify(event || {}));
      },

      ensureSkillTestTimelineEventDraft() {
        const selected = this.skillTestTimelineSelectedEvent();
        if (!selected) {
          this.skillTestTimelineEventDraft = null;
          return null;
        }
        if (!this.skillTestTimelineEventDraft || this.skillTestTimelineEventDraft.id !== selected.event.id) {
          this.skillTestTimelineEventDraft = this.cloneSkillTestTimelineEvent(selected.event);
        }
        return this.skillTestTimelineEventDraft;
      },

      skillTestTimelineEditorEvent(event) {
        if (event?.id && event.id === this.selectedSkillTestTimelineEventId) {
          return this.ensureSkillTestTimelineEventDraft() || event;
        }
        return event || {};
      },

      skillTestTimelineSelectedAtMs() {
        const selected = this.skillTestTimelineSelectedEvent();
        if (!selected) {
          return 0;
        }
        const editorEvent = this.skillTestTimelineEditorEvent(selected.event);
        return Math.min(this.skillTestTimelineDurationMs(), Math.max(0, Number(editorEvent?.at_ms || 0)));
      },


      selectSkillTestTimelineEvent(eventId) {
        this.selectedSkillTestTimelineEventId = eventId || "";
        this.selectedSkillTestTimelineEventIds = eventId ? [eventId] : [];
        this.skillTestTimelineEventDraft = null;
        if (eventId) {
          this.ensureSkillTestTimelineEventDraft();
        }
      },

      toggleSkillTestTimelineEventSelection(eventId) {
        if (!eventId) {
          return;
        }
        const selected = new Set(this.selectedSkillTestTimelineEventIds || []);
        if (selected.has(eventId)) {
          selected.delete(eventId);
        } else {
          selected.add(eventId);
        }
        this.selectedSkillTestTimelineEventIds = Array.from(selected);
        if (!selected.has(this.selectedSkillTestTimelineEventId)) {
          this.selectedSkillTestTimelineEventId = "";
          this.skillTestTimelineEventDraft = null;
        }
      },

      replaceSkillTestTimelineEventSelection(eventId) {
        this.selectedSkillTestTimelineEventIds = eventId ? [eventId] : [];
      },

      handleSkillTestTimelineEventClick(clickEvent, eventId) {
        if (!eventId || this.wasSkillTestTimelineDragGesture(eventId)) {
          return;
        }
        if (clickEvent?.metaKey || clickEvent?.ctrlKey || clickEvent?.shiftKey) {
          this.toggleSkillTestTimelineEventSelection(eventId);
          return;
        }
        this.openSkillTestTimelineEventEditor(eventId);
      },

      openSkillTestTimelineEventEditor(eventId) {
        if (!eventId || this.wasSkillTestTimelineDragGesture(eventId)) {
          return;
        }
        this.selectSkillTestTimelineEvent(eventId);
      },

      collapseSkillTestTimelineEventEditor() {
        this.selectedSkillTestTimelineEventId = "";
        this.selectedSkillTestTimelineEventIds = [];
        this.skillTestTimelineEventDraft = null;
      },

      isSkillTestTimelineEventExpanded(event) {
        return Boolean(event?.id && this.selectedSkillTestTimelineEventId === event.id);
      },

      wasSkillTestTimelineDragGesture(eventId) {
        const lastDrag = this.skillTestTimelineLastDrag;
        return Boolean(
          lastDrag?.eventId === eventId &&
          lastDrag.moved &&
          Date.now() - Number(lastDrag.endedAt || 0) < 350
        );
      },


      skillTestTimelineSelectedEvent() {
        const events = this.skillTestTimelineEvents();
        const selectedIndex = events.findIndex((event) => event.id === this.selectedSkillTestTimelineEventId);
        if (selectedIndex >= 0) {
          return { event: events[selectedIndex], index: selectedIndex };
        }
        return null;
      },


      addSkillTestTimelineEventFromLane(pointerEvent, laneId) {
        if (!this.canAddSkillTestTimelineEventToLane(laneId)) {
          return;
        }
        const timeline = this.skillTestTimelineDraft();
        const atMs = this.skillTestTimelineClickAtMs(pointerEvent);
        const isExpectation = this.isSkillTestExpectationLane(laneId);
        const event = isExpectation
          ? {
              id: this.nextSkillTestTimelineEventId(timeline.events, "expected"),
              lane_id: "expected.semantic",
              at_ms: atMs,
              expectation: "描述该时间点以前应满足的语义输出"
            }
          : {
              id: this.nextSkillTestTimelineEventId(timeline.events, "input"),
              lane_id: laneId || "input.text",
              at_ms: atMs,
              ...this.skillTestEventDefaultsForLane(laneId || "input.text"),
              payload_inline: laneId === "input.text" ? "填写文本输入" : ""
            };
        timeline.events.push(event);
        this.writeSkillTestTimelineDraft(timeline);
        this.selectSkillTestTimelineEvent(event.id);
      },


      addSkillTestTimelineInputEvent() {
        const timeline = this.skillTestTimelineDraft();
        const laneId = this.skillTestCaseForm.event_lane_id || "input.text";
        const defaultsForLane = this.skillTestEventDefaultsForLane(laneId);
        const event = {
          id: this.nextSkillTestTimelineEventId(timeline.events, "input"),
          lane_id: laneId,
          at_ms: Math.max(0, Number(this.skillTestCaseForm.event_at_ms || 0)),
          ...defaultsForLane,
          payload_inline: this.skillTestCaseForm.event_payload_inline || ""
        };
        if (this.skillTestCaseForm.event_asset_id) {
          event.asset_id = this.skillTestCaseForm.event_asset_id;
        }
        timeline.events.push(event);
        this.skillTestCaseForm.event_payload_inline = "";
        this.skillTestCaseForm.event_asset_id = "";
        this.writeSkillTestTimelineDraft(timeline);
        this.selectSkillTestTimelineEvent(event.id);
      },


      addSkillTestTimelineExpectation() {
        const timeline = this.skillTestTimelineDraft();
        const expectation = this.skillTestCaseForm.expectation_text.trim();
        if (!expectation) {
          this.showNotice("error", "请填写语义期望。");
          return;
        }
        const event = {
          id: this.nextSkillTestTimelineEventId(timeline.events, "expected"),
          lane_id: "expected.semantic",
          at_ms: Math.max(0, Number(this.skillTestCaseForm.expectation_at_ms || 0)),
          expectation
        };
        timeline.events.push(event);
        this.skillTestCaseForm.expectation_text = "";
        this.writeSkillTestTimelineDraft(timeline);
        this.selectSkillTestTimelineEvent(event.id);
      },


      removeSkillTestTimelineEvent(eventIndex) {
        const timeline = this.skillTestTimelineDraft();
        const removedId = timeline.events[eventIndex]?.id;
        timeline.events.splice(eventIndex, 1);
        if (removedId === this.selectedSkillTestTimelineEventId) {
          this.selectedSkillTestTimelineEventId = "";
          this.skillTestTimelineEventDraft = null;
        }
        this.selectedSkillTestTimelineEventIds = (this.selectedSkillTestTimelineEventIds || []).filter((eventId) => eventId !== removedId);
        this.writeSkillTestTimelineDraft(timeline);
      },

      applySkillTestTimelineEventField(event, durationMs, fieldName, value) {
        if (fieldName === "at_ms") {
          event[fieldName] = Math.min(durationMs, Math.max(0, Number(value || 0)));
        } else if (fieldName === "lane_id") {
          const laneId = value || "input.text";
          event[fieldName] = laneId;
          if (this.isSkillTestExpectationLane(laneId)) {
            event.expectation = event.expectation || event.payload_inline || "";
            delete event.payload_inline;
            delete event.asset_id;
            delete event.event_kind;
            delete event.mime_type;
          } else {
            Object.assign(event, this.skillTestEventDefaultsForLane(laneId));
            delete event.expectation;
          }
          if (!this.skillTestTimelineEventUsesAsset(event)) {
            delete event.asset_id;
          } else if (
            event.asset_id &&
            !this.skillTestAssetMatchesLane(this.skillTestAssetById(event.asset_id), laneId)
          ) {
            delete event.asset_id;
          }
        } else if (fieldName === "required") {
          event[fieldName] = Boolean(value);
        } else {
          event[fieldName] = value;
        }
      },

      updateSkillTestTimelineEvent(eventIndex, fieldName, value) {
        const timeline = this.skillTestTimelineDraft();
        if (!timeline.events[eventIndex]) {
          return;
        }
        const nextEvent = { ...timeline.events[eventIndex] };
        this.applySkillTestTimelineEventField(nextEvent, timeline.duration_ms, fieldName, value);
        timeline.events[eventIndex] = nextEvent;
        this.writeSkillTestTimelineDraft(timeline);
      },

      updateSkillTestTimelineEventDraft(fieldName, value) {
        const draft = this.ensureSkillTestTimelineEventDraft();
        if (!draft) {
          return;
        }
        const nextDraft = { ...draft };
        this.applySkillTestTimelineEventField(nextDraft, this.skillTestTimelineDurationMs(), fieldName, value);
        this.skillTestTimelineEventDraft = nextDraft;
      },

      flushSkillTestTimelineEventDraft() {
        const draft = this.skillTestTimelineEventDraft;
        if (!draft?.id) {
          return false;
        }
        const timeline = this.skillTestTimelineDraft();
        const eventIndex = timeline.events.findIndex((event) => event.id === draft.id);
        if (eventIndex >= 0) {
          timeline.events[eventIndex] = { ...timeline.events[eventIndex], ...draft };
          this.writeSkillTestTimelineDraft(timeline);
          return true;
        }
        return false;
      },

      saveSkillTestTimelineEventEditor() {
        if (!this.flushSkillTestTimelineEventDraft()) {
          this.collapseSkillTestTimelineEventEditor();
          return;
        }
        this.collapseSkillTestTimelineEventEditor();
      },


      updateSelectedSkillTestTimelineEvent(fieldName, value) {
        if (this.skillTestTimelineEventDraft) {
          this.updateSkillTestTimelineEventDraft(fieldName, value);
          return;
        }
        const selected = this.skillTestTimelineSelectedEvent();
        if (!selected) {
          return;
        }
        this.updateSkillTestTimelineEvent(selected.index, fieldName, value);
      },

      updateSkillTestTimelineSelectedAtMs(value) {
        this.updateSelectedSkillTestTimelineEvent("at_ms", value);
      },


      updateSelectedSkillTestTimelineEventSeconds(value) {
        const selected = this.skillTestTimelineSelectedEvent();
        if (!selected) {
          return;
        }
        this.updateSkillTestTimelineEvent(selected.index, "at_ms", Math.max(0, Math.round(Number(value || 0))) * 1000);
      },


      updateSelectedSkillTestTimelineEventAsset(assetId) {
        const selected = this.skillTestTimelineSelectedEvent();
        if (!selected) {
          return;
        }
        if (!assetId) {
          this.updateSkillTestTimelineEvent(selected.index, "asset_id", "");
          return;
        }
        this.updateSkillTestTimelineEvent(selected.index, "asset_id", assetId);
      },


      removeSelectedSkillTestTimelineEvent() {
        const selected = this.skillTestTimelineSelectedEvent();
        if (selected) {
          this.removeSkillTestTimelineEvent(selected.index);
        }
      },


      setSkillTestTimelineEventAtFromTrack(pointerEvent, eventIndexOrId, trackElement = null) {
        const atMs = this.skillTestTimelineClickAtMs(pointerEvent, trackElement);
        const eventIndex =
          typeof eventIndexOrId === "string"
            ? this.skillTestTimelineEvents().findIndex((event) => event.id === eventIndexOrId)
            : eventIndexOrId;
        if (eventIndex < 0) {
          return;
        }
        this.updateSkillTestTimelineEvent(eventIndex, "at_ms", atMs);
      },

      skillTestTimelineSelectedDragEventIds(eventId) {
        const selectedIds = this.selectedSkillTestTimelineEventIds || [];
        if (eventId && selectedIds.includes(eventId)) {
          return selectedIds;
        }
        return eventId ? [eventId] : [];
      },

      setSkillTestTimelineDragGroupAtFromTrack(pointerEvent, trackElement = null) {
        const dragState = this.skillTestTimelineDragState;
        if (!dragState?.eventIds?.length) {
          return;
        }
        const pointerAtMs = this.skillTestTimelineClickAtMs(pointerEvent, trackElement);
        const durationMs = this.skillTestTimelineDurationMs();
        const initialEvents = dragState.initialEvents || [];
        if (!initialEvents.length) {
          return;
        }
        const minAtMs = Math.min(...initialEvents.map((event) => Number(event.at_ms || 0)));
        const maxAtMs = Math.max(...initialEvents.map((event) => Number(event.at_ms || 0)));
        const minDeltaMs = -minAtMs;
        const maxDeltaMs = durationMs - maxAtMs;
        const requestedDeltaMs = pointerAtMs - Number(dragState.anchorStartAtMs || 0);
        const deltaMs = Math.min(maxDeltaMs, Math.max(minDeltaMs, requestedDeltaMs));
        const initialById = new Map(initialEvents.map((event) => [event.id, event]));
        const timeline = this.skillTestTimelineDraft();
        timeline.events = (timeline.events || []).map((event) => {
          const initial = initialById.get(event.id);
          if (!initial) {
            return event;
          }
          return {
            ...event,
            at_ms: Math.min(durationMs, Math.max(0, Number(initial.at_ms || 0) + deltaMs))
          };
        });
        this.writeSkillTestTimelineDraft(timeline);
      },


      startSkillTestTimelineEventDrag(pointerEvent, eventIndex) {
        if (pointerEvent.metaKey || pointerEvent.ctrlKey || pointerEvent.shiftKey) {
          return;
        }
        const track = pointerEvent.currentTarget.closest("[data-skill-test-timeline-track]");
        const event = this.skillTestTimelineEvents()[eventIndex];
        const eventId = event?.id || "";
        if (!eventId) {
          return;
        }
        const eventIds = this.skillTestTimelineSelectedDragEventIds(eventId);
        if (!(this.selectedSkillTestTimelineEventIds || []).includes(eventId)) {
          this.replaceSkillTestTimelineEventSelection(eventId);
        }
        const initialEvents = this.skillTestTimelineEvents()
          .filter((item) => eventIds.includes(item.id))
          .map((item) => ({ id: item.id, at_ms: Number(item.at_ms || 0) }));
        this.skillTestTimelineDragState = {
          eventId,
          eventIds,
          initialEvents,
          anchorStartAtMs: Number(event.at_ms || 0),
          startX: pointerEvent.clientX,
          startY: pointerEvent.clientY,
          moved: false
        };
        const moveHandler = (moveEvent) => {
          const dragState = this.skillTestTimelineDragState;
          if (!dragState || dragState.eventId !== eventId) {
            return;
          }
          const deltaX = Math.abs(moveEvent.clientX - dragState.startX);
          const deltaY = Math.abs(moveEvent.clientY - dragState.startY);
          if (!dragState.moved && Math.max(deltaX, deltaY) < 4) {
            return;
          }
          dragState.moved = true;
          this.setSkillTestTimelineDragGroupAtFromTrack(moveEvent, track);
        };
        const upHandler = () => {
          const dragState = this.skillTestTimelineDragState;
          this.skillTestTimelineLastDrag = {
            eventId,
            moved: Boolean(dragState?.moved),
            endedAt: Date.now()
          };
          this.skillTestTimelineDragState = null;
          window.removeEventListener("pointermove", moveHandler);
          window.removeEventListener("pointerup", upHandler);
        };
        window.addEventListener("pointermove", moveHandler);
        window.addEventListener("pointerup", upHandler, { once: true });
      },


      skillTestTimelineEventLabel(event) {
        if (this.isSkillTestTimelineExpectationEvent(event)) {
          return event.expectation || "填写语义期望";
        }
        if (event.asset_id) {
          const asset = this.skillTestAssetById(event.asset_id);
          if (asset) {
            return this.skillTestAssetLabel(asset);
          }
        }
        if (this.skillTestTimelineEventUsesAsset(event)) {
          return "上传文件";
        }
        if (typeof event.payload_inline === "string") {
          return event.payload_inline || event.event_kind;
        }
        return event.event_kind || event.lane_id;
      },


      skillTestTimelineEventTextValue(event) {
        if (this.isSkillTestTimelineExpectationEvent(event)) {
          return event.expectation || "";
        }
        const payload = event?.payload_inline;
        if (typeof payload === "string") {
          return payload;
        }
        if (payload && typeof payload === "object") {
          return payload.caption || payload.description || "";
        }
        return "";
      },


      skillTestPendingAssets() {
        return (this.skillTestDataObjects || []).filter((asset) => asset.is_local && asset.file);
      },


      skillTestTimelineWithAssetIdMap(timeline, assetIdMap = {}, options = {}) {
        const mapped = JSON.parse(JSON.stringify(timeline || this.defaultSkillTestTimeline()));
        mapped.events = (mapped.events || []).map((event) => {
          const next = { ...event };
          if (next.asset_id && assetIdMap[next.asset_id]) {
            next.asset_id = assetIdMap[next.asset_id];
          } else if (options.removeLocalAssetIds && typeof next.asset_id === "string" && next.asset_id.startsWith("local_")) {
            delete next.asset_id;
          }
          return next;
        });
        return mapped;
      },


      async loadSkillTestCases(skillId) {
        this.busy.skillTestCases = true;
        try {
          this.skillTestCases = await this.apiRequest(`/skills/${skillId}/test-scenarios`);
        } finally {
          this.busy.skillTestCases = false;
        }
      },


      async createSkillTestCase() {
        if (!this.currentSkill || !this.skillTestCaseForm.name.trim()) {
          this.showNotice("error", "请填写测试场景名称。");
          return;
        }
        this.busy.skillTestSave = true;
        try {
          const timeline = this.parseSkillTestTimeline();
          const pendingAssets = this.skillTestPendingAssets();
          const createTimeline = this.skillTestTimelineWithAssetIdMap(timeline, {}, { removeLocalAssetIds: true });
          const created = await this.apiRequest(`/skills/${this.currentSkill.id}/test-scenarios`, {
            method: "POST",
            body: JSON.stringify({
              name: this.skillTestCaseForm.name.trim(),
              description: this.skillTestCaseForm.description.trim(),
              target_version_selector: "latest",
              target_compile_artifact_id: null,
              duration_ms: createTimeline.duration_ms,
              timeline: createTimeline,
              judge_policy: this.parseSkillTestJudgePolicy()
            })
          });
          if (pendingAssets.length) {
            const assetIdMap = await this.persistSkillTestPendingAssets(created.id, pendingAssets);
            const patchedTimeline = this.skillTestTimelineWithAssetIdMap(timeline, assetIdMap);
            await this.apiRequest(`/skills/${this.currentSkill.id}/test-scenarios/${created.id}`, {
              method: "PATCH",
              body: JSON.stringify({
                duration_ms: patchedTimeline.duration_ms,
                timeline: patchedTimeline
              })
            });
          }
          this.skillTestDataObjects = [];
          this.resetSkillTestCaseForm();
          await this.loadSkillTestCases(this.currentSkill.id);
          await this.navigate(buildSkillTestScenarioPath(this.currentSkill.id, created.id));
        } catch (error) {
          this.showNotice("error", error.message || "创建测试场景失败。");
        } finally {
          this.busy.skillTestSave = false;
        }
      },


      async loadSkillTestCaseDetail(skillId, scenarioId) {
        this.busy.skillTestCase = true;
        try {
          const [caseDetail, dataObjects, runs] = await Promise.all([
            this.apiRequest(`/skills/${skillId}/test-scenarios/${scenarioId}`),
            this.apiRequest(`/skills/${skillId}/test-scenarios/${scenarioId}/assets`),
            this.apiRequest(`/skills/${skillId}/test-scenarios/${scenarioId}/runs`)
          ]);
          this.skillTestCase = caseDetail;
          this.skillTestDataObjects = dataObjects;
          this.skillTestRuns = runs;
          this.resetSkillTestCaseForm(caseDetail);
        } finally {
          this.busy.skillTestCase = false;
        }
      },


      async saveSkillTestCase() {
        if (!this.currentSkill || !this.skillTestCase) {
          return;
        }
        this.busy.skillTestSave = true;
        try {
          const timeline = this.parseSkillTestTimeline();
          const saved = await this.apiRequest(`/skills/${this.currentSkill.id}/test-scenarios/${this.skillTestCase.id}`, {
            method: "PATCH",
            body: JSON.stringify({
              name: this.skillTestCaseForm.name.trim(),
              description: this.skillTestCaseForm.description.trim(),
              target_version_selector: "latest",
              target_compile_artifact_id: null,
              duration_ms: timeline.duration_ms,
              timeline,
              judge_policy: this.parseSkillTestJudgePolicy()
            })
          });
          this.skillTestCase = saved;
          await this.loadSkillTestCases(this.currentSkill.id);
          this.showNotice("success", "测试场景已保存。");
        } catch (error) {
          this.showNotice("error", error.message || "保存测试场景失败。");
        } finally {
          this.busy.skillTestSave = false;
        }
      },


      async deleteSkillTestCase(caseId = this.skillTestCase?.id) {
        if (!this.currentSkill || !caseId) {
          return;
        }
        this.busy.skillTestSave = true;
        try {
          await this.apiRequest(`/skills/${this.currentSkill.id}/test-scenarios/${caseId}`, { method: "DELETE" });
          await this.loadSkillTestCases(this.currentSkill.id);
          if (this.route.name === "skill-test-scenario") {
            await this.navigate(buildSkillDetailPath(this.currentSkill.id));
            this.activeDetailTab = "test";
          }
        } catch (error) {
          this.showNotice("error", error.message || "删除测试场景失败。");
        } finally {
          this.busy.skillTestSave = false;
        }
      },


      inferSkillTestLaneForMime(mimeType = "") {
        if (mimeType.startsWith("audio/")) {
          return "input.audio";
        }
        if (mimeType.startsWith("video/")) {
          return "input.video";
        }
        return "input.image";
      },


      createSkillTestAssetDraftFromFile(file, options = {}) {
        const laneId = options.lane_id || options.laneId || this.inferSkillTestLaneForMime(file.type || "");
        return {
          id: `local_${Date.now()}_${Math.random().toString(16).slice(2)}`,
          skill_definition_id: this.currentSkill?.id || "",
          scenario_id: this.skillTestCase?.id || "",
          artifact_object_id: "",
          name: options.name || file.name,
          description: options.description || "",
          lane_id: laneId,
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          size_bytes: file.size || 0,
          checksum: "local",
          created_at: new Date().toISOString(),
          file,
          is_local: true
        };
      },


      handleSkillTestFile(event) {
        this.skillTestDataForm.file = event.target.files?.[0] || null;
        if (this.skillTestDataForm.file && !this.skillTestDataForm.name) {
          this.skillTestDataForm.name = this.skillTestDataForm.file.name;
        }
        if (this.skillTestDataForm.file) {
          this.skillTestDataForm.role = this.inferSkillTestLaneForMime(this.skillTestDataForm.file.type || "");
        }
      },


      async handleSkillTestTimelineEventFile(event) {
        const file = event.target.files?.[0] || null;
        if (!file) {
          return;
        }
        const selected = this.skillTestTimelineSelectedEvent();
        const editorEvent = selected ? this.skillTestTimelineEditorEvent(selected.event) : null;
        if (!selected || !this.skillTestTimelineEventUsesAsset(editorEvent)) {
          this.showNotice("error", "请先选择图片、音频或视频事件。");
          event.target.value = "";
          return;
        }
        const laneId = editorEvent.lane_id;
        if (!this.skillTestMimeMatchesLane(file.type || "", laneId)) {
          this.showNotice("error", "文件类型与当前信道不匹配。");
          event.target.value = "";
          return;
        }
        const assetDraft = this.createSkillTestAssetDraftFromFile(file, {
          lane_id: laneId,
          description: this.skillTestTimelineEventTextValue(editorEvent)
        });
        this.busy.skillTestData = true;
        try {
          if (this.skillTestCase) {
            const uploaded = await this.uploadSkillTestAssetFile(this.skillTestCase.id, assetDraft);
            this.skillTestDataObjects = [uploaded, ...(this.skillTestDataObjects || []).filter((asset) => asset.id !== uploaded.id)];
            this.updateSkillTestTimelineEventDraft("asset_id", uploaded.id);
          } else {
            this.skillTestDataObjects = [assetDraft, ...(this.skillTestDataObjects || [])];
            this.updateSkillTestTimelineEventDraft("asset_id", assetDraft.id);
          }
        } catch (error) {
          this.showNotice("error", error.message || "上传测试文件失败。");
        } finally {
          this.busy.skillTestData = false;
          event.target.value = "";
        }
      },


      async uploadSkillTestAssetFile(scenarioId, assetDraft) {
        const formData = new FormData();
        formData.append("file", assetDraft.file);
        formData.append("name", assetDraft.name || assetDraft.file.name);
        formData.append("description", assetDraft.description || "");
        formData.append("lane_id", assetDraft.lane_id || this.inferSkillTestLaneForMime(assetDraft.mime_type || ""));
        return this.apiRequest(`/skills/${this.currentSkill.id}/test-scenarios/${scenarioId}/assets`, {
          method: "POST",
          body: formData
        });
      },


      async persistSkillTestPendingAssets(scenarioId, pendingAssets = this.skillTestPendingAssets()) {
        const assetIdMap = {};
        for (const asset of pendingAssets) {
          const uploaded = await this.uploadSkillTestAssetFile(scenarioId, asset);
          assetIdMap[asset.id] = uploaded.id;
        }
        return assetIdMap;
      },


      async uploadSkillTestData() {
        if (!this.currentSkill || !this.skillTestDataForm.file) {
          this.showNotice("error", "请选择要上传的测试数据。");
          return;
        }
        this.busy.skillTestData = true;
        try {
          const assetDraft = this.createSkillTestAssetDraftFromFile(this.skillTestDataForm.file, {
            name: this.skillTestDataForm.name || this.skillTestDataForm.file.name,
            description: this.skillTestDataForm.description || "",
            lane_id: this.skillTestDataForm.role || this.inferSkillTestLaneForMime(this.skillTestDataForm.file.type || ""),
          });
          if (this.skillTestCase) {
            await this.uploadSkillTestAssetFile(this.skillTestCase.id, assetDraft);
            await this.loadSkillTestCaseDetail(this.currentSkill.id, this.skillTestCase.id);
          } else {
            this.skillTestDataObjects = [assetDraft, ...(this.skillTestDataObjects || [])];
          }
          this.skillTestDataForm = { name: "", description: "", role: "input.image", file: null };
        } catch (error) {
          this.showNotice("error", error.message || "上传测试资源失败。");
        } finally {
          this.busy.skillTestData = false;
        }
      },


      async deleteSkillTestData(dataId) {
        const localAsset = (this.skillTestDataObjects || []).find((asset) => asset.id === dataId && asset.is_local);
        if (localAsset) {
          this.skillTestDataObjects = this.skillTestDataObjects.filter((asset) => asset.id !== dataId);
          const timeline = this.skillTestTimelineDraft();
          timeline.events = (timeline.events || []).map((event) => {
            if (event.asset_id === dataId) {
              const next = { ...event };
              delete next.asset_id;
              return next;
            }
            return event;
          });
          this.writeSkillTestTimelineDraft(timeline);
          return;
        }
        if (!this.currentSkill || !this.skillTestCase) {
          return;
        }
        this.busy.skillTestData = true;
        try {
          await this.apiRequest(`/skills/${this.currentSkill.id}/test-scenarios/${this.skillTestCase.id}/assets/${dataId}`, {
            method: "DELETE"
          });
          await this.loadSkillTestCaseDetail(this.currentSkill.id, this.skillTestCase.id);
        } finally {
          this.busy.skillTestData = false;
        }
      },


      toggleSkillTestDataSelection(dataId) {
        const selected = new Set(this.skillTestStartForm.selected_data_object_ids || []);
        if (selected.has(dataId)) {
          selected.delete(dataId);
        } else {
          selected.add(dataId);
        }
        this.skillTestStartForm.selected_data_object_ids = Array.from(selected);
      },


      isSkillTestDataSelected(dataId) {
        return (this.skillTestStartForm.selected_data_object_ids || []).includes(dataId);
      },


      async startSkillTestRun(caseId = this.skillTestCase?.id) {
        if (!this.currentSkill || !caseId) {
          return;
        }
        this.busy.skillTestRun = true;
        try {
          const testRun = await this.apiRequest(`/skills/${this.currentSkill.id}/test-scenarios/${caseId}/runs`, {
            method: "POST",
            body: JSON.stringify({})
          });
          await this.navigate(buildSkillTestScenarioRunReviewPath(this.currentSkill.id, caseId, testRun.id));
        } catch (error) {
          const activeTestRunId = error.payload?.details?.scenario_run_id;
          if (activeTestRunId) {
            this.showNotice("info", error.message || "当前场景已有进行中的测试，请继续已有测试。");
            await this.navigate(buildSkillTestScenarioRunReviewPath(this.currentSkill.id, caseId, activeTestRunId));
            return;
          }
          this.showNotice("error", error.message || "启动测试失败。");
        } finally {
          this.busy.skillTestRun = false;
        }
      },


      async loadSkillTestRunReview(skillId, scenarioId, scenarioRunId) {
        this.stopSkillTestReviewPlayback();
        this.busy.skillTestRun = true;
        try {
          const review = await this.apiRequest(`/skill-test-scenario-runs/${scenarioRunId}/review`);
          this.skillTestReview = review;
          this.skillTestCase = review.scenario;
          this.skillTestRun = review.scenario_run;
          this.skillTestRuns = window.PSOPRuntimeEvents.mergeById(this.skillTestRuns, [review.scenario_run]);
          this.skillTestReviewPlayheadMs = 0;
          this.skillTestReviewCursor = 0;
          this.skillTestReviewAutoFollow = true;
          if (review.replay?.run) {
            this.liveRun = review.replay.run;
            this.liveRunTerminalEvents = window.PSOPRuntimeEvents.mergeBySeq([], review.replay.terminal_events || []);
            this.liveRunTraceEvents = window.PSOPRuntimeEvents.mergeBySeq([], review.replay.trace_events || []);
            this.liveRunBindings = window.PSOPRuntimeEvents.mergeById([], review.replay.bindings || []);
          }
          await this.loadSkillTestCaseDetail(skillId, scenarioId);
          this.skillTestReview = review;
          this.skillTestCase = review.scenario;
          this.skillTestRun = review.scenario_run;
          this.applySkillTestReviewInitialPlayhead(review);
        } finally {
          this.busy.skillTestRun = false;
        }
      },


      async evaluateSkillTestRun() {
        if (!this.skillTestRun) {
          return;
        }
        this.busy.skillTestEvaluate = true;
        try {
          this.skillTestRun = await this.apiRequest(`/skill-test-scenario-runs/${this.skillTestRun.id}/evaluate`, {
            method: "POST"
          });
        } finally {
          this.busy.skillTestEvaluate = false;
        }
      },


      currentSkillTestForkCursor() {
        const cutoffMs = this.skillTestReviewPlayheadMsValue();
        const anchors = this.skillTestReview?.cursor_anchors || [];
        const anchor =
          [...anchors]
            .reverse()
            .find((item) => Number(item.time_ms || 0) <= cutoffMs) || anchors[0] || {};
        return {
          time_ms: Math.max(0, Number(anchor.time_ms || 0)),
          terminal_seq: Math.max(0, Number(anchor.terminal_seq || 0)),
          snapshot_seq: Math.max(0, Number(anchor.snapshot_seq || 0))
        };
      },


      async forkSkillTestScenario() {
        if (!this.currentSkill || !this.skillTestRun) {
          return;
        }
        this.busy.skillTestSave = true;
        try {
          const forked = await this.apiRequest(`/skill-test-scenario-runs/${this.skillTestRun.id}/fork-scenario`, {
            method: "POST",
            body: JSON.stringify({
              cursor: this.currentSkillTestForkCursor(),
              name: `${this.skillTestCase?.name || "Scenario"} fork`
            })
          });
          await this.navigate(buildSkillTestScenarioPath(this.currentSkill.id, forked.id));
        } catch (error) {
          this.showNotice("error", error.message || "Fork 测试场景失败。");
        } finally {
          this.busy.skillTestSave = false;
        }
      },


      async forkSkillDebug() {
        if (!this.currentSkill || !this.skillTestRun) {
          return;
        }
        this.busy.skillTestRun = true;
        try {
          const invocation = await this.apiRequest(`/skill-test-scenario-runs/${this.skillTestRun.id}/fork-debug`, {
            method: "POST",
            body: JSON.stringify({ cursor: this.currentSkillTestForkCursor() })
          });
          await this.navigate(buildSkillDebugRunLivePath(this.currentSkill.id, invocation.run_id));
        } catch (error) {
          this.showNotice("error", error.message || "Fork 调试失败。");
        } finally {
          this.busy.skillTestRun = false;
        }
      },


      formatAssertionSummary(summary) {
        if (!summary) {
          return "0 passed / 0 failed";
        }
        const inconclusive = summary.inconclusive ? ` / ${summary.inconclusive} inconclusive` : "";
        return `${summary.passed || 0} passed / ${summary.failed || 0} failed / ${summary.pending || 0} pending${inconclusive}`;
      },


      assertionStatusTone(value) {
        return this.statusBadgeTone(value);
      },


      isOpenSkillTestRun(testRun) {
        return [
          "running",
          "queued",
          "pending",
          "waiting_input",
          "waiting_runtime",
          "waiting_checkpoint",
          "waiting_time"
        ].includes(String(testRun?.status || "").toLowerCase());
      },


      openSkillTestRunsCount() {
        return this.skillTestRuns.filter((testRun) => this.isOpenSkillTestRun(testRun)).length;
      },


      openSkillTestRunForCase(caseId = this.skillTestCase?.id) {
        if (!caseId) {
          return null;
        }
        return this.sortedSkillTestRuns().find((testRun) => testRun.scenario_id === caseId && this.isOpenSkillTestRun(testRun)) || null;
      },


      sortedSkillTestRuns() {
        return [...this.skillTestRuns].sort((left, right) => {
          const leftOpen = this.isOpenSkillTestRun(left) ? 1 : 0;
          const rightOpen = this.isOpenSkillTestRun(right) ? 1 : 0;
          if (leftOpen !== rightOpen) {
            return rightOpen - leftOpen;
          }
          const leftTime = new Date(left.updated_at || left.started_at || left.created_at || 0).getTime();
          const rightTime = new Date(right.updated_at || right.started_at || right.created_at || 0).getTime();
          return rightTime - leftTime;
        });
      },


      skillTestRunActivityLabel(testRun) {
        if (!testRun) {
          return "最近 N/A";
        }
        if (testRun.ended_at) {
          return `结束 ${this.formatDateTime(testRun.ended_at)}`;
        }
        return `最近 ${this.formatDateTime(testRun.updated_at || testRun.started_at || testRun.created_at)}`;
      },


      filteredSkillTestCases() {
        const query = this.skillTestCaseSearch.trim().toLowerCase();
        if (!query) {
          return this.skillTestCases;
        }

        return this.skillTestCases.filter((testCase) => {
          const searchable = [
            testCase.name,
            testCase.description,
            JSON.stringify(testCase.timeline || {}),
            testCase.latest_run?.run_id || "",
            testCase.latest_run?.status || "",
            testCase.status || ""
          ]
            .join(" ")
            .toLowerCase();
          return searchable.includes(query);
        });
      },


      skillTestReviewInputSteps() {
        return this.skillTestReviewTimeline().events || [];
      },


      skillTestReviewTimeline() {
        const timeline =
          this.skillTestReview?.scenario_timeline ||
          this.skillTestRun?.timeline ||
          this.skillTestCase?.timeline ||
          this.defaultSkillTestTimeline();
        return this.normalizeSkillTestTimelineDraft(timeline);
      },


      skillTestReviewTimelineLanes() {
        const timeline = this.skillTestReviewTimeline();
        const defaultLaneIds = this.defaultSkillTestTimeline().lanes.map((lane) => lane.id);
        return (timeline.lanes || [])
          .slice()
          .sort((left, right) => {
            const leftIndex = defaultLaneIds.indexOf(left.id);
            const rightIndex = defaultLaneIds.indexOf(right.id);
            return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
          });
      },


      skillTestReviewTimelineEvents() {
        return this.skillTestReviewTimeline().events || [];
      },


      skillTestReviewEventsForLane(laneId) {
        return this.skillTestReviewTimelineEvents()
          .map((event, index) => ({ event, index }))
          .filter((item) => item.event.lane_id === laneId)
          .map((item) => ({
            ...item,
            render_key: `review:${item.event.lane_id}:${item.event.id}:${item.index}`
          }));
      },


      shouldShowSkillTestReviewTimelineLaneGroup(lane, laneIndex) {
        if (!lane) {
          return false;
        }
        const lanes = this.skillTestReviewTimelineLanes();
        const group = this.skillTestTimelineLaneGroup(lane.id);
        return lanes.findIndex((item) => this.skillTestTimelineLaneGroup(item.id) === group) === laneIndex;
      },


      skillTestReviewDurationMs() {
        return Math.max(1, Number(this.skillTestReviewTimeline().duration_ms || 1800000));
      },


      skillTestReviewLatestRecordedMs(review = this.skillTestReview) {
        const run = review?.scenario_run || this.skillTestRun || {};
        const origin =
          new Date(run.time_origin || run.started_at || run.created_at || review?.scenario_run?.time_origin || 0).getTime();
        const values = [];
        (review?.cursor_anchors || []).forEach((anchor) => {
          const value = Number(anchor?.time_ms);
          if (Number.isFinite(value)) {
            values.push(value);
          }
        });
        (review?.driver_events || []).forEach((event) => {
          const value = Number(event?.at_ms);
          if (Number.isFinite(value)) {
            values.push(value);
          }
        });
        (review?.replay?.terminal_events || []).forEach((event) => {
          const occurredAt = new Date(event?.occurred_at).getTime();
          if (Number.isFinite(origin) && origin > 0 && Number.isFinite(occurredAt)) {
            values.push(occurredAt - origin);
          }
        });
        return Math.max(0, ...values);
      },


      skillTestReviewRunElapsedMs(review = this.skillTestReview) {
        const run = review?.scenario_run || this.skillTestRun || {};
        const originValue = run.time_origin || run.started_at || run.created_at;
        const origin = new Date(originValue || 0).getTime();
        if (!Number.isFinite(origin) || origin <= 0) {
          return 0;
        }
        return Math.max(0, Date.now() - origin);
      },


      skillTestReviewInitialPlayheadMs(review = this.skillTestReview) {
        const duration = this.skillTestReviewDurationMs();
        const run = review?.scenario_run || this.skillTestRun || {};
        if (!this.isOpenSkillTestRun(run)) {
          return duration;
        }
        const latestRecorded = this.skillTestReviewLatestRecordedMs(review);
        const elapsed = this.skillTestReviewRunElapsedMs(review);
        return Math.min(duration, Math.max(0, latestRecorded, elapsed));
      },


      applySkillTestReviewInitialPlayhead(review = this.skillTestReview) {
        this.stopSkillTestReviewPlayback();
        this.updateSkillTestReviewPlayhead(this.skillTestReviewInitialPlayheadMs(review));
        this.skillTestReviewAutoFollow = false;
      },


      skillTestReviewPlayheadMsValue() {
        return Math.min(this.skillTestReviewDurationMs(), Math.max(0, Number(this.skillTestReviewPlayheadMs || 0)));
      },


      skillTestReviewProgressPercent() {
        return Math.min(100, Math.max(0, (this.skillTestReviewPlayheadMsValue() / this.skillTestReviewDurationMs()) * 100));
      },


      skillTestReviewTimelineEventPercent(event) {
        return Math.min(100, Math.max(0, (Number(event?.at_ms || 0) / this.skillTestReviewDurationMs()) * 100));
      },


      skillTestReviewTimelineEventLeftStyle(event) {
        return `left: clamp(2.5rem, ${this.skillTestReviewTimelineEventPercent(event)}%, calc(100% - 2.5rem))`;
      },


      skillTestReviewPlayheadLeftStyle() {
        return `left: clamp(0.75rem, ${this.skillTestReviewProgressPercent()}%, calc(100% - 0.75rem))`;
      },


      skillTestReviewTicks() {
        const duration = this.skillTestReviewDurationMs();
        const tickCount = 6;
        return Array.from({ length: tickCount + 1 }, (_, index) => ({
          ms: Math.round((duration * index) / tickCount),
          percent: (index / tickCount) * 100
        }));
      },


      skillTestReviewEvaluations() {
        return this.skillTestReview?.expectation_evaluations || [];
      },


      skillTestReviewEvaluationFor(expectationId) {
        return this.skillTestReviewEvaluations().find((item) => item.expectation_id === expectationId) || null;
      },


      skillTestReviewStepEvents(eventId) {
        return (this.skillTestReview?.driver_events || []).filter((event) => event.event_id === eventId);
      },


      isSkillTestReviewEventReached(event) {
        return this.skillTestReviewPlayheadMsValue() >= Number(event?.at_ms || 0);
      },


      skillTestReviewStepStatus(step) {
        if (!this.isSkillTestReviewEventReached(step)) {
          return "not_occurred";
        }
        if (this.isSkillTestTimelineExpectationEvent(step)) {
          return this.skillTestReviewEvaluationFor(step.id)?.status || "triggered";
        }
        const events = this.skillTestReviewStepEvents(step.id);
        if (events.some((event) => event.status === "sent")) {
          return "sent";
        }
        return "sent";
      },


      skillTestReviewStepStatusLabel(step) {
        return this.formatStatus(this.skillTestReviewStepStatus(step));
      },


      skillTestReviewAssertionVerdictLabel(event) {
        if (!this.isSkillTestTimelineExpectationEvent(event) || !this.isSkillTestReviewEventReached(event)) {
          return "";
        }
        const status = this.skillTestReviewStepStatus(event);
        if (status === "passed") {
          return "符合预期";
        }
        if (["failed", "rejected"].includes(status)) {
          return "不符合预期";
        }
        if (status === "inconclusive") {
          return "未能判定";
        }
        return "待判定";
      },


      skillTestReviewEventTooltip(event) {
        const parts = [
          `${this.formatSkillTestTimelineMs(event?.at_ms || 0)} · ${this.skillTestTimelineLaneLabel({ id: event?.lane_id })}`,
          this.skillTestTimelineEventLabel(event),
          this.skillTestReviewAssertionVerdictLabel(event) || this.skillTestReviewStepStatusLabel(event)
        ];
        const evaluation = this.isSkillTestTimelineExpectationEvent(event)
          ? this.skillTestReviewEvaluationFor(event.id)
          : null;
        if (evaluation?.reason) {
          parts.push(evaluation.reason);
        }
        return parts.filter(Boolean).join(" | ");
      },


      skillTestReviewEventFrameTone(event) {
        const status = this.skillTestReviewStepStatus(event);
        if (status === "not_occurred") {
          return "border-slate-600/70 bg-slate-900/75 text-slate-200 ring-1 ring-slate-700/60";
        }
        if (status === "passed") {
          return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100 ring-1 ring-emerald-500/20";
        }
        if (status === "failed" || status === "rejected") {
          return "border-rose-500/45 bg-rose-500/10 text-rose-100 ring-1 ring-rose-500/20";
        }
        if (status === "inconclusive") {
          return "border-amber-500/45 bg-amber-500/10 text-amber-100 ring-1 ring-amber-500/20";
        }
        if (this.isSkillTestTimelineExpectationEvent(event)) {
          return "border-sky-500/45 bg-sky-500/10 text-sky-100 ring-1 ring-sky-500/20";
        }
        return "border-orange-500/50 bg-orange-500/10 text-orange-100 ring-1 ring-orange-500/20";
      },


      skillTestReviewEventBadgeTone(event) {
        return this.statusBadgeTone(this.skillTestReviewStepStatus(event));
      },


      skillTestReviewOriginTime() {
        const originValue =
          this.skillTestRun?.time_origin ||
          this.skillTestRun?.started_at ||
          this.skillTestRun?.created_at ||
          this.skillTestReview?.scenario_run?.time_origin ||
          this.skillTestReview?.scenario_run?.started_at ||
          this.skillTestReview?.scenario_run?.created_at;
        const originTime = new Date(originValue || 0).getTime();
        if (Number.isFinite(originTime) && originTime > 0) {
          return originTime;
        }
        const bounds = this.skillTestReviewTimeBounds();
        return bounds.start;
      },


      skillTestReviewTimeBounds() {
        const times = (this.skillTestReview?.replay_timeline || [])
          .map((item) => new Date(item.occurred_at).getTime())
          .filter((value) => Number.isFinite(value));
        if (!times.length) {
          const now = Date.now();
          return { start: now, end: now };
        }
        return { start: Math.min(...times), end: Math.max(...times) };
      },


      skillTestReviewCutoffTime() {
        return this.skillTestReviewOriginTime() + this.skillTestReviewPlayheadMsValue();
      },


      filteredSkillTestReviewTerminalEvents() {
        const events = this.skillTestReview?.replay?.terminal_events || [];
        if (this.skillTestReviewPlayheadMsValue() >= this.skillTestReviewDurationMs()) {
          return events;
        }
        const cutoff = this.skillTestReviewCutoffTime();
        return events.filter((event) => {
          const occurredAt = new Date(event.occurred_at).getTime();
          return !Number.isFinite(occurredAt) || occurredAt <= cutoff;
        });
      },


      updateSkillTestReviewCursor(value) {
        this.skillTestReviewCursor = Number(value);
        this.skillTestReviewAutoFollow = this.skillTestReviewCursor >= 100;
      },


      updateSkillTestReviewPlayhead(value) {
        this.skillTestReviewPlayheadMs = Math.min(this.skillTestReviewDurationMs(), Math.max(0, Number(value || 0)));
        this.skillTestReviewCursor = this.skillTestReviewProgressPercent();
        if (this.skillTestReviewPlayheadMsValue() >= this.skillTestReviewDurationMs()) {
          this.stopSkillTestReviewPlayback();
        }
      },


      startSkillTestReviewPlayback() {
        if (!this.skillTestReview) {
          return;
        }
        this.stopSkillTestReviewPlayback();
        if (this.skillTestReviewPlayheadMsValue() >= this.skillTestReviewDurationMs()) {
          this.updateSkillTestReviewPlayhead(0);
        }
        this.skillTestReviewPlaybackRunning = true;
        this.skillTestReviewPlaybackTimer = window.setInterval(() => {
          this.updateSkillTestReviewPlayhead(this.skillTestReviewPlayheadMsValue() + 1000);
        }, 1000);
      },


      stopSkillTestReviewPlayback() {
        if (this.skillTestReviewPlaybackTimer) {
          window.clearInterval(this.skillTestReviewPlaybackTimer);
          this.skillTestReviewPlaybackTimer = null;
        }
        this.skillTestReviewPlaybackRunning = false;
      },


      toggleSkillTestReviewPlayback() {
        if (this.skillTestReviewPlaybackRunning) {
          this.stopSkillTestReviewPlayback();
        } else {
          this.startSkillTestReviewPlayback();
        }
      },


      restartSkillTestReviewPlayback() {
        this.updateSkillTestReviewPlayhead(0);
        this.startSkillTestReviewPlayback();
      },


      skillTestReviewProgressLabel() {
        return `${this.formatSkillTestTimelineMs(this.skillTestReviewPlayheadMsValue())} / ${this.formatSkillTestTimelineMs(this.skillTestReviewDurationMs())}`;
      },
  };
})();
