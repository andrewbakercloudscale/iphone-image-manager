import Foundation
import ImageCaptureCore

/// Read-only probe of a tethered iPhone over ImageCaptureCore.
///
/// This deliberately never calls requestDeleteFiles. The delete question is
/// answered by reading the device capability list, not by deleting anything.
final class Probe: NSObject {

    private let browser = ICDeviceBrowser()
    private var camera: ICCameraDevice?

    private let presentation: ICMediaPresentation
    private let metadataSample: Int
    private let deadline: Date

    private var finished = false
    private var exitCode: Int32 = 0
    private var lastCatalogPercent = -1
    private let startedAt = Date()

    init(presentation: ICMediaPresentation, timeout: TimeInterval, metadataSample: Int) {
        self.presentation = presentation
        self.metadataSample = metadataSample
        self.deadline = Date().addingTimeInterval(timeout)
        super.init()
    }

    func run() -> Int32 {
        Out.emit([
            "event": "start",
            "presentationRequested": presentation == .originalAssets ? "original" : "converted",
            "startedAt": Date().iso,
        ])

        browser.delegate = self
        browser.browsedDeviceTypeMask = ICDeviceTypeMask(
            rawValue: ICDeviceTypeMask.camera.rawValue | ICDeviceLocationTypeMask.local.rawValue
        )!
        browser.start()

        while !finished && Date() < deadline {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.25))
        }

        if !finished {
            if camera == nil {
                Out.emit([
                    "event": "error",
                    "code": "NO_DEVICE",
                    "message": "No camera device appeared before the timeout. Is the iPhone plugged in, unlocked and trusted?",
                ])
            } else {
                Out.emit([
                    "event": "error",
                    "code": "TIMEOUT",
                    "message": "Device found but the content catalog did not complete before the timeout.",
                    "lastCatalogPercent": lastCatalogPercent,
                ])
            }
            exitCode = 2
        }

        camera?.requestCloseSession()
        browser.stop()
        return exitCode
    }

    private func finish(_ code: Int32) {
        exitCode = code
        finished = true
    }

    // MARK: - Dumping

    private func dumpDevice(_ camera: ICCameraDevice) {
        var rec: [String: Any] = [
            "event": "device",
            "name": jsonSafe(camera.name),
            "productKind": jsonSafe(camera.productKind),
            "uuid": jsonSafe(camera.uuidString),
            "persistentID": jsonSafe(camera.persistentIDString),
            "serialNumber": jsonSafe(camera.serialNumberString),
            "transport": jsonSafe(camera.transportType),
            "capabilities": camera.capabilities,
            "usbVendorID": camera.usbVendorID,
            "usbProductID": camera.usbProductID,
            "hasOpenSession": camera.hasOpenSession,
            "isLocked": camera.isLocked,
            "isAccessRestrictedAppleDevice": camera.isAccessRestrictedAppleDevice,
            "isEjectable": camera.isEjectable,
            "iCloudPhotosEnabled": camera.iCloudPhotosEnabled,
            "contentCatalogPercentCompleted": camera.contentCatalogPercentCompleted,
            "mountPoint": jsonSafe(camera.mountPoint),
        ]

        // The answers to the two questions that decide the architecture.
        rec["canDeleteOneFile"] = camera.capabilities.contains(ICDeviceCapability.cameraDeviceCanDeleteOneFile.rawValue)
        rec["canDeleteAllFiles"] = camera.capabilities.contains(ICDeviceCapability.cameraDeviceCanDeleteAllFiles.rawValue)
        rec["supportsHEIF"] = camera.capabilities.contains(ICDeviceCapability.cameraDeviceSupportsHEIF.rawValue)
        rec["mediaPresentationInEffect"] =
            camera.mediaPresentation == .originalAssets ? "original" : "converted"

        Out.emit(rec)
    }

    private func dumpAssets(_ camera: ICCameraDevice) {
        let items = camera.mediaFiles ?? []
        var files = 0
        var folders = 0
        var totalBytes: Int64 = 0
        var byUTI: [String: Int] = [:]
        var metadataDumped = 0

        for item in items {
            guard let file = item as? ICCameraFile else {
                folders += 1
                continue
            }
            files += 1
            totalBytes += Int64(file.fileSize)
            let uti = file.uti ?? "unknown"
            byUTI[uti, default: 0] += 1

            var rec: [String: Any] = [
                "event": "asset",
                "name": jsonSafe(file.name),
                "uti": jsonSafe(file.uti),
                "fileSystemPath": jsonSafe(file.fileSystemPath),
                "folder": jsonSafe(file.parentFolder?.name),
                "fileSize": Int64(file.fileSize),
                "width": file.width,
                "height": file.height,
                "duration": file.duration,
                "orientation": file.orientation.rawValue,
                "ptpObjectHandle": file.ptpObjectHandle,

                "originalFilename": jsonSafe(file.originalFilename),
                "createdFilename": jsonSafe(file.createdFilename),

                "creationDate": jsonSafe(file.creationDate),
                "modificationDate": jsonSafe(file.modificationDate),
                "fileCreationDate": jsonSafe(file.fileCreationDate),
                "fileModificationDate": jsonSafe(file.fileModificationDate),
                "exifCreationDate": jsonSafe(file.exifCreationDate),
                "exifModificationDate": jsonSafe(file.exifModificationDate),

                // The fields that decide whether asset grouping is possible at all.
                "originatingAssetID": jsonSafe(file.originatingAssetID),
                "groupUUID": jsonSafe(file.groupUUID),
                "burstUUID": jsonSafe(file.burstUUID),
                "relatedUUID": jsonSafe(file.relatedUUID),
                "fingerprint": jsonSafe(file.fingerprint),
                "gpsString": jsonSafe(file.gpsString),

                "isRaw": file.isRaw,
                "isLocked": file.isLocked,
                "inTemporaryStore": file.isInTemporaryStore,
                "addedAfterCatalogCompleted": file.wasAddedAfterContentCatalogCompleted,
                "firstPicked": file.firstPicked,
                "burstFavorite": file.burstFavorite,
                "burstPicked": file.burstPicked,
                "highFramerate": file.highFramerate,
                "timeLapse": file.timeLapse,

                "pairedRawImage": jsonSafe(file.pairedRawImage?.name),
                "sidecarFiles": (file.sidecarFiles ?? []).map { jsonSafe($0.name) },
            ]

            // metadata triggers a fetch, so sample a handful rather than 40,000.
            if metadataDumped < metadataSample {
                if let md = file.metadataIfAvailable {
                    rec["metadataKeys"] = Array(md.keys).sorted()
                    metadataDumped += 1
                } else {
                    rec["metadataKeys"] = NSNull()
                }
            }

            Out.emit(rec)
        }

        Out.emit([
            "event": "summary",
            "mediaItems": items.count,
            "files": files,
            "nonFileItems": folders,
            "totalBytes": totalBytes,
            "byUTI": byUTI,
            "elapsedSeconds": Date().timeIntervalSince(startedAt),
            "metadataSampled": metadataDumped,
        ])
    }
}

// MARK: - ICDeviceBrowserDelegate

extension Probe: ICDeviceBrowserDelegate {
    func deviceBrowser(_ browser: ICDeviceBrowser, didAdd device: ICDevice, moreComing: Bool) {
        guard let cam = device as? ICCameraDevice else {
            Out.emit(["event": "skippedDevice", "name": jsonSafe(device.name), "type": device.type.rawValue])
            return
        }
        if camera != nil { return }
        camera = cam
        cam.delegate = self
        Out.emit(["event": "deviceFound", "name": jsonSafe(cam.name)])
        cam.requestOpenSession()
    }

    func deviceBrowser(_ browser: ICDeviceBrowser, didRemove device: ICDevice, moreGoing: Bool) {
        Out.emit(["event": "deviceRemoved", "name": jsonSafe(device.name)])
        if device === camera {
            Out.emit(["event": "error", "code": "DISCONNECTED",
                      "message": "The device was disconnected during the probe."])
            finish(3)
        }
    }
}

// MARK: - ICDeviceDelegate

extension Probe: ICDeviceDelegate {
    func didRemove(_ device: ICDevice) {
        Out.emit(["event": "didRemove", "name": jsonSafe(device.name)])
    }

    func device(_ device: ICDevice, didOpenSessionWithError error: Error?) {
        if let error {
            Out.emit(["event": "error", "code": "OPEN_SESSION_FAILED",
                      "message": error.localizedDescription])
            finish(4)
            return
        }
        guard let cam = device as? ICCameraDevice else { return }
        Out.emit(["event": "sessionOpened"])

        if cam.capabilities.contains(ICDeviceCapability.cameraDeviceSupportsHEIF.rawValue) {
            cam.mediaPresentation = presentation
            Out.emit(["event": "mediaPresentationSet",
                      "requested": presentation == .originalAssets ? "original" : "converted"])
        } else {
            Out.emit(["event": "mediaPresentationUnavailable",
                      "message": "Device does not advertise ICCameraDeviceSupportsHEIF."])
        }
    }

    func device(_ device: ICDevice, didCloseSessionWithError error: Error?) {
        Out.emit(["event": "sessionClosed", "error": jsonSafe(error?.localizedDescription)])
    }

    func deviceDidBecomeReady(_ device: ICDevice) {
        Out.emit(["event": "deviceReady"])
    }

    func device(_ device: ICDevice, didEncounterError error: Error?) {
        Out.emit(["event": "deviceError", "message": jsonSafe(error?.localizedDescription)])
    }
}

// MARK: - ICCameraDeviceDelegate

extension Probe: ICCameraDeviceDelegate {
    func deviceDidBecomeReady(withCompleteContentCatalog device: ICCameraDevice) {
        Out.emit(["event": "catalogComplete",
                  "elapsedSeconds": Date().timeIntervalSince(startedAt)])
        dumpDevice(device)
        dumpAssets(device)
        finish(0)
    }

    func cameraDevice(_ camera: ICCameraDevice, didAdd items: [ICCameraItem]) {
        let pct = camera.contentCatalogPercentCompleted
        if Int(pct) / 10 != lastCatalogPercent / 10 {
            lastCatalogPercent = Int(pct)
            Out.emit(["event": "catalogProgress", "percent": pct])
        }
    }

    func cameraDevice(_ camera: ICCameraDevice, didRemove items: [ICCameraItem]) {}
    func cameraDevice(_ camera: ICCameraDevice, didRenameItems items: [ICCameraItem]) {}
    func cameraDeviceDidChangeCapability(_ camera: ICCameraDevice) {
        Out.emit(["event": "capabilityChanged", "capabilities": camera.capabilities])
    }
    func cameraDevice(_ camera: ICCameraDevice, didReceiveThumbnail thumbnail: CGImage?,
                      for item: ICCameraItem, error: Error?) {}
    func cameraDevice(_ camera: ICCameraDevice, didReceiveMetadata metadata: [AnyHashable: Any]?,
                      for item: ICCameraItem, error: Error?) {}
    func cameraDevice(_ camera: ICCameraDevice, didReceivePTPEvent eventData: Data) {}
    func cameraDeviceDidRemoveAccessRestriction(_ device: ICDevice) {
        Out.emit(["event": "accessRestrictionRemoved"])
    }
    func cameraDeviceDidEnableAccessRestriction(_ device: ICDevice) {
        Out.emit(["event": "accessRestrictionEnabled",
                  "message": "The device locked or trust was withdrawn."])
    }
}
