import Foundation

/// One engine event: a `samplebuddy --json` line, as streamed by `samplebuddy serve` (plus job/seq/state).
struct EngineEvent: Decodable {
    let event: String
    var job: String?
    var seq: Int?
    var state: String?
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

/// A job as the service reports it (`GET /jobs`).
struct ServiceJob: Decodable {
    let id: String
    let input: String
    let state: String
    var reason: String?
    var events: [EngineEvent]
}

struct ServiceJobList: Decodable {
    let jobs: [ServiceJob]
    let seq: Int
}

enum EngineError: LocalizedError {
    case http(Int, String)
    case startFailed(String)

    var errorDescription: String? {
        switch self {
        case .http(_, let message): return message
        case .startFailed(let message): return message
        }
    }
}

/// Finds or starts the local engine service (`samplebuddy serve`) and talks to it over 127.0.0.1.
enum Engine {
    /// GUI apps launch with a bare PATH; the engine needs ffmpeg, and fetchers may need go/brew.
    static let searchPath = [
        NSString(string: "~/.local/bin").expandingTildeInPath,
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
    ]
    static let defaultPort = 7429
    /// The service quits after this long with no jobs and no app connected (frees the models' RAM).
    static let idleExitSeconds = 900

    static func locate(override: String) -> String? {
        let fm = FileManager.default
        if !override.isEmpty {
            let p = NSString(string: override).expandingTildeInPath
            return fm.isExecutableFile(atPath: p) ? p : nil
        }
        return searchPath.map { "\($0)/samplebuddy" }.first { fm.isExecutableFile(atPath: $0) }
    }

    static var supportDir: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/samplebuddy")
    }

    static var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/SampleBuddy/engine.log")
    }

    static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 60   // the event stream sends a keepalive every 15 s
        config.timeoutIntervalForResource = .infinity
        return URLSession(configuration: config)
    }()

    // MARK: service discovery

    static func base(port: Int) -> URL { URL(string: "http://127.0.0.1:\(port)")! }

    /// The port the running service wrote to server.json, else the default.
    static func advertisedPort() -> Int {
        guard let data = try? Data(contentsOf: supportDir.appendingPathComponent("server.json")),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let port = obj["port"] as? Int else { return defaultPort }
        return port
    }

    static func healthy(_ base: URL) async -> Bool {
        var req = URLRequest(url: base.appendingPathComponent("health"))
        req.timeoutInterval = 2
        guard let (data, resp) = try? await session.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return obj["ok"] as? Bool == true
    }

    /// Returns the service's base URL, starting `samplebuddy serve` if nothing answers.
    static func ensureRunning(binary: String) async throws -> URL {
        if await healthy(base(port: advertisedPort())) { return base(port: advertisedPort()) }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: binary)
        process.arguments = ["serve", "--idle-exit", String(idleExitSeconds)]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = searchPath.joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let log = try? FileHandle(forWritingTo: logURL)
        log?.seekToEndOfFile()
        process.standardOutput = log
        process.standardError = log
        process.standardInput = FileHandle.nullDevice
        do { try process.run() } catch {
            throw EngineError.startFailed("could not start engine: \(error.localizedDescription)")
        }

        // Python + torch imports take a few seconds on a cold start
        for _ in 0..<100 {
            try await Task.sleep(nanoseconds: 300_000_000)
            if await healthy(base(port: advertisedPort())) { return base(port: advertisedPort()) }
            if !process.isRunning {
                // it exits at once if another client started the service first
                if await healthy(base(port: advertisedPort())) { return base(port: advertisedPort()) }
                throw EngineError.startFailed("the engine stopped while starting (is it up to date? "
                    + "Run: uv tool upgrade samplebuddy). Last output: \(lastLogLine())")
            }
        }
        throw EngineError.startFailed("the engine didn't answer within 30 s. See \(logURL.path)")
    }

    private static func lastLogLine() -> String {
        guard let s = try? String(contentsOf: logURL, encoding: .utf8) else { return "none" }
        return s.split(whereSeparator: \.isNewline).last.map(String.init) ?? "none"
    }

    // MARK: API

    private static func call(_ base: URL, _ method: String, _ path: String, body: Any? = nil) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = method
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            throw EngineError.http(code, msg ?? "engine returned HTTP \(code)")
        }
        return data
    }

    static func submit(_ base: URL, input: String, options: [String: Any]) async throws -> ServiceJob {
        let data = try await call(base, "POST", "jobs", body: ["input": input, "options": options])
        return try JSONDecoder().decode(ServiceJob.self, from: data)
    }

    static func jobs(_ base: URL) async throws -> ServiceJobList {
        try JSONDecoder().decode(ServiceJobList.self, from: try await call(base, "GET", "jobs"))
    }

    static func job(_ base: URL, id: String) async throws -> ServiceJob {
        try JSONDecoder().decode(ServiceJob.self, from: try await call(base, "GET", "jobs/\(id)"))
    }

    static func cancel(_ base: URL, id: String) async throws {
        _ = try await call(base, "POST", "jobs/\(id)/cancel")
    }

    static func remove(_ base: URL, id: String) async throws {
        _ = try await call(base, "DELETE", "jobs/\(id)")
    }

    /// Streams every event after `since` until the connection drops.
    static func events(_ base: URL, since: Int, onEvent: @MainActor (EngineEvent) -> Void) async throws {
        var comps = URLComponents(url: base.appendingPathComponent("events"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "since", value: String(since))]
        let (bytes, resp) = try await session.bytes(from: comps.url!)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw EngineError.http((resp as? HTTPURLResponse)?.statusCode ?? 0, "event stream refused")
        }
        for try await line in bytes.lines where line.hasPrefix("data: ") {
            if let ev = try? JSONDecoder().decode(EngineEvent.self, from: Data(line.dropFirst(6).utf8)) {
                await onEvent(ev)
            }
        }
    }

    // MARK: link downloader config

    /// The engine reads the link downloader from this file (see samplebuddy/fetch.py).
    static var configURL: URL { supportDir.appendingPathComponent("config.json") }

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
