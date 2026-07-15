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

Run the following command from the project directory:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m streamlit run webui\Main.py
```

`webui.bat` is optional and is intended for environments where you have already configured a compatible Python or `uv` runtime.

## Documentation and example

- [Markdown voiceover script format](docs/markdown-script-format.md)
- [Markdown script example](examples/markdown-scripts/marriage.md)
