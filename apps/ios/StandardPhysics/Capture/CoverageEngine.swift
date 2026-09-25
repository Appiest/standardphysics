import Foundation
import RoomPlan
import simd

enum SurfaceConfidence: String, Codable, Sendable {
    case low
    case medium
    case high
}

private enum CoveragePolicy {
    static let requiredObservedFraction = 0.90
    static let requiredViewpointCount = 3
    static let maximumObservationDistance: Float = 3
    static let maximumObservationAngleRadians = 50 * Float.pi / 180
    static let minimumFacingDot = Float(cos(Double(maximumObservationAngleRadians)))
    static let supportFaceMaximumUpDot: Float = -0.9
    static let floorContactTolerance: Float = 0.05

    static func isComplete(observedFraction: Double, viewpointCount: Int, highConfidence: Bool) -> Bool {
        observedFraction >= requiredObservedFraction &&
            viewpointCount >= requiredViewpointCount &&
            highConfidence
    }

    static func isCloseEnoughToObserve(distance: Float) -> Bool {
        distance <= maximumObservationDistance
    }
}

enum SurfaceShape: Sendable {
    case plane(width: Float, height: Float, localU: SIMD3<Float>, localV: SIMD3<Float>, localNormal: SIMD3<Float>)
    case box(size: SIMD3<Float>)
}

struct SurfaceSnapshot: Identifiable, Sendable {
    let id: UUID
    let width: Float
    let height: Float
    let transform: simd_float4x4
    let confidence: SurfaceConfidence
    let isWall: Bool
    let shape: SurfaceShape
    let kind: String
    let name: String?
    let restsOnFloor: Bool

    init(id: UUID, width: Float, height: Float, transform: simd_float4x4, confidence: SurfaceConfidence, isWall: Bool = true, shape: SurfaceShape? = nil, kind: String? = nil, name: String? = nil, restsOnFloor: Bool = false) {
        self.id = id
        self.width = width
        self.height = height
        self.transform = transform
        self.confidence = confidence
        self.isWall = isWall
        self.shape = shape ?? .plane(width: width, height: height, localU: SIMD3(1, 0, 0), localV: SIMD3(0, 1, 0), localNormal: SIMD3(0, 0, 1))
        self.kind = kind ?? (isWall ? "wall" : "area")
        self.name = name
        self.restsOnFloor = restsOnFloor
    }

    var center: SIMD3<Float> { SIMD3(transform.columns.3.x, transform.columns.3.y, transform.columns.3.z) }
    var friendlyName: String { name ?? kind }
}

struct CameraObservation: Sendable {
    let transform: simd_float4x4
    let intrinsics: simd_float3x3
    let imageResolution: SIMD2<Float>

    var position: SIMD3<Float> { SIMD3(transform.columns.3.x, transform.columns.3.y, transform.columns.3.z) }

    static func lookingStraightAhead(position: SIMD3<Float>) -> CameraObservation {
        var transform = matrix_identity_float4x4
        transform.columns.3 = SIMD4(position, 1)
        return CameraObservation(
            transform: transform,
            intrinsics: simd_float3x3(SIMD3(500, 0, 0), SIMD3(0, 500, 0), SIMD3(500, 500, 1)),
            imageResolution: SIMD2(1_000, 1_000)
        )
    }
}

/// One cell of a surface, in world space, and whether the walk has covered it.
struct PaintedSample: Sendable, Equatable {
    let worldPoint: SIMD3<Float>
    let worldNormal: SIMD3<Float>
    let isObserved: Bool
}

struct SurfaceCoverage: Identifiable, Codable, Equatable, Sendable {
    let id: UUID
    let observedFraction: Double
    let observedSegments: [Bool]
    let viewpointCount: Int
    let highConfidence: Bool

    enum CodingKeys: String, CodingKey {
        case id = "node_id"
        case observedFraction = "observed_fraction"
        case viewpointCount = "viewpoint_count"
    }

    var isDone: Bool {
        CoveragePolicy.isComplete(
            observedFraction: observedFraction,
            viewpointCount: viewpointCount,
            highConfidence: highConfidence
        )
    }

    init(id: UUID, observedFraction: Double, observedSegments: [Bool] = [], viewpointCount: Int, highConfidence: Bool) {
        self.id = id
        self.observedFraction = observedFraction
        self.observedSegments = observedSegments
        self.viewpointCount = viewpointCount
        self.highConfidence = highConfidence
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(UUID.self, forKey: .id)
        observedFraction = try container.decode(Double.self, forKey: .observedFraction)
        observedSegments = []
        viewpointCount = try container.decode(Int.self, forKey: .viewpointCount)
        highConfidence = false
    }
}

struct CoverageSnapshot: Sendable {
    var surfaces: [SurfaceCoverage] = []
    var unfinishedDirection: CoverageAngle = .zero
    var instruction = "Turn around slowly"

    /// Said when every measured surface is covered. Surfaces say nothing about
    /// outlets, a TV or a restroom, which is why the completion line asks for
    /// those evidence photos instead of claiming the shop is fully captured.
    static let completeInstruction =
        "Room surfaces covered. Now add close-ups of low outlets, the TV, and the restroom entrance."

    var isComplete: Bool { !surfaces.isEmpty && surfaces.allSatisfy(\.isDone) }
}

struct CoverageAngle: Equatable, Sendable {
    let radians: Double
    static let zero = CoverageAngle(radians: 0)
}

struct CoverageEngine {
    private struct ObservationState {
        var observedSamples: Set<Int> = []
        var viewpoints: [SIMD3<Float>] = []
        var geometry: GeometryFingerprint?
    }

    private struct GeometryFingerprint: Equatable {
        let transform: [Float]
        let shape: ShapeFingerprint
        let restsOnFloor: Bool
    }

    private enum ShapeFingerprint: Equatable {
        case plane(width: Float, height: Float, u: SIMD3<Float>, v: SIMD3<Float>, normal: SIMD3<Float>)
        case box(size: SIMD3<Float>)
    }

    private struct SurfaceSample {
        let index: Int
        let localPoint: SIMD3<Float>
        let localNormal: SIMD3<Float>
        let weight: Float
        let segment: Int?
    }

    private struct PreparedSurfaceSample {
        let sample: SurfaceSample
        let worldPoint: SIMD3<Float>
        let worldNormal: SIMD3<Float>
    }

    private struct PreparedCamera {
        let position: SIMD3<Float>
        let inverseTransform: simd_float4x4
        let intrinsics: simd_float3x3
        let imageResolution: SIMD2<Float>

        init(_ camera: CameraObservation) {
            position = camera.position
            inverseTransform = simd_inverse(camera.transform)
            intrinsics = camera.intrinsics
            imageResolution = camera.imageResolution
        }
    }

    private struct Guidance {
        let angle: CoverageAngle
        let instruction: String
    }

    private enum GuidanceNeed: Equatable {
        case point
        case moveCloser
        case secondViewpoint
        case steadierView
    }

    private struct GuidanceTarget {
        let surface: SurfaceSnapshot
        let sample: SurfaceSample
        let need: GuidanceNeed
    }

    private let gridSize: Int
    private var observations: [UUID: ObservationState] = [:]
    /// Every camera the capture has fed in, so a surface is always scored
    /// against the whole walk.
    ///
    /// RoomPlan keeps refining a wall while the owner walks, and each
    /// refinement is new geometry. Replaying only the cameras that happened to
    /// see the old geometry threw away every earlier view that did not, so a
    /// wall RoomPlan reconstructed at high confidence from a four minute walk
    /// could come back with nothing observed. The capture feeds one camera
    /// every half second, a few hundred in a whole scan, so all of them are
    /// kept.
    private var cameras: [CameraObservation] = []
    private(set) var snapshot = CoverageSnapshot()

    init(gridSize: Int = 10) {
        precondition(gridSize > 0)
        self.gridSize = gridSize
    }

    mutating func reset() {
        observations.removeAll()
        cameras.removeAll()
        snapshot = CoverageSnapshot()
    }

    mutating func update(surfaces: [SurfaceSnapshot], camera: CameraObservation) {
        cameras.append(camera)
        let preparedCamera = PreparedCamera(camera)
        for surface in surfaces {
            observe(surface: surface, from: camera, preparedCamera: preparedCamera)
        }
        snapshot = makeSnapshot(surfaces: surfaces, camera: camera)
    }

    /// Scores the processed room against the whole walk, including surfaces
    /// RoomPlan only settled on at the end.
    mutating func reconcile(finalSurfaces: [SurfaceSnapshot]) -> CoverageSnapshot {
        observations = Dictionary(
            finalSurfaces.map { surface in
                var replayed = replayedState(on: surface)
                replayed.geometry = geometryFingerprint(for: surface)
                return (surface.id, replayed)
            },
            uniquingKeysWith: { _, newer in newer }
        )
        snapshot = makeSnapshot(surfaces: finalSurfaces, camera: nil)
        return snapshot
    }

    private mutating func observe(
        surface: SurfaceSnapshot,
        from camera: CameraObservation,
        preparedCamera: PreparedCamera
    ) {
        let geometry = geometryFingerprint(for: surface)
        guard observations[surface.id]?.geometry == geometry else {
            var replayed = replayedState(on: surface)
            replayed.geometry = geometry
            observations[surface.id] = replayed
            return
        }
        var state = observations[surface.id, default: ObservationState()]
        let visibleSamples = visibleSamples(on: surface, from: preparedCamera)
        guard !visibleSamples.isEmpty else {
            observations[surface.id] = state
            return
        }
        state.observedSamples.formUnion(visibleSamples.map(\.index))
        if state.viewpoints.allSatisfy({ simd_distance($0, camera.position) >= 1 }) { state.viewpoints.append(camera.position) }
        observations[surface.id] = state
    }

    private func geometryFingerprint(for surface: SurfaceSnapshot) -> GeometryFingerprint {
        let transform = surface.transform
        let values = [
            transform.columns.0.x, transform.columns.0.y, transform.columns.0.z, transform.columns.0.w,
            transform.columns.1.x, transform.columns.1.y, transform.columns.1.z, transform.columns.1.w,
            transform.columns.2.x, transform.columns.2.y, transform.columns.2.z, transform.columns.2.w,
            transform.columns.3.x, transform.columns.3.y, transform.columns.3.z, transform.columns.3.w
        ]
        let shape: ShapeFingerprint
        switch surface.shape {
        case let .plane(width, height, localU, localV, localNormal):
            shape = .plane(width: width, height: height, u: localU, v: localV, normal: localNormal)
        case let .box(size):
            shape = .box(size: size)
        }
        return GeometryFingerprint(transform: values, shape: shape, restsOnFloor: surface.restsOnFloor)
    }

    private func replayedState(on surface: SurfaceSnapshot) -> ObservationState {
        var replayed = ObservationState()
        let surfaceSamples = preparedSamples(on: surface)
        for camera in cameras {
            let visibleSamples = visibleSamples(in: surfaceSamples, from: PreparedCamera(camera))
            guard !visibleSamples.isEmpty else { continue }
            replayed.observedSamples.formUnion(visibleSamples.map(\.index))
            if replayed.viewpoints.allSatisfy({ simd_distance($0, camera.position) >= 1 }) {
                replayed.viewpoints.append(camera.position)
            }
        }
        return replayed
    }

    private func makeSnapshot(surfaces: [SurfaceSnapshot], camera: CameraObservation?) -> CoverageSnapshot {
        var result = CoverageSnapshot()
        result.surfaces = surfaces.map(coverage(for:))
        guard let camera else {
            result.instruction = result.isComplete ? CoverageSnapshot.completeInstruction : "Turn around slowly"
            return result
        }
        let guidance = guidance(for: surfaces, coverage: result.surfaces, camera: camera)
        result.unfinishedDirection = guidance.angle
        result.instruction = guidance.instruction
        return result
    }

    private func coverage(for surface: SurfaceSnapshot) -> SurfaceCoverage {
        let state = observations[surface.id, default: ObservationState()]
        let samples = completionSamples(on: surface)
        let totalWeight = samples.reduce(0) { $0 + $1.weight }
        let observedWeight = samples.filter { state.observedSamples.contains($0.index) }.reduce(0) { $0 + $1.weight }
        let fraction = totalWeight > 0 ? Double(observedWeight / totalWeight) : 0
        return SurfaceCoverage(
            id: surface.id,
            observedFraction: min(1, fraction),
            observedSegments: observedSegments(samples: samples, state: state, shape: surface.shape),
            viewpointCount: state.viewpoints.count,
            highConfidence: surface.confidence == .high
        )
    }

    private func samples(on surface: SurfaceSnapshot) -> [SurfaceSample] {
        switch surface.shape {
        case let .plane(width, height, localU, localV, localNormal):
            return faceSamples(startIndex: 0, center: .zero, width: width, height: height, localU: localU, localV: localV, localNormal: localNormal, area: width * height, segmented: true)
        case let .box(size):
            return boxSamples(size: size)
        }
    }

    private func completionSamples(on surface: SurfaceSnapshot) -> [SurfaceSample] {
        let allSamples = samples(on: surface)
        guard case .box = surface.shape, surface.restsOnFloor else { return allSamples }

        // Only a clearly downward-facing base is normally against the floor and cannot be captured handheld.
        // Every other box face remains part of the completion requirement.
        let normalTransform = simd_transpose(simd_inverse(surface.transform))
        let worldUp = SIMD3<Float>(0, 1, 0)
        return allSamples.filter { sample in
            let transformed = normalTransform * SIMD4(sample.localNormal, 0)
            let worldNormal = simd_normalize(SIMD3(transformed.x, transformed.y, transformed.z))
            return simd_dot(worldNormal, worldUp) > CoveragePolicy.supportFaceMaximumUpDot
        }
    }

    /// Where the paint goes, and whether it is down yet.
    ///
    /// Drawn from the same samples that decide completion, so a wall that looks
    /// painted is a wall the app will accept. A prettier overlay that disagreed
    /// with the rule would leave an owner staring at a finished room the app
    /// says is unfinished.
    func paint(on surfaces: [SurfaceSnapshot]) -> [PaintedSample] {
        surfaces.flatMap { surface -> [PaintedSample] in
            let observed = observations[surface.id]?.observedSamples ?? []
            return preparedSamples(on: surface).map { prepared in
                PaintedSample(
                    worldPoint: prepared.worldPoint,
                    worldNormal: prepared.worldNormal,
                    isObserved: observed.contains(prepared.sample.index)
                )
            }
        }
    }

    private func preparedSamples(on surface: SurfaceSnapshot) -> [PreparedSurfaceSample] {
        let normalTransform = simd_transpose(simd_inverse(surface.transform))
        return samples(on: surface).map { sample in
            let point = surface.transform * SIMD4(sample.localPoint, 1)
            let transformedNormal = normalTransform * SIMD4(sample.localNormal, 0)
            return PreparedSurfaceSample(
                sample: sample,
                worldPoint: SIMD3(point.x, point.y, point.z),
                worldNormal: simd_normalize(SIMD3(transformedNormal.x, transformedNormal.y, transformedNormal.z))
            )
        }
    }

    private func boxSamples(size: SIMD3<Float>) -> [SurfaceSample] {
        let x = max(size.x, 0)
        let y = max(size.y, 0)
        let z = max(size.z, 0)
        let faces = [
            (SIMD3<Float>(1, 0, 0), SIMD3<Float>(0, 1, 0), SIMD3<Float>(0, 0, 1), y, z),
            (SIMD3<Float>(-1, 0, 0), SIMD3<Float>(0, 1, 0), SIMD3<Float>(0, 0, -1), y, z),
            (SIMD3<Float>(0, 1, 0), SIMD3<Float>(1, 0, 0), SIMD3<Float>(0, 0, -1), x, z),
            (SIMD3<Float>(0, -1, 0), SIMD3<Float>(1, 0, 0), SIMD3<Float>(0, 0, 1), x, z),
            (SIMD3<Float>(0, 0, 1), SIMD3<Float>(1, 0, 0), SIMD3<Float>(0, 1, 0), x, y),
            (SIMD3<Float>(0, 0, -1), SIMD3<Float>(-1, 0, 0), SIMD3<Float>(0, 1, 0), x, y)
        ]
        let halfSize = size / 2
        let samplesPerFace = gridSize * gridSize
        return faces.enumerated().flatMap { offset, face in
            faceSamples(startIndex: offset * samplesPerFace, center: face.0 * halfSize, width: face.3, height: face.4, localU: face.1, localV: face.2, localNormal: face.0, area: face.3 * face.4, segmented: false)
        }
    }

    private func faceSamples(startIndex: Int, center: SIMD3<Float>, width: Float, height: Float, localU: SIMD3<Float>, localV: SIMD3<Float>, localNormal: SIMD3<Float>, area: Float, segmented: Bool) -> [SurfaceSample] {
        guard width > 0, height > 0 else { return [] }
        let count = Float(gridSize * gridSize)
        return (0..<gridSize).flatMap { row in
            (0..<gridSize).map { column in
                let u = (Float(column) + 0.5) / Float(gridSize) - 0.5
                let v = (Float(row) + 0.5) / Float(gridSize) - 0.5
                return SurfaceSample(index: startIndex + row * gridSize + column, localPoint: center + localU * (u * width) + localV * (v * height), localNormal: localNormal, weight: area / count, segment: segmented ? column : nil)
            }
        }
    }

    private func observedSegments(samples: [SurfaceSample], state: ObservationState, shape: SurfaceShape) -> [Bool] {
        guard case .plane = shape else { return [] }
        return (0..<gridSize).map { segment in
            let segmentSamples = samples.filter { $0.segment == segment }
            let total = segmentSamples.reduce(0) { $0 + $1.weight }
            let observed = segmentSamples.filter { state.observedSamples.contains($0.index) }.reduce(0) { $0 + $1.weight }
            return total > 0 && observed / total >= Float(CoveragePolicy.requiredObservedFraction)
        }
    }

    private func visibleSamples(on surface: SurfaceSnapshot, from camera: PreparedCamera) -> [SurfaceSample] {
        visibleSamples(in: preparedSamples(on: surface), from: camera)
    }

    private func visibleSamples(
        in samples: [PreparedSurfaceSample],
        from camera: PreparedCamera
    ) -> [SurfaceSample] {
        samples.filter { isVisible($0, from: camera) }.map(\.sample)
    }

    private func isVisible(_ sample: PreparedSurfaceSample, from camera: PreparedCamera) -> Bool {
        let pointToCamera = camera.position - sample.worldPoint
        let distance = simd_length(pointToCamera)
        guard distance > 0, CoveragePolicy.isCloseEnoughToObserve(distance: distance) else { return false }
        guard simd_dot(sample.worldNormal, pointToCamera / distance) > CoveragePolicy.minimumFacingDot else { return false }
        let cameraPoint = camera.inverseTransform * SIMD4(sample.worldPoint, 1)
        guard cameraPoint.z < 0 else { return false }
        let depth = -cameraPoint.z
        let x = camera.intrinsics.columns.0.x * cameraPoint.x / depth + camera.intrinsics.columns.2.x
        let y = camera.intrinsics.columns.1.y * cameraPoint.y / depth + camera.intrinsics.columns.2.y
        return x >= 0 && x <= camera.imageResolution.x && y >= 0 && y <= camera.imageResolution.y
    }

    private func guidance(for surfaces: [SurfaceSnapshot], coverage: [SurfaceCoverage], camera: CameraObservation) -> Guidance {
        // RoomPlan can report the same surface identifier twice in one update,
        // and `uniqueKeysWithValues` traps on that rather than tolerating it.
        // This runs on the display link for every frame of a scan, so the trap
        // took the whole capture down mid-walk. The later reading is the newer
        // one, so it wins.
        let coverageByID = Dictionary(coverage.map { ($0.id, $0) }, uniquingKeysWith: { _, newer in newer })
        let candidates = surfaces.flatMap { surface -> [GuidanceTarget] in
            guard let surfaceCoverage = coverageByID[surface.id], !surfaceCoverage.isDone else { return [] }
            let state = observations[surface.id, default: ObservationState()]
            let remaining = completionSamples(on: surface).filter { !state.observedSamples.contains($0.index) }
            if !remaining.isEmpty {
                return remaining.map { GuidanceTarget(surface: surface, sample: $0, need: .point) }
            }
            let need: GuidanceNeed = surfaceCoverage.viewpointCount < CoveragePolicy.requiredViewpointCount ? .secondViewpoint : .steadierView
            return nearestCenterSample(on: surface).map { [GuidanceTarget(surface: surface, sample: $0, need: need)] } ?? []
        }
        guard var target = candidates.min(by: { distance(to: $0.sample, on: $0.surface, from: camera) < distance(to: $1.sample, on: $1.surface, from: camera) }) else {
            return Guidance(angle: .zero, instruction: CoverageSnapshot.completeInstruction)
        }
        let targetDistance = distance(to: target.sample, on: target.surface, from: camera)
        if target.need == .point, !CoveragePolicy.isCloseEnoughToObserve(distance: targetDistance) {
            target = GuidanceTarget(surface: target.surface, sample: target.sample, need: .moveCloser)
        }
        let worldPoint = worldPoint(for: target.sample, on: target.surface)
        let cameraDirection = simd_inverse(camera.transform) * SIMD4(worldPoint - camera.position, 0)
        let angle = CoverageAngle(radians: Double(atan2(cameraDirection.x, -cameraDirection.z)))
        return Guidance(angle: angle, instruction: instruction(for: target, angle: angle))
    }

    private func instruction(for target: GuidanceTarget, angle: CoverageAngle) -> String {
        let targetName = guidanceTargetName(for: target.surface, angle: angle)
        return switch target.need {
        case .point: "Point the phone at the \(targetName)."
        case .moveCloser: "Move closer to the \(targetName), then point the phone at it."
        case .secondViewpoint: "Walk to a new spot. Point the phone at the \(targetName)."
        case .steadierView: "Hold the phone steady on the \(targetName)."
        }
    }

    private func guidanceTargetName(for surface: SurfaceSnapshot, angle: CoverageAngle) -> String {
        guard surface.isWall, surface.name == nil else { return surface.friendlyName }
        return switch angle.radians {
        case (-.pi / 4)...(.pi / 4): "wall ahead"
        case (.pi / 4)...(3 * .pi / 4): "wall to your right"
        case (-3 * .pi / 4)...(-.pi / 4): "wall to your left"
        default: "wall behind you"
        }
    }

    private func nearestCenterSample(on surface: SurfaceSnapshot) -> SurfaceSample? {
        completionSamples(on: surface).min { simd_length($0.localPoint) < simd_length($1.localPoint) }
    }

    private func distance(to sample: SurfaceSample, on surface: SurfaceSnapshot, from camera: CameraObservation) -> Float {
        simd_distance(worldPoint(for: sample, on: surface), camera.position)
    }

    private func worldPoint(for sample: SurfaceSample, on surface: SurfaceSnapshot) -> SIMD3<Float> {
        let point = surface.transform * SIMD4(sample.localPoint, 1)
        return SIMD3(point.x, point.y, point.z)
    }
}

enum RoomCoverage {
    static func snapshots(from room: CapturedRoom) -> [SurfaceSnapshot] {
        let floorHeights = room.floors.map { $0.transform.columns.3.y }
        return room.walls.map(wallSnapshot) + room.doors.map { planeSnapshot($0, kind: "door") } + room.windows.map { planeSnapshot($0, kind: "window") } + room.openings.map { planeSnapshot($0, kind: "opening") } + room.floors.map(floorSnapshot) + room.objects.map { objectSnapshot($0, floorHeights: floorHeights) }
    }

    static func reconcile(_ engine: inout CoverageEngine, finalRoom: CapturedRoom) -> CoverageSnapshot {
        engine.reconcile(finalSurfaces: snapshots(from: finalRoom))
    }

    private static func wallSnapshot(_ surface: CapturedRoom.Surface) -> SurfaceSnapshot {
        planeSnapshot(surface, kind: "wall", isWall: true)
    }

    private static func planeSnapshot(_ surface: CapturedRoom.Surface, kind: String, isWall: Bool = false) -> SurfaceSnapshot {
        let shape = planeShape(dimensions: surface.dimensions, transform: surface.transform, isFloor: false)
        return SurfaceSnapshot(id: surface.identifier, width: shape.width, height: shape.height, transform: surface.transform, confidence: SurfaceConfidence(surface.confidence), isWall: isWall, shape: shape.surfaceShape, kind: kind)
    }

    private static func floorSnapshot(_ surface: CapturedRoom.Surface) -> SurfaceSnapshot {
        let shape = planeShape(dimensions: surface.dimensions, transform: surface.transform, isFloor: true)
        return SurfaceSnapshot(id: surface.identifier, width: shape.width, height: shape.height, transform: surface.transform, confidence: SurfaceConfidence(surface.confidence), isWall: false, shape: shape.surfaceShape, kind: "floor")
    }

    private static func objectSnapshot(_ object: CapturedRoom.Object, floorHeights: [Float]) -> SurfaceSnapshot {
        SurfaceSnapshot(id: object.identifier, width: object.dimensions.x, height: object.dimensions.y, transform: object.transform, confidence: SurfaceConfidence(object.confidence), isWall: false, shape: .box(size: object.dimensions), kind: String(describing: object.category), restsOnFloor: objectRestsOnFloor(dimensions: object.dimensions, transform: object.transform, floorHeights: floorHeights))
    }

    static func objectRestsOnFloor(dimensions: SIMD3<Float>, transform: simd_float4x4, floorHeights: [Float]) -> Bool {
        guard !floorHeights.isEmpty else { return false }
        let halfDimensions = SIMD3(abs(dimensions.x), abs(dimensions.y), abs(dimensions.z)) / 2
        let bottomY = [-halfDimensions.x, halfDimensions.x].flatMap { x in
            [-halfDimensions.y, halfDimensions.y].flatMap { y in
                [-halfDimensions.z, halfDimensions.z].map { z in
                    (transform * SIMD4(x, y, z, 1)).y
                }
            }
        }.min() ?? transform.columns.3.y
        return floorHeights.contains { abs(bottomY - $0) <= CoveragePolicy.floorContactTolerance }
    }

    static func planeShape(dimensions: SIMD3<Float>, transform: simd_float4x4, isFloor: Bool) -> (width: Float, height: Float, surfaceShape: SurfaceShape) {
        let values = [abs(dimensions.x), abs(dimensions.y), abs(dimensions.z)]
        let normalAxis = values.indices.min { values[$0] < values[$1] } ?? 2
        let faceAxes = values.indices.filter { $0 != normalAxis }
        let localU = axis(faceAxes[0])
        let localV = axis(faceAxes[1])
        var localNormal = axis(normalAxis)
        if isFloor && simd_dot(worldNormal(localNormal, transform: transform), SIMD3<Float>(0, 1, 0)) < 0 {
            localNormal *= -1
        }
        return (
            values[faceAxes[0]],
            values[faceAxes[1]],
            .plane(width: values[faceAxes[0]], height: values[faceAxes[1]], localU: localU, localV: localV, localNormal: localNormal)
        )
    }

    private static func axis(_ index: Int) -> SIMD3<Float> {
        switch index {
        case 0: SIMD3<Float>(1, 0, 0)
        case 1: SIMD3<Float>(0, 1, 0)
        default: SIMD3<Float>(0, 0, 1)
        }
    }

    private static func worldNormal(_ localNormal: SIMD3<Float>, transform: simd_float4x4) -> SIMD3<Float> {
        let transformed = simd_transpose(simd_inverse(transform)) * SIMD4(localNormal, 0)
        return simd_normalize(SIMD3(transformed.x, transformed.y, transformed.z))
    }
}

private extension SurfaceConfidence {
    init(_ confidence: CapturedRoom.Confidence) {
        switch confidence {
        case .low: self = .low
        case .medium: self = .medium
        case .high: self = .high
        @unknown default: self = .low
        }
    }
}
