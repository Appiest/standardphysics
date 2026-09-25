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
    @Published private(set) var instruction = "Turn around slowly"
    @Published private(set) var capturedScan: CapturedScan?
    @Published private(set) var hasDetailedGeometry = false
    @Published private(set) var paint: [PaintedSample] = []
    weak var controller: RoomCaptureController?

    func attach(_ controller: RoomCaptureController) {
        guard self.controller == nil else { return }
        self.controller = controller
        do {
            try controller.start(in: ScanExporter.makeCaptureDirectory())
            phase = .scanning
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

    func didUpdate(coverage: CoverageSnapshot, surfaces: [SurfaceSnapshot], instruction: String? = nil) {
        guard phase == .scanning else { return }
        self.coverage = coverage
        self.surfaces = surfaces
        self.instruction = coverage.isComplete ? CoverageSnapshot.completeInstruction
            : instruction ?? coverage.instruction
    }

    func didRecordDetail() { hasDetailedGeometry = true }

    func didFinish(_ scan: CapturedScan) {
        capturedScan = scan
        phase = .ready
    }

    func didFail(_ message: String) { phase = .failed(message) }
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
