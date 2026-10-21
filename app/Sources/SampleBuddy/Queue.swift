import Foundation
import SwiftUI

enum JobState: Equatable {
    case queued, running(String), done, failed(String), cancelled

    var isActive: Bool { if case .running = self { return true } else { return false } }
}

@MainActor
final class Job: ObservableObject, Identifiable {
    let id = UUID()
    let input: String          // a link or a local file path
    var isLink: Bool { input.hasPrefix("http") || input.hasPrefix("spotify:") }

    @Published var title: String
    @Published var state: JobState = .queued
    @Published var track: URL?
    @Published var bpm: Int?
    @Published var key: String?
    @Published var keyLowConfidence = false
    @Published var stems: [URL] = []
    @Published var log: [String] = []

    var serviceID: String?     // nil until the engine service accepted it
    var lastSeq = 0            // last service event applied, so replays are idempotent
    var lastError: String?

    init(input: String) {
        self.input = input
        title = input.hasPrefix("http") || input.hasPrefix("spotify:")
            ? "Link: " + input.replacingOccurrences(of: "https://", with: "")
            : URL(fileURLWithPath: input).deletingPathExtension().lastPathComponent
        if !isLink { track = URL(fileURLWithPath: input) }
    }

    var stemsFolder: URL? { stems.first?.deletingLastPathComponent() }
}

/// Mirrors the engine service's shared job queue (`samplebuddy serve`). The service runs one job
/// at a time and keeps the models loaded; other clients (e.g. a DAW plugin) see the same queue.
@MainActor
final class QueueModel: ObservableObject {
    @Published var jobs: [Job] = []
    @Published var engineMissing = false

    @AppStorage("outputFolder") var outputFolder = NSString(string: "~/Downloads").expandingTildeInPath
    @AppStorage("keyNotation") var keyNotation = "standard"
    @AppStorage("makeStems") var makeStems = true
    @AppStorage("bpmRange") var bpmRange = "65-150"
    @AppStorage("enginePath") var enginePath = ""

    static let audioExtensions: Set<String> = ["flac", "wav", "aif", "aiff", "mp3", "m4a", "ogg", "opus"]

    private var base: URL?
    private var lastSeq = 0
    private var pendingSubmits = 0
    private var connection: Task<Void, Never>?
    private var startError: String?

    init() {
        connection = Task { await connect() }
    }

    func add(links text: String) {
        let links = text.split(whereSeparator: { $0.isWhitespace })
            .map(String.init)
            .filter { $0.hasPrefix("http") || $0.hasPrefix("spotify:") }
        links.forEach { jobs.append(Job(input: $0)) }
        submitPending()
    }

    func add(files urls: [URL]) {
        let fm = FileManager.default
        for url in urls {
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else { continue }
            if isDir.boolValue {
                let items = (try? fm.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? []
                for f in items.sorted(by: { $0.lastPathComponent < $1.lastPathComponent })
                where Self.audioExtensions.contains(f.pathExtension.lowercased()) {
                    jobs.append(Job(input: f.path))
                }
            } else if Self.audioExtensions.contains(url.pathExtension.lowercased()) {
                jobs.append(Job(input: url.path))
            }
        }
        submitPending()
    }

    func retry(_ job: Job) {
        guard !job.state.isActive else { return }
        let fresh = Job(input: job.track?.path ?? job.input)
        if let i = jobs.firstIndex(where: { $0.id == job.id }) { jobs[i] = fresh }
        forget(job)
        submitPending()
    }

    func cancel(_ job: Job) {
        job.state = .cancelled
        guard let base, let id = job.serviceID, !id.isEmpty else { return }
        Task { try? await Engine.cancel(base, id: id) }
    }

    func remove(_ job: Job) {
        if job.state == .queued || job.state.isActive { job.state = .cancelled }
        jobs.removeAll { $0.id == job.id }
        forget(job)
    }

    func clearFinished() {
        let finished = jobs.filter { if case .queued = $0.state { return false }; return !$0.state.isActive }
        jobs.removeAll { j in finished.contains { $0 === j } }
        finished.forEach(forget)
    }

    /// Drops a job from the service's queue too (cancelling it there if it's still going).
    private func forget(_ job: Job) {
        guard let base, let id = job.serviceID, !id.isEmpty else { return }
        Task { try? await Engine.remove(base, id: id) }
    }

    // MARK: service connection

    private func options() -> [String: Any] {
        ["stems": makeStems, "key": keyNotation, "bpm_range": bpmRange, "out": outputFolder]
    }

    /// Sends every job the service hasn't accepted yet, in list order.
    private func submitPending() {
        guard let base else {
            if let startError { failUnsent(startError) }
            return
        }
        for job in jobs where job.serviceID == nil && job.state == .queued {
            job.serviceID = ""  // in flight
            pendingSubmits += 1
            let opts = options()
            Task {
                defer { pendingSubmits -= 1 }
                do {
                    let sj = try await Engine.submit(base, input: job.input, options: opts)
                    job.serviceID = sj.id
                    if !jobs.contains(where: { $0 === job }) {  // removed while in flight
                        try? await Engine.remove(base, id: sj.id)
                    } else if job.state == .cancelled {
                        try? await Engine.cancel(base, id: sj.id)
                    } else {
                        sj.events.forEach { apply($0, to: job) }
                    }
                } catch {
                    job.serviceID = nil
                    job.state = .failed(error.localizedDescription)
                }
            }
        }
    }

    private func failUnsent(_ reason: String) {
        for job in jobs where job.serviceID == nil && job.state == .queued { job.state = .failed(reason) }
    }

    /// Finds or starts the service, syncs the queue, then follows its event stream; reconnects forever.
    private func connect() async {
        while !Task.isCancelled {
            guard let binary = Engine.locate(override: enginePath) else {
                // a service someone else started is fine too
                let b = Engine.base(port: Engine.advertisedPort())
                if await Engine.healthy(b) { await follow(b) } else {
                    engineMissing = true
                    base = nil
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                }
                continue
            }
            engineMissing = false
            do {
                let b = try await Engine.ensureRunning(binary: binary)
                startError = nil
                await follow(b)
            } catch {
                base = nil
                startError = error.localizedDescription
                failUnsent(error.localizedDescription)
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }

    private func follow(_ b: URL) async {
        engineMissing = false
        base = b
        do {
            try await resync()
            submitPending()
            try await Engine.events(b, since: lastSeq) { [weak self] ev in self?.handle(ev) }
        } catch {}
        base = nil
        try? await Task.sleep(nanoseconds: 1_000_000_000)
    }

    /// Brings the list in line with the service's queue (on connect, or after missing events).
    private func resync() async throws {
        guard let base else { return }
        let list = try await Engine.jobs(base)
        let ids = Set(list.jobs.map(\.id))
        // jobs the service no longer has were removed elsewhere (or by a reset)
        jobs.removeAll { if let id = $0.serviceID, !id.isEmpty { return !ids.contains(id) }; return false }
        for sj in list.jobs { adopt(sj) }
        lastSeq = max(lastSeq, list.seq)
    }

    private func adopt(_ sj: ServiceJob) {
        if let job = jobs.first(where: { $0.serviceID == sj.id }) {
            sj.events.forEach { apply($0, to: job) }
        } else {
            let job = Job(input: sj.input)
            job.serviceID = sj.id
            sj.events.forEach { apply($0, to: job) }
            jobs.append(job)
        }
    }

    private func handle(_ ev: EngineEvent) {
        if let s = ev.seq { lastSeq = max(lastSeq, s) }
        if ev.event == "resync" {
            Task { try? await resync() }
            return
        }
        guard let id = ev.job else { return }
        if ev.event == "removed" {
            jobs.removeAll { $0.serviceID == id }
            return
        }
        if let job = jobs.first(where: { $0.serviceID == id }) {
            apply(ev, to: job)
        } else {
            discover(id)
        }
    }

    /// A job we don't know: added by another client, or ours with the submit reply still in flight.
    private func discover(_ id: String) {
        guard let base else { return }
        Task {
            while pendingSubmits > 0 { try? await Task.sleep(nanoseconds: 100_000_000) }
            if let sj = try? await Engine.job(base, id: id) { adopt(sj) }
        }
    }

    private func apply(_ ev: EngineEvent, to job: Job) {
        if let s = ev.seq {
            guard s > job.lastSeq else { return }
            job.lastSeq = s
        }
        // the user cancelled here; ignore the tail of events until the service confirms
        if job.state == .cancelled && ev.event != "state" { return }
        switch ev.event {
        case "state":
            switch ev.state {
            case "queued": job.state = .queued
            case "running": job.state = .running(job.isLink ? "downloading" : "starting")
            case "done":
                if job.bpm == nil, let t = job.track, let parsed = Self.parsePrefix(t) {
                    (job.bpm, job.key) = parsed
                }
                job.state = .done
            case "failed": job.state = .failed(ev.reason ?? job.lastError ?? job.log.last ?? "failed")
            case "cancelled": job.state = .cancelled
            default: break
            }
        case "fetched":
            if let f = ev.file {
                job.track = URL(fileURLWithPath: f)
                job.title = URL(fileURLWithPath: f).deletingPathExtension().lastPathComponent
            }
        case "log":
            if let m = ev.message { job.log.append(m) }
        case "progress":
            job.state = .running(ev.phase ?? "working")
        case "analyzed", "skip":
            if let f = ev.file, ev.event == "analyzed" {
                job.track = URL(fileURLWithPath: f)
                job.title = URL(fileURLWithPath: f).deletingPathExtension().lastPathComponent
            }
            if let b = ev.bpm { job.bpm = b }
            if let k = ev.key { job.key = k }
            if let s = ev.key_strength { job.keyLowConfidence = s < 0.55 }
            if let files = ev.files { job.stems = files.map { URL(fileURLWithPath: $0) } }
        case "stems":
            job.stems = (ev.files ?? []).map { URL(fileURLWithPath: $0) }
        case "error":
            job.lastError = ev.reason
            job.log.append("error: \(ev.reason ?? "unknown")")
        default:
            break
        }
        if job.log.count > 400 { job.log.removeFirst(job.log.count - 400) }
    }

    /// "92 BPM Am - Work" -> (92, "Am"), for files that were already analyzed earlier.
    static func parsePrefix(_ url: URL) -> (Int, String)? {
        let parts = url.lastPathComponent.split(separator: " ", maxSplits: 3).map(String.init)
        guard parts.count >= 3, parts[1] == "BPM", let bpm = Int(parts[0]) else { return nil }
        return (bpm, parts[2])
    }
}
