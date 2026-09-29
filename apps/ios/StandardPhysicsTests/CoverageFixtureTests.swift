import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

final class CoverageFixtureTests: CoverageEngineTestCase {
    func testBedroomFixtureDecodesAndBuildsFloorAndObjectSnapshots() throws {
        let room = try roomFixture(named: "apple_bedroom3.room")
        let snapshots = RoomCoverage.snapshots(from: room)
        let floor = try XCTUnwrap(room.floors.first)
        let floorSnapshot = try XCTUnwrap(snapshots.first { $0.id == floor.identifier })

        XCTAssertEqual(floor.dimensions, SIMD3<Float>(3.5544705, 3.3611374, 0))
        XCTAssertGreaterThan(floor.transform.columns.2.y, 0.99)
        XCTAssertEqual(floorSnapshot.width, floor.dimensions.x, accuracy: 0.0001)
        XCTAssertEqual(floorSnapshot.height, floor.dimensions.y, accuracy: 0.0001)
        XCTAssertFalse(floorSnapshot.isWall)
        XCTAssertGreaterThan(worldFloorNormal(for: floorSnapshot).y, 0.99)
        XCTAssertTrue(snapshots.contains { $0.kind == "bed" })
        XCTAssertTrue(room.objects.allSatisfy { object in
            guard let snapshot = snapshots.first(where: { $0.id == object.identifier }),
                  case let .box(size) = snapshot.shape else { return false }
            return size == object.dimensions
        })
    }

    func testRealRoomFixturesReconcileOnlyTheirFinalRoomPlanIDs() throws {
        for (name, expectedObjectKind) in [
            ("apple_bedroom3.room", "bed"),
            ("apple_livingroom.room", "storage")
        ] {
            let room = try roomFixture(named: name)
            let snapshots = RoomCoverage.snapshots(from: room)
            var engine = CoverageEngine(gridSize: 1)
            let finalCoverage = RoomCoverage.reconcile(&engine, finalRoom: room)

            XCTAssertEqual(Set(finalCoverage.surfaces.map(\.id)), Set(snapshots.map(\.id)), name)
            XCTAssertEqual(finalCoverage.surfaces.count, snapshots.count, name)
            XCTAssertTrue(finalCoverage.surfaces.allSatisfy { $0.observedFraction == 0 && $0.viewpointCount == 0 }, name)
            XCTAssertEqual(snapshots.filter { $0.kind == "floor" }.count, room.floors.count, name)
            XCTAssertEqual(snapshots.filter { $0.shape.isBox }.count, room.objects.count, name)
            XCTAssertTrue(snapshots.contains { $0.kind == expectedObjectKind }, name)
        }
    }

    /// RoomPlan reported one surface twice under a single identifier and the
    /// engine built a dictionary that traps on duplicates, on the display link,
    /// for every frame. A scan died mid-walk with a Swift runtime trap, and the
    /// owner lost the room they had just walked.
    func testARepeatedSurfaceIdentifierDoesNotEndTheScan() {
        var engine = CoverageEngine(gridSize: 1)
        let repeated = UUID()
        let wall = { (width: Float) in
            SurfaceSnapshot(
                id: repeated,
                width: width,
                height: 2,
                transform: matrix_identity_float4x4,
                confidence: .high,
                isWall: true,
                shape: .plane(
                    width: width,
                    height: 2,
                    localU: SIMD3<Float>(1, 0, 0),
                    localV: SIMD3<Float>(0, 1, 0),
                    localNormal: SIMD3<Float>(0, 0, 1)
                )
            )
        }

        engine.update(
            surfaces: [wall(2), wall(3)],
            camera: downwardCamera(position: SIMD3<Float>(0, 2, 0))
        )

        XCTAssertFalse(engine.snapshot.surfaces.isEmpty)
    }
}
