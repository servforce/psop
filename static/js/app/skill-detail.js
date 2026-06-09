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
    buildPlatformAgentRunPath,
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

      async loadSkills(options = {}) {
        this.busy.list = true;
        try {
          const params = new URLSearchParams();
          const useFilters = options.useFilters !== false;
          if (useFilters && this.filters.search.trim()) {
            params.set("search", this.filters.search.trim());
          }
          if (useFilters && this.filters.published_state) {
            params.set("is_published", String(this.filters.published_state === "published"));
          }
          const suffix = params.toString() ? `?${params}` : "";
          this.skills = await this.apiRequest(`/pskills${suffix}`);
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
          const created = await this.apiRequest("/pskills", {
            method: "POST",
            body: JSON.stringify(payload)
          });
          this.createForm = { name: "", description: "" };
          this.createModalOpen = false;
          await this.navigate(buildSkillDetailPath(created.id));
          this.showNotice("success", "PSkill 已创建，并已在 GitLab 中初始化。");
        } catch (error) {
          this.showNotice("error", error.message || "创建 PSkill 失败。");
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
          await this.apiRequest(`/pskills/${deletedSkill.id}`, {
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

          this.showCenterToast("success", "PSkill 已删除，对应 GitLab 仓库项目已归档。");
        } catch (error) {
          this.showCenterToast("error", error.message || "删除 PSkill 失败。");
        } finally {
          this.busy.delete = false;
        }
      },


      async loadSkillDetail(skillId, options = {}) {
        this.busy.detail = true;
        try {
          const detail = await this.apiRequest(`/pskills/${skillId}`);

          this.currentSkill = detail;
          this.metadataForm = {
            name: detail.name,
            description: detail.description
          };
          this.resetLazyDetailState(skillId);
          if (!["overview", "source", "materials", "publish", "compiler", "runtime", "test"].includes(this.activeDetailTab)) {
            this.activeDetailTab = "overview";
          }
          if (this.activeDetailTab === "materials") {
            await this.loadMaterials(detail.id);
          }
          if (this.activeDetailTab === "publish") {
            await this.loadPublishWorkspaceData(detail.id);
          }
          if (this.activeDetailTab === "compiler") {
            await this.loadCompilerRequests(detail.id);
          }
          if (this.activeDetailTab === "runtime") {
            this.invocationForm.skill_key = detail.key;
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
        this.materialsLoadedSkillId = null;
        this.materials = [];
        this.materialDetail = null;
        this.materialAnalysis = null;
        this.materialDetailTab = "analysis";
        this.materialUploadFiles = [];
        this.materialUploadItems = [];
        this.materialUploadSelectedIndex = 0;
        this.materialUploadNameAutoFilled = false;
        this.materialUploadProgress = null;
        this.materialUploadError = "";
        this.materialUploadForm = {
          name: "",
          description: "",
          source_note: ""
        };
        this.materialUploadModalOpen = false;
        this.materialGenerateModalOpen = false;
        this.materialGenerateForm = {
          user_description: ""
        };
        this.materialGenerationResult = null;
        this.closeMaterialImagePreview();
        this.publishRecordsLoadedSkillId = null;
        this.publishRecords = [];
        this.pskillVersionsLoadedSkillId = null;
        this.pskillVersions = [];
        this.publishGateResult = null;
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
        this.resetCompilerArtifactState();
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
          const source = await this.apiRequest(`/pskills/${skillId}/source`);
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


      async loadPublishRecords(skillId, force = false) {
        if (!force && this.publishRecordsLoadedSkillId === skillId) {
          return;
        }

        this.busy.publishRecords = true;
        try {
          this.publishRecords = await this.apiRequest(`/pskills/${skillId}/publishes`);
          this.publishRecordsLoadedSkillId = skillId;
        } finally {
          this.busy.publishRecords = false;
        }
      },

      async loadSkillVersions(skillId, force = false) {
        if (!force && this.pskillVersionsLoadedSkillId === skillId) {
          return;
        }

        this.busy.pskillVersions = true;
        try {
          this.pskillVersions = await this.apiRequest(`/pskills/${skillId}/versions`);
          this.pskillVersionsLoadedSkillId = skillId;
        } finally {
          this.busy.pskillVersions = false;
        }
      },

      async loadPublishWorkspaceData(skillId, force = false) {
        await Promise.all([
          this.loadPublishRecords(skillId, force),
          this.loadSkillVersions(skillId, force)
        ]);
      },

      async runPublishGate() {
        if (!this.currentSkill?.id) {
          return;
        }

        this.busy.publishGate = true;
        this.clearNotice();
        try {
          const payload = {
            pskill_id: this.currentSkill.id,
            pskill_version_id: this.currentSkill.latest_published_version?.id || null
          };
          this.publishGateResult = await this.apiRequest(`/pskills/${this.currentSkill.id}/publish-gate`, {
            method: "POST",
            body: JSON.stringify(payload)
          });
          const message = this.publishGateResult.status === "passed"
            ? "发布门禁通过。"
            : this.publishGateResult.status === "failed"
              ? "发布门禁失败，请检查阻塞项。"
              : "发布门禁需要人工复核。";
          this.showNotice(this.publishGateResult.status === "failed" ? "error" : "success", message);
        } catch (error) {
          this.showNotice("error", error.message || "运行发布门禁失败。");
        } finally {
          this.busy.publishGate = false;
        }
      },


      async loadMaterials(skillId, force = false) {
        if (!force && this.materialsLoadedSkillId === skillId) {
          return;
        }

        this.busy.materials = true;
        try {
          this.materials = await this.apiRequest(`/pskills/${skillId}/materials`);
          this.materialsLoadedSkillId = skillId;
          if (this.materialDetail && !this.materials.some((material) => material.id === this.materialDetail.id)) {
            this.materialDetail = null;
            this.materialAnalysis = null;
          }
          if (!this.materialDetail && this.materials.length > 0) {
            await this.openMaterialDetail(this.materials[0]);
          }
        } finally {
          this.busy.materials = false;
        }
      },


      async openMaterialDetail(material) {
        if (!this.currentSkill || !material?.id) {
          return;
        }

        this.busy.materialDetail = true;
        try {
          this.materialDetailTab = "analysis";
          this.materialDetail = await this.apiRequest(`/pskills/${this.currentSkill.id}/materials/${material.id}`);
          await this.loadMaterialAnalysis(this.materialDetail.id);
        } finally {
          this.busy.materialDetail = false;
        }
      },


      async loadMaterialAnalysis(materialId) {
        if (!this.currentSkill || !materialId) {
          return;
        }
        try {
          this.materialAnalysis = await this.apiRequest(`/pskills/${this.currentSkill.id}/materials/${materialId}/analysis`);
        } catch (error) {
          this.materialAnalysis = null;
        }
      },


      async analyzeMaterial(material = this.materialDetail) {
        if (!this.currentSkill || !material) {
          return;
        }
        const analysisStatus = material.analysis_status || material.status;
        if (material.status === "processing" || ["pending", "running"].includes(analysisStatus)) {
          this.showNotice("error", "素材正在分析中，不能重复解析。");
          return;
        }
        const materialId = material.id;
        this.busy.materialAnalyze = true;
        this.clearNotice();
        try {
          const analysis = await this.apiRequest(`/pskills/${this.currentSkill.id}/materials/${materialId}/analyze`, {
            method: "POST"
          });
          await this.loadMaterials(this.currentSkill.id, true);
          if (this.materialDetail?.id === materialId) {
            this.materialAnalysis = analysis;
            this.materialDetail = await this.apiRequest(`/pskills/${this.currentSkill.id}/materials/${materialId}`);
          }
          this.showNotice("success", "素材分析任务已提交。");
        } catch (error) {
          this.showNotice("error", error.message || "提交素材分析失败。");
        } finally {
          this.busy.materialAnalyze = false;
        }
      },


      handleMaterialFileChange(event) {
        const files = Array.from(event?.target?.files || []);
        if (files.length === 0) {
          return;
        }
        this.syncMaterialUploadSelectedItem();
        const nextItems = files.map((file) => ({
          file,
          name: file.name,
          description: "",
          source_note: ""
        }));
        const startIndex = this.materialUploadItems.length;
        this.materialUploadItems = [...this.materialUploadItems, ...nextItems];
        this.materialUploadFiles = this.materialUploadItems.map((item) => item.file);
        this.materialUploadSelectedIndex = startIndex;
        this.materialUploadForm = this.materialUploadItemForm(this.materialUploadSelectedItem());
        this.materialUploadError = "";
        this.materialUploadNameAutoFilled = nextItems.length === 1;
        if (this.$refs.materialFileInput) {
          this.$refs.materialFileInput.value = "";
        }
      },


      openMaterialUploadModal() {
        this.materialUploadFiles = [];
        this.materialUploadItems = [];
        this.materialUploadSelectedIndex = 0;
        this.materialUploadNameAutoFilled = false;
        this.materialUploadProgress = null;
        this.materialUploadError = "";
        this.materialUploadForm = {
          name: "",
          description: "",
          source_note: ""
        };
        this.materialUploadModalOpen = true;
      },


      closeMaterialUploadModal() {
        if (this.busy.materialUpload) {
          return;
        }
        this.materialUploadModalOpen = false;
      },


      materialUploadSelectedItem() {
        return this.materialUploadItems[this.materialUploadSelectedIndex] || null;
      },


      materialUploadItemForm(item) {
        return {
          name: item?.name || "",
          description: item?.description || "",
          source_note: item?.source_note || ""
        };
      },


      selectMaterialUploadItem(index) {
        if (this.busy.materialUpload || index < 0 || index >= this.materialUploadItems.length) {
          return;
        }
        this.syncMaterialUploadSelectedItem();
        this.materialUploadSelectedIndex = index;
        this.materialUploadForm = this.materialUploadItemForm(this.materialUploadSelectedItem());
        this.materialUploadNameAutoFilled = false;
      },


      syncMaterialUploadSelectedItem() {
        const item = this.materialUploadSelectedItem();
        if (!item) {
          return;
        }
        ["name", "description", "source_note"].forEach((key) => {
          item[key] = this.materialUploadForm[key] || "";
        });
      },


      removeMaterialUploadItem(index) {
        if (this.busy.materialUpload || index < 0 || index >= this.materialUploadItems.length) {
          return;
        }
        this.syncMaterialUploadSelectedItem();
        this.materialUploadItems.splice(index, 1);
        this.materialUploadFiles = this.materialUploadItems.map((item) => item.file);
        if (this.materialUploadSelectedIndex >= this.materialUploadItems.length) {
          this.materialUploadSelectedIndex = Math.max(0, this.materialUploadItems.length - 1);
        } else if (index < this.materialUploadSelectedIndex) {
          this.materialUploadSelectedIndex -= 1;
        }
        this.materialUploadForm = this.materialUploadItemForm(this.materialUploadSelectedItem());
        if (this.materialUploadItems.length === 0 && this.$refs.materialFileInput) {
          this.$refs.materialFileInput.value = "";
        }
      },


      async submitMaterial() {
        if (!this.currentSkill) {
          return;
        }
        this.materialUploadError = "";
        this.syncMaterialUploadSelectedItem();
        const selectedItems = this.materialUploadItems.length > 0
          ? this.materialUploadItems
          : Array.from(this.$refs.materialFileInput?.files || []).map((file) => ({
            file,
            name: file.name,
            description: "",
            source_note: ""
          }));
        if (selectedItems.length === 0) {
          this.materialUploadError = "请选择要上传的素材文件。";
          return;
        }

        this.busy.materialUpload = true;
        this.clearNotice();
        const createdMaterials = [];
        const failedUploads = [];
        try {
          for (const [index, item] of selectedItems.entries()) {
            const selectedFile = item.file;
            this.materialUploadProgress = {
              current: index + 1,
              total: selectedItems.length,
              filename: selectedFile.name
            };
            const formData = new FormData();
            formData.append("file", selectedFile);
            ["name", "description", "source_note"].forEach((key) => {
              const value = item[key];
              if (value) {
                formData.append(key, value);
              }
            });
            try {
              const created = await this.apiRequest(`/pskills/${this.currentSkill.id}/materials`, {
                method: "POST",
                body: formData
              });
              createdMaterials.push(created);
            } catch (error) {
              failedUploads.push({
                ...item,
                message: error.message || "保存素材失败。"
              });
            }
          }

          if (createdMaterials.length > 0) {
            await this.loadMaterials(this.currentSkill.id, true);
          }
          if (failedUploads.length > 0) {
            this.materialUploadItems = failedUploads.map(({ message, ...item }) => item);
            this.materialUploadFiles = this.materialUploadItems.map((item) => item.file);
            this.materialUploadSelectedIndex = 0;
            this.materialUploadForm = this.materialUploadItemForm(this.materialUploadSelectedItem());
            this.materialUploadNameAutoFilled = false;
            if (this.$refs.materialFileInput) {
              this.$refs.materialFileInput.value = "";
            }
            const prefix = createdMaterials.length > 0 ? `已上传 ${createdMaterials.length} 个素材，` : "";
            const firstFailure = failedUploads[0];
            const suffix = failedUploads.length === 1
              ? `${firstFailure.file.name} 上传失败：${firstFailure.message}`
              : `${failedUploads.length} 个文件上传失败，首个失败：${firstFailure.file.name}：${firstFailure.message}`;
            const message = `${prefix}${suffix}`;
            this.materialUploadError = message;
            this.showNotice("error", message);
            return;
          }

          this.materialUploadFiles = [];
          this.materialUploadItems = [];
          this.materialUploadSelectedIndex = 0;
          this.materialUploadNameAutoFilled = false;
          this.materialUploadProgress = null;
          this.materialUploadForm = {
            name: "",
            description: "",
            source_note: ""
          };
          if (this.$refs.materialFileInput) {
            this.$refs.materialFileInput.value = "";
          }
          this.materialUploadModalOpen = false;
          if (createdMaterials.length > 0) {
            await this.loadMaterials(this.currentSkill.id, true);
          }
          const lastCreated = createdMaterials[createdMaterials.length - 1];
          if (lastCreated) {
            await this.openMaterialDetail(lastCreated);
          }
          const noticeType = createdMaterials.some((material) => material.status === "failed") ? "error" : "success";
          this.showNotice(noticeType, this.materialUploadSuccessMessage(createdMaterials));
        } catch (error) {
          const message = error.message || "保存素材失败。";
          this.materialUploadError = message;
          this.showNotice("error", message);
        } finally {
          this.materialUploadProgress = null;
          this.busy.materialUpload = false;
        }
      },


      materialUploadSuccessMessage(createdMaterials) {
        if (!Array.isArray(createdMaterials) || createdMaterials.length === 0) {
          return "素材已保存。";
        }
        if (createdMaterials.length === 1) {
          const created = createdMaterials[0];
          const statusMessages = {
            ready: "素材已保存并解析完成。",
            processing: "素材已保存，视频分析已开始。",
            failed: created.error_message || "素材已保存，但解析失败。"
          };
          return statusMessages[created.status] || "素材已保存。";
        }
        const processingCount = createdMaterials.filter((material) => material.status === "processing").length;
        const failedCount = createdMaterials.filter((material) => material.status === "failed").length;
        const readyCount = createdMaterials.filter((material) => material.status === "ready").length;
        const parts = [`已上传 ${createdMaterials.length} 个素材`];
        if (readyCount) {
          parts.push(`${readyCount} 个已解析`);
        }
        if (processingCount) {
          parts.push(`${processingCount} 个视频已开始分析`);
        }
        if (failedCount) {
          parts.push(`${failedCount} 个解析失败`);
        }
        return `${parts.join("，")}。`;
      },


      async deleteMaterial(material) {
        if (!this.currentSkill || !material?.id) {
          return;
        }

        this.busy.materialDelete = true;
        this.clearNotice();
        try {
          await this.apiRequest(`/pskills/${this.currentSkill.id}/materials/${material.id}`, {
            method: "DELETE"
          });
          if (this.materialDetail?.id === material.id) {
            this.materialDetail = null;
          }
          await this.loadMaterials(this.currentSkill.id, true);
          this.showNotice("success", "素材已移除。");
        } catch (error) {
          this.showNotice("error", error.message || "移除素材失败。");
        } finally {
          this.busy.materialDelete = false;
        }
      },


      allMaterialsReady() {
        return this.materials.length > 0 && this.materials.every((material) => material.status === "ready");
      },


      readyVideoMaterials() {
        return this.materials.filter((material) => this.isVideoMaterial(material) && material.status === "ready");
      },


      hasReadyVideoMaterial() {
        return this.readyVideoMaterials().length > 0;
      },


      canGenerateSkillDraftFromMaterials() {
        return this.allMaterialsReady() && this.hasReadyVideoMaterial();
      },


      materialDraftMaterialIds() {
        return (this.materials || []).map((material) => material.id).filter(Boolean);
      },


      materialDraftPatchFileChanges() {
        const changes = this.materialGenerationResult?.patch?.file_changes;
        return Array.isArray(changes) ? changes : [];
      },


      canApplyMaterialDraftPatch() {
        if (this.materialGenerationResult?.status !== "patch_proposed") {
          return false;
        }
        return this.materialDraftPatchFileChanges().some(
          (change) => change?.path && typeof change.proposed_content === "string"
        );
      },


      openMaterialGenerateModal() {
        if (this.materials.length === 0) {
          this.showCenterToast("error", "请先上传素材。");
          return;
        }
        if (!this.allMaterialsReady()) {
          this.showCenterToast("error", "请等待全部素材分析完成后再生成。");
          return;
        }
        if (!this.hasReadyVideoMaterial()) {
          this.showCenterToast("error", "请至少上传一个已分析完成的视频素材。");
          return;
        }
        this.materialGenerateForm = {
          user_description: ""
        };
        this.materialGenerationResult = null;
        this.materialGenerateModalOpen = true;
      },


      closeMaterialGenerateModal() {
        if (this.busy.materialGenerate || this.busy.materialApply) {
          return;
        }
        this.materialGenerateModalOpen = false;
      },


      async generateSkillDraftFromMaterials() {
        if (!this.currentSkill || !this.canGenerateSkillDraftFromMaterials()) {
          return;
        }
        if (!this.materialGenerateForm.user_description.trim()) {
          this.showCenterToast("error", "请输入生成描述。");
          return;
        }

        this.busy.materialGenerate = true;
        this.clearNotice();
        try {
          const skillId = this.currentSkill.id;
          const result = await this.apiRequest(`/pskills/${skillId}/draft/generate`, {
            method: "POST",
            body: JSON.stringify({
              user_description: this.materialGenerateForm.user_description.trim(),
              material_ids: this.materialDraftMaterialIds(),
              base_commit_sha: this.currentSkill.latest_draft_head_sha
            })
          });
          this.materialGenerationResult = result;
          if (result.status === "patch_proposed") {
            this.showCenterToast("success", "PSkill 草稿 patch 已生成。");
            this.showNotice("success", "pskill.builder 已生成可审查 patch，请确认后应用到 GitLab draft。");
          } else if (result.status === "failed") {
            this.showCenterToast("error", result.error_message || "生成 PSkill 草稿失败。");
          } else {
            this.showCenterToast("success", "PSkill 草稿已生成。");
          }
        } catch (error) {
          this.showCenterToast("error", error.message || "生成 PSkill 草稿失败。");
        } finally {
          this.busy.materialGenerate = false;
        }
      },


      async applyMaterialDraftPatch() {
        if (!this.currentSkill || !this.canApplyMaterialDraftPatch()) {
          return;
        }

        const files = {};
        for (const change of this.materialDraftPatchFileChanges()) {
          if (change?.path && typeof change.proposed_content === "string") {
            files[change.path] = change.proposed_content;
          }
        }

        this.busy.materialApply = true;
        this.clearNotice();
        try {
          const skillId = this.currentSkill.id;
          const result = await this.apiRequest(`/pskills/${skillId}/draft/apply-patch`, {
            method: "POST",
            body: JSON.stringify({
              base_commit_sha: this.materialGenerationResult.base_commit_sha,
              files,
              builder_agent_run_id: this.materialGenerationResult.agent_run?.id || null,
              commit_message: "Apply pskill.builder material draft"
            })
          });
          this.materialGenerationResult = {
            ...this.materialGenerationResult,
            status: "applied",
            applied: result.applied,
            changed_files: result.changed_files || [],
            committed_commit_sha: result.committed_commit_sha,
            source: result.source
          };
          this.sourceLoadedSkillId = null;
          this.repositoryLoadedSkillId = null;
          this.currentSkill = await this.apiRequest(`/pskills/${skillId}`);
          await this.loadMaterials(skillId, true);
          this.showCenterToast("success", "PSkill 草稿已应用。");
          this.showNotice("success", "pskill.builder patch 已应用到 GitLab draft。");
        } catch (error) {
          this.showCenterToast("error", error.message || "应用 PSkill 草稿失败。");
        } finally {
          this.busy.materialApply = false;
        }
      },


      materialKindLabel(value) {
        const labels = {
          text: "文本",
          markdown: "Markdown",
          pdf: "PDF",
          image: "图片",
          audio: "音频",
          video: "视频",
          file: "文件"
        };
        return labels[value] || value || "素材";
      },


      materialKindIcon(value) {
        const icons = {
          text: "description",
          markdown: "article",
          pdf: "picture_as_pdf",
          image: "image",
          audio: "graphic_eq",
          video: "movie",
          file: "attach_file"
        };
        return icons[value] || "draft";
      },


      materialContentUrl(material) {
        if (!this.currentSkill || !material?.id) {
          return "";
        }
        return `${this.apiBaseUrl}/pskills/${encodeURIComponent(this.currentSkill.id)}/materials/${encodeURIComponent(material.id)}/content`;
      },


      materialDerivedAssetContentUrl(asset) {
        if (!this.currentSkill || !this.materialDetail?.id || !asset?.id) {
          return "";
        }
        return `${this.apiBaseUrl}/pskills/${encodeURIComponent(this.currentSkill.id)}/materials/${encodeURIComponent(this.materialDetail.id)}/derived-assets/${encodeURIComponent(asset.id)}/content`;
      },


      materialVisibleEvidenceItems() {
        const result = this.materialDetail?.analysis_result || {};
        const items = Array.isArray(result.evidence_items) ? result.evidence_items : [];
        const hasTextContent = Boolean(String(result.content?.text || "").trim());
        const derivedAssets = Array.isArray(this.materialDetail?.derived_assets)
          ? this.materialDetail.derived_assets
          : [];
        const derivedAssetIds = new Set(derivedAssets.map((asset) => asset.id).filter(Boolean));

        return items.filter((item) => {
          if (hasTextContent && item?.kind === "audio_transcript") {
            return false;
          }
          if (derivedAssetIds.has(item?.asset_id)) {
            return false;
          }
          if (derivedAssets.length > 0 && item?.kind === "video_keyframe") {
            return false;
          }
          return true;
        });
      },


      openMaterialImagePreview(asset) {
        const src = this.materialDerivedAssetContentUrl(asset);
        if (!src) {
          return;
        }
        this.materialImagePreview = {
          open: true,
          src,
          title: asset.label || asset.filename || "派生资产",
          description: asset.label || "",
          timestamp_ms: asset.timestamp_ms ?? null,
          frame_source: asset.asset_metadata?.frame_source || ""
        };
      },


      closeMaterialImagePreview() {
        this.materialImagePreview = {
          open: false,
          src: "",
          title: "",
          description: "",
          timestamp_ms: null,
          frame_source: ""
        };
      },


      materialFrameSourceLabel(value) {
        if (value === "scene_change") {
          return "场景变化";
        }
        if (value === "timeline_sample") {
          return "时间采样";
        }
        return value ? "候选帧" : "";
      },


      selectMaterialDetailTab(tabName) {
        if (["analysis", "preview"].includes(tabName)) {
          this.materialDetailTab = tabName;
        }
      },


      isVideoMaterial(material) {
        return material?.material_kind === "video" || String(material?.mime_type || "").startsWith("video/");
      },


      canPreviewMaterial(kind, material = this.materialDetail) {
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
            !this.canPreviewMaterial("image", material) &&
            !this.canPreviewMaterial("audio", material) &&
            !this.canPreviewMaterial("video", material) &&
            !this.canPreviewMaterial("pdf", material);
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
          const tree = await this.apiRequest(`/pskills/${skillId}/repository/tree${suffix}`);
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
          const file = await this.apiRequest(`/pskills/${this.currentSkill.id}/repository/files?${params}`);
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
          const saved = await this.apiRequest(`/pskills/${skillId}/repository/files`, {
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
              ? `/pskills/${skillId}/repository/folders`
              : `/pskills/${skillId}/repository/files`;
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
          await this.apiRequest(`/pskills/${this.currentSkill.id}`, {
            method: "PATCH",
            body: JSON.stringify(this.metadataForm)
          });
          await this.loadSkillDetail(this.currentSkill.id);
          this.showNotice("success", "PSkill 基本信息已更新。");
        } catch (error) {
          this.showNotice("error", error.message || "更新 PSkill 基本信息失败。");
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
          const saved = await this.apiRequest(`/pskills/${this.currentSkill.id}/source`, {
            method: "PUT",
            body: JSON.stringify(this.sourceForm)
          });
          this.sourceForm.base_commit_sha = saved.head_commit_sha;
          await this.loadSkillDetail(this.currentSkill.id);
          await this.loadSkillSource(this.currentSkill.id);
          this.showNotice("success", "PSkill 源码已提交到 GitLab。");
        } catch (error) {
          this.showNotice("error", error.message || "保存 PSkill source 失败。");
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
        this.disconnectPublishActivityWebSocket();
        if (this.publishEventSource) {
          this.publishEventSource.close();
          this.publishEventSource = null;
        }
        if (this.publishPollTimer) {
          window.clearInterval(this.publishPollTimer);
          this.publishPollTimer = null;
        }
      },


      connectPublishActivityWebSocket(skillId, compileRequestId = "") {
        if (!skillId) {
          return false;
        }
        if (typeof WebSocket === "undefined") {
          return false;
        }
        if (this.publishActivityWs && this.publishActivityWsSkillId === skillId && this.publishActivityWs.readyState === WebSocket.OPEN) {
          return true;
        }

        this.disconnectPublishActivityWebSocket();
        const socket = new WebSocket(resolveWsUrl(this.apiBaseUrl, `/ws/pskills/${encodeURIComponent(skillId)}/activity`));
        this.publishActivityWs = socket;
        this.publishActivityWsSkillId = skillId;
        socket.addEventListener("message", (event) => {
          try {
            const message = JSON.parse(event.data);
            if (message.event_type === "pskill.activity.snapshot") {
              this.applyPublishActivitySnapshot(message.payload, compileRequestId);
            }
            if (message.event_type === "pskill.activity.error") {
              this.showNotice("error", message.payload?.message || "获取 Skill 活动流失败。");
              this.disconnectPublishActivityWebSocket();
              if (compileRequestId) {
                this.startPublishProgressPolling(compileRequestId);
              }
            }
          } catch {
            // Ignore malformed activity messages; polling/SSE remains the recovery path.
          }
        });
        socket.addEventListener("error", () => {
          this.disconnectPublishActivityWebSocket();
          if (compileRequestId) {
            this.startPublishProgressPolling(compileRequestId);
          }
        });
        return true;
      },


      disconnectPublishActivityWebSocket() {
        if (this.publishActivityWs) {
          this.publishActivityWs.close();
        }
        this.publishActivityWs = null;
        this.publishActivityWsSkillId = "";
      },


      applyPublishActivitySnapshot(snapshot, expectedCompileRequestId = "") {
        if (!snapshot || typeof snapshot !== "object") {
          return;
        }
        if (Array.isArray(snapshot.versions)) {
          this.pskillVersions = snapshot.versions;
          this.pskillVersionsLoadedSkillId = snapshot.pskill?.id || this.pskillVersionsLoadedSkillId;
        }
        if (Array.isArray(snapshot.publishes)) {
          this.publishRecords = snapshot.publishes;
          this.publishRecordsLoadedSkillId = snapshot.pskill?.id || this.publishRecordsLoadedSkillId;
        }

        const compileRequests = Array.isArray(snapshot.compile_requests) ? snapshot.compile_requests : [];
        const compileRequest = compileRequests.find((item) => item.id === expectedCompileRequestId) || compileRequests[0];
        if (!compileRequest) {
          return;
        }
        this.applyPublishProgress({
          compile_request: { id: compileRequest.id },
          terminal: Boolean(compileRequest.progress?.terminal),
          terminal_status: compileRequest.progress?.terminal_status,
          error_message: compileRequest.progress?.error_message || compileRequest.error_message || "",
          stages: compileRequest.progress?.stages || []
        });
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
          const result = await this.apiRequest(`/pskills/${this.currentSkill.id}/publish`, {
            method: "POST",
            body: JSON.stringify(this.publishForm)
          });
          this.publishForm = { publish_reason: "" };
          const compileRequestId = result.compile_request?.id;
          if (!compileRequestId) {
            throw new Error("发布任务缺少 compile request。");
          }
          this.publishProgress.compile_request_id = compileRequestId;
          if (!this.connectPublishActivityWebSocket(this.currentSkill.id, compileRequestId)) {
            this.startPublishEventStream(compileRequestId);
          }
        } catch (error) {
          const errorMessage = error.message || "发布 PSkill 失败。";
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
            await this.loadPublishWorkspaceData(this.currentSkill.id, true);
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
            await this.loadMaterials(this.currentSkill.id, true);
          }
          if (this.activeDetailTab === "publish") {
            await this.loadPublishWorkspaceData(this.currentSkill.id, true);
          }
          this.showNotice("success", "已刷新当前 PSkill。");
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

        this.activeDetailTab = "runtime";
        await this.navigate(buildSkillDetailPath(this.currentSkill.id));
      },


      async openCurrentSkillCompiler() {
        if (!this.currentSkill?.id) {
          return;
        }

        this.activeDetailTab = "compiler";
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
            await this.loadMaterials(this.currentSkill.id);
          }
          if (tabName === "publish") {
            await this.loadPublishWorkspaceData(this.currentSkill.id);
          }
          if (tabName === "compiler") {
            await this.loadCompilerRequests(this.currentSkill.id);
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
          const publishedState = this.filters.published_state;
          const publishedMatched =
            !publishedState ||
            (publishedState === "published" && this.isSkillPublished(skill)) ||
            (publishedState === "unpublished" && !this.isSkillPublished(skill));

          return (
            nameMatched &&
            publishedMatched &&
            this.inDateRange(skill.created_at, this.filters.created_from, this.filters.created_to)
          );
        });
      },


      isSkillPublished(skill) {
        if (typeof skill?.is_published === "boolean") {
          return skill.is_published;
        }

        return Boolean(skill?.latest_published_commit_sha || skill?.latest_published_at);
      },


      skillPublishStatus(skill) {
        return this.isSkillPublished(skill) ? "published" : "unpublished";
      },


      clearFilters() {
        this.filters = {
          search: "",
          published_state: "",
          created_from: "",
          created_to: ""
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

      publishGateWarnings() {
        return this.publishGateResult?.result_json?.warnings || [];
      },

      publishGateBlockingFindings() {
        return this.publishGateResult?.result_json?.blocking_findings || [];
      },

      publishGateScenarioResults(gate = this.publishGateResult) {
        const results = gate?.result_json?.coverage?.scenario_results;
        return Array.isArray(results) ? results : [];
      },

      publishGateTestRunEvidence(gate = this.publishGateResult) {
        const testRunId = String(gate?.test_run_id || "").trim();
        const results = this.publishGateScenarioResults(gate);
        const matched = results.find((item) => String(item?.latest_run_id || "").trim() === testRunId);
        const fallback = results.find((item) => String(item?.latest_run_id || "").trim());
        const item = matched || fallback || null;
        const latestRunId = String(item?.latest_run_id || testRunId || "").trim();
        const scenarioId = String(item?.scenario_id || "").trim();
        if (!latestRunId || !scenarioId) {
          return null;
        }
        return {
          scenario_id: scenarioId,
          test_run_id: latestRunId,
          agent_run_id: String(item?.agent_run_id || "").trim(),
          run_id: String(item?.run_id || "").trim()
        };
      },

      publishGateTestRunReviewPath(gate = this.publishGateResult) {
        const evidence = this.publishGateTestRunEvidence(gate);
        if (!this.currentSkill?.id || !evidence) {
          return "";
        }
        return buildSkillTestScenarioRunReviewPath(this.currentSkill.id, evidence.scenario_id, evidence.test_run_id);
      },

      openPublishGateTestRunReview(gate = this.publishGateResult) {
        const path = this.publishGateTestRunReviewPath(gate);
        if (!path) {
          return;
        }
        this.navigate(path);
      },

      publishGateTesterAgentRunPath(gate = this.publishGateResult) {
        const evidence = this.publishGateTestRunEvidence(gate);
        if (!evidence?.agent_run_id) {
          return "";
        }
        return buildPlatformAgentRunPath(evidence.agent_run_id, { tab: "events" });
      },

      openPublishGateTesterAgentRun(gate = this.publishGateResult) {
        const path = this.publishGateTesterAgentRunPath(gate);
        if (!path) {
          return;
        }
        this.navigate(path);
      },

      skillVersionBuilderAgentRunId(version = this.currentSkill?.current_draft_version) {
        return String(version?.builder_agent_run_id || version?.builderAgentRunId || "").trim();
      },

      skillVersionBuilderAgentRunPath(version = this.currentSkill?.current_draft_version, focus = { tab: "events" }) {
        const agentRunId = this.skillVersionBuilderAgentRunId(version);
        if (!agentRunId) {
          return "";
        }
        return buildPlatformAgentRunPath(agentRunId, focus);
      },

      openSkillVersionBuilderAgentRun(version = this.currentSkill?.current_draft_version, focus = { tab: "events" }) {
        const path = this.skillVersionBuilderAgentRunPath(version, focus);
        if (!path) {
          return;
        }
        this.navigate(path);
      },

      publishGateReplayPath(gate = this.publishGateResult) {
        const evidence = this.publishGateTestRunEvidence(gate);
        if (!evidence?.run_id) {
          return "";
        }
        return this.currentSkill?.id
          ? buildSkillReplayPath(this.currentSkill.id, evidence.run_id)
          : buildReplayPath(evidence.run_id);
      },

      openPublishGateReplay(gate = this.publishGateResult) {
        const path = this.publishGateReplayPath(gate);
        if (!path) {
          return;
        }
        this.navigate(path);
      },

      publishGateCompileArtifactId(gate = this.publishGateResult) {
        return String(gate?.result_json?.compile_artifact_id || "").trim();
      },
  };
})();
