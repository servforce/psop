const fs = require("fs");
const path = require("path");

const skillDetailPath = path.join(__dirname, "../../../pages/skill-detail.html");
const appPath = path.join(__dirname, "../../app.js");
const skillDetailAppPath = path.join(__dirname, "../../app/skill-detail.js");
const formattersPath = path.join(__dirname, "../../app/formatters.js");

test("skill detail exposes raw materials tab and generation workflow", () => {
  const html = fs.readFileSync(skillDetailPath, "utf8");
  const appJs = fs.readFileSync(appPath, "utf8");
  const skillDetailJs = fs.readFileSync(skillDetailAppPath, "utf8");
  const formattersJs = fs.readFileSync(formattersPath, "utf8");

  expect(html).toContain("<span>素材</span>");
  expect(html).not.toContain("<span>原始素材</span>");
  expect(html.indexOf("<span>信息</span>")).toBeLessThan(html.indexOf("<span>素材</span>"));
  expect(html.indexOf("<span>素材</span>")).toBeLessThan(html.indexOf("<span>源码</span>"));
  expect(html).toContain("rawMaterialUploadMode");
  expect(html).toContain("openRawMaterialUploadModal('file')");
  expect(html).toContain("openRawMaterialUploadModal('url')");
  expect(html).toContain("submitRawMaterial()");
  expect(html).toContain("openRawMaterialGenerateModal()");
  expect(html).toContain("generateSkillDraftFromRawMaterials()");
  expect(html).toContain("rawMaterialGenerationResult.committed_commit_sha");
  expect(html).toContain("rawMaterialContentUrl(rawMaterialDetail)");
  expect(html).toContain("xl:divide-x xl:divide-slate-800");
  expect(html).toContain("flex min-h-14 shrink-0 items-center border-b border-slate-800 bg-slate-950/70 px-3 py-1.5");
  expect(html).toContain('class="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5 xl:flex-nowrap xl:overflow-hidden"');
  expect(html).toContain('class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] leading-4 xl:flex-1 xl:flex-nowrap xl:overflow-hidden"');
  expect(html).toContain("grid min-h-0 grid-rows-[auto_minmax(0,1fr)] overflow-hidden");
  expect(html).toContain('class="flex h-10 items-center border-b border-slate-800 px-3"');
  expect(html).toContain('class="flex h-10 items-center justify-between border-b border-slate-800 px-3"');
  expect(html).toContain("min-h-0 overflow-y-auto overflow-x-hidden px-3 py-3");
  expect(html).toContain('class="min-h-0 overflow-auto"');
  expect(html).not.toContain('x-text="rawMaterialDetail.filename"');
  expect(html).toContain('dt class="font-semibold uppercase tracking-[0.16em] text-slate-500">类型');
  expect(html).toContain('class="topbar-icon-button h-7 w-7" :href="rawMaterialContentUrl(rawMaterialDetail)"');
  expect(html).toContain('title="打开原文件" aria-label="打开原文件"');
  expect(html).not.toMatch(/class="topbar-icon-button h-7 w-7"[\s\S]{0,300}<span>打开<\/span>/);
  expect(html).toContain('x-if="canPreviewRawMaterial(\'document\')"');
  expect(html).toContain('sandbox referrerpolicy="no-referrer"');
  expect(html).not.toContain("当前类型使用解析文本预览。");
  expect(html).not.toContain('section class="min-h-[16rem] border border-slate-800');
  expect(html).not.toContain("rounded-none border border-slate-800 bg-black/25");
  expect(html).not.toContain("max-h-[32rem] overflow-auto whitespace-pre-wrap");

  expect(appJs).toContain("rawMaterialsLoadedSkillId");
  expect(appJs).toContain("selectedRawMaterialIds");
  expect(appJs).toContain("rawMaterialUploadModalOpen");
  expect(appJs).toContain("rawMaterialGenerateForm");
  expect(appJs).toContain("rawMaterialGenerate");

  expect(skillDetailJs).toContain("openRawMaterialUploadModal");
  expect(skillDetailJs).toContain("closeRawMaterialUploadModal");
  expect(skillDetailJs).toContain("/raw-materials");
  expect(skillDetailJs).toContain("/raw-materials/generate-skill-draft");
  expect(skillDetailJs).toContain("material_ids: this.selectedRawMaterialIds");
  expect(skillDetailJs).toContain("user_description: this.rawMaterialGenerateForm.user_description.trim()");
  expect(skillDetailJs).toContain("base_commit_sha: this.currentSkill.latest_draft_head_sha");
  expect(skillDetailJs).toContain('kind === "document"');
  expect(skillDetailJs).toContain("this.sourceLoadedSkillId = null");
  expect(skillDetailJs).toContain("this.repositoryLoadedSkillId = null");
  expect(formattersJs).toContain("formatBytes(value)");
});
