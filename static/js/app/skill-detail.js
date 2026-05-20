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

  window.PSOPConsoleSkillDetailMethods = {

      async loadSkills() {
        this.busy.list = true;
        try {
          const params = new URLSearchParams();
          if (this.filters.search.trim()) {
            params.set("search", this.filters.search.trim());
          }
          if (this.filters.status) {
            params.set("status", this.filters.status);
          }
          const suffix = params.toString() ? `?${params}` : "";
          this.skills = await this.apiRequest(`/skills${suffix}`);
        } finally {
          this.busy.list = false;
        }
      },


      async createSkill() {
        this.busy.create = true;
        this.clearNotice();

        try {
          const payload = {
            ...this.createForm,
            key: generateSkillKey(this.createForm.name)
          };
          const created = await this.apiRequest("/skills", {
            method: "POST",
            body: JSON.stringify(payload)
          });
          this.createForm = { name: "", description: "" };
          this.createModalOpen = false;
          await this.navigate(buildSkillDetailPath(created.id));
          this.showNotice("success", "Skill 已创建，并已在 GitLab 中初始化。");
        } catch (error) {
          this.showNotice("error", error.message || "创建 Skill 失败。");
        } finally {
          this.busy.create = false;
        }
      },


      async deleteSkill() {
        if (!this.deleteTargetSkill || !this.isDeleteConfirmationValid()) {
          return;
        }

        const deletedSkill = this.deleteTargetSkill;
        this.busy.delete = true;
        this.clearNotice();

        try {
          await this.apiRequest(`/skills/${deletedSkill.id}`, {
            method: "DELETE",
            body: JSON.stringify(this.deleteForm)
          });
          this.deleteModalOpen = false;
          this.deleteTargetSkill = null;
          this.deleteForm = { confirmation_name: "" };

          if (this.currentSkill?.id === deletedSkill.id) {
            await this.navigate("/admin/skills");
          } else {
            await this.loadSkills();
          }

          this.showCenterToast("success", "Skill 已删除，对应 GitLab 仓库项目已归档。");
        } catch (error) {
          this.showCenterToast("error", error.message || "删除 Skill 失败。");
        } finally {
          this.busy.delete = false;
        }
      },


      async loadSkillDetail(skillId, options = {}) {
        this.busy.detail = true;
        try {
          const detail = await this.apiRequest(`/skills/${skillId}`);

          this.currentSkill = detail;
          this.metadataForm = {
            name: detail.name,
            description: detail.description
          };
          this.resetLazyDetailState(skillId);
          if (!["overview", "source", "materials", "publish", "compiler", "debug", "runtime", "test"].includes(this.activeDetailTab)) {
            this.activeDetailTab = "overview";
          }
          if (this.activeDetailTab === "materials") {
            await this.loadRawMaterials(detail.id);
          }
          if (this.activeDetailTab === "compiler") {
            await this.loadCompilerRequests(detail.id);
          }
          if (this.activeDetailTab === "runtime") {
            this.invocationForm.skill_key = detail.key;
            await this.loadInvocations(detail.key);
          }
          if (this.activeDetailTab === "debug") {
            await this.loadInvocations(detail.key);
          }
          if (this.activeDetailTab === "test" && options.loadTestCases !== false) {
            await this.loadSkillTestCases(detail.id);
          }
        } finally {
          this.busy.detail = false;
        }
      },


      resetLazyDetailState(skillId) {
        this.sourceLoadedSkillId = null;
        this.repositoryLoadedSkillId = null;
        this.repositoryPath = "";
        this.repositoryEntries = [];
        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.rawMaterialsLoadedSkillId = null;
        this.rawMaterials = [];
        this.rawMaterialDetail = null;
        this.selectedRawMaterialIds = [];
        this.rawMaterialUploadMode = "file";
        this.rawMaterialUploadFile = null;
        this.rawMaterialUploadForm = {
          name: "",
          description: "",
          material_kind: "",
          source_note: "",
          source_url: ""
        };
        this.rawMaterialUploadModalOpen = false;
        this.rawMaterialGenerateModalOpen = false;
        this.rawMaterialGenerateForm = {
          user_description: ""
        };
        this.rawMaterialGenerationResult = null;
        this.publishRecordsLoadedSkillId = null;
        this.publishRecords = [];
        this.sourceForm = {
          readme_content: "",
          skill_md_content: "",
          skill_yaml_content: "",
          base_commit_sha: ""
        };
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
        this.sourceCreateModalOpen = false;
        this.sourceActionMenuOpen = false;
        this.sourceCreateMode = "file";
        this.sourceCreateForm = {
          path: "",
          content: ""
        };
        this.skillTestCases = [];
        this.skillTestCase = null;
        this.skillTestDataObjects = [];
        this.skillTestRuns = [];
        this.skillTestRun = null;
        this.skillTestReview = null;
        this.skillTestReviewCursor = 100;
        this.skillTestReviewAutoFollow = true;
        this.skillTestCaseSearch = "";
        this.resetSkillTestCaseForm();
        this.closeCompilerArtifactWorkspace();
        if (skillId) {
          this.activeSourceTab = "skill.yaml";
        }
      },


      async loadSkillSource(skillId) {
        if (this.sourceLoadedSkillId === skillId) {
          return;
        }

        this.busy.source = true;
        try {
          const source = await this.apiRequest(`/skills/${skillId}/source`);
          this.sourceForm = {
            readme_content: source.readme_content,
            skill_md_content: source.skill_md_content,
            skill_yaml_content: source.skill_yaml_content,
            base_commit_sha: source.head_commit_sha
          };
          this.sourceLoadedSkillId = skillId;
        } finally {
          this.busy.source = false;
        }
      },


      async loadPublishRecords(skillId) {
        if (this.publishRecordsLoadedSkillId === skillId) {
          return;
        }

        this.busy.publishRecords = true;
        try {
          this.publishRecords = await this.apiRequest(`/skills/${skillId}/publishes`);
          this.publishRecordsLoadedSkillId = skillId;
        } finally {
          this.busy.publishRecords = false;
        }
      },


      async loadRawMaterials(skillId, force = false) {
        if (!force && this.rawMaterialsLoadedSkillId === skillId) {
          return;
        }

        this.busy.rawMaterials = true;
        try {
          this.rawMaterials = await this.apiRequest(`/skills/${skillId}/raw-materials`);
          this.rawMaterialsLoadedSkillId = skillId;
          this.selectedRawMaterialIds = this.selectedRawMaterialIds.filter((materialId) =>
            this.rawMaterials.some((material) => material.id === materialId)
          );
          if (this.rawMaterialDetail && !this.rawMaterials.some((material) => material.id === this.rawMaterialDetail.id)) {
            this.rawMaterialDetail = null;
          }
          if (!this.rawMaterialDetail && this.rawMaterials.length > 0) {
            await this.openRawMaterialDetail(this.rawMaterials[0]);
          }
        } finally {
          this.busy.rawMaterials = false;
        }
      },


      async openRawMaterialDetail(material) {
        if (!this.currentSkill || !material?.id) {
          return;
        }

        this.busy.rawMaterialDetail = true;
        try {
          this.rawMaterialDetail = await this.apiRequest(`/skills/${this.currentSkill.id}/raw-materials/${material.id}`);
        } finally {
          this.busy.rawMaterialDetail = false;
        }
      },


      handleRawMaterialFileChange(event) {
        const file = event?.target?.files?.[0] || null;
        this.rawMaterialUploadFile = file;
        if (file && !this.rawMaterialUploadForm.name) {
          this.rawMaterialUploadForm.name = file.name;
        }
      },


      openRawMaterialUploadModal(mode = "file") {
        this.rawMaterialUploadMode = mode;
        this.rawMaterialUploadFile = null;
        this.rawMaterialUploadForm = {
          name: "",
          description: "",
          material_kind: "",
          source_note: "",
          source_url: ""
        };
        this.rawMaterialUploadModalOpen = true;
      },


      closeRawMaterialUploadModal() {
        if (this.busy.rawMaterialUpload) {
          return;
        }
        this.rawMaterialUploadModalOpen = false;
      },


      async submitRawMaterial() {
        if (!this.currentSkill) {
          return;
        }
        if (this.rawMaterialUploadMode === "file" && !this.rawMaterialUploadFile) {
          this.showCenterToast("error", "请选择要上传的素材文件。");
          return;
        }
        if (this.rawMaterialUploadMode === "url" && !this.rawMaterialUploadForm.source_url.trim()) {
          this.showCenterToast("error", "请输入参考 URL。");
          return;
        }

        const formData = new FormData();
        if (this.rawMaterialUploadMode === "file") {
          formData.append("file", this.rawMaterialUploadFile);
        } else {
          formData.append("source_url", this.rawMaterialUploadForm.source_url.trim());
        }
        ["name", "description", "material_kind", "source_note"].forEach((key) => {
          const value = this.rawMaterialUploadForm[key];
          if (value) {
            formData.append(key, value);
          }
        });

        this.busy.rawMaterialUpload = true;
        this.clearNotice();
        try {
          const created = await this.apiRequest(`/skills/${this.currentSkill.id}/raw-materials`, {
            method: "POST",
            body: formData
          });
          this.rawMaterialUploadFile = null;
          this.rawMaterialUploadForm = {
            name: "",
            description: "",
            material_kind: "",
            source_note: "",
            source_url: ""
          };
          if (this.$refs.rawMaterialFileInput) {
            this.$refs.rawMaterialFileInput.value = "";
          }
          this.rawMaterialUploadModalOpen = false;
          await this.loadRawMaterials(this.currentSkill.id, true);
          await this.openRawMaterialDetail(created);
          this.showNotice(created.status === "ready" ? "success" : "error", created.status === "ready" ? "素材已保存并解析完成。" : "素材已保存，但解析失败。");
        } catch (error) {
          this.showNotice("error", error.message || "保存素材失败。");
        } finally {
          this.busy.rawMaterialUpload = false;
        }
      },


      async deleteRawMaterial(material) {
        if (!this.currentSkill || !material?.id) {
          return;
        }

        this.busy.rawMaterialDelete = true;
        this.clearNotice();
        try {
          await this.apiRequest(`/skills/${this.currentSkill.id}/raw-materials/${material.id}`, {
            method: "DELETE"
          });
          this.selectedRawMaterialIds = this.selectedRawMaterialIds.filter((materialId) => materialId !== material.id);
          if (this.rawMaterialDetail?.id === material.id) {
            this.rawMaterialDetail = null;
          }
          await this.loadRawMaterials(this.currentSkill.id, true);
          this.showNotice("success", "素材已移除。");
        } catch (error) {
          this.showNotice("error", error.message || "移除素材失败。");
        } finally {
          this.busy.rawMaterialDelete = false;
        }
      },


      toggleRawMaterialSelection(material) {
        if (!material || material.status !== "ready") {
          return;
        }
        if (this.selectedRawMaterialIds.includes(material.id)) {
          this.selectedRawMaterialIds = this.selectedRawMaterialIds.filter((materialId) => materialId !== material.id);
          return;
        }
        this.selectedRawMaterialIds = [...this.selectedRawMaterialIds, material.id];
      },


      isRawMaterialSelected(material) {
        return Boolean(material?.id && this.selectedRawMaterialIds.includes(material.id));
      },


      selectedRawMaterials() {
        return this.rawMaterials.filter((material) => this.selectedRawMaterialIds.includes(material.id));
      },


      openRawMaterialGenerateModal() {
        if (this.selectedRawMaterialIds.length === 0) {
          this.showCenterToast("error", "请选择至少一个已解析素材。");
          return;
        }
        this.rawMaterialGenerateForm = {
          user_description: ""
        };
        this.rawMaterialGenerationResult = null;
        this.rawMaterialGenerateModalOpen = true;
      },


      closeRawMaterialGenerateModal() {
        if (this.busy.rawMaterialGenerate) {
          return;
        }
        this.rawMaterialGenerateModalOpen = false;
      },


      async generateSkillDraftFromRawMaterials() {
        if (!this.currentSkill || this.selectedRawMaterialIds.length === 0) {
          return;
        }
        if (!this.rawMaterialGenerateForm.user_description.trim()) {
          this.showCenterToast("error", "请输入生成描述。");
          return;
        }

        this.busy.rawMaterialGenerate = true;
        this.clearNotice();
        try {
          const skillId = this.currentSkill.id;
          const result = await this.apiRequest(`/skills/${skillId}/raw-materials/generate-skill-draft`, {
            method: "POST",
            body: JSON.stringify({
              material_ids: this.selectedRawMaterialIds,
              user_description: this.rawMaterialGenerateForm.user_description.trim(),
              base_commit_sha: this.currentSkill.latest_draft_head_sha
            })
          });
          this.rawMaterialGenerationResult = result;
          this.sourceLoadedSkillId = null;
          this.repositoryLoadedSkillId = null;
          this.currentSkill = await this.apiRequest(`/skills/${skillId}`);
          await this.loadRawMaterials(skillId, true);
          this.showNotice("success", "Skill 草稿已生成并提交到 GitLab draft。");
        } catch (error) {
          this.showNotice("error", error.message || "生成 Skill 草稿失败。");
        } finally {
          this.busy.rawMaterialGenerate = false;
        }
      },


      rawMaterialKindLabel(value) {
        const labels = {
          text: "文本",
          markdown: "Markdown",
          pdf: "PDF",
          image: "图片",
          audio: "音频",
          video: "视频",
          url: "URL",
          file: "文件"
        };
        return labels[value] || value || "素材";
      },


      rawMaterialKindIcon(value) {
        const icons = {
          text: "description",
          markdown: "article",
          pdf: "picture_as_pdf",
          image: "image",
          audio: "graphic_eq",
          video: "movie",
          url: "link",
          file: "attach_file"
        };
        return icons[value] || "draft";
      },


      rawMaterialContentUrl(material) {
        if (!this.currentSkill || !material?.id) {
          return "";
        }
        return `${this.apiBaseUrl}/skills/${encodeURIComponent(this.currentSkill.id)}/raw-materials/${encodeURIComponent(material.id)}/content`;
      },


      canPreviewRawMaterial(kind, material = this.rawMaterialDetail) {
        const mimeType = String(material?.mime_type || "");
        if (kind === "image") {
          return mimeType.startsWith("image/");
        }
        if (kind === "audio") {
          return mimeType.startsWith("audio/");
        }
        if (kind === "video") {
          return mimeType.startsWith("video/");
        }
        if (kind === "pdf") {
          return mimeType === "application/pdf";
        }
        if (kind === "document") {
          return Boolean(material?.id) &&
            !this.canPreviewRawMaterial("image", material) &&
            !this.canPreviewRawMaterial("audio", material) &&
            !this.canPreviewRawMaterial("video", material) &&
            !this.canPreviewRawMaterial("pdf", material);
        }
        return false;
      },


      async loadRepositoryTree(skillId, path = this.repositoryPath || "") {
        this.busy.repositoryTree = true;
        try {
          const params = new URLSearchParams();
          const normalizedPath = this.normalizeRepositoryPath(path, true);
          if (normalizedPath) {
            params.set("path", normalizedPath);
          }
          const suffix = params.toString() ? `?${params}` : "";
          const tree = await this.apiRequest(`/skills/${skillId}/repository/tree${suffix}`);
          this.repositoryPath = tree.path || "";
          this.repositoryEntries = tree.entries || [];
          this.repositoryLoadedSkillId = skillId;
          await this.ensureDefaultRepositoryPreview(skillId);
        } finally {
          this.busy.repositoryTree = false;
        }
      },


      async ensureDefaultRepositoryPreview(skillId) {
        if (this.selectedRepositoryFile || this.repositoryPath) {
          return;
        }

        const readmeEntry = this.repositoryEntries.find(
          (entry) => entry.type === "blob" && entry.name.toLowerCase() === "readme.md"
        );
        if (readmeEntry) {
          await this.loadRepositoryFile(readmeEntry.path);
        }
      },


      async openRepositoryFolder(entry) {
        if (!this.currentSkill || entry.type !== "tree") {
          return;
        }

        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
        await this.loadRepositoryTree(this.currentSkill.id, entry.path);
      },


      async openRepositoryPath(path) {
        if (!this.currentSkill) {
          return;
        }

        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
        await this.loadRepositoryTree(this.currentSkill.id, path);
      },


      async openRepositoryFile(entry) {
        if (!this.currentSkill || entry.type !== "blob") {
          return;
        }

        await this.loadRepositoryFile(entry.path);
      },


      async loadRepositoryFile(path) {
        if (!this.currentSkill) {
          return;
        }

        this.busy.repositoryFile = true;
        try {
          const params = new URLSearchParams({ path });
          const file = await this.apiRequest(`/skills/${this.currentSkill.id}/repository/files?${params}`);
          this.selectedRepositoryFile = file;
          this.repositoryEditing = false;
          this.repositoryFileForm = {
            path: file.file_path,
            content: file.content,
            base_commit_sha: file.head_commit_sha
          };
        } finally {
          this.busy.repositoryFile = false;
        }
      },


      closeRepositoryFile() {
        this.selectedRepositoryFile = null;
        this.repositoryEditing = false;
        this.repositoryFileForm = {
          path: "",
          content: "",
          base_commit_sha: ""
        };
      },


      startRepositoryEdit() {
        if (!this.selectedRepositoryFile) {
          return;
        }
        if (this.isSystemManifestFile()) {
          this.showCenterToast("info", "skill.yaml 为系统生成预览，请通过结构化配置修改。");
          return;
        }
        this.repositoryEditing = true;
      },


      cancelRepositoryEdit() {
        if (!this.selectedRepositoryFile) {
          return;
        }
        this.repositoryFileForm.content = this.selectedRepositoryFile.content;
        this.repositoryEditing = false;
      },


      async saveRepositoryFile() {
        if (!this.currentSkill || !this.repositoryFileForm.path) {
          return;
        }

        const skillId = this.currentSkill.id;
        const currentPath = this.repositoryPath;
        const filePath = this.repositoryFileForm.path;
        this.busy.repositorySave = true;
        this.clearNotice();

        try {
          const saved = await this.apiRequest(`/skills/${skillId}/repository/files`, {
            method: "PUT",
            body: JSON.stringify(this.repositoryFileForm)
          });
          await this.loadSkillDetail(skillId);
          await this.loadRepositoryTree(skillId, currentPath);
          await this.loadRepositoryFile(saved.file_path || filePath);
          this.repositoryEditing = false;
          this.showNotice("success", "文件已提交到 GitLab。");
        } catch (error) {
          this.showNotice("error", error.message || "保存文件失败。");
        } finally {
          this.busy.repositorySave = false;
        }
      },


      openSourceCreateModal(mode) {
        this.sourceCreateMode = mode;
        this.sourceCreateForm = {
          path: this.repositoryPath ? `${this.repositoryPath}/` : "",
          content: ""
        };
        this.sourceActionMenuOpen = false;
        this.sourceCreateModalOpen = true;
      },


      toggleSourceActionMenu() {
        this.sourceActionMenuOpen = !this.sourceActionMenuOpen;
      },


      closeSourceActionMenu() {
        this.sourceActionMenuOpen = false;
      },


      closeSourceCreateModal() {
        if (this.busy.repositoryCreate) {
          return;
        }

        this.sourceCreateModalOpen = false;
        this.sourceCreateForm = {
          path: "",
          content: ""
        };
      },


      async createRepositoryEntry() {
        if (!this.currentSkill) {
          return;
        }

        const skillId = this.currentSkill.id;
        const currentPath = this.repositoryPath;
        const mode = this.sourceCreateMode;
        const path = this.normalizeRepositoryPath(this.sourceCreateForm.path);
        if (!path) {
          this.showNotice("error", "请填写仓库路径。");
          return;
        }

        this.busy.repositoryCreate = true;
        this.clearNotice();

        try {
          const endpoint =
            mode === "folder"
              ? `/skills/${skillId}/repository/folders`
              : `/skills/${skillId}/repository/files`;
          const body =
            mode === "folder"
              ? { path }
              : { path, content: this.sourceCreateForm.content };
          const created = await this.apiRequest(endpoint, {
            method: "POST",
            body: JSON.stringify(body)
          });
          this.sourceCreateModalOpen = false;
          await this.loadSkillDetail(skillId);
          await this.loadRepositoryTree(skillId, currentPath);
          if (mode === "file") {
            await this.loadRepositoryFile(created.file_path);
          }
          this.showNotice("success", mode === "folder" ? "文件夹已创建。" : "文件已创建。");
        } catch (error) {
          this.showNotice("error", error.message || "创建失败。");
        } finally {
          this.busy.repositoryCreate = false;
        }
      },


      async saveMetadata() {
        if (!this.currentSkill) {
          return;
        }

        this.busy.metadata = true;
        this.clearNotice();

        try {
          await this.apiRequest(`/skills/${this.currentSkill.id}`, {
            method: "PATCH",
            body: JSON.stringify(this.metadataForm)
          });
          await this.loadSkillDetail(this.currentSkill.id);
          this.showNotice("success", "Skill 基本信息已更新。");
        } catch (error) {
          this.showNotice("error", error.message || "更新 Skill 基本信息失败。");
        } finally {
          this.busy.metadata = false;
        }
      },


      async saveSource() {
        if (!this.currentSkill) {
          return;
        }
        if (this.sourceLoadedSkillId !== this.currentSkill.id) {
          await this.loadSkillSource(this.currentSkill.id);
        }

        this.busy.source = true;
        this.clearNotice();

        try {
          const saved = await this.apiRequest(`/skills/${this.currentSkill.id}/source`, {
            method: "PUT",
            body: JSON.stringify(this.sourceForm)
          });
          this.sourceForm.base_commit_sha = saved.head_commit_sha;
          await this.loadSkillDetail(this.currentSkill.id);
          await this.loadSkillSource(this.currentSkill.id);
          this.showNotice("success", "Skill 源码已提交到 GitLab。");
        } catch (error) {
          this.showNotice("error", error.message || "保存 Skill source 失败。");
        } finally {
          this.busy.source = false;
        }
      },


      emptyPublishProgress() {
        return {
          active: false,
          compile_request_id: null,
          terminal: false,
          terminal_status: null,
          error_message: "",
          stages: this.defaultPublishStages()
        };
      },


      defaultPublishStages() {
        return [
          { key: "source_frozen", label: "冻结源码", status: "pending", message: "等待提交发布请求。" },
          { key: "compile_request_created", label: "创建编译任务", status: "pending", message: "等待创建编译任务。" },
          { key: "source_loaded", label: "读取冻结源码", status: "pending", message: "等待读取冻结 commit 下的源码。" },
          { key: "manifest_checked", label: "校验 manifest", status: "pending", message: "等待校验发布版本 manifest snapshot。" },
          { key: "agent_compiling", label: "智能体编译 EG", status: "pending", message: "等待调用 SKILL 编译智能体。" },
          { key: "artifact_validating", label: "校验 EG artifact", status: "pending", message: "等待执行 formal-v5 校验。" },
          { key: "artifact_emitting", label: "写入编译产物", status: "pending", message: "等待写入 EG 编译产物。" },
          { key: "publish_finalizing", label: "完成发布", status: "pending", message: "等待写入发布终态。" }
        ];
      },


      isPublishInProgress() {
        return this.publishProgress.active && !this.publishProgress.terminal;
      },


      stopPublishProgressWatchers() {
        if (this.publishEventSource) {
          this.publishEventSource.close();
          this.publishEventSource = null;
        }
        if (this.publishPollTimer) {
          window.clearInterval(this.publishPollTimer);
          this.publishPollTimer = null;
        }
      },


      startPublishEventStream(compileRequestId) {
        this.stopPublishProgressWatchers();
        const url = `${this.apiBaseUrl}/compiler/requests/${encodeURIComponent(compileRequestId)}/events`;
        const eventSource = new EventSource(url);
        this.publishEventSource = eventSource;

        const handleEvent = (event) => {
          this.applyPublishProgress(JSON.parse(event.data));
        };
        eventSource.addEventListener("publish.progress", handleEvent);
        eventSource.addEventListener("publish.terminal", handleEvent);
        eventSource.onerror = () => {
          eventSource.close();
          if (this.publishEventSource === eventSource) {
            this.publishEventSource = null;
            this.startPublishProgressPolling(compileRequestId);
          }
        };
      },


      startPublishProgressPolling(compileRequestId) {
        if (this.publishPollTimer) {
          return;
        }

        const poll = async () => {
          try {
            const progress = await this.apiRequest(`/compiler/requests/${compileRequestId}/progress`);
            this.applyPublishProgress(progress);
          } catch (error) {
            this.showNotice("error", error.message || "获取发布进度失败。");
          }
        };
        poll();
        this.publishPollTimer = window.setInterval(poll, 1500);
      },


      async applyPublishProgress(progress) {
        this.publishProgress = {
          active: true,
          compile_request_id: progress.compile_request?.id || this.publishProgress.compile_request_id,
          terminal: Boolean(progress.terminal),
          terminal_status: progress.terminal_status,
          error_message: progress.error_message || "",
          stages: progress.stages || []
        };

        if (!progress.terminal) {
          return;
        }

        this.stopPublishProgressWatchers();
        this.busy.publish = false;
        if (this.currentSkill) {
          await this.loadSkillDetail(this.currentSkill.id);
          this.publishRecordsLoadedSkillId = null;
          await this.loadPublishRecords(this.currentSkill.id);
        }

        if (progress.terminal_status === "succeeded") {
          this.showNotice("success", "发布并编译成功。");
        } else {
          this.showNotice("error", progress.error_message || "发布失败。");
        }
      },


      async publishSkill() {
        if (!this.currentSkill) {
          return;
        }
        if (!this.publishForm.publish_reason) {
          this.showCenterToast("error", "请输入发布说明。");
          return;
        }

        this.busy.publish = true;
        this.clearNotice();
        this.publishProgress = {
          active: true,
          compile_request_id: null,
          terminal: false,
          terminal_status: null,
          error_message: "",
          stages: this.defaultPublishStages().map((stage, index) => index === 0
            ? { ...stage, status: "running", message: "正在提交发布请求..." }
            : stage)
        };

        try {
          const result = await this.apiRequest(`/skills/${this.currentSkill.id}/publish`, {
            method: "POST",
            body: JSON.stringify(this.publishForm)
          });
          this.publishForm = { publish_reason: "" };
          const compileRequestId = result.compile_request?.id;
          if (!compileRequestId) {
            throw new Error("发布任务缺少 compile request。");
          }
          this.publishProgress.compile_request_id = compileRequestId;
          this.startPublishEventStream(compileRequestId);
        } catch (error) {
          const errorMessage = error.message || "发布 Skill 失败。";
          const stages = this.publishProgress.stages.length > 0
            ? this.publishProgress.stages
            : [{ key: "source_frozen", label: "冻结源码", status: "running", message: "" }];
          this.publishProgress = {
            ...this.publishProgress,
            terminal: true,
            terminal_status: "failed",
            error_message: errorMessage,
            stages: stages.map((stage, index) => index === 0
              ? { ...stage, status: "failed", message: errorMessage, finished_at: new Date().toISOString() }
              : stage)
          };
          if (this.currentSkill) {
            this.publishRecordsLoadedSkillId = null;
            await this.loadPublishRecords(this.currentSkill.id);
          }
          this.showNotice("error", errorMessage);
          this.busy.publish = false;
        } finally {
          if (!this.isPublishInProgress()) {
            this.busy.publish = false;
          }
        }
      },


      async refreshCurrentSkill() {
        if (!this.currentSkill) {
          return;
        }

        try {
          await this.loadSkillDetail(this.currentSkill.id);
          if (this.activeDetailTab === "source") {
            await this.loadRepositoryTree(this.currentSkill.id, this.repositoryPath);
          }
          if (this.activeDetailTab === "materials") {
            await this.loadRawMaterials(this.currentSkill.id, true);
          }
          if (this.activeDetailTab === "publish") {
            await this.loadPublishRecords(this.currentSkill.id);
          }
          this.showNotice("success", "已刷新当前 Skill。");
        } catch (error) {
          this.showNotice("error", error.message || "刷新 Skill 失败。");
        }
      },


      async openSkill(skillId) {
        this.activeDetailTab = "overview";
        await this.navigate(buildSkillDetailPath(skillId));
      },


      async openCurrentSkillDebug() {
        if (!this.currentSkill?.id) {
          return;
        }

        this.activeDetailTab = "debug";
        await this.navigate(buildSkillDetailPath(this.currentSkill.id));
      },


      async openCurrentSkillRuntime() {
        if (!this.currentSkill?.id) {
          return;
        }

        this.activeDetailTab = "runtime";
        await this.navigate(buildSkillDetailPath(this.currentSkill.id));
      },


      async selectDetailTab(tabName) {
        this.activeDetailTab = tabName;
        if (!this.currentSkill) {
          return;
        }
        if (this.route.name === "skill-test-scenario-new" && tabName !== "test") {
          window.history.pushState({}, "", buildSkillDetailPath(this.currentSkill.id));
          this.syncRoute();
        }

        try {
          if (tabName === "source") {
            await this.loadRepositoryTree(this.currentSkill.id, this.repositoryPath);
          }
          if (tabName === "materials") {
            await this.loadRawMaterials(this.currentSkill.id);
          }
          if (tabName === "publish") {
            await this.loadPublishRecords(this.currentSkill.id);
          }
          if (tabName === "compiler") {
            await this.loadCompilerRequests(this.currentSkill.id);
          }
          if (tabName === "debug") {
            await this.loadInvocations(this.currentSkill.key);
          }
          if (tabName === "runtime") {
            this.invocationForm.skill_key = this.currentSkill.key;
            await this.loadInvocations(this.currentSkill.key);
          }
          if (tabName === "test") {
            await this.loadSkillTestCases(this.currentSkill.id);
          }
        } catch (error) {
          this.showNotice("error", error.message || "数据加载失败。");
        }
      },


      isDeleteConfirmationValid() {
        return (
          Boolean(this.deleteTargetSkill) &&
          this.deleteForm.confirmation_name === this.deleteTargetSkill.name
        );
      },


      filteredSkills() {
        return this.skills.filter((skill) => {
          const nameQuery = this.filters.search.trim().toLowerCase();
          const nameMatched =
            !nameQuery ||
            skill.name.toLowerCase().includes(nameQuery);

          return (
            nameMatched &&
            this.inDateRange(skill.created_at, this.filters.created_from, this.filters.created_to) &&
            this.inDateRange(skill.latest_published_at, this.filters.published_from, this.filters.published_to)
          );
        });
      },


      clearFilters() {
        this.filters = {
          search: "",
          status: "",
          created_from: "",
          created_to: "",
          published_from: "",
          published_to: ""
        };
        this.loadSkills();
      },


      hasActiveFilters() {
        return Object.values(this.filters).some((value) => Boolean(String(value || "").trim()));
      },


      selectSourceTab(tabName) {
        this.activeSourceTab = tabName;
      },


      currentSourceValue() {
        if (this.activeSourceTab === "README.md") {
          return this.sourceForm.readme_content;
        }

        if (this.activeSourceTab === "SKILL.md") {
          return this.sourceForm.skill_md_content;
        }

        return this.sourceForm.skill_yaml_content;
      },


      updateSourceValue(event) {
        if (this.activeSourceTab === "README.md") {
          this.sourceForm.readme_content = event.target.value;
          return;
        }

        if (this.activeSourceTab === "SKILL.md") {
          this.sourceForm.skill_md_content = event.target.value;
          return;
        }

        this.sourceForm.skill_yaml_content = event.target.value;
      },


      normalizeRepositoryPath(value, allowEmpty = false) {
        const normalized = String(value || "")
          .trim()
          .replace(/\\/g, "/")
          .replace(/\/+/g, "/")
          .replace(/^\/+|\/+$/g, "");
        return normalized || (allowEmpty ? "" : "");
      },


      repositoryBreadcrumbs() {
        const parts = this.repositoryPath ? this.repositoryPath.split("/") : [];
        const breadcrumbs = [{ label: "根目录", path: "" }];
        parts.forEach((part, index) => {
          breadcrumbs.push({
            label: part,
            path: parts.slice(0, index + 1).join("/")
          });
        });
        return breadcrumbs;
      },


      parentRepositoryPath() {
        if (!this.repositoryPath) {
          return "";
        }

        const parts = this.repositoryPath.split("/");
        parts.pop();
        return parts.join("/");
      },


      repositoryEntryIcon(entry) {
        if (entry.type === "tree") {
          return "folder";
        }

        if (entry.name.endsWith(".md")) {
          return "markdown";
        }

        if (entry.name.endsWith(".yaml") || entry.name.endsWith(".yml")) {
          return "description";
        }

        return "draft";
      },


      repositoryEntryLabel(entry) {
        if (entry.type !== "tree" && this.isSystemManifestFile(entry.path)) {
          return "系统";
        }
        return entry.type === "tree" ? "文件夹" : "文件";
      },


      isSystemManifestFile(path = this.repositoryFileForm.path) {
        return this.normalizeRepositoryPath(path).toLowerCase() === "skill.yaml";
      },


      isMarkdownPreview() {
        return this.repositoryPreviewKind() === "markdown";
      },


      repositoryPreviewKind(path = this.repositoryFileForm.path) {
        const normalized = this.normalizeRepositoryPath(path).toLowerCase();
        if (normalized.endsWith(".md") || normalized.endsWith(".markdown")) {
          return "markdown";
        }
        if (normalized.endsWith(".json")) {
          return "json";
        }
        if (normalized.endsWith(".yaml") || normalized.endsWith(".yml")) {
          return "yaml";
        }
        return "text";
      },


      repositoryPreviewHtml() {
        const kind = this.repositoryPreviewKind();
        if (kind === "markdown") {
          return renderMarkdown(this.repositoryFileForm.content);
        }
        if (kind === "json") {
          return `<pre class="source-code-preview"><code>${highlightJson(this.repositoryFileForm.content)}</code></pre>`;
        }
        if (kind === "yaml") {
          return `<pre class="source-code-preview"><code>${highlightYaml(this.repositoryFileForm.content)}</code></pre>`;
        }

        return `<pre class="source-code-preview"><code>${escapeHtml(this.repositoryFileForm.content)}</code></pre>`;
      },


      publishStageIcon(stage) {
        if (stage.status === "succeeded") {
          return "check";
        }
        if (stage.status === "failed") {
          return "close";
        }
        if (stage.status === "running") {
          return "progress_activity";
        }
        return "radio_button_unchecked";
      },


      publishStageTone(stage) {
        if (stage.status === "succeeded") {
          return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
        }
        if (stage.status === "failed") {
          return "border-rose-500/30 bg-rose-500/10 text-rose-200";
        }
        if (stage.status === "running") {
          return "border-sky-500/30 bg-sky-500/10 text-sky-200";
        }
        return "border-slate-700 bg-slate-950/40 text-slate-500";
      },


      filteredPublishRecords() {
        return this.publishRecords.filter((record) => {
          const statusMatched = !this.publishFilters.status || record.publish_status === this.publishFilters.status;
          return (
            statusMatched &&
            this.inDateRange(
              record.published_at || record.created_at,
              this.publishFilters.published_from,
              this.publishFilters.published_to
            )
          );
        });
      },
  };
})();
