// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "SampleBuddy",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "SampleBuddy", path: "Sources/SampleBuddy")
    ]
)
