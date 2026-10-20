# SampleBuddy: technical brief

Status as of 2026-10-20. Covers what was built, how each piece held up on real tracks, and how to package it for other producers.

## 1. What exists

```
[optional]   link ──► your own fetcher (not included) ──► audio file
                                                              │
[samplebuddy]                   any local audio file ◄────────┘
                                         │
                       Essentia: BPM + key ──► tags + rename "92 BPM Am - …"
                                         │
             BS-Roformer: Vocals | Instrumental
                                         │
             Demucs htdemucs_ft (on Instrumental): Drums | Bass | Other
                                         │
             mixing.py: shared gain, per-stem rescale undo, click repair
                                         │
                       "<name> [Stems]/<name> (Vocals|Drums|Bass|Other|Instrumental).flac"
```

- **SampleBuddy** (this repo) is the engine (`samplebuddy` CLI) plus a SwiftUI Mac app in `app/`. Links go through a user-configured fetcher, so the repo ships no downloader.
- **Downloader**: none. A fetcher is any program you configure yourself (see the README). See §4 for why.

## 2. Technology choices and how they held up

Tested on 8 hip-hop tracks from 1994–2004, on an M-series Mac (MPS).

### Links: an external fetcher contract
- **Choice:** SampleBuddy calls `fetcher <out_dir> <url>…` and treats every line that's an existing file path as a download. Everything else is shown as status. The tool stays independent of any source.
- **What we learned from testing with a third-party downloader:**
  - Proxy-based sources are unreliable. Outages of several hours and periodic browser verification steps are normal.
  - Some services return 30-second previews instead of the full track. A fetcher should check durations.
  - When a link fails, the fetcher's last status line is passed through as the error, so the app shows the real reason ("server on a break, retry in N min") instead of a generic failure.
- **Verdict:** Keep sources pluggable and out of the main project.

### BPM and key: Essentia (AGPL-3.0)
- **Choice:** `RhythmExtractor2013` (multifeature) for BPM and `KeyExtractor` (bgate profile) for key. They are mature, deterministic and run in seconds per track.
- **What worked:** BPMs were all plausible for the genre (83–99). Key confidence was ≥0.7 on 6 of 8 tracks.
- **What didn't:** Key confidence was low on "Work" (0.44) and "Dear Summer" (0.52). Sample-based tracks with modal or ambiguous harmony are the classic failure case. These are flagged in the output.
- **Watch out:**
  - The BPM fold range defaults to 65–150, which is hip-hop-centric. House and DnB users need `--bpm-range 85-180`. A genre-aware or two-candidate output ("87/174") would be better.
  - **AGPL.** Distributing samplebuddy makes it AGPL. That's fine for open source, but it blocks a closed-source paid app. Alternatives:
    - BPM: librosa (ISC) or madmom (BSD; models CC BY-NC-SA, so not for paid use).
    - Key: libKeyFinder (GPL), or a small permissively licensed CNN. Decide before anyone else contributes.

### Stems: audio-separator 0.47 (MIT), the Ultimate Vocal Remover engine
- **Choice:** Two passes, like an expert UVR workflow.
  1. **BS-Roformer viperx-1297** for Vocals and Instrumental. Vocals SDR 11.8, instrumental 16.5; among the best openly available.
  2. **Demucs v4 htdemucs_ft** on the clean instrumental for Drums, Bass and Other. It's the best 4-stem model in the catalog (drums 10.0, bass 12.0 SDR).
- **Why two passes:** Demucs alone is noticeably worse on vocals. Running Demucs on an instrumental with the vocals already removed means its drum, bass and other stems carry no vocal bleed.
- **Verified:**
  - Vocals + Instrumental rebuild the mix at 75–77 dB SNR, effectively exact.
  - The four stems rebuild the mix at 22–33 dB SNR. This is normal for htdemucs_ft: it's a bag of four per-stem models that aren't forced to sum exactly.
  - Output is 24-bit FLAC, sample-aligned, the same length as the source, and 1.8–4.2 dB below the original (shared clip guard).
- **Problems found and fixed:**
  1. **Per-stem normalization.** audio-separator peak-normalizes each stem on its own by default, which destroys the relative levels. Fixed by feeding the input 6 dB quieter at 24-bit and applying one shared gain per song.
  2. **Demucs click artifact.** On "I Love The Dough", Demucs invented a ~1 ms burst at 0:30 in Drums. That pushed Drums above 1.0, audio-separator rescaled the whole Drums stem, and the set fell 7 dB with a broken sum (6 dB SNR). `mixing.repair_split` fits per-part gains while excluding bursts, then cancels the burst with a 10 ms crossfade. Result: 25 dB, in line with the other tracks. Unit tests cover both cases.
  3. **Python 3.13** needed undeclared or removed modules (`audioread`, `standard-aifc`, `standard-sunau`). They're pinned in `pyproject.toml`.
- **Cost:**
  - About 5.5 minutes per track on MPS. The Roformer pass is ~85% of that, at its default overlap of 8.
  - About 1 GB of models, downloaded once.
  - About 180 MB of disk per track's stems.
- **Model weight licenses:**
  - Demucs weights are MIT.
  - The community Roformer weights (viperx) have no clear license. Download them at runtime (as now) and never bundle them in an app.

## 3. Where it lands vs. what producers already have

FL Studio (21.2+), Ableton Live (12.3+), Logic Pro (11+), Serato and others now include stem separation. **Stems inside the DAW are a commodity.** What SampleBuddy does that they don't:

- **Batch, outside the DAW:** a folder of 50 samples in, organized crates out.
- **Naming and tags:** BPM and key in the filename and the tags, so browsers and samplers sort and filter by them.
- **Quality:** the two-pass Roformer + Demucs chain beats the single-model separators bundled in DAWs, especially on vocals.
- **Level-correct stems:** they sum back to the original, so a flip starts from the real balance.

The pitch is "crate prep": drop tracks in and get flip-ready material back. It's a utility, not a plugin.

## 4. Packaging recommendation

**Recommended order:**

1. **CLI via uv, now.** `uv tool install git+https://github.com/arielbenavi/samplebuddy` gives one command, and uv handles Python and torch. This is also the engine everything else wraps. It's already built, with `--json` progress events for GUIs.
2. **Claude Code plugin, cheap.** A skill that calls `samplebuddy`. It reaches only Claude Code users, but it's almost free and it's how you use it day to day.
3. **Mac app, the real product for producers (v0.1 built, in `app/`).** A small native SwiftUI shell:
   - a drop zone plus a watched "Inbox" folder
   - a queue with per-track progress (from `--json`), and reveal-in-Finder
   - it installs the engine with uv on first launch instead of bundling it. Bundling torch and the models means a 1.5–2.5 GB .app plus the Roformer license problem.
   - It needs an Apple Developer account ($99/yr) for signing and notarization, or users hit Gatekeeper warnings.
4. **DAW plugin later, as a thin companion only.**
   - A Loopcloud/Splice-style VST3/AU (JUCE) could hold the link box, the queue and drag-out tiles. It would send jobs to the same local engine (`--json` today, or a localhost API later).
   - Never load torch or models inside the DAW process: a crash or 2 GB of RAM would take the user's session down with it.
   - FL Studio's Python API covers MIDI controllers and the piano roll only, so FL-native scripting is no route.
   - It's weeks of C++ plus signing for 3 plugin formats. Build it only once the app shows people want this inside the DAW.

**Keep downloaders out of the project.**
- One-click downloading from streaming services through unofficial proxies is the part that gets repositories DMCA'd and apps pulled.
- Such downloaders also break often (see §2).
- SampleBuddy on local files is legally clean, and anyone who has a downloader can plug it in as a fetcher.
- Also add a disclaimer that users must own or have rights to the audio they process. Separating someone else's stems is fine for practice and private flips, but releasing them needs clearance.

## 5. Next steps, by value

1. **Quality presets:**
   - `--fast`: htdemucs only, about 1 min per track.
   - `--best`: today's chain.
   - Possibly an "ultra" ensemble, at maybe 2× the time.
2. **More stems:**
   - Drum sub-stems (kick, snare, hats, toms, cymbals) with `MDX23C-DrumSep`, which audio-separator already ships. For flipping samples, this is the feature producers would love most.
   - `htdemucs_6s` for guitar and piano.
3. **Better BPM and key:**
   - Show both tempo candidates.
   - Pick the range by genre.
   - Compare key against a second detector and flag disagreements.
   - Optionally detect downbeats and write a beat-grid or cue file.
4. **Speed:** Tune Roformer overlap and segment size (overlap 4 is about 2× faster at a small quality cost). Optional cloud GPU.
5. **Mac app polish:** an app icon, a watched Inbox folder, first-launch engine install, and notarization.
6. **Linux/Windows:** CUDA works through audio-separator's `[gpu]` extra. Only CI is needed.
