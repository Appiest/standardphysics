import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

final class CoverageViewRulesTests: CoverageEngineTestCase {
    func testFiveMeterBoundaryCountsButAFartherViewDoesNot() {
        let surface = standardSurface()
        var boundaryEngine = CoverageEngine(gridSize: 1)
        var distantEngine = CoverageEngine(gridSize: 1)

        boundaryEngine.update(surfaces: [surface], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 5)))
        distantEngine.update(surfaces: [surface], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 5.01)))

        XCTAssertEqual(boundaryEngine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(distantEngine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
    }
    func testViewAtSixtyDegreesOrMoreDoesNotCount() {
        let surface = standardSurface()
        var acceptableEngine = CoverageEngine(gridSize: 1)
        var justOverEngine = CoverageEngine(gridSize: 1)
        var steepEngine = CoverageEngine(gridSize: 1)

        acceptableEngine.update(surfaces: [surface], camera: wideCamera(position: SIMD3<Float>(1.72, 0, 1)))
        justOverEngine.update(surfaces: [surface], camera: wideCamera(position: SIMD3<Float>(1.74, 0, 1)))
        steepEngine.update(surfaces: [surface], camera: wideCamera(position: SIMD3<Float>(1.9, 0, 1)))

        XCTAssertEqual(acceptableEngine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(justOverEngine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
        XCTAssertEqual(steepEngine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
    }
    func testAVisibleFacingPointOutsideTheCameraFrustumDoesNotCount() {
        var transform = matrix_identity_float4x4
        transform.columns.3 = SIMD4(3, 0, 0, 1)
        let surface = SurfaceSnapshot(
            id: UUID(),
            width: 1,
            height: 1,
            transform: transform,
            confidence: .high
        )
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [surface], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
    }

    func testViewsCloserThanOneMeterDoNotAddASecondViewpoint() {
        let surface = standardSurface()
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [surface], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2)))
        engine.update(surfaces: [surface], camera: .lookingStraightAhead(position: SIMD3<Float>(0.99, 0, 2)))

        XCTAssertEqual(engine.snapshot.surfaces[0].viewpointCount, 1)
        XCTAssertFalse(engine.snapshot.surfaces[0].isDone)
    }
}
