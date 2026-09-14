// Port selection and spawning must precede the preload's API URL snapshot.
// Health checking must not hold up loading and painting the renderer.
export async function launchDesktop({ prepareBackend, createWindow, isQuitting }) {
  const { ready } = await prepareBackend();
  if (!isQuitting()) await createWindow();
  await ready;
}
