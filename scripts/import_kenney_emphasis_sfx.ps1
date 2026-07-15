[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$sourceRepo = "https://github.com/Calinou/kenney-ui-audio.git"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputDir = Join-Path $projectRoot "resource\sfx"
$tempRoot = [IO.Path]::GetFullPath($env:TEMP)
$tempDir = [IO.Path]::GetFullPath(
    (Join-Path $tempRoot ("money-printer-turbo-kenney-ui-audio-" + [guid]::NewGuid()))
)
$sourceAudioDir = Join-Path $tempDir "addons\kenney_ui_audio"
$sourceLicense = Join-Path $tempDir "LICENSE.txt"

$selected = @(
    [pscustomobject]@{ output = "pop-01.wav"; source = "click1.wav"; group = "pop"; sound_id = "pop-01" }
    [pscustomobject]@{ output = "pop-02.wav"; source = "click2.wav"; group = "pop"; sound_id = "pop-02" }
    [pscustomobject]@{ output = "pop-03.wav"; source = "click3.wav"; group = "pop"; sound_id = "pop-03" }
    [pscustomobject]@{ output = "whoosh-01.wav"; source = "rollover1.wav"; group = "whoosh"; sound_id = "whoosh-01" }
    [pscustomobject]@{ output = "whoosh-02.wav"; source = "rollover2.wav"; group = "whoosh"; sound_id = "whoosh-02" }
    [pscustomobject]@{ output = "whoosh-03.wav"; source = "rollover3.wav"; group = "whoosh"; sound_id = "whoosh-03" }
    [pscustomobject]@{ output = "hit-01.wav"; source = "switch10.wav"; group = "hit"; sound_id = "hit-01" }
    [pscustomobject]@{ output = "hit-02.wav"; source = "switch11.wav"; group = "hit"; sound_id = "hit-02" }
    [pscustomobject]@{ output = "hit-03.wav"; source = "switch12.wav"; group = "hit"; sound_id = "hit-03" }
    [pscustomobject]@{ output = "sparkle-01.wav"; source = "switch20.wav"; group = "sparkle"; sound_id = "sparkle-01" }
    [pscustomobject]@{ output = "sparkle-02.wav"; source = "switch21.wav"; group = "sparkle"; sound_id = "sparkle-02" }
    [pscustomobject]@{ output = "sparkle-03.wav"; source = "switch22.wav"; group = "sparkle"; sound_id = "sparkle-03" }
)

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is required to import the CC0 sound effects."
}

$ffmpeg = if ($env:IMAGEIO_FFMPEG_EXE) {
    $env:IMAGEIO_FFMPEG_EXE
} else {
    (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
}
if (-not $ffmpeg) {
    throw "FFmpeg was not found. Set IMAGEIO_FFMPEG_EXE or add ffmpeg to PATH."
}

$ffprobe = Join-Path (Split-Path $ffmpeg -Parent) "ffprobe.exe"
if (-not (Test-Path -LiteralPath $ffprobe)) {
    throw "ffprobe.exe was not found next to FFmpeg: $ffprobe"
}

try {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    git clone --depth 1 $sourceRepo $tempDir
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to clone Kenney UI Audio source repository."
    }

    $manifestItems = @()
    foreach ($item in $selected) {
        $sourceFile = Join-Path $sourceAudioDir $item.source
        $outputFile = Join-Path $outputDir $item.output
        if (-not (Test-Path -LiteralPath $sourceFile)) {
            throw "Expected source audio file is missing: $sourceFile"
        }

        & $ffmpeg -y -v error -i $sourceFile -t 0.60 -ac 1 -ar 44100 $outputFile
        if ($LASTEXITCODE -ne 0) {
            throw "FFmpeg failed to convert $($item.source)."
        }

        $durationText = & $ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $outputFile
        if ($LASTEXITCODE -ne 0) {
            throw "ffprobe failed to inspect $($item.output)."
        }
        $manifestItems += [ordered]@{
            sound_id = $item.sound_id
            group = $item.group
            file = "resource/sfx/$($item.output)"
            duration = [math]::Round([double]$durationText, 6)
        }
    }

    Copy-Item -LiteralPath $sourceLicense -Destination (Join-Path $outputDir "LICENSE-KENNEY-CC0.txt") -Force
    [ordered]@{
        source_repo = $sourceRepo
        license = "CC0-1.0"
        source_license_file = "LICENSE.txt"
        sounds = $manifestItems
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $outputDir "manifest.json") -Encoding utf8
}
finally {
    if (
        (Test-Path -LiteralPath $tempDir) -and
        $tempDir.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)
    ) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force
    }
}
