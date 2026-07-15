param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$CosyvoicePython = '',
    [string]$AlignerPython = '',
    [string]$MptPython = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

function Write-Check {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Details,
        [bool]$Required = $false
    )

    $state = if ($Ok) { 'OK' } elseif ($Required) { 'MISSING' } else { 'WARN' }
    Write-Host ('[{0}] {1}: {2}' -f $state, $Name, $Details)
    return ($Ok -or -not $Required)
}

Write-Output 'MoneyPrinterTurbo local voice Windows environment check'
Write-Output ('Project root: {0}' -f $ProjectRoot)

$requiredFailures = 0
$requiredCommands = @('git', 'python', 'ffmpeg', 'nvidia-smi')
foreach ($commandName in $requiredCommands) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        Write-Check -Name $commandName -Ok $false -Details 'command not found' -Required $true | Out-Null
        $requiredFailures++
    }
    else {
        $version = switch ($commandName) {
            'git' { (& $command.Source --version 2>$null | Select-Object -First 1) }
            'python' { (& $command.Source --version 2>$null | Select-Object -First 1) }
            'ffmpeg' { (& $command.Source -version 2>$null | Select-Object -First 1) }
            'nvidia-smi' { (& $command.Source --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null | Select-Object -First 1) }
        }
        Write-Check -Name $commandName -Ok $true -Details ([string]$version) | Out-Null
    }
}

foreach ($optionalCommand in @('conda', 'sox', 'uv')) {
    $command = Get-Command $optionalCommand -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        Write-Check -Name $optionalCommand -Ok $false -Details 'not installed; setup or fallback path is required' | Out-Null
    }
    else {
        $version = (& $command.Source --version 2>$null | Select-Object -First 1)
        Write-Check -Name $optionalCommand -Ok $true -Details ([string]$version) | Out-Null
    }
}

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    Write-Check -Name 'project root' -Ok $false -Details 'directory does not exist' -Required $true | Out-Null
    $requiredFailures++
}

$driveName = ([System.IO.Path]::GetPathRoot($ProjectRoot)).TrimEnd('\').TrimEnd(':')
$drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
if ($null -ne $drive) {
    $freeGb = [math]::Round($drive.Free / 1GB, 2)
    $spaceOk = $freeGb -ge 10
    Write-Check -Name 'disk space' -Ok $spaceOk -Details ('{0} GB free on {1} (10 GB minimum for staged setup)' -f $freeGb, $driveName) | Out-Null
}

foreach ($labelPath in @(
    @{ Name = 'mpt python'; Value = $MptPython },
    @{ Name = 'cosyvoice python'; Value = $CosyvoicePython },
    @{ Name = 'aligner python'; Value = $AlignerPython }
)) {
    if ([string]::IsNullOrWhiteSpace($labelPath.Value)) {
        Write-Check -Name $labelPath.Name -Ok $false -Details 'not configured yet' | Out-Null
    }
    else {
        Write-Check -Name $labelPath.Name -Ok (Test-Path -LiteralPath $labelPath.Value -PathType Leaf) -Details $labelPath.Value | Out-Null
    }
}

if ($requiredFailures -gt 0) {
    Write-Output ('Environment check failed with {0} required item(s) missing.' -f $requiredFailures)
    exit 2
}

Write-Output 'Required host checks passed. Optional dependencies may still need installation.'
exit 0
