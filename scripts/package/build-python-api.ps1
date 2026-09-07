$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$distPath = Join-Path $repoRoot "build\package"
$workPath = Join-Path $repoRoot "build\pyinstaller"
$specPath = Join-Path $repoRoot "build\pyinstaller"
$apiEntryPoint = Join-Path $PSScriptRoot "lora_api_entry.py"
$cliEntryPoint = Join-Path $PSScriptRoot "lora_entry.py"
$wheelPath = Join-Path $workPath "wheel"
$loraWheel = $null

function Invoke-PyInstaller {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,
        [Parameter(Mandatory = $true)]
        [string] $EntryPoint,
        [string[]] $HiddenImports = @()
    )

    $arguments = @(
        "run",
        "--no-project",
        "--with",
        "pyinstaller",
        "--with",
        $loraWheel,
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        $Name,
        "--paths",
        "src",
        "--collect-submodules",
        "lora",
        "--collect-submodules",
        "lora_api",
        "--collect-submodules",
        "pygent",
        "--collect-submodules",
        "pygent_ai",
        "--collect-submodules",
        "uvicorn",
        "--copy-metadata",
        "lora",
        "--distpath",
        $distPath,
        "--workpath",
        $workPath,
        "--specpath",
        $specPath
    )

    foreach ($hiddenImport in $HiddenImports) {
        $arguments += @("--hidden-import", $hiddenImport)
    }

    $arguments += $EntryPoint
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller target $Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $repoRoot
try {
    if (Test-Path $wheelPath) {
        Remove-Item -LiteralPath $wheelPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $distPath, $workPath, $specPath, $wheelPath | Out-Null

    & uv build --wheel --out-dir $wheelPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the isolated Lora package wheel"
    }

    $builtWheels = @(Get-ChildItem -LiteralPath $wheelPath -Filter "lora-*.whl" -File)
    if ($builtWheels.Count -ne 1) {
        throw "Expected exactly one Lora wheel, found $($builtWheels.Count)"
    }
    $loraWheel = $builtWheels[0].FullName

    Invoke-PyInstaller `
        -Name "lora-api" `
        -EntryPoint $apiEntryPoint `
        -HiddenImports @("lora_api.main")

    Invoke-PyInstaller `
        -Name "lora" `
        -EntryPoint $cliEntryPoint `
        -HiddenImports @("lora.cli.main")

    Copy-Item `
        -LiteralPath (Join-Path $distPath "lora\lora.exe") `
        -Destination (Join-Path $distPath "lora-api\lora.exe") `
        -Force

}
finally {
    Pop-Location
}
