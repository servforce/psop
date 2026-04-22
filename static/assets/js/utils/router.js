export function normalizePath(pathname) {
  if (!pathname || pathname === "/") {
    return "/";
  }

  if (pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

export function isKnownScaffoldRoute(pathname) {
  return ["/", "/backend", "/docs", "/static"].includes(normalizePath(pathname));
}

export function resolveScaffoldRoute(pathname) {
  const normalized = normalizePath(pathname);
  return isKnownScaffoldRoute(normalized) ? normalized : "/";
}
