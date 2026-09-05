export function cleanProjectPath(value = "") {
  const trimmed = value.trim();
  return /^(["']).*\1$/.test(trimmed) ? trimmed.slice(1, -1).trim() : trimmed;
}

export function projectPathKey(value) {
  const path = cleanProjectPath(value);
  const windows = /^[a-z]:[\\/]|^\\\\/i.test(path);
  const normalized = windows ? path.replaceAll("\\", "/").toLowerCase() : path;
  return normalized.replace(/\/+$/, "") || (normalized ? "/" : "");
}

export function isAbsoluteProjectPath(value) {
  const path = cleanProjectPath(value);
  return !/[\r\n\0]/.test(path) && /^(?:[a-z]:[\\/]|\\\\[^\\]+\\[^\\]+|\/)/i.test(path);
}

export function projectChoices(projects, currentPath, query = "") {
  const seen = new Set();
  const needle = cleanProjectPath(query).replaceAll("\\", "/").toLowerCase();
  return [
    ...(currentPath ? [{ workspace_root: currentPath }] : []),
    ...projects,
  ].filter((project) => {
    if (!project.workspace_root) return false;
    const key = projectPathKey(project.workspace_root);
    if (seen.has(key)) return false;
    seen.add(key);
    return `${project.label || ""} ${project.workspace_root}`.replaceAll("\\", "/").toLowerCase().includes(needle);
  });
}
