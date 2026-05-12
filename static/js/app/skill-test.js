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
        return this.normalizeSkillTestTimelineDraft(
          this.parseSkillTestJsonField("timeline_json", this.defaultSkillTestTimeline(), "时间轴 JSON 必须是对象。")
        );
      },


      parseSkillTestJudgePolicy() {
        return this.parseSkillTestJsonField("judge_policy_json", this.defaultSkillTestJudgePolicy(), "Judge 策略 JSON 必须是对象。");
      },


      isSkillTestExpectationLane(laneId) {
        return laneId === "expected.semantic";
      },


      skillTestEventDefaultsForLane(laneId) {
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


      normalizeSkillTestTimelineDraft(timeline) {
        if (!timeline || typeof timeline !== "object" || Array.isArray(timeline)) {
          return this.defaultSkillTestTimeline();
        }
        const defaults = this.defaultSkillTestTimeline();
        const draft = JSON.parse(JSON.stringify(timeline));
        const durationMs = Math.max(1, Number(draft.duration_ms || this.skillTestCaseForm.duration_ms || defaults.duration_ms));
        const lanes = Array.isArray(draft.lanes) && draft.lanes.length ? draft.lanes : defaults.lanes;
        const events = Array.isArray(draft.events) ? draft.events : [];
        return {
          ...draft,
          schema_version: draft.schema_version || "psop-skill-test-timeline/v1",
          duration_ms: durationMs,
          lanes,
          events: events
            .filter((event) => event && typeof event === "object" && !Array.isArray(event))
            .map((event, index) => {
              const laneId = event.expectation ? "expected.semantic" : event.lane_id || "input.text";
              const defaultsForLane = this.skillTestEventDefaultsForLane(laneId);
              return {
                ...defaultsForLane,
                ...event,
                id: event.id || `event_${index + 1}`,
                lane_id: laneId,
                at_ms: Math.min(durationMs, Math.max(0, Number(event.at_ms || 0))),
                required: event.required !== false
              };
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
          .filter((item) => item.event.lane_id === laneId);
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


      selectSkillTestTimelineEvent(eventId) {
        this.selectedSkillTestTimelineEventId = eventId || "";
      },


      skillTestTimelineSelectedEvent() {
        const events = this.skillTestTimelineEvents();
        const selectedIndex = events.findIndex((event) => event.id === this.selectedSkillTestTimelineEventId);
        if (selectedIndex >= 0) {
          return { event: events[selectedIndex], index: selectedIndex };
        }
        if (events.length > 0) {
          return { event: events[0], index: 0 };
        }
        return null;
      },


      addSkillTestTimelineEventFromLane(pointerEvent, laneId) {
        const timeline = this.skillTestTimelineDraft();
        const atMs = this.skillTestTimelineClickAtMs(pointerEvent);
        const isExpectation = this.isSkillTestExpectationLane(laneId);
        const event = isExpectation
          ? {
              id: `expected_${timeline.events.length + 1}`,
              lane_id: "expected.semantic",
              at_ms: atMs,
              expectation: "描述该时间点以前应满足的语义输出"
            }
          : {
              id: `input_${timeline.events.length + 1}`,
              lane_id: laneId || "input.text",
              at_ms: atMs,
              ...this.skillTestEventDefaultsForLane(laneId || "input.text"),
              payload_inline: laneId === "input.text" ? "填写文本输入" : ""
            };
        timeline.events.push(event);
        this.selectedSkillTestTimelineEventId = event.id;
        this.writeSkillTestTimelineDraft(timeline);
      },


      addSkillTestTimelineInputEvent() {
        const timeline = this.skillTestTimelineDraft();
        const laneId = this.skillTestCaseForm.event_lane_id || "input.text";
        const defaultsForLane = this.skillTestEventDefaultsForLane(laneId);
        const event = {
          id: `input_${timeline.events.length + 1}`,
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
        this.selectedSkillTestTimelineEventId = event.id;
        this.writeSkillTestTimelineDraft(timeline);
      },


      addSkillTestTimelineExpectation() {
        const timeline = this.skillTestTimelineDraft();
        const expectation = this.skillTestCaseForm.expectation_text.trim();
        if (!expectation) {
          this.showNotice("error", "请填写语义期望。");
          return;
        }
        const event = {
          id: `expected_${timeline.events.length + 1}`,
          lane_id: "expected.semantic",
          at_ms: Math.max(0, Number(this.skillTestCaseForm.expectation_at_ms || 0)),
          expectation
        };
        timeline.events.push(event);
        this.skillTestCaseForm.expectation_text = "";
        this.selectedSkillTestTimelineEventId = event.id;
        this.writeSkillTestTimelineDraft(timeline);
      },


      removeSkillTestTimelineEvent(eventIndex) {
        const timeline = this.skillTestTimelineDraft();
        const removedId = timeline.events[eventIndex]?.id;
        timeline.events.splice(eventIndex, 1);
        if (removedId === this.selectedSkillTestTimelineEventId) {
          this.selectedSkillTestTimelineEventId = timeline.events[Math.min(eventIndex, timeline.events.length - 1)]?.id || "";
        }
        this.writeSkillTestTimelineDraft(timeline);
      },


      updateSkillTestTimelineEvent(eventIndex, fieldName, value) {
        const timeline = this.skillTestTimelineDraft();
        if (!timeline.events[eventIndex]) {
          return;
        }
        if (fieldName === "at_ms") {
          timeline.events[eventIndex][fieldName] = Math.min(timeline.duration_ms, Math.max(0, Number(value || 0)));
        } else if (fieldName === "lane_id") {
          const laneId = value || "input.text";
          timeline.events[eventIndex][fieldName] = laneId;
          if (!this.isSkillTestExpectationLane(laneId)) {
            Object.assign(timeline.events[eventIndex], this.skillTestEventDefaultsForLane(laneId));
          }
          if (!this.skillTestTimelineEventUsesAsset(timeline.events[eventIndex])) {
            delete timeline.events[eventIndex].asset_id;
          } else if (
            timeline.events[eventIndex].asset_id &&
            !this.skillTestAssetMatchesLane(this.skillTestAssetById(timeline.events[eventIndex].asset_id), laneId)
          ) {
            delete timeline.events[eventIndex].asset_id;
          }
        } else if (fieldName === "required") {
          timeline.events[eventIndex][fieldName] = Boolean(value);
        } else {
          timeline.events[eventIndex][fieldName] = value;
        }
        this.writeSkillTestTimelineDraft(timeline);
      },


      updateSelectedSkillTestTimelineEvent(fieldName, value) {
        const selected = this.skillTestTimelineSelectedEvent();
        if (!selected) {
          return;
        }
        this.updateSkillTestTimelineEvent(selected.index, fieldName, value);
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


      startSkillTestTimelineEventDrag(pointerEvent, eventIndex) {
        const track = pointerEvent.currentTarget.closest("[data-skill-test-timeline-track]");
        const event = this.skillTestTimelineEvents()[eventIndex];
        const eventId = event?.id || "";
        this.selectSkillTestTimelineEvent(eventId);
        this.setSkillTestTimelineEventAtFromTrack(pointerEvent, eventId, track);
        const moveHandler = (moveEvent) => this.setSkillTestTimelineEventAtFromTrack(moveEvent, eventId, track);
        const upHandler = () => {
          window.removeEventListener("pointermove", moveHandler);
          window.removeEventListener("pointerup", upHandler);
        };
        window.addEventListener("pointermove", moveHandler);
        window.addEventListener("pointerup", upHandler, { once: true });
      },


      skillTestTimelineEventLabel(event) {
        if (event.expectation) {
          return event.expectation;
        }
        if (event.asset_id) {
          const asset = this.skillTestAssetById(event.asset_id);
          if (asset) {
            return this.skillTestAssetLabel(asset);
          }
        }
        if (this.skillTestTimelineEventUsesAsset(event)) {
          return "选择资源";
        }
        if (typeof event.payload_inline === "string") {
          return event.payload_inline || event.event_kind;
        }
        return event.event_kind || event.lane_id;
      },


      skillTestTimelineEventTextValue(event) {
        if (event?.expectation) {
          return event.expectation;
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
        if (!selected || !this.skillTestTimelineEventUsesAsset(selected.event)) {
          this.showNotice("error", "请先选择图片、音频或视频事件。");
          event.target.value = "";
          return;
        }
        const laneId = selected.event.lane_id;
        if (!this.skillTestMimeMatchesLane(file.type || "", laneId)) {
          this.showNotice("error", "文件类型与当前信道不匹配。");
          event.target.value = "";
          return;
        }
        const assetDraft = this.createSkillTestAssetDraftFromFile(file, {
          lane_id: laneId,
          description: this.skillTestTimelineEventTextValue(selected.event)
        });
        this.busy.skillTestData = true;
        try {
          if (this.skillTestCase) {
            const uploaded = await this.uploadSkillTestAssetFile(this.skillTestCase.id, assetDraft);
            this.skillTestDataObjects = [uploaded, ...(this.skillTestDataObjects || []).filter((asset) => asset.id !== uploaded.id)];
            this.updateSelectedSkillTestTimelineEventAsset(uploaded.id);
          } else {
            this.skillTestDataObjects = [assetDraft, ...(this.skillTestDataObjects || [])];
            this.updateSelectedSkillTestTimelineEventAsset(assetDraft.id);
          }
        } catch (error) {
          this.showNotice("error", error.message || "上传并绑定测试资源失败。");
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
        this.busy.skillTestRun = true;
        try {
          const review = await this.apiRequest(`/skill-test-scenario-runs/${scenarioRunId}/review`);
          this.skillTestReview = review;
          this.skillTestCase = review.scenario;
          this.skillTestRun = review.scenario_run;
          this.skillTestRuns = window.PSOPRuntimeEvents.mergeById(this.skillTestRuns, [review.scenario_run]);
          this.skillTestReviewCursor = 100;
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
        const cutoff = this.skillTestReviewCutoffTime();
        const anchors = this.skillTestReview?.cursor_anchors || [];
        const anchor =
          [...anchors]
            .reverse()
            .find((item) => new Date(item.occurred_at).getTime() <= cutoff) || anchors[0] || {};
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
        return ["running", "queued", "pending", "waiting_input"].includes(String(testRun?.status || "").toLowerCase());
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
        return this.skillTestReview?.scenario_timeline?.events || [];
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


      skillTestReviewStepStatus(step) {
        if (step.expectation) {
          return this.skillTestReviewEvaluationFor(step.id)?.status || "pending";
        }
        const events = this.skillTestReviewStepEvents(step.id);
        if (events.some((event) => event.status === "sent")) {
          return "sent";
        }
        return "waiting";
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
        const bounds = this.skillTestReviewTimeBounds();
        const percent = Math.max(0, Math.min(100, Number(this.skillTestReviewCursor || 0))) / 100;
        return bounds.start + (bounds.end - bounds.start) * percent;
      },


      filteredSkillTestReviewTerminalEvents() {
        const cutoff = this.skillTestReviewCutoffTime();
        return (this.skillTestReview?.replay?.terminal_events || []).filter((event) => {
          const occurredAt = new Date(event.occurred_at).getTime();
          return !Number.isFinite(occurredAt) || occurredAt <= cutoff;
        });
      },


      updateSkillTestReviewCursor(value) {
        this.skillTestReviewCursor = Number(value);
        this.skillTestReviewAutoFollow = this.skillTestReviewCursor >= 100;
      },


      skillTestReviewProgressLabel() {
        const cutoff = new Date(this.skillTestReviewCutoffTime());
        return `${Math.round(Number(this.skillTestReviewCursor || 0))}% · ${this.formatDateTime(cutoff.toISOString())}`;
      },
  };
})();
