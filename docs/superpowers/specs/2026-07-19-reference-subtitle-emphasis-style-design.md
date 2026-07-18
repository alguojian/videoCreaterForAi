# Reference Subtitle and Emphasis Style Design

## Goal

Make the local video output match the user's approved reference style without
changing the local audio workflow: one-line bottom subtitles of at most 18
Chinese characters, and colorful emphasis overlays that appear mostly in the
horizontal center with a light pop animation.

## Approved behavior

### Bottom subtitles

- Use the existing `MicrosoftYaHeiBold.ttc` default and the existing thin
  black stroke default.
- Keep every locally generated cue to at most 18 visible characters.
- Keep subtitles to one visual line. Long narration is split into more SRT
  cues; text is never silently clipped.
- Keep the existing bottom subtitle position and no-background presentation.

### Emphasis overlays

- Keep the existing random emphasis color palette.
- Use one animation only: `pop`. It is the existing short scale-in animation
  and does not rotate, slide, or shake.
- Select horizontal positions with deterministic weighted randomness:
  left 15%, center 70%, right 15%.
- Preserve the existing upper/middle emphasis layers so closely timed terms do
  not occupy the same vertical space.
- Use `C:\\Windows\\Fonts\\simkai.ttf` when it is available for the
  emphasis text. If it is unavailable or cannot render the text, retain the
  existing project-local fallback behavior.
- Preserve the existing emphasis outline and shadow-like black edge.

### Local emphasis sound effects

- Do not change the sound manifest, sound file locations, available sound IDs,
  volume control, mix timing, or sample normalization.
- Continue resolving sounds from `resource/sfx/manifest.json` and
  `resource/sfx/`.
- Keeping these paths stable allows the user to update local audio files later
  without a code change.

## Architecture

The change remains inside the existing local subtitle and emphasis components:

1. `config.toml` changes the local subtitle segment limit from 14 to 18.
2. `LocalVoiceSettings` changes the matching fallback default to 18 and
   passes the value to the existing cue builder.
3. `app.services.emphasis` keeps its seeded per-task selector but replaces
   uniform horizontal selection with a weighted selection. The arranging step
   must retain the selected horizontal position instead of overwriting it with
   a uniform left/center/right shuffle.
4. `app.services.video` resolves the local SimKai font only for emphasis;
   regular subtitles retain their configured project font.
5. Existing sound loading and MoviePy audio mixing are deliberately untouched.

## Error handling and compatibility

- A missing or glyph-incompatible SimKai font falls back to the existing
  project-local `MicrosoftYaHeiBold.ttc` path.
- Explicit user font choices still take precedence over the reference-style
  default.
- Existing emphasis manifests remain valid because no cue JSON fields change.
- The seeded selector keeps a task's result reproducible while allowing
  different tasks to receive different positions and colors.

## Verification

- A red/green unit test proves positions are reproducible for a task and
  generated with the 15/70/15 weighting rule.
- A regression test proves arranging cues does not overwrite an already chosen
  weighted horizontal position.
- A settings test proves the local subtitle default is 18 characters and an
  explicit configuration still overrides it.
- A font-resolution test proves SimKai is selected when present and the
  project fallback is used when it is unavailable.
- Existing emphasis sound tests continue to prove that sound IDs map to local
  manifest files.
- Run the focused suites and then the full test suite before generating a
  short visual/audio smoke video.
