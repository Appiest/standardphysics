import Foundation

/// One keyframe on disk and the pose it was taken from.
struct WalkFrame: Sendable {
    let fileURL: URL
    let pose: PoseRecord
}

/// What the server scan for a walk is called before the walk has ended.
struct WalkUploadPlan: Sendable {
    let client: ScanUploadClient
    let name: String
    let replaces: UUID?
}

/// Sends a walk's keyframes to the server while the walk is still going, so
/// the server can find objects in them before the owner taps Done.
///
/// It is strictly a head start. Frames it cannot send in time, because the
/// queue was full, the phone was off Wi-Fi, or the network failed, are simply
/// left for the upload after the walk, which skips whatever this recorded.
/// Nothing here ever waits on capture: `offer` only appends to a bounded
/// buffer, and a full buffer drops the frame instead of blocking.
actor WalkFrameStreamer {
    enum Offer: Equatable { case queued, dropped }

    static let defaultCapacity = 8
    static let defaultRetryDelays: [Duration] = [.seconds(1), .seconds(2), .seconds(4), .seconds(8)]

    private enum Outcome { case sent, skipped, stopStreaming }

    private nonisolated let inbox: AsyncStream<WalkFrame>.Continuation
    private let frames: AsyncStream<WalkFrame>
    private let plan: WalkUploadPlan
    private let gate: WalkStreamingGate
    private let retryDelays: [Duration]
    private var store: ResumableUploadStore
    private var worker: Task<Void, Never>?
    private var hasGivenUp = false

    init(
        captureDirectory: URL,
        plan: WalkUploadPlan,
        gate: WalkStreamingGate,
        capacity: Int = defaultCapacity,
        retryDelays: [Duration] = defaultRetryDelays
    ) {
        let (stream, continuation) = AsyncStream.makeStream(
            of: WalkFrame.self,
            bufferingPolicy: .bufferingOldest(capacity)
        )
        frames = stream
        inbox = continuation
        self.plan = plan
        self.gate = gate
        self.retryDelays = retryDelays
        store = ResumableUploadStore(captureDirectory: captureDirectory)
    }

    var remoteScanID: UUID? { store.scanID }

    /// Safe from any thread, and returns at once.
    @discardableResult
    nonisolated func offer(_ frame: WalkFrame) -> Offer {
        if case .enqueued = inbox.yield(frame) { return .queued }
        return .dropped
    }

    func start() {
        guard worker == nil else { return }
        worker = Task { await self.drain() }
    }

    /// The walk has ended: frames already queued may still go while the room
    /// is processed, but no new ones will come.
    nonisolated func finishOffering() {
        inbox.finish()
    }

    /// Stops sending and waits until nothing more will be written to the
    /// upload receipt, so the upload after the walk can take it over.
    func stop() async {
        inbox.finish()
        worker?.cancel()
        await worker?.value
    }

    /// The walk was cancelled: the scan made for it on the server goes too.
    func discard() async {
        await stop()
        guard let scanID = store.scanID else { return }
        try? await plan.client.delete(id: scanID)
        try? store.forgetRemoteScan()
    }

    private func drain() async {
        for await frame in frames {
            if Task.isCancelled || hasGivenUp { return }
            if await send(frame) == .stopStreaming { hasGivenUp = true }
        }
    }

    private func send(_ frame: WalkFrame) async -> Outcome {
        guard let artifactID = frame.pose.frameArtifactID else { return .skipped }
        let artifact = CaptureArtifact(id: artifactID, kind: .frames, fileURL: frame.fileURL)
        return await withRetries {
            let scanID = try await self.remoteScan()
            try await self.plan.client.upload(artifact, to: scanID, pose: frame.pose)
            try await self.recordUploaded(artifactID)
        }
    }

    /// Tries once, then once after each retry delay while the network keeps
    /// failing. The frame is left for later rather than holding the queue.
    private func withRetries(_ attempt: @escaping () async throws -> Void) async -> Outcome {
        for delay in [Duration.zero] + retryDelays {
            guard await wait(delay), gate.allowsStreaming else { return .skipped }
            do {
                try await attempt()
                return .sent
            } catch {
                guard Self.isWorthRetrying(error), !Task.isCancelled else { return Self.outcome(of: error) }
            }
        }
        return .skipped
    }

    private func wait(_ delay: Duration) async -> Bool {
        guard delay > .zero else { return !Task.isCancelled }
        do { try await Task.sleep(for: delay) } catch { return false }
        return true
    }

    private func remoteScan() async throws -> UUID {
        if let scanID = store.scanID { return scanID }
        let remote = try await plan.client.createScan(name: plan.name, duration: 0, replaces: plan.replaces)
        try store.begin(scanID: remote.id, apiBaseURL: plan.client.baseURL)
        return remote.id
    }

    private func recordUploaded(_ artifactID: String) throws {
        try store.recordUploaded(artifactID: artifactID)
    }

    private static func isWorthRetrying(_ error: Error) -> Bool {
        guard let urlError = error as? URLError else { return false }
        return urlError.code != .cancelled
    }

    /// A signed-out session or a missing scan will fail every later frame
    /// too, so streaming stops and the upload after the walk sorts it out.
    /// Anything else, like a refused pose, only costs this one frame.
    private static func outcome(of error: Error) -> Outcome {
        switch error {
        case UploadClientError.signedOut, UploadClientError.remoteScanMissing, is UploadStoreError: .stopStreaming
        default: .skipped
        }
    }
}
