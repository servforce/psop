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

  window.PSOPConsoleFormatMethods = {

      formatJson(value) {
        return JSON.stringify(value ?? null, null, 2);
      },


      jsonHighlightHtml(value) {
        return highlightJson(value);
      },


      syncJsonHighlightScroll(event) {
        const textarea = event?.target;
        const highlightLayer = textarea?.previousElementSibling;
        if (!textarea || !highlightLayer) {
          return;
        }
        highlightLayer.scrollTop = textarea.scrollTop;
        highlightLayer.scrollLeft = textarea.scrollLeft;
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


      formatBytes(value) {
        const bytes = Number(value || 0);
        if (!Number.isFinite(bytes) || bytes <= 0) {
          return "0 B";
        }
        const units = ["B", "KB", "MB", "GB"];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
          size /= 1024;
          unitIndex += 1;
        }
        return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
      },

      formatDuration(value) {
        if (value === null || value === undefined || value === "") {
          return "N/A";
        }
        const milliseconds = Number(value);
        if (!Number.isFinite(milliseconds) || milliseconds < 0) {
          return "N/A";
        }
        if (milliseconds < 1000) {
          return `${Math.round(milliseconds)} ms`;
        }
        const totalSeconds = Math.round(milliseconds / 1000);
        if (totalSeconds < 60) {
          return `${totalSeconds} s`;
        }
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        if (minutes < 60) {
          return seconds ? `${minutes} m ${seconds} s` : `${minutes} m`;
        }
        const hours = Math.floor(minutes / 60);
        const remainingMinutes = minutes % 60;
        return remainingMinutes ? `${hours} h ${remainingMinutes} m` : `${hours} h`;
      },

      formatTokenUsage(value) {
        if (!value || typeof value !== "object") {
          return "N/A";
        }
        if (value.total_tokens === null || value.total_tokens === undefined || value.total_tokens === "") {
          return "N/A";
        }
        const total = Number(value.total_tokens);
        if (!Number.isFinite(total)) {
          return "N/A";
        }
        return new Intl.NumberFormat("zh-CN").format(total);
      },


      formatStatus(value) {
        const statusMap = {
          active: "启用",
          archived: "已归档",
          ready: "已就绪",
          draft: "草稿",
          published: "已发布",
          unpublished: "未发布",
          requested: "已请求",
          compiling: "编译中",
          processing: "处理中",
          pending: "待处理",
          running: "运行中",
          reviewing: "Review 中",
          testing: "测试中",
          waiting_input: "等待输入",
          waiting_authorization: "等待授权",
          waiting_tool_authorization: "等待授权",
          waiting_checkpoint: "等待检查点",
          waiting_runtime: "等待运行",
          matched: "已匹配",
          sent: "已发送",
          output: "已输出",
          triggered: "已触发",
          not_occurred: "未发生",
          inconclusive: "未定",
          queued: "排队中",
          accepted: "已接受",
          approved: "已批准",
          authorized: "已授权",
          succeeded: "成功",
          activated: "已激活",
          canary: "灰度中",
          rolled_back: "已回滚",
          passed: "通过",
          failed: "失败",
          denied: "已拒绝",
          retryable_failed: "等待重试",
          deadletter: "死信",
          dead_letter: "死信",
          rejected: "已拒绝",
          cancelled: "已取消",
          canceled: "已取消",
          timeout: "已超时",
          timed_out: "已超时",
          skipped: "已跳过"
        };
        return statusMap[value] || value || "未知";
      },


      statusBadgeTone(value) {
        const normalized = String(value || "").toLowerCase();
        if (["active", "published", "succeeded", "success", "accepted", "approved", "activated", "ready"].includes(normalized)) {
          return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
        }
        if (["passed"].includes(normalized)) {
          return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
        }
        if (["compiling", "running", "testing", "canary", "waiting_input", "waiting_checkpoint", "waiting_runtime", "in_progress", "processing", "matched", "triggered", "output"].includes(normalized)) {
          return "border-sky-500/25 bg-sky-500/10 text-sky-200";
        }
        if (["requested", "pending", "queued", "draft", "reviewing", "unpublished", "retrying", "retryable_failed", "sent", "inconclusive"].includes(normalized)) {
          return "border-amber-500/25 bg-amber-500/10 text-amber-200";
        }
        if (["failed", "error", "rejected", "cancelled", "canceled", "timeout", "timed_out", "deadletter", "dead_letter", "rolled_back"].includes(normalized)) {
          return "border-rose-500/30 bg-rose-500/10 text-rose-200";
        }
        if (["not_occurred"].includes(normalized)) {
          return "border-slate-500/45 bg-slate-800/80 text-slate-200";
        }
        if (["archived", "skipped", "unknown"].includes(normalized)) {
          return "border-slate-700 bg-slate-950/40 text-slate-400";
        }
        return "border-slate-700 bg-slate-950/40 text-slate-400";
      },


      wsStatusLabel(value) {
        const labels = {
          idle: "未连接",
          connecting: "连接中",
          open: "已连接",
          closed: "已断开",
          error: "连接异常"
        };
        return labels[value] || "未知";
      },


      terminalDirectionLabel(value) {
        return value === "output" ? "输出" : "输入";
      },


      terminalDirectionTone(value) {
        return value === "output"
          ? "border-sky-500/25 bg-sky-500/10 text-sky-200"
          : "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
      },


      formatTerminalPayload(value) {
        if (typeof value === "string") {
          return value;
        }
        if (value === null || value === undefined) {
          return "";
        }
        return JSON.stringify(value, null, 2);
      },


      formatTerminalEventPayload(event) {
        if (!event) {
          return "";
        }
        const lines = [];
        if (event.artifact_object_id) {
          lines.push(`artifact_object_id: ${event.artifact_object_id}`);
        }
        if (event.mime_type && event.mime_type !== "text/plain") {
          lines.push(`mime_type: ${event.mime_type}`);
        }
        const payload = this.formatTerminalPayload(event.payload_inline);
        if (payload) {
          lines.push(payload);
        }
        return lines.join("\n") || event.event_kind || "";
      },
  };
})();
