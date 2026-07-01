(function () {
  function normalizePath(pathname) {
    if (!pathname || pathname === "/") {
      return "/";
    }

    return pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  }

  function resolveAdminRoute(pathname) {
    const normalized = normalizePath(pathname);
    if (normalized === "/" || normalized === "/admin" || normalized === "/admin/skills") {
      return { name: "skills-list", params: {} };
    }

    if (normalized === "/admin/tasks") {
      return { name: "tasks-list", params: {} };
    }

    const detailMatch = normalized.match(/^\/admin\/skills\/([^/]+)$/);
    if (detailMatch) {
      return {
        name: "skill-detail",
        params: { skillId: detailMatch[1] }
      };
    }

    const skillRunLiveMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/live$/);
    if (skillRunLiveMatch) {
      return {
        name: "skill-run-live",
        params: { skillId: skillRunLiveMatch[1], runId: skillRunLiveMatch[2] }
      };
    }

    const skillRunReplayMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/live\/replay$/);
    if (skillRunReplayMatch) {
      return {
        name: "skill-run-live",
        params: { skillId: skillRunReplayMatch[1], runId: skillRunReplayMatch[2], view: "replay" }
      };
    }

    const skillDebugRunLiveMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/debug\/runs\/([^/]+)\/live$/);
    if (skillDebugRunLiveMatch) {
      return {
        name: "skill-debug-live",
        params: { skillId: skillDebugRunLiveMatch[1], runId: skillDebugRunLiveMatch[2] }
      };
    }

    const skillReplayRunMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/runs\/([^/]+)\/replay$/);
    if (skillReplayRunMatch) {
      return {
        name: "skill-run-live",
        params: { skillId: skillReplayRunMatch[1], runId: skillReplayRunMatch[2], view: "replay" }
      };
    }

    const skillTestRunReviewMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/([^/]+)\/runs\/([^/]+)\/review$/);
    if (skillTestRunReviewMatch) {
      return {
        name: "skill-test-scenario-review",
        params: { skillId: skillTestRunReviewMatch[1], scenarioId: skillTestRunReviewMatch[2], scenarioRunId: skillTestRunReviewMatch[3] }
      };
    }

    const skillTestNewMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/new$/);
    if (skillTestNewMatch) {
      return {
        name: "skill-test-scenario-new",
        params: { skillId: skillTestNewMatch[1] }
      };
    }

    const skillTestScenarioMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/tests\/([^/]+)$/);
    if (skillTestScenarioMatch) {
      return {
        name: "skill-test-scenario",
        params: { skillId: skillTestScenarioMatch[1], scenarioId: skillTestScenarioMatch[2] }
      };
    }

    const skillCompilerArtifactMatch = normalized.match(/^\/admin\/skills\/([^/]+)\/compiler\/artifacts\/([^/]+)$/);
    if (skillCompilerArtifactMatch) {
      return {
        name: "skill-compiler-artifact",
        params: { skillId: skillCompilerArtifactMatch[1], artifactId: skillCompilerArtifactMatch[2] }
      };
    }

    if (normalized === "/admin/compiler") {
      return { name: "compiler-list", params: {} };
    }

    if (normalized === "/admin/agent-prompts") {
      return { name: "agent-prompts-list", params: {} };
    }

    const agentPromptMatch = normalized.match(/^\/admin\/agent-prompts\/([^/]+)$/);
    if (agentPromptMatch) {
      return {
        name: "agent-prompt-detail",
        params: { definitionId: agentPromptMatch[1] }
      };
    }

    const compilerArtifactMatch = normalized.match(/^\/admin\/compiler\/artifacts\/([^/]+)$/);
    if (compilerArtifactMatch) {
      return {
        name: "compiler-artifact",
        params: { artifactId: compilerArtifactMatch[1] }
      };
    }

    if (normalized === "/admin/invocations") {
      return { name: "invocations-list", params: {} };
    }

    const runLiveMatch = normalized.match(/^\/admin\/runs\/([^/]+)\/live$/);
    if (runLiveMatch) {
      return { name: "run-live", params: { runId: runLiveMatch[1] } };
    }

    const runReplayMatch = normalized.match(/^\/admin\/runs\/([^/]+)\/live\/replay$/);
    if (runReplayMatch) {
      return { name: "run-live", params: { runId: runReplayMatch[1], view: "replay" } };
    }

    if (normalized === "/admin/replay") {
      return { name: "replay-list", params: {} };
    }

    const replayRunMatch = normalized.match(/^\/admin\/replay\/runs\/([^/]+)$/);
    if (replayRunMatch) {
      return { name: "run-live", params: { runId: replayRunMatch[1], view: "replay" } };
    }

    return { name: "skills-list", params: {} };
  }

  function buildSkillDetailPath(skillId) {
    return `/admin/skills/${skillId}`;
  }

  function buildTasksPath() {
    return "/admin/tasks";
  }

  function buildRunLivePath(runId) {
    return `/admin/runs/${runId}/live`;
  }

  function buildSkillRunLivePath(skillId, runId) {
    return `/admin/skills/${skillId}/runs/${runId}/live`;
  }

  function buildSkillDebugRunLivePath(skillId, runId) {
    return `/admin/skills/${skillId}/debug/runs/${runId}/live`;
  }

  function buildReplayPath(runId) {
    return `/admin/runs/${runId}/live/replay`;
  }

  function buildSkillReplayPath(skillId, runId) {
    return `/admin/skills/${skillId}/runs/${runId}/live/replay`;
  }

  function buildSkillTestScenarioPath(skillId, scenarioId) {
    return `/admin/skills/${skillId}/tests/${scenarioId}`;
  }

  function buildSkillTestScenarioNewPath(skillId) {
    return `/admin/skills/${skillId}/tests/new`;
  }

  function buildSkillTestScenarioRunReviewPath(skillId, scenarioId, scenarioRunId) {
    return `/admin/skills/${skillId}/tests/${scenarioId}/runs/${scenarioRunId}/review`;
  }

  function buildCompilerArtifactPath(artifactId) {
    return `/admin/compiler/artifacts/${artifactId}`;
  }

  function buildSkillCompilerArtifactPath(skillId, artifactId) {
    return `/admin/skills/${skillId}/compiler/artifacts/${artifactId}`;
  }

  function buildAgentPromptPath(definitionId) {
    return `/admin/agent-prompts/${definitionId}`;
  }

  function generateSkillKey(name) {
    return window.PSOPSkillKey.generateSkillKey(name);
  }

  function resolveApiBaseUrl() {
    if (window.__PSOP_API_BASE_URL) {
      return window.__PSOP_API_BASE_URL;
    }

    if (window.location.port === "4173") {
      return "http://127.0.0.1:8011/api/v1";
    }

    return "/api/v1";
  }

  function resolveWsUrl(apiBaseUrl, pathname) {
    const apiUrl = new URL(apiBaseUrl, window.location.origin);
    const protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${apiUrl.host}${pathname}`;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function highlightJson(value) {
    const text = String(value ?? "");
    if (!text) {
      return "";
    }

    const tokenPattern =
      /("(?:\\u[a-fA-F0-9]{4}|\\["\\/bfnrt]|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
    let html = "";
    let lastIndex = 0;
    let match = tokenPattern.exec(text);
    while (match) {
      const token = match[0];
      html += escapeHtml(text.slice(lastIndex, match.index));

      let tokenClass = "json-token-number";
      if (token.startsWith("\"")) {
        tokenClass = /:\s*$/.test(token) ? "json-token-key" : "json-token-string";
      } else if (token === "true" || token === "false") {
        tokenClass = "json-token-boolean";
      } else if (token === "null") {
        tokenClass = "json-token-null";
      }

      html += `<span class="${tokenClass}">${escapeHtml(token)}</span>`;
      lastIndex = match.index + token.length;
      match = tokenPattern.exec(text);
    }

    html += escapeHtml(text.slice(lastIndex));
    return html;
  }

  function highlightYamlScalar(value) {
    let html = escapeHtml(value);
    const stringTokens = [];
    html = html.replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;)/g, (token) => {
      const placeholder = `@@PSOP_YAML_STRING_${stringTokens.length}@@`;
      stringTokens.push(`<span class="yaml-token-string">${token}</span>`);
      return placeholder;
    });
    html = html.replace(/\b(true|false|yes|no|on|off)\b/gi, '<span class="yaml-token-boolean">$1</span>');
    html = html.replace(/\b(null|~)\b/gi, '<span class="yaml-token-null">$1</span>');
    html = html.replace(/(^|[\s\[{,])(-?\d+(?:\.\d+)?)(?=$|[\s\]},])/g, '$1<span class="yaml-token-number">$2</span>');
    stringTokens.forEach((token, index) => {
      html = html.replace(`@@PSOP_YAML_STRING_${index}@@`, token);
    });
    return html;
  }

  function highlightYaml(value) {
    const lines = String(value ?? "").replace(/\r\n/g, "\n").split("\n");
    return lines
      .map((line) => {
        const commentIndex = line.indexOf("#");
        const code = commentIndex >= 0 ? line.slice(0, commentIndex) : line;
        const comment = commentIndex >= 0 ? line.slice(commentIndex) : "";
        const keyMatch = code.match(/^(\s*(?:-\s*)?)([A-Za-z0-9_.-]+)(\s*:)(.*)$/);
        let html;
        if (keyMatch) {
          html = `${escapeHtml(keyMatch[1])}<span class="yaml-token-key">${escapeHtml(keyMatch[2])}</span>${escapeHtml(keyMatch[3])}${highlightYamlScalar(keyMatch[4])}`;
        } else {
          const listMatch = code.match(/^(\s*-\s+)(.*)$/);
          html = listMatch
            ? `${escapeHtml(listMatch[1])}${highlightYamlScalar(listMatch[2])}`
            : highlightYamlScalar(code);
        }
        if (comment) {
          html += `<span class="yaml-token-comment">${escapeHtml(comment)}</span>`;
        }
        return html;
      })
      .join("\n");
  }

  function renderInlineMarkdown(value) {
    return escapeHtml(value)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>'
      );
  }

  function renderMarkdown(value) {
    const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let inCodeBlock = false;
    let codeLines = [];
    let codeLanguage = "";
    let listType = null;

    function closeList() {
      if (listType) {
        html.push(`</${listType}>`);
        listType = null;
      }
    }

    function closeCodeBlock() {
      const code = codeLines.join("\n");
      const language = codeLanguage.toLowerCase();
      let codeHtml = escapeHtml(code);
      if (language === "json") {
        codeHtml = highlightJson(code);
      } else if (language === "yaml" || language === "yml") {
        codeHtml = highlightYaml(code);
      }
      html.push(`<pre class="source-code-preview"><code>${codeHtml}</code></pre>`);
      codeLines = [];
      codeLanguage = "";
      inCodeBlock = false;
    }

    for (const line of lines) {
      const fence = line.trim().match(/^```([A-Za-z0-9_-]+)?/);
      if (fence) {
        if (inCodeBlock) {
          closeCodeBlock();
        } else {
          closeList();
          inCodeBlock = true;
          codeLines = [];
          codeLanguage = fence[1] || "";
        }
        continue;
      }

      if (inCodeBlock) {
        codeLines.push(line);
        continue;
      }

      const trimmed = line.trim();
      if (!trimmed) {
        closeList();
        continue;
      }

      const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        closeList();
        const level = heading[1].length;
        html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        continue;
      }

      const quote = trimmed.match(/^>\s?(.+)$/);
      if (quote) {
        closeList();
        html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
        continue;
      }

      const unordered = trimmed.match(/^[-*]\s+(.+)$/);
      if (unordered) {
        if (listType !== "ul") {
          closeList();
          listType = "ul";
          html.push("<ul>");
        }
        html.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
        continue;
      }

      const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
      if (ordered) {
        if (listType !== "ol") {
          closeList();
          listType = "ol";
          html.push("<ol>");
        }
        html.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
        continue;
      }

      closeList();
      html.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
    }

    if (inCodeBlock) {
      closeCodeBlock();
    }
    closeList();

    return html.join("");
  }

  function createInitialState() {
    return {
      apiBaseUrl: resolveApiBaseUrl(),
      route: { name: "skills-list", params: {} },
      sidebarCollapsed: false,
      createModalOpen: false,
      publishDrawerOpen: false,
      publishWorkspaceOpen: false,
      deleteModalOpen: false,
      deleteTargetSkill: null,
      loadingPage: false,
      skills: [],
      currentSkill: null,
      activeDetailTab: "overview",
      sourceLoadedSkillId: null,
      repositoryLoadedSkillId: null,
      repositoryPath: "",
      repositoryEntries: [],
      selectedRepositoryFile: null,
      repositoryEditing: false,
      rawMaterialsLoadedSkillId: null,
      rawMaterials: [],
      rawMaterialDetail: null,
      rawMaterialAnalysis: null,
      rawMaterialDetailTab: "analysis",
      rawMaterialUploadFiles: [],
      rawMaterialUploadItems: [],
      rawMaterialUploadSelectedIndex: 0,
      rawMaterialUploadNameAutoFilled: false,
      rawMaterialUploadProgress: null,
      rawMaterialUploadError: "",
      rawMaterialUploadForm: {
        name: "",
        description: "",
        source_note: ""
      },
      rawMaterialUploadModalOpen: false,
      rawMaterialGenerateModalOpen: false,
      rawMaterialGenerateForm: {
        user_description: ""
      },
      rawMaterialGenerationResult: null,
      builderAgentPanel: {
        open: false,
        status: "idle",
        userInput: "",
        submittedInput: "",
        generationId: "",
        jobId: "",
        agentRunId: "",
        startedAt: "",
        elapsedMs: 0,
        processExpanded: true,
        timeline: null,
        steps: [],
        result: null,
        errorMessage: "",
        sseConnected: false,
        usingPolling: false,
        lastEventAt: "",
        resultRefreshed: false
      },
      builderAgentEventSource: null,
      builderAgentPollTimer: null,
      builderAgentElapsedTimer: null,
      rawMaterialImagePreview: {
        open: false,
        src: "",
        title: "",
        description: "",
        timestamp_ms: null,
        frame_source: ""
      },
      publishRecordsLoadedSkillId: null,
      publishRecords: [],
      publishEventSource: null,
      publishPollTimer: null,
      publishProgress: {
        active: false,
        compile_request_id: null,
        terminal: false,
        terminal_status: null,
        error_message: "",
        stages: []
      },
      compilerRequests: [],
      compilerArtifact: null,
      compilerArtifactView: "graph",
      compilerArtifactGraphError: "",
      compilerArtifactGraphModel: null,
      compilerArtifactJsonDraft: "",
      compilerArtifactJsonError: "",
      selectedArtifactNodeId: "",
      compilerArtifactNodeDrawerOpen: false,
      compilerArtifactNodeEditorTab: "form",
      compilerArtifactNodeForm: {
        id: "",
        kind: "",
        label: "",
        actor_name: "",
        workflow_title: "",
        workflow_goal: "",
        guard_phase_is: "",
        projection_system_template: "",
        projection_user_template: "",
        merge_path: "",
        merge_from: "",
        merge_value: ""
      },
      compilerArtifactNodeJsonDraft: "",
      compilerArtifactNodeJsonError: "",
      bpmnViewer: null,
      agentPrompts: [],
      agentPromptDetail: null,
      agentPromptBindings: [],
      agentPromptSelectedVersionId: "",
      agentPromptSelectedFile: "",
      agentPromptFileDraft: "",
      agentPromptValidation: null,
      compilerFilters: {
        skill_search: "",
        status: "",
        requested_from: "",
        requested_to: ""
      },
      tasks: [],
      taskStats: null,
      taskLastLoadedAt: "",
      taskPollTimer: null,
      taskFilters: {
        job_type: "",
        status: "",
        q: "",
        created_from: "",
        created_to: ""
      },
      publishFilters: {
        status: "",
        published_from: "",
        published_to: ""
      },
      skillCompilerFilters: {
        status: "",
        requested_from: "",
        requested_to: ""
      },
      runtimeFilters: {
        created_from: "",
        created_to: ""
      },
      skillTestCases: [],
      skillTestCase: null,
      skillTestDataObjects: [],
      skillTestRuns: [],
      skillTestRun: null,
      skillTestReview: null,
      skillTestReviewCursor: 100,
      skillTestReviewAutoFollow: true,
      skillTestReviewPlayheadMs: 0,
      skillTestReviewPlaybackTimer: null,
      skillTestReviewPlaybackRunning: false,
      skillTestReviewPollTimer: null,
      skillTestReviewPollRunId: "",
      skillTestReviewPanelTab: "transcript",
      skillTestReviewDetailTab: "transcript",
      selectedSkillTestReviewExpectationId: "",
      skillTestReviewExpandedEventKey: "",
      selectedSkillTestReviewLaneId: "",
      selectedSkillTestTimelineEventId: "",
      selectedSkillTestTimelineEventIds: [],
      skillTestTimelineEventDraft: null,
      skillTestScenarioDetailPanel: "info",
      skillTestScenarioInfoTab: "basic",
      selectedSkillTestTimelineLaneId: "",
      skillTestTimelineDragState: null,
      skillTestTimelineLastDrag: null,
      skillTestCaseSearch: "",
      skillTestCaseForm: {
        name: "",
        description: "",
        target_version_selector: "latest",
        target_compile_artifact_id: "",
        duration_ms: 1800000,
        timeline_json: "{\n  \"schema_version\": \"psop-skill-test-timeline/v1\",\n  \"duration_ms\": 1800000,\n  \"lanes\": [],\n  \"events\": []\n}",
        judge_policy_json: "{\n  \"route_key\": \"skill-test-judge\",\n  \"confidence_threshold\": 0.5,\n  \"inconclusive_counts_as_failure\": true\n}",
        event_lane_id: "input.text",
        event_at_ms: 0,
        event_payload_inline: "",
        event_asset_id: "",
        expectation_at_ms: 0,
        expectation_text: ""
      },
      skillTestDataForm: {
        name: "",
        description: "",
        role: "input.image",
        file: null
      },
      skillTestStartForm: {
        selected_data_object_ids: []
      },
      invocations: [],
      replayRuns: [],
      liveRun: null,
      liveRunBindings: [],
      liveRunTerminalSession: null,
      liveRunTerminalEvents: [],
      liveRunTraceEvents: [],
      liveRunInteractionTab: "terminal",
      liveRunLoadedRunId: "",
      selectedLiveRunReplayItemKey: "",
      selectedLiveRunProcessEventKey: "",
      terminalMediaPreview: {
        open: false,
        kind: "",
        src: "",
        title: "",
        description: ""
      },
      liveRunWs: null,
      liveRunWsRunId: "",
      liveRunWsStatus: "idle",
      replayDetail: null,
      invocationForm: {
        skill_key: "",
        user_input: ""
      },
      skillDebugForm: {
        user_input: ""
      },
      terminalInputForm: {
        payload: "",
        attachments: []
      },
      copyFeedback: {},
      buttonTooltipInstalled: false,
      dangerActionConfirmationInstalled: false,
      centerToast: null,
      centerToastTimer: null,
      notice: null,
      createForm: {
        name: "",
        description: ""
      },
      deleteForm: {
        confirmation_name: ""
      },
      filters: {
        search: "",
        published_state: "",
        created_from: "",
        created_to: ""
      },
      metadataForm: {
        name: "",
        description: ""
      },
      sourceForm: {
        readme_content: "",
        skill_md_content: "",
        skill_yaml_content: "",
        base_commit_sha: ""
      },
      repositoryFileForm: {
        path: "",
        content: "",
        base_commit_sha: ""
      },
      sourceCreateModalOpen: false,
      sourceActionMenuOpen: false,
      sourceCreateMode: "file",
      sourceCreateForm: {
        path: "",
        content: ""
      },
      publishForm: {
        publish_reason: ""
      },
      activeSourceTab: "skill.yaml",
      busy: {
        list: false,
        create: false,
        detail: false,
        metadata: false,
        source: false,
        repositoryTree: false,
        repositoryFile: false,
        repositorySave: false,
        repositoryCreate: false,
        rawMaterials: false,
        rawMaterialDetail: false,
        rawMaterialAnalyze: false,
        rawMaterialUpload: false,
        rawMaterialDelete: false,
        rawMaterialGenerate: false,
        publishRecords: false,
        publish: false,
        delete: false,
        compilerRequests: false,
        compilerArtifact: false,
        compilerArtifactSave: false,
        agentPrompts: false,
        agentPromptDetail: false,
        agentPromptSave: false,
        agentPromptAction: false,
        manualCompile: false,
        invocations: false,
        createInvocation: false,
        liveRun: false,
        terminalInput: false,
        skillTestCases: false,
        skillTestCase: false,
        skillTestSave: false,
        skillTestData: false,
        skillTestRun: false,
        skillTestEvaluate: false,
        skillTestSendData: false,
        skillTestCancel: false,
        tasks: false,
        replayRuns: false,
        replayDetail: false
      }
    };
  }

  window.PSOPConsoleHelpers = {
    normalizePath,
    resolveAdminRoute,
    buildSkillDetailPath,
    buildTasksPath,
    buildRunLivePath,
    buildSkillRunLivePath,
    buildSkillDebugRunLivePath,
    buildReplayPath,
    buildSkillReplayPath,
    buildSkillTestScenarioPath,
    buildSkillTestScenarioNewPath,
    buildSkillTestScenarioRunReviewPath,
    buildCompilerArtifactPath,
    buildSkillCompilerArtifactPath,
    buildAgentPromptPath,
    generateSkillKey,
    resolveApiBaseUrl,
    resolveWsUrl,
    escapeHtml,
    highlightJson,
    highlightYamlScalar,
    highlightYaml,
    renderInlineMarkdown,
    renderMarkdown
  };

  function createSkillsConsole() {
    return {
      ...createInitialState(),
      ...window.PSOPConsoleCoreMethods,
      ...window.PSOPConsoleSkillDetailMethods,
      ...window.PSOPConsoleCompilerMethods,
      ...window.PSOPConsoleAgentPromptMethods,
      ...window.PSOPConsoleSkillTestMethods,
      ...window.PSOPConsoleTasksMethods,
      ...window.PSOPConsoleRuntimeMethods,
      ...window.PSOPConsoleFormatMethods
    };
  }

  document.addEventListener("alpine:init", function () {
    window.Alpine.data("skillsConsole", createSkillsConsole);
  });
})();
