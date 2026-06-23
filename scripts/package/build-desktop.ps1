param(
    [ValidateSet("nsis", "dir")]
    [string] $Target = "nsis"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$desktopRoot = Join-Path $repoRoot "apps\desktop"

function Set-DefaultEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,
        [Parameter(Mandatory = $true)]
        [string] $Value
    )

    if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,
        [Parameter(Mandatory = $true)]
        [string[]] $ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Remove-StaleElectronOutput {
    $releaseRoot = Join-Path $desktopRoot "release"
    foreach ($name in @("win-unpacked", "win-unpacked.tmp")) {
        $target = Join-Path $releaseRoot $name
        if (Test-Path $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
}

Set-DefaultEnv "ELECTRON_MIRROR" "https://npmmirror.com/mirrors/electron/"
Set-DefaultEnv "ELECTRON_BUILDER_BINARIES_MIRROR" "https://npmmirror.com/mirrors/electron-builder-binaries/"

Push-Location $desktopRoot
try {
    Invoke-Native "powershell" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "build-python-api.ps1"))
    Invoke-Native "npm" @("run", "build")
    Remove-StaleElectronOutput
    Invoke-Native "npx" @("electron-builder", "--win", $Target)
}
finally {
    Pop-Location
}
