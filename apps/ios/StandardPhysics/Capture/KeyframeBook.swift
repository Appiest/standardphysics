import Foundation

/// Pure, ARKit-free bookkeeping for the keyframes sampled during a capture.
///
/// FrameRecorder hands it plain values pulled from each ARFrame; this type
/// owns the one thing that must never go wrong under partial JPEG failures:
/// a keyframe's sequence number is fixed the moment it is sampled and is
/// never reassigned or reused, even when that keyframe's JPEG never reaches
/// disk. Being free of ARKit/UIKit makes it directly unit-testable.
struct KeyframeBook {
    private(set) var poseRecords: [PoseRecord] = []
    private var nextNumber = 0

    /// Reserves the next sequence number for a keyframe about to be sampled.
    mutating func reserveNextNumber() -> Int {
        defer { nextNumber += 1 }
        return nextNumber
    }

    mutating func append(_ record: PoseRecord) {
        poseRecords.append(record)
    }

    /// Pose records limited to the keyframes whose JPEG actually reached
    /// disk. `fileExists` is injected so this stays free of FileManager and
    /// therefore trivial to test with a fake file list.
    func savedRecords(fileExists: (String) -> Bool) -> [PoseRecord] {
        poseRecords.filter { fileExists($0.image) }
    }
}
