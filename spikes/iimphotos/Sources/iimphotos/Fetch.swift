import Foundation
import Photos

/// Measures the one thing the chunking design depends on: whether an original
/// that lives only in iCloud can actually be pulled down on demand, and at what
/// sustained rate. Nothing is written to disk; bytes are counted and discarded.
enum Fetch {

    struct Result {
        var bytes: Int64 = 0
        var seconds: Double = 0
        var error: String?
        var wasLocal: Bool = false
    }

    /// One attempt. `allowNetwork: false` is how we tell local from iCloud-only:
    /// a remote asset fails immediately rather than downloading.
    static func request(_ resource: PHAssetResource, allowNetwork: Bool, timeout: Double) -> Result {
        var result = Result()
        let options = PHAssetResourceRequestOptions()
        options.isNetworkAccessAllowed = allowNetwork

        let semaphore = DispatchSemaphore(value: 0)
        let started = Date()
        var bytes: Int64 = 0
        var failure: Error?

        PHAssetResourceManager.default().requestData(
            for: resource,
            options: options,
            dataReceivedHandler: { data in bytes += Int64(data.count) },
            completionHandler: { error in
                failure = error
                semaphore.signal()
            }
        )

        if semaphore.wait(timeout: .now() + timeout) == .timedOut {
            result.error = "timed out after \(Int(timeout))s"
            result.seconds = timeout
            return result
        }

        result.bytes = bytes
        result.seconds = Date().timeIntervalSince(started)
        result.error = failure.map { "\(($0 as NSError).domain) \(($0 as NSError).code): \($0.localizedDescription)" }
        return result
    }

    static func run(count: Int, timeout: Double, onlyRemote: Bool) {
        let options = PHFetchOptions()
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]
        let all = PHAsset.fetchAssets(with: options)
        guard all.count > 0 else { Out.fail("library is empty", code: 5) }

        Out.emit(["event": "fetchStart", "libraryAssets": all.count,
                  "requested": count, "onlyRemote": onlyRemote])

        var attempted = 0
        var localCount = 0
        var remoteCount = 0
        var downloadedBytes: Int64 = 0
        var downloadSeconds = 0.0
        var failures = 0
        let started = Date()

        all.enumerateObjects { asset, _, stop in
            if attempted >= count { stop.pointee = true; return }

            let resources = PHAssetResource.assetResources(for: asset)
            // The original, not a derivative. Requesting the wrong one is the
            // same transcoding hazard described in docs/SAFETY.md section 2b.
            guard let resource = resources.first(where: {
                $0.type == .photo || $0.type == .video || $0.type == .fullSizePhoto
            }) ?? resources.first else { return }

            let declaredSize = (resource.value(forKey: "fileSize") as? Int64) ?? -1

            // Probe locality with network access denied.
            let localProbe = request(resource, allowNetwork: false, timeout: 20)
            let isLocal = localProbe.error == nil && localProbe.bytes > 0

            if isLocal {
                localCount += 1
                if onlyRemote { return }   // not what we came to measure
            } else {
                remoteCount += 1
            }

            attempted += 1

            let real = isLocal ? localProbe : request(resource, allowNetwork: true, timeout: timeout)
            if real.error != nil || real.bytes == 0 { failures += 1 }
            if !isLocal && real.error == nil {
                downloadedBytes += real.bytes
                downloadSeconds += real.seconds
            }

            let rate: Double = real.seconds > 0
                ? Double(real.bytes) / real.seconds / 1_048_576
                : 0
            let sizeMatches: Any = declaredSize < 0 ? NSNull() : (declaredSize == real.bytes)

            var record: [String: Any] = [:]
            record["event"] = "fetch"
            record["n"] = attempted
            record["filename"] = resource.originalFilename
            record["uti"] = resource.uniformTypeIdentifier
            record["wasLocal"] = isLocal
            record["declaredBytes"] = declaredSize
            record["receivedBytes"] = real.bytes
            record["seconds"] = (real.seconds * 100).rounded() / 100
            record["mbPerSecond"] = (rate * 100).rounded() / 100
            record["sizeMatches"] = sizeMatches
            record["error"] = jsonSafe(real.error)
            Out.emit(record)
        }

        let sustained: Double = downloadSeconds > 0
            ? Double(downloadedBytes) / downloadSeconds / 1_048_576
            : 0
        var summary: [String: Any] = [:]
        summary["event"] = "fetchSummary"
        summary["attempted"] = attempted
        summary["local"] = localCount
        summary["remote"] = remoteCount
        summary["failures"] = failures
        summary["downloadedBytes"] = downloadedBytes
        summary["downloadSeconds"] = (downloadSeconds * 100).rounded() / 100
        summary["sustainedMBPerSecond"] = (sustained * 100).rounded() / 100
        summary["wallClockSeconds"] = (Date().timeIntervalSince(started) * 100).rounded() / 100
        Out.emit(summary)

        if attempted == 0 {
            Out.emit(["event": "error", "code": "NO_SAMPLE",
                      "message": onlyRemote
                        ? "Every asset examined was already local, so on-demand fetch was never exercised."
                        : "No assets could be examined."])
            exit(6)
        }
    }
}
