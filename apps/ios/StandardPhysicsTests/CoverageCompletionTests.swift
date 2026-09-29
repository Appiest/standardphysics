import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

final class CoverageCompletionTests: CoverageEngineTestCase {
    func testAWallIsDoneAtSeventyPercentFromTwoSpotsWithRoomPlanSure() {
        XCTAssertTrue(SurfaceCoverage(id: UUID(), observedFraction: 0.70, viewpointCount: 2, highConfidence: true).isDone)
        XCTAssertFalse(SurfaceCoverage(id: UUID(), observedFraction: 0.69, viewpointCount: 2, highConfidence: true).isDone)
        XCTAssertFalse(SurfaceCoverage(id: UUID(), observedFraction: 1, viewpointCount: 1, highConfidence: true).isDone)
        XCTAssertFalse(SurfaceCoverage(id: UUID(), observedFraction: 1, viewpointCount: 3, highConfidence: false).isDone)
    }

    func testTheFloorIsDoneAtHalfItsArea() {
        XCTAssertTrue(SurfaceCoverage(
            id: UUID(), observedFraction: 0.50, viewpointCount: 2, highConfidence: true, kind: "floor").isDone)
        XCTAssertFalse(SurfaceCoverage(
            id: UUID(), observedFraction: 0.49, viewpointCount: 2, highConfidence: true, kind: "floor").isDone)
    }

    func testFurnitureNeverHoldsUpEnough() {
        let snapshot = CoverageSnapshot(surfaces: [
            SurfaceCoverage(id: UUID(), observedFraction: 1, viewpointCount: 2, highConfidence: true),
            SurfaceCoverage(id: UUID(), observedFraction: 0, viewpointCount: 0, highConfidence: false, kind: "chair"),
        ])

        XCTAssertTrue(snapshot.isComplete)
    }

    func testWallsCountByAreaSoAMissedStubIsEnoughButAMissedWallIsNot() {
        let longWall = SurfaceCoverage(id: UUID(), observedFraction: 1, viewpointCount: 2, highConfidence: true, area: 24)
        let stub = SurfaceCoverage(id: UUID(), observedFraction: 0.01, viewpointCount: 1, highConfidence: true, area: 1.6)
        let missedWall = SurfaceCoverage(id: UUID(), observedFraction: 0.2, viewpointCount: 1, highConfidence: true, area: 24)

        XCTAssertTrue(CoverageSnapshot(surfaces: [longWall, stub]).isComplete)
        XCTAssertFalse(CoverageSnapshot(surfaces: [longWall, stub, missedWall]).isComplete)
    }

    func testAnUnfinishedFloorHoldsUpEnough() {
        let wall = SurfaceCoverage(id: UUID(), observedFraction: 1, viewpointCount: 2, highConfidence: true)
        let floor = SurfaceCoverage(
            id: UUID(), observedFraction: 0.3, viewpointCount: 4, highConfidence: true, kind: "floor", area: 60)

        XCTAssertFalse(CoverageSnapshot(surfaces: [wall, floor]).isComplete)
        XCTAssertFalse(CoverageSnapshot(surfaces: [floor]).isComplete)
    }
    func testSurfaceNeedsTwoSeparatedViews() {
        let surfaceID = UUID(uuidString: "38C99F20-2327-4BB7-A435-AFCBBB1C2C73")!
        var engine = CoverageEngine(gridSize: 2)
        let surface = SurfaceSnapshot(
            id: surfaceID,
            width: 2,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high
        )

        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2))
        )
        XCTAssertFalse(engine.snapshot.surfaces[0].isDone)

        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(1.1, 0, 2))
        )

        let result = engine.snapshot.surfaces[0]
        XCTAssertEqual(result.observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(result.observedSegments, [true, true])
        XCTAssertEqual(result.viewpointCount, 2)
        XCTAssertTrue(result.isDone)
    }
    func testEnoughSaysThatsEverythingWeNeed() {
        var engine = CoverageEngine(gridSize: 1)
        let surface = SurfaceSnapshot(
            id: UUID(),
            width: 1,
            height: 1,
            transform: matrix_identity_float4x4,
            confidence: .high
        )

        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2))
        )
        XCTAssertNotEqual(engine.snapshot.instruction, CoverageSnapshot.completeInstruction)
        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(1.1, 0, 2))
        )

        let coverage = engine.snapshot
        XCTAssertTrue(coverage.isComplete)
        XCTAssertEqual(coverage.instruction, "That\u{2019}s everything we need.")

        let reconciled = engine.reconcile(finalSurfaces: [surface])
        XCTAssertTrue(reconciled.isComplete)
        XCTAssertEqual(reconciled.instruction, CoverageSnapshot.completeInstruction)
    }
}
