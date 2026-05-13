const fs = require("fs");
const path = require("path");
const vm = require("vm");

const corePath = path.join(__dirname, "../../app/core.js");

function loadCoreMethods() {
  const helperNames = [
    "normalizePath",
    "resolveAdminRoute",
    "buildSkillDetailPath",
    "buildRunLivePath",
    "buildSkillRunLivePath",
    "buildSkillDebugRunLivePath",
    "buildReplayPath",
    "buildSkillReplayPath",
    "buildSkillTestScenarioPath",
    "buildSkillTestScenarioNewPath",
    "buildSkillTestScenarioRunReviewPath",
    "buildCompilerArtifactPath",
    "generateSkillKey",
    "resolveApiBaseUrl",
    "resolveWsUrl",
    "escapeHtml",
    "highlightJson",
    "highlightYamlScalar",
    "highlightYaml",
    "renderInlineMarkdown",
    "renderMarkdown"
  ];
  const context = {
    window: {
      PSOPConsoleHelpers: Object.fromEntries(helperNames.map((name) => [name, jest.fn()]))
    }
  };
  vm.runInNewContext(fs.readFileSync(corePath, "utf8"), context);
  return context.window.PSOPConsoleCoreMethods;
}

function textNode(text) {
  return {
    nodeType: 3,
    textContent: text
  };
}

function createElement({ classes = [], attrs = {}, dataset = {}, childNodes = [] } = {}) {
  const textContent = childNodes.map((node) => node.textContent || "").join("");
  const element = {
    nodeType: 1,
    textContent,
    childNodes,
    dataset,
    classList: {
      contains(name) {
        return classes.includes(name);
      }
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : "";
    },
    setAttribute(name, value) {
      attrs[name] = String(value);
    },
    closest(selector) {
      return selector.includes("button") ? element : null;
    },
    querySelector(selector) {
      const stack = [...childNodes];
      while (stack.length) {
        const node = stack.shift();
        if (
          node?.nodeType === 1 &&
          selector.includes("material-symbols") &&
          (
            node.classList?.contains("material-symbols-outlined") ||
            node.classList?.contains("material-symbols-rounded") ||
            node.classList?.contains("material-symbols-sharp")
          )
        ) {
          return node;
        }
        stack.push(...(node?.childNodes || []));
      }
      return null;
    }
  };
  return element;
}

function createButton(options = {}) {
  const attrs = { ...(options.attrs || {}) };
  return {
    attrs,
    element: createElement({
      classes: options.classes || [],
      attrs,
      dataset: options.dataset || {},
      childNodes: options.childNodes || []
    })
  };
}

test("button tooltips prefer readable button text over icon ligatures", () => {
  const methods = loadCoreMethods();
  const { attrs, element } = createButton({
    childNodes: [
      createElement({
        classes: ["material-symbols-outlined"],
        childNodes: [textNode("save")]
      }),
      textNode(" 保存场景 ")
    ]
  });

  methods.ensureButtonTooltip(element);

  expect(attrs.title).toBe("保存场景");
  expect(attrs["aria-label"]).toBe("保存场景");
});

test("button tooltips describe icon-only actions", () => {
  const methods = loadCoreMethods();
  const { attrs, element } = createButton({
    childNodes: [
      createElement({
        classes: ["material-symbols-outlined"],
        childNodes: [textNode("delete")]
      })
    ]
  });

  methods.ensureButtonTooltip(element);

  expect(attrs.title).toBe("删除");
  expect(attrs["aria-label"]).toBe("删除");
});

test("button tooltips keep explicit accessibility labels", () => {
  const methods = loadCoreMethods();
  const { attrs, element } = createButton({
    attrs: { "aria-label": "打开运行现场" },
    childNodes: [
      createElement({
        classes: ["material-symbols-outlined"],
        childNodes: [textNode("open_in_new")]
      })
    ]
  });

  methods.ensureButtonTooltip(element);

  expect(attrs.title).toBe("打开运行现场");
  expect(attrs["aria-label"]).toBe("打开运行现场");
});

test("button tooltips refresh generated text when button labels change", () => {
  const methods = loadCoreMethods();
  const label = textNode("保存");
  const { attrs, element } = createButton({
    childNodes: [label]
  });

  methods.ensureButtonTooltip(element);
  label.textContent = "运行";
  methods.ensureButtonTooltip(element);

  expect(attrs.title).toBe("运行");
  expect(attrs["aria-label"]).toBe("运行");
});

test("danger action buttons ask for confirmation before running", () => {
  const methods = loadCoreMethods();
  const { element } = createButton({
    classes: ["button-danger"],
    childNodes: [
      createElement({
        classes: ["material-symbols-outlined"],
        childNodes: [textNode("delete")]
      })
    ]
  });
  const event = {
    target: element,
    defaultPrevented: false,
    preventDefault: jest.fn(function preventDefault() {
      this.defaultPrevented = true;
    }),
    stopImmediatePropagation: jest.fn()
  };
  methods.confirmDangerAction = jest.fn(() => false);

  methods.handleDangerActionClick(event);

  expect(methods.describeDangerActionConfirmation(element)).toBe("确认删除？此操作可能无法撤销。");
  expect(methods.confirmDangerAction).toHaveBeenCalledWith("确认删除？此操作可能无法撤销。", element);
  expect(event.preventDefault).toHaveBeenCalledTimes(1);
  expect(event.stopImmediatePropagation).toHaveBeenCalledTimes(1);
});

test("confirmed danger actions continue to their original click handler", () => {
  const methods = loadCoreMethods();
  const { element } = createButton({
    classes: ["button-danger"],
    childNodes: [textNode("删除场景")]
  });
  const event = {
    target: element,
    defaultPrevented: false,
    preventDefault: jest.fn(),
    stopImmediatePropagation: jest.fn()
  };
  methods.confirmDangerAction = jest.fn(() => true);

  methods.handleDangerActionClick(event);

  expect(methods.confirmDangerAction).toHaveBeenCalledWith("确认删除场景？此操作可能无法撤销。", element);
  expect(event.preventDefault).not.toHaveBeenCalled();
  expect(event.stopImmediatePropagation).not.toHaveBeenCalled();
});

test("opening the existing delete modal does not add another confirmation", () => {
  const methods = loadCoreMethods();
  const { element } = createButton({
    classes: ["icon-button-danger"],
    attrs: { "@click.stop": "openDeleteModal(skill)" },
    childNodes: [
      createElement({
        classes: ["material-symbols-outlined"],
        childNodes: [textNode("delete")]
      })
    ]
  });

  expect(methods.describeDangerActionConfirmation(element)).toBe("");
});

test("explicit danger confirmation messages are supported", () => {
  const methods = loadCoreMethods();
  const { element } = createButton({
    dataset: { dangerConfirm: "确认移除此事件？" },
    childNodes: [textNode("移除")]
  });

  expect(methods.describeDangerActionConfirmation(element)).toBe("确认移除此事件？");
});
