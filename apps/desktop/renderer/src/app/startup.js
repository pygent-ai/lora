export async function waitForDesktopBackend({
  desktop = globalThis.loraDesktop,
  delay = () => new Promise((resolve) => setTimeout(resolve, 150)),
  now = Date.now,
  timeoutMs = 45_000,
} = {}) {
  if (!desktop?.getBackendStatus) return;
  const deadline = now() + timeoutMs;
  while (now() < deadline) {
    const status = await desktop.getBackendStatus();
    if (status.state === "ready") return;
    if (["error", "exited", "stopping"].includes(status.state)) {
      throw new Error(status.error || "本地服务未能启动，请重新打开应用。");
    }
    await delay();
  }
  throw new Error("本地服务启动超时，请重新打开应用。");
}
