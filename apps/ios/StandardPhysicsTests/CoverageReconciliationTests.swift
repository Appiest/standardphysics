import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

final class CoverageReconciliationTests: CoverageEngineTestCase {
    func testFinalReconciliationScoresEverySurfaceAgainstTheWholeWalk() {
        let observedID = UUID()
        let settledLateID = UUID()
        let outOfSightID = UUID()
        let observed = standardSurface(id: observedID)
        let settledLate = standardSurface(id: settledLateID)
        var behindTransform = matrix_identity_float4x4
        behindTransform.columns.3 = SIMD4(0, 0, 6, 1)
        let outOfSight = SurfaceSnapshot(id: outOfSightID, width: 1, height: 1, transform: behindTransform, confidence: .high)
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [observed], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2)))
        engine.update(surfaces: [observed], camera: .lookingStraightAhead(position: SIMD3<Float>(1.1, 0, 2)))
        let reconciled = engine.reconcile(finalSurfaces: [observed, settledLate, outOfSight])

        XCTAssertEqual(reconciled.surfaces.map(\.id), [observedID, settledLateID, outOfSightID])
        XCTAssertEqual(reconciled.surfaces[0].observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(reconciled.surfaces[0].viewpointCount, 2)
        XCTAssertEqual(reconciled.surfaces[1].observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(reconciled.surfaces[1].viewpointCount, 2)
        XCTAssertEqual(reconciled.surfaces[2].observedFraction, 0, accuracy: 0.001)
        XCTAssertEqual(reconciled.surfaces[2].viewpointCount, 0)

        let afterDeletion = engine.reconcile(finalSurfaces: [settledLate])
        XCTAssertEqual(afterDeletion.surfaces.map(\.id), [settledLateID])
    }

    func testARefinedWallKeepsTheViewsOfWhereItEndsUp() {
        let id = UUID()
        var firstGuessTransform = matrix_identity_float4x4
        firstGuessTransform.columns.3 = SIMD4(3, 0, 0, 1)
        let firstGuess = SurfaceSnapshot(id: id, width: 1, height: 1, transform: firstGuessTransform, confidence: .high)
        let refined = standardSurface(id: id)
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [firstGuess], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2)))
        engine.update(surfaces: [firstGuess], camera: .lookingStraightAhead(position: SIMD3<Float>(1.1, 0, 2)))
        engine.update(surfaces: [refined], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 9)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(engine.snapshot.surfaces[0].viewpointCount, 2)
    }

    func testFinalReconciliationDoesNotCarryGridCellsAcrossChangedGeometry() {
        let id = UUID()
        let live = standardSurface(id: id)
        var finalTransform = matrix_identity_float4x4
        finalTransform.columns.3 = SIMD4(3, 0, 0, 1)
        let final = SurfaceSnapshot(
            id: id,
            width: 1,
            height: 1,
            transform: finalTransform,
            confidence: .high
        )
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [live], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2)))
        let reconciled = engine.reconcile(finalSurfaces: [final])

        XCTAssertEqual(reconciled.surfaces[0].observedFraction, 0, accuracy: 0.001)
        XCTAssertEqual(reconciled.surfaces[0].viewpointCount, 0)
    }

    func testLiveGeometryChangeReplaysCoverageInsteadOfReusingGridIndices() {
        let id = UUID()
        let live = standardSurface(id: id)
        var movedTransform = matrix_identity_float4x4
        movedTransform.columns.3 = SIMD4(3, 0, 0, 1)
        let moved = SurfaceSnapshot(
            id: id,
            width: 1,
            height: 1,
            transform: movedTransform,
            confidence: .high
        )
        var engine = CoverageEngine(gridSize: 1)
        let camera = CameraObservation.lookingStraightAhead(position: SIMD3<Float>(0, 0, 2))

        engine.update(surfaces: [live], camera: camera)
        engine.update(surfaces: [moved], camera: camera)

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
        XCTAssertEqual(engine.snapshot.surfaces[0].viewpointCount, 0)
    }
}
