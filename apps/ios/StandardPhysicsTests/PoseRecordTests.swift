import XCTest
@testable import StandardPhysics

final class PoseRecordTests: XCTestCase {
    func testVersion1PoseRecordsStillDecode() throws {
        let json = """
        [{
            "image": "frames/frame_0000.jpg",
            "timestamp": 0.0,
            "transform": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
            "intrinsics": [1,0,0, 0,1,0, 0,0,1],
            "orientation": "portrait"
        }]
        """
        let poses = try JSONDecoder().decode([PoseRecord].self, from: Data(json.utf8))
        let pose = try XCTUnwrap(poses.first)

        XCTAssertEqual(pose.image, "frames/frame_0000.jpg")
        XCTAssertEqual(pose.orientation, "portrait")
        XCTAssertNil(pose.metadataVersion)
        XCTAssertNil(pose.frameID)
        XCTAssertNil(pose.imageWidth)
        XCTAssertFalse(pose.isVersion2OrLater)

        // Old records never recorded a frame id, so it must be derived from
        // the image file name rather than trusted as absent metadata.
        XCTAssertEqual(pose.frameArtifactID, "frame-0000")
    }

    func testVersion2PoseRecordRoundTripsThroughEncodingWithSnakeCaseKeys() throws {
        let pose = PoseRecord.keyframe(
            frameNumber: 7,
            timestamp: 3.5,
            transform: Array(repeating: 0, count: 16),
            intrinsics: Array(repeating: 0, count: 9),
            orientation: "landscape_left",
            imageWidth: 1920,
            imageHeight: 1440,
            calibrationWidth: 1920,
            calibrationHeight: 1440
        )

        let data = try JSONEncoder.standardPhysics.encode(pose)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["metadata_version"] as? Int, 2)
        XCTAssertEqual(json["frame_id"] as? String, "frame-0007")
        XCTAssertEqual(json["image_width"] as? Int, 1920)
        XCTAssertEqual(json["image_height"] as? Int, 1440)
        XCTAssertEqual(json["calibration_width"] as? Int, 1920)
        XCTAssertEqual(json["calibration_height"] as? Int, 1440)
        XCTAssertEqual(json["image_orientation"] as? String, "sensor")
        XCTAssertEqual(json["orientation"] as? String, "landscape_left")

        let decoded = try JSONDecoder().decode(PoseRecord.self, from: data)
        XCTAssertEqual(decoded.frameArtifactID, "frame-0007")
        XCTAssertTrue(decoded.isVersion2OrLater)
    }

    func testFrameIdentityParsesAndFormatsFrameNumbers() {
        XCTAssertEqual(FrameIdentity.fileName(forNumber: 5), "frame_0005.jpg")
        XCTAssertEqual(FrameIdentity.relativeImagePath(forNumber: 5), "frames/frame_0005.jpg")
        XCTAssertEqual(FrameIdentity.artifactID(forNumber: 5), "frame-0005")
        XCTAssertEqual(FrameIdentity.number(fromFileName: "frame_0123.jpg"), 123)
        XCTAssertEqual(FrameIdentity.number(fromImagePath: "frames/frame_0123.jpg"), 123)
        XCTAssertNil(FrameIdentity.number(fromFileName: "not-a-frame.jpg"))
    }
}
