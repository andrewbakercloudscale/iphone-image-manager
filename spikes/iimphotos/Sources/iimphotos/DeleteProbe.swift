import Foundation
import Photos
import AppKit

/// Answers "is deletion permitted" without deleting a photograph.
///
/// It creates a 1x1 PNG of its own, confirms it landed in the library, then
/// deletes that same asset and confirms it is gone. No asset the user did not
/// ask for is ever touched, and the four-step removal sequence in
/// docs/SAFETY.md does not apply because nothing of the user's is at risk.
enum DeleteProbe {

    static func run() -> Never {
        let image = NSImage(size: NSSize(width: 1, height: 1))
        image.lockFocus()
        NSColor.black.setFill()
        NSRect(x: 0, y: 0, width: 1, height: 1).fill()
        image.unlockFocus()

        guard let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else {
            Out.fail("could not build the test image")
        }

        let temp = FileManager.default.temporaryDirectory
            .appendingPathComponent("iim-delete-probe-\(UUID().uuidString).png")
        do { try png.write(to: temp) } catch { Out.fail("could not write the test image: \(error)") }
        defer { try? FileManager.default.removeItem(at: temp) }

        Out.emit(["event": "deleteProbeStart",
                  "message": "creating a 1x1 test image, then deleting that same image"])

        var createdIdentifier: String?
        do {
            try PHPhotoLibrary.shared().performChangesAndWait {
                let request = PHAssetCreationRequest.forAsset()
                request.addResource(with: .photo, fileURL: temp, options: nil)
                createdIdentifier = request.placeholderForCreatedAsset?.localIdentifier
            }
        } catch {
            Out.emit(["event": "error", "code": "CREATE_FAILED",
                      "message": "\(error.localizedDescription)",
                      "meaning": "Cannot write to the library, so deletion cannot be tested this way."])
            exit(8)
        }

        guard let identifier = createdIdentifier else {
            Out.fail("the library accepted the asset but returned no identifier", code: 8)
        }
        Out.emit(["event": "created", "localIdentifier": identifier])

        let fetched = PHAsset.fetchAssets(withLocalIdentifiers: [identifier], options: nil)
        guard fetched.count == 1 else {
            Out.fail("the created asset could not be fetched back", code: 8)
        }

        do {
            try PHPhotoLibrary.shared().performChangesAndWait {
                PHAssetChangeRequest.deleteAssets(fetched)
            }
        } catch {
            Out.emit(["event": "error", "code": "DELETE_REFUSED",
                      "message": "\(error.localizedDescription)",
                      "meaning": "PHAssetChangeRequest.deleteAssets is not permitted. "
                        + "Removal cannot be built on PhotoKit either.",
                      "leftBehind": identifier])
            exit(9)
        }

        let after = PHAsset.fetchAssets(withLocalIdentifiers: [identifier], options: nil)
        Out.emit([
            "event": "deleteProbeResult",
            "deletionPermitted": after.count == 0,
            "stillPresent": after.count,
            "note": after.count == 0
                ? "PHAssetChangeRequest.deleteAssets works. It moves the asset to Recently Deleted."
                : "deleteAssets reported success but the asset is still fetchable.",
        ])
        exit(after.count == 0 ? 0 : 9)
    }
}
