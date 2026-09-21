import Foundation

/// One sampled camera pose, alongside the JPEG frame it was captured with.
///
/// Version 1 records only carried `image`/`timestamp`/`transform`/`intrinsics`/
/// `orientation`. Version 2 adds the fields below so the server can tie every
/// photo to the exact pose it was taken from without guessing from array
/// position. The new fields decode as optional so old poses.json files still
/// load; `frameArtifactID` falls back to parsing the image file name for them.
struct PoseRecord: Codable, Sendable {
    let image: String
    let timestamp: TimeInterval
    let transform: [Float]
    let intrinsics: [Float]
    let orientation: String
    let metadataVersion: Int?
    let frameID: String?
    let imageWidth: Int?
    let imageHeight: Int?
    let calibrationWidth: Int?
    let calibrationHeight: Int?
    let imageOrientation: String?

    enum CodingKeys: String, CodingKey {
        case image
        case timestamp
        case transform
        case intrinsics
        case orientation
        case metadataVersion = "metadata_version"
        case frameID = "frame_id"
        case imageWidth = "image_width"
        case imageHeight = "image_height"
        case calibrationWidth = "calibration_width"
        case calibrationHeight = "calibration_height"
        case imageOrientation = "image_orientation"
    }

    init(
        image: String,
        timestamp: TimeInterval,
        transform: [Float],
        intrinsics: [Float],
        orientation: String,
        metadataVersion: Int? = nil,
        frameID: String? = nil,
        imageWidth: Int? = nil,
        imageHeight: Int? = nil,
        calibrationWidth: Int? = nil,
        calibrationHeight: Int? = nil,
        imageOrientation: String? = nil
    ) {
        self.image = image
        self.timestamp = timestamp
        self.transform = transform
        self.intrinsics = intrinsics
        self.orientation = orientation
        self.metadataVersion = metadataVersion
        self.frameID = frameID
        self.imageWidth = imageWidth
        self.imageHeight = imageHeight
        self.calibrationWidth = calibrationWidth
        self.calibrationHeight = calibrationHeight
        self.imageOrientation = imageOrientation
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        image = try container.decode(String.self, forKey: .image)
        timestamp = try container.decode(TimeInterval.self, forKey: .timestamp)
        transform = try container.decode([Float].self, forKey: .transform)
        intrinsics = try container.decode([Float].self, forKey: .intrinsics)
        orientation = try container.decode(String.self, forKey: .orientation)
        metadataVersion = try container.decodeIfPresent(Int.self, forKey: .metadataVersion)
        frameID = try container.decodeIfPresent(String.self, forKey: .frameID)
        imageWidth = try container.decodeIfPresent(Int.self, forKey: .imageWidth)
        imageHeight = try container.decodeIfPresent(Int.self, forKey: .imageHeight)
        calibrationWidth = try container.decodeIfPresent(Int.self, forKey: .calibrationWidth)
        calibrationHeight = try container.decodeIfPresent(Int.self, forKey: .calibrationHeight)
        imageOrientation = try container.decodeIfPresent(String.self, forKey: .imageOrientation)
    }
}

extension PoseRecord {
    /// The pixel data is written to disk exactly as the sensor delivered it,
    /// with no rotation applied.
    static let sensorImageOrientation = "sensor"

    /// Builds a version-2 pose record for a keyframe. The image path and the
    /// frame id both come from `FrameIdentity`, so a pose record and its JPEG
    /// always agree on the same frame number.
    static func keyframe(
        frameNumber: Int,
        timestamp: TimeInterval,
        transform: [Float],
        intrinsics: [Float],
        orientation: String,
        imageWidth: Int,
        imageHeight: Int,
        calibrationWidth: Int,
        calibrationHeight: Int
    ) -> PoseRecord {
        PoseRecord(
            image: FrameIdentity.relativeImagePath(forNumber: frameNumber),
            timestamp: timestamp,
            transform: transform,
            intrinsics: intrinsics,
            orientation: orientation,
            metadataVersion: 2,
            frameID: FrameIdentity.artifactID(forNumber: frameNumber),
            imageWidth: imageWidth,
            imageHeight: imageHeight,
            calibrationWidth: calibrationWidth,
            calibrationHeight: calibrationHeight,
            imageOrientation: sensorImageOrientation
        )
    }

    var isVersion2OrLater: Bool {
        (metadataVersion ?? 1) >= 2
    }

    /// The artifact id for this pose's JPEG: its own `frame_id` when present,
    /// otherwise the number parsed back out of the image file name. Version-1
    /// poses never recorded a frame id, so this is the only way to identify
    /// their frame without trusting array position.
    var frameArtifactID: String? {
        frameID ?? FrameIdentity.number(fromImagePath: image).map(FrameIdentity.artifactID(forNumber:))
    }
}

/// The single source of truth for how a keyframe's sequence number maps to
/// its JPEG file name and its upload artifact id. Every place that needs one
/// of these three forms — FrameRecorder when it samples a keyframe,
/// ScanExporter when it assigns artifact ids, and the photo manifest when it
/// lists frames — goes through here instead of re-deriving it.
enum FrameIdentity {
    private static let digits = 4

    static func fileName(forNumber number: Int) -> String {
        String(format: "frame_%0\(digits)d.jpg", number)
    }

    static func relativeImagePath(forNumber number: Int) -> String {
        "frames/\(fileName(forNumber: number))"
    }

    static func artifactID(forNumber number: Int) -> String {
        String(format: "frame-%0\(digits)d", number)
    }

    /// Parses "frame_0005.jpg" back into 5. Returns nil for anything else.
    static func number(fromFileName fileName: String) -> Int? {
        guard fileName.hasSuffix(".jpg") else { return nil }
        let base = fileName.dropLast(".jpg".count)
        guard base.hasPrefix("frame_") else { return nil }
        return Int(base.dropFirst("frame_".count))
    }

    /// Parses a pose record's `image` path, e.g. "frames/frame_0005.jpg".
    static func number(fromImagePath path: String) -> Int? {
        number(fromFileName: (path as NSString).lastPathComponent)
    }
}
