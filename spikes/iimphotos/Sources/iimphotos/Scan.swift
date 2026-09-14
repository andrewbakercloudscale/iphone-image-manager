import Foundation
import Photos

/// Dumps every asset in the library as JSON Lines, one object per asset.
///
/// Read-only. Emits only what PhotoKit can answer without downloading anything,
/// so a full scan costs no bandwidth. The source application is deliberately
/// absent here: PhotoKit has no API for it and it is read from Photos.sqlite on
/// the Python side.
enum Scan {

    static func run(limit: Int, since: Date?) {
        let options = PHFetchOptions()
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]
        if let since {
            options.predicate = NSPredicate(format: "modificationDate > %@", since as NSDate)
        }

        let assets = PHAsset.fetchAssets(with: options)
        let total = assets.count
        Out.emit(["event": "scanStart", "assets": total, "limit": limit])

        // Album membership is per-collection, so it is gathered once up front
        // rather than queried per asset, which would be O(assets x albums).
        let albums = albumMembership()

        var emitted = 0
        var bytes: Int64 = 0
        let started = Date()

        assets.enumerateObjects { asset, _, stop in
            if limit > 0 && emitted >= limit { stop.pointee = true; return }
            let record = describe(asset, albums: albums[asset.localIdentifier] ?? [])
            bytes += (record["sizeBytes"] as? Int64) ?? 0
            Out.emit(record)
            emitted += 1
            if emitted % 5000 == 0 {
                Out.emit(["event": "scanProgress", "emitted": emitted, "of": total,
                          "elapsedSeconds": Int(Date().timeIntervalSince(started))])
            }
        }

        Out.emit([
            "event": "scanComplete",
            "emitted": emitted,
            "libraryAssets": total,
            "totalBytes": bytes,
            "elapsedSeconds": (Date().timeIntervalSince(started) * 10).rounded() / 10,
        ])
    }

    private static func albumMembership() -> [String: [String]] {
        var result: [String: [String]] = [:]
        for kind in [PHAssetCollectionType.album, .smartAlbum] {
            let collections = PHAssetCollection.fetchAssetCollections(
                with: kind, subtype: .any, options: nil)
            collections.enumerateObjects { collection, _, _ in
                guard let title = collection.localizedTitle else { return }
                PHAsset.fetchAssets(in: collection, options: nil)
                    .enumerateObjects { asset, _, _ in
                        result[asset.localIdentifier, default: []].append(title)
                    }
            }
        }
        return result
    }

    static func subtypeNames(_ asset: PHAsset) -> [String] {
        var names: [String] = []
        let table: [(PHAssetMediaSubtype, String)] = [
            (.photoScreenshot, "screenshot"),
            (.photoLive, "live"),
            (.photoPanorama, "panorama"),
            (.photoHDR, "hdr"),
            (.photoDepthEffect, "depthEffect"),
            (.videoTimelapse, "timelapse"),
            (.videoHighFrameRate, "highFrameRate"),
            (.videoStreamed, "streamed"),
        ]
        for (flag, name) in table where asset.mediaSubtypes.contains(flag) {
            names.append(name)
        }
        return names
    }

    static func describe(_ asset: PHAsset, albums: [String]) -> [String: Any] {
        let resources = PHAssetResource.assetResources(for: asset)
        let primary = resources.first {
            $0.type == .photo || $0.type == .video || $0.type == .fullSizePhoto
        } ?? resources.first

        var size: Int64 = 0
        var resourceList: [[String: Any]] = []
        for resource in resources {
            let resourceSize = (resource.value(forKey: "fileSize") as? Int64) ?? 0
            resourceList.append([
                "type": resource.type.rawValue,
                "filename": resource.originalFilename,
                "uti": resource.uniformTypeIdentifier,
                "sizeBytes": resourceSize,
            ])
            if resource === primary { size = resourceSize }
        }

        var record: [String: Any] = [:]
        record["event"] = "asset"
        record["localIdentifier"] = asset.localIdentifier
        record["filename"] = jsonSafe(primary?.originalFilename)
        record["uti"] = jsonSafe(primary?.uniformTypeIdentifier)
        record["mediaType"] = asset.mediaType.rawValue
        record["subtypes"] = subtypeNames(asset)
        record["createdAt"] = jsonSafe(asset.creationDate)
        record["modifiedAt"] = jsonSafe(asset.modificationDate)
        record["width"] = asset.pixelWidth
        record["height"] = asset.pixelHeight
        record["durationSeconds"] = asset.duration
        record["sizeBytes"] = size
        record["isFavorite"] = asset.isFavorite
        record["isHidden"] = asset.isHidden
        record["burstIdentifier"] = jsonSafe(asset.burstIdentifier)
        record["representsBurst"] = asset.representsBurst
        record["sourceType"] = asset.sourceType.rawValue
        record["albums"] = albums
        record["resources"] = resourceList
        if let location = asset.location {
            record["latitude"] = location.coordinate.latitude
            record["longitude"] = location.coordinate.longitude
        }
        return record
    }
}
