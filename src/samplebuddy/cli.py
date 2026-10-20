"""samplebuddy CLI: analyze + rename + tag, then separate stems.

    samplebuddy track.flac other.wav some_folder/ [--no-stems] [--key camelot]
    samplebuddy https://example.com/track/...   (needs a configured fetcher, see fetch.py)

Each track is renamed in place to "<BPM> BPM <Key> - <name>" (skipped if already prefixed)
and gets a "<new name> [Stems]/" folder next to it. Re-running is safe: finished work is skipped."""
import argparse
import json
import os
import sys

from . import __version__, pipeline
from .pipeline import collect, default_model_dir  # noqa: F401  (kept for importers)


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

    opts = pipeline.Options(out=args.out, stems=not args.no_stems, rename=not args.no_rename,
                            key=args.key, bpm_range=(lo, hi), force=args.force)
    splitter = None

    def get_splitter():
        nonlocal splitter
        if splitter is None:
            from .separate import StemSplitter
            if not args.json:
                print(f"separating stems, ~5 min per track; models download to {args.models} on first run",
                      flush=True)
            splitter = StemSplitter(args.models)
        return splitter
    try:
        failed, had_input = pipeline.run(args.inputs, opts, emit, get_splitter)
    finally:
        if splitter is not None:
            splitter.close()
    if not had_input:
        ap.error("no audio files found")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
