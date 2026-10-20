"""The per-job pipeline shared by the CLI and the service: fetch links, analyze + rename + tag, split stems.

Progress is reported through emit(event, **fields) using the same event shapes as `--json`."""
import os
from dataclasses import dataclass

from . import fetch, naming

AUDIO_EXT = {".flac", ".wav", ".aif", ".aiff", ".mp3", ".m4a", ".ogg", ".opus", ".alac"}


def default_model_dir():
    from platformdirs import user_cache_dir
    return os.environ.get("SAMPLEBUDDY_MODELS") or os.path.join(user_cache_dir("samplebuddy"), "models")


@dataclass
class Options:
    out: str = os.path.expanduser("~/Downloads")
    stems: bool = True
    rename: bool = True
    key: str = "standard"
    bpm_range: tuple = (65.0, 150.0)
    force: bool = False


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


def run(inputs, opts: Options, emit, get_splitter, cancelled=lambda: False):
    """Process one job. get_splitter() returns a StemSplitter (the caller owns its lifetime,
    so a service can keep models loaded between jobs). Returns (failed_count, had_any_input)."""
    links = [i for i in inputs if fetch.is_link(i)]
    files = collect([i for i in inputs if not fetch.is_link(i)], emit)
    failed = 0
    if links:
        emit("fetching", urls=links, out=opts.out)
        fetch_log = []

        def on_fetch_log(line):
            fetch_log.append(line)
            emit("log", message=line)
        try:
            got = fetch.fetch(links, opts.out, on_log=on_fetch_log)
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
        return failed, bool(links)

    # Phase 1: analysis (seconds per track)
    from . import analyze
    ready = []
    for f in files:
        if cancelled():
            return failed, True
        d, base = os.path.split(f)
        stem, ext = os.path.splitext(base)
        prior = naming.parse_prefix(stem)
        if prior and not opts.force:
            emit("skip", file=f, reason="already analyzed", bpm=prior[0], key=prior[1])
            ready.append(f)
            continue
        emit("progress", file=f, phase="analyzing")
        try:
            a = analyze.analyze(f, opts.bpm_range, opts.key)
        except Exception as e:  # keep going on one bad file
            failed += 1
            emit("error", file=f, reason=f"analysis failed: {e}")
            continue
        analyze.write_tags(f, a)
        new = f
        if opts.rename:
            new = os.path.join(d, naming.prefixed(prior[2] if prior else stem, a.bpm, a.key) + ext)
            os.replace(f, new)
        emit("analyzed", file=new, source=f, bpm=a.bpm, key=a.key, key_strength=round(a.key_strength, 2))
        ready.append(new)

    if not opts.stems:
        return failed, True

    # Phase 2: stems (~5 min per track on Apple Silicon)
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
        return failed, True

    emit("progress", file=todo[0][0], phase="loading models")
    splitter = get_splitter()
    for i, (f, out, stem) in enumerate(todo, 1):
        if cancelled():
            break
        try:
            written = splitter.split(f, out, stem, on_phase=lambda ph, f=f: emit("progress", file=f, phase=ph))
            emit("stems", file=f, dir=out, files=written, index=i, total=len(todo))
        except Exception as e:
            failed += 1
            emit("error", file=f, reason=f"separation failed: {e}")
    return failed, True
