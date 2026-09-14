// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "iimhelper",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "iimhelper",
            path: "Sources/iimhelper"
        )
    ]
)
