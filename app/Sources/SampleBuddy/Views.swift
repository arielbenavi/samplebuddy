import AVFoundation
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject var queue: QueueModel
    @State private var linkText = ""
    @State private var dropTargeted = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if queue.engineMissing { engineMissingBanner }
            if queue.jobs.isEmpty {
                emptyState
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(queue.jobs) { JobRow(job: $0) }
                    }
                    .padding(14)
                }
            }
        }
        .frame(minWidth: 640, minHeight: 440)
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 3, dash: [10]))
                    .background(Color.accentColor.opacity(0.08))
                    .padding(8)
                    .allowsHitTesting(false)
            }
        }
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            for p in providers {
                _ = p.loadObject(ofClass: URL.self) { url, _ in
                    if let url { Task { @MainActor in queue.add(files: [url]) } }
                }
            }
            return true
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "link")
                .foregroundStyle(.secondary)
            TextField("Paste a link (or several), or drop audio files anywhere", text: $linkText)
                .textFieldStyle(.roundedBorder)
                .onSubmit(addLinks)
            Button("Add", action: addLinks)
                .keyboardShortcut(.defaultAction)
                .disabled(linkText.trimmingCharacters(in: .whitespaces).isEmpty)
            Button {
                let panel = NSOpenPanel()
                panel.allowsMultipleSelection = true
                panel.canChooseDirectories = true
                panel.allowedContentTypes = [.audio, .folder]
                if panel.runModal() == .OK { queue.add(files: panel.urls) }
            } label: { Image(systemName: "folder.badge.plus") }
                .help("Add audio files or a folder")
                .accessibilityLabel("Add files")
            Button { queue.clearFinished() } label: { Image(systemName: "checklist.checked") }
                .help("Clear finished")
                .accessibilityLabel("Clear finished")
        }
        .padding(12)
    }

    private func addLinks() {
        queue.add(links: linkText)
        linkText = ""
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "waveform.badge.plus")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.secondary)
            Text("Drop tracks here or paste a link")
                .font(.title3.weight(.medium))
            Text("Every track comes back named \"BPM Key - Title\", with Vocals, Drums, Bass, Other and Instrumental stems you can drag straight into FL Studio or any DAW.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var engineMissingBanner: some View {
        HStack {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            Text("The SampleBuddy engine isn't installed. In Terminal run: uv tool install samplebuddy, or set its path in Settings.")
                .font(.callout)
            Spacer()
        }
        .padding(10)
        .background(Color.orange.opacity(0.12))
    }
}

struct JobRow: View {
    @ObservedObject var job: Job
    @EnvironmentObject var queue: QueueModel
    @State private var showLog = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                statusIcon
                VStack(alignment: .leading, spacing: 2) {
                    Text(job.title).font(.headline).lineLimit(1).truncationMode(.middle)
                    statusText.font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if let bpm = job.bpm { Badge(text: "\(bpm) BPM") }
                if let key = job.key {
                    Badge(text: key, warn: job.keyLowConfidence)
                        .help(job.keyLowConfidence ? "Low key confidence: check by ear" : "Detected key")
                }
                menu
            }
            if !job.stems.isEmpty || job.state == .done, let track = job.track {
                StemStrip(track: track, stems: job.stems)
            }
            if showLog {
                ScrollView {
                    Text(job.log.suffix(60).joined(separator: "\n"))
                        .font(.system(.caption2, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(height: 110)
                .padding(6)
                .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.05)))
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.primary.opacity(0.08)))
    }

    @ViewBuilder private var statusIcon: some View {
        switch job.state {
        case .queued: Image(systemName: "clock").foregroundStyle(.secondary)
        case .running: ProgressView().controlSize(.small)
        case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
        case .cancelled: Image(systemName: "stop.circle").foregroundStyle(.secondary)
        }
    }

    private var statusText: Text {
        switch job.state {
        case .queued: Text("Waiting")
        case .running(let phase): Text(phase.prefix(1).uppercased() + phase.dropFirst() + "…")
        case .done: Text(job.stems.isEmpty ? "Done" : "Done · stems ready to drag")
        case .failed(let why): Text(why).foregroundColor(.red)
        case .cancelled: Text("Cancelled")
        }
    }

    private var menu: some View {
        Menu {
            if let t = job.track, FileManager.default.fileExists(atPath: t.path) {
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([job.stemsFolder ?? t]) }
            }
            Button(showLog ? "Hide log" : "Show log") { showLog.toggle() }
            Divider()
            if job.state.isActive || job.state == .queued { Button("Cancel") { queue.cancel(job) } }
            if case .failed = job.state { Button("Retry") { queue.retry(job) } }
            if job.state == .cancelled { Button("Retry") { queue.retry(job) } }
            Button("Remove from list") { queue.remove(job) }
        } label: { Image(systemName: "ellipsis.circle") }
            .accessibilityLabel("Track actions")
            .menuStyle(.borderlessButton)
            .fixedSize()
    }
}

struct Badge: View {
    let text: String
    var warn = false
    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold).monospacedDigit())
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(Capsule().fill(warn ? Color.orange.opacity(0.2) : Color.accentColor.opacity(0.15)))
    }
}

/// The full track plus each stem as a tile: drag it into a DAW, click ▶ to audition.
struct StemStrip: View {
    let track: URL
    let stems: [URL]

    private var tiles: [(String, URL)] {
        var all = [("Full", track)]
        for s in stems {
            let name = s.deletingPathExtension().lastPathComponent
            let label = name.range(of: "(", options: .backwards).map {
                String(name[$0.upperBound...]).replacingOccurrences(of: ")", with: "")
            } ?? name
            all.append((label, s))
        }
        return all
    }

    var body: some View {
        HStack(spacing: 6) {
            ForEach(tiles, id: \.1) { StemTile(label: $0.0, url: $0.1) }
            Spacer(minLength: 0)
        }
    }
}

struct StemTile: View {
    let label: String
    let url: URL
    @ObservedObject private var player = Player.shared

    private var symbol: String {
        switch label {
        case "Vocals": return "music.mic"
        case "Drums": return "cylinder.split.1x2"
        case "Bass": return "speaker.wave.3"
        case "Other": return "pianokeys"
        case "Instrumental": return "guitars"
        default: return "waveform"
        }
    }

    var body: some View {
        HStack(spacing: 5) {
            Button { player.toggle(url) } label: {
                Image(systemName: player.playing == url ? "stop.fill" : "play.fill").frame(width: 12)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(player.playing == url ? "Stop \(label)" : "Play \(label)")
            Image(systemName: symbol).foregroundStyle(.secondary)
            Text(label).font(.caption.weight(.medium))
        }
        .padding(.horizontal, 8).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 7).fill(Color.primary.opacity(0.06)))
        .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(Color.primary.opacity(0.1)))
        .onDrag { NSItemProvider(contentsOf: url) ?? NSItemProvider() }
        .help("Drag into your DAW · \(url.lastPathComponent)")
    }
}

@MainActor
final class Player: NSObject, ObservableObject, AVAudioPlayerDelegate {
    static let shared = Player()
    @Published var playing: URL?
    private var player: AVAudioPlayer?

    func toggle(_ url: URL) {
        if playing == url { stop(); return }
        stop()
        guard let p = try? AVAudioPlayer(contentsOf: url) else { return }
        p.delegate = self
        p.play()
        player = p
        playing = url
    }

    func stop() {
        player?.stop()
        player = nil
        playing = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.stop() }
    }
}

struct SettingsView: View {
    @EnvironmentObject var queue: QueueModel
    @State private var fetcher = Engine.readFetcher()

    var body: some View {
        Form {
            Section("Output") {
                LabeledContent("Links download to") {
                    HStack {
                        Text(queue.outputFolder).lineLimit(1).truncationMode(.middle)
                        Button("Choose…") {
                            let panel = NSOpenPanel()
                            panel.canChooseDirectories = true
                            panel.canChooseFiles = false
                            if panel.runModal() == .OK, let u = panel.url { queue.outputFolder = u.path }
                        }
                    }
                }
                Text("Dropped files are processed where they are.").font(.caption).foregroundStyle(.secondary)
                Toggle("Separate stems (about 5 min per track)", isOn: $queue.makeStems)
            }
            Section("Analysis") {
                Picker("Key notation", selection: $queue.keyNotation) {
                    Text("Standard (Am)").tag("standard")
                    Text("Camelot (8A)").tag("camelot")
                }
                Picker("Tempo range", selection: $queue.bpmRange) {
                    Text("Hip-hop / R&B (65–150)").tag("65-150")
                    Text("House / Techno / DnB (85–180)").tag("85-180")
                }
            }
            Section("Advanced") {
                TextField("Engine path (blank = auto-detect)", text: $queue.enginePath)
                TextField("Link downloader (fetcher) path", text: $fetcher)
                    .onSubmit { Engine.writeFetcher(fetcher) }
                    .onChange(of: fetcher) { Engine.writeFetcher(fetcher) }
                Text("SampleBuddy doesn't include a downloader. A fetcher is any program run as `fetcher <out_dir> <url>…` that prints each downloaded file's path.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
    }
}
