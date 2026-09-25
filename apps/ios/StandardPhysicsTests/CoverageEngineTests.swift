import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

final class CoverageEngineTests: XCTestCase {
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

    func testFloorPlaneUsesItsExplicitUpNormal() {
        var engine = CoverageEngine(gridSize: 1)
        let floor = SurfaceSnapshot(
            id: UUID(),
            width: 2,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high,
            isWall: false,
            shape: .plane(
                width: 2,
                height: 2,
                localU: SIMD3<Float>(1, 0, 0),
                localV: SIMD3<Float>(0, 0, 1),
                localNormal: SIMD3<Float>(0, 1, 0)
            )
        )

        engine.update(surfaces: [floor], camera: downwardCamera(position: SIMD3<Float>(0, 2, 0)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
    }

    func testFloorFactoryUsesXZWhenYIsTheThinDimension() {
        let shape = RoomCoverage.planeShape(
            dimensions: SIMD3<Float>(4, 0.02, 3),
            transform: matrix_identity_float4x4,
            isFloor: true
        )
        let floor = SurfaceSnapshot(
            id: UUID(),
            width: shape.width,
            height: shape.height,
            transform: matrix_identity_float4x4,
            confidence: .high,
            isWall: false,
            shape: shape.surfaceShape
        )
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [floor], camera: downwardCamera(position: SIMD3<Float>(0, 2, 0)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
    }

    func testFloorFactoryUsesRotatedXYWhenZIsTheThinDimension() {
        let transform = simd_float4x4(
            SIMD4(1, 0, 0, 0),
            SIMD4(0, 0, -1, 0),
            SIMD4(0, 1, 0, 0),
            SIMD4(0, 0, 0, 1)
        )
        let shape = RoomCoverage.planeShape(
            dimensions: SIMD3<Float>(4, 3, 0.02),
            transform: transform,
            isFloor: true
        )
        let floor = SurfaceSnapshot(
            id: UUID(),
            width: shape.width,
            height: shape.height,
            transform: transform,
            confidence: .high,
            isWall: false,
            shape: shape.surfaceShape
        )
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [floor], camera: downwardCamera(position: SIMD3<Float>(0, 2, 0)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
    }

    func testFloorFactoryFlipsARotatedXYNormalTowardWorldUp() {
        let transform = simd_float4x4(
            SIMD4(1, 0, 0, 0),
            SIMD4(0, 0, 1, 0),
            SIMD4(0, -1, 0, 0),
            SIMD4(0, 0, 0, 1)
        )
        let shape = RoomCoverage.planeShape(
            dimensions: SIMD3<Float>(4, 3, 0.02),
            transform: transform,
            isFloor: true
        )
        let floor = SurfaceSnapshot(
            id: UUID(),
            width: shape.width,
            height: shape.height,
            transform: transform,
            confidence: .high,
            isWall: false,
            shape: shape.surfaceShape
        )
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [floor], camera: downwardCamera(position: SIMD3<Float>(0, 2, 0)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
    }

    func testBoxFrontAndTopAreIndependentAreaWeightedFaces() {
        let box = SurfaceSnapshot(
            id: UUID(),
            width: 2,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high,
            isWall: false,
            shape: .box(size: SIMD3<Float>(2, 2, 2)),
            kind: "counter"
        )
        var frontEngine = CoverageEngine(gridSize: 1)
        var topEngine = CoverageEngine(gridSize: 1)

        frontEngine.update(surfaces: [box], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2.9)))
        topEngine.update(surfaces: [box], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2.9)))
        topEngine.update(surfaces: [box], camera: downwardCamera(position: SIMD3<Float>(0, 2.9, 0)))

        XCTAssertEqual(frontEngine.snapshot.surfaces[0].observedFraction, 1.0 / 6.0, accuracy: 0.001)
        XCTAssertEqual(topEngine.snapshot.surfaces[0].observedFraction, 1.0 / 3.0, accuracy: 0.001)
    }

    func testFloorContactBoxExcludesOnlyItsClearlyDownwardBase() {
        let box = SurfaceSnapshot(
            id: UUID(),
            width: 2,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high,
            isWall: false,
            shape: .box(size: SIMD3<Float>(2, 2, 2)),
            kind: "counter",
            restsOnFloor: true
        )
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [box], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2.9)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1.0 / 5.0, accuracy: 0.001)
    }

    func testElevatedBoxKeepsAllSixFacesRequired() {
        var transform = matrix_identity_float4x4
        transform.columns.3.y = 1.06
        let restsOnFloor = RoomCoverage.objectRestsOnFloor(
            dimensions: SIMD3<Float>(2, 2, 2),
            transform: transform,
            floorHeights: [0]
        )
        let box = SurfaceSnapshot(
            id: UUID(),
            width: 2,
            height: 2,
            transform: transform,
            confidence: .high,
            isWall: false,
            shape: .box(size: SIMD3<Float>(2, 2, 2)),
            kind: "shelf",
            restsOnFloor: restsOnFloor
        )
        var engine = CoverageEngine(gridSize: 1)

        XCTAssertFalse(restsOnFloor)
        engine.update(surfaces: [box], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2.9)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1.0 / 6.0, accuracy: 0.001)
    }

    func testBoxWithoutFloorMetadataKeepsAllSixFacesRequired() {
        let box = SurfaceSnapshot(
            id: UUID(),
            width: 2,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high,
            isWall: false,
            shape: .box(size: SIMD3<Float>(2, 2, 2)),
            kind: "shelf"
        )
        var engine = CoverageEngine(gridSize: 1)

        XCTAssertFalse(box.restsOnFloor)
        XCTAssertFalse(RoomCoverage.objectRestsOnFloor(dimensions: SIMD3<Float>(2, 2, 2), transform: matrix_identity_float4x4, floorHeights: []))
        engine.update(surfaces: [box], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 2.9)))

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1.0 / 6.0, accuracy: 0.001)
    }

    func testFloorContactMetadataChangeReplaysBoxCoverage() {
        let id = UUID()
        let elevated = SurfaceSnapshot(
            id: id,
            width: 2,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high,
            isWall: false,
            shape: .box(size: SIMD3<Float>(2, 2, 2)),
            kind: "counter"
        )
        let floorContact = SurfaceSnapshot(
            id: id,
            width: 2,
            height: 2,
            transform: matrix_identity_float4x4,
            confidence: .high,
            isWall: false,
            shape: .box(size: SIMD3<Float>(2, 2, 2)),
            kind: "counter",
            restsOnFloor: true
        )
        let camera = CameraObservation.lookingStraightAhead(position: SIMD3<Float>(0, 0, 2.9))
        var engine = CoverageEngine(gridSize: 1)

        engine.update(surfaces: [elevated], camera: camera)
        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1.0 / 6.0, accuracy: 0.001)

        engine.update(surfaces: [floorContact], camera: camera)

        XCTAssertEqual(engine.snapshot.surfaces[0].observedFraction, 1.0 / 5.0, accuracy: 0.001)
    }

    func testObjectFloorContactUsesRotatedWorldBounds() {
        let angle = Float.pi / 4
        let transform = simd_float4x4(
            SIMD4(cos(angle), sin(angle), 0, 0),
            SIMD4(-sin(angle), cos(angle), 0, 0),
            SIMD4(0, 0, 1, 0),
            SIMD4(0, Float(2).squareRoot(), 0, 1)
        )

        XCTAssertTrue(
            RoomCoverage.objectRestsOnFloor(
                dimensions: SIMD3<Float>(2, 2, 2),
                transform: transform,
                floorHeights: [0]
            )
        )
    }

    func testThreeMeterBoundaryCountsButAFartherViewDoesNot() {
        let surface = standardSurface()
        var boundaryEngine = CoverageEngine(gridSize: 1)
        var distantEngine = CoverageEngine(gridSize: 1)

        boundaryEngine.update(surfaces: [surface], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 3)))
        distantEngine.update(surfaces: [surface], camera: .lookingStraightAhead(position: SIMD3<Float>(0, 0, 3.01)))

        XCTAssertEqual(boundaryEngine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(distantEngine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
    }

    func testViewAtFiftyDegreesOrMoreDoesNotCount() {
        let surface = standardSurface()
        var acceptableEngine = CoverageEngine(gridSize: 1)
        var exactEngine = CoverageEngine(gridSize: 1)
        var steepEngine = CoverageEngine(gridSize: 1)

        acceptableEngine.update(surfaces: [surface], camera: wideCamera(position: SIMD3<Float>(1.18, 0, 1)))
        exactEngine.update(surfaces: [surface], camera: wideCamera(position: SIMD3<Float>(1.2, 0, 1)))
        steepEngine.update(surfaces: [surface], camera: wideCamera(position: SIMD3<Float>(1.3, 0, 1)))

        XCTAssertEqual(acceptableEngine.snapshot.surfaces[0].observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(exactEngine.snapshot.surfaces[0].observedFraction, 0, accuracy: 0.001)
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

    func testLegacySeventyPercentAndTwoViewpointsNeverComplete() {
        XCTAssertFalse(
            SurfaceCoverage(
                id: UUID(),
                observedFraction: 0.70,
                viewpointCount: 2,
                highConfidence: true
            ).isDone
        )
        XCTAssertFalse(
            SurfaceCoverage(
                id: UUID(),
                observedFraction: 0.90,
                viewpointCount: 2,
                highConfidence: true
            ).isDone
        )
        XCTAssertFalse(
            SurfaceCoverage(
                id: UUID(),
                observedFraction: 0.90,
                viewpointCount: 3,
                highConfidence: false
            ).isDone
        )
    }

    func testSurfaceNeedsNinetyPercentAndThreeSeparatedViews() {
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

        var result = engine.snapshot.surfaces[0]
        XCTAssertEqual(result.observedFraction, 1, accuracy: 0.001)
        XCTAssertEqual(result.observedSegments, [true, true])
        XCTAssertEqual(result.viewpointCount, 2)
        XCTAssertFalse(result.isDone)

        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(-1.1, 0, 2))
        )

        result = engine.snapshot.surfaces[0]
        XCTAssertEqual(result.viewpointCount, 3)
        XCTAssertTrue(result.isDone)
    }

    func testCompletedSurfacesAskForEvidenceCloseUpsInsteadOfClaimingDone() {
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
        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(1.1, 0, 2))
        )
        engine.update(
            surfaces: [surface],
            camera: .lookingStraightAhead(position: SIMD3<Float>(-1.1, 0, 2))
        )

        let coverage = engine.snapshot
        XCTAssertTrue(coverage.isComplete)
        XCTAssertEqual(coverage.instruction, CoverageSnapshot.completeInstruction)
        XCTAssertTrue(coverage.instruction.contains("outlets"))
        XCTAssertTrue(coverage.instruction.contains("TV"))
        XCTAssertTrue(coverage.instruction.contains("restroom entrance"))

        let reconciled = engine.reconcile(finalSurfaces: [surface])
        XCTAssertTrue(reconciled.isComplete)
        XCTAssertEqual(reconciled.instruction, CoverageSnapshot.completeInstruction)
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
        transform.columns.3 = SIMD4(3, 0, -2, 1)
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

    private func translatedCamera(x: Float, z: Float) -> simd_float4x4 {
        var transform = matrix_identity_float4x4
        transform.columns.3 = SIMD4(x, 0, z, 1)
        return transform
    }

    private func standardSurface(id: UUID = UUID()) -> SurfaceSnapshot {
        SurfaceSnapshot(
            id: id,
            width: 1,
            height: 1,
            transform: matrix_identity_float4x4,
            confidence: .high
        )
    }

    private func wideCamera(position: SIMD3<Float>) -> CameraObservation {
        let camera = CameraObservation.lookingStraightAhead(position: position)
        return CameraObservation(
            transform: camera.transform,
            intrinsics: simd_float3x3(
                SIMD3(100, 0, 0),
                SIMD3(0, 100, 0),
                SIMD3(500, 500, 1)
            ),
            imageResolution: camera.imageResolution
        )
    }

    private func downwardCamera(position: SIMD3<Float>) -> CameraObservation {
        let transform = simd_float4x4(
            SIMD4(1, 0, 0, 0),
            SIMD4(0, 0, -1, 0),
            SIMD4(0, 1, 0, 0),
            SIMD4(position, 1)
        )
        return CameraObservation(
            transform: transform,
            intrinsics: simd_float3x3(
                SIMD3(500, 0, 0),
                SIMD3(0, 500, 0),
                SIMD3(500, 500, 1)
            ),
            imageResolution: SIMD2(1_000, 1_000)
        )
    }

    private func roomFixture(named name: String) throws -> CapturedRoom {
        let bundle = Bundle(for: Self.self)
        guard let url = bundle.url(forResource: name, withExtension: "json") else {
            throw FixtureError.missing(name)
        }
        return try JSONDecoder().decode(CapturedRoom.self, from: Data(contentsOf: url))
    }

    private func worldFloorNormal(for surface: SurfaceSnapshot) -> SIMD3<Float> {
        guard case let .plane(_, _, _, _, localNormal) = surface.shape else {
            XCTFail("Expected floor plane")
            return .zero
        }
        let normalTransform = simd_transpose(simd_inverse(surface.transform))
        let transformed = normalTransform * SIMD4(localNormal, 0)
        return simd_normalize(SIMD3(transformed.x, transformed.y, transformed.z))
    }

    private enum FixtureError: Error {
        case missing(String)
    }
}

private extension SurfaceShape {
    var isBox: Bool {
        guard case .box = self else { return false }
        return true
    }
}
