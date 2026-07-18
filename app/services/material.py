import math
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List
from urllib.parse import urlencode

import requests
from loguru import logger
from moviepy.video.io.VideoFileClip import VideoFileClip

from app.config import config
from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode
from app.services.script_document import TimedScriptScene
from app.utils import utils

# Thread-safe counter for API key rotation
_api_key_counter = 0
_api_key_lock = threading.Lock()
_ASPECT_RATIO_TOLERANCE = 0.03


class SceneMaterialError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedSceneMaterials:
    scene_index: int
    first_row: int
    last_row: int
    start: float
    end: float
    required_duration: float
    search_terms: tuple[str, ...]
    video_paths: tuple[str, ...]


def _select_matching_video_file(
    video_files, video_aspect: VideoAspect, url_field: str
):
    """Choose a sufficiently large file with the requested orientation and ratio."""
    target_width, target_height = VideoAspect(video_aspect).to_resolution()
    target_ratio = target_width / target_height
    if target_width == target_height:
        target_orientation = "square"
    elif target_width < target_height:
        target_orientation = "portrait"
    else:
        target_orientation = "landscape"

    candidates = []
    values = video_files.values() if isinstance(video_files, dict) else video_files
    for video in values:
        try:
            width = int(video.get("width", 0))
            height = int(video.get("height", 0))
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0 or not video.get(url_field):
            continue
        if width == height:
            orientation = "square"
        elif width < height:
            orientation = "portrait"
        else:
            orientation = "landscape"
        if orientation != target_orientation:
            continue
        if width < target_width or height < target_height:
            continue
        ratio_error = abs((width / height) - target_ratio) / target_ratio
        if ratio_error > _ASPECT_RATIO_TOLERANCE:
            continue
        candidates.append((ratio_error, -(width * height), video))

    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _get_tls_verify() -> bool:
    # 默认开启 TLS 证书校验，防止素材搜索和下载过程被中间人篡改。
    # 仅在企业代理、自签证书等明确需要的场景下，允许用户通过
    # `config.toml` 显式设置 `tls_verify = false` 临时关闭。
    tls_verify = config.app.get("tls_verify", True)
    if isinstance(tls_verify, str):
        tls_verify = tls_verify.strip().lower() not in ("0", "false", "no", "off")

    if not tls_verify:
        logger.warning(
            "TLS certificate verification is disabled by config.app.tls_verify=false. "
            "Only use this in trusted proxy environments."
        )

    return bool(tls_verify)


def get_api_key(cfg_key: str):
    api_keys = config.app.get(cfg_key)
    if not api_keys:
        raise ValueError(
            f"\n\n##### {cfg_key} is not set #####\n\nPlease set it in the config.toml file: {config.config_file}\n\n"
            f"{utils.to_json(config.app)}"
        )

    # if only one key is provided, return it
    if isinstance(api_keys, str):
        return api_keys

    global _api_key_counter
    with _api_key_lock:
        _api_key_counter += 1
        return api_keys[_api_key_counter % len(api_keys)]


def search_videos_pexels(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    video_orientation = aspect.name
    api_key = get_api_key("pexels_api_keys")
    headers = {
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    }
    # Build URL
    params = {"query": search_term, "per_page": 20, "orientation": video_orientation}
    query_url = f"https://api.pexels.com/videos/search?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers=headers,
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        response = r.json()
        video_items = []
        if "videos" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["videos"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video = _select_matching_video_file(
                v["video_files"], aspect, "link"
            )
            if video:
                item = MaterialInfo()
                item.provider = "pexels"
                item.url = video["link"]
                item.duration = duration
                video_items.append(item)
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_videos_pixabay(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)

    api_key = get_api_key("pixabay_api_keys")
    # Build URL
    params = {
        "q": search_term,
        "video_type": "all",  # Accepted values: "all", "film", "animation"
        "per_page": 50,
        "key": api_key,
    }
    query_url = f"https://pixabay.com/api/videos/?{urlencode(params)}"
    logger.info(
        f"searching Pixabay videos for query: {search_term}, "
        f"with proxies: {config.proxy}"
    )

    try:
        r = requests.get(
            query_url, proxies=config.proxy, verify=_get_tls_verify(), timeout=(30, 60)
        )
        response = r.json()
        video_items = []
        if "hits" not in response:
            logger.error(
                f"Pixabay search failed for query {search_term!r}: invalid response"
            )
            return video_items
        videos = response["hits"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video = _select_matching_video_file(v["videos"], aspect, "url")
            if video:
                item = MaterialInfo()
                item.provider = "pixabay"
                item.url = video["url"]
                item.duration = duration
                video_items.append(item)
        return video_items
    except Exception as e:
        logger.error(
            f"Pixabay search failed for query {search_term!r}: "
            f"{type(e).__name__}"
        )

    return []


def search_videos_coverr(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    """
    Coverr (https://coverr.co) - free HD/4K stock videos,
    subject to Coverr license terms (https://coverr.co/license).

    Coverr API notes (based on official docs at api.coverr.co/docs/):
      - 鉴权: Authorization: Bearer <api_key>
      - 搜索端点: GET /videos?query=...,响应结构 {"hits": [...], ...}
      - 加 ?urls=true 在搜索响应里直接返回 mp4 直链
      - URL 是 signed JWT(绑定 API key,无过期时间)
      - Coverr 库以 16:9 横屏为主,9:16 portrait 占比极低(约 1%)
        因此本函数不做 aspect_ratio 过滤,由下游 video.py 的
        resize + letterbox 逻辑统一处理
      - duration 字段同时存在 number 和 string 两种形态,本函数都接受

    本函数使用 urls.mp4_download 字段作为下载地址 —— 按 Coverr 官方文档
    (https://api.coverr.co/docs/videos/#download-a-video) 的说法,
    GET 这个 URL 本身就被 Coverr 当作一次合法的 download 事件计入统计,
    无需再调用 PATCH /videos/:id/stats/downloads。
    """
    api_key = get_api_key("coverr_api_keys")
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {
        "query": search_term,
        "page_size": 20,
        "urls": "true",
        "sort": "popular",
    }
    query_url = f"https://api.coverr.co/videos?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers=headers,
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        response = r.json()
        video_items: List[MaterialInfo] = []

        if not isinstance(response, dict) or "hits" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items

        for v in response["hits"]:
            # duration 在不同响应里可能是 number(11.625) 或 string("10.500000")
            try:
                duration = int(float(v.get("duration") or 0))
            except (TypeError, ValueError):
                continue
            if duration < minimum_duration:
                continue

            video_id = v.get("id")
            mp4_download_url = (v.get("urls") or {}).get("mp4_download")
            if not video_id or not mp4_download_url:
                continue

            item = MaterialInfo()
            item.provider = "coverr"
            item.url = mp4_download_url
            item.duration = duration
            video_items.append(item)
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def _video_cache_identity(video_url: str) -> str:
    return str(video_url).split("?", 1)[0]


def save_video(video_url: str, save_dir: str = "") -> str:
    if not save_dir:
        save_dir = utils.storage_dir("cache_videos")

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    url_hash = utils.md5(_video_cache_identity(video_url))
    video_id = f"vid-{url_hash}"
    video_path = f"{save_dir}/{video_id}.mp4"

    # if video already exists, return the path
    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        logger.info(f"video already exists: {video_path}")
        return video_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # if video does not exist, download it
    with open(video_path, "wb") as f:
        f.write(
            requests.get(
                video_url,
                headers=headers,
                proxies=config.proxy,
                verify=_get_tls_verify(),
                timeout=(60, 240),
            ).content
        )

    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        clip = None
        try:
            clip = VideoFileClip(video_path)
            duration = clip.duration
            fps = clip.fps
            if duration > 0 and fps > 0:
                return video_path
        except Exception as e:
            logger.warning(f"invalid video file: {video_path} => {str(e)}")
            try:
                os.remove(video_path)
            except Exception as remove_error:
                logger.warning(
                    f"failed to remove invalid video file: {video_path}, error: {str(remove_error)}"
                )
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception as close_error:
                    logger.warning(
                        f"failed to close video clip: {video_path}, error: {str(close_error)}"
                    )
    return ""


def _search_function(source: str):
    functions = {
        "pexels": search_videos_pexels,
        "pixabay": search_videos_pixabay,
        "coverr": search_videos_coverr,
    }
    if source not in functions:
        raise SceneMaterialError(f"Markdown 场景不支持素材源：{source}")
    return functions[source]


def _scene_material_directory(task_id: str) -> str:
    material_directory = config.app.get("material_directory", "").strip()
    if material_directory == "task":
        return utils.task_dir(task_id)
    if material_directory and not os.path.isdir(material_directory):
        return ""
    return material_directory


def _material_download_workers() -> int:
    try:
        configured = int(config.app.get("material_download_workers", 4))
    except (TypeError, ValueError):
        configured = 4
    return max(1, min(configured, 8))


def _download_scene_candidate_batch(
    scene_index: int,
    candidates,
    save_dir: str,
    workers: int,
) -> dict[int, str]:
    """Download a bounded candidate batch while retaining original indexes."""
    results: dict[int, str] = {}

    def download(index: int, item):
        video_url = str(getattr(item, "url", "") or "").strip()
        logger.info(
            f"downloading video for scene {scene_index}: {video_url}"
        )
        try:
            return index, save_video(video_url, save_dir)
        except Exception as exc:
            logger.error(
                f"failed to download scene video: {utils.to_json(item)} "
                f"=> {str(exc)}"
            )
            return index, ""

    if workers == 1:
        for index, item in enumerate(candidates):
            result_index, saved_path = download(index, item)
            results[result_index] = saved_path
        return results

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="material-download",
    ) as executor:
        futures = [
            executor.submit(download, index, item)
            for index, item in enumerate(candidates)
        ]
        for future in as_completed(futures):
            result_index, saved_path = future.result()
            results[result_index] = saved_path
    return results


def download_scene_materials(
    task_id: str,
    scenes: list[TimedScriptScene],
    source: str,
    video_aspect: VideoAspect,
    max_clip_duration: int,
) -> list[DownloadedSceneMaterials]:
    try:
        max_clip_duration_value = float(max_clip_duration)
    except (TypeError, ValueError) as exc:
        raise SceneMaterialError("场景素材的最大片段时长必须是有限正数") from exc
    if not math.isfinite(max_clip_duration_value) or max_clip_duration_value <= 0:
        raise SceneMaterialError("场景素材的最大片段时长必须是有限正数")

    search_videos = _search_function(source)
    material_directory = _scene_material_directory(task_id)
    download_workers = _material_download_workers()
    plans: list[DownloadedSceneMaterials] = []
    used_video_identities: set[str] = set()

    for scene in scenes:
        try:
            scene_start = float(scene.start)
            scene_end = float(scene.end)
        except (TypeError, ValueError) as exc:
            raise SceneMaterialError(
                f"场景 {scene.index} 时间必须是有限值"
            ) from exc
        if not math.isfinite(scene_start) or not math.isfinite(scene_end):
            raise SceneMaterialError(f"场景 {scene.index} 时间必须是有限值")
        if scene_start < 0 or scene_end < 0:
            raise SceneMaterialError(f"场景 {scene.index} 时间不能为负数")
        if scene_end <= scene_start:
            raise SceneMaterialError(
                f"场景 {scene.index} 结束时间必须晚于开始时间："
                f"{scene_start} - {scene_end}"
            )
        if not scene.search_terms or any(
            not str(term).strip() for term in scene.search_terms
        ):
            raise SceneMaterialError(f"场景 {scene.index} 搜索词不能为空")

        required_duration = scene_end - scene_start
        if not math.isfinite(required_duration) or required_duration <= 0:
            raise SceneMaterialError(
                f"场景 {scene.index} 时长必须是有限正数"
            )
        minimum_duration = max(
            1,
            min(
                math.ceil(max_clip_duration_value),
                math.ceil(required_duration),
            ),
        )
        covered_duration = 0.0
        video_paths: list[str] = []

        for search_term in scene.search_terms:
            candidates = search_videos(
                search_term=search_term,
                minimum_duration=minimum_duration,
                video_aspect=video_aspect,
            )
            logger.info(
                f"found {len(candidates)} videos for scene {scene.index} "
                f"query '{search_term}'"
            )

            batch: list[tuple[object, str]] = []
            queued_identities: set[str] = set()
            for item in candidates:
                video_url = str(getattr(item, "url", "") or "").strip()
                video_identity = _video_cache_identity(video_url)
                if (
                    not video_url
                    or not video_identity
                    or video_identity in used_video_identities
                    or video_identity in queued_identities
                ):
                    continue

                try:
                    duration = float(getattr(item, "duration", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(duration) or duration <= 0:
                    continue
                queued_identities.add(video_identity)
                batch.append((item, video_identity))

            for offset in range(0, len(batch), download_workers):
                current_batch = batch[offset : offset + download_workers]
                batch_results = _download_scene_candidate_batch(
                    scene.index,
                    [item for item, _ in current_batch],
                    material_directory,
                    download_workers,
                )
                for batch_index, (item, video_identity) in enumerate(current_batch):
                    saved_video_path = batch_results.get(batch_index, "")
                    if not saved_video_path:
                        continue
                    try:
                        duration = float(getattr(item, "duration", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    used_video_identities.add(video_identity)
                    video_paths.append(saved_video_path)
                    covered_duration += min(duration, max_clip_duration_value)
                    if covered_duration >= required_duration:
                        break
                if covered_duration >= required_duration:
                    break

            if covered_duration >= required_duration:
                break

        if covered_duration < required_duration:
            attempted_terms = "；".join(scene.search_terms)
            raise SceneMaterialError(
                f"场景 {scene.index} 素材搜索失败：{attempted_terms}；"
                f"对应文案：第 {scene.first_row}～{scene.last_row} 行"
            )

        plans.append(
            DownloadedSceneMaterials(
                scene_index=scene.index,
                first_row=scene.first_row,
                last_row=scene.last_row,
                start=scene_start,
                end=scene_end,
                required_duration=required_duration,
                search_terms=tuple(scene.search_terms),
                video_paths=tuple(video_paths),
            )
        )

    return plans


def download_videos(
    task_id: str,
    search_terms: List[str],
    source: str = "pexels",
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    audio_duration: float = 0.0,
    max_clip_duration: int = 5,
    match_script_order: bool = False,
) -> List[str]:
    search_videos = search_videos_pexels
    if source == "pixabay":
        search_videos = search_videos_pixabay
    elif source == "coverr":
        search_videos = search_videos_coverr

    material_directory = _scene_material_directory(task_id)

    if match_script_order:
        return _download_videos_by_script_order(
            task_id=task_id,
            search_terms=search_terms,
            search_videos=search_videos,
            video_aspect=video_aspect,
            audio_duration=audio_duration,
            max_clip_duration=max_clip_duration,
            material_directory=material_directory,
        )

    valid_video_items = []
    valid_video_urls = []
    found_duration = 0.0
    for search_term in search_terms:
        video_items = search_videos(
            search_term=search_term,
            minimum_duration=max_clip_duration,
            video_aspect=video_aspect,
        )
        logger.info(f"found {len(video_items)} videos for '{search_term}'")

        for item in video_items:
            if item.url not in valid_video_urls:
                valid_video_items.append(item)
                valid_video_urls.append(item.url)
                found_duration += item.duration

    logger.info(
        f"found total videos: {len(valid_video_items)}, required duration: {audio_duration} seconds, found duration: {found_duration} seconds"
    )
    video_paths = []

    concat_mode_value = getattr(video_concat_mode, "value", video_concat_mode)
    if concat_mode_value == VideoConcatMode.random.value:
        random.shuffle(valid_video_items)

    total_duration = 0.0
    for item in valid_video_items:
        try:
            logger.info(f"downloading video: {item.url}")
            saved_video_path = save_video(
                video_url=item.url, save_dir=material_directory
            )
            if saved_video_path:
                logger.info(f"video saved: {saved_video_path}")
                video_paths.append(saved_video_path)
                seconds = min(max_clip_duration, item.duration)
                total_duration += seconds
                if total_duration > audio_duration:
                    logger.info(
                        f"total duration of downloaded videos: {total_duration} seconds, skip downloading more"
                    )
                    break
        except Exception as e:
            logger.error(f"failed to download video: {utils.to_json(item)} => {str(e)}")
    logger.success(f"downloaded {len(video_paths)} videos")
    return video_paths


def _download_videos_by_script_order(
    task_id: str,
    search_terms: List[str],
    search_videos,
    video_aspect: VideoAspect,
    audio_duration: float,
    max_clip_duration: int,
    material_directory: str,
) -> List[str]:
    """
    按脚本文案顺序下载素材。

    默认下载逻辑会把所有关键词的候选素材合并成一个大列表；如果第一个
    关键词返回很多结果，最终下载时可能一直消耗这个关键词的素材，后续
    脚本主题就排不上时间线。这里按关键词分组后轮询下载：
    第 1 轮取每个关键词的第 1 个候选，第 2 轮取每个关键词的第 2 个候选。
    这样在不重写视频合成引擎的前提下，尽量保证素材顺序贴近文案顺序。
    """
    logger.info("downloading videos with script-order material matching")
    candidate_groups = []
    valid_video_urls = set()
    found_duration = 0.0

    for search_term in search_terms:
        video_items = search_videos(
            search_term=search_term,
            minimum_duration=max_clip_duration,
            video_aspect=video_aspect,
        )
        logger.info(f"found {len(video_items)} videos for '{search_term}'")

        term_items = []
        for item in video_items:
            if item.url in valid_video_urls:
                continue
            term_items.append(item)
            valid_video_urls.add(item.url)
            found_duration += item.duration

        if term_items:
            candidate_groups.append((search_term, term_items))

    logger.info(
        f"found total ordered video candidates: {sum(len(items) for _, items in candidate_groups)}, "
        f"required duration: {audio_duration} seconds, found duration: {found_duration} seconds"
    )

    video_paths = []
    total_duration = 0.0
    candidate_index = 0
    while candidate_groups and total_duration <= audio_duration:
        has_candidate = False
        for search_term, term_items in candidate_groups:
            if candidate_index >= len(term_items):
                continue

            has_candidate = True
            item = term_items[candidate_index]
            try:
                logger.info(
                    f"downloading ordered video for '{search_term}': {item.url}"
                )
                saved_video_path = save_video(
                    video_url=item.url, save_dir=material_directory
                )
                if saved_video_path:
                    logger.info(f"video saved: {saved_video_path}")
                    video_paths.append(saved_video_path)
                    total_duration += min(max_clip_duration, item.duration)
                    if total_duration > audio_duration:
                        logger.info(
                            f"total duration of downloaded videos: {total_duration} seconds, skip downloading more"
                        )
                        break
            except Exception as e:
                logger.error(
                    f"failed to download ordered video: {utils.to_json(item)} => {str(e)}"
                )

        if not has_candidate:
            break
        candidate_index += 1

    logger.success(f"downloaded {len(video_paths)} ordered videos")
    return video_paths


if __name__ == "__main__":
    download_videos(
        "test123", ["Money Exchange Medium"], audio_duration=100, source="pixabay"
    )
