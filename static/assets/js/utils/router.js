export function normalizePath(pathname) {
  if (!pathname || pathname === "/") {
    return "/";
  }

  if (pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

export function resolveAdminRoute(pathname) {
  const normalized = normalizePath(pathname);
  if (normalized === "/" || normalized === "/admin" || normalized === "/admin/skills") {
    return { name: "skills-list", params: {} };
  }

  const detailMatch = normalized.match(/^\/admin\/skills\/([^/]+)$/);
  if (detailMatch) {
    return {
      name: "skill-detail",
      params: { skillId: detailMatch[1] }
    };
  }

  return { name: "skills-list", params: {} };
}

export function buildSkillDetailPath(skillId) {
  return `/admin/skills/${skillId}`;
}
