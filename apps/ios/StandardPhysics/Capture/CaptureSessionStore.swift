import RoomPlan
import SwiftUI

@MainActor
final class CaptureSessionStore: ObservableObject {
    enum Phase: Equatable {
        case preparing, scanning, processing, ready
        case failed(String)
    }

    @Published private(set) var phase: Phase = .preparing
    @Published private(set) var coverage = CoverageSnapshot()
    @Published private(set) var surfaces: [SurfaceSnapshot] = []
    @Published private(set) var instruction = CoverageSnapshot.openingInstruction
    @Published private(set) var capturedScan: CapturedScan?
    @Published private(set) var hasDetailedGeometry = false
    @Published private(set) var paint: [PaintedSample] = []
    /// When the walk will stop on its own, once the owner has been warned.
    @Published private(set) var timeLimit: Date?
    weak var controller: RoomCaptureController?

    private var scanningStartedAt: Date?
    private var announcedWallIDs: Set<UUID> = []
    private var hasAnnouncedCompletion = false
    private let coachesTheOpening = !WalkHistory.hasWalked
    private let uploadPlan: WalkUploadPlan?

    init(uploadPlan: WalkUploadPlan? = nil) {
        self.uploadPlan = uploadPlan
    }

    func attach(_ controller: RoomCaptureController) {
        guard self.controller == nil else { return }
        self.controller = controller
        do {
            try controller.start(in: ScanExporter.makeCaptureDirectory(), uploadPlan: uploadPlan)
            phase = .scanning
            scanningStartedAt = Date()
        } catch {
            didFail("Start a new scan to try again.")
        }
    }

    func finish() {
        guard phase == .scanning else { return }
        phase = .processing
        instruction = "Building your room"
        controller?.finish()
    }

    func cancel() { controller?.cancel() }

    func retrySave() {
        phase = .processing
        controller?.retryExport()
    }

    var canRetrySave: Bool { controller?.canExport == true }

    func didPaint(_ paint: [PaintedSample]) {
        guard phase == .scanning else { return }
        self.paint = paint
    }

    func didUpdate(coverage: CoverageSnapshot, surfaces: [SurfaceSnapshot], instruction coaching: String? = nil) {
        guard phase == .scanning else { return }
        announceProgress(coverage)
        self.coverage = coverage
        self.surfaces = surfaces
        instruction = currentInstruction(coverage: coverage, coaching: coaching)
    }

    func didReachTimeWarning(secondsLeft: TimeInterval) {
        guard phase == .scanning else { return }
        timeLimit = Date().addingTimeInterval(secondsLeft)
        Haptics.warning()
        instruction = currentInstruction(coverage: coverage, coaching: nil)
    }

    /// The countdown beside it carries the seconds, so the words don't.
    static let timeWarningInstruction = "Time is nearly up. Finish this wall, then tap Done."

    /// What the one line at the top says, in the order it matters: finished
    /// beats running out of time, which beats RoomPlan's own hints, which beat
    /// the opening coaching, which beats pointing at the next gap.
    private func currentInstruction(coverage: CoverageSnapshot, coaching: String?) -> String {
        if coverage.isComplete { return CoverageSnapshot.completeInstruction }
        if timeLimit != nil { return Self.timeWarningInstruction }
        if let coaching { return coaching }
        return openingCoaching ?? coverage.instruction
    }

    /// The first 20 seconds of someone's first walk teach it in place.
    private var openingCoaching: String? {
        guard coachesTheOpening, let scanningStartedAt else { return nil }
        return WalkHistory.openingLine(after: Date().timeIntervalSince(scanningStartedAt))
    }

    private func announceProgress(_ coverage: CoverageSnapshot) {
        if coverage.isComplete, !hasAnnouncedCompletion {
            hasAnnouncedCompletion = true
            Haptics.walkComplete()
            return
        }
        let newlyFinished = coverage.finishedWallIDs.subtracting(announcedWallIDs)
        guard !newlyFinished.isEmpty else { return }
        announcedWallIDs.formUnion(newlyFinished)
        if !hasAnnouncedCompletion { Haptics.wallDone() }
    }

    func didRecordDetail() { hasDetailedGeometry = true }

#if DEBUG
    /// A walk in progress with made-up coverage, for `SP_DEBUG_SCREEN`.
    func showForDebugging(coverage: CoverageSnapshot, surfaces: [SurfaceSnapshot], secondsLeft: TimeInterval?) {
        phase = .scanning
        self.coverage = coverage
        self.surfaces = surfaces
        timeLimit = secondsLeft.map { Date().addingTimeInterval($0) }
        instruction = currentInstruction(coverage: coverage, coaching: nil)
    }
#endif

    func didFinish(_ scan: CapturedScan) {
        WalkHistory.hasWalked = true
        capturedScan = scan
        phase = .ready
    }

    func didFail(_ message: String) { phase = .failed(message) }
}

/// Whether this phone has finished a walk before, and what the first one
/// says while the owner finds their feet.
enum WalkHistory {
    private static let key = "SP_HAS_WALKED"

    static var hasWalked: Bool {
        get { UserDefaults.standard.bool(forKey: key) }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }

    static func openingLine(after elapsed: TimeInterval) -> String? {
        switch elapsed {
        case ..<10: CoverageSnapshot.openingInstruction
        case ..<20: "Now walk slowly along that wall, about a stride away."
        default: nil
        }
    }
}

struct RoomCaptureContainer: UIViewControllerRepresentable {
    @ObservedObject var store: CaptureSessionStore

    func makeUIViewController(context: Context) -> RoomCaptureController {
        let controller = RoomCaptureController(store: store)
        controller.loadViewIfNeeded()
        store.attach(controller)
        return controller
    }

    func updateUIViewController(_ controller: RoomCaptureController, context: Context) {}

    static func dismantleUIViewController(_ controller: RoomCaptureController, coordinator: ()) {
        controller.cancel()
    }
}
