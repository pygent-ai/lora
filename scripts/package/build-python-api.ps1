$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$distPath = Join-Path $repoRoot "build\package"
$workPath = Join-Path $repoRoot "build\pyinstaller"
$specPath = Join-Path $repoRoot "build\pyinstaller"
$apiEntryPoint = Join-Path $PSScriptRoot "lora_api_entry.py"
$cliEntryPoint = Join-Path $PSScriptRoot "lora_entry.py"

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
        "--with",
        "pyinstaller",
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
    New-Item -ItemType Directory -Force -Path $distPath, $workPath, $specPath | Out-Null

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

    $cliInternal = Join-Path $distPath "lora\_internal"
    if (Test-Path $cliInternal) {
        Copy-Item `
            -LiteralPath $cliInternal `
            -Destination (Join-Path $distPath "lora-api\_internal") `
            -Recurse `
            -Force
    }
}
finally {
    Pop-Location
}
