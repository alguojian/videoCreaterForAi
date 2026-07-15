# SimHei Multi-Term Emphasis Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package SimHei as the dedicated emphasis font and render up to three emphasis terms from one subtitle simultaneously in stable high/middle/low layers with left/center/right positions and independent animations.

**Architecture:** Extend `VideoParams` and `EmphasisCue` without breaking old task manifests. Build grouped timing and slot assignment in `app.services.emphasis`, resolve the project-local font and layer coordinates in `app.services.video`, then expose the font through the existing WebUI emphasis panel. Keep Pillow RGBA rendering as the single text rasterization path.

**Tech Stack:** Python 3.11, Pydantic, Pillow, MoviePy, Streamlit, pytest, Windows PowerShell.

---

## File map

- Create `resource/fonts/SimHei.ttf`: project-local copy of the installed Windows SimHei font.
- Modify `app/models/schema.py`: dedicated emphasis font parameter.
- Modify `app/services/emphasis.py`: grouped cue metadata, center position, overlap timing, and rolling layers.
- Modify `app/services/video.py`: font fallback and three-layer rendering coordinates.
- Modify `webui/Main.py`: emphasis font selection and task restore state.
- Modify `webui/i18n/*.json`: translated label for the emphasis font control.
- Modify `scripts/generate_marriage_pixabay_video.py`: sample with two terms in one subtitle line.
- Modify `test/services/test_schema.py`, `test/services/test_emphasis.py`, `test/services/test_video.py`, and `test/services/test_subtitle_background_settings.py`: regression coverage.

### Task 1: Package SimHei and add a dedicated task parameter

**Files:**
- Create: `resource/fonts/SimHei.ttf`
- Modify: `app/models/schema.py:98-107`
- Modify: `test/services/test_schema.py:15-38`

- [ ] **Step 1: Write the failing schema test**

```python
def test_video_params_uses_project_simhei_for_emphasis(self):
    params = VideoParams(video_subject="婚姻")

    self.assertEqual(params.emphasis_font_name, "SimHei.ttf")
    self.assertNotEqual(params.emphasis_font_name, params.font_name)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_schema.py -k project_simhei
```

Expected: FAIL because `VideoParams` has no `emphasis_font_name` attribute.

- [ ] **Step 3: Add the parameter**

Add beside the other emphasis fields in `VideoParams`:

```python
emphasis_font_name: str = Field(default="SimHei.ttf", max_length=255)
```

- [ ] **Step 4: Copy and validate the installed font**

Run:

```powershell
Copy-Item -LiteralPath C:\Windows\Fonts\simhei.ttf -Destination resource\fonts\SimHei.ttf
& .\tools\miniforge3\envs\mpt\python.exe -c "from PIL import ImageFont; f=ImageFont.truetype(r'resource/fonts/SimHei.ttf', 92); print(f.getbbox('婚姻二字'))"
```

Expected: a four-number glyph bounding box and exit code 0.

- [ ] **Step 5: Run schema tests and commit**

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_schema.py
git add app/models/schema.py test/services/test_schema.py resource/fonts/SimHei.ttf
git commit -m "feat: package dedicated SimHei emphasis font"
```

### Task 2: Group same-subtitle terms into stable rolling layers

**Files:**
- Modify: `app/services/emphasis.py:47-280`
- Modify: `test/services/test_emphasis.py:19-88`

- [ ] **Step 1: Write failing tests for three simultaneous terms and legacy manifests**

```python
def test_same_subtitle_terms_overlap_in_three_distinct_layers():
    subtitles = [
        (7, "00:00:00,000 --> 00:00:04,000", "婚姻不是逃避而是共同成长"),
    ]

    cues = build_emphasis_cues(
        "layered-task",
        subtitles,
        ["婚姻", "逃避", "共同成长"],
    )

    assert [cue.subtitle_index for cue in cues] == [7, 7, 7]
    assert [cue.layer for cue in cues] == [0, 1, 2]
    assert len({cue.position for cue in cues}) == 3
    assert {cue.position for cue in cues} == {"left", "center", "right"}
    assert cues[0].start < cues[1].start < cues[2].start
    assert [cue.end for cue in cues] == [4.0, 4.0, 4.0]


def test_fourth_term_replaces_the_old_high_layer_term():
    subtitles = [
        (2, "00:00:00,000 --> 00:00:08,000", "甲乙丙丁戊己庚辛"),
    ]

    cues = build_emphasis_cues(
        "rolling-task",
        subtitles,
        ["甲乙", "丙丁", "戊己", "庚辛"],
    )

    assert [cue.layer for cue in cues] == [0, 1, 2, 0]
    assert cues[0].end == pytest.approx(cues[3].start)
    assert cues[1].end == pytest.approx(8.0)
    assert cues[2].end == pytest.approx(8.0)
    assert cues[3].end == pytest.approx(8.0)


def test_old_emphasis_payload_defaults_to_the_high_layer():
    cue = EmphasisCue.from_dict(
        {
            "text": "婚姻二字",
            "start": 0.0,
            "end": 1.0,
            "color": "#FF5A36",
            "animation": "pop",
            "sound_id": "pop-01",
            "position": "left",
        }
    )

    assert cue.subtitle_index == -1
    assert cue.layer == 0
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_emphasis.py -k "overlap_in_three or fourth_term or old_emphasis_payload"
```

Expected: FAIL because `center`, `subtitle_index`, and `layer` are unsupported and cue ends are capped at 1.35 seconds.

- [ ] **Step 3: Extend the cue model and stable position set**

Use these definitions:

```python
from dataclasses import dataclass, replace

EMPHASIS_POSITIONS = ("left", "center", "right")

@dataclass(frozen=True)
class EmphasisCue:
    text: str
    start: float
    end: float
    color: str
    animation: str
    sound_id: str
    position: str
    subtitle_index: int = -1
    layer: int = 0
```

Validate `position in EMPHASIS_POSITIONS`, `subtitle_index >= -1`, and `layer in {0, 1, 2}`. Include both fields in `to_dict()` and load with `payload.get("subtitle_index", -1)` and `payload.get("layer", 0)`.

- [ ] **Step 4: Assign grouped timing, layers, and positions**

Make `_cue_for_term()` set `end=cue_end` and `subtitle_index=subtitle_index`. After all matches, call a helper with this behavior:

```python
def _arrange_grouped_cues(task_id: str, cues: list[EmphasisCue]) -> list[EmphasisCue]:
    grouped: dict[int, list[EmphasisCue]] = {}
    for cue in cues:
        grouped.setdefault(cue.subtitle_index, []).append(cue)

    arranged: list[EmphasisCue] = []
    for subtitle_index, group in grouped.items():
        ordered = sorted(group, key=lambda item: (item.start, item.text))
        positions = list(EMPHASIS_POSITIONS)
        random.Random(f"{task_id}:{subtitle_index}:positions").shuffle(positions)
        layer_entries: dict[int, int] = {}
        for order, cue in enumerate(ordered):
            layer = order % 3
            if layer in layer_entries:
                previous_index = layer_entries[layer]
                previous = arranged[previous_index]
                arranged[previous_index] = replace(previous, end=cue.start)
            arranged.append(replace(cue, layer=layer, position=positions[layer]))
            layer_entries[layer] = len(arranged) - 1
    return sorted(arranged, key=lambda item: (item.start, item.layer, item.text))
```

- [ ] **Step 5: Run all emphasis tests and commit**

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_emphasis.py
git add app/services/emphasis.py test/services/test_emphasis.py
git commit -m "feat: layer simultaneous emphasis terms"
```

### Task 3: Resolve SimHei and render high/middle/low slots

**Files:**
- Modify: `app/services/video.py:978-1118,1207-1215,1395-1405`
- Modify: `test/services/test_video.py:45-188`

- [ ] **Step 1: Write failing font fallback and layout tests**

```python
def test_resolve_emphasis_font_prefers_requested_project_font():
    path = vd.resolve_emphasis_font_path("SimHei.ttf", "婚姻二字")
    assert Path(path).name == "SimHei.ttf"


def test_three_emphasis_layers_are_ordered_and_center_is_supported():
    cues = [
        EmphasisCue("第一重点", 0, 2, "#FF5A36", "pop", "pop-01", "left", 1, 0),
        EmphasisCue("第二重点", 0.5, 2, "#FFB000", "zoom", "hit-01", "center", 1, 1),
        EmphasisCue("第三重点", 1, 2, "#A86BFF", "shake", "pop-02", "right", 1, 2),
    ]

    clips = vd.create_emphasis_text_clips(
        cues,
        video_size=(1920, 1080),
        font_path=vd.resolve_emphasis_font_path("SimHei.ttf", "第一重点第二重点第三重点"),
        font_size=92,
    )
    try:
        positions = [clip.pos(0.3) for clip in clips]
        assert positions[0][1] < positions[1][1] < positions[2][1]
        center_x = positions[1][0] + clips[1].w / 2
        assert center_x == pytest.approx(960, abs=60)
        assert positions[2][1] + clips[2].h < 1080 * 0.72
    finally:
        for clip in clips:
            clip.close()
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_video.py -k "resolve_emphasis_font or three_emphasis_layers"
```

Expected: FAIL because the resolver does not exist and every cue currently receives the same vertical position.

- [ ] **Step 3: Add the project font resolver**

```python
def resolve_emphasis_font_path(font_name: str, sample: str) -> str:
    requested = Path(utils.font_dir()) / (font_name or "SimHei.ttf")
    fallback = Path(utils.font_dir()) / "MicrosoftYaHeiBold.ttc"
    selected = requested
    if not requested.is_file() or not subtitle_font_supports_text(str(requested), sample):
        logger.warning(f"emphasis font unavailable, using fallback: {requested}")
        selected = fallback
    return str(selected).replace("\\", "/") if os.name == "nt" else str(selected)
```

- [ ] **Step 4: Map cue positions and layers**

Inside `create_emphasis_text_clips()` use:

```python
center_x_ratio = {"left": 0.24, "center": 0.50, "right": 0.76}[cue.position]
center_y_ratio = (0.28, 0.43, 0.57)[cue.layer]
center_x = video_width * center_x_ratio
base_x = min(max(safe_left, center_x - max_render_width / 2), safe_right - max_render_width)
base_y = center_y_ratio * video_height - max_render_height / 2
base_y = min(max(32, base_y), video_height * 0.68 - max_render_height)
```

In `generate_video()`, keep the subtitle `font_path` unchanged and pass a separately resolved `emphasis_font_path` based on `params.emphasis_font_name` and all cue text.

- [ ] **Step 5: Run video tests and commit**

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_video.py -k emphasis
git add app/services/video.py test/services/test_video.py
git commit -m "feat: render emphasis terms in three slots"
```

### Task 4: Expose and restore the dedicated font in WebUI

**Files:**
- Modify: `webui/Main.py:979-995,3179-3232`
- Modify: `webui/i18n/de.json`
- Modify: `webui/i18n/en.json`
- Modify: `webui/i18n/es.json`
- Modify: `webui/i18n/id.json`
- Modify: `webui/i18n/pt.json`
- Modify: `webui/i18n/ru.json`
- Modify: `webui/i18n/tr.json`
- Modify: `webui/i18n/vi.json`
- Modify: `webui/i18n/zh.json`
- Modify: `test/services/test_subtitle_background_settings.py:16-46`

- [ ] **Step 1: Add a failing locale contract test**

Add `"Emphasis Font"` to the existing `required_keys` set, then run:

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_subtitle_background_settings.py -k all_locales
```

Expected: nine subtest failures because the translation key is absent.

- [ ] **Step 2: Add translations**

Add these values under each file's `Translation` object:

```text
zh: 重点词字体
en: Emphasis Font
de: Hervorhebungsschrift
es: Fuente de énfasis
id: Font Penekanan
pt: Fonte de ênfase
ru: Шрифт акцента
tr: Vurgu Yazı Tipi
vi: Phông chữ nhấn mạnh
```

- [ ] **Step 3: Add the WebUI control and restore state**

Restore old tasks with:

```python
_set_stable_widget_value(
    "emphasis_font_name_select",
    params.get("emphasis_font_name") or "SimHei.ttf",
)
```

In `_render_emphasis_settings()` add this control immediately after the manual terms field:

```python
fonts = get_all_fonts()
if "SimHei.ttf" not in fonts:
    fonts.insert(0, "SimHei.ttf")
st.session_state.setdefault("emphasis_font_name_select", "SimHei.ttf")
params.emphasis_font_name = st.selectbox(
    tr("Emphasis Font"),
    options=fonts,
    key="emphasis_font_name_select",
    disabled=disabled,
)
```

- [ ] **Step 4: Verify WebUI syntax, locales, and tests**

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m compileall -q webui\Main.py
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_subtitle_background_settings.py test/services/test_webui_i18n.py
```

Expected: all tests pass and every locale JSON parses.

- [ ] **Step 5: Commit**

```powershell
git add webui/Main.py webui/i18n/*.json test/services/test_subtitle_background_settings.py
git commit -m "feat: configure emphasis font in webui"
```

### Task 5: Generate and verify the multi-term sample

**Files:**
- Modify: `scripts/generate_marriage_pixabay_video.py:40-70`
- Modify: `task_plan.md`
- Modify: `findings.md`
- Modify: `progress.md`

- [ ] **Step 1: Make the sample exercise overlap**

Set the sample parameters to:

```python
font_name="MicrosoftYaHeiBold.ttc",
emphasis_font_name="SimHei.ttf",
emphasis_terms=(
    "婚姻二字,只有两笔,不是找一个完美的人,一起面对生活,"
    "平凡日子,好好说话,彼此安心"
),
```

This creates two emphasis terms in the first subtitle line and two in the “平凡日子里，也别忘了好好说话” line.

- [ ] **Step 2: Run focused and full verification**

```powershell
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q test/services/test_schema.py test/services/test_emphasis.py test/services/test_video.py test/services/test_subtitle_background_settings.py
& .\tools\miniforge3\envs\mpt\python.exe -m pytest -q --ignore=third_party
git diff --check
```

Expected: zero failures; only the existing dependency deprecation warnings remain.

- [ ] **Step 3: Render without invoking CosyVoice**

```powershell
$env:MPT_REUSE_SAMPLE_AUDIO='1'
$env:MPT_REUSE_DOWNLOADED_MATERIALS='1'
& .\tools\miniforge3\envs\mpt\python.exe scripts\generate_marriage_pixabay_video.py
```

Expected: `storage/tasks/marriage-pixabay-landscape-test/final-1.mp4` is recreated.

- [ ] **Step 4: Verify media and extract overlap frames**

```powershell
& ffprobe -v error -show_entries format=duration,size -show_entries stream=codec_type,codec_name,width,height,avg_frame_rate -of default=noprint_wrappers=1 storage\tasks\marriage-pixabay-landscape-test\final-1.mp4
& ffmpeg -hide_banner -y -ss 0.75 -i storage\tasks\marriage-pixabay-landscape-test\final-1.mp4 -frames:v 1 $env:TEMP\mpt-simhei-overlap-01.png
& ffmpeg -hide_banner -y -ss 16.60 -i storage\tasks\marriage-pixabay-landscape-test\final-1.mp4 -frames:v 1 $env:TEMP\mpt-simhei-overlap-02.png
```

Expected: H.264/AAC, 1920×1080, 30fps; both screenshots show two simultaneous SimHei terms, with the first above the second and no glyph clipping.

- [ ] **Step 5: Record evidence and commit the sample script**

Update the three progress files with the RED/GREEN results, font provenance, sample path, frame times, and full test count. Then run:

```powershell
git add scripts/generate_marriage_pixabay_video.py
git commit -m "test: exercise multi-term emphasis sample"
```
