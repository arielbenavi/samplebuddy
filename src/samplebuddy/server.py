"""samplebuddy serve: one local engine shared by every client (the Mac app now, a DAW plugin later).

A localhost-only HTTP API on 127.0.0.1 (default port 7429; the port in use is written to
server.json in the app-support dir). It owns one job queue, runs one job at a time, and persists
the queue to queue.json so a restart resumes queued work. The stem models stay loaded between jobs.

    GET    /health                 {"ok", "version", "pid", "port"}
    GET    /capabilities           the stems, options and inputs this engine supports
    GET    /jobs                   every job, oldest first, each with its events
    POST   /jobs                   {"input": file, folder or link, "options": {...}} -> the job
    GET    /jobs/<id>              one job
    POST   /jobs/<id>/cancel       stop it (a running job stops at its next phase)
    DELETE /jobs/<id>              cancel if needed and forget it
    GET    /events?since=<seq>     Server-Sent Events: every event after seq, then live

Events are the `samplebuddy --json` events with "job" and "seq" added, plus "state"
(queued, running, done, failed, cancelled, with an optional "reason") and "removed"."""
import argparse
import dataclasses
import json
import os
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, fetch, naming, pipeline

DEFAULT_PORT = 7429
KEEP_JOB_EVENTS = 300   # per job; the oldest "log" lines go first
KEEP_EVENTS = 5000      # for /events replay; older clients get a "resync"
ACTIVE = ("queued", "running")


def state_dir():
    """~/Library/Application Support/samplebuddy on macOS (next to config.json)."""
    return os.path.dirname(fetch.config_path())


def port_file(directory=None):
    return os.path.join(directory or state_dir(), "server.json")


def capabilities():
    d = pipeline.Options()
    return {
        "version": __version__,
        "stems": list(naming.STEM_NAMES),
        "inputs": {"extensions": sorted(e.lstrip(".") for e in pipeline.AUDIO_EXT), "folders": True,
                   "links": bool(fetch.fetcher_command())},
        "options": [
            {"name": "stems", "type": "bool", "default": d.stems, "label": "Make stems"},
            {"name": "rename", "type": "bool", "default": d.rename, "label": "Rename with BPM and key"},
            {"name": "key", "type": "choice", "default": d.key, "label": "Key notation",
             "choices": [{"value": "standard", "label": "Standard (Am)"},
                         {"value": "camelot", "label": "Camelot (8A)"}]},
            {"name": "bpm_range", "type": "range", "default": list(d.bpm_range), "label": "Tempo range",
             "help": "Half/double-time detections are folded into this range"},
            {"name": "out", "type": "path", "default": d.out, "label": "Folder for downloaded links"},
            {"name": "force", "type": "bool", "default": d.force, "label": "Re-analyze named files"},
        ],
    }


def parse_options(raw) -> dict:
    """Validate client options; returns the full normalized set (stored with the job)."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("options must be an object")
    unknown = set(raw) - {f.name for f in dataclasses.fields(pipeline.Options)}
    if unknown:
        raise ValueError(f"unknown option(s): {', '.join(sorted(unknown))}")
    o = pipeline.Options()
    for name in ("stems", "rename", "force"):
        if name in raw:
            if not isinstance(raw[name], bool):
                raise ValueError(f"{name} must be true or false")
            setattr(o, name, raw[name])
    if "key" in raw:
        if raw["key"] not in ("standard", "camelot"):
            raise ValueError("key must be 'standard' or 'camelot'")
        o.key = raw["key"]
    if "bpm_range" in raw:
        try:
            o.bpm_range = pipeline.parse_bpm_range(raw["bpm_range"])
        except (TypeError, ValueError) as e:
            raise ValueError(str(e)) from None
    if "out" in raw:
        if not isinstance(raw["out"], str) or not os.path.isabs(os.path.expanduser(raw["out"])):
            raise ValueError("out must be an absolute path")
        o.out = os.path.expanduser(raw["out"])
    d = dataclasses.asdict(o)
    d["bpm_range"] = list(o.bpm_range)
    return d


class Engine:
    """The persistent queue plus the worker thread that runs it."""

    def __init__(self, store_path, make_splitter, run=pipeline.run):
        self.path = store_path
        self.lock = threading.Condition()
        self.jobs = {}      # id -> job dict; insertion order is queue order
        self.events = []    # recent events across all jobs, for /events
        self.seq = 0
        self.clients = 0    # open /events streams
        self.last_activity = time.monotonic()
        self._cancel = set()
        self._make_splitter = make_splitter
        self._splitter = None
        self._run = run
        self._stop = False
        self._thread = None
        with self.lock:
            self._load()

    # --- persistence ---------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            os.replace(self.path, self.path + ".bad")  # keep it for inspection, start empty
            return
        self.seq = int(data.get("seq", 0))
        for job in data.get("jobs", []):
            self.jobs[job["id"]] = job
        for job in self.jobs.values():
            if job["state"] == "running":  # interrupted by a restart: run it again
                self._set_state(job, "queued", "engine restarted")
        self.events = sorted((e for j in self.jobs.values() for e in j["events"]), key=lambda e: e["seq"])
        self.events = self.events[-KEEP_EVENTS:]

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"seq": self.seq, "jobs": list(self.jobs.values())}, f)
        os.replace(tmp, self.path)

    # --- events (call with the lock held) ------------------------------------------------
    def _emit(self, job_id, event, **kw):
        self.seq += 1
        ev = {"event": event, "job": job_id, "seq": self.seq, **kw}
        job = self.jobs.get(job_id)
        if job is not None:
            evs = job["events"]
            evs.append(ev)
            if len(evs) > KEEP_JOB_EVENTS:
                del evs[next((i for i, e in enumerate(evs) if e["event"] == "log"), 0)]
        self.events.append(ev)
        del self.events[:-KEEP_EVENTS]
        self._save()
        self.lock.notify_all()
        return ev

    def _set_state(self, job, state, reason=None):
        job["state"], job["reason"] = state, reason
        self._emit(job["id"], "state", state=state, **({"reason": reason} if reason else {}))

    # --- API -----------------------------------------------------------------------------
    def submit(self, input, options=None):
        if not isinstance(input, str) or not input.strip():
            raise ValueError("input must be a file, folder or link")
        input = input.strip()
        if not fetch.is_link(input):
            input = os.path.expanduser(input)
            if not os.path.isabs(input):
                raise ValueError("input path must be absolute")
            if not os.path.exists(input):
                raise ValueError(f"not found: {input}")
        opts = parse_options(options)
        with self.lock:
            job = {"id": uuid.uuid4().hex[:12], "input": input, "options": opts, "state": "queued",
                   "reason": None, "created": time.time(), "events": []}
            self.jobs[job["id"]] = job
            self._set_state(job, "queued")
            self.last_activity = time.monotonic()
            return dict(job)

    def list(self):
        with self.lock:
            return [dict(j, events=list(j["events"])) for j in self.jobs.values()]

    def get(self, job_id):
        with self.lock:
            j = self.jobs.get(job_id)
            return dict(j, events=list(j["events"])) if j else None

    def cancel(self, job_id):
        """Returns the job, or None if unknown. A running job is flagged and stops at its next event."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job["state"] == "queued":
                self._set_state(job, "cancelled")
            elif job["state"] == "running":
                self._cancel.add(job_id)
            return dict(job)

    def remove(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return False
            if job["state"] == "running":
                self._cancel.add(job_id)
            del self.jobs[job_id]
            self._emit(job_id, "removed")
            return True

    def events_after(self, since):
        """(events, resync): resync is True when the caller missed events we no longer keep."""
        if since > self.seq or (self.events and since < self.events[0]["seq"] - 1):
            return [], True
        return [e for e in self.events if e["seq"] > since], False

    # --- worker --------------------------------------------------------------------------
    def start(self, idle_exit=0, on_idle=lambda: None):
        self._thread = threading.Thread(target=self._work, args=(idle_exit, on_idle), daemon=True)
        self._thread.start()

    def stop(self):
        with self.lock:
            self._stop = True
            self.lock.notify_all()
        if self._thread:
            self._thread.join(timeout=5)
        if self._splitter is not None:
            self._splitter.close()

    def wait_idle(self, timeout=10):
        """Block until nothing is queued or running (for tests)."""
        end = time.monotonic() + timeout
        with self.lock:
            while any(j["state"] in ACTIVE for j in self.jobs.values()):
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.lock.wait(left)
        return True

    def _splitter_once(self):
        if self._splitter is None:
            self._splitter = self._make_splitter()
        return self._splitter

    def _work(self, idle_exit, on_idle):
        while True:
            with self.lock:
                job = None
                while not self._stop:
                    job = next((j for j in self.jobs.values() if j["state"] == "queued"), None)
                    if job:
                        break
                    if (idle_exit and not self.clients
                            and time.monotonic() - self.last_activity > idle_exit):
                        on_idle()
                        return
                    self.lock.wait(timeout=1)
                if self._stop:
                    return
                self._set_state(job, "running")
            self._process(job)
            with self.lock:
                self.last_activity = time.monotonic()

    def _process(self, job):
        job_id, last_error = job["id"], [None]

        def emit(event, **kw):
            with self.lock:
                if job_id in self._cancel:
                    raise pipeline.Cancelled()
                if event == "error":
                    last_error[0] = kw.get("reason")
                self._emit(job_id, event, **kw)

        o = dict(job["options"])
        o["bpm_range"] = tuple(o["bpm_range"])
        try:
            failed, had_input = self._run([job["input"]], pipeline.Options(**o), emit, self._splitter_once)
            final = ("failed", last_error[0] or "nothing to do") if failed or not had_input else ("done", None)
        except pipeline.Cancelled:
            final = ("cancelled", None)
        except Exception as e:  # e.g. the models failed to load; keep serving
            traceback.print_exc()
            final = ("failed", f"engine error: {e}")
        with self.lock:
            self._cancel.discard(job_id)
            if job_id in self.jobs:
                self._set_state(job, *final)


class Handler(BaseHTTPRequestHandler):
    server_version = f"samplebuddy/{__version__}"

    def log_message(self, fmt, *args):
        pass

    @property
    def engine(self) -> Engine:
        return self.server.engine

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        # Any web page can reach 127.0.0.1, so refuse whatever a browser could send:
        # cross-origin requests (Origin / Sec-Fetch-*) and DNS-rebinding hosts.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if (self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site")
                or host not in ("127.0.0.1", "localhost", "[::1]")):
            self._send(403, {"error": "only local apps may use this engine"})
            return False
        with self.engine.lock:
            self.engine.last_activity = time.monotonic()
        return True

    def _path(self):
        return [p for p in urlparse(self.path).path.split("/") if p]

    def do_GET(self):
        if not self._allowed():
            return
        p = self._path()
        if p == ["health"]:
            self._send(200, {"ok": True, "version": __version__, "pid": os.getpid(),
                             "port": self.server.server_address[1]})
        elif p == ["capabilities"]:
            self._send(200, capabilities())
        elif p == ["jobs"]:
            self._send(200, {"jobs": self.engine.list(), "seq": self.engine.seq})
        elif len(p) == 2 and p[0] == "jobs":
            job = self.engine.get(p[1])
            self._send(200, job) if job else self._send(404, {"error": "no such job"})
        elif p == ["events"]:
            try:
                since = int(parse_qs(urlparse(self.path).query).get("since", ["0"])[0])
            except ValueError:
                return self._send(400, {"error": "since must be a number"})
            self._stream(since)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._allowed():
            return
        p = self._path()
        if p == ["jobs"]:
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(415, {"error": "send JSON"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                job = self.engine.submit(body.get("input"), body.get("options"))
            except (ValueError, AttributeError) as e:
                return self._send(400, {"error": str(e)})
            self._send(201, job)
        elif len(p) == 3 and p[0] == "jobs" and p[2] == "cancel":
            job = self.engine.cancel(p[1])
            self._send(200, job) if job else self._send(404, {"error": "no such job"})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._allowed():
            return
        p = self._path()
        if len(p) == 2 and p[0] == "jobs" and self.engine.remove(p[1]):
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "no such job"})

    def _stream(self, since):
        eng = self.engine
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with eng.lock:
            eng.clients += 1
        try:
            while not getattr(self.server, "closing", False):
                with eng.lock:
                    new, resync = eng.events_after(since)
                    if not new and not resync:
                        eng.lock.wait(timeout=15)
                        new, resync = eng.events_after(since)
                if resync:
                    new = [{"event": "resync", "seq": eng.seq}]
                if new:
                    for ev in new:
                        self.wfile.write(f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n".encode())
                    since = new[-1]["seq"]
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except OSError:  # client went away
            pass
        finally:
            with eng.lock:
                eng.clients -= 1


def make_server(engine, port=DEFAULT_PORT):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    httpd.engine = engine
    return httpd


def running_server(directory=None):
    """The port of a healthy samplebuddy server for this user, or None."""
    import urllib.request
    try:
        with open(port_file(directory)) as f:
            port = int(json.load(f)["port"])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return port if json.load(r).get("ok") else None
    except (OSError, ValueError, KeyError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="samplebuddy serve", description="Run the shared local engine.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    ap.add_argument("--models", default=pipeline.default_model_dir(), help="model cache dir")
    ap.add_argument("--state-dir", default=state_dir(), help="where queue.json and server.json live")
    ap.add_argument("--idle-exit", type=float, default=0, metavar="SECONDS",
                    help="quit after this long with no jobs and no connected clients (default: never)")
    args = ap.parse_args(argv)

    port = running_server(args.state_dir)
    if port:
        print(f"samplebuddy is already serving on 127.0.0.1:{port}", flush=True)
        return 0

    def make_splitter():
        from .separate import StemSplitter
        return StemSplitter(args.models)

    engine = Engine(os.path.join(args.state_dir, "queue.json"), make_splitter)
    httpd = None
    for p in range(args.port, args.port + 10):  # the default may be taken by something else
        try:
            httpd = make_server(engine, p)
            break
        except OSError:
            continue
    if httpd is None:
        print(f"no free port in {args.port}-{args.port + 9}", file=sys.stderr)
        return 1
    port = httpd.server_address[1]
    pf = port_file(args.state_dir)
    os.makedirs(args.state_dir, exist_ok=True)
    with open(pf, "w") as f:
        json.dump({"port": port, "pid": os.getpid(), "version": __version__}, f)

    def shut_down():
        httpd.closing = True
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    engine.start(idle_exit=args.idle_exit, on_idle=shut_down)
    print(f"samplebuddy {__version__} serving on http://127.0.0.1:{port} "
          f"({sum(j['state'] in ACTIVE for j in engine.list())} job(s) queued)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.closing = True
        engine.stop()
        httpd.server_close()
        try:
            with open(pf) as f:
                if json.load(f).get("pid") == os.getpid():
                    os.remove(pf)
        except (OSError, ValueError):
            pass
    return 0
