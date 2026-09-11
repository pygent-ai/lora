import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import viteConfig from "../../vite.config.mjs";

const packageJson = JSON.parse(await readFile(new URL("../../package.json", import.meta.url), "utf8"));
const rootPackageJson = JSON.parse(await readFile(new URL("../../../../package.json", import.meta.url), "utf8"));
const electronMain = await readFile(new URL("./main.mjs", import.meta.url), "utf8");
const electronPreload = await readFile(new URL("../preload/preload.mjs", import.meta.url), "utf8");
const desktopBuildScript = await readFile(new URL("../../../../scripts/package/build-desktop.ps1", import.meta.url), "utf8");
const pythonBuildScript = await readFile(new URL("../../../../scripts/package/build-python-api.ps1", import.meta.url), "utf8");
const cliPathInstallerScript = await readFile(new URL("../../installer/cli-path.nsh", import.meta.url), "utf8");

test("desktop package exposes Windows exe packaging scripts", () => {
  assert.equal(packageJson.main, "electron/main/main.mjs");
  assert.match(packageJson.scripts["package:python"], /build-python-api\.ps1/);
  assert.match(packageJson.scripts["package:win"], /build-desktop\.ps1 -Target nsis/);
  assert.match(packageJson.scripts["package:portable"], /build-desktop\.ps1 -Target portable/);
  assert.match(packageJson.scripts["package:dir"], /build-desktop\.ps1 -Target dir/);
  assert.equal(packageJson.build.portable.artifactName, "Lora-Desktop-${version}-portable.${ext}");
});

test("workspace development command launches the full Electron stack", () => {
  assert.equal(rootPackageJson.scripts.dev, "npm --prefix apps/desktop run dev");
  assert.equal(packageJson.scripts.dev, "node scripts/dev.mjs");
  assert.equal(packageJson.scripts["dev:renderer"], "vite --host 127.0.0.1");
  assert.match(electronMain, /lora:dev-shutdown/);
});

test("Choose Project uses the native directory picker", () => {
  assert.match(electronMain, /dialog\.showOpenDialog/);
  assert.match(electronMain, /properties: \["openDirectory", "createDirectory"\]/);
  assert.match(electronPreload, /chooseProjectDirectory/);
  assert.match(electronPreload, /project:choose-directory/);
});

test("desktop window does not show the default Electron menu", () => {
  assert.match(electronMain, /mainWindow\.removeMenu\(\)/);
});

test("production assets use file-compatible relative URLs", () => {
  assert.equal(viteConfig.base, "./");
});

test("electron-builder bundles the PyInstaller lora-api output", () => {
  const extraResources = packageJson.build.extraResources;

  assert.deepEqual(extraResources, [
    {
      from: "../../build/package/lora-api",
      to: "backend/lora-api",
    },
  ]);
});

test("Python packaging builds both local API and lora chat CLI executables", () => {
  assert.match(pythonBuildScript, /lora_api_entry\.py/);
  assert.match(pythonBuildScript, /lora_entry\.py/);
  assert.match(pythonBuildScript, /-Name "lora-api"/);
  assert.match(pythonBuildScript, /-Name "lora"/);
  assert.match(pythonBuildScript, /uv build --wheel/);
  assert.match(pythonBuildScript, /"--no-project"/);
  assert.match(pythonBuildScript, /"--with",\s*\$loraWheel/);
  assert.match(pythonBuildScript, /"--copy-metadata",\s*"lora"/);
  assert.doesNotMatch(pythonBuildScript, /lora\\_internal/);
});

test("installer adds the bundled lora CLI directory to user PATH", () => {
  assert.equal(packageJson.build.nsis.include, "installer/cli-path.nsh");
  assert.match(cliPathInstallerScript, /customInstall/);
  assert.match(cliPathInstallerScript, /customUnInstall/);
  assert.match(cliPathInstallerScript, /backend\\lora-api/);
  assert.match(cliPathInstallerScript, /lora\.exe/);
  assert.match(cliPathInstallerScript, /Environment/);
});

test("desktop packaging script configures Electron download mirrors", () => {
  assert.match(desktopBuildScript, /ELECTRON_MIRROR/);
  assert.match(desktopBuildScript, /ELECTRON_BUILDER_BINARIES_MIRROR/);
});

test("desktop packaging script clears stale Electron output before packaging", () => {
  assert.match(desktopBuildScript, /win-unpacked/);
  assert.match(desktopBuildScript, /win-unpacked\.tmp/);
});

test("desktop packaging script fails when native build commands fail", () => {
  assert.match(desktopBuildScript, /\$LASTEXITCODE/);
});
