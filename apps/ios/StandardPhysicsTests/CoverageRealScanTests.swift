import RoomPlan
import XCTest
import simd
@testable import StandardPhysics

/// Replays the two walks recorded on a phone (datasets/phone) through the
/// coverage engine, the way the capture reconciles its final room.
final class CoverageRealScanTests: XCTestCase {
    /// Both walks ran the whole four minutes around the room. A gate neither
    /// of them reaches tells an owner who did everything right to keep going.
    func testBothPhoneWalksHaveEnough() throws {
        for name in ["test1", "ravida"] {
            var engine = CoverageEngine()
            let snapshot = try PhoneWalk(named: name).replay(into: &engine)
            XCTAssertTrue(snapshot.isComplete, "\(name) should have enough")
            XCTAssertEqual(snapshot.instruction, CoverageSnapshot.completeInstruction)
        }
    }

    /// Leaving out every pose near the longest wall has to send the owner
    /// back to it.
    func testAWalkThatSkipsAWallDoesNotHaveEnough() throws {
        let walk = try PhoneWalk(named: "ravida")
        let longest = try XCTUnwrap(walk.surfaces.filter { $0.kind == "wall" }.max { $0.width < $1.width })
        let partial = PhoneWalk(surfaces: walk.surfaces, cameras: walk.cameras.filter { camera in
            let offset = camera.position - longest.center
            return simd_length(SIMD2(offset.x, offset.z)) > 5
        })
        var engine = CoverageEngine()
        let snapshot = partial.replay(into: &engine)

        XCTAssertFalse(try XCTUnwrap(snapshot.surfaces.first { $0.id == longest.id }).isDone)
        XCTAssertFalse(snapshot.isComplete)
    }
}

struct PhoneWalk {
    let surfaces: [SurfaceSnapshot]
    let cameras: [CameraObservation]

    init(surfaces: [SurfaceSnapshot], cameras: [CameraObservation]) {
        self.surfaces = surfaces
        self.cameras = cameras
    }

    init(named name: String) throws {
        let directory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../../../datasets/phone/\(name)")
            .standardizedFileURL
        let room = try JSONDecoder().decode(
            CapturedRoom.self, from: Data(contentsOf: directory.appendingPathComponent("room.json")))
        let poses = try JSONDecoder().decode(
            [PoseRecord].self, from: Data(contentsOf: directory.appendingPathComponent("poses.json")))
        surfaces = RoomCoverage.snapshots(from: room)
        cameras = poses.map(Self.camera)
    }

    func replay(into engine: inout CoverageEngine) -> CoverageSnapshot {
        for camera in cameras { engine.update(surfaces: [], camera: camera) }
        return engine.reconcile(finalSurfaces: surfaces)
    }

    private static func camera(from pose: PoseRecord) -> CameraObservation {
        let t = pose.transform
        let k = pose.intrinsics
        return CameraObservation(
            transform: simd_float4x4(
                SIMD4(t[0], t[1], t[2], t[3]), SIMD4(t[4], t[5], t[6], t[7]),
                SIMD4(t[8], t[9], t[10], t[11]), SIMD4(t[12], t[13], t[14], t[15])),
            intrinsics: simd_float3x3(
                SIMD3(k[0], k[1], k[2]), SIMD3(k[3], k[4], k[5]), SIMD3(k[6], k[7], k[8])),
            imageResolution: SIMD2(Float(pose.imageWidth ?? 1920), Float(pose.imageHeight ?? 1440))
        )
    }
}
