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
    var process: Process?

    init(input: String) {
        self.input = input
        title = input.hasPrefix("http") || input.hasPrefix("spotify:")
            ? "Link: " + input.replacingOccurrences(of: "https://", with: "")
            : URL(fileURLWithPath: input).deletingPathExtension().lastPathComponent
        if !isLink { track = URL(fileURLWithPath: input) }
    }

    var stemsFolder: URL? { stems.first?.deletingLastPathComponent() }
}

/// Runs one engine process per job, one job at a time (the separator saturates the GPU).
@MainActor
final class QueueModel: ObservableObject {
    @Published var jobs: [Job] = []
    @Published var engineMissing = false
    private var running = false

    @AppStorage("outputFolder") var outputFolder = NSString(string: "~/Downloads").expandingTildeInPath
    @AppStorage("keyNotation") var keyNotation = "standard"
    @AppStorage("makeStems") var makeStems = true
    @AppStorage("bpmRange") var bpmRange = "65-150"
    @AppStorage("enginePath") var enginePath = ""

    static let audioExtensions: Set<String> = ["flac", "wav", "aif", "aiff", "mp3", "m4a", "ogg", "opus"]

    func add(links text: String) {
        let links = text.split(whereSeparator: { $0.isWhitespace })
            .map(String.init)
            .filter { $0.hasPrefix("http") || $0.hasPrefix("spotify:") }
        links.forEach { jobs.append(Job(input: $0)) }
        pump()
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
        pump()
    }

    func retry(_ job: Job) {
        guard !job.state.isActive else { return }
        let fresh = Job(input: job.track?.path ?? job.input)
        if let i = jobs.firstIndex(where: { $0.id == job.id }) { jobs[i] = fresh }
        pump()
    }

    func cancel(_ job: Job) {
        if job.state.isActive { job.process?.terminate() }
        job.state = .cancelled
    }

    func remove(_ job: Job) {
        cancel(job)
        jobs.removeAll { $0.id == job.id }
    }

    func clearFinished() {
        jobs.removeAll { if case .queued = $0.state { return false }; return !$0.state.isActive }
    }

    private func pump() {
        guard !running, let job = jobs.first(where: { $0.state == .queued }) else { return }
        guard let binary = Engine.locate(override: enginePath) else {
            engineMissing = true
            return
        }
        engineMissing = false
        running = true
        Task {
            await run(job, binary: binary)
            running = false
            pump()
        }
    }

    private func run(_ job: Job, binary: String) async {
        job.state = .running(job.isLink ? "downloading" : "starting")
        var args = ["--key", keyNotation, "--bpm-range", bpmRange, "--out", outputFolder]
        if !makeStems { args.append("--no-stems") }
        args.append(job.input)
        let process = Process()
        job.process = process
        var lastError: String?

        let code = await Engine.run(binary: binary, arguments: args, process: process, onEvent: { ev in
            switch ev.event {
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
                lastError = ev.reason
                job.log.append("error: \(ev.reason ?? "unknown")")
            default:
                break
            }
        }, onLog: { line in
            job.log.append(line)
            if job.log.count > 400 { job.log.removeFirst(job.log.count - 400) }
        })

        if job.state == .cancelled { return }
        if code == 0 {
            if job.bpm == nil, let t = job.track, let parsed = Self.parsePrefix(t) {
                (job.bpm, job.key) = parsed
            }
            job.state = .done
        } else {
            job.state = .failed(lastError ?? job.log.last ?? "engine exited with code \(code)")
        }
    }

    /// "92 BPM Am - Work" -> (92, "Am"), for files that were already analyzed earlier.
    static func parsePrefix(_ url: URL) -> (Int, String)? {
        let parts = url.lastPathComponent.split(separator: " ", maxSplits: 3).map(String.init)
        guard parts.count >= 3, parts[1] == "BPM", let bpm = Int(parts[0]) else { return nil }
        return (bpm, parts[2])
    }
}
