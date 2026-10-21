"""Job queue and HTTP API, with the analyzer and stem splitter mocked (no ML models)."""
import http.client
import json
import os
import threading

import pytest

from samplebuddy import analyze, naming, server
from samplebuddy.analyze import Analysis


class FakeSplitter:
    def __init__(self, gate=None):
        self.gate = gate  # threading.Event: split() blocks on it after the first phase
        self.calls = 0
        self.closed = False

    def split(self, path, out_dir, name, on_phase=lambda phase: None):
        self.calls += 1
        on_phase("separating vocals")
        if self.gate:
            self.gate.wait(5)
        on_phase("writing stems")
        os.makedirs(out_dir, exist_ok=True)
        written = []
        for part in naming.STEM_NAMES:
            dest = os.path.join(out_dir, naming.stem_file_name(name, part))
            open(dest, "w").close()
            written.append(dest)
        return written

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_analysis(monkeypatch):
    monkeypatch.setattr(analyze, "analyze", lambda path, rng, notation: Analysis(92, "Am", 0.8))
    monkeypatch.setattr(analyze, "write_tags", lambda path, a: True)


@pytest.fixture
def track(tmp_path):
    d = tmp_path / "music"
    d.mkdir()
    p = d / "Work.flac"
    p.write_bytes(b"not really audio")
    return str(p)


def make_engine(tmp_path, splitter=None):
    made = []

    def factory():
        made.append(splitter or FakeSplitter())
        return made[-1]
    eng = server.Engine(str(tmp_path / "state" / "queue.json"), factory)
    eng.made = made
    return eng


def states(job):
    return [e["state"] for e in job["events"] if e["event"] == "state"]


def test_job_runs_to_done_with_cli_event_shapes(tmp_path, track):
    eng = make_engine(tmp_path)
    eng.start()
    try:
        job = eng.submit(track)
        assert eng.wait_idle()
        job = eng.get(job["id"])
    finally:
        eng.stop()
    assert job["state"] == "done"
    assert states(job) == ["queued", "running", "done"]
    by_kind = {e["event"]: e for e in job["events"]}
    renamed = os.path.join(os.path.dirname(track), "92 BPM Am - Work.flac")
    assert by_kind["analyzed"]["file"] == renamed and by_kind["analyzed"]["bpm"] == 92
    assert by_kind["stems"]["files"][0].endswith("92 BPM Am - Work (Vocals).flac")
    assert all(os.path.exists(f) for f in by_kind["stems"]["files"])
    assert [e["seq"] for e in job["events"]] == sorted(e["seq"] for e in job["events"])


def test_models_load_once_across_jobs(tmp_path, track):
    other = os.path.join(os.path.dirname(track), "Other Song.wav")
    open(other, "wb").close()
    eng = make_engine(tmp_path)
    eng.start()
    try:
        eng.submit(track)
        eng.submit(other)
        assert eng.wait_idle()
    finally:
        eng.stop()
    assert len(eng.made) == 1 and eng.made[0].calls == 2 and eng.made[0].closed


def test_queue_survives_restart_and_requeues_running(tmp_path, track):
    eng = make_engine(tmp_path)  # worker not started: jobs stay queued
    a = eng.submit(track)
    b = eng.submit(track, {"key": "camelot", "stems": False})
    with eng.lock:
        eng._set_state(eng.jobs[a["id"]], "running")  # as if killed mid-job

    again = make_engine(tmp_path)
    jobs = {j["id"]: j for j in again.list()}
    assert list(jobs) == [a["id"], b["id"]]
    assert jobs[a["id"]]["state"] == "queued" and jobs[a["id"]]["reason"] == "engine restarted"
    assert jobs[b["id"]]["options"]["key"] == "camelot" and jobs[b["id"]]["options"]["stems"] is False
    assert again.seq == max(e["seq"] for j in jobs.values() for e in j["events"])


def test_corrupt_queue_file_is_set_aside(tmp_path):
    path = tmp_path / "state" / "queue.json"
    path.parent.mkdir()
    path.write_text("{nope")
    eng = server.Engine(str(path), FakeSplitter)
    assert eng.list() == [] and (tmp_path / "state" / "queue.json.bad").exists()


def test_cancel_queued_job_is_never_run(tmp_path, track):
    eng = make_engine(tmp_path)
    job = eng.submit(track)
    eng.cancel(job["id"])
    eng.start()
    try:
        assert eng.wait_idle()
    finally:
        eng.stop()
    assert eng.get(job["id"])["state"] == "cancelled" and not eng.made
    assert os.path.exists(track)  # untouched


def test_cancel_running_job_stops_at_next_phase(tmp_path, track):
    gate = threading.Event()
    eng = make_engine(tmp_path, FakeSplitter(gate))
    eng.start()
    try:
        job = eng.submit(track)
        with eng.lock:
            eng.lock.wait_for(lambda: any(e.get("phase") == "separating vocals"
                                          for e in eng.jobs[job["id"]]["events"]), timeout=5)
        eng.cancel(job["id"])
        gate.set()
        assert eng.wait_idle()
        job = eng.get(job["id"])
    finally:
        eng.stop()
    assert job["state"] == "cancelled"
    assert not any(e["event"] == "stems" for e in job["events"])
    assert not any(e.get("phase") == "writing stems" for e in job["events"])


def test_missing_input_and_bad_options_are_rejected(tmp_path, track):
    eng = make_engine(tmp_path)
    for bad in ("relative/path.flac", str(tmp_path / "gone.flac"), ""):
        with pytest.raises(ValueError):
            eng.submit(bad)
    for opts in ({"key": "lydian"}, {"bpm_range": "150-65"}, {"stems": "yes"}, {"colour": 1}, {"out": "rel"}):
        with pytest.raises(ValueError):
            eng.submit(track, opts)
    assert eng.list() == []


def test_failed_link_without_fetcher_reports_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("SAMPLEBUDDY_FETCHER", raising=False)
    monkeypatch.setattr(server.fetch, "load_config", lambda: {})
    eng = make_engine(tmp_path)
    eng.start()
    try:
        job = eng.submit("https://example.com/track/1", {"out": str(tmp_path)})
        assert eng.wait_idle()
        job = eng.get(job["id"])
    finally:
        eng.stop()
    assert job["state"] == "failed" and "no downloader configured" in job["reason"]


def test_capabilities_describe_stems_and_options():
    caps = server.capabilities()
    assert caps["stems"] == list(naming.STEM_NAMES)
    names = {o["name"] for o in caps["options"]}
    assert names == {"stems", "rename", "key", "bpm_range", "out", "force"}
    server.parse_options({o["name"]: o["default"] for o in caps["options"]})  # defaults round-trip


# --- HTTP ---------------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path):
    eng = make_engine(tmp_path)
    httpd = server.make_server(eng, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    eng.start()
    port = httpd.server_address[1]

    def call(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Content-Type": "application/json"} if body is not None else {}
        c.request(method, path, json.dumps(body) if body is not None else None, {**h, **(headers or {})})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"null")
    call.port, call.engine = port, eng
    yield call
    httpd.closing = True
    httpd.shutdown()
    eng.stop()
    httpd.server_close()


def read_events(port, since, until_state, job_id):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", f"/events?since={since}")
    r = c.getresponse()
    assert r.getheader("Content-Type") == "text/event-stream"
    got = []
    for raw in r:
        line = raw.decode().strip()
        if line.startswith("data: "):
            got.append(json.loads(line[6:]))
            if got[-1].get("job") == job_id and got[-1].get("state") == until_state:
                break
    c.close()
    return got


def test_http_submit_list_and_stream(api, track):
    status, health = api("GET", "/health")
    assert status == 200 and health["ok"] and health["port"] == api.port
    status, caps = api("GET", "/capabilities")
    assert status == 200 and "Vocals" in caps["stems"]

    status, job = api("POST", "/jobs", {"input": track, "options": {"bpm_range": "85-180"}})
    assert status == 201 and job["options"]["bpm_range"] == [85.0, 180.0]
    events = read_events(api.port, 0, "done", job["id"])
    kinds = [e["event"] for e in events if e["job"] == job["id"]]
    assert kinds[0] == "state" and "analyzed" in kinds and "stems" in kinds
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)

    status, listing = api("GET", "/jobs")
    assert status == 200 and [j["id"] for j in listing["jobs"]] == [job["id"]]
    assert listing["jobs"][0]["state"] == "done" and listing["seq"] == events[-1]["seq"]

    # replay from the middle only returns what came after
    mid = events[len(events) // 2]["seq"]
    later = read_events(api.port, mid, "done", job["id"])
    assert [e["seq"] for e in later] == [e["seq"] for e in events if e["seq"] > mid]

    assert api("DELETE", f"/jobs/{job['id']}") == (200, {"ok": True})
    assert api("GET", f"/jobs/{job['id']}")[0] == 404


def test_http_cancel_and_errors(api, track):
    api.engine.stop()  # keep the job queued
    status, job = api("POST", "/jobs", {"input": track})
    status, cancelled = api("POST", f"/jobs/{job['id']}/cancel")
    assert status == 200 and cancelled["state"] == "cancelled"
    assert api("POST", "/jobs/nope/cancel")[0] == 404
    assert api("POST", "/jobs", {"input": "/does/not/exist.flac"})[0] == 400
    assert api("POST", "/jobs", {"input": track, "options": {"key": "x"}})[0] == 400
    assert api("GET", "/nothing")[0] == 404


def test_http_refuses_browser_requests(api, track):
    assert api("GET", "/jobs", headers={"Origin": "https://evil.example"})[0] == 403
    assert api("GET", "/jobs", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert api("GET", "/jobs", headers={"Host": "evil.example:7429"})[0] == 403
    c = http.client.HTTPConnection("127.0.0.1", api.port, timeout=5)
    c.request("POST", "/jobs", json.dumps({"input": track}), {"Content-Type": "text/plain"})
    assert c.getresponse().status == 415
    assert api.engine.list() == []
