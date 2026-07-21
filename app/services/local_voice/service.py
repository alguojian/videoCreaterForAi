from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .audio_processor import concatenate_blocks, validate_wav, write_manifest
from .exceptions import InvalidVoiceProfileError, LocalVoiceError
from .models import AlignmentCharacter, PipelineManifest
from .profile_store import ProfileStore
from .subtitle_builder import build_cues, write_srt
from .text_normalizer import normalize_narration, split_into_blocks
from .worker_runner import run_worker


DEFAULT_REFERENCE_TEXT = "希望你以后能够做的比我还好呦。"
DEFAULT_LANGUAGE = "Chinese"


def _project_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _repair_alignment_payloads(
    payloads: list[dict[str, Any]], audio_duration: float
) -> list[dict[str, Any]]:
    """Repair rare zero-width model spans before strict domain validation."""
    repaired: list[dict[str, Any]] = []
    for index, payload in enumerate(payloads):
        item = dict(payload)
        start = float(item["start"])
        end = float(item["end"])
        if end <= start:
            next_start = (
                float(payloads[index + 1]["start"])
                if index + 1 < len(payloads)
                else audio_duration
            )
            end = min(audio_duration, next_start) if next_start > start else min(
                audio_duration, start + 0.02
            )
            if end <= start:
                raise LocalVoiceError(
                    f"cannot repair zero-length alignment span at index {index}"
                )
        item["start"] = start
        item["end"] = end
        repaired.append(item)
    return repaired


@dataclass(frozen=True)
class LocalVoiceSettings:
    project_root: Path
    enabled: bool = False
    use_rl_model: bool = False
    cosyvoice_python: Path = Path("tools/cosyvoice312/python.exe")
    aligner_python: Path = Path("tools/miniforge3/envs/qwen-aligner/python.exe")
    cosyvoice_worker: Path = Path("workers/cosyvoice_worker/generate.py")
    aligner_worker: Path = Path("workers/qwen_aligner_worker/align.py")
    cosyvoice_model_dir: Path = Path("resource/models/Fun-CosyVoice3-0.5B-2512")
    aligner_model_dir: Path = Path("resource/models/Qwen3-ForcedAligner-0.6B")
    cosyvoice_repo: Path = Path("third_party/CosyVoice")
    voice_profile_root: Path = Path("resource/voice_profiles")
    default_voice_profile: str = ""
    worker_timeout_seconds: float = 600.0
    sample_rate: int = 24000
    channels: int = 1
    block_max_chars: int = 200
    block_pause_ms: int = 250
    subtitle_provider: str = "qwen_forced_aligner"
    subtitle_min_duration_ms: int = 850
    subtitle_max_duration_ms: int = 4200
    subtitle_max_chars: int = 18
    subtitle_gap_ms: int = 40
    subtitle_fallback: str = "whisper"

    @classmethod
    def from_mapping(
        cls, mapping: dict[str, Any] | None, *, project_root: str | Path
    ) -> "LocalVoiceSettings":
        values = mapping or {}
        root = Path(project_root).expanduser().resolve()

        def configured_path(key: str, default: str | Path) -> Path:
            value = values.get(key, default)
            return _project_path(root, value or default)

        return cls(
            project_root=root,
            enabled=bool(values.get("enabled", False)),
            use_rl_model=bool(values.get("use_rl_model", False)),
            cosyvoice_python=configured_path("cosyvoice_python", cls.cosyvoice_python),
            aligner_python=configured_path("aligner_python", cls.aligner_python),
            cosyvoice_worker=configured_path("cosyvoice_worker", cls.cosyvoice_worker),
            aligner_worker=configured_path("aligner_worker", cls.aligner_worker),
            cosyvoice_model_dir=configured_path("cosyvoice_model_dir", cls.cosyvoice_model_dir),
            aligner_model_dir=configured_path("aligner_model_dir", cls.aligner_model_dir),
            cosyvoice_repo=configured_path("cosyvoice_repo", cls.cosyvoice_repo),
            voice_profile_root=configured_path("voice_profile_root", cls.voice_profile_root),
            default_voice_profile=str(values.get("default_voice_profile", "") or "").strip(),
            worker_timeout_seconds=float(values.get("worker_timeout_seconds", 600)),
            sample_rate=int(values.get("sample_rate", 24000)),
            channels=int(values.get("channels", 1)),
            block_max_chars=int(values.get("block_max_chars", 200)),
            block_pause_ms=int(values.get("block_pause_ms", 250)),
            subtitle_provider=str(values.get("subtitle_provider", "qwen_forced_aligner")),
            subtitle_min_duration_ms=int(values.get("subtitle_min_duration_ms", 850)),
            subtitle_max_duration_ms=int(values.get("subtitle_max_duration_ms", 4200)),
            subtitle_max_chars=int(values.get("subtitle_max_chars", 18)),
            subtitle_gap_ms=int(values.get("subtitle_gap_ms", 40)),
            subtitle_fallback=str(values.get("subtitle_fallback", "whisper")),
        )


@dataclass(frozen=True)
class LocalVoiceAudioResult:
    audio_file: Path
    duration: float
    spoken_text: str
    display_text: str
    manifest_file: Path


Runner = Callable[..., dict[str, Any]]
ProgressCallback = Callable[[str, int, int, str], None]


class LocalVoiceService:
    def __init__(
        self,
        settings: LocalVoiceSettings,
        *,
        runner: Runner = run_worker,
    ) -> None:
        self.settings = settings
        self.runner = runner

    def available_voice_names(self) -> list[str]:
        names = [f"local:{profile.profile_id}" for profile in ProfileStore(self.settings.voice_profile_root).list_profiles()]
        return names or ["local:default"]

    def _profile(self, voice_name: str):
        profile_id = voice_name.split(":", 1)[1] if voice_name.startswith("local:") else ""
        # `local:default` is the stable UI/API name for the configured default
        # profile.  Without this explicit mapping it would always fall back to
        # CosyVoice's bundled reference, even when a custom default exists.
        if profile_id in {"", "default"}:
            profile_id = self.settings.default_voice_profile or profile_id
        if profile_id in {"", "default"}:
            reference = self.settings.cosyvoice_repo / "asset" / "zero_shot_prompt.wav"
            if not reference.is_file():
                raise InvalidVoiceProfileError(f"built-in reference audio does not exist: {reference}")
            return reference, DEFAULT_REFERENCE_TEXT, ""

        store = ProfileStore(self.settings.voice_profile_root)
        profile = store.get_profile(profile_id)
        reference = self.settings.voice_profile_root / profile.profile_id / profile.reference_audio
        if not reference.is_file():
            raise InvalidVoiceProfileError(f"profile reference audio does not exist: {reference}")
        return reference, profile.reference_text, profile.default_instruction

    def _run(
        self,
        python: Path,
        worker: Path,
        request: dict[str, Any],
        task_dir: Path,
        progress_callback=None,
    ) -> dict[str, Any]:
        kwargs = {
            "project_root": self.settings.project_root,
            "timeout_seconds": self.settings.worker_timeout_seconds,
        }
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback
        return self.runner(
            python,
            worker,
            request,
            task_dir,
            **kwargs,
        )

    def synthesize(
        self,
        task_id: str,
        task_dir: str | Path,
        text: str,
        voice_name: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> LocalVoiceAudioResult:
        spoken_text, display_text = normalize_narration(text)
        blocks = split_into_blocks(spoken_text, self.settings.block_max_chars)
        reference_audio, reference_text, instruction = self._profile(voice_name)
        root = Path(task_dir).expanduser().resolve() / "local_voice"
        root.mkdir(parents=True, exist_ok=True)
        block_paths: list[Path] = []
        stages: dict[str, str] = {"normalize": "completed", "tts": "running"}
        block_requests: list[dict[str, str]] = []

        for index, block_text in enumerate(blocks, start=1):
            block_dir = root / f"block-{index:03d}"
            block_dir.mkdir(parents=True, exist_ok=True)
            output_wav = block_dir / "speech.wav"
            block_requests.append(
                {
                    "block_id": f"{index:03d}",
                    "spoken_text": block_text,
                    "output_wav": str(output_wav),
                }
            )
            block_paths.append(output_wav)

        if progress_callback:
            progress_callback("audio", 0, len(block_requests), "loading CosyVoice model")
        request = {
            "model_dir": str(self.settings.cosyvoice_model_dir),
            "reference_audio": str(reference_audio),
            "reference_text": reference_text,
            "instruction": instruction,
            "cosyvoice_repo": str(self.settings.cosyvoice_repo),
            "use_rl_model": self.settings.use_rl_model,
            "blocks": block_requests,
        }
        result = self._run(
            self.settings.cosyvoice_python,
            self.settings.cosyvoice_worker,
            request,
            root,
            progress_callback=progress_callback,
        )
        if result.get("status") != "completed":
            raise LocalVoiceError("CosyVoice did not complete the audio blocks")
        completed_blocks = int(result.get("blocks_completed", len(block_paths)))
        if completed_blocks != len(block_paths):
            raise LocalVoiceError(
                f"CosyVoice completed {completed_blocks}/{len(block_paths)} blocks"
            )
        for index, output_wav in enumerate(block_paths, start=1):
            validate_wav(
                output_wav,
                sample_rate=self.settings.sample_rate,
                channels=self.settings.channels,
            )
            if progress_callback:
                progress_callback(
                    "audio",
                    index,
                    len(block_paths),
                    f"CosyVoice block {index}/{len(block_paths)}",
                )

        output_audio = Path(task_dir).expanduser().resolve() / "audio.wav"
        duration = concatenate_blocks(
            block_paths,
            output_audio,
            pause_ms=self.settings.block_pause_ms,
            sample_rate=self.settings.sample_rate,
            channels=self.settings.channels,
        )
        stages["tts"] = "completed"
        stages["audio"] = "completed"
        manifest_file = Path(task_dir).expanduser().resolve() / "local_voice_manifest.json"
        write_manifest(
            PipelineManifest(
                task_id=task_id,
                stages=stages,
                artifacts={"audio": str(output_audio), "reference_audio": str(reference_audio)},
            ),
            manifest_file,
        )
        return LocalVoiceAudioResult(output_audio, duration, spoken_text, display_text, manifest_file)

    def align_subtitle(
        self,
        task_id: str,
        task_dir: str | Path,
        audio_file: str | Path,
        text: str,
        *,
        language: str = DEFAULT_LANGUAGE,
        subtitle_file: str | Path | None = None,
    ) -> Path:
        spoken_text, display_text = normalize_narration(text)
        task_path = Path(task_dir).expanduser().resolve()
        result = self._run(
            self.settings.aligner_python,
            self.settings.aligner_worker,
            {
                "model_dir": str(self.settings.aligner_model_dir),
                "audio_file": str(Path(audio_file).expanduser().resolve()),
                "spoken_text": spoken_text,
                "language": language or DEFAULT_LANGUAGE,
            },
            task_path / "local_voice_alignment",
        )
        duration = validate_wav(
            audio_file,
            sample_rate=self.settings.sample_rate,
            channels=self.settings.channels,
        )
        alignment_payloads = _repair_alignment_payloads(
            result.get("alignment", []), duration
        )
        alignment = [
            AlignmentCharacter.from_dict(item) for item in alignment_payloads
        ]
        if not alignment:
            raise LocalVoiceError("Qwen ForcedAligner returned no alignment")
        cues = build_cues(
            alignment,
            display_text,
            min_duration=self.settings.subtitle_min_duration_ms / 1000,
            max_duration=self.settings.subtitle_max_duration_ms / 1000,
            max_chars=self.settings.subtitle_max_chars,
            gap_seconds=self.settings.subtitle_gap_ms / 1000,
            audio_duration=duration,
        )
        output = Path(subtitle_file).expanduser().resolve() if subtitle_file else task_path / "subtitle.srt"
        write_srt(cues, output)
        return output


def settings_from_config(config_mapping: dict[str, Any], project_root: str | Path) -> LocalVoiceSettings:
    return LocalVoiceSettings.from_mapping(config_mapping, project_root=project_root)


def is_local_voice_request(voice_name: str | None, tts_server: str | None = None) -> bool:
    name = str(voice_name or "").strip().lower()
    return name.startswith("local:") or str(tts_server or "").strip().lower() == "local-cosyvoice"


def is_local_audio_artifact(task_dir: str | Path, audio_file: str | Path) -> bool:
    return (
        (Path(task_dir).expanduser().resolve() / "local_voice_manifest.json").is_file()
        and Path(audio_file).expanduser().resolve().name == "audio.wav"
    )
