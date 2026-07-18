# Reference Subtitle and Emphasis Style Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make new local videos use one-line 18-character subtitles and reference-style emphasis text with random colors, a light pop animation, a 15/70/15 left/center/right distribution, SimKai typography, and unchanged local sound effects.

**Architecture:** Keep the existing subtitle builder, emphasis manifest schema, and local WAV sound mixer. Change the local subtitle segment default to 18, make horizontal placement weighted and retained through cue arrangement, set new task defaults to SimKai plus non-random animation, and extend emphasis font resolution to use the installed Windows SimKai file with the current project fallback.

**Tech Stack:** Python, Pydantic, pytest, Pillow, MoviePy, FFmpeg, Streamlit.

---

### Task 1: Make emphasis placement weighted and retain it during cue arrangement

**Files:**
- Modify: app/services/emphasis.py
- Modify: test/services/test_emphasis.py

- [ ] **Step 1: Write the failing weighting test**

Add this executable test near the seeded-style tests:

~~~python
from unittest.mock import Mock

def test_style_requests_center_weighted_position(monkeypatch):
    selector = Mock()
    selector.choice.side_effect = ["pop", "pop-01", "center"]
    selector.choices.return_value = ["center"]
    monkeypatch.setattr(emphasis.random, "Random", lambda *_: selector)

    color, animation, sound_id, position = emphasis._style_for(
        "weighted-task",
        1,
        0,
        random_colors=False,
        random_animations=False,
    )

    assert (color, animation, sound_id, position) == (
        "#FF5A36",
        "pop",
        "pop-01",
        "center",
    )
    selector.choices.assert_called_once_with(
        emphasis.EMPHASIS_POSITIONS,
        weights=emphasis.EMPHASIS_POSITION_WEIGHTS,
        k=1,
    )
~~~

- [ ] **Step 2: Write the failing arrangement regression test**

Add this test after the existing legacy grouping test:

~~~python
def test_arrangement_keeps_the_weighted_horizontal_position():
    cues = [
        EmphasisCue("第一词", 0.0, 3.0, "#FF5A36", "pop", "pop-01", "center", subtitle_index=1),
        EmphasisCue("第二词", 1.0, 3.0, "#FFB000", "pop", "pop-02", "center", subtitle_index=1),
        EmphasisCue("第三词", 2.0, 3.0, "#A86BFF", "pop", "pop-03", "center", subtitle_index=1),
    ]

    arranged = emphasis._arrange_grouped_cues("keep-position", cues)

    assert [cue.layer for cue in arranged] == [0, 1, 2]
    assert [cue.position for cue in arranged] == ["center", "center", "center"]
~~~

- [ ] **Step 3: Run the new tests and verify red**

Run:

~~~powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_emphasis.py -k "weighted_position or keeps_the_weighted" -q
~~~

Expected: the weighting test fails because _style_for calls choice instead of choices, and the arrangement test fails because _arrange_grouped_cues overwrites the input positions.

- [ ] **Step 4: Implement the minimal weighted selector**

In app/services/emphasis.py:

~~~python
EMPHASIS_POSITION_WEIGHTS = (15, 70, 15)
~~~

Replace the final position choice in _style_for with:

~~~python
position = selector.choices(
    EMPHASIS_POSITIONS,
    weights=EMPHASIS_POSITION_WEIGHTS,
    k=1,
)[0]
~~~

In _arrange_grouped_cues, retain each cue's existing position when replacing its layer; remove the shuffled positions list and the position=positions[layer] override. Keep the existing layer order and prior-cue end truncation unchanged.

- [ ] **Step 5: Run the focused emphasis suite**

Run:

~~~powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_emphasis.py -q
~~~

Expected: all emphasis tests pass, including the existing local WAV manifest test.

### Task 2: Default new tasks to light pop animation and SimKai emphasis text

**Files:**
- Modify: app/models/schema.py
- Modify: webui/Main.py
- Modify: app/services/video.py
- Modify: test/services/test_schema.py
- Modify: test/services/test_video.py
- Modify: test/services/test_webui_markdown_import.py

- [ ] **Step 1: Write failing model and WebUI default tests**

Change the schema default test to require:

~~~python
assert params.emphasis_font_name == "SimKai.ttf"
assert params.emphasis_random_animations is False
~~~

Add a WebUI source test that requires:

~~~python
assert 'params.get("emphasis_font_name") or "SimKai.ttf"' in restoration
assert 'default_value="SimKai.ttf"' in rendering
assert 'setdefault("emphasis_random_animations_checkbox", False)' in rendering
~~~

- [ ] **Step 2: Write the failing system-font resolver test**

Add this test next to the existing emphasis font tests:

~~~python
def test_resolve_emphasis_font_uses_system_simkai_when_available(tmp_path):
    simkai = tmp_path / "simkai.ttf"
    simkai.write_bytes((Path(utils.font_dir()) / "SimHei.ttf").read_bytes())

    with patch.object(vd, "_system_emphasis_font_path", return_value=simkai):
        path = vd.resolve_emphasis_font_path("SimKai.ttf", "重点词")

    assert Path(path) == simkai
~~~

- [ ] **Step 3: Run the new default and resolver tests and verify red**

Run:

~~~powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_schema.py test/services/test_video.py test/services/test_webui_markdown_import.py -k "emphasis_font or random_animations or simkai" -q
~~~

Expected: failures show SimHei and random animations are still defaults, and _system_emphasis_font_path does not exist.

- [ ] **Step 4: Implement the defaults and portable fallback**

Set VideoParams.emphasis_font_name to SimKai.ttf and emphasis_random_animations to False. Update old-task restore and the WebUI select box defaults to the same values. Ensure SimKai.ttf is offered before project fonts in the emphasis font select box.

Add this helper in app/services/video.py:

~~~python
def _system_emphasis_font_path(font_name: str) -> Path | None:
    if os.name != "nt" or str(font_name).lower() != "simkai.ttf":
        return None
    candidate = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "simkai.ttf"
    return candidate if candidate.is_file() else None
~~~

Update resolve_emphasis_font_path to prefer an existing requested project font, then _system_emphasis_font_path, then the existing MicrosoftYaHeiBold.ttc fallback. Keep the glyph-support check for every selected candidate.

- [ ] **Step 5: Run the focused defaults and renderer tests**

Run:

~~~powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_schema.py test/services/test_video.py test/services/test_webui_markdown_import.py -k "emphasis or simkai" -q
~~~

Expected: all selected tests pass and no test changes the local SFX manifest or audio mixer.

### Task 3: Raise local subtitle segmentation to 18 characters while preserving one-line cues

**Files:**
- Modify: config.toml
- Modify: config.example.toml
- Modify: app/services/local_voice/service.py
- Modify: test/services/test_local_voice_service.py
- Modify: test/services/test_local_voice_subtitle_builder.py

- [ ] **Step 1: Write the failing settings and cue tests**

Replace the default assertion in the existing settings test and add an explicit override:

~~~python
assert LocalVoiceSettings.from_mapping({}, project_root=tmp_path).subtitle_max_chars == 18
explicit = LocalVoiceSettings.from_mapping(
    {"subtitle_max_chars": 12}, project_root=tmp_path
)
assert explicit.subtitle_max_chars == 12
~~~

Add this cue-builder test:

~~~python
def test_build_cues_keeps_an_eighteen_character_chinese_cue_on_one_line():
    text = "一二三四五六七八九十一二三四五六七八"
    cues = build_cues(
        _alignment(text, step=0.1),
        display_text=text,
        max_chars=18,
        min_duration=0.1,
        max_duration=4.2,
        audio_duration=3.0,
    )

    assert [cue.text for cue in cues] == [text]
    assert "\n" not in cues[0].text
    assert len(cues[0].text) == 18
~~~

- [ ] **Step 2: Run the subtitle tests and verify red**

Run:

~~~powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_local_voice_service.py test/services/test_local_voice_subtitle_builder.py -k "subtitle_max_chars or eighteen_character" -q
~~~

Expected: the settings assertion fails because the application fallback remains 14.

- [ ] **Step 3: Implement the 18-character default**

Set subtitle_max_chars to 18 in config.toml and config.example.toml. Set both LocalVoiceSettings.subtitle_max_chars and the from_mapping fallback value to 18. Do not alter subtitle_max_lines, cue timing, punctuation removal, or renderer wrapping logic.

- [ ] **Step 4: Run the focused local subtitle suites**

Run:

~~~powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_local_voice_service.py test/services/test_local_voice_subtitle_builder.py -q
~~~

Expected: all local voice service and subtitle builder tests pass.

### Task 4: Verify the complete change and generate a local reference-style smoke video

**Files:**
- Test only: test/services/
- Runtime input: storage/tasks/ai-agent-hot-sync-20260718/

- [ ] **Step 1: Run the complete test suite**

Run:

~~~powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q --ignore=third_party
~~~

Expected: zero failures; existing deprecation warnings may remain.

- [ ] **Step 2: Render a local smoke video from existing artifacts**

Use storage/tasks/ai-agent-hot-sync-20260718/combined-loop.mp4,
audio.wav, subtitle.srt, and emphasis.json. Instantiate VideoParams with
emphasis_enabled=True, emphasis_random_colors=True,
emphasis_random_animations=False, emphasis_font_name="SimKai.ttf", and
font_name="MicrosoftYaHeiBold.ttc". Write the result as
storage/tasks/ai-agent-hot-sync-20260718/final-reference-style.mp4.

- [ ] **Step 3: Validate streams and visual rules**

Run ffprobe against final-reference-style.mp4 and extract screenshots during
two emphasis cues. Confirm a non-empty video stream, an AAC audio stream, one
bottom subtitle line of at most 18 characters, a horizontal pop emphasis
overlay, and one local SFX mix event per emphasis cue.

- [ ] **Step 4: Review and commit only the approved feature hunks**

Run:

~~~powershell
git diff --check
git status --short
git add -p app/services/emphasis.py app/services/video.py app/services/local_voice/service.py app/models/schema.py webui/Main.py config.toml config.example.toml test/services/test_emphasis.py test/services/test_video.py test/services/test_schema.py test/services/test_local_voice_service.py test/services/test_local_voice_subtitle_builder.py test/services/test_webui_markdown_import.py
git commit -m "feat: apply reference subtitle style"
~~~

Stage only the new feature hunks because app/services/video.py and several
tests already contain unrelated uncommitted work. Do not stage user Markdown
scripts or generated storage artifacts.
