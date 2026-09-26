import Foundation

/// The owner's first minutes after a walk: the quick answers, the quick
/// photos and the door push, then the measuring wait.
///
/// The server keeps the requests and says which are still open, so each
/// answer is followed by a fresh list. A "no" to the restroom closes the
/// restroom photo on the server, and the next list simply leaves it out.
@MainActor
final class ShopSetupModel: ObservableObject, Identifiable {
    enum Step: Equatable {
        case preparing
        case question(OwnerRequest)
        case photo(OwnerRequest)
        case pushForce(OwnerRequest)
        case measuring
    }

    let id = UUID()
    /// This phone's upload of the walk, when the walk was taken on it.
    let upload: UploadViewModel?
    @Published private(set) var scanID: UUID?
    @Published private(set) var step: Step = .preparing
    @Published private(set) var isSending = false
    @Published private(set) var problem: String?
    @Published private(set) var resultsReady = false
    @Published private(set) var progress = (done: 0, total: 0)

    private weak var app: AppModel?
    private var requests: [OwnerRequest] = []
    /// Answered or skipped on this phone, so a slow or failed refresh never
    /// asks the same thing twice.
    private var settled: Set<String> = []

    init(upload: UploadViewModel, app: AppModel) {
        self.upload = upload
        self.app = app
        scanID = upload.scanID
    }

    init(scanID: UUID, app: AppModel) {
        upload = nil
        self.app = app
        self.scanID = scanID
    }

    /// Waits for the upload to give the walk a scan on the server, then asks
    /// for its requests. A walk that could not start uploading goes straight
    /// to the wait, which says what went wrong.
    func begin() async {
#if DEBUG
        if isDebugPreview { return }
#endif
        while scanID == nil {
            if let found = upload?.scanID {
                scanID = found
                break
            }
            if upload?.errorMessage != nil || upload == nil {
                step = .measuring
                return
            }
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
        }
        await refresh()
    }

    func refresh() async {
        guard let scanID, let api = app?.api() else {
            step = .measuring
            return
        }
        do {
            requests = try await api.requests(scanID: scanID)
            problem = nil
        } catch OwnerAPIError.signedOut {
            problem = OwnerAPIError.signedOut.localizedDescription
        } catch {
            if requests.isEmpty { step = .measuring }
        }
        advance()
    }

    func answer(_ yes: Bool) {
        send { api, scanID, request in
            try await api.answer(scanID: scanID, requestID: request.id, yes: yes)
        }
    }

    func savePushForce(_ pounds: Double) {
        send { api, scanID, request in
            try await api.answer(scanID: scanID, requestID: request.id, number: pounds)
        }
    }

    func sendPhoto(_ jpeg: Data) {
        send { api, scanID, request in
            try await api.sendPhoto(scanID: scanID, requestID: request.id, jpeg: jpeg)
        }
    }

    func skip() {
        send { api, scanID, request in
            try await api.skip(scanID: scanID, requestID: request.id)
        }
    }

    /// Watches for the results: the upload reaching Ready on this phone, or,
    /// for a shop walked elsewhere, the journey moving past measuring.
    func watchForResults() async {
#if DEBUG
        if isDebugPreview { return }
#endif
        while !Task.isCancelled && !resultsReady {
            resultsReady = await resultsAreReady()
            if resultsReady { return }
            do { try await Task.sleep(for: .seconds(upload == nil ? 5 : 1)) } catch { return }
        }
    }

#if DEBUG
    /// A screen of the flow with made-up requests, for `SP_DEBUG_SCREEN`.
    init(debugStep: Step, progress: (done: Int, total: Int), app: AppModel) {
        upload = nil
        self.app = app
        scanID = UUID()
        step = debugStep
        self.progress = progress
        isDebugPreview = true
    }

    private(set) var isDebugPreview = false
    var debugPrompt: MeasuringView.Prompt?
#endif

    private func resultsAreReady() async -> Bool {
        if let upload { return upload.state == .ready }
        guard let scanID, let journey = try? await app?.api()?.journey(scanID: scanID) else { return false }
        return !journey.isBeforeResults
    }

    private var currentRequest: OwnerRequest? {
        switch step {
        case .question(let request), .photo(let request), .pushForce(let request): request
        case .preparing, .measuring: nil
        }
    }

    private func send(_ action: @escaping (OwnerAPI, UUID, OwnerRequest) async throws -> Void) {
        guard !isSending, let request = currentRequest, let scanID, let api = app?.api() else { return }
        isSending = true
        problem = nil
        Task {
            do {
                try await action(api, scanID, request)
                settled.insert(request.id)
                Haptics.sent()
                await refresh()
            } catch {
                problem = error.localizedDescription
            }
            isSending = false
        }
    }

    private func advance() {
        let remaining = requests.filter { !settled.contains($0.id) }
        step = Self.nextStep(in: remaining)
        progress = Self.progress(of: requests, settled: settled)
    }

    /// The questions first, because a "no" removes a photo or a number that
    /// would otherwise be asked for, then the photos, then the door push.
    nonisolated static func nextStep(in requests: [OwnerRequest]) -> Step {
        let open = requests.filter { $0.isInShop && $0.isOpen }
        if let question = open.first(where: \.isYesOrNo) { return .question(question) }
        if let photo = open.first(where: \.isPhoto) { return .photo(photo) }
        if let push = open.first(where: \.isPushForce) { return .pushForce(push) }
        return .measuring
    }

    nonisolated static func progress(of requests: [OwnerRequest], settled: Set<String>) -> (done: Int, total: Int) {
        let asked = requests.filter { $0.isInShop && $0.status != "not_applicable" && $0.kind != "another_look" }
        let open = asked.filter { $0.isOpen && !settled.contains($0.id) }
        return (asked.count - open.count, asked.count)
    }
}
