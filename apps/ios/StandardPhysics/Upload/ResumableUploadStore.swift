import Foundation

struct ResumableUploadStore {
    private struct State: Codable {
        var scanID: UUID?
        var apiBaseURL: URL?
        var previousScanIDs: [UUID] = []
        var completedArtifactIDs: Set<String> = []
        var isFinalized = false
        var lastServerState: ScanState?

        enum CodingKeys: String, CodingKey {
            case scanID
            case apiBaseURL
            case previousScanIDs
            case completedArtifactIDs
            case isFinalized
            case lastServerState
        }

        init() {}

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            scanID = try container.decodeIfPresent(UUID.self, forKey: .scanID)
            apiBaseURL = try container.decodeIfPresent(URL.self, forKey: .apiBaseURL)
            previousScanIDs = try container.decodeIfPresent([UUID].self, forKey: .previousScanIDs) ?? []
            completedArtifactIDs = try container.decodeIfPresent(Set<String>.self, forKey: .completedArtifactIDs) ?? []
            isFinalized = try container.decodeIfPresent(Bool.self, forKey: .isFinalized) ?? false
            lastServerState = try container.decodeIfPresent(ScanState.self, forKey: .lastServerState)
        }
    }

    private let stateURL: URL
    private var state: State

    var scanID: UUID? { state.scanID }
    var apiBaseURL: URL? { state.apiBaseURL }
    var previousScanIDs: [UUID] { state.previousScanIDs }
    var completedArtifactIDs: Set<String> { state.completedArtifactIDs }
    var isFinalized: Bool { state.isFinalized }
    var lastServerState: ScanState? { state.lastServerState }
    var shouldPollServer: Bool {
        state.scanID != nil
            && state.isFinalized
            && state.lastServerState != .ready
            && state.lastServerState != .failed
    }
    var historyText: String {
        if let lastServerState = state.lastServerState { return lastServerState.displayText }
        if state.scanID != nil { return ScanState.uploading.displayText }
        return "Saved on this phone"
    }

    init(captureDirectory: URL) {
        stateURL = captureDirectory.appendingPathComponent("upload-state.json")
        state = (try? Data(contentsOf: stateURL))
            .flatMap { try? JSONDecoder().decode(State.self, from: $0) }
            ?? State()
    }

    func needsUpload(artifactID: String) -> Bool {
        !state.completedArtifactIDs.contains(artifactID)
    }

    mutating func begin(scanID: UUID, apiBaseURL: URL) throws {
        guard state.scanID == nil || state.scanID == scanID else {
            throw UploadStoreError.scanAlreadyStarted
        }
        let normalizedURL = Self.normalizedAPIBaseURL(apiBaseURL)
        guard state.apiBaseURL == nil || state.apiBaseURL == normalizedURL else {
            throw UploadStoreError.apiBaseURLMismatch
        }
        try update {
            $0.scanID = scanID
            $0.apiBaseURL = normalizedURL
        }
    }

    mutating func bindOrValidate(apiBaseURL: URL) throws {
        guard state.scanID != nil else { throw UploadStoreError.scanNotStarted }
        let normalizedURL = Self.normalizedAPIBaseURL(apiBaseURL)
        if let storedURL = state.apiBaseURL, storedURL != normalizedURL {
            throw UploadStoreError.apiBaseURLMismatch
        }
        guard state.apiBaseURL == nil else { return }
        try update { $0.apiBaseURL = normalizedURL }
    }

    mutating func replaceRemoteScan(with scanID: UUID, apiBaseURL: URL) throws {
        guard let existingScanID = state.scanID else {
            throw UploadStoreError.scanNotStarted
        }
        let normalizedURL = Self.normalizedAPIBaseURL(apiBaseURL)
        try update { candidate in
            if !candidate.previousScanIDs.contains(existingScanID) {
                candidate.previousScanIDs.append(existingScanID)
            }
            candidate.scanID = scanID
            candidate.apiBaseURL = normalizedURL
            candidate.completedArtifactIDs = []
            candidate.isFinalized = false
            candidate.lastServerState = nil
        }
    }

    /// Drops every trace of the remote scan, for a walk that was cancelled
    /// after its scan was already made on the server and then deleted there.
    mutating func forgetRemoteScan() throws {
        try update { $0 = State() }
    }

    mutating func recordUploaded(artifactID: String) throws {
        guard state.scanID != nil else { throw UploadStoreError.scanNotStarted }
        try update { $0.completedArtifactIDs.insert(artifactID) }
    }

    mutating func recordFinalized() throws {
        guard state.scanID != nil else { throw UploadStoreError.scanNotStarted }
        try update { $0.isFinalized = true }
    }

    mutating func record(state serverState: ScanState) throws {
        guard state.scanID != nil else { throw UploadStoreError.scanNotStarted }
        try update { $0.lastServerState = serverState }
    }

    private mutating func update(_ mutation: (inout State) -> Void) throws {
        var candidate = state
        mutation(&candidate)
        try persist(candidate)
        state = candidate
    }

    private func persist(_ candidate: State) throws {
        let data = try JSONEncoder().encode(candidate)
        try data.write(to: stateURL, options: .atomic)
    }

    private static func normalizedAPIBaseURL(_ url: URL) -> URL {
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return url
        }
        components.query = nil
        components.fragment = nil
        if components.path.hasSuffix("/") && components.path.count > 1 {
            components.path.removeLast()
        }
        return components.url ?? url
    }
}

enum UploadStoreError: Error, Equatable {
    case scanAlreadyStarted
    case scanNotStarted
    case apiBaseURLMismatch
}
