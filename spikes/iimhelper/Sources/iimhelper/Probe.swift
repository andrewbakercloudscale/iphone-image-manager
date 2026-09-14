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

    /// A session that opened cleanly. A locked phone fails the open, and every
    /// number read afterwards is meaningless, so this gates the whole dump.
    private var sessionOpenOK = false
    private var retryAfter: Date?
    private var openAttempts = 0
    private var lockWarned = false
    private var lastHeartbeat = Date.distantPast
    private var dumped = false
    private var lastMediaCount = -1
    private var stableTicks = 0
    private var lastProgressEmit = Date.distantPast
    private var itemsSeen = 0

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

            // A locked iPhone refuses the session. Wait for the user to unlock
            // it rather than reporting an empty library, which is the same
            // silent-failure shape this project exists to avoid.
            if let due = retryAfter, Date() >= due, let cam = camera, !sessionOpenOK {
                retryAfter = Date().addingTimeInterval(3)
                openAttempts += 1
                cam.requestOpenSession()
            }

            heartbeat()
        }

        if !finished {
            if camera != nil && !sessionOpenOK {
                Out.emit([
                    "event": "error",
                    "code": "LOCKED",
                    "message": "The iPhone stayed locked for the whole run. Unlock it and keep it unlocked.",
                    "openAttempts": openAttempts,
                ])
                exitCode = 5
                camera?.requestCloseSession()
                browser.stop()
                return exitCode
            }
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

    /// Polled state, so silence from the framework is still visible as progress
    /// or the lack of it. Delegate callbacks cannot report their own absence.
    private func heartbeat() {
        guard Date().timeIntervalSince(lastHeartbeat) >= 2 else { return }
        lastHeartbeat = Date()
        let elapsed = Date().timeIntervalSince(startedAt)

        guard let cam = camera else {
            Out.emit([
                "event": "heartbeat",
                "state": "waitingForDevice",
                "elapsedSeconds": Int(elapsed),
            ])
            return
        }
        let mediaCount = cam.mediaFiles?.count ?? -1
        let percent = cam.contentCatalogPercentCompleted

        Out.emit([
            "event": "heartbeat",
            "state": sessionOpenOK ? "cataloguing" : "waitingForUnlock",
            "elapsedSeconds": Int(elapsed),
            "isLocked": cam.isLocked,
            "catalogPercent": percent,
            "mediaFiles": mediaCount,
            "contents": cam.contents?.count ?? -1,
            "itemsAdded": itemsSeen,
            "stableTicks": stableTicks,
            "openAttempts": openAttempts,
        ])

        // deviceDidBecomeReadyWithCompleteContentCatalog is NOT reliable: on an
        // iPhone 15 Pro Max running iOS 26.6.2 the catalog reached 100% and the
        // callback never arrived, leaving the probe waiting indefinitely. So
        // decide from polled state instead, once the count has stopped moving.
        guard sessionOpenOK, !dumped, percent >= 100, mediaCount > 0 else {
            lastMediaCount = mediaCount
            return
        }
        if mediaCount == lastMediaCount {
            stableTicks += 1
        } else {
            stableTicks = 0
        }
        lastMediaCount = mediaCount

        if stableTicks >= 3 {
            Out.emit([
                "event": "catalogSettled",
                "message": "Catalog at 100% and the count stopped moving.",
                "elapsedSeconds": Int(elapsed),
                "mediaFiles": mediaCount,
            ])
            completeCatalog(cam)
        }
    }

    /// Dump the device and everything it exposed, then stop.
    private func completeCatalog(_ device: ICCameraDevice) {
        guard !dumped else { return }
        dumped = true
        Out.emit(["event": "catalogComplete",
                  "elapsedSeconds": Date().timeIntervalSince(startedAt)])
        dumpDevice(device)
        let count = dumpAssets(device)

        if count == 0 {
            Out.emit([
                "event": "error",
                "code": "EMPTY_CATALOG",
                "message": "The device exposed zero media files. This almost always means it "
                    + "is locked. Capability flags read in this state are not trustworthy.",
                "isLocked": device.isLocked,
                "capabilities": device.capabilities,
            ])
            finish(5)
            return
        }
        finish(0)
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

    @discardableResult
    private func dumpAssets(_ camera: ICCameraDevice) -> Int {
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
        return files
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
            // Do not give up: a locked phone is the normal case at this point.
            if !lockWarned {
                Out.emit(["event": "waitingForUnlock", "message": error.localizedDescription])
                Out.log("the iPhone is locked. Unlock it and keep it unlocked; still waiting ...")
                lockWarned = true
            }
            retryAfter = Date().addingTimeInterval(3)
            return
        }
        guard let cam = device as? ICCameraDevice else { return }
        sessionOpenOK = true
        retryAfter = nil
        Out.emit(["event": "sessionOpened", "afterAttempts": openAttempts])

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
        // A locked device still fires this, with an empty catalog and a stripped
        // capability list. Reporting that as a result would be a lie.
        guard sessionOpenOK else {
            Out.emit(["event": "catalogIgnored",
                      "message": "Catalog completed but no session was ever opened cleanly."])
            return
        }
        Out.emit(["event": "catalogReadyCallback", "note": "the delegate did fire"])
        completeCatalog(device)
    }

    func cameraDevice(_ camera: ICCameraDevice, didAdd items: [ICCameraItem]) {
        itemsSeen += items.count
        // Throttled: this can fire thousands of times on a large library.
        guard Date().timeIntervalSince(lastProgressEmit) >= 1 else { return }
        lastProgressEmit = Date()
        lastCatalogPercent = Int(camera.contentCatalogPercentCompleted)
        Out.emit([
            "event": "catalogProgress",
            "percent": camera.contentCatalogPercentCompleted,
            "itemsAdded": itemsSeen,
            "elapsedSeconds": Int(Date().timeIntervalSince(startedAt)),
        ])
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
        Out.emit(["event": "unlocked", "message": "Device unlocked, resuming."])
        lockWarned = false
        if !sessionOpenOK {
            retryAfter = Date()  // retry on the next run-loop tick
        }
    }

    func cameraDeviceDidEnableAccessRestriction(_ device: ICDevice) {
        // Do not abort. Drop back to waiting and let the retry loop pick it up
        // when the user unlocks. Losing an hour of cataloguing to a screen
        // timeout is not acceptable behaviour.
        Out.emit([
            "event": "locked",
            "message": "The device locked or trust was withdrawn. Waiting for unlock.",
            "sessionWasOpen": sessionOpenOK,
        ])
        Out.log("the iPhone locked. Unlock it to continue; progress is not lost unless it times out.")
        sessionOpenOK = false
        retryAfter = Date().addingTimeInterval(2)
    }
}
