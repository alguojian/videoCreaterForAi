import json
import math
import os.path
import re
from os import path

from loguru import logger

from app.config import config
from app.models import const
from app.models.schema import VideoConcatMode, VideoParams
from app.services import (
    emphasis,
    llm,
    material,
    script_document,
    subtitle,
    twelvelabs,
    upload_post,
    video,
    voice,
)
from app.services import local_voice as local_voice_service
from app.services import state as sm
from app.utils import file_security, utils


class _MarkdownManifestWriteError(RuntimeError):
    def __init__(self, filename: str, error: Exception):
        self.filename = path.basename(filename)
        self.error_type = type(error).__name__
        super().__init__(self.filename, self.error_type)


def _log_markdown_artifact_write_error(
    *,
    stage: str,
    filename: str,
    error_type: str,
) -> None:
    logger.error(
        "Markdown artifact write failed: "
        f"stage={stage}; file={path.basename(filename)}; error_type={error_type}"
    )


def generate_script(task_id, params):
    logger.info("\n\n## generating video script")
    video_script = params.video_script.strip()
    if not video_script:
        video_script = llm.generate_script(
            video_subject=params.video_subject,
            language=params.video_language,
            paragraph_number=params.paragraph_number,
            video_script_prompt=params.video_script_prompt,
            custom_system_prompt=params.custom_system_prompt,
        )
    else:
        logger.debug(f"video script: \n{video_script}")

    if not video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video script.")
        return None

    return video_script


def generate_terms(task_id, params, video_script):
    logger.info("\n\n## generating video terms")
    video_terms = params.video_terms
    if not video_terms:
        # 开启素材按文案顺序匹配后，关键词本身也必须按脚本叙事顺序生成；
        # 否则后续即使顺序下载和顺序拼接，也只能复用一组全局主题词，
        # 无法改善“后面内容的画面提前出现”的问题。
        video_terms = llm.generate_terms(
            video_subject=params.video_subject,
            video_script=video_script,
            amount=8 if params.match_materials_to_script else 5,
            match_script_order=params.match_materials_to_script,
        )
    else:
        if isinstance(video_terms, str):
            video_terms = [term.strip() for term in re.split(r"[,，]", video_terms)]
        elif isinstance(video_terms, list):
            video_terms = [term.strip() for term in video_terms]
        else:
            raise ValueError("video_terms must be a string or a list of strings.")

        logger.debug(f"video terms: {utils.to_json(video_terms)}")

    if not video_terms:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video terms.")
        return None

    # 可选的 TwelveLabs Marengo 语义重排：未启用时返回原顺序，无任何副作用。
    # 顺序匹配模式下关键词顺序本身就是脚本叙事顺序，必须保持原样，故跳过。
    if not params.match_materials_to_script:
        video_terms = twelvelabs.rerank_terms_by_subject(
            video_subject=params.video_subject,
            search_terms=video_terms,
        )

    return video_terms


def _serialize_markdown_artifact(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=4,
        allow_nan=False,
    )


def save_script_data(task_id, video_script, video_terms, params, strict=False):
    script_file = path.join(utils.task_dir(task_id), "script.json")
    normalized_params = (
        params.model_dump(mode="json", warnings=False)
        if isinstance(params, VideoParams)
        else params
    )
    script_data = {
        "script": video_script,
        "search_terms": video_terms,
        "params": normalized_params,
    }

    serialized_script = (
        _serialize_markdown_artifact(script_data)
        if strict
        else utils.to_json(script_data)
    )
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(serialized_script)


def resolve_custom_audio_file(task_id: str, custom_audio_file: str | None) -> str:
    requested_file = (custom_audio_file or "").strip()
    if not requested_file:
        return ""

    task_dir = utils.task_dir(task_id)
    try:
        return file_security.resolve_path_within_directory(
            task_dir,
            requested_file,
        )
    except ValueError as exc:
        task_dir_error = exc

    server_audio_file = path.realpath(
        requested_file
        if path.isabs(requested_file)
        else path.join(utils.root_dir(), requested_file)
    )
    if not path.isabs(requested_file):
        project_root = path.realpath(utils.root_dir())
        try:
            if path.commonpath([project_root, server_audio_file]) != project_root:
                raise ValueError(
                    "relative custom audio paths must stay within the project directory"
                )
        except ValueError as exc:
            raise ValueError(
                "custom audio file must be task-local or an existing server-side file"
            ) from exc

    if not path.isfile(server_audio_file):
        raise ValueError(
            "custom audio file does not exist or is not a file"
        ) from task_dir_error

    return server_audio_file


def _get_local_voice_service() -> local_voice_service.LocalVoiceService:
    settings = local_voice_service.settings_from_config(
        dict(config.local_voice), utils.root_dir()
    )
    return local_voice_service.LocalVoiceService(settings)


def _uses_local_voice(params) -> bool:
    return local_voice_service.is_local_voice_request(
        getattr(params, "voice_name", None), config.ui.get("tts_server")
    )


def _local_alignment_language(value: str | None) -> str:
    language = str(value or "").strip()
    lowered = language.lower()
    if lowered.startswith(("zh", "cmn")) or language in {"中文", "汉语", "Chinese"}:
        return "Chinese"
    if lowered.startswith("en") or language in {"英文", "英语", "English"}:
        return "English"
    return language or "Chinese"


def generate_audio(task_id, params, video_script):
    '''
    Generate audio for the video script.
    If a custom audio file is provided, it will be used directly.
    There will be no subtitle maker object returned in this case.
    Otherwise, TTS will be used to generate the audio.
    Returns:
        - audio_file: path to the generated or provided audio file
        - audio_duration: duration of the audio in seconds
        - sub_maker: subtitle maker object if TTS is used, None otherwise
    '''
    logger.info("\n\n## generating audio")
    # /audio 和 /subtitle 请求模型不包含 custom_audio_file，
    # 这里统一做兼容读取，避免直调接口时抛属性错误。
    requested_custom_audio_file = getattr(params, "custom_audio_file", None)
    try:
        custom_audio_file = resolve_custom_audio_file(
            task_id, requested_custom_audio_file
        )
    except ValueError as exc:
        logger.error(
            "custom audio file is invalid, "
            f"task_id: {task_id}, path: {requested_custom_audio_file}, error: {str(exc)}"
        )
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return None, None, None

    if not custom_audio_file:
        if _uses_local_voice(params):
            logger.info("using local CosyVoice3 for audio generation")
            if not config.local_voice.get("enabled", False):
                sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
                logger.error(
                    "local voice was selected but [local_voice].enabled is false"
                )
                return None, None, None
            try:
                result = _get_local_voice_service().synthesize(
                    task_id,
                    utils.task_dir(task_id),
                    video_script,
                    getattr(params, "voice_name", ""),
                )
            except local_voice_service.LocalVoiceError as exc:
                sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
                logger.error(f"failed to generate local voice audio: {exc}")
                return None, None, None
            return str(result.audio_file), math.ceil(result.duration), None

        logger.info("no custom audio file provided, using TTS to generate audio.")
        audio_file = path.join(utils.task_dir(task_id), "audio.mp3")
        sub_maker = voice.tts(
            text=video_script,
            voice_name=voice.parse_voice_name(params.voice_name),
            voice_rate=params.voice_rate,
            voice_file=audio_file,
        )
        if sub_maker is None:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                """failed to generate audio:
1. check if the language of the voice matches the language of the video script.
2. check if the network is available. If you are in China, it is recommended to use a VPN and enable the global traffic mode.
            """.strip()
            )
            return None, None, None
        audio_duration = math.ceil(voice.get_audio_duration(sub_maker))
        if audio_duration == 0:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error("failed to get audio duration.")
            return None, None, None
        return audio_file, audio_duration, sub_maker
    else:
        logger.info(f"using custom audio file: {custom_audio_file}")
        audio_duration = voice.get_audio_duration(custom_audio_file)
        if audio_duration == 0:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error("failed to get audio duration from custom audio file.")
            return None, None, None
        return custom_audio_file, audio_duration, None

def generate_subtitle(task_id, params, video_script, sub_maker, audio_file):
    '''
    Generate subtitle for the video script.
    If subtitle generation is disabled or no subtitle maker is provided, it will return an empty string.
    Otherwise, it will generate the subtitle using the specified provider.
    Returns:
        - subtitle_path: path to the generated subtitle file
    '''
    logger.info("\n\n## generating subtitle")
    if not params.subtitle_enabled:
        return ""

    subtitle_path = path.join(utils.task_dir(task_id), "subtitle.srt")

    if local_voice_service.is_local_audio_artifact(utils.task_dir(task_id), audio_file):
        settings = local_voice_service.settings_from_config(
            dict(config.local_voice), utils.root_dir()
        )
        provider = settings.subtitle_provider.strip().lower()
        if provider in {"qwen", "qwen_forced_aligner", "forced_aligner"}:
            language = _local_alignment_language(
                getattr(params, "video_language", "")
            )
            try:
                generated = local_voice_service.LocalVoiceService(settings).align_subtitle(
                    task_id,
                    utils.task_dir(task_id),
                    audio_file,
                    video_script,
                    language=language,
                    subtitle_file=subtitle_path,
                )
            except local_voice_service.LocalVoiceError as exc:
                logger.warning(f"local subtitle alignment failed: {exc}")
                if settings.subtitle_fallback.strip().lower() != "whisper":
                    return ""
                subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
                subtitle.correct(subtitle_file=subtitle_path, video_script=video_script)
                subtitle_lines = subtitle.file_to_subtitles(subtitle_path)
                if not subtitle_lines:
                    logger.warning(f"fallback subtitle file is invalid: {subtitle_path}")
                    return ""
                return subtitle_path
            else:
                subtitle_lines = subtitle.file_to_subtitles(str(generated))
                if not subtitle_lines:
                    logger.warning(f"subtitle file is invalid: {generated}")
                    return ""
                return str(generated)

    subtitle_provider = config.app.get("subtitle_provider", "edge").strip().lower()
    logger.info(f"\n\n## generating subtitle, provider: {subtitle_provider}")

    if sub_maker is None and subtitle_provider != "whisper":
        # 自定义音频不会经过 TTS，因此没有 Edge/Azure 等 TTS 返回的
        # sub_maker 时间轴。只有 Whisper 可以直接从音频文件转写字幕；
        # 其他字幕提供方继续保持原有行为，避免生成错误的空时间轴。
        logger.warning(
            "subtitle maker is missing, skip subtitle generation for provider: "
            f"{subtitle_provider}"
        )
        return ""

    subtitle_fallback = False
    if subtitle_provider == "edge":
        voice.create_subtitle(
            text=video_script, sub_maker=sub_maker, subtitle_file=subtitle_path
        )
        if not os.path.exists(subtitle_path):
            subtitle_fallback = True
            logger.warning("subtitle file not found, fallback to whisper")

    if subtitle_provider == "whisper" or subtitle_fallback:
        subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
        logger.info("\n\n## correcting subtitle")
        subtitle.correct(subtitle_file=subtitle_path, video_script=video_script)

    subtitle_lines = subtitle.file_to_subtitles(subtitle_path)
    if not subtitle_lines:
        logger.warning(f"subtitle file is invalid: {subtitle_path}")
        return ""

    return subtitle_path


def generate_emphasis(task_id, params, video_script, subtitle_path) -> str:
    if not params.emphasis_enabled or not subtitle_path:
        return ""

    logger.info("\n\n## generating emphasis cues")
    subtitles = subtitle.file_to_subtitles(subtitle_path)
    if not subtitles:
        logger.warning(f"subtitle file is invalid for emphasis cues: {subtitle_path}")
        return ""

    manual_terms = emphasis.parse_manual_terms(params.emphasis_terms)
    automatic_terms = (
        [] if manual_terms else llm.generate_emphasis_terms(video_script)
    )
    cues = emphasis.build_emphasis_cues(
        task_id=task_id,
        subtitles=subtitles,
        automatic_terms=automatic_terms,
        manual_terms=params.emphasis_terms,
        random_colors=params.emphasis_random_colors,
        random_animations=params.emphasis_random_animations,
    )
    output_path = path.join(utils.task_dir(task_id), "emphasis.json")
    emphasis.write_emphasis_cues(cues, output_path)
    return output_path


def _markdown_runtime(params, subtitle_path, target_video_duration=None):
    document = params.markdown_script
    if document is None:
        return None
    if not subtitle_path:
        raise script_document.MarkdownAlignmentError(
            "Markdown 视频缺少可用于场景和重点词对齐的字幕时间轴"
        )

    subtitles = subtitle.file_to_subtitles(subtitle_path)
    if not subtitles:
        raise script_document.MarkdownAlignmentError(
            f"Markdown 视频字幕时间轴无效：{subtitle_path}"
        )
    timed_rows = script_document.align_rows_to_subtitles(document, subtitles)
    timed_scenes = script_document.build_timed_scenes(
        document,
        timed_rows,
        total_duration=target_video_duration,
    )
    return document, timed_rows, timed_scenes


def _generate_markdown_emphasis(task_id, params, timed_rows) -> str:
    if not params.emphasis_enabled:
        return ""

    logger.info("\n\n## generating markdown emphasis cues")
    cues = emphasis.build_markdown_emphasis_cues(
        task_id=task_id,
        timed_rows=timed_rows,
        random_colors=params.emphasis_random_colors,
        random_animations=params.emphasis_random_animations,
    )
    output_path = path.join(utils.task_dir(task_id), "emphasis.json")
    try:
        emphasis.write_emphasis_cues(cues, output_path)
    except (OSError, TypeError, ValueError) as exc:
        raise _MarkdownManifestWriteError("emphasis.json", exc) from exc
    return output_path


def _write_scene_material_manifest(task_id, scene_materials) -> str:
    manifest_path = path.join(utils.task_dir(task_id), "scene-materials.json")
    manifest = [
        {
            "scene_index": item.scene_index,
            "first_row": item.first_row,
            "last_row": item.last_row,
            "start": item.start,
            "end": item.end,
            "required_duration": item.required_duration,
            "search_terms": list(item.search_terms),
            "video_paths": list(item.video_paths),
        }
        for item in scene_materials
    ]
    try:
        serialized_manifest = _serialize_markdown_artifact(manifest)
        with open(manifest_path, "w", encoding="utf-8") as manifest_file:
            manifest_file.write(serialized_manifest)
    except (OSError, TypeError, ValueError) as exc:
        raise _MarkdownManifestWriteError("scene-materials.json", exc) from exc
    return manifest_path


def get_video_materials(task_id, params, video_terms, audio_duration):
    if params.video_source == "local":
        logger.info("\n\n## preprocess local materials")
        materials = video.preprocess_video(
            materials=params.video_materials, clip_duration=params.video_clip_duration
        )
        if not materials:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "no valid materials found, please check the materials and try again."
            )
            return None
        return [material_info.url for material_info in materials]
    else:
        logger.info(f"\n\n## downloading videos from {params.video_source}")
        # 顺序匹配模式只在用户显式开启时生效。这里强制素材下载按关键词顺序
        # 轮询，避免某个早期关键词下载太多素材，把后续脚本主题挤出最终时间线。
        downloaded_videos = material.download_videos(
            task_id=task_id,
            search_terms=video_terms,
            source=params.video_source,
            video_aspect=params.video_aspect,
            video_concat_mode=(
                VideoConcatMode.sequential
                if params.match_materials_to_script
                else params.video_concat_mode
            ),
            audio_duration=audio_duration * params.video_count,
            max_clip_duration=params.video_clip_duration,
            match_script_order=params.match_materials_to_script,
        )
        if not downloaded_videos:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "failed to download videos, maybe the network is not available. if you are in China, please use a VPN."
            )
            return None
        return downloaded_videos


def generate_final_videos(
    task_id,
    params,
    downloaded_videos,
    audio_file,
    subtitle_path,
    emphasis_path="",
    scene_materials=None,
):
    final_video_paths = []
    combined_video_paths = []
    # 多视频生成默认会打散素材以增加差异；但“按文案顺序匹配素材”追求的是
    # 时间线稳定性和可解释性，所以开启后所有输出都使用顺序拼接。
    if params.match_materials_to_script:
        video_concat_mode = VideoConcatMode.sequential
    elif params.video_count == 1:
        video_concat_mode = params.video_concat_mode
    else:
        video_concat_mode = VideoConcatMode.random
    video_transition_mode = params.video_transition_mode

    _progress = 50
    for i in range(params.video_count):
        index = i + 1
        combined_video_path = path.join(
            utils.task_dir(task_id), f"combined-{index}.mp4"
        )
        logger.info(f"\n\n## combining video: {index} => {combined_video_path}")
        if scene_materials is not None:
            video.combine_scene_videos(
                combined_video_path=combined_video_path,
                scene_plans=scene_materials,
                video_aspect=params.video_aspect,
                video_transition_mode=video_transition_mode,
                max_clip_duration=params.video_clip_duration,
                threads=params.n_threads,
                clip_speed=params.video_clip_speed,
            )
        else:
            video.combine_videos(
                combined_video_path=combined_video_path,
                video_paths=downloaded_videos,
                audio_file=audio_file,
                video_aspect=params.video_aspect,
                video_concat_mode=video_concat_mode,
                video_transition_mode=video_transition_mode,
                max_clip_duration=params.video_clip_duration,
                threads=params.n_threads,
                clip_speed=params.video_clip_speed,
            )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_path = path.join(utils.task_dir(task_id), f"final-{index}.mp4")

        logger.info(f"\n\n## generating video: {index} => {final_video_path}")
        video.generate_video(
            video_path=combined_video_path,
            audio_path=audio_file,
            subtitle_path=subtitle_path,
            output_file=final_video_path,
            params=params,
            emphasis_path=emphasis_path,
        )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_paths.append(final_video_path)
        combined_video_paths.append(combined_video_path)

    return final_video_paths, combined_video_paths


def start(task_id, params: VideoParams, stop_at: str = "video"):
    logger.info(f"start task: {task_id}, stop_at: {stop_at}")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)

    markdown_document = params.markdown_script
    markdown_scenes = []
    if markdown_document is not None:
        try:
            markdown_scenes = script_document.build_scenes(markdown_document)
        except script_document.MarkdownAlignmentError as exc:
            logger.error(f"invalid Markdown script: {exc}")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

        markdown_source = str(params.video_source or "").strip().lower()
        if markdown_source not in {"pexels", "pixabay", "coverr"}:
            logger.error(
                "Markdown 视频只支持在线素材源：pexels、pixabay、coverr"
            )
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

    # 1. Generate script
    if markdown_document is not None:
        script_document.apply_to_video_params(markdown_document, params)
        video_script = markdown_document.script_text()
    else:
        video_script = generate_script(task_id, params)
    if not video_script or "Error: " in video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=10)

    if stop_at == "script":
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, script=video_script
        )
        return {"script": video_script}

    # 2. Generate terms
    video_terms = ""
    if markdown_document is not None:
        video_terms = [
            term
            for scene in markdown_scenes
            for term in scene.search_terms
        ]
    elif params.video_source != "local":
        video_terms = generate_terms(task_id, params, video_script)
        if not video_terms:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

    if markdown_document is None:
        save_script_data(task_id, video_script, video_terms, params)
    else:
        try:
            save_script_data(
                task_id,
                video_script,
                video_terms,
                params,
                strict=True,
            )
        except (OSError, TypeError, ValueError) as exc:
            _log_markdown_artifact_write_error(
                stage="script manifest",
                filename="script.json",
                error_type=type(exc).__name__,
            )
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

    if stop_at == "terms":
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, terms=video_terms
        )
        return {"script": video_script, "terms": video_terms}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)

    # 3. Generate audio
    audio_file, audio_duration, sub_maker = generate_audio(
        task_id, params, video_script
    )
    if not audio_file:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=30)

    if stop_at == "audio":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            audio_file=audio_file,
        )
        return {"audio_file": audio_file, "audio_duration": audio_duration}

    markdown_target_video_duration = None
    if markdown_document is not None:
        try:
            exact_audio_duration = voice.get_audio_duration(audio_file)
            if isinstance(exact_audio_duration, bool):
                raise ValueError("bool is not an audio duration")
            exact_audio_duration = float(exact_audio_duration)
        except (OSError, OverflowError, TypeError, ValueError):
            logger.error("failed to read exact Markdown audio duration")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        if not math.isfinite(exact_audio_duration) or exact_audio_duration <= 0:
            logger.error("Markdown audio duration must be finite and positive")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        markdown_target_video_duration = video.get_required_video_duration(
            exact_audio_duration
        )

    # 4. Generate subtitle
    alignment_subtitle_path = ""
    if markdown_document is not None:
        subtitle_params = (
            params
            if params.subtitle_enabled
            else params.model_copy(update={"subtitle_enabled": True})
        )
        alignment_subtitle_path = generate_subtitle(
            task_id, subtitle_params, video_script, sub_maker, audio_file
        )
        subtitle_path = alignment_subtitle_path if params.subtitle_enabled else ""
    else:
        subtitle_path = generate_subtitle(
            task_id, params, video_script, sub_maker, audio_file
        )

    if stop_at == "subtitle":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            subtitle_path=subtitle_path,
        )
        return {"subtitle_path": subtitle_path}

    markdown_timed_scenes = None
    if markdown_document is not None:
        try:
            _, markdown_timed_rows, markdown_timed_scenes = _markdown_runtime(
                params,
                alignment_subtitle_path,
                target_video_duration=markdown_target_video_duration,
            )
            emphasis_path = _generate_markdown_emphasis(
                task_id, params, markdown_timed_rows
            )
        except _MarkdownManifestWriteError as exc:
            _log_markdown_artifact_write_error(
                stage="emphasis manifest",
                filename=exc.filename,
                error_type=exc.error_type,
            )
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        except (script_document.MarkdownAlignmentError, ValueError) as exc:
            logger.error(f"failed to align Markdown script: {exc}")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
    else:
        emphasis_path = generate_emphasis(
            task_id, params, video_script, subtitle_path
        )

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=40)

    # 5. Get video materials
    scene_materials = None
    if markdown_document is not None:
        try:
            scene_materials = material.download_scene_materials(
                task_id=task_id,
                scenes=markdown_timed_scenes,
                source=params.video_source,
                video_aspect=params.video_aspect,
                max_clip_duration=params.video_clip_duration,
            )
        except Exception as exc:
            logger.error(f"failed to download Markdown scene materials: {exc}")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        if not scene_materials:
            logger.error("failed to download Markdown scene materials")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        downloaded_videos = [
            video_path
            for scene_item in scene_materials
            for video_path in scene_item.video_paths
        ]
        if not downloaded_videos:
            logger.error("Markdown scene materials contain no video files")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        try:
            _write_scene_material_manifest(task_id, scene_materials)
        except _MarkdownManifestWriteError as exc:
            _log_markdown_artifact_write_error(
                stage="scene manifest",
                filename=exc.filename,
                error_type=exc.error_type,
            )
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
    else:
        downloaded_videos = get_video_materials(
            task_id, params, video_terms, audio_duration
        )
    if not downloaded_videos:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    if stop_at == "materials":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            materials=downloaded_videos,
        )
        return {"materials": downloaded_videos}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=50)

    # 仅完整视频生成流程才需要处理视频拼接模式；
    # 这样可以避免 /subtitle 和 /audio 这类请求访问不存在的字段。
    if type(params.video_concat_mode) is str:
        params.video_concat_mode = VideoConcatMode(params.video_concat_mode)

    # 6. Generate final videos
    try:
        final_video_paths, combined_video_paths = generate_final_videos(
            task_id=task_id,
            params=params,
            downloaded_videos=downloaded_videos,
            audio_file=audio_file,
            subtitle_path=subtitle_path,
            emphasis_path=emphasis_path,
            scene_materials=scene_materials,
        )
    except Exception as exc:
        if markdown_document is None:
            raise
        logger.error(f"failed to generate Markdown video: {exc}")
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    if not final_video_paths:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    logger.success(
        f"task {task_id} finished, generated {len(final_video_paths)} videos."
    )

    # 7. Cross-post to social platforms (if enabled)
    cross_post_results = []
    if upload_post.upload_post_service.is_configured() and upload_post.upload_post_service.auto_upload:
        platforms = upload_post.upload_post_service.platforms
        logger.info(f"\n\n## cross-posting videos to {', '.join(platforms)}")

        youtube_extra = None
        if any(p.startswith("youtube") for p in platforms):
            if markdown_document is not None:
                youtube_extra = {
                    "youtube_title": markdown_document.title,
                    "youtube_description": "",
                    "tags": [],
                    "privacyStatus": upload_post.upload_post_service.youtube_privacy_status,
                    "containsSyntheticMedia": True,
                }
            else:
                metadata = llm.generate_social_metadata(
                    video_subject=params.video_subject,
                    video_script=video_script,
                    language=params.video_language or "",
                    platform="youtube_shorts",
                )
                youtube_extra = {
                    "youtube_title": metadata.get("title", params.video_subject),
                    "youtube_description": metadata.get("caption", ""),
                    "tags": metadata.get("hashtags", []),
                    "privacyStatus": upload_post.upload_post_service.youtube_privacy_status,
                    "containsSyntheticMedia": True,
                }

        for video_path in final_video_paths:
            result = upload_post.cross_post_video(
                video_path=video_path,
                title=params.video_subject or "Check out this video! #shorts #viral",
                youtube_extra=youtube_extra,
            )
            cross_post_results.append(result)
            if result.get('success'):
                logger.info(f"✅ Cross-posted: {video_path}")
            else:
                logger.warning(f"⚠️ Failed to cross-post: {video_path} - {result.get('error', 'Unknown error')}")

    kwargs = {
        "videos": final_video_paths,
        "combined_videos": combined_video_paths,
        "script": video_script,
        "terms": video_terms,
        "audio_file": audio_file,
        "audio_duration": audio_duration,
        "subtitle_path": subtitle_path,
        "emphasis_path": emphasis_path,
        "materials": downloaded_videos,
        "cross_post_results": cross_post_results if cross_post_results else None,
    }
    sm.state.update_task(
        task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs
    )
    return kwargs


if __name__ == "__main__":
    task_id = "task_id"
    params = VideoParams(
        video_subject="金钱的作用",
        voice_name="zh-CN-XiaoyiNeural-Female",
        voice_rate=1.0,
    )
    start(task_id, params, stop_at="video")
