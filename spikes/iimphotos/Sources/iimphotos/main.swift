import Foundation
import Photos
import AppKit

let usage = """
iimphotos: PhotoKit probe for the iPhone Image Manager P0b spike.

USAGE
    iimphotos auth                      Request and report Photos authorisation.
    iimphotos probe [--albums]          Survey the library: field coverage, types,
                                        subtypes, sizes. Read-only.
    iimphotos fetch [options]           Time on-demand original fetches. This is
                                        the measurement the chunking design
                                        depends on. Nothing is written to disk.
    iimphotos scan [options]            Dump every asset as JSON Lines. Read-only
                                        and costs no bandwidth: it emits only what
                                        PhotoKit answers without downloading.
    iimphotos export [options]          Write originals to disk. Reads work from
                                        stdin as <localIdentifier>TAB<path>.
    iimphotos trash                     Move files to the macOS Trash, never
                                        unlink them. Reads paths from stdin, one
                                        per line. Touches no photo library.
    iimphotos delete                    DELETE assets from the Photos library.
                                        Reads local identifiers from stdin, one
                                        per line. Confirms by re-fetching and
                                        exits non-zero if any survive. Deleted
                                        assets go to Recently Deleted, not to
                                        oblivion. macOS raises one confirmation
                                        dialog per invocation.
    iimphotos delete-roundtrip          Create a 1x1 test image OF ITS OWN, then
                                        delete it. Proves deletion works without
                                        touching any of your photographs.

SCAN OPTIONS
    --limit N        Stop after N assets. Default: 0, meaning all.

EXPORT OPTIONS
    --timeout S      Per-asset timeout in seconds. Default: 1800.
    --no-network     Refuse iCloud downloads, so only already-local assets
                     succeed. Useful for measuring what is resident.

FETCH OPTIONS
    --count N        How many assets to sample. Default: 10.
    --timeout S      Per-asset download timeout in seconds. Default: 300.
    --only-remote    Skip assets already stored locally, so the measurement is
                     of real iCloud downloads rather than disk reads.

OUTPUT
    JSON Lines on stdout, one object per line. Diagnostics on stderr.

SAFETY
    probe and fetch are read-only and contain no call to deleteAssets.
    delete-roundtrip deletes only an image it created itself, in the same run.
"""

var args = Array(CommandLine.arguments.dropFirst())
if args.isEmpty || args.contains("-h") || args.contains("--help") {
    print(usage)
    exit(args.isEmpty ? 64 : 0)
}
let command = args.removeFirst()

func option(_ name: String, default def: String) -> String {
    guard let i = args.firstIndex(of: name) else { return def }
    guard i + 1 < args.count else { Out.fail("option \(name) needs a value", code: 64) }
    return args[i + 1]
}

func describe(_ status: PHAuthorizationStatus) -> String {
    switch status {
    case .notDetermined: return "notDetermined"
    case .restricted:    return "restricted"
    case .denied:        return "denied"
    case .authorized:    return "authorized"
    case .limited:       return "limited"
    @unknown default:    return "unknown"
    }
}

/// Photos authorisation is asynchronous and needs a run loop turn.
func authorize() -> PHAuthorizationStatus {
    var status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    if status == .notDetermined {
        Out.log("requesting Photos access; approve the prompt")
        let semaphore = DispatchSemaphore(value: 0)
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { newStatus in
            status = newStatus
            semaphore.signal()
        }
        while semaphore.wait(timeout: .now() + 0.1) == .timedOut {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
        }
    }
    Out.emit(["event": "authorization", "status": describe(status)])
    return status
}

func requireAuthorization() {
    let status = authorize()
    guard status == .authorized else {
        Out.emit([
            "event": "error",
            "code": "NOT_AUTHORIZED",
            "status": describe(status),
            "message": status == .limited
                ? "Only limited access was granted. The tool needs full library access: "
                  + "System Settings > Privacy & Security > Photos."
                : "Photos access was not granted. Grant it in "
                  + "System Settings > Privacy & Security > Photos.",
        ])
        exit(7)
    }
}

switch command {
case "auth":
    let status = authorize()
    exit(status == .authorized ? 0 : 7)

case "probe":
    requireAuthorization()
    Probe.run(sampleAlbums: args.contains("--albums"))
    exit(0)

case "fetch":
    requireAuthorization()
    guard let count = Int(option("--count", default: "10")), count > 0 else {
        Out.fail("--count must be a positive integer", code: 64)
    }
    guard let timeout = Double(option("--timeout", default: "300")), timeout > 0 else {
        Out.fail("--timeout must be a positive number of seconds", code: 64)
    }
    Fetch.run(count: count, timeout: timeout, onlyRemote: args.contains("--only-remote"))
    exit(0)

case "scan":
    requireAuthorization()
    guard let limit = Int(option("--limit", default: "0")), limit >= 0 else {
        Out.fail("--limit must be zero or more", code: 64)
    }
    Scan.run(limit: limit, since: nil)
    exit(0)

case "export":
    requireAuthorization()
    guard let exportTimeout = Double(option("--timeout", default: "1800")),
          exportTimeout > 0 else {
        Out.fail("--timeout must be a positive number of seconds", code: 64)
    }
    Export.run(timeout: exportTimeout, allowNetwork: !args.contains("--no-network"))
    exit(0)

case "trash":
    // No authorisation: this moves files on disk and never opens the library.
    Trash.run()

case "delete":
    requireAuthorization()
    Delete.run()

case "delete-roundtrip":
    requireAuthorization()
    DeleteProbe.run()

default:
    FileHandle.standardError.write("unknown command: \(command)\n\n\(usage)\n".data(using: .utf8)!)
    exit(64)
}
