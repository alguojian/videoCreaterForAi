param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$CondaExecutable = '',
    [switch]$SkipHostDependencies,
    [switch]$SkipWorkerDependencies
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-CondaExecutable {
    param([string]$Requested)

    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        if (-not (Test-Path -LiteralPath $Requested -PathType Leaf)) {
            throw "The requested Conda executable does not exist: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }

    $command = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:USERPROFILE 'miniforge3\condabin\conda.bat'),
        (Join-Path $env:USERPROFILE 'miniconda3\condabin\conda.bat'),
        (Join-Path $env:LOCALAPPDATA 'miniforge3\condabin\conda.bat'),
        (Join-Path $env:LOCALAPPDATA 'miniconda3\condabin\conda.bat')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    throw 'Conda was not found. Install Miniforge/Miniconda for Windows, reopen PowerShell, and rerun this script.'
}

function Invoke-Conda {
    param([string[]]$Arguments)
    & $script:Conda @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ('Conda command failed with exit code {0}: conda {1}' -f $LASTEXITCODE, ($Arguments -join ' '))
    }
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$Conda = Resolve-CondaExecutable -Requested $CondaExecutable
Write-Output "Using Conda: $Conda"
Write-Output "Project root: $ProjectRoot"

Invoke-Conda @('create', '-n', 'mpt', 'python=3.11', '-y')
Invoke-Conda @('create', '-n', 'cosyvoice', 'python=3.10', '-y')
Invoke-Conda @('create', '-n', 'qwen-aligner', 'python=3.12', '-y')

if (-not $SkipHostDependencies) {
    Invoke-Conda @('run', '-n', 'mpt', 'python', '-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Conda @('run', '-n', 'mpt', 'python', '-m', 'pip', 'install', '-r', (Join-Path $ProjectRoot 'requirements.txt'))
    Invoke-Conda @('run', '-n', 'mpt', 'python', '-m', 'pip', 'install', 'pytest')
}

if (-not $SkipWorkerDependencies) {
    Invoke-Conda @('run', '-n', 'cosyvoice', 'python', '-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Conda @('run', '-n', 'qwen-aligner', 'python', '-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Conda @('run', '-n', 'qwen-aligner', 'python', '-m', 'pip', 'install', 'qwen-asr')
}

$mptPython = (& $Conda run -n mpt python -c "import sys; print(sys.executable)").Trim()
$cosyvoicePython = (& $Conda run -n cosyvoice python -c "import sys; print(sys.executable)").Trim()
$alignerPython = (& $Conda run -n qwen-aligner python -c "import sys; print(sys.executable)").Trim()

Write-Output "mpt_python=$mptPython"
Write-Output "cosyvoice_python=$cosyvoicePython"
Write-Output "aligner_python=$alignerPython"
Write-Output 'Environment creation completed. Model download is intentionally a separate step because model sizes must be checked against available disk space first.'
