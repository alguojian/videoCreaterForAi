param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable,
    [Parameter(Mandatory = $true)]
    [string]$ModelRoot,
    [string[]]$Model = @('cosyvoice', 'qwen')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$downloader = Join-Path $scriptRoot 'download_local_voice_models.py'
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable does not exist: $PythonExecutable"
}
if (-not (Test-Path -LiteralPath $downloader -PathType Leaf)) {
    throw "Model downloader does not exist: $downloader"
}

$arguments = @($downloader, '--root', $ModelRoot)
foreach ($name in $Model) {
    $arguments += @('--model', $name)
}
& $PythonExecutable @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Model download failed with exit code $LASTEXITCODE"
}
