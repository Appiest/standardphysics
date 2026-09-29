import XCTest
import RoomPlan
import simd
@testable import StandardPhysics

class CoverageEngineTestCase: XCTestCase {
    func translatedCamera(x: Float, z: Float) -> simd_float4x4 {
        var transform = matrix_identity_float4x4
        transform.columns.3 = SIMD4(x, 0, z, 1)
        return transform
    }

    func standardSurface(id: UUID = UUID()) -> SurfaceSnapshot {
        SurfaceSnapshot(
            id: id,
            width: 1,
            height: 1,
            transform: matrix_identity_float4x4,
            confidence: .high
        )
    }

    func wideCamera(position: SIMD3<Float>) -> CameraObservation {
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

    func downwardCamera(position: SIMD3<Float>) -> CameraObservation {
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

    func roomFixture(named name: String) throws -> CapturedRoom {
        let bundle = Bundle(for: Self.self)
        guard let url = bundle.url(forResource: name, withExtension: "json") else {
            throw FixtureError.missing(name)
        }
        return try JSONDecoder().decode(CapturedRoom.self, from: Data(contentsOf: url))
    }

    func worldFloorNormal(for surface: SurfaceSnapshot) -> SIMD3<Float> {
        guard case let .plane(_, _, _, _, localNormal) = surface.shape else {
            XCTFail("Expected floor plane")
            return .zero
        }
        let normalTransform = simd_transpose(simd_inverse(surface.transform))
        let transformed = normalTransform * SIMD4(localNormal, 0)
        return simd_normalize(SIMD3(transformed.x, transformed.y, transformed.z))
    }

    enum FixtureError: Error {
        case missing(String)
    }
}

extension SurfaceShape {
    var isBox: Bool {
        guard case .box = self else { return false }
        return true
    }
}
