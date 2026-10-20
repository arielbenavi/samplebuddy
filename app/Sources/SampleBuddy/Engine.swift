import Foundation

/// One line of `samplebuddy --json` output.
struct EngineEvent: Decodable {
    let event: String
    var file: String?
    var source: String?
    var phase: String?
    var reason: String?
    var message: String?
    var bpm: Int?
    var key: String?
    var key_strength: Double?
    var dir: String?
    var files: [String]?
}

/// Locates and runs the `samplebuddy` CLI (the Python engine), streaming its JSON events.
enum Engine {
    /// GUI apps launch with a bare PATH; the engine needs ffmpeg, and fetchers may need go/brew.
    static let searchPath = [
        NSString(string: "~/.local/bin").expandingTildeInPath,
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
    ]

    static func locate(override: String) -> String? {
        let fm = FileManager.default
        if !override.isEmpty {
            let p = NSString(string: override).expandingTildeInPath
            return fm.isExecutableFile(atPath: p) ? p : nil
        }
        return searchPath.map { "\($0)/samplebuddy" }.first { fm.isExecutableFile(atPath: $0) }
    }

    /// Runs the engine and calls `onEvent` on the main actor for every event. Returns the exit code.
    static func run(binary: String, arguments: [String], process: Process,
                    onEvent: @escaping @MainActor (EngineEvent) -> Void,
                    onLog: @escaping @MainActor (String) -> Void) async -> Int32 {
        process.executableURL = URL(fileURLWithPath: binary)
        process.arguments = ["--json"] + arguments
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = searchPath.joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err

        // stderr carries model-loading and progress-bar noise; keep the last lines for error reports
        err.fileHandleForReading.readabilityHandler = { h in
            let data = h.availableData
            guard !data.isEmpty, let s = String(data: data, encoding: .utf8) else { return }
            let lines = s.split(whereSeparator: \.isNewline).map(String.init)
                .filter { !$0.contains("it/s") && !$0.contains("s/it") }
            if !lines.isEmpty { Task { @MainActor in lines.forEach(onLog) } }
        }

        do { try process.run() } catch {
            await onLog("could not start engine: \(error.localizedDescription)")
            return -1
        }
        do {
            for try await line in out.fileHandleForReading.bytes.lines {
                if let data = line.data(using: .utf8),
                   let ev = try? JSONDecoder().decode(EngineEvent.self, from: data) {
                    await onEvent(ev)
                } else {
                    await onLog(line)
                }
            }
        } catch {
            await onLog("engine output error: \(error.localizedDescription)")
        }
        process.waitUntilExit()
        err.fileHandleForReading.readabilityHandler = nil
        return process.terminationStatus
    }

    /// The engine reads the link downloader from this file (see samplebuddy/fetch.py).
    static var configURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/samplebuddy/config.json")
    }

    static func readFetcher() -> String {
        guard let data = try? Data(contentsOf: configURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        return obj["fetcher"] as? String ?? ""
    }

    static func writeFetcher(_ path: String) {
        var obj: [String: Any] = [:]
        if let data = try? Data(contentsOf: configURL),
           let existing = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { obj = existing }
        obj["fetcher"] = path.isEmpty ? nil : path
        try? FileManager.default.createDirectory(at: configURL.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted]) {
            try? data.write(to: configURL)
        }
    }
}
