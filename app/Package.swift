// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "PhotoSentinel",
    platforms: [.macOS(.v14)],
    targets: [
        // Everything that reads and decides: the ledger, analytics, findings, jobs. No UI.
        // Phase A reads the Python engine's ledger and logs; it writes nothing.
        .target(name: "PhotoCore", linkerSettings: [.linkedLibrary("sqlite3")]),
        // The window and menu bar. Thin: it renders PhotoCore's state and forwards clicks.
        .executableTarget(name: "PhotoSentinel", dependencies: ["PhotoCore"]),
        .testTarget(name: "PhotoCoreTests", dependencies: ["PhotoCore"]),
    ]
)
