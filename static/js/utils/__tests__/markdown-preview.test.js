const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = path.join(__dirname, "../../app.js");
const skillDetailPath = path.join(__dirname, "../../app/skill-detail.js");

function loadPreviewContext() {
  const context = {
    URL,
    URLSearchParams,
    document: { addEventListener: jest.fn() },
    window: {
      location: {
        origin: "http://web.example",
        port: ""
      }
    }
  };
  vm.runInNewContext(fs.readFileSync(appPath, "utf8"), context);
  vm.runInNewContext(fs.readFileSync(skillDetailPath, "utf8"), context);
  return context.window;
}

test("repository image paths resolve relative to the current markdown file", () => {
  const { resolveRepositoryImagePath } = loadPreviewContext().PSOPConsoleHelpers;

  expect(resolveRepositoryImagePath("SKILL.md", "references/frame.jpg?cache=1#preview"))
    .toBe("references/frame.jpg");
  expect(resolveRepositoryImagePath("docs/guides/setup.md", "../../references/panel.png"))
    .toBe("references/panel.png");
  expect(resolveRepositoryImagePath("docs/setup.md", "./images/demo.WEBP"))
    .toBe("docs/images/demo.WEBP");
});

test.each([
  ["SKILL.md", "../references/outside.jpg"],
  ["docs/guide.md", "../../references/outside.jpg"],
  ["SKILL.md", "/references/absolute.jpg"],
  ["SKILL.md", "https://example.test/external.jpg"],
  ["SKILL.md", "data:image/png;base64,AAAA"],
  ["SKILL.md", "references\\bad.jpg"],
  ["SKILL.md", "references%5Cbad.jpg"],
  ["SKILL.md", "references/icon.svg"]
])("repository image paths reject unsafe or unsupported target %s", (currentPath, target) => {
  const { resolveRepositoryImagePath } = loadPreviewContext().PSOPConsoleHelpers;

  expect(resolveRepositoryImagePath(currentPath, target)).toBe("");
});

test("markdown renders resolved repository images with escaped attributes", () => {
  const { renderMarkdown } = loadPreviewContext().PSOPConsoleHelpers;
  const html = renderMarkdown(
    '![<part "A">](references/frame.jpg "Inspect <carefully>")',
    { resolveImageUrl: (target) => `/repository/raw?path=${target}&ref=commit-1` }
  );

  expect(html).toBe(
    '<p><img src="/repository/raw?path=references/frame.jpg&amp;ref=commit-1" ' +
    'alt="&lt;part &quot;A&quot;&gt;" title="Inspect &lt;carefully&gt;" loading="lazy" decoding="async"></p>'
  );
});

test("markdown renders multiple and list-contained images", () => {
  const { renderMarkdown } = loadPreviewContext().PSOPConsoleHelpers;
  const html = renderMarkdown(
    "- ![one](references/one.jpg) ![two](references/two.png)",
    { resolveImageUrl: (target) => `/raw/${target}` }
  );

  expect(html).toContain('<ul><li><img src="/raw/references/one.jpg"');
  expect(html).toContain('<img src="/raw/references/two.png"');
  expect(html).toContain("</li></ul>");
});

test("markdown keeps image syntax as text without an approved resolver", () => {
  const { renderMarkdown } = loadPreviewContext().PSOPConsoleHelpers;

  expect(renderMarkdown("![local](references/frame.jpg)")).toBe(
    "<p>![local](references/frame.jpg)</p>"
  );
  expect(
    renderMarkdown("![remote](https://example.test/frame.jpg)", { resolveImageUrl: () => "" })
  ).toBe("<p>![remote](https://example.test/frame.jpg)</p>");
});

test("skill repository preview builds a commit-scoped PSOP image URL", () => {
  const window = loadPreviewContext();
  const methods = window.PSOPConsoleSkillDetailMethods;
  const app = {
    apiBaseUrl: "http://api.example/api/v1",
    currentSkill: { id: "skill/one" },
    repositoryFileForm: {
      path: "docs/guide.md",
      content: "![panel](../references/panel.png)",
      base_commit_sha: "commit-0001"
    }
  };
  app.normalizeRepositoryPath = (value, allowEmpty) => methods.normalizeRepositoryPath.call(app, value, allowEmpty);
  app.repositoryPreviewKind = () => methods.repositoryPreviewKind.call(app);
  app.repositoryMarkdownImageUrl = (target) => methods.repositoryMarkdownImageUrl.call(app, target);

  const html = methods.repositoryPreviewHtml.call(app);

  expect(html).toContain(
    "http://api.example/api/v1/skills/skill%2Fone/repository/raw?" +
    "path=references%2Fpanel.png&amp;ref=commit-0001"
  );
});

test("skill source opens SKILL.md by default", async () => {
  const methods = loadPreviewContext().PSOPConsoleSkillDetailMethods;
  const app = {
    selectedRepositoryFile: null,
    repositoryPath: "",
    repositoryEntries: [
      { type: "blob", name: "README.md", path: "README.md" },
      { type: "blob", name: "SKILL.md", path: "SKILL.md" }
    ],
    loadRepositoryFile: jest.fn(async () => {})
  };

  await methods.ensureDefaultRepositoryPreview.call(app, "skill-1");

  expect(app.loadRepositoryFile).toHaveBeenCalledWith("SKILL.md");
});
