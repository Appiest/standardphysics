import ARKit
import RoomPlan
import UIKit

final class RoomCaptureController: UIViewController, RoomCaptureViewDelegate, RoomCaptureSessionDelegate {
    private weak var store: CaptureSessionStore?
    private var captureView: RoomCaptureView?
    private var recorder: FrameRecorder?
    private var detailRecorder: LidarMeshRecorder?
    private var coverageEngine = CoverageEngine()
    private var liveRoom: CapturedRoom?
    private var processedRoom: CapturedRoom?
    private var recording: RecordingResult?
    private var finalMeshFrame: ARFrame?
    private var directory: URL?
    private var isFinishing = false
    private var isExporting = false
    private var isCancelled = false
    private var hasExported = false
    private var terminalFailure: String?
    private var processingTimeout: Task<Void, Never>?
    private var coaching: String?

    var canExport: Bool { terminalFailure == nil && processedRoom != nil && recording != nil }

    init(store: CaptureSessionStore) {
        self.store = store
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { nil }

    func start(in directory: URL) throws {
        self.directory = directory
        // RoomPlan preserves the settings of an already-running AR session on iOS 17+.
        let session = ARSession()
        let recorder = try FrameRecorder(session: session, directory: directory)
        let preparedConfiguration = SceneCaptureConfiguration.prepare()
        let configuration = preparedConfiguration.configuration
        session.run(configuration)
        let captureView = RoomCaptureView(frame: view.bounds, arSession: session)
        captureView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        captureView.delegate = self
        captureView.captureSession.delegate = self
        view.addSubview(captureView)
        self.captureView = captureView
        recorder.onTimeLimit = { [weak self] in self?.store?.finish() }
        recorder.onObservation = { [weak self] frame in self?.observe(frame) }
        self.recorder = recorder
        detailRecorder = LidarMeshRecorder(
            directory: directory,
            peopleFilteringEnabled: preparedConfiguration.options.peopleFilteringEnabled
        )
        var roomConfiguration = RoomCaptureSession.Configuration()
        roomConfiguration.isCoachingEnabled = true
        captureView.captureSession.run(configuration: roomConfiguration)
        recorder.start()
    }

    /// The session RoomPlan is running, for anything that draws over its view.
    var arSession: ARSession? { captureView?.captureSession.arSession }

    func finish() {
        guard !isFinishing else { return }
        isFinishing = true
        finalMeshFrame = captureView?.captureSession.arSession.currentFrame
        saveRecoveryRoom(liveRoom)
        captureView?.captureSession.stop(pauseARSession: true)
        recorder?.stop { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let recording): self.recording = recording
            case .failure:
                guard let recovered = RecordingResult.recovered(from: self.directory) else {
                    self.recording = nil
                    self.processingTimeout?.cancel()
                    self.processingTimeout = nil
                    self.failCapture("Free some space on this phone. Return to saved scans to recover your room.")
                    return
                }
                self.recording = recovered
            }
            self.exportIfReady()
        }
        processingTimeout = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(20)) } catch { return }
            guard let self, processedRoom == nil else { return }
            useLiveRoomAfterProcessingFailure()
        }
    }

    func cancel() {
        guard !isCancelled, !hasExported else { return }
        isCancelled = true
        isFinishing = true
        processingTimeout?.cancel()
        processingTimeout = nil
        saveRecoveryRoom(liveRoom)
        captureView?.captureSession.stop(pauseARSession: true)

        let recorder = self.recorder
        self.recorder = nil
        recorder?.cancel { [weak self] error in
            guard let self else { return }
            if error != nil {
                self.store?.didFail("Free some space on this phone. Return to saved scans to recover your room.")
            }
        }
        detailRecorder = nil
    }

    func retryExport() { exportIfReady() }

    private func observe(_ frame: ARFrame) {
        guard !isFinishing else { return }
        detailRecorder?.sample(frame)
        if detailRecorder?.triangleCount ?? 0 > 0 { store?.didRecordDetail() }
        guard let liveRoom else { return }
        let camera = CameraObservation(
            transform: frame.camera.transform,
            intrinsics: frame.camera.intrinsics,
            imageResolution: SIMD2(Float(frame.camera.imageResolution.width), Float(frame.camera.imageResolution.height))
        )
        let surfaces = RoomCoverage.snapshots(from: liveRoom)
        coverageEngine.update(surfaces: surfaces, camera: camera)
        store?.didUpdate(coverage: coverageEngine.snapshot, surfaces: surfaces, instruction: coaching)
        // Only the developer build paints, and working out where costs enough
        // to be worth skipping when nothing is going to draw it.
        if DeveloperMode.isOn {
            store?.didPaint(coverageEngine.paint(on: surfaces))
        }
    }

    nonisolated func captureView(shouldPresent data: CapturedRoomData, error: Error?) -> Bool {
        if error != nil {
            Task { @MainActor [weak self] in self?.useLiveRoomAfterProcessingFailure() }
        }
        return error == nil
    }

    nonisolated func captureView(didPresent room: CapturedRoom, error: Error?) {
        Task { @MainActor [weak self] in
            guard let self, !isCancelled, !isExporting, !hasExported else { return }
            guard error == nil else { useLiveRoomAfterProcessingFailure(); return }
            processedRoom = room
            saveRecoveryRoom(room)
            processingTimeout?.cancel()
            exportIfReady()
        }
    }

    nonisolated func captureSession(_ session: RoomCaptureSession, didUpdate room: CapturedRoom) {
        Task { @MainActor [weak self] in
            guard let self, !isFinishing else { return }
            liveRoom = room
        }
    }

    nonisolated func captureSession(_ session: RoomCaptureSession, didAdd room: CapturedRoom) {
        captureSession(session, didUpdate: room)
    }

    nonisolated func captureSession(_ session: RoomCaptureSession, didChange room: CapturedRoom) {
        captureSession(session, didUpdate: room)
    }

    nonisolated func captureSession(_ session: RoomCaptureSession, didRemove room: CapturedRoom) {
        captureSession(session, didUpdate: room)
    }

    nonisolated func captureSession(_ session: RoomCaptureSession, didProvide instruction: RoomCaptureSession.Instruction) {
        let text = instruction.friendlyText
        Task { @MainActor [weak self] in self?.coaching = text }
    }

    nonisolated func captureSession(_ session: RoomCaptureSession, didEndWith data: CapturedRoomData, error: Error?) {
        guard error != nil else { return }
        Task { @MainActor [weak self] in
            self?.finish()
            self?.useLiveRoomAfterProcessingFailure()
        }
    }

    private func useLiveRoomAfterProcessingFailure() {
        guard !isCancelled, terminalFailure == nil, processedRoom == nil else { return }
        processedRoom = liveRoom
        guard processedRoom != nil else {
            failCapture("Start a new scan and walk around the room.")
            return
        }
        exportIfReady()
    }

    private func saveRecoveryRoom(_ room: CapturedRoom?) {
        guard let room, let directory else { return }
        do {
            try JSONEncoder.standardPhysics.encode(room).write(
                to: directory.appendingPathComponent("room.recovery.json"), options: .atomic)
        } catch {
            store?.didFail("Free some space on this phone, then save again.")
        }
    }

    private func exportIfReady() {
        guard !isCancelled, terminalFailure == nil, !isExporting, !hasExported,
              let room = processedRoom, let recording, let directory else { return }
        isExporting = true
        let coverage = coverageEngine.reconcile(finalSurfaces: RoomCoverage.snapshots(from: room))
        let detailRecorder = detailRecorder
        let finalMeshFrame = finalMeshFrame
        Task { [weak self] in
            var recording = recording
            do { try await detailRecorder?.finish(frame: finalMeshFrame) }
            catch { recording.captureNotice = "Your room layout is saved. Record another pass to save the detailed surfaces." }
            let savedRecording = recording
            let result = await Task.detached(priority: .userInitiated) {
                Result { try ScanExporter.export(room: room, recording: savedRecording, coverage: coverage, directory: directory) }
            }.value
            guard let self else { return }
            isExporting = false
            switch result {
            case .success(let scan):
                hasExported = true
                if !isCancelled { store?.didFinish(scan) }
            case .failure: store?.didFail("Free some space on this phone, then save again.")
            }
        }
    }

    private func failCapture(_ message: String) {
        guard terminalFailure == nil else { return }
        terminalFailure = message
        store?.didFail(message)
    }
}

private extension RoomCaptureSession.Instruction {
    var friendlyText: String? {
        switch self {
        case .moveCloseToWall: "Walk closer to the wall"
        case .moveAwayFromWall: "Take one step back"
        case .slowDown: "Turn around slowly"
        case .turnOnLight: "Turn on more lights"
        case .lowTexture: nil
        case .normal: nil
        @unknown default: nil
        }
    }
}
