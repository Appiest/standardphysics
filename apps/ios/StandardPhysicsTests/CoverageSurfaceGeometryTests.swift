import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

final class CoverageSurfaceGeometryTests: CoverageEngineTestCase {
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
}
