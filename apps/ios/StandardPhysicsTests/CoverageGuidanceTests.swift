import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

final class CoverageGuidanceTests: CoverageEngineTestCase {
    func testInstructionNamesTheNearestUnfinishedRegion() {
        var nearbyTransform = matrix_identity_float4x4
        nearbyTransform.columns.3 = SIMD4(0, 0, -2, 1)
        var distantTransform = matrix_identity_float4x4
        distantTransform.columns.3 = SIMD4(0, 0, -4, 1)
        let nearby = SurfaceSnapshot(id: UUID(), width: 1, height: 1, transform: nearbyTransform, confidence: .high, kind: "wall", name: "back wall")
        let distant = SurfaceSnapshot(id: UUID(), width: 1, height: 1, transform: distantTransform, confidence: .high, isWall: false, kind: "counter")
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [distant, nearby], camera: .lookingStraightAhead(position: .zero))

        XCTAssertEqual(engine.snapshot.instruction, "Walk to a new spot. Point the phone at the back wall.")
    }

    func testTheWalkOpensOnOneWallRatherThanTurningOnTheSpot() {
        XCTAssertEqual(CoverageSnapshot().instruction, "Point the phone at the wall in front of you.")
        XCTAssertFalse(CoverageSnapshot().instruction.localizedCaseInsensitiveContains("turn around"))
    }
    func testLowConfidenceSurfaceStaysUnfinished() {
        var engine = CoverageEngine(gridSize: 1)
        let surface = SurfaceSnapshot(
            id: UUID(),
            width: 1,
            height: 1,
            transform: matrix_identity_float4x4,
            confidence: .low
        )

        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2))
        )
        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(1.1, 0, 2))
        )
        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(-1.1, 0, 2))
        )

        XCTAssertFalse(engine.snapshot.surfaces[0].isDone)
    }

    func testFullyObservedWallWithOneViewpointAsksForANewSpot() {
        var engine = CoverageEngine(gridSize: 1)
        let wall = SurfaceSnapshot(
            id: UUID(),
            width: 1,
            height: 1,
            transform: matrix_identity_float4x4,
            confidence: .high,
            kind: "wall"
        )

        engine.update(
            surfaces: [wall],
            camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2))
        )

        let coverage = engine.snapshot.surfaces[0]
        XCTAssertEqual(coverage.observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(coverage.viewpointCount, 1)
        XCTAssertFalse(coverage.isDone)
        XCTAssertEqual(engine.snapshot.instruction, "Walk to a new spot. Point the phone at the wall ahead.")
    }

    func testFullyObservedLowConfidenceWallAsksForASteadierView() {
        var engine = CoverageEngine(gridSize: 1)
        let wall = SurfaceSnapshot(
            id: UUID(),
            width: 1,
            height: 1,
            transform: matrix_identity_float4x4,
            confidence: .low,
            kind: "wall"
        )

        engine.update(
            surfaces: [wall],
            camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2))
        )
        engine.update(
            surfaces: [wall],
            camera: .lookingStraightAhead(position: SIMD3<Float>(1.1, 0, 2))
        )
        engine.update(
            surfaces: [wall],
            camera: .lookingStraightAhead(position: SIMD3<Float>(-1.1, 0, 2))
        )

        let coverage = engine.snapshot.surfaces[0]
        XCTAssertEqual(coverage.observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(coverage.viewpointCount, 3)
        XCTAssertFalse(coverage.isDone)
        XCTAssertEqual(engine.snapshot.instruction, "Hold the phone steady on the wall ahead.")
    }

    func testDistantUnobservedWallUsesMoveCloserGuidance() {
        var transform = matrix_identity_float4x4
        transform.columns.3 = SIMD4(4.5, 0, -3, 1)
        let wall = SurfaceSnapshot(
            id: UUID(),
            width: 1,
            height: 1,
            transform: transform,
            confidence: .high,
            kind: "wall"
        )
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [wall], camera: .lookingStraightAhead(position: .zero))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
        XCTAssertFalse(engine.snapshot.surfaces[0].isDone)
        XCTAssertEqual(engine.snapshot.instruction, "Move closer to the wall to your right, then point the phone at it.")
    }

    func testGuidancePointsToTheNearestUnfinishedCell() {
        var engine = CoverageEngine(gridSize: 2)
        let surface = SurfaceSnapshot(
            id: UUID(),
            width: 4,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high
        )
        var cameraTransform = matrix_identity_float4x4
        cameraTransform.columns.3 = SIMD4(-1, 0, 2, 1)
        let camera = CameraObservation(
            transform: cameraTransform,
            intrinsics: simd_float3x3(
                SIMD3(500, 0, 0),
                SIMD3(0, 500, 0),
                SIMD3(375, 375, 1)
            ),
            imageResolution: SIMD2(750, 750)
        )

        engine.update(surfaces: [surface], camera: camera)

        XCTAssertEqual(engine.snapshot.surfaces[0].observedSegments, [true, false])
        XCTAssertEqual(engine.snapshot.unfinishedDirection.radians, .pi / 4, accuracy: 0.001)
    }

    func testThinObservedBandDoesNotFillTheWholeWallOnTheMap() {
        var engine = CoverageEngine(gridSize: 10)
        let surface = SurfaceSnapshot(
            id: UUID(),
            width: 4,
            height: 4,
            transform: matrix_identity_float4x4,
            confidence: .high
        )
        let camera = CameraObservation(
            transform: translatedCamera(x: 0, z: 2),
            intrinsics: simd_float3x3(
                SIMD3(500, 0, 0),
                SIMD3(0, 500, 0),
                SIMD3(1_000, 50, 1)
            ),
            imageResolution: SIMD2(2_000, 100)
        )

        engine.update(surfaces: [surface], camera: camera)

        XCTAssertEqual(engine.snapshot.surfaces[0].observedSegments, Array(repeating: false, count: 10))
    }

    func testBackSideOfSurfaceDoesNotCountAsObserved() {
        var engine = CoverageEngine(gridSize: 1)
        let surface = SurfaceSnapshot(
            id: UUID(),
            width: 1,
            height: 1,
            transform: matrix_identity_float4x4,
            confidence: .high
        )
        var cameraTransform = matrix_identity_float4x4
        cameraTransform.columns.0.x = -1
        cameraTransform.columns.2.z = -1
        cameraTransform.columns.3.z = -2

        engine.update(
            surfaces: [surface],
            camera: CameraObservation(
                transform: cameraTransform,
                intrinsics: simd_float3x3(
                    SIMD3(500, 0, 0),
                    SIMD3(0, 500, 0),
                    SIMD3(500, 500, 1)
                ),
                imageResolution: SIMD2(1_000, 1_000)
            )
        )

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 0)
    }
}
