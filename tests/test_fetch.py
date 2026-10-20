import json
import os
import stat

from samplebuddy import cli, fetch


def _script(tmp_path, body):
    p = tmp_path / "fetcher.sh"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_fetch_collects_paths_and_logs(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("SAMPLEBUDDY_FETCHER", _script(tmp_path, (
        'echo "status: working on $2"\n'
        'touch "$1/Song - Artist.flac"\n'
        'echo "$1/Song - Artist.flac"\n')))
    logs = []
    got = fetch.fetch(["https://example.com/track/x"], str(out), on_log=logs.append)
    assert got == [str(out / "Song - Artist.flac")]
    assert logs == ["status: working on https://example.com/track/x"]


def test_failed_link_reports_downloader_reason(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SAMPLEBUDDY_FETCHER", _script(tmp_path, 'echo "server taking a break, try in 10 min"\n'))
    code = cli.main(["--json", "--out", str(tmp_path), "https://example.com/track/x"])
    assert code == 1
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    errors = [e for e in events if e["event"] == "error"]
    assert errors and "server taking a break" in errors[-1]["reason"]


def test_no_fetcher_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SAMPLEBUDDY_FETCHER", raising=False)
    monkeypatch.setattr(fetch, "config_path", lambda: os.path.join(tmp_path, "missing.json"))
    assert cli.main(["--json", "--out", str(tmp_path), "https://example.com/x"]) == 1
    assert "no downloader configured" in capsys.readouterr().out


def test_is_link():
    assert fetch.is_link("https://example.com/track/abc")
    assert fetch.is_link("spotify:track:abc")
    assert not fetch.is_link("/Users/me/song.flac")
