import Foundation
import Photos

/// Writes originals to disk, one asset at a time.
///
/// Reads work from stdin as `<localIdentifier>\t<absolute destination path>`,
/// so all path construction stays in Python where it is tested. Streams via
/// `writeData(for:toFile:)` rather than accumulating in memory, because a 4 GB
/// video would otherwise have to fit in RAM.
enum Export {

    static func run(timeout: Double, allowNetwork: Bool) {
        var requested = 0
        var written = 0
        var failed = 0
        var bytes: Int64 = 0
        var seconds = 0.0
        let started = Date()

        Out.emit(["event": "exportStart", "allowNetwork": allowNetwork,
                  "timeoutSeconds": Int(timeout)])

        while let line = readLine(strippingNewline: true) {
            if line.isEmpty { continue }
            let parts = line.components(separatedBy: "\t")
            guard parts.count == 2 else {
                Out.emit(["event": "exportError", "line": line,
                          "message": "expected <localIdentifier>TAB<path>"])
                failed += 1
                continue
            }
            requested += 1
            let result = exportOne(identifier: parts[0], destination: parts[1],
                                   timeout: timeout, allowNetwork: allowNetwork)
            if result.error == nil {
                written += 1
                bytes += result.bytes
                seconds += result.seconds
            } else {
                failed += 1
            }
            Out.emit(result.record)
        }

        let rate = seconds > 0 ? Double(bytes) / seconds / 1_048_576 : 0
        Out.emit([
            "event": "exportComplete",
            "requested": requested,
            "written": written,
            "failed": failed,
            "bytes": bytes,
            "transferSeconds": (seconds * 100).rounded() / 100,
            "mbPerSecond": (rate * 100).rounded() / 100,
            "wallClockSeconds": (Date().timeIntervalSince(started) * 100).rounded() / 100,
        ])
    }

    private struct Outcome {
        var bytes: Int64 = 0
        var seconds: Double = 0
        var error: String?
        var record: [String: Any] = [:]
    }

    private static func exportOne(identifier: String, destination: String,
                                  timeout: Double, allowNetwork: Bool) -> Outcome {
        var outcome = Outcome()
        var record: [String: Any] = ["event": "exported", "localIdentifier": identifier,
                                     "path": destination]

        func fail(_ message: String, code: String) -> Outcome {
            record["error"] = message
            record["code"] = code
            outcome.error = message
            outcome.record = record
            return outcome
        }

        let fetched = PHAsset.fetchAssets(withLocalIdentifiers: [identifier], options: nil)
        guard let asset = fetched.firstObject else {
            return fail("no asset with that local identifier", code: "NOT_FOUND")
        }

        let resources = PHAssetResource.assetResources(for: asset)
        // The original, never a rendition. Requesting the wrong resource is the
        // transcoding hazard in docs/SAFETY.md section 2b.
        guard let resource = resources.first(where: {
            $0.type == .photo || $0.type == .video
        }) ?? resources.first else {
            return fail("asset exposes no resources", code: "NO_RESOURCE")
        }

        record["filename"] = resource.originalFilename
        record["uti"] = resource.uniformTypeIdentifier
        let declared = (resource.value(forKey: "fileSize") as? Int64) ?? -1
        record["declaredBytes"] = declared

        let url = URL(fileURLWithPath: destination)
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            // writeData refuses to overwrite, and a stale partial from a killed
            // run would otherwise fail every retry.
            if FileManager.default.fileExists(atPath: destination) {
                try FileManager.default.removeItem(at: url)
            }
        } catch {
            return fail("cannot prepare destination: \(error.localizedDescription)",
                        code: "DESTINATION")
        }

        let options = PHAssetResourceRequestOptions()
        options.isNetworkAccessAllowed = allowNetwork

        let semaphore = DispatchSemaphore(value: 0)
        var failure: Error?
        let began = Date()
        PHAssetResourceManager.default().writeData(for: resource, toFile: url, options: options) {
            error in
            failure = error
            semaphore.signal()
        }

        if semaphore.wait(timeout: .now() + timeout) == .timedOut {
            try? FileManager.default.removeItem(at: url)
            return fail("timed out after \(Int(timeout))s", code: "TIMEOUT")
        }
        if let failure {
            try? FileManager.default.removeItem(at: url)
            let nsError = failure as NSError
            return fail("\(nsError.domain) \(nsError.code): \(failure.localizedDescription)",
                        code: "TRANSFER")
        }

        let attributes = try? FileManager.default.attributesOfItem(atPath: destination)
        let actual = (attributes?[.size] as? NSNumber)?.int64Value ?? -1
        outcome.bytes = max(actual, 0)
        outcome.seconds = Date().timeIntervalSince(began)

        record["bytes"] = actual
        record["seconds"] = (outcome.seconds * 100).rounded() / 100
        record["mbPerSecond"] = outcome.seconds > 0
            ? (Double(actual) / outcome.seconds / 1_048_576 * 100).rounded() / 100 : 0

        // A short file that reports success is the failure mode that matters:
        // it would be hashed, verified and called a backup.
        if declared >= 0 && actual != declared {
            try? FileManager.default.removeItem(at: url)
            return fail("wrote \(actual) bytes, expected \(declared)", code: "SIZE_MISMATCH")
        }

        outcome.record = record
        return outcome
    }
}
