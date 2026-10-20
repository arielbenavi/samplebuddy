"""Link input through a user-configured external downloader ("fetcher").

SampleBuddy ships no downloader. A fetcher is any executable called as

    <fetcher> <output_dir> <url> [<url> ...]

that downloads audio into output_dir and prints the path of every file it produced on its own
line. Any other output line is passed through as a log message. It is configured by
$SAMPLEBUDDY_FETCHER or by "fetcher" in config.json (see config_path())."""
import json
import os
import subprocess


def config_path():
    from platformdirs import user_config_dir
    return os.path.join(user_config_dir("samplebuddy"), "config.json")


def load_config():
    try:
        with open(config_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fetcher_command():
    return os.environ.get("SAMPLEBUDDY_FETCHER") or load_config().get("fetcher")


def is_link(arg: str) -> bool:
    return arg.startswith(("http://", "https://", "spotify:"))


def fetch(urls, out_dir, on_log=lambda line: None):
    """Run the fetcher; return the downloaded file paths. Raises RuntimeError if none is configured."""
    cmd = fetcher_command()
    if not cmd:
        raise RuntimeError(
            f"no downloader configured for links; set SAMPLEBUDDY_FETCHER or 'fetcher' in {config_path()}")
    os.makedirs(out_dir, exist_ok=True)
    proc = subprocess.Popen([os.path.expanduser(cmd), out_dir, *urls], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    files = []
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line and os.path.isfile(line):
            files.append(line)
        elif line.strip():
            on_log(line)
    proc.wait()
    return files
