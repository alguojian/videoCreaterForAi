# videoCreaterForAi

A Windows-native local voiceover video generator driven by a structured Markdown script.

## Main features

- Upload a final Markdown script directly;
- Read the title, voiceover rows, emphasis terms, and material search terms from the file;
- Generate voiceover locally with CosyVoice3;
- Align subtitles with Qwen3 Forced Aligner;
- Search footage from Pexels, Pixabay, or Coverr;
- Default landscape 16:9 output, with aspect-ratio filtering for supported online sources;
- Bottom single-line white subtitles with black outline;
- Multiple emphasis terms per row, repeated emphasis across rows, random positions/colors/animations, and a fixed sound-effect library;
- No LLM call is required during Markdown-based video generation.

## Workflow

Prepare Markdown -> upload and preview it in WebUI -> generate voiceover and subtitles locally -> search footage by scene -> compose and export the video.

## Windows startup

Install the main environment first:

```powershell
uv sync --frozen
```

Then start the WebUI:

```powershell
.\webui.bat
```

If you do not use `uv`, create a `.venv` and install `requirements.txt` first. See the [Windows setup and configuration guide](docs/windows-setup-guide.md) for the complete process.

## Documentation and example

- [Windows setup and configuration guide](docs/windows-setup-guide.md)
- [Markdown voiceover script format](docs/markdown-script-format.md)
- [Markdown script example](examples/markdown-scripts/marriage.md)
