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
        this.skillTestScenarioDetailPanel = "info";
        this.skillTestScenarioInfoTab = "basic";
        this.selectedSkillTestTimelineLaneId = "";
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
            { id: "sensor.gps", kind: "input", label: "GPS", event_kind: "sensor.gps.reading.v1", mime_type: "application/json" },
            { id: "sensor.pose3d", kind: "input", label: "三轴定位", event_kind: "sensor.pose3d.reading.v1", mime_type: "application/json" },
            { id: "input.text", kind: "input", label: "文本" },
            { id: "input.image", kind: "input", label: "图片" },
            { id: "input.audio", kind: "input", label: "音频" },
            { id: "input.video", kind: "input", label: "视频" },
            { id: "expected.semantic", kind: "output", label: "文本" }
          ],
          events: []
        };
      },


      defaultSkillTestJudgePolicy() {
        return {
          route_key: "text",
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
          throw new Error("请填写文本输出事件的期望内容。");
        }
      },


      parseSkillTestJudgePolicy() {
        return this.parseSkillTestJsonField("judge_policy_json", this.defaultSkillTestJudgePolicy(), "Judge 策略 JSON 必须是对象。");
      },


      isSkillTestExpectationLane(laneId) {
        return laneId === "expected.semantic";
      },

      isSkillTestRuntimeOutputLane(laneId) {
        return laneId === "actual.output";
      },

      isSkillTestSensorLane(laneId) {
        return ["sensor.gps", "sensor.pose3d"].includes(laneId || "");
      },

      isSkillTestTimelineExpectationEvent(event) {
        return this.isSkillTestExpectationLane(event?.lane_id) || Object.prototype.hasOwnProperty.call(event || {}, "expectation");
      },


      skillTestEventDefaultsForLane(laneId) {
        if (this.isSkillTestExpectationLane(laneId)) {
          return {};
        }
        if (laneId === "sensor.gps") {
          return {
            event_kind: "sensor.gps.reading.v1",
            mime_type: "application/json",
            payload_inline: this.defaultSkillTestSensorPayload(laneId)
          };
        }
        if (laneId === "sensor.pose3d") {
          return {
            event_kind: "sensor.pose3d.reading.v1",
            mime_type: "application/json",
            payload_inline: this.defaultSkillTestSensorPayload(laneId)
          };
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

      skillTestSensorFields(laneId) {
        if (laneId === "sensor.gps") {
          return [
            { key: "latitude", label: "纬度", required: true, type: "number" },
            { key: "longitude", label: "经度", required: true, type: "number" },
            { key: "altitude", label: "海拔", required: false, type: "number" },
            { key: "accuracy_m", label: "精度（米）", required: false, type: "number" },
            { key: "timestamp", label: "时间戳", required: false, type: "text" }
          ];
        }
        if (laneId === "sensor.pose3d") {
          return [
            { key: "x", label: "X 坐标", required: true, type: "number" },
            { key: "y", label: "Y 坐标", required: true, type: "number" },
            { key: "z", label: "Z 坐标", required: true, type: "number" },
            { key: "roll", label: "横滚角", required: false, type: "number" },
            { key: "pitch", label: "俯仰角", required: false, type: "number" },
            { key: "yaw", label: "偏航角", required: false, type: "number" },
            { key: "timestamp", label: "时间戳", required: false, type: "text" }
          ];
        }
        return [];
      },

      defaultSkillTestSensorPayload(laneId) {
        if (laneId === "sensor.gps") {
          return { latitude: 0, longitude: 0 };
        }
        if (laneId === "sensor.pose3d") {
          return { x: 0, y: 0, z: 0 };
        }
        return {};
      },

      normalizeSkillTestSensorPayload(laneId, payload) {
        const fields = this.skillTestSensorFields(laneId);
        const raw = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
        const normalized = { ...this.defaultSkillTestSensorPayload(laneId), ...raw };
        fields.forEach((field) => {
          const value = normalized[field.key];
          if (field.type === "number") {
            if (value === "" || value === null || value === undefined) {
              if (field.required) {
                normalized[field.key] = 0;
              } else {
                delete normalized[field.key];
              }
              return;
            }
            const numberValue = Number(value);
            if (Number.isFinite(numberValue)) {
              normalized[field.key] = numberValue;
            } else if (field.required) {
              normalized[field.key] = 0;
            } else {
              delete normalized[field.key];
            }
          } else if (value !== undefined && value !== null && String(value).trim() !== "") {
            normalized[field.key] = String(value);
          } else {
            delete normalized[field.key];
          }
        });
        return normalized;
      },

      skillTestSensorFieldValue(event, fieldName) {
        const payload = event?.payload_inline;
        if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
          return "";
        }
        return payload[fieldName] ?? "";
      },

      skillTestTimelineEventIdPrefix(laneId) {
        if (this.isSkillTestExpectationLane(laneId)) {
          return "expected";
        }
        if (laneId === "sensor.gps") {
          return "gps";
        }
        if (laneId === "sensor.pose3d") {
          return "pose";
        }
        return "input";
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


      normalizeSkillTestTimelineLanes(lanes) {
        const defaults = this.defaultSkillTestTimeline().lanes;
        const defaultIds = defaults.map((lane) => lane.id);
        const rawLanes = Array.isArray(lanes)
          ? lanes.filter((lane) => lane && typeof lane === "object" && !Array.isArray(lane) && lane.id)
          : [];
        const rawById = new Map();
        rawLanes.forEach((lane) => {
          const id = String(lane.id);
          if (!rawById.has(id)) {
            rawById.set(id, { ...lane, id });
          }
        });
        const mergedDefaults = defaults.map((lane) => ({ ...(rawById.get(lane.id) || {}), ...lane }));
        const customLanes = [];
        const seenCustomIds = new Set(defaultIds);
        rawLanes.forEach((lane) => {
          const id = String(lane.id);
          if (seenCustomIds.has(id)) {
            return;
          }
          seenCustomIds.add(id);
          customLanes.push({ ...lane, id });
        });
        return [...mergedDefaults, ...customLanes];
      },


      normalizeSkillTestTimelineDraft(timeline) {
        if (!timeline || typeof timeline !== "object" || Array.isArray(timeline)) {
          return this.defaultSkillTestTimeline();
        }
        const defaults = this.defaultSkillTestTimeline();
        const draft = JSON.parse(JSON.stringify(timeline));
        const durationMs = Math.max(1, Number(draft.duration_ms || this.skillTestCaseForm.duration_ms || defaults.duration_ms));
        const lanes = this.normalizeSkillTestTimelineLanes(draft.lanes);
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
                delete normalized.parts;
                delete normalized.event_kind;
                delete normalized.mime_type;
              } else if (this.isSkillTestSensorLane(laneId)) {
                normalized.payload_inline = this.normalizeSkillTestSensorPayload(laneId, event.payload_inline);
                delete normalized.asset_id;
                delete normalized.parts;
              } else if (Array.isArray(event.parts) && event.parts.length) {
                normalized.parts = this.normalizeSkillTestTimelineParts(event.parts);
                normalized.event_kind = "terminal.multimodal.input.v1";
                normalized.mime_type = "multipart/mixed";
              }
              normalizedEvents.push(normalized);
              return normalized;
            })
            .sort((left, right) => Number(left.at_ms || 0) - Number(right.at_ms || 0))
        };
      },


      normalizeSkillTestTimelineParts(parts) {
        const seenIds = new Set();
        return (parts || [])
          .filter((part) => part && typeof part === "object" && !Array.isArray(part))
          .map((part, index) => {
            const kind = this.normalizeSkillTestTimelinePartKind(part.kind || part.mime_type || "");
            let partId = String(part.part_id || `${kind || "part"}_${index + 1}`).trim();
            if (!partId || seenIds.has(partId)) {
              partId = `part_${index + 1}`;
            }
            seenIds.add(partId);
            return {
              ...part,
              part_id: partId,
              kind,
              mime_type: part.mime_type || (kind === "text" ? "text/plain" : `${kind}/*`)
            };
          })
          .filter((part) => ["text", "image", "video", "audio"].includes(part.kind));
      },


      normalizeSkillTestTimelinePartKind(value) {
        const raw = String(value || "").toLowerCase();
        if (raw === "text" || raw.startsWith("text/")) {
          return "text";
        }
        if (raw === "audio" || raw.startsWith("audio/")) {
          return "audio";
        }
        if (raw === "video" || raw.startsWith("video/")) {
          return "video";
        }
        return "image";
      },


      syncSkillTestTimelineTextPart(parts, text) {
        const normalized = this.normalizeSkillTestTimelineParts(parts);
        const textValue = String(text || "");
        const textIndex = normalized.findIndex((part) => part.kind === "text");
        if (textIndex >= 0) {
          normalized[textIndex] = { ...normalized[textIndex], text: textValue, mime_type: "text/plain" };
          return normalized;
        }
        if (!textValue.trim()) {
          return normalized;
        }
        return [
          {
            part_id: "text_1",
            kind: "text",
            mime_type: "text/plain",
            text: textValue
          },
          ...normalized
        ];
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

      skillTestTimelineSelectedLane() {
        if (!this.selectedSkillTestTimelineLaneId) {
          return null;
        }
        return this.skillTestTimelineLanes().find((lane) => lane.id === this.selectedSkillTestTimelineLaneId) || null;
      },

      skillTestTimelineSelectedLaneEvents() {
        const lane = this.skillTestTimelineSelectedLane();
        return lane ? this.skillTestTimelineEventsForLane(lane.id) : [];
      },

      skillTestTimelineLaneRangeLabel(laneId) {
        const events = this.skillTestTimelineEventsForLane(laneId).map((item) => item.event);
        if (!events.length) {
          return "暂无事件";
        }
        const firstMs = Number(events[0]?.at_ms || 0);
        const lastMs = Number(events[events.length - 1]?.at_ms || 0);
        if (firstMs === lastMs) {
          return `仅 ${this.formatSkillTestTimelineMs(firstMs)}`;
        }
        return `${this.formatSkillTestTimelineMs(firstMs)} - ${this.formatSkillTestTimelineMs(lastMs)}`;
      },

      skillTestTimelineLaneAssetCount(laneId) {
        return this.skillTestTimelineEventsForLane(laneId).filter((item) => item.event.asset_id).length;
      },

      skillTestTimelineSelectedEditorEvent() {
        const selected = this.skillTestTimelineSelectedEvent();
        return selected ? this.skillTestTimelineEditorEvent(selected.event) : {};
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
        if (!laneId || this.isSkillTestRuntimeOutputLane(laneId)) {
          return false;
        }
        if (this.route?.name === "skill-test-scenario-new" || !this.skillTestCase) {
          return !this.skillTestTimelineLaneHasExpandedEvent(laneId);
        }
        return true;
      },

      isSkillTestTimelineLaneSelected(laneId) {
        return Boolean(laneId && this.skillTestScenarioDetailPanel === "lane" && this.selectedSkillTestTimelineLaneId === laneId);
      },

      openSkillTestScenarioInfoPanel() {
        this.selectedSkillTestTimelineLaneId = "";
        this.collapseSkillTestTimelineEventEditor();
        this.skillTestScenarioDetailPanel = "info";
        this.skillTestScenarioInfoTab = this.skillTestScenarioInfoTab || "basic";
      },

      openSkillTestTimelineLaneDetail(laneId) {
        if (!laneId) {
          return;
        }
        this.selectedSkillTestTimelineLaneId = laneId;
        this.selectedSkillTestTimelineEventId = "";
        this.selectedSkillTestTimelineEventIds = [];
        this.skillTestTimelineEventDraft = null;
        this.skillTestScenarioDetailPanel = "lane";
      },


      skillTestTimelineLaneLabel(lane) {
        if (lane?.id === "expected.semantic") {
          return "文本";
        }
        if (lane?.id === "sensor.gps") {
          return "GPS";
        }
        if (lane?.id === "sensor.pose3d") {
          return "三轴";
        }
        if (this.isSkillTestRuntimeOutputLane(lane?.id)) {
          return "真实";
        }
        return lane?.label || lane?.id || "";
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
        if (laneId === "sensor.gps") {
          return "location_on";
        }
        if (laneId === "sensor.pose3d") {
          return "view_in_ar";
        }
        if (laneId === "expected.semantic") {
          return "fact_check";
        }
        if (this.isSkillTestRuntimeOutputLane(laneId)) {
          return "terminal";
        }
        return "text_fields";
      },

      isSkillTestTimelineTriggerEvent(event) {
        if (!event) {
          return false;
        }
        const eventKind = String(event.event_kind || event.kind || event.type || "").toLowerCase();
        if (/(trigger|schedule|timer|clock)/.test(eventKind)) {
          return true;
        }
        return [
          "trigger_event_id",
          "trigger_event",
          "trigger_id",
          "source_event_id",
          "depends_on_event_id",
          "scheduled_by_event_id"
        ].some((key) => {
          const value = event[key];
          if (Array.isArray(value)) {
            return value.length > 0;
          }
          return value !== undefined && value !== null && String(value).trim() !== "";
        });
      },

      skillTestTimelineEventIcon(event) {
        if (this.isSkillTestTimelineTriggerEvent(event)) {
          return "schedule";
        }
        return this.skillTestTimelineLaneIcon(event?.lane_id);
      },


      skillTestTimelineLaneGroup(laneId) {
        return laneId === "expected.semantic" || this.isSkillTestRuntimeOutputLane(laneId) ? "output" : "input";
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
        if (this.isSkillTestRuntimeOutputLane(laneId)) {
          return "border-cyan-500/30 bg-cyan-500/10 text-cyan-200";
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
        if (laneId === "sensor.gps") {
          return "border-lime-500/25 bg-lime-500/10 text-lime-200";
        }
        if (laneId === "sensor.pose3d") {
          return "border-fuchsia-500/25 bg-fuchsia-500/10 text-fuchsia-200";
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
        if (laneId === "sensor.gps") {
          return "border-lime-500/60 bg-slate-950 text-slate-100 ring-1 ring-lime-500/25";
        }
        if (laneId === "sensor.pose3d") {
          return "border-fuchsia-500/60 bg-slate-950 text-slate-100 ring-1 ring-fuchsia-500/25";
        }
        return "border-orange-500/60 bg-slate-950 text-slate-100 ring-1 ring-orange-500/25";
      },


      skillTestTimelineEventUsesAsset(event) {
        return ["input.image", "input.audio", "input.video"].includes(event?.lane_id || "");
      },


      skillTestTimelineEventCanAttachParts(event) {
        return Boolean(event && !this.isSkillTestTimelineExpectationEvent(event) && !this.isSkillTestSensorLane(event.lane_id));
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


      skillTestTimelinePartsAccept() {
        return "image/*,audio/*,video/*";
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


      skillTestTimelineEventAsset(event) {
        if (!event?.asset_id) {
          return null;
        }
        return this.skillTestAssetById(event.asset_id);
      },


      skillTestTimelineEventParts(event) {
        return Array.isArray(event?.parts) ? event.parts : [];
      },


      skillTestTimelineEventHasParts(event) {
        return this.skillTestTimelineEventParts(event).length > 0;
      },


      skillTestTimelinePartAsset(part) {
        if (!part?.asset_id) {
          return null;
        }
        return this.skillTestAssetById(part.asset_id);
      },


      skillTestTimelinePartLabel(part) {
        if (!part) {
          return "";
        }
        if (part.kind === "text") {
          return part.text || "文本";
        }
        const asset = this.skillTestTimelinePartAsset(part);
        const metadata = part.metadata && typeof part.metadata === "object" ? part.metadata : {};
        return asset ? this.skillTestAssetLabel(asset) : metadata.name || metadata.filename || part.asset_id || part.part_id || "素材";
      },


      skillTestTimelinePartMeta(part) {
        if (!part) {
          return "";
        }
        if (part.kind === "text") {
          return "text/plain";
        }
        const asset = this.skillTestTimelinePartAsset(part);
        const values = [part.kind || "", asset?.mime_type || part.mime_type || ""].filter(Boolean);
        return values.join(" · ");
      },


      skillTestTimelinePartIcon(part) {
        if (part?.kind === "audio") {
          return "graphic_eq";
        }
        if (part?.kind === "video") {
          return "movie";
        }
        if (part?.kind === "text") {
          return "text_fields";
        }
        return "image";
      },


      skillTestTimelinePartPreviewUrl(part) {
        const asset = this.skillTestTimelinePartAsset(part);
        if (!asset) {
          return "";
        }
        return this.skillTestAssetContentUrl(asset);
      },


      removeSkillTestTimelineEventPart(partId) {
        const draft = this.ensureSkillTestTimelineEventDraft();
        if (!draft || !Array.isArray(draft.parts)) {
          return;
        }
        this.updateSkillTestTimelineEventDraft("parts", draft.parts.filter((part) => part.part_id !== partId));
      },


      nextSkillTestTimelinePartId(parts, kind) {
        const prefix = kind || "part";
        const usedIds = new Set((parts || []).map((part) => String(part?.part_id || "")).filter(Boolean));
        let index = 1;
        while (usedIds.has(`${prefix}_${index}`)) {
          index += 1;
        }
        return `${prefix}_${index}`;
      },


      skillTestTimelineEventPartsFromDraft(event) {
        const parts = this.normalizeSkillTestTimelineParts(event?.parts || []);
        if (!parts.some((part) => part.kind === "text")) {
          const text = typeof event?.payload_inline === "string" ? event.payload_inline : "";
          if (text.trim()) {
            parts.unshift({
              part_id: "text_1",
              kind: "text",
              mime_type: "text/plain",
              text
            });
          }
        }
        if (event?.asset_id && !parts.some((part) => part.asset_id === event.asset_id)) {
          const asset = this.skillTestTimelineEventAsset(event);
          const kind = this.normalizeSkillTestTimelinePartKind(asset?.mime_type || event.mime_type || "");
          parts.push({
            part_id: this.nextSkillTestTimelinePartId(parts, kind),
            kind,
            mime_type: asset?.mime_type || event.mime_type || `${kind}/*`,
            asset_id: event.asset_id
          });
        }
        return parts;
      },


      appendSkillTestTimelineEventAssetPart(asset) {
        const draft = this.ensureSkillTestTimelineEventDraft();
        if (!draft || !asset) {
          return;
        }
        const parts = this.skillTestTimelineEventPartsFromDraft(draft);
        const kind = this.normalizeSkillTestTimelinePartKind(asset.mime_type || "");
        const nextPart = {
          part_id: this.nextSkillTestTimelinePartId(parts, kind),
          kind,
          mime_type: asset.mime_type || `${kind}/*`,
          asset_id: asset.id
        };
        this.skillTestTimelineEventDraft = {
          ...draft,
          asset_id: "",
          event_kind: "terminal.multimodal.input.v1",
          mime_type: "multipart/mixed",
          parts: this.normalizeSkillTestTimelineParts([...parts, nextPart])
        };
      },


      skillTestAssetContentUrl(asset) {
        if (!asset) {
          return "";
        }
        if (asset.preview_url) {
          return asset.preview_url;
        }
        if (asset.is_local) {
          return "";
        }
        const skillId = this.currentSkill?.id || this.skillTestCase?.skill_definition_id || asset.skill_definition_id || "";
        const scenarioId = this.skillTestCase?.id || asset.scenario_id || "";
        if (!skillId || !scenarioId || !asset.id) {
          return "";
        }
        return `${this.apiBaseUrl}/skills/${encodeURIComponent(skillId)}/test-scenarios/${encodeURIComponent(scenarioId)}/assets/${encodeURIComponent(asset.id)}/content`;
      },


      skillTestTimelineAssetPreviewKind(event) {
        const laneId = event?.lane_id || "";
        const asset = this.skillTestTimelineEventAsset(event);
        const mimeType = asset?.mime_type || event?.mime_type || "";
        if (mimeType.startsWith("image/") || laneId === "input.image") {
          return "image";
        }
        if (mimeType.startsWith("audio/") || laneId === "input.audio") {
          return "audio";
        }
        if (mimeType.startsWith("video/") || laneId === "input.video") {
          return "video";
        }
        return "";
      },


      skillTestTimelineAssetPreviewUrl(event) {
        const asset = this.skillTestTimelineEventAsset(event);
        return this.skillTestAssetContentUrl(asset);
      },


      skillTestTimelineAssetPreviewMeta(event) {
        const asset = this.skillTestTimelineEventAsset(event);
        if (!asset) {
          return "";
        }
        const parts = [];
        if (asset.mime_type) {
          parts.push(asset.mime_type);
        }
        if (asset.size_bytes) {
          parts.push(this.formatBytes(asset.size_bytes));
        }
        return parts.join(" · ");
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
        this.selectedSkillTestTimelineLaneId = "";
        this.skillTestTimelineEventDraft = null;
        if (eventId) {
          this.skillTestScenarioDetailPanel = "event";
          this.ensureSkillTestTimelineEventDraft();
        } else if (this.skillTestScenarioDetailPanel === "event") {
          this.skillTestScenarioDetailPanel = "info";
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
          if (this.skillTestScenarioDetailPanel === "event") {
            this.skillTestScenarioDetailPanel = "info";
          }
        }
      },

      replaceSkillTestTimelineEventSelection(eventId) {
        this.selectedSkillTestTimelineEventIds = eventId ? [eventId] : [];
      },

      skillTestTimelineSelectedEventIdSet() {
        return new Set([
          ...(this.selectedSkillTestTimelineEventIds || []),
          this.selectedSkillTestTimelineEventId
        ].filter(Boolean));
      },

      hasSelectedSkillTestTimelineEvents() {
        const selectedIds = this.skillTestTimelineSelectedEventIdSet();
        if (!selectedIds.size) {
          return false;
        }
        return this.skillTestTimelineEvents().some((event) => selectedIds.has(event.id));
      },

      isSkillTestTimelineShortcutEditableTarget(target) {
        const element = target?.nodeType === 1 ? target : target?.parentElement;
        if (!element) {
          return false;
        }
        const tagName = String(element.tagName || "").toLowerCase();
        if (["input", "textarea", "select"].includes(tagName)) {
          return true;
        }
        if (typeof element.closest === "function") {
          return Boolean(element.closest("[contenteditable]"));
        }
        return false;
      },

      shouldHandleSkillTestTimelineBackspaceShortcut(keyboardEvent) {
        if (!keyboardEvent || keyboardEvent.defaultPrevented || keyboardEvent.key !== "Backspace") {
          return false;
        }
        if (keyboardEvent.altKey || keyboardEvent.ctrlKey || keyboardEvent.metaKey) {
          return false;
        }
        if (!["skill-test-scenario", "skill-test-scenario-new"].includes(this.route?.name)) {
          return false;
        }
        if (this.isSkillTestTimelineShortcutEditableTarget(keyboardEvent.target)) {
          return false;
        }
        return this.hasSelectedSkillTestTimelineEvents();
      },

      confirmSkillTestTimelineEventDeletion() {
        if (typeof this.confirmDangerAction !== "function") {
          return true;
        }
        return this.confirmDangerAction("确认删除事件？此操作可能无法撤销。");
      },

      handleSkillTestTimelineKeyboardShortcut(keyboardEvent) {
        if (!this.shouldHandleSkillTestTimelineBackspaceShortcut(keyboardEvent)) {
          return false;
        }
        keyboardEvent.preventDefault?.();
        keyboardEvent.stopPropagation?.();
        if (!this.confirmSkillTestTimelineEventDeletion()) {
          return false;
        }
        return this.removeSelectedSkillTestTimelineEvents();
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
        if (this.skillTestScenarioDetailPanel === "event") {
          this.skillTestScenarioDetailPanel = "info";
        }
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
              expectation: "描述该时间点以前应满足的文本输出"
            }
          : {
              id: this.nextSkillTestTimelineEventId(timeline.events, this.skillTestTimelineEventIdPrefix(laneId || "input.text")),
              lane_id: laneId || "input.text",
              at_ms: atMs,
              ...this.skillTestEventDefaultsForLane(laneId || "input.text"),
              payload_inline: this.isSkillTestSensorLane(laneId)
                ? this.defaultSkillTestSensorPayload(laneId)
                : laneId === "input.text"
                  ? "填写文本输入"
                  : ""
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
          id: this.nextSkillTestTimelineEventId(timeline.events, this.skillTestTimelineEventIdPrefix(laneId)),
          lane_id: laneId,
          at_ms: Math.max(0, Number(this.skillTestCaseForm.event_at_ms || 0)),
          ...defaultsForLane,
          payload_inline: this.isSkillTestSensorLane(laneId)
            ? this.defaultSkillTestSensorPayload(laneId)
            : this.skillTestCaseForm.event_payload_inline || ""
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
          if (this.skillTestScenarioDetailPanel === "event") {
            this.skillTestScenarioDetailPanel = "info";
          }
        }
        this.selectedSkillTestTimelineEventIds = (this.selectedSkillTestTimelineEventIds || []).filter((eventId) => eventId !== removedId);
        this.writeSkillTestTimelineDraft(timeline);
      },

      applySkillTestTimelineEventField(event, durationMs, fieldName, value) {
        if (fieldName === "at_ms") {
          event[fieldName] = Math.min(durationMs, Math.max(0, Number(value || 0)));
        } else if (fieldName === "lane_id") {
          return;
        } else if (fieldName === "required") {
          event[fieldName] = Boolean(value);
        } else if (fieldName === "payload_inline" && Array.isArray(event.parts)) {
          event[fieldName] = value;
          event.parts = this.syncSkillTestTimelineTextPart(event.parts, value);
          event.event_kind = "terminal.multimodal.input.v1";
          event.mime_type = "multipart/mixed";
        } else if (fieldName === "parts") {
          event.parts = this.normalizeSkillTestTimelineParts(value);
          event.event_kind = "terminal.multimodal.input.v1";
          event.mime_type = "multipart/mixed";
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

      updateSkillTestTimelineSensorField(fieldName, value) {
        const draft = this.ensureSkillTestTimelineEventDraft();
        if (!draft || !this.isSkillTestSensorLane(draft.lane_id)) {
          return;
        }
        const payload = this.normalizeSkillTestSensorPayload(draft.lane_id, draft.payload_inline);
        const field = this.skillTestSensorFields(draft.lane_id).find((item) => item.key === fieldName);
        if (!field) {
          return;
        }
        if (field.type === "number") {
          if (value === "" && !field.required) {
            delete payload[fieldName];
          } else {
            const numberValue = Number(value);
            payload[fieldName] = Number.isFinite(numberValue) ? numberValue : 0;
          }
        } else if (value === "") {
          delete payload[fieldName];
        } else {
          payload[fieldName] = String(value);
        }
        this.skillTestTimelineEventDraft = { ...draft, payload_inline: payload };
      },

      flushSkillTestTimelineEventDraft() {
        const draft = this.skillTestTimelineEventDraft;
        if (!draft?.id) {
          return false;
        }
        const timeline = this.skillTestTimelineDraft();
        const eventIndex = timeline.events.findIndex((event) => event.id === draft.id);
        if (eventIndex >= 0) {
          const currentEvent = timeline.events[eventIndex];
          timeline.events[eventIndex] = { ...currentEvent, ...draft, lane_id: currentEvent.lane_id };
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
        return this.removeSelectedSkillTestTimelineEvents();
      },

      removeSelectedSkillTestTimelineEvents() {
        const selectedIds = this.skillTestTimelineSelectedEventIdSet();
        if (!selectedIds.size) {
          return false;
        }
        const timeline = this.skillTestTimelineDraft();
        const nextEvents = (timeline.events || []).filter((event) => !selectedIds.has(event.id));
        if (nextEvents.length === (timeline.events || []).length) {
          return false;
        }
        timeline.events = nextEvents;
        this.selectedSkillTestTimelineEventId = "";
        this.selectedSkillTestTimelineEventIds = [];
        this.skillTestTimelineEventDraft = null;
        this.skillTestTimelineDragState = null;
        if (this.skillTestScenarioDetailPanel === "event") {
          this.skillTestScenarioDetailPanel = "info";
        }
        this.writeSkillTestTimelineDraft(timeline);
        return true;
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
        if (this.isSkillTestRuntimeOutputLane(event?.lane_id)) {
          return this.skillTestReviewRuntimeOutputLabel(event);
        }
        if (this.isSkillTestSensorLane(event?.lane_id)) {
          return this.skillTestSensorEventLabel(event);
        }
        if (this.skillTestTimelineEventHasParts(event)) {
          return this.skillTestTimelineEventParts(event)
            .map((part) => this.skillTestTimelinePartLabel(part))
            .filter(Boolean)
            .join(" + ");
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
        if (this.isSkillTestSensorLane(event?.lane_id)) {
          return this.formatSkillTestReviewDebugJson(event?.payload_inline || {});
        }
        if (this.skillTestTimelineEventHasParts(event)) {
          const textPart = this.skillTestTimelineEventParts(event).find((part) => part.kind === "text");
          if (textPart) {
            return textPart.text || "";
          }
        }
        const payload = event?.payload_inline;
        if (typeof payload === "string") {
          return payload;
        }
        if (payload && typeof payload === "object") {
          return payload.description || "";
        }
        return "";
      },

      skillTestSensorEventLabel(event) {
        const payload = this.normalizeSkillTestSensorPayload(event?.lane_id, event?.payload_inline);
        if (event?.lane_id === "sensor.gps") {
          return `${payload.latitude}, ${payload.longitude}`;
        }
        if (event?.lane_id === "sensor.pose3d") {
          return `x ${payload.x} / y ${payload.y} / z ${payload.z}`;
        }
        return "传感器读数";
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
          if (Array.isArray(next.parts)) {
            next.parts = next.parts.map((part) => {
              const nextPart = { ...part };
              if (nextPart.asset_id && assetIdMap[nextPart.asset_id]) {
                nextPart.asset_id = assetIdMap[nextPart.asset_id];
              } else if (options.removeLocalAssetIds && typeof nextPart.asset_id === "string" && nextPart.asset_id.startsWith("local_")) {
                delete nextPart.asset_id;
              }
              return nextPart;
            });
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
        const previewUrl = typeof URL !== "undefined" && typeof URL.createObjectURL === "function"
          ? URL.createObjectURL(file)
          : "";
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
          preview_url: previewUrl,
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
        if (!selected || !this.skillTestTimelineEventCanAttachParts(editorEvent)) {
          this.showNotice("error", "请先选择输入事件。");
          event.target.value = "";
          return;
        }
        const laneId = editorEvent.lane_id;
        const inferredLaneId = this.inferSkillTestLaneForMime(file.type || "");
        const acceptsAnyMedia = laneId === "input.text" || this.skillTestTimelineEventHasParts(editorEvent);
        if (!acceptsAnyMedia && !this.skillTestMimeMatchesLane(file.type || "", laneId)) {
          this.showNotice("error", "文件类型与当前信道不匹配。");
          event.target.value = "";
          return;
        }
        const assetDraft = this.createSkillTestAssetDraftFromFile(file, {
          lane_id: acceptsAnyMedia ? inferredLaneId : laneId,
          description: this.skillTestTimelineEventTextValue(editorEvent)
        });
        this.busy.skillTestData = true;
        try {
          if (this.skillTestCase) {
            const uploaded = await this.uploadSkillTestAssetFile(this.skillTestCase.id, assetDraft);
            this.skillTestDataObjects = [uploaded, ...(this.skillTestDataObjects || []).filter((asset) => asset.id !== uploaded.id)];
            this.appendSkillTestTimelineEventAssetPart(uploaded);
          } else {
            this.skillTestDataObjects = [assetDraft, ...(this.skillTestDataObjects || [])];
            this.appendSkillTestTimelineEventAssetPart(assetDraft);
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
        this.stopSkillTestReviewPolling();
        this.busy.skillTestRun = true;
        try {
          const review = await this.apiRequest(`/skill-test-scenario-runs/${scenarioRunId}/review`);
          this.applySkillTestReviewPayload(review);
          this.skillTestReviewPlayheadMs = 0;
          this.skillTestReviewCursor = 0;
          this.skillTestReviewAutoFollow = true;
          this.skillTestReviewPanelTab = "transcript";
          this.skillTestReviewDetailTab = "transcript";
          this.selectedSkillTestReviewExpectationId = "";
          this.skillTestReviewExpandedEventKey = "";
          this.selectedSkillTestReviewLaneId = "";
          await this.loadSkillTestCaseDetail(skillId, scenarioId);
          this.applySkillTestReviewPayload(review);
          this.applySkillTestReviewInitialPlayhead(review);
          this.startSkillTestReviewPolling(scenarioRunId);
        } finally {
          this.busy.skillTestRun = false;
        }
      },


      applySkillTestReviewPayload(review) {
        if (!review) {
          return;
        }
        const previousPlayhead = this.skillTestReviewPlayheadMsValue();
        this.skillTestReview = review;
        this.skillTestCase = review.scenario;
        this.skillTestRun = review.scenario_run;
        this.skillTestRuns = window.PSOPRuntimeEvents.mergeById(this.skillTestRuns || [], [review.scenario_run]);
        if (review.replay?.run) {
          this.liveRun = review.replay.run;
          this.liveRunTerminalEvents = window.PSOPRuntimeEvents.mergeBySeq([], review.replay.terminal_events || []);
          this.liveRunTraceEvents = window.PSOPRuntimeEvents.mergeBySeq([], review.replay.trace_events || []);
          this.liveRunBindings = window.PSOPRuntimeEvents.mergeById([], review.replay.bindings || []);
        }
        if (
          this.selectedSkillTestReviewExpectationId &&
          !this.skillTestReviewTimelineEvents().some((event) => event.id === this.selectedSkillTestReviewExpectationId)
        ) {
          this.selectedSkillTestReviewExpectationId = "";
        }
        if (this.skillTestReviewExpandedEventKey && !this.skillTestReviewEventByKey(this.skillTestReviewExpandedEventKey)) {
          this.skillTestReviewExpandedEventKey = "";
          this.skillTestReviewDetailTab = "transcript";
        }
        if (
          this.selectedSkillTestReviewLaneId &&
          !this.skillTestReviewTimelineLanes().some((lane) => lane.id === this.selectedSkillTestReviewLaneId)
        ) {
          this.selectedSkillTestReviewLaneId = "";
          this.skillTestReviewDetailTab = "transcript";
        }
        this.skillTestReviewPlayheadMs = Math.min(this.skillTestReviewDurationMs(), Math.max(0, Number(previousPlayhead || 0)));
        this.skillTestReviewCursor = this.skillTestReviewProgressPercent();
      },


      async refreshSkillTestRunReview(scenarioRunId = this.skillTestReviewPollRunId, options = {}) {
        if (!scenarioRunId) {
          return null;
        }
        if (!options.force && this.route?.name && this.route.name !== "skill-test-scenario-review") {
          this.stopSkillTestReviewPolling();
          return null;
        }
        const review = await this.apiRequest(`/skill-test-scenario-runs/${scenarioRunId}/review`);
        this.applySkillTestReviewPayload(review);
        if (!this.shouldPollSkillTestRunReview(review)) {
          this.stopSkillTestReviewPolling();
        }
        return review;
      },


      startSkillTestReviewPolling(scenarioRunId = this.skillTestRun?.id) {
        this.stopSkillTestReviewPolling();
        if (!scenarioRunId || !this.shouldPollSkillTestRunReview(this.skillTestReview)) {
          return;
        }
        this.skillTestReviewPollRunId = scenarioRunId;
        this.skillTestReviewPollTimer = window.setInterval(async () => {
          try {
            await this.refreshSkillTestRunReview(scenarioRunId);
          } catch {
            // A later tick or route reload can recover the review snapshot.
          }
        }, 2000);
      },


      stopSkillTestReviewPolling() {
        if (this.skillTestReviewPollTimer) {
          window.clearInterval(this.skillTestReviewPollTimer);
          this.skillTestReviewPollTimer = null;
        }
        this.skillTestReviewPollRunId = "";
      },


      shouldPollSkillTestRunReview(review = this.skillTestReview) {
        if (!review) {
          return false;
        }
        return this.isOpenSkillTestRun(review.scenario_run || this.skillTestRun) || this.hasPendingSkillTestReviewJudgement(review);
      },


      hasPendingSkillTestReviewJudgement(review = this.skillTestReview) {
        const expectations = this.skillTestReviewExpectationEvents(review);
        if (!expectations.length) {
          return false;
        }
        const evaluatedIds = new Set((review?.expectation_evaluations || []).map((item) => item.expectation_id));
        return expectations.some((event) => event?.id && !evaluatedIds.has(event.id));
      },


      skillTestReviewExpectationEvents(review = this.skillTestReview) {
        const timeline = this.normalizeSkillTestTimelineDraft(
          review?.scenario_timeline ||
            review?.scenario_run?.timeline ||
            this.skillTestRun?.timeline ||
            this.skillTestCase?.timeline ||
            this.defaultSkillTestTimeline()
        );
        return (timeline.events || []).filter((event) => this.isSkillTestTimelineExpectationEvent(event));
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
          if (this.skillTestReview?.scenario_run?.id === this.skillTestRun.id) {
            await this.refreshSkillTestRunReview(this.skillTestRun.id, { force: true });
          }
        } finally {
          this.busy.skillTestEvaluate = false;
        }
      },


      async cancelSkillTestRun() {
        if (!this.skillTestRun || !this.isOpenSkillTestRun(this.skillTestRun)) {
          return;
        }
        if (!this.confirmDangerAction("确认终止当前测试运行？终止后不会继续发送时间轴事件。")) {
          return;
        }
        this.busy.skillTestCancel = true;
        try {
          this.stopSkillTestReviewPlayback();
          const cancelled = await this.apiRequest(`/skill-test-scenario-runs/${this.skillTestRun.id}/cancel`, {
            method: "POST",
            body: JSON.stringify({ reason: "用户终止测试" })
          });
          this.skillTestRun = cancelled;
          this.skillTestRuns = window.PSOPRuntimeEvents.mergeById(this.skillTestRuns || [], [cancelled]);
          if (this.skillTestReview?.scenario_run?.id === cancelled.id) {
            await this.refreshSkillTestRunReview(cancelled.id, { force: true });
          }
          this.stopSkillTestReviewPolling();
          this.showNotice("success", "已终止测试运行。");
        } catch (error) {
          this.showNotice("error", error.message || "终止测试运行失败。");
        } finally {
          this.busy.skillTestCancel = false;
        }
      },


      currentSkillTestForkCursor() {
        const stageOutput = this.selectedSkillTestStageOutput();
        if (stageOutput?.cursor) {
          return {
            time_ms: Math.max(0, Number(stageOutput.cursor.time_ms || 0)),
            terminal_seq: Math.max(0, Number(stageOutput.cursor.terminal_seq || 0)),
            snapshot_seq: Math.max(0, Number(stageOutput.cursor.snapshot_seq || 0))
          };
        }
        const cutoffMs = this.skillTestReviewPlayheadMsValue();
        const anchors = this.skillTestReview?.cursor_anchors || [];
        const anchor =
          [...anchors]
            .reverse()
            .find((item) => Number(item.time_ms || 0) <= cutoffMs) || {};
        return {
          time_ms: Math.max(0, Number(cutoffMs || 0)),
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
        const lanes = (timeline.lanes || []).filter((lane) => !this.isSkillTestRuntimeOutputLane(lane.id));
        return lanes
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
        if (this.isSkillTestRuntimeOutputLane(laneId)) {
          return [];
        }
        return this.skillTestReviewTimelineEvents()
          .map((event, index) => ({ event, index }))
          .filter((item) => item.event.lane_id === laneId)
          .map((item) => ({
            ...item,
            render_key: `review:${item.event.lane_id}:${item.event.id}:${item.index}`
          }));
      },


      skillTestReviewSelectedLane() {
        if (!this.selectedSkillTestReviewLaneId) {
          return null;
        }
        return this.skillTestReviewTimelineLanes().find((lane) => lane.id === this.selectedSkillTestReviewLaneId) || null;
      },


      skillTestReviewSelectedLaneEvents() {
        const lane = this.skillTestReviewSelectedLane();
        return lane ? this.skillTestReviewEventsForLane(lane.id) : [];
      },


      skillTestReviewLaneRangeLabel(laneId) {
        const events = this.skillTestReviewEventsForLane(laneId).map((item) => item.event);
        if (!events.length) {
          return "暂无事件";
        }
        const firstMs = Number(events[0]?.at_ms || 0);
        const lastMs = Number(events[events.length - 1]?.at_ms || 0);
        if (firstMs === lastMs) {
          return `仅 ${this.formatSkillTestTimelineMs(firstMs)}`;
        }
        return `${this.formatSkillTestTimelineMs(firstMs)} - ${this.formatSkillTestTimelineMs(lastMs)}`;
      },


      skillTestReviewLaneReachedCount(laneId) {
        return this.skillTestReviewEventsForLane(laneId).filter((item) => this.isSkillTestReviewEventReached(item.event)).length;
      },


      skillTestReviewLaneRuntimeOutputCount(laneId) {
        if (!this.isSkillTestExpectationLane(laneId)) {
          return 0;
        }
        return this.skillTestReviewEventsForLane(laneId).reduce(
          (count, item) => count + this.skillTestReviewRuntimeOutputsForExpectation(item.event).length,
          0
        );
      },


      skillTestReviewRuntimeOutputEvents() {
        return (this.skillTestReview?.replay?.terminal_events || [])
          .filter((event) => event?.direction === "output")
          .map((event, index) => this.skillTestReviewRuntimeOutputEvent(event, index))
          .sort((left, right) => Number(left.at_ms || 0) - Number(right.at_ms || 0));
      },


      skillTestReviewRuntimeOutputEvent(event, index) {
        const eventId = event?.id || event?.seq_no || index;
        return {
          id: `runtime_output_${eventId}`,
          lane_id: "actual.output",
          at_ms: this.skillTestReviewRuntimeEventAtMs(event),
          event_kind: event?.event_kind || "terminal.output",
          mime_type: event?.mime_type || "",
          payload_inline: this.skillTestReviewRuntimeOutputLabel(event),
          seq_no: event?.seq_no,
          occurred_at: event?.occurred_at,
          direction: "output",
          required: false,
          terminal_event: event
        };
      },


      skillTestReviewSemanticExpectationEvents() {
        return this.skillTestReviewTimelineEvents()
          .filter((event) => this.isSkillTestTimelineExpectationEvent(event))
          .slice()
          .sort((left, right) => Number(left.at_ms || 0) - Number(right.at_ms || 0));
      },


      skillTestReviewRuntimeOutputsForExpectation(expectationEvent) {
        if (!this.isSkillTestTimelineExpectationEvent(expectationEvent)) {
          return [];
        }
        const stageOutput = this.skillTestReviewStageOutputForEvent(expectationEvent);
        if (stageOutput?.actual_outputs?.length) {
          return stageOutput.actual_outputs.map((output, index) => ({
            id: output.id || `stage_output_${expectationEvent.id}_${index}`,
            lane_id: "actual.output",
            at_ms: Number(output.at_ms || 0),
            event_kind: output.event_kind || "terminal.output",
            mime_type: output.mime_type || "",
            payload_inline: output.payload_inline,
            seq_no: output.seq_no,
            occurred_at: output.occurred_at,
            direction: "output",
            required: false,
            terminal_event: output
          }));
        }
        const expectations = this.skillTestReviewSemanticExpectationEvents();
        const targetIndex = expectations.findIndex((event) => event.id === expectationEvent.id);
        if (targetIndex < 0) {
          return [];
        }
        return this.skillTestReviewRuntimeOutputEvents().filter((outputEvent) => {
          const outputMs = Number(outputEvent.at_ms || 0);
          const bindingIndex = expectations.findIndex((event) => outputMs <= Number(event.at_ms || 0));
          return bindingIndex === targetIndex;
        });
      },


      skillTestReviewRuntimeEventAtMs(event) {
        const origin = this.skillTestReviewOriginTime();
        const occurredAt = new Date(event?.occurred_at).getTime();
        if (!Number.isFinite(origin) || origin <= 0 || !Number.isFinite(occurredAt)) {
          return 0;
        }
        return Math.min(this.skillTestReviewDurationMs(), Math.max(0, occurredAt - origin));
      },


      skillTestReviewRuntimeOutputLabel(event) {
        const sourceEvent = event?.terminal_event || event;
        const parts = [];
        if (sourceEvent?.artifact_object_id) {
          parts.push(`artifact_object_id: ${sourceEvent.artifact_object_id}`);
        }
        if (sourceEvent?.mime_type && sourceEvent.mime_type !== "text/plain") {
          parts.push(`mime_type: ${sourceEvent.mime_type}`);
        }
        const payload = sourceEvent?.payload_inline ?? event?.payload_inline;
        if (typeof payload === "string") {
          if (payload) {
            parts.push(payload);
          }
        } else if (payload !== null && payload !== undefined) {
          try {
            parts.push(JSON.stringify(payload, null, 2));
          } catch {
            parts.push(String(payload));
          }
        }
        return parts.join("\n") || sourceEvent?.event_kind || event?.event_kind || "Runtime output";
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
        return `left: clamp(4rem, ${this.skillTestReviewTimelineEventPercent(event)}%, calc(100% - 4rem))`;
      },


      skillTestReviewLaneProgressStyle() {
        return `width: ${this.skillTestReviewProgressPercent()}%`;
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

      skillTestReviewStageOutputs() {
        return this.skillTestReview?.stage_outputs || [];
      },

      skillTestReviewStageOutputFor(stageId) {
        return this.skillTestReviewStageOutputs().find((item) => item.stage_id === stageId || item.event_id === stageId) || null;
      },

      skillTestReviewStageOutputForEvent(event) {
        if (!this.isSkillTestTimelineExpectationEvent(event)) {
          return null;
        }
        return this.skillTestReviewStageOutputFor(event.id);
      },

      selectedSkillTestStageOutput() {
        const expandedEvent = this.skillTestReviewExpandedEvent();
        if (this.isSkillTestTimelineExpectationEvent(expandedEvent)) {
          return this.skillTestReviewStageOutputForEvent(expandedEvent);
        }
        return this.skillTestReviewStageOutputForEvent(this.selectedSkillTestReviewExpectationEvent());
      },


      selectSkillTestReviewEvent(event) {
        if (!this.isSkillTestTimelineExpectationEvent(event)) {
          return;
        }
        this.selectedSkillTestReviewExpectationId = event.id;
      },


      isSkillTestReviewLaneSelected(laneId) {
        return Boolean(laneId && this.selectedSkillTestReviewLaneId === laneId);
      },


      openSkillTestReviewLaneDetail(laneId) {
        if (!laneId) {
          return;
        }
        this.selectedSkillTestReviewLaneId = laneId;
        this.skillTestReviewExpandedEventKey = "";
        this.skillTestReviewDetailTab = "events";
      },


      closeSkillTestReviewLaneDetail() {
        this.selectedSkillTestReviewLaneId = "";
        this.skillTestReviewDetailTab = "transcript";
      },


      openSkillTestReviewEvent(event) {
        if (!event) {
          return;
        }
        this.selectedSkillTestReviewLaneId = "";
        if (this.isSkillTestTimelineExpectationEvent(event)) {
          this.selectSkillTestReviewEvent(event);
        } else {
          this.selectedSkillTestReviewExpectationId = "";
        }
        this.skillTestReviewExpandedEventKey = this.skillTestReviewEventKey(event);
        this.skillTestReviewDetailTab = "content";
      },


      closeSkillTestReviewEvent() {
        this.skillTestReviewExpandedEventKey = "";
        this.skillTestReviewDetailTab = "transcript";
      },


      skillTestReviewEventKey(event) {
        if (!event) {
          return "";
        }
        const eventId = event.id ?? event.seq_no ?? "";
        return `${event.lane_id || ""}:${String(eventId)}`;
      },


      skillTestReviewEventItems() {
        return this.skillTestReviewTimelineLanes().flatMap((lane) => this.skillTestReviewEventsForLane(lane.id));
      },


      skillTestReviewEventByKey(eventKey) {
        if (!eventKey) {
          return null;
        }
        const item = this.skillTestReviewEventItems().find((entry) => this.skillTestReviewEventKey(entry.event) === eventKey);
        return item?.event || null;
      },


      skillTestReviewExpandedEvent() {
        return this.skillTestReviewEventByKey(this.skillTestReviewExpandedEventKey);
      },


      skillTestReviewEventTitle(event) {
        return this.skillTestTimelineEventLabel(event) || event?.event_kind || event?.id || "Review event";
      },


      skillTestReviewEventContentSections(event) {
        if (!event) {
          return [];
        }
        if (this.isSkillTestTimelineExpectationEvent(event)) {
          const stageOutput = this.skillTestReviewStageOutputForEvent(event);
          const sections = [
            {
              id: `${event.id || "expectation"}:expectation`,
              title: "阶段期望",
              meta: this.formatSkillTestTimelineMs(event.at_ms || 0),
              content: event.expectation || "暂无语义期望。"
            }
          ];
          if (stageOutput) {
            const cursor = stageOutput.cursor || {};
            sections.push({
              id: `${event.id || "expectation"}:slice`,
              title: "阶段切面",
              meta: this.formatSkillTestTimelineMs(stageOutput.time_ms ?? event.at_ms ?? 0),
              content: [
                `切面时间: ${this.formatSkillTestTimelineMs(stageOutput.time_ms ?? event.at_ms ?? 0)}`,
                `Terminal Seq: ${cursor.terminal_seq === undefined || cursor.terminal_seq === null ? 0 : cursor.terminal_seq}`,
                `Snapshot Seq: ${cursor.snapshot_seq === undefined || cursor.snapshot_seq === null ? 0 : cursor.snapshot_seq}`
              ].join("\n")
            });
          }
          sections.push(
            ...this.skillTestReviewRuntimeOutputsForExpectation(event).map((outputEvent, index) => ({
              id: outputEvent.id || `${event.id || "expectation"}:runtime:${index}`,
              title: `真实输出 #${index + 1}`,
              meta: this.formatSkillTestTimelineMs(outputEvent.at_ms || 0),
              content: this.skillTestReviewRuntimeOutputLabel(outputEvent)
            }))
          );
          if (stageOutput) {
            sections.push({
              id: `${event.id || "expectation"}:judge`,
              title: "智能体判定",
              meta: stageOutput.judge_result?.status || "pending",
              content: stageOutput.judge_result?.reason || "暂无 Judge 结论。"
            });
            sections.push({
              id: `${event.id || "expectation"}:human`,
              title: "人工判定",
              meta: stageOutput.human_review?.status || "pending",
              content: stageOutput.human_review?.reason || "等待人工复核。"
            });
          }
          return sections;
        }
        if (this.isSkillTestRuntimeOutputLane(event.lane_id)) {
          return [
            {
              id: `${event.id || "runtime"}:runtime`,
              title: "真实输出",
              meta: this.formatSkillTestTimelineMs(event.at_ms || 0),
              content: this.skillTestReviewRuntimeOutputLabel(event)
            }
          ];
        }
        if (this.isSkillTestSensorLane(event.lane_id)) {
          return [
            {
              id: `${event.id || "sensor"}:payload`,
              title: "传感器读数",
              meta: this.skillTestTimelineLaneLabel({ id: event.lane_id }),
              content: this.formatSkillTestReviewDebugJson(event.payload_inline || {})
            }
          ];
        }
        const payload = this.formatTerminalPayload(event.payload_inline);
        const sections = [];
        if (event.asset_id) {
          sections.push({
            id: `${event.id || "input"}:asset`,
            title: "资源信息",
            meta: this.skillTestTimelineLaneLabel({ id: event.lane_id }),
            content: `asset_id: ${event.asset_id}\nasset: ${this.skillTestTimelineEventAssetLabel(event)}`
          });
        }
        if (payload) {
          sections.push({
            id: `${event.id || "input"}:payload`,
            title: this.skillTestTimelineEventUsesAsset(event) ? "输入说明" : "输入内容",
            meta: this.formatSkillTestTimelineMs(event.at_ms || 0),
            content: payload
          });
        }
        if (!sections.length) {
          sections.push({
            id: `${event.id || "event"}:fallback`,
            title: "事件信息",
            meta: this.formatSkillTestTimelineMs(event.at_ms || 0),
            content: event.event_kind || "暂无事件内容。"
          });
        }
        return sections;
      },


      skillTestReviewEventPrimaryContent(event) {
        return this.skillTestReviewEventContentSections(event)
          .map((section) => `${section.title}\n${section.content}`)
          .join("\n\n");
      },


      skillTestReviewTerminalEventParts(event) {
        return Array.isArray(event?.parts) ? event.parts : [];
      },


      skillTestReviewTerminalEventHasParts(event) {
        return this.skillTestReviewTerminalEventParts(event).length > 0;
      },


      skillTestReviewTerminalPartMediaUrl(event, part) {
        if (!event?.run_id || !event?.id || !part?.part_id || !part?.artifact_object_id) {
          return "";
        }
        return `${this.apiBaseUrl}/terminal/sessions/${encodeURIComponent(event.run_id)}/events/${encodeURIComponent(event.id)}/parts/${encodeURIComponent(part.part_id)}/content`;
      },


      skillTestReviewEventMetadata(event) {
        if (!event) {
          return [];
        }
        const terminalEvent = event.terminal_event || null;
        const asset = event.asset_id ? this.skillTestAssetById(event.asset_id) : null;
        const assetLabel = asset ? this.skillTestAssetLabel(asset) : event.asset_id ? this.skillTestTimelineEventAssetLabel(event) : "";
        const stageOutput = this.skillTestReviewStageOutputForEvent(event);
        const pairs = [
          ["状态", this.skillTestReviewEventStatusLabel(event)],
          ["阶段 ID", stageOutput?.stage_id],
          ["人工判定", stageOutput?.human_review?.status],
          [
            "真实输出",
            this.isSkillTestTimelineExpectationEvent(event)
              ? `${this.skillTestReviewRuntimeOutputsForExpectation(event).length} 个`
              : ""
          ],
          ["事件 ID", event.id],
          ["事件类型", event.event_kind],
          ["MIME 类型", event.mime_type],
          ["资源", event.asset_id ? `${assetLabel} · ${event.asset_id}` : ""],
          ["必填", event.required === false ? "否" : "是"]
        ];
        if (terminalEvent) {
          pairs.push(
            ["终端序号", terminalEvent.seq_no === undefined || terminalEvent.seq_no === null ? "" : `#${terminalEvent.seq_no}`],
            ["方向", this.terminalDirectionLabel(terminalEvent.direction)],
            ["发生时间", this.formatDateTime(terminalEvent.occurred_at)],
            ["终端事件类型", terminalEvent.event_kind],
            ["终端 MIME 类型", terminalEvent.mime_type],
            ["Artifact 对象", terminalEvent.artifact_object_id]
          );
        }
        return pairs
          .filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== "")
          .map(([label, value]) => ({ label, value: String(value) }));
      },


      skillTestReviewExpandedEvaluation(event) {
        return this.isSkillTestTimelineExpectationEvent(event) ? this.skillTestReviewEvaluationFor(event.id) : null;
      },


      skillTestReviewExpandedEventRawJson(event) {
        if (!event) {
          return "null";
        }
        return this.formatSkillTestReviewDebugJson(event.terminal_event || event);
      },


      isSkillTestReviewExpectationSelected(event) {
        return Boolean(
          this.isSkillTestTimelineExpectationEvent(event) &&
            event?.id &&
            this.selectedSkillTestReviewExpectationId === event.id
        );
      },


      selectedSkillTestReviewExpectationEvent() {
        if (!this.selectedSkillTestReviewExpectationId) {
          return null;
        }
        return (
          this.skillTestReviewTimelineEvents().find(
            (event) =>
              this.isSkillTestTimelineExpectationEvent(event) && event.id === this.selectedSkillTestReviewExpectationId
          ) || null
        );
      },


      selectedSkillTestReviewEvaluation() {
        const event = this.selectedSkillTestReviewExpectationEvent();
        return event ? this.skillTestReviewEvaluationFor(event.id) : null;
      },


      selectedSkillTestReviewJudgeRawResponse() {
        return this.selectedSkillTestReviewEvaluation()?.raw_response || {};
      },


      selectedSkillTestReviewJudgeRequest() {
        return this.selectedSkillTestReviewJudgeRawResponse().request || null;
      },


      selectedSkillTestReviewJudgePromptPayload() {
        const request = this.selectedSkillTestReviewJudgeRequest();
        if (request?.prompt_payload) {
          return request.prompt_payload;
        }
        const event = this.selectedSkillTestReviewExpectationEvent();
        if (!event) {
          return null;
        }
        const cutoff = this.skillTestReviewOriginTime() + Number(event.at_ms || 0);
        const outputs = (this.skillTestReview?.replay?.terminal_events || []).filter((item) => {
          const occurredAt = new Date(item.occurred_at).getTime();
          return item.direction === "output" && (!Number.isFinite(occurredAt) || occurredAt <= cutoff);
        });
        return {
          expectation: event.expectation || "",
          cutoff_occurred_at: new Date(cutoff).toISOString(),
          terminal_outputs_before_cutoff: outputs,
          final_output: this.skillTestReview?.replay?.run?.final_output || this.liveRun?.final_output || "",
          run_status: this.skillTestRun?.status || "",
          reconstructed: true
        };
      },


      selectedSkillTestReviewJudgeParsedOutput() {
        return this.selectedSkillTestReviewJudgeRawResponse().parsed || null;
      },


      selectedSkillTestReviewJudgeRawOutput() {
        const rawResponse = this.selectedSkillTestReviewJudgeRawResponse();
        return rawResponse.content || (rawResponse.error ? `${rawResponse.error_type || "Error"}: ${rawResponse.error}` : "");
      },


      selectedSkillTestReviewJudgeUsage() {
        return this.selectedSkillTestReviewJudgeRawResponse().usage || null;
      },


      selectedSkillTestReviewJudgeProviderRaw() {
        return this.selectedSkillTestReviewJudgeRawResponse().raw || null;
      },


      selectedSkillTestReviewJudgeInputNotice() {
        if (!this.selectedSkillTestReviewExpectationEvent()) {
          return "";
        }
        if (this.selectedSkillTestReviewJudgeRequest()) {
          return "已保存本次 Judge 调用的真实输入。";
        }
        return "该历史记录未保存 Judge 输入，以下为基于当前 replay 重建的参考输入。";
      },


      formatSkillTestReviewDebugJson(value) {
        return JSON.stringify(value ?? null, null, 2);
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
        if (this.isSkillTestRuntimeOutputLane(step?.lane_id)) {
          return "output";
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


      skillTestReviewEventStatusLabel(event) {
        const status = this.skillTestReviewStepStatus(event);
        if (this.isSkillTestRuntimeOutputLane(event?.lane_id)) {
          return status === "not_occurred" ? "未输出" : "已输出";
        }
        if (this.isSkillTestTimelineExpectationEvent(event)) {
          return this.skillTestReviewAssertionVerdictLabel(event) || "未判定";
        }
        return status === "not_occurred" ? "未发送" : this.skillTestReviewStepStatusLabel(event);
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
          this.skillTestReviewEventStatusLabel(event),
          "点击查看完整内容"
        ];
        if (this.isSkillTestRuntimeOutputLane(event?.lane_id)) {
          const terminalEvent = event.terminal_event || {};
          if (terminalEvent.seq_no !== undefined && terminalEvent.seq_no !== null) {
            parts.push(`terminal seq #${terminalEvent.seq_no}`);
          }
          if (terminalEvent.event_kind) {
            parts.push(terminalEvent.event_kind);
          }
        }
        const runtimeOutputCount = this.skillTestReviewRuntimeOutputsForExpectation(event).length;
        if (runtimeOutputCount) {
          parts.push(`${runtimeOutputCount} 个真实输出`);
        }
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
        if (this.isSkillTestRuntimeOutputLane(event?.lane_id)) {
          return "border-cyan-500/45 bg-cyan-500/10 text-cyan-100 ring-1 ring-cyan-500/20";
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
