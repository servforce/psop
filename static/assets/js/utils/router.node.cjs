function normalizePath(pathname) {
  if (!pathname || pathname === "/") {
    return "/";
  }

  if (pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }

  return pathname;
}

function isKnownScaffoldRoute(pathname) {
  return ["/", "/backend", "/docs", "/static"].includes(normalizePath(pathname));
}

function resolveScaffoldRoute(pathname) {
  const normalized = normalizePath(pathname);
  return isKnownScaffoldRoute(normalized) ? normalized : "/";
}

module.exports = {
  normalizePath,
  isKnownScaffoldRoute,
  resolveScaffoldRoute
};
