(function () {
  window.PSOPConsoleAgentPromptMethods = {
      async loadAgentPrompts() {
        this.busy.agentPrompts = true;
        try {
          const prompts = await this.apiRequest("/agent-prompts");
          this.agentPrompts = prompts;
          this.agentPromptBindings = this.agentPromptBindingsFromPrompts(prompts);
        } finally {
          this.busy.agentPrompts = false;
        }
      },

      async loadAgentPromptDetail(definitionId, versionId = "") {
        this.busy.agentPromptDetail = true;
        try {
          const query = versionId ? `?version_id=${encodeURIComponent(versionId)}` : "";
          this.agentPromptDetail = await this.apiRequest(`/agent-prompts/${definitionId}${query}`);
          this.agentPromptBindings = this.agentPromptDetail?.bindings || [];
          const selected = this.agentPromptDetail.selected_version;
          this.agentPromptSelectedVersionId = selected?.id || "";
          const files = selected?.files || {};
          const preferredFile = this.agentPromptSelectedFile && files[this.agentPromptSelectedFile] !== undefined
            ? this.agentPromptSelectedFile
            : this.agentPromptFileNames(files)[0] || "";
          this.selectAgentPromptFile(preferredFile);
          this.agentPromptValidation = null;
        } finally {
          this.busy.agentPromptDetail = false;
        }
      },

      agentPromptBindingsFromPrompts(prompts = this.agentPrompts) {
        return (prompts || []).flatMap((prompt) => prompt.bindings || []);
      },

      agentPromptFileNames(files = this.agentPromptDetail?.selected_version?.files || {}) {
        const preferred = ["agent.yaml", "system.md", "user_template.md", "user_template.json", "output_schema.json"];
        const names = Object.keys(files || {});
        return [
          ...preferred.filter((name) => names.includes(name)),
          ...names.filter((name) => !preferred.includes(name)).sort()
        ];
      },

      selectedAgentPromptVersion() {
        return this.agentPromptDetail?.selected_version || null;
      },

      selectedAgentPromptVersionSummary() {
        const versionId = this.agentPromptSelectedVersionId;
        return (this.agentPromptDetail?.versions || []).find((version) => version.id === versionId) || null;
      },

      selectAgentPromptFile(path) {
        this.agentPromptSelectedFile = path || "";
        const files = this.selectedAgentPromptVersion()?.files || {};
        this.agentPromptFileDraft = path ? String(files[path] || "") : "";
      },

      async selectAgentPromptVersion(versionId) {
        if (!this.agentPromptDetail || !versionId || versionId === this.agentPromptSelectedVersionId) {
          return;
        }
        await this.loadAgentPromptDetail(this.agentPromptDetail.id, versionId);
      },

      updateAgentPromptFileDraft(value) {
        this.agentPromptFileDraft = value;
      },

      async saveAgentPromptFileDraft() {
        const detail = this.agentPromptDetail;
        const version = this.selectedAgentPromptVersion();
        if (!detail || !version || !this.agentPromptSelectedFile) {
          return;
        }
        if (version.status !== "draft") {
          this.showNotice("error", "已发布版本不可编辑，请先创建新 draft。");
          return;
        }
        this.busy.agentPromptSave = true;
        try {
          const files = {
            ...(version.files || {}),
            [this.agentPromptSelectedFile]: this.agentPromptFileDraft
          };
          const updated = await this.apiRequest(`/agent-prompts/${detail.id}/versions/${version.id}/files`, {
            method: "PUT",
            body: JSON.stringify({ files })
          });
          this.agentPromptDetail.selected_version = updated;
          this.agentPromptSelectedVersionId = updated.id;
          this.showCenterToast("success", "Prompt 文件已保存。");
        } finally {
          this.busy.agentPromptSave = false;
        }
      },

      async createAgentPromptDraft() {
        const detail = this.agentPromptDetail;
        const version = this.selectedAgentPromptVersion();
        if (!detail) {
          return;
        }
        this.busy.agentPromptAction = true;
        try {
          const next = await this.apiRequest(`/agent-prompts/${detail.id}/versions`, {
            method: "POST",
            body: JSON.stringify({ parent_version_id: version?.id || null })
          });
          this.agentPromptDetail = next;
          this.agentPromptSelectedVersionId = next.selected_version?.id || "";
          this.selectAgentPromptFile(this.agentPromptFileNames(next.selected_version?.files || {})[0] || "");
          this.showCenterToast("success", "已创建 draft。");
        } finally {
          this.busy.agentPromptAction = false;
        }
      },

      async validateAgentPromptVersion() {
        const detail = this.agentPromptDetail;
        const version = this.selectedAgentPromptVersion();
        if (!detail || !version) {
          return;
        }
        this.busy.agentPromptAction = true;
        try {
          this.agentPromptValidation = await this.apiRequest(`/agent-prompts/${detail.id}/versions/${version.id}/validate`, {
            method: "POST"
          });
          this.showCenterToast(this.agentPromptValidation.valid ? "success" : "error", this.agentPromptValidation.valid ? "校验通过。" : "校验未通过。");
        } finally {
          this.busy.agentPromptAction = false;
        }
      },

      async publishAgentPromptVersion() {
        const detail = this.agentPromptDetail;
        const version = this.selectedAgentPromptVersion();
        if (!detail || !version) {
          return;
        }
        this.busy.agentPromptAction = true;
        try {
          const published = await this.apiRequest(`/agent-prompts/${detail.id}/versions/${version.id}/publish`, {
            method: "POST"
          });
          await this.loadAgentPromptDetail(detail.id, published.id);
          this.showCenterToast("success", "版本已发布。");
        } finally {
          this.busy.agentPromptAction = false;
        }
      },

      async activateAgentPromptVersion(usageKey = "") {
        const detail = this.agentPromptDetail;
        const version = this.selectedAgentPromptVersion();
        if (!detail || !version) {
          return;
        }
        this.busy.agentPromptAction = true;
        try {
          const payload = usageKey ? { usage_key: usageKey } : {};
          this.agentPromptDetail = await this.apiRequest(`/agent-prompts/${detail.id}/versions/${version.id}/activate`, {
            method: "POST",
            body: JSON.stringify(payload)
          });
          await this.loadAgentPromptDetail(detail.id, version.id);
          this.showCenterToast("success", "已启用版本。");
        } finally {
          this.busy.agentPromptAction = false;
        }
      },

      agentPromptStatusClass(status) {
        if (status === "published") {
          return "border-emerald-500/25 bg-emerald-500/10 text-emerald-200";
        }
        if (status === "draft") {
          return "border-amber-500/25 bg-amber-500/15 text-amber-200";
        }
        return "border-slate-700 bg-slate-900/60 text-slate-300";
      },

      agentPromptStatusLabel(status) {
        return {
          draft: "草稿",
          published: "已发布",
          archived: "已归档",
          active: "启用"
        }[status] || status || "未知";
      },

      shortPromptHash(value) {
        const text = String(value || "");
        return text ? text.slice(0, 12) : "未生成";
      },

      agentPromptActiveBindingLabels(prompt) {
        const bindings = prompt?.bindings || [];
        if (!bindings.length) {
          return "未绑定";
        }
        return bindings.map((item) => item.usage_key).join(" / ");
      },

      agentPromptAgentKeyLabel(prompt) {
        return String(prompt?.agent_key || "").trim() || "N/A";
      },

      agentPromptSelectedFileLanguage() {
        const file = this.agentPromptSelectedFile || "";
        if (file.endsWith(".json")) {
          return "JSON";
        }
        if (file.endsWith(".yaml") || file.endsWith(".yml")) {
          return "YAML";
        }
        return "Markdown";
      }
  };
})();
