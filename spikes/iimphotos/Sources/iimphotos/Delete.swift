import Foundation
import Photos

/// Delete assets from the Photos library, which is what removal from the phone
/// actually is: the library is iCloud-synced, so this reaches the device.
///
/// `docs/SAFETY.md` section 8: removal goes through Apple's API and never by
/// unlinking files, so deleted assets land in Recently Deleted and stay
/// restorable for 30 days. That window is the second copy the safety model
/// counts on while the Mac copy is being released.
///
/// Reads local identifiers from stdin, one per line. Everything here is
/// written so that a caller may mark its ledger only on the strength of the
/// exit code:
///
/// - An identifier the library does not hold is reported and skipped, never
///   guessed at. Deleting "the nearest match" is not a thing this does.
/// - Deletion is confirmed by **re-fetching afterwards**. `performChangesAndWait`
///   returning is a claim that the change was applied; asking the library what
///   it now holds is evidence. That distinction is the whole reason the spike
///   that proved this API checked the same way.
/// - The exit code is non-zero if any requested asset is still present, so a
///   partial deletion can never read as a complete one.
///
/// On macOS this raises a system confirmation dialog, once per batch rather
/// than once per asset. That prompt is a feature and is deliberately not
/// worked around.
enum Delete {
    static func run() {
        var identifiers: [String] = []
        while let line = readLine(strippingNewline: true) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if !trimmed.isEmpty { identifiers.append(trimmed) }
        }

        guard !identifiers.isEmpty else {
            Out.emit(["event": "deleteResult", "requested": 0, "deleted": 0, "missing": 0])
            exit(0)
        }

        let fetched = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
        var found: [String: PHAsset] = [:]
        fetched.enumerateObjects { asset, _, _ in found[asset.localIdentifier] = asset }

        // Report what is not there before touching anything. An asset already
        // gone is not an error, but the caller must not be told it was deleted
        // by this run when it was not.
        var missing: [String] = []
        for identifier in identifiers where found[identifier] == nil {
            missing.append(identifier)
            Out.emit([
                "event": "deleteSkipped",
                "localIdentifier": identifier,
                "reason": "the library does not hold this identifier",
            ])
        }

        let targets = identifiers.compactMap { found[$0] }
        guard !targets.isEmpty else {
            Out.emit([
                "event": "deleteResult",
                "requested": identifiers.count,
                "deleted": 0,
                "missing": missing.count,
            ])
            exit(0)
        }

        do {
            try PHPhotoLibrary.shared().performChangesAndWait {
                PHAssetChangeRequest.deleteAssets(targets as NSArray)
            }
        } catch {
            // A refusal, a cancelled confirmation dialog, or a library that is
            // not writable all land here. Nothing has been deleted.
            Out.emit([
                "event": "error",
                "code": "DELETE_REFUSED",
                "message": "\(error.localizedDescription)",
                "meaning": "Nothing was deleted. The user may have declined the confirmation.",
                "requested": identifiers.count,
            ])
            exit(9)
        }

        // Evidence, not the absence of an exception: ask the library what it
        // still holds. `performChangesAndWait` returning is a claim.
        let after = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
        var survivors: Set<String> = []
        after.enumerateObjects { asset, _, _ in survivors.insert(asset.localIdentifier) }

        var deleted = 0
        for identifier in identifiers where found[identifier] != nil {
            if survivors.contains(identifier) {
                Out.emit([
                    "event": "deleteFailed",
                    "localIdentifier": identifier,
                    "error": "still present after deleteAssets reported success",
                ])
            } else {
                deleted += 1
                Out.emit(["event": "deleted", "localIdentifier": identifier])
            }
        }

        let stillThere = targets.count - deleted
        Out.emit([
            "event": "deleteResult",
            "requested": identifiers.count,
            "deleted": deleted,
            "missing": missing.count,
            "stillPresent": stillThere,
        ])
        exit(stillThere == 0 ? 0 : 9)
    }
}
