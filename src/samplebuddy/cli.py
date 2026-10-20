"""samplebuddy CLI: analyze + rename + tag, then separate stems.

    samplebuddy track.flac other.wav some_folder/ [--no-stems] [--key camelot]
    samplebuddy https://example.com/track/...   (needs a configured fetcher, see fetch.py)

Each track is renamed in place to "<BPM> BPM <Key> - <name>" (skipped if already prefixed)
and gets a "<new name> [Stems]/" folder next to it. Re-running is safe: finished work is skipped."""
import argparse
import json
import os
import sys

from . import __version__, fetch, naming

AUDIO_EXT = {".flac", ".wav", ".aif", ".aiff", ".mp3", ".m4a", ".ogg", ".opus", ".alac"}


def default_model_dir():
    from platformdirs import user_cache_dir
    return os.environ.get("SAMPLEBUDDY_MODELS") or os.path.join(user_cache_dir("samplebuddy"), "models")


def collect(paths, emit):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                full = os.path.join(p, name)
                if os.path.isfile(full) and os.path.splitext(name)[1].lower() in AUDIO_EXT:
                    files.append(full)
        elif os.path.isfile(p):
            files.append(p)
        else:
            emit("error", file=p, reason="not found")
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(prog="samplebuddy", description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="+", help="audio files, folders (not recursed) or links")
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads"),
                    help="where downloaded links go (default ~/Downloads); local files are processed in place")
    ap.add_argument("--no-stems", action="store_true", help="only analyze, tag and rename")
    ap.add_argument("--no-rename", action="store_true", help="tag only; keep original filenames")
    ap.add_argument("--key", choices=["standard", "camelot"], default="standard", help="key notation (Am vs 8A)")
    ap.add_argument("--bpm-range", default="65-150",
                    help="fold half/double-time detections into this range (default 65-150; try 85-180 for house/DnB)")
    ap.add_argument("--force", action="store_true", help="re-analyze already-prefixed files")
    ap.add_argument("--models", default=default_model_dir(), help="model cache dir (~1 GB, downloaded on first run)")
    ap.add_argument("--json", action="store_true", help="emit one JSON event per line (for GUIs/scripts)")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    lo, hi = (float(x) for x in args.bpm_range.split("-"))

    def emit(event, **kw):
        if args.json:
            print(json.dumps({"event": event, **kw}), flush=True)
        elif event == "analyzed":
            flag = "  (key low-confidence, check by ear)" if kw["key_strength"] < 0.55 else ""
            print(f"{kw['bpm']:>3} BPM {kw['key']:<4} {os.path.basename(kw['file'])}{flag}", flush=True)
        elif event == "stems":
            print(f"stems [{kw['index']}/{kw['total']}] {kw['dir']}", flush=True)
        elif event in ("fetched", "log"):
            print(kw.get("file") or kw.get("message"), flush=True)
        elif event in ("skip", "error"):
            print(f"{event}: {kw.get('reason', '')} {kw.get('file', '')}", file=sys.stderr, flush=True)

    links = [i for i in args.inputs if fetch.is_link(i)]
    files = collect([i for i in args.inputs if not fetch.is_link(i)], emit)
    failed = 0
    if links:
        emit("fetching", urls=links, out=args.out)
        fetch_log = []

        def on_fetch_log(line):
            fetch_log.append(line)
            emit("log", message=line)
        try:
            got = fetch.fetch(links, args.out, on_log=on_fetch_log)
        except (RuntimeError, OSError) as e:
            got = []
            fetch_log.append(str(e))
        for f in got:
            emit("fetched", file=f)
        if len(got) < len(links):
            failed += 1
            why = fetch_log[-1] if fetch_log else "the downloader printed nothing"
            emit("error", file=" ".join(links),
                 reason=f"downloaded {len(got)} of {len(links)} link(s): {why}")
        files += got
    if not files:
        if not links:
            ap.error("no audio files found")
        return 1

    # Phase 1: analysis (seconds per track)
    from . import analyze
    ready = []
    for f in files:
        d, base = os.path.split(f)
        stem, ext = os.path.splitext(base)
        prior = naming.parse_prefix(stem)
        if prior and not args.force:
            emit("skip", file=f, reason="already analyzed", bpm=prior[0], key=prior[1])
            ready.append(f)
            continue
        emit("progress", file=f, phase="analyzing")
        try:
            a = analyze.analyze(f, (lo, hi), args.key)
        except Exception as e:  # keep going on one bad file
            failed += 1
            emit("error", file=f, reason=f"analysis failed: {e}")
            continue
        analyze.write_tags(f, a)
        new = f
        if not args.no_rename:
            new = os.path.join(d, naming.prefixed(prior[2] if prior else stem, a.bpm, a.key) + ext)
            os.replace(f, new)
        emit("analyzed", file=new, source=f, bpm=a.bpm, key=a.key, key_strength=round(a.key_strength, 2))
        ready.append(new)

    if args.no_stems:
        return 1 if failed else 0

    # Phase 2: stems (~5 min per track on Apple Silicon); models load once
    todo = []
    for f in ready:
        d, base = os.path.split(f)
        stem = os.path.splitext(base)[0]
        out = os.path.join(d, naming.stems_dir_name(stem))
        files_out = [os.path.join(out, naming.stem_file_name(stem, p)) for p in naming.STEM_NAMES]
        if all(os.path.exists(p) for p in files_out):
            emit("skip", file=f, reason="stems exist", dir=out, files=files_out)
        else:
            todo.append((f, out, stem))
    if not todo:
        return 1 if failed else 0

    from .separate import StemSplitter
    if not args.json:
        print(f"separating {len(todo)} track(s), ~5 min each; models download to {args.models} on first run",
              flush=True)
    emit("progress", file=todo[0][0], phase="loading models")
    splitter = StemSplitter(args.models)
    try:
        for i, (f, out, stem) in enumerate(todo, 1):
            try:
                files_written = splitter.split(f, out, stem,
                                               on_phase=lambda ph, f=f: emit("progress", file=f, phase=ph))
                emit("stems", file=f, dir=out, files=files_written, index=i, total=len(todo))
            except Exception as e:
                failed += 1
                emit("error", file=f, reason=f"separation failed: {e}")
    finally:
        splitter.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
