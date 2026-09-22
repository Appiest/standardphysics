import RoomPlan
import XCTest
@testable import StandardPhysics

final class ScanExportTests: XCTestCase {
    func testRealRoomExportsAndCanBeRecoveredWithoutVideo() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "apple_bedroom3.room", withExtension: "json"))
        let room = try JSONDecoder().decode(CapturedRoom.self, from: Data(contentsOf: source))
        try JSONEncoder().encode(room).write(to: directory.appendingPathComponent("room.recovery.json"))
        let recovered = try CaptureRecovery.recover(from: directory)
        XCTAssertTrue(FileManager.default.fileExists(atPath: recovered.roomURL.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent("capture.json").path))
        XCTAssertFalse(recovered.artifacts.contains { $0.kind == .walkthroughMP4 })
        XCTAssertNotNil(recovered.captureNotice)
        let coverageURL = try XCTUnwrap(recovered.artifacts.first { $0.kind == .coverage }?.fileURL)
        let coverage = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: coverageURL)) as? [String: Any])
        let expectedIdentifiers = Set(RoomCoverage.snapshots(from: room).map(\.id))
        XCTAssertEqual(Set(coverage.keys), Set(expectedIdentifiers.map(\.uuidString)))
        let metadataURL = try XCTUnwrap(recovered.artifacts.first { $0.kind == .roomMetadata }?.fileURL)
        let metadata = try PropertyListSerialization.propertyList(from: Data(contentsOf: metadataURL), format: nil)
        let nodeMap = try XCTUnwrap(metadata as? [String: String])
        let mappedIdentifiers = try Set(nodeMap.values.map { try XCTUnwrap(UUID(uuidString: $0)) })
        XCTAssertEqual(mappedIdentifiers, expectedIdentifiers)
        XCTAssertEqual(nodeMap.count, expectedIdentifiers.count)
        let saved = try JSONDecoder().decode(CapturedScan.self,
            from: Data(contentsOf: directory.appendingPathComponent("capture.json")))
        XCTAssertEqual(saved.id, UUID(uuidString: directory.lastPathComponent))
        let secondRecovery = try CaptureRecovery.recover(from: directory)
        XCTAssertEqual(secondRecovery.id, saved.id)
        XCTAssertEqual(saved.artifacts.count, 5)

        let notice = "Your room is saved. Record another pass to add the missing images."
        let recording = RecordingResult(videoURL: nil, frameURLs: [],
            posesURL: directory.appendingPathComponent("poses.json"), duration: 0, captureNotice: notice)
        let warnedScan = try ScanExporter.export(room: room, recording: recording,
            coverage: CoverageSnapshot(), directory: directory)
        XCTAssertEqual(try warnedScan.renamed("Recovered room").captureNotice, notice)
    }

    func testFrameArtifactIdentifiersComeFromFileNumbersNotArrayPosition() throws {
        let directory = try makeCaptureDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let room = try loadFixtureRoom()

        // frame_0001.jpg is missing, simulating a JPEG write that failed
        // between frame_0000.jpg and frame_0002.jpg.
        let numbers = [0, 2, 3]
        let (posesURL, frameURLs) = try writeKeyframes(numbers, in: directory)

        let recording = RecordingResult(videoURL: nil, frameURLs: frameURLs, posesURL: posesURL, duration: 1.5)
        let scan = try ScanExporter.export(room: room, recording: recording, coverage: CoverageSnapshot(), directory: directory)

        let frameArtifacts = scan.artifacts.filter { $0.kind == .frames }
        XCTAssertEqual(Set(frameArtifacts.map(\.id)), ["frame-0000", "frame-0002", "frame-0003"])
        XCTAssertEqual(frameArtifacts.first { $0.id == "frame-0002" }?.fileURL.lastPathComponent, "frame_0002.jpg")
        XCTAssertEqual(frameArtifacts.first { $0.id == "frame-0003" }?.fileURL.lastPathComponent, "frame_0003.jpg")
    }

    func testRecoveryAssignsFrameArtifactIdentifiersFromFileNumbers() throws {
        let directory = try makeCaptureDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let room = try loadFixtureRoom()
        try JSONEncoder().encode(room).write(to: directory.appendingPathComponent("room.recovery.json"))
        _ = try writeKeyframes([0, 2, 3], in: directory)

        let recovered = try CaptureRecovery.recover(from: directory)

        let frameArtifacts = recovered.artifacts.filter { $0.kind == .frames }
        XCTAssertEqual(Set(frameArtifacts.map(\.id)), ["frame-0000", "frame-0002", "frame-0003"])
    }

    func testPhotoManifestListsVersion2FramesWithChecksums() throws {
        let directory = try makeCaptureDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let room = try loadFixtureRoom()
        let numbers = [0, 2, 3]
        let (posesURL, frameURLs) = try writeKeyframes(numbers, in: directory)

        let recording = RecordingResult(videoURL: nil, frameURLs: frameURLs, posesURL: posesURL, duration: 1.5)
        let scan = try ScanExporter.export(room: room, recording: recording, coverage: CoverageSnapshot(), directory: directory)

        let manifestArtifact = try XCTUnwrap(scan.artifacts.first { $0.kind == .photoManifest })
        XCTAssertEqual(manifestArtifact.id, "photo-manifest")

        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestArtifact.fileURL)) as? [String: Any]
        )
        XCTAssertEqual(manifest["manifest_version"] as? Int, 1)
        XCTAssertEqual(manifest["poses_sha256"] as? String, try SHA256Digest.hexDigest(of: posesURL))

        let frames = try XCTUnwrap(manifest["frames"] as? [[String: Any]])
        XCTAssertEqual(frames.map { $0["frame_id"] as? String }, ["frame-0000", "frame-0002", "frame-0003"])
        for (frame, number) in zip(frames, numbers) {
            let fileURL = directory.appendingPathComponent(FrameIdentity.relativeImagePath(forNumber: number))
            XCTAssertEqual(frame["sha256"] as? String, try SHA256Digest.hexDigest(of: fileURL))
            XCTAssertEqual(frame["bytes"] as? Int, try Data(contentsOf: fileURL).count)
        }
    }

    func testNoManifestIsWrittenWhenPosesHaveNoVersion2Records() throws {
        let directory = try makeCaptureDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let room = try loadFixtureRoom()

        let framesDirectory = directory.appendingPathComponent("frames", isDirectory: true)
        try FileManager.default.createDirectory(at: framesDirectory, withIntermediateDirectories: true)
        let frameURL = framesDirectory.appendingPathComponent("frame_0000.jpg")
        try Data("jpeg-0".utf8).write(to: frameURL)
        let legacyPose = PoseRecord(
            image: "frames/frame_0000.jpg",
            timestamp: 0,
            transform: Array(repeating: 0, count: 16),
            intrinsics: Array(repeating: 0, count: 9),
            orientation: "portrait"
        )
        let posesURL = directory.appendingPathComponent("poses.json")
        try JSONEncoder.standardPhysics.encode([legacyPose]).write(to: posesURL)

        let recording = RecordingResult(videoURL: nil, frameURLs: [frameURL], posesURL: posesURL, duration: 0.5)
        let scan = try ScanExporter.export(room: room, recording: recording, coverage: CoverageSnapshot(), directory: directory)

        XCTAssertFalse(scan.artifacts.contains { $0.kind == .photoManifest })
        XCTAssertEqual(scan.artifacts.filter { $0.kind == .frames }.map(\.id), ["frame-0000"])
    }

    func testTrackingLossSetsATruthfulCaptureNotice() throws {
        let directory = try makeCaptureDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let room = try loadFixtureRoom()
        let (posesURL, frameURLs) = try writeKeyframes([0], in: directory)

        let recording = RecordingResult(
            videoURL: nil, frameURLs: frameURLs, posesURL: posesURL, duration: 0.5,
            captureNotice: nil, trackingInterruptions: 2
        )
        let scan = try ScanExporter.export(room: room, recording: recording, coverage: CoverageSnapshot(), directory: directory)

        XCTAssertEqual(
            scan.captureNotice,
            "Tracking was lost during this scan. Record another pass to fill in what it missed."
        )
    }

    func testCaptureNoticePrecedence() {
        let recording = RecordingResult(
            videoURL: nil, frameURLs: [], posesURL: URL(fileURLWithPath: "/t/poses.json"), duration: 1
        )
        XCTAssertEqual(
            ScanExporter.captureNotice(for: recording),
            "Your room is saved. Scan again to add a walkthrough."
        )

        var withTracking = recording
        withTracking.trackingInterruptions = 3
        XCTAssertEqual(
            ScanExporter.captureNotice(for: withTracking),
            "Tracking was lost during this scan. Record another pass to fill in what it missed."
        )

        var withNotice = withTracking
        withNotice.captureNotice = "Your room is saved. Record another pass to add the missing images."
        XCTAssertEqual(
            ScanExporter.captureNotice(for: withNotice),
            "Your room is saved. Record another pass to add the missing images."
        )
    }

    func testOrphanFrameFilesUploadWithoutInventedPoses() throws {
        let directory = try makeCaptureDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let room = try loadFixtureRoom()
        let (posesURL, frameURLs) = try writeKeyframes([0, 2], in: directory)

        // A JPEG with no pose record: it is on disk but no synchronized
        // camera metadata ever claimed it. The file still uploads under its
        // own identity, but the manifest must not invent a manifest row or a
        // pose for it.
        let orphanURL = directory.appendingPathComponent("frames", isDirectory: true)
            .appendingPathComponent("frame_0005.jpg")
        try Data("jpeg-5".utf8).write(to: orphanURL)

        let recording = RecordingResult(
            videoURL: nil, frameURLs: frameURLs + [orphanURL], posesURL: posesURL, duration: 1.5
        )
        let scan = try ScanExporter.export(room: room, recording: recording, coverage: CoverageSnapshot(), directory: directory)

        let frameArtifacts = scan.artifacts.filter { $0.kind == .frames }
        XCTAssertEqual(Set(frameArtifacts.map(\.id)), ["frame-0000", "frame-0002", "frame-0005"])
        XCTAssertTrue(frameArtifacts.contains { $0.id == "frame-0005" && $0.fileURL == orphanURL })

        let manifestArtifact = try XCTUnwrap(scan.artifacts.first { $0.kind == .photoManifest })
        let manifest = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: manifestArtifact.fileURL)) as? [String: Any]
        )
        let frames = try XCTUnwrap(manifest["frames"] as? [[String: Any]])
        XCTAssertEqual(Set(frames.compactMap { $0["frame_id"] as? String }), ["frame-0000", "frame-0002"])

        let poses = try JSONDecoder().decode([PoseRecord].self, from: Data(contentsOf: posesURL))
        XCTAssertFalse(poses.contains { $0.frameArtifactID == "frame-0005" })
        XCTAssertFalse(poses.contains { $0.image.hasSuffix("frame_0005.jpg") })
    }

    private func loadFixtureRoom() throws -> CapturedRoom {
        let source = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "apple_bedroom3.room", withExtension: "json"))
        return try JSONDecoder().decode(CapturedRoom.self, from: Data(contentsOf: source))
    }

    private func makeCaptureDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    /// Writes a poses.json (version-2 records) and matching JPEG files for
    /// the given frame numbers, standing in for a capture that has gaps
    /// where a JPEG failed to write.
    private func writeKeyframes(_ numbers: [Int], in directory: URL) throws -> (posesURL: URL, frameURLs: [URL]) {
        let framesDirectory = directory.appendingPathComponent("frames", isDirectory: true)
        try FileManager.default.createDirectory(at: framesDirectory, withIntermediateDirectories: true)

        var poses: [PoseRecord] = []
        var frameURLs: [URL] = []
        for number in numbers {
            let fileURL = framesDirectory.appendingPathComponent(FrameIdentity.fileName(forNumber: number))
            try Data("jpeg-\(number)".utf8).write(to: fileURL)
            frameURLs.append(fileURL)
            poses.append(PoseRecord.keyframe(
                frameNumber: number,
                timestamp: TimeInterval(number) * 0.5,
                transform: Array(repeating: 0, count: 16),
                intrinsics: Array(repeating: 0, count: 9),
                orientation: "portrait",
                imageWidth: 1920,
                imageHeight: 1440,
                calibrationWidth: 1920,
                calibrationHeight: 1440
            ))
        }
        let posesURL = directory.appendingPathComponent("poses.json")
        try JSONEncoder.standardPhysics.encode(poses).write(to: posesURL, options: .atomic)
        return (posesURL, frameURLs)
    }
}
