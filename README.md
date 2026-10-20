# SampleBuddy

Drop in tracks or paste links, and get them back ready to flip:

```
~/Downloads/92 BPM Am - Work - Gang Starr.flac            ← renamed, BPM + key tagged
~/Downloads/92 BPM Am - Work - Gang Starr [Stems]/
    92 BPM Am - Work - Gang Starr (Vocals).flac
    92 BPM Am - Work - Gang Starr (Drums).flac
    92 BPM Am - Work - Gang Starr (Bass).flac
    92 BPM Am - Work - Gang Starr (Other).flac
    92 BPM Am - Work - Gang Starr (Instrumental).flac
```

The stems are 24-bit, the same length, sample-aligned, and share one gain, so they sum back to the original. Drag them straight into FL Studio or any DAW.

There are two parts:
- **The Mac app** (`app/`): paste links or drop files, watch the queue, audition each stem, and drag stems out into your DAW.
- **The engine** (`samplebuddy` CLI, Python): does the analysis and separation. The app runs it, and you can use it directly.

## Install

Requirements:
- macOS 14+ on Apple Silicon
- [uv](https://docs.astral.sh/uv/)
- ffmpeg: `brew install ffmpeg`

```bash
uv tool install git+https://github.com/arielbenavi/samplebuddy
```

To build the app (needs Xcode or the Command Line Tools):

```bash
app/build.sh --install
```

This installs `/Applications/SampleBuddy.app`. It's signed ad hoc; until there's a notarized release, right-click it and choose Open the first time.

## Use

In the app, paste links into the top field, drop files or folders anywhere in the window or on the Dock icon, then drag the stem tiles into your DAW. Settings (⌘,) has the output folder, key notation (Am or 8A), tempo range and stems on/off.

From the terminal:

```bash
samplebuddy ~/Downloads/track.flac             # analyze, rename, stems
samplebuddy ~/Music/to-flip/                   # every audio file in a folder
samplebuddy track.wav --no-stems               # just BPM + key (seconds)
samplebuddy track.wav --key camelot            # "92 BPM 8A - ..."
samplebuddy track.wav --bpm-range 85-180       # house / DnB tempos
samplebuddy <link> --out ~/Downloads           # needs a fetcher, see below
samplebuddy track.wav --json                   # JSON event lines (what the app reads)
```

Re-running is safe: files that already have the prefix and finished `[Stems]` folders are skipped.

The first run downloads about 1 GB of models to `~/Library/Caches/samplebuddy/models` (override with `SAMPLEBUDDY_MODELS`). Stems take about 5 minutes per track on an M-series Mac.

## Links: bring your own downloader

SampleBuddy doesn't include a downloader. Links go to a **fetcher**: any executable you configure that is called as:

```
fetcher <output_dir> <url> [<url> ...]
```

The fetcher downloads the audio into `output_dir` and prints the path of each file it saved, one per line. Any other line it prints is shown as status. To set it up, do one of these:
- set it in the app's Settings
- add `{"fetcher": "/path/to/fetcher"}` to `~/Library/Application Support/samplebuddy/config.json`
- set the `SAMPLEBUDDY_FETCHER` environment variable

Only process audio you have the rights to use.

## How it works

| Step | Tech | Notes |
| --- | --- | --- |
| BPM | Essentia `RhythmExtractor2013` (multifeature) | Folded into `--bpm-range` to fix half/double-time detections |
| Key | Essentia `KeyExtractor` (bgate profile) | Confidence below 0.55 is flagged as worth checking by ear |
| Vocals / Instrumental | BS-Roformer viperx-1297 via [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) (UVR's engine) | Vocals SDR ~11.8, instrumental ~16.5 |
| Drums / Bass / Other | Demucs v4 `htdemucs_ft`, run on the Roformer instrumental | Demucs's leftover vocal output is folded into Other |
| Levels | `mixing.py` | Input fed at −6 dB so no stem gets normalized on its own. One shared gain is fit back to the mix, per-stem rescales are undone, and Demucs click artifacts are repaired |
| App | SwiftUI (`app/`) | Runs `samplebuddy --json` one job at a time and drags stems out as file URLs |

## Development

```bash
uv sync && uv run pytest          # engine tests
cd app && swift build             # app
```

## License

AGPL-3.0, because Essentia is AGPL. See [docs/BRIEF.md](docs/BRIEF.md) for the trade-offs.
