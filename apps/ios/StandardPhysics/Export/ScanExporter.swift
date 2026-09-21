import Foundation
import RoomPlan

enum ArtifactKind: String, Codable, Sendable {
    case roomUSDZ = "room_usdz"
    case roomJSON = "room_json"
    case roomMetadata = "room_metadata"
    case walkthroughMP4 = "walkthrough_mp4"
    case frames
    case poses
    case coverage
    case lidarMesh = "lidar_mesh"
    case photoManifest = "photo_manifest"
}

struct CaptureArtifact: Identifiable, Codable, Sendable {
    let id: String
    let kind: ArtifactKind
    let fileURL: URL
}

struct CapturedScan: Identifiable, Codable {
    let id: UUID
    let directory: URL
    let roomURL: URL
    let duration: TimeInterval
    let artifacts: [CaptureArtifact]
    let name: String?
    var captureNotice: String? = nil

    func renamed(_ name: String) throws -> CapturedScan {
        var uploadArtifacts = artifacts
        let meshURL = directory.appendingPathComponent("lidar-mesh.json")
        if !uploadArtifacts.contains(where: { $0.kind == .lidarMesh }),
           FileManager.default.fileExists(atPath: meshURL.path) {
            var mesh = try LidarMesh.load(from: directory)
            if mesh.floorY == nil {
                let room = try JSONDecoder().decode(CapturedRoom.self,
                    from: Data(contentsOf: directory.appendingPathComponent("room.json")))
                mesh.floorY = room.floors.first?.transform.columns.3.y ?? 0
                try mesh.write(to: directory)
            }
            uploadArtifacts.append(CaptureArtifact(id: "lidar-mesh", kind: .lidarMesh, fileURL: meshURL))
        }
        let updated = CapturedScan(
            id: id,
            directory: directory,
            roomURL: roomURL,
            duration: duration,
            artifacts: uploadArtifacts,
            name: name,
            captureNotice: captureNotice
        )
        try JSONEncoder.standardPhysics.encode(updated).write(
            to: directory.appendingPathComponent("capture.json"),
            options: .atomic
        )
        return updated
    }
}

enum ScanExporter {
    private struct CoverageValue: Codable {
        let observedFraction: Double
        let viewpointCount: Int

        enum CodingKeys: String, CodingKey {
            case observedFraction = "observed_fraction"
            case viewpointCount = "viewpoint_count"
        }
    }

    static func makeCaptureDirectory(id: UUID = UUID()) throws -> URL {
        let root = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Captures", isDirectory: true)
        let directory = root.appendingPathComponent(id.uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    static func export(
        room: CapturedRoom,
        recording: RecordingResult,
        coverage: CoverageSnapshot,
        directory: URL
    ) throws -> CapturedScan {
        guard let captureID = UUID(uuidString: directory.lastPathComponent) else {
            throw ExportError.invalidCaptureDirectory
        }
        let roomURL = directory.appendingPathComponent("room.usdz")
        let roomJSONURL = directory.appendingPathComponent("room.json")
        let metadataURL = directory.appendingPathComponent("room.metadata.json")
        let coverageURL = directory.appendingPathComponent("coverage.json")

        try room.export(
            to: roomURL,
            metadataURL: metadataURL,
            exportOptions: [.parametric, .mesh]
        )
        try JSONEncoder.standardPhysics.encode(room).write(to: roomJSONURL, options: .atomic)
        let coverageByID = Dictionary(uniqueKeysWithValues: coverage.surfaces.map {
            ($0.id.uuidString, CoverageValue(
                observedFraction: $0.observedFraction,
                viewpointCount: $0.viewpointCount
            ))
        })
        try JSONEncoder.standardPhysics.encode(coverageByID).write(to: coverageURL, options: .atomic)

        var artifacts = [
            CaptureArtifact(id: "room-usdz", kind: .roomUSDZ, fileURL: roomURL),
            CaptureArtifact(id: "room-json", kind: .roomJSON, fileURL: roomJSONURL),
            CaptureArtifact(id: "room-metadata", kind: .roomMetadata, fileURL: metadataURL),
            CaptureArtifact(id: "poses", kind: .poses, fileURL: recording.posesURL),
            CaptureArtifact(id: "coverage", kind: .coverage, fileURL: coverageURL)
        ]
        if var mesh = try? LidarMesh.load(from: directory) {
            mesh.floorY = room.floors.first?.transform.columns.3.y ?? 0
            try mesh.write(to: directory)
            artifacts.append(CaptureArtifact(id: "lidar-mesh", kind: .lidarMesh,
                fileURL: directory.appendingPathComponent("lidar-mesh.json")))
        }
        if let videoURL = recording.videoURL {
            artifacts.append(CaptureArtifact(id: "walkthrough", kind: .walkthroughMP4, fileURL: videoURL))
        }
        artifacts.append(contentsOf: recording.frameURLs.compactMap { fileURL in
            FrameIdentity.number(fromFileName: fileURL.lastPathComponent).map { number in
                CaptureArtifact(id: FrameIdentity.artifactID(forNumber: number), kind: .frames, fileURL: fileURL)
            }
        })
        if let manifest = try writePhotoManifest(posesURL: recording.posesURL, directory: directory) {
            artifacts.append(manifest)
        }

        let scan = CapturedScan(
            id: captureID,
            directory: directory,
            roomURL: roomURL,
            duration: recording.duration,
            artifacts: artifacts,
            name: nil,
            captureNotice: recording.captureNotice
                ?? (recording.videoURL == nil ? "Your room is saved. Scan again to add a walkthrough." : nil)
        )
        try JSONEncoder.standardPhysics.encode(scan).write(
            to: directory.appendingPathComponent("capture.json"),
            options: .atomic
        )
        return scan
    }

    enum ExportError: Error { case invalidCaptureDirectory }

    private struct PhotoManifestFrame: Codable {
        let frameID: String
        let sha256: String
        let bytes: Int

        enum CodingKeys: String, CodingKey {
            case frameID = "frame_id"
            case sha256
            case bytes
        }
    }

    private struct PhotoManifest: Codable {
        let manifestVersion: Int
        let posesSHA256: String
        let frames: [PhotoManifestFrame]

        enum CodingKeys: String, CodingKey {
            case manifestVersion = "manifest_version"
            case posesSHA256 = "poses_sha256"
            case frames
        }
    }

    /// Writes photo-manifest.json when poses.json has at least one version-2
    /// record whose JPEG exists, so the server can tell a fully-photographed
    /// scan from an older capture that never recorded photo metadata.
    private static func writePhotoManifest(posesURL: URL, directory: URL) throws -> CaptureArtifact? {
        guard let posesData = try? Data(contentsOf: posesURL),
              let poses = try? JSONDecoder().decode([PoseRecord].self, from: posesData) else { return nil }
        let frames = try photoManifestFrames(for: poses, directory: directory)
        guard !frames.isEmpty else { return nil }

        let manifest = PhotoManifest(
            manifestVersion: 1,
            posesSHA256: try SHA256Digest.hexDigest(of: posesURL),
            frames: frames.sorted { $0.frameID < $1.frameID }
        )
        let manifestURL = directory.appendingPathComponent("photo-manifest.json")
        try JSONEncoder.standardPhysics.encode(manifest).write(to: manifestURL, options: .atomic)
        return CaptureArtifact(id: "photo-manifest", kind: .photoManifest, fileURL: manifestURL)
    }

    private static func photoManifestFrames(for poses: [PoseRecord], directory: URL) throws -> [PhotoManifestFrame] {
        try poses.compactMap { pose in
            guard pose.isVersion2OrLater, let frameID = pose.frameArtifactID else { return nil }
            let fileURL = directory.appendingPathComponent(pose.image)
            guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
            let attributes = try FileManager.default.attributesOfItem(atPath: fileURL.path)
            let bytes = attributes[.size] as? Int ?? 0
            return PhotoManifestFrame(frameID: frameID, sha256: try SHA256Digest.hexDigest(of: fileURL), bytes: bytes)
        }
    }
}

enum CaptureLibrary {
    static func all() -> [CapturedScan] {
        guard let root = try? FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Captures", isDirectory: true),
        let directories = try? FileManager.default.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: .skipsHiddenFiles
        ) else { return [] }

        return directories.compactMap { directory in
            let manifest = directory.appendingPathComponent("capture.json")
            return try? JSONDecoder().decode(CapturedScan.self, from: Data(contentsOf: manifest))
        }.sorted { left, right in
            let leftDate = (try? left.directory.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            let rightDate = (try? right.directory.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            return leftDate > rightDate
        }
    }

    /// Removes a scan's folder from this phone. The room, the walkthrough and
    /// the upload receipt all live inside it, so one call clears the lot.
    static func remove(_ scan: CapturedScan) throws {
        try FileManager.default.removeItem(at: scan.directory)
    }
}
