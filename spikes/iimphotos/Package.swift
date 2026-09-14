// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "iimphotos",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "iimphotos",
            path: "Sources/iimphotos",
            exclude: ["Info.plist"],
            linkerSettings: [
                // A command line tool has no bundle, so the Photos usage
                // description has to be embedded in the binary itself or
                // requesting authorisation terminates the process.
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Sources/iimphotos/Info.plist",
                ])
            ]
        )
    ]
)
