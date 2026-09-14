import Foundation
import Photos
import CoreLocation

/// Read-only survey of the Mac's Photos library.
enum Probe {

    static func run(sampleAlbums: Bool) {
        let all = PHAsset.fetchAssets(with: nil)
        let total = all.count
        Out.emit(["event": "libraryOpened", "assets": total])

        guard total > 0 else {
            Out.emit(["event": "error", "code": "EMPTY_LIBRARY",
                      "message": "PhotoKit reports zero assets. Is iCloud Photos enabled for this library?"])
            exit(5)
            }

        // Coverage counters. The point of this spike is which fields are really
        // populated, not which ones the framework declares.
        var present: [String: Int] = [:]
        var mediaType: [String: Int] = [:]
        var subtypes: [String: Int] = [:]
        var sourceTypes: [String: Int] = [:]
        var utis: [String: Int] = [:]
        var resourceTypes: [String: Int] = [:]
        var totalBytes: Int64 = 0
        var withBytes = 0
        var oldest: Date?
        var newest: Date?

        func mark(_ key: String, _ isPresent: Bool) {
            if isPresent { present[key, default: 0] += 1 }
        }

        let started = Date()
        all.enumerateObjects { asset, index, _ in
            if index % 5000 == 0 && index > 0 {
                Out.emit(["event": "progress", "scanned": index, "of": total,
                          "elapsedSeconds": Int(Date().timeIntervalSince(started))])
            }

            switch asset.mediaType {
            case .image: mediaType["image", default: 0] += 1
            case .video: mediaType["video", default: 0] += 1
            case .audio: mediaType["audio", default: 0] += 1
            default:     mediaType["unknown", default: 0] += 1
            }

            if asset.mediaSubtypes.contains(.photoScreenshot) { subtypes["photoScreenshot", default: 0] += 1 }
            if asset.mediaSubtypes.contains(.photoLive)       { subtypes["photoLive", default: 0] += 1 }
            if asset.mediaSubtypes.contains(.photoPanorama)   { subtypes["photoPanorama", default: 0] += 1 }
            if asset.mediaSubtypes.contains(.photoHDR)        { subtypes["photoHDR", default: 0] += 1 }
            if asset.mediaSubtypes.contains(.photoDepthEffect){ subtypes["photoDepthEffect", default: 0] += 1 }
            if asset.mediaSubtypes.contains(.videoTimelapse)  { subtypes["videoTimelapse", default: 0] += 1 }
            if asset.mediaSubtypes.contains(.videoHighFrameRate) { subtypes["videoHighFrameRate", default: 0] += 1 }
            if asset.mediaSubtypes.contains(.videoStreamed)   { subtypes["videoStreamed", default: 0] += 1 }

            switch asset.sourceType {
            case .typeUserLibrary:  sourceTypes["userLibrary", default: 0] += 1
            case .typeCloudShared:  sourceTypes["cloudShared", default: 0] += 1
            case .typeiTunesSynced: sourceTypes["iTunesSynced", default: 0] += 1
            default:                sourceTypes["other", default: 0] += 1
            }

            mark("localIdentifier", !asset.localIdentifier.isEmpty)
            mark("creationDate", asset.creationDate != nil)
            mark("modificationDate", asset.modificationDate != nil)
            mark("location", asset.location != nil)
            mark("isFavorite", asset.isFavorite)
            mark("burstIdentifier", asset.burstIdentifier != nil)
            mark("representsBurst", asset.representsBurst)
            mark("pixelSize", asset.pixelWidth > 0 && asset.pixelHeight > 0)
            mark("duration", asset.duration > 0)
            mark("isHidden", asset.isHidden)

            if let created = asset.creationDate {
                if oldest == nil || created < oldest! { oldest = created }
                if newest == nil || created > newest! { newest = created }
            }

            // Resources carry the original filename, UTI and byte size, none of
            // which PHAsset itself exposes.
            let resources = PHAssetResource.assetResources(for: asset)
            mark("hasResources", !resources.isEmpty)
            for resource in resources {
                resourceTypes[String(describing: resource.type), default: 0] += 1
                utis[resource.uniformTypeIdentifier, default: 0] += 1
            }
            if let primary = resources.first {
                mark("originalFilename", !primary.originalFilename.isEmpty)
                // fileSize is not public API but is the only way to size an
                // asset without downloading it. Absence is itself a finding.
                if let size = primary.value(forKey: "fileSize") as? Int64, size > 0 {
                    totalBytes += size
                    withBytes += 1
                }
            }
        }

        Out.emit([
            "event": "survey",
            "assets": total,
            "elapsedSeconds": Date().timeIntervalSince(started),
            "mediaType": mediaType,
            "mediaSubtypes": subtypes,
            "sourceTypes": sourceTypes,
            "resourceTypes": resourceTypes,
            "utis": utis,
            "fieldPresence": present,
            "assetsWithByteSize": withBytes,
            "totalBytes": totalBytes,
            "oldest": jsonSafe(oldest),
            "newest": jsonSafe(newest),
        ])

        if sampleAlbums {
            surveyAlbums()
        }
    }

    /// Album membership is one of the things USB could not provide at all.
    static func surveyAlbums() {
        var albums: [[String: Any]] = []
        for (kind, label) in [(PHAssetCollectionType.album, "album"),
                              (PHAssetCollectionType.smartAlbum, "smartAlbum")] {
            let collections = PHAssetCollection.fetchAssetCollections(with: kind, subtype: .any, options: nil)
            collections.enumerateObjects { collection, _, _ in
                let count = PHAsset.fetchAssets(in: collection, options: nil).count
                if count > 0 {
                    albums.append([
                        "kind": label,
                        "title": jsonSafe(collection.localizedTitle),
                        "count": count,
                    ])
                }
            }
        }
        Out.emit(["event": "albums", "count": albums.count, "albums": albums])
    }
}
