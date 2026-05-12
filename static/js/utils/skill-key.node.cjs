function generateSkillKey(name, suffix) {
  const readablePart = String(name || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  const resolvedSuffix = suffix || Date.now().toString(36).slice(-6);
  const base = readablePart.length >= 2 ? readablePart : "skill";
  const maxBaseLength = 120 - resolvedSuffix.length - 1;
  const safeBase = base.slice(0, maxBaseLength).replace(/-+$/g, "") || "skill";

  return `${safeBase}-${resolvedSuffix}`;
}

module.exports = {
  generateSkillKey
};
