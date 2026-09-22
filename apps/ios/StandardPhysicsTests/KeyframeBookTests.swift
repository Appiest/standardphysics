import XCTest
@testable import StandardPhysics

final class KeyframeBookTests: XCTestCase {
    func testAFailedJPEGWriteNeverRenumbersLaterFrames() {
        var book = KeyframeBook()
        var writtenImages: Set<String> = []

        func sampleKeyframe(writeSucceeds: Bool) {
            let number = book.reserveNextNumber()
            let record = PoseRecord.keyframe(
                frameNumber: number,
                timestamp: TimeInterval(number),
                transform: Array(repeating: 0, count: 16),
                intrinsics: Array(repeating: 0, count: 9),
                orientation: "portrait",
                imageWidth: 1920,
                imageHeight: 1440,
                calibrationWidth: 1920,
                calibrationHeight: 1440
            )
            book.append(record)
            if writeSucceeds { writtenImages.insert(record.image) }
        }

        sampleKeyframe(writeSucceeds: true)   // frame_0000: succeeds
        sampleKeyframe(writeSucceeds: false)  // frame_0001: JPEG write fails
        sampleKeyframe(writeSucceeds: true)   // frame_0002: succeeds
        sampleKeyframe(writeSucceeds: true)   // frame_0003: succeeds

        let saved = book.savedRecords { writtenImages.contains($0) }

        XCTAssertEqual(saved.map(\.image), [
            "frames/frame_0000.jpg",
            "frames/frame_0002.jpg",
            "frames/frame_0003.jpg"
        ])
        XCTAssertEqual(saved.map(\.frameID), ["frame-0000", "frame-0002", "frame-0003"])
        for record in saved {
            XCTAssertEqual(record.frameArtifactID, record.frameID)
        }
    }

    func testReservedNumbersAreMonotonicAndNeverReused() {
        var book = KeyframeBook()
        let numbers = (0..<10).map { _ in book.reserveNextNumber() }
        XCTAssertEqual(numbers, Array(0..<10))
    }
}

final class TrackingLedgerTests: XCTestCase {
    func testSettlingAtTheStartIsNotATrackingLoss() {
        var ledger = TrackingLedger()
        ledger.recordUnusableTracking()
        ledger.recordUnusableTracking()
        XCTAssertEqual(ledger.interruptionCount, 0)
        XCTAssertFalse(ledger.didLoseTracking)
    }

    func testLossAfterUsableTrackingCountsAsOneEpisodeAndRecoveryAllowsAnother() {
        var ledger = TrackingLedger()
        ledger.recordUsableTracking()
        ledger.recordUnusableTracking()
        ledger.recordUnusableTracking() // same episode
        XCTAssertEqual(ledger.interruptionCount, 1)

        ledger.recordUsableTracking()
        ledger.recordUsableTracking()
        ledger.recordUnusableTracking()
        XCTAssertEqual(ledger.interruptionCount, 2)
        XCTAssertTrue(ledger.didLoseTracking)
    }
}
