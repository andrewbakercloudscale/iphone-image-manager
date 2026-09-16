import Foundation

/// Move files to the macOS Trash, never unlink them.
///
/// `docs/SAFETY.md` section 5: the tool never `unlink`s a file containing user
/// media. Anything it removes from the Mac goes to the Trash, restorable from
/// Finder. Python has no binding for that on this machine, and shelling out to
/// Finder needs automation permission and is slow per file, so it lives here
/// beside the other things only macOS can answer.
///
/// Reads paths from stdin, one per line. Exits non-zero if any file could not
/// be trashed, because a caller that deletes its ledger row on the strength of
/// a zero exit must be told when one did not move.
enum Trash {
    static func run() {
        var trashed = 0
        var failed = 0
        var bytes: Int64 = 0

        while let line = readLine(strippingNewline: true) {
            let path = line.trimmingCharacters(in: .whitespaces)
            if path.isEmpty { continue }

            let url = URL(fileURLWithPath: path)
            // Size first: once it is in the Trash the caller cannot ask.
            let size = (try? FileManager.default.attributesOfItem(atPath: path)[.size] as? Int64)
                .flatMap { $0 } ?? 0

            var destination: NSURL?
            do {
                try FileManager.default.trashItem(at: url, resultingItemURL: &destination)
                trashed += 1
                bytes += size
                Out.emit([
                    "event": "trashed",
                    "path": path,
                    "bytes": size,
                    "trashedTo": (destination as URL?)?.path ?? "",
                ])
            } catch {
                failed += 1
                Out.emit([
                    "event": "trashFailed",
                    "path": path,
                    "error": error.localizedDescription,
                ])
            }
        }

        Out.emit([
            "event": "trashComplete",
            "trashed": trashed,
            "failed": failed,
            "bytes": bytes,
        ])
        exit(failed == 0 ? 0 : 1)
    }
}
