const configuredBasePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

export const basePath = configuredBasePath.endsWith("/")
  ? configuredBasePath.slice(0, -1)
  : configuredBasePath;

export function withBasePath(path: string): string {
  if (
    !basePath ||
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path === basePath ||
    path.startsWith(`${basePath}/`) ||
    /^[a-z][a-z0-9+.-]*:/i.test(path)
  ) {
    return path;
  }

  return `${basePath}${path}`;
}

export function withoutBasePath(pathname: string | null): string | null {
  if (!basePath || !pathname) {
    return pathname;
  }

  if (pathname === basePath) {
    return "/";
  }

  if (pathname.startsWith(`${basePath}/`)) {
    return pathname.slice(basePath.length);
  }

  return pathname;
}
