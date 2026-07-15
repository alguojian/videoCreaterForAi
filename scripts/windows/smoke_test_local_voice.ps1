param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonExecutable = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $candidate = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $PythonExecutable = if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $candidate
    }
    else {
        (Get-Command python -ErrorAction Stop).Source
    }
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$SmokeScript = Join-Path $ProjectRoot 'scripts\smoke_test_local_voice.py'
if (-not (Test-Path -LiteralPath $SmokeScript -PathType Leaf)) {
    throw "Smoke test script does not exist: $SmokeScript"
}

Push-Location $ProjectRoot
try {
    & $PythonExecutable $SmokeScript
    if ($LASTEXITCODE -ne 0) {
        throw "local voice smoke test failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
