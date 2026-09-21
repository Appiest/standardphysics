import XCTest
@testable import StandardPhysics

@MainActor
final class UploadViewModelTests: XCTestCase {
    override func tearDown() {
        UploadURLProtocolStub.handler = nil
        super.tearDown()
    }

    func testCoreEvidenceFinalizesBeforeSlowExtrasAndReadyIsObservable() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory, optionalArtifactKinds: [.walkthroughMP4, .frames, .lidarMesh])
        let remoteID = UUID()
        var uploadedKinds: [ArtifactKind] = []
        var completedAfterKinds: [ArtifactKind] = []

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                return .scan(status: 201, id: remoteID, state: .uploading)
            case "PUT":
                let artifact = try XCTUnwrap(scan.artifacts.first { path.hasSuffix("/\($0.id)") })
                uploadedKinds.append(artifact.kind)
                let delay: TimeInterval = artifact.kind == .walkthroughMP4 ? 0.25 : 0
                return StubResponse(status: 201, data: Data("{}".utf8), delay: delay)
            case "POST" where path.hasSuffix("/complete"):
                completedAfterKinds = uploadedKinds
                return .scan(status: 200, id: remoteID, state: .measuring)
            case "GET":
                return .scan(status: 200, id: remoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(
            scan: scan,
            name: "Tea House",
            client: makeClient(),
            pollInterval: .milliseconds(5)
        )
        model.start()

        try await waitUntil { model.state == .ready && model.uploadedCount < model.totalCount }
        XCTAssertEqual(
            completedAfterKinds,
            [.lidarMesh, .roomMetadata, .poses, .coverage, .roomUSDZ, .roomJSON]
        )

        try await waitUntil { model.uploadedCount == model.totalCount }
        XCTAssertEqual(uploadedKinds, [.lidarMesh, .roomMetadata, .poses, .coverage, .roomUSDZ, .roomJSON, .walkthroughMP4, .frames])
    }

    func testRestartUsesPersistedReceiptsWithoutCreatingAnotherRemoteScan() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory)
        let remoteID = UUID()
        var store = ResumableUploadStore(captureDirectory: directory)
        try store.begin(scanID: remoteID, apiBaseURL: URL(string: "https://standard.physics")!)
        try store.recordUploaded(artifactID: "room-usdz")
        var uploadedArtifactIDs: [String] = []

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                XCTFail("A persisted remote scan must be resumed")
                return StubResponse(status: 500, data: Data())
            case "PUT":
                let artifact = try XCTUnwrap(scan.artifacts.first { path.hasSuffix("/\($0.id)") })
                uploadedArtifactIDs.append(artifact.id)
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                return .scan(status: 200, id: remoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(scan: scan, name: "Tea House", client: makeClient(), pollInterval: .milliseconds(5))
        model.start()
        try await waitUntil { model.state == .ready }

        XCTAssertEqual(uploadedArtifactIDs, ["room-metadata", "poses", "coverage", "room-json"])
        XCTAssertEqual(ResumableUploadStore(captureDirectory: directory).completedArtifactIDs, Set(scan.artifacts.map(\.id)))
    }

    func testRetryAfterTerminalFailureCreatesReplacementRemoteAndResetsReceipts() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory)
        let oldRemoteID = UUID()
        let newRemoteID = UUID()
        var store = ResumableUploadStore(captureDirectory: directory)
        try store.begin(scanID: oldRemoteID, apiBaseURL: URL(string: "https://standard.physics")!)
        for artifact in scan.artifacts {
            try store.recordUploaded(artifactID: artifact.id)
        }
        try store.recordFinalized()
        try store.record(state: .failed)
        var createdCount = 0

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            XCTAssertFalse(path.contains(oldRemoteID.uuidString))
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                createdCount += 1
                return .scan(status: 201, id: newRemoteID, state: .uploading)
            case "PUT":
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                return .scan(status: 200, id: newRemoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(scan: scan, name: "Tea House", client: makeClient(), pollInterval: .milliseconds(5))
        XCTAssertEqual(model.state, .failed)
        model.retry()
        try await waitUntil { model.state == .ready }

        let restored = ResumableUploadStore(captureDirectory: directory)
        XCTAssertEqual(createdCount, 1)
        XCTAssertEqual(restored.scanID, newRemoteID)
        XCTAssertEqual(restored.previousScanIDs, [oldRemoteID])
        XCTAssertEqual(restored.completedArtifactIDs, Set(scan.artifacts.map(\.id)))
    }

    func testTransientNetworkRetryRetainsTheSameRemoteScan() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory)
        let remoteID = UUID()
        var createCount = 0
        var firstUploadFails = true

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                createCount += 1
                return .scan(status: 201, id: remoteID, state: .uploading)
            case "PUT":
                XCTAssertTrue(path.contains(remoteID.uuidString))
                if firstUploadFails {
                    firstUploadFails = false
                    return StubResponse(status: 500, data: Data())
                }
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                return .scan(status: 200, id: remoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(scan: scan, name: "Tea House", client: makeClient(), pollInterval: .milliseconds(5))
        model.start()
        try await waitUntil { model.errorMessage != nil }
        XCTAssertEqual(model.scanID, remoteID)

        model.retry()
        try await waitUntil { model.state == .ready }
        XCTAssertEqual(createCount, 1)
        XCTAssertEqual(ResumableUploadStore(captureDirectory: directory).scanID, remoteID)
    }

    func testMissingPersistedRemoteCanBeReplacedFromTheSavedCapture() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory)
        let missingRemoteID = UUID()
        let replacementRemoteID = UUID()
        var store = ResumableUploadStore(captureDirectory: directory)
        try store.begin(scanID: missingRemoteID, apiBaseURL: URL(string: "https://standard.physics")!)
        var createdCount = 0

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            if path.contains(missingRemoteID.uuidString) {
                return StubResponse(status: 404, data: Data())
            }
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                createdCount += 1
                return .scan(status: 201, id: replacementRemoteID, state: .uploading)
            case "PUT":
                XCTAssertTrue(path.contains(replacementRemoteID.uuidString))
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                return .scan(status: 200, id: replacementRemoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(
            scan: scan,
            name: "Tea House",
            client: makeClient(),
            pollInterval: .milliseconds(5)
        )
        model.start()
        try await waitUntil { model.state == .failed }
        XCTAssertEqual(
            model.errorMessage,
            "The upload server lost this scan. Try again to upload your saved copy."
        )
        XCTAssertEqual(ResumableUploadStore(captureDirectory: directory).lastServerState, .failed)

        model.retry()
        try await waitUntil { model.state == .ready }

        let restored = ResumableUploadStore(captureDirectory: directory)
        XCTAssertEqual(createdCount, 1)
        XCTAssertEqual(restored.scanID, replacementRemoteID)
        XCTAssertEqual(restored.previousScanIDs, [missingRemoteID])
        XCTAssertEqual(restored.completedArtifactIDs, Set(scan.artifacts.map(\.id)))
    }

    func testEndpointChangeRejectsPersistedRemoteBeforeIssuingARequest() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory)
        let remoteID = UUID()
        var store = ResumableUploadStore(captureDirectory: directory)
        try store.begin(scanID: remoteID, apiBaseURL: URL(string: "https://first.standard.physics")!)
        var requestCount = 0
        UploadURLProtocolStub.handler = { _ in
            requestCount += 1
            return StubResponse(status: 500, data: Data())
        }

        let changedEndpointClient = ScanUploadClient(
            baseURL: URL(string: "https://second.standard.physics")!,
            session: makeSession()
        )
        let model = UploadViewModel(scan: scan, name: "Tea House", client: changedEndpointClient)
        model.start()
        try await waitUntil { model.errorMessage != nil }

        XCTAssertEqual(model.errorMessage, "This scan is linked to a different upload server.")
        XCTAssertEqual(model.scanID, remoteID)
        XCTAssertEqual(requestCount, 0)
    }

    func testOptionalFailureRemainsVisibleAtReadyAndRetriesWithoutFinalizingAgain() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory, optionalArtifactKinds: [.walkthroughMP4])
        let remoteID = UUID()
        var pollCount = 0
        var completionCount = 0
        var firstOptionalUploadFails = true

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                return .scan(status: 201, id: remoteID, state: .uploading)
            case "PUT" where path.hasSuffix("/optional-0"):
                if firstOptionalUploadFails {
                    firstOptionalUploadFails = false
                    return StubResponse(status: 500, data: Data())
                }
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "PUT":
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                completionCount += 1
                return .scan(status: 200, id: remoteID, state: .measuring)
            case "GET":
                pollCount += 1
                let state: ScanState = pollCount < 4 ? .measuring : .ready
                return .scan(status: 200, id: remoteID, state: state)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(
            scan: scan,
            name: "Tea House",
            client: makeClient(),
            pollInterval: .milliseconds(20)
        )
        model.start()
        try await waitUntil {
            model.state == .measuring && model.optionalUploadErrorMessage != nil
        }
        XCTAssertEqual(model.pendingOptionalUploadCount, 1)

        try await waitUntil { model.state == .ready }
        XCTAssertEqual(model.optionalUploadErrorMessage, "Some video or images could not upload.")
        XCTAssertEqual(model.pendingOptionalUploadCount, 1)

        model.retry()
        try await waitUntil { model.pendingOptionalUploadCount == 0 }
        XCTAssertEqual(model.state, .ready)
        XCTAssertNil(model.optionalUploadErrorMessage)
        XCTAssertEqual(completionCount, 1)
    }

    func testCancelledRunCannotOverwriteReplacementRunState() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let scan = try makeScan(in: directory)
        let cancelledRemoteID = UUID()
        let replacementRemoteID = UUID()
        var createCount = 0

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                createCount += 1
                if createCount == 1 {
                    return .scan(status: 201, id: cancelledRemoteID, state: .uploading, delay: 0.2)
                }
                return .scan(status: 201, id: replacementRemoteID, state: .uploading)
            case "PUT":
                XCTAssertTrue(path.contains(replacementRemoteID.uuidString))
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                return .scan(status: 200, id: replacementRemoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(scan: scan, name: "Tea House", client: makeClient(), pollInterval: .milliseconds(5))
        model.start()
        try await waitUntil { createCount == 1 }
        model.retry()
        try await waitUntil { model.state == .ready }
        try await Task.sleep(for: .milliseconds(250))

        XCTAssertEqual(model.scanID, replacementRemoteID)
        XCTAssertEqual(ResumableUploadStore(captureDirectory: directory).scanID, replacementRemoteID)
        XCTAssertEqual(createCount, 2)
    }

    func testPhotoManifestUploadsAfterAllFramesAndNotWhenAFrameFails() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        // optional-0 and optional-1 are frame artifacts; optional-2 is the manifest.
        let scan = try makeScan(in: directory, optionalArtifactKinds: [.frames, .frames, .photoManifest])
        let remoteID = UUID()
        var uploadedIDs: [String] = []
        var secondFrameShouldFail = true

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                return .scan(status: 201, id: remoteID, state: .uploading)
            case "PUT" where path.hasSuffix("/optional-1") && secondFrameShouldFail:
                secondFrameShouldFail = false
                return StubResponse(status: 500, data: Data())
            case "PUT":
                let artifact = try XCTUnwrap(scan.artifacts.first { path.hasSuffix("/\($0.id)") })
                if artifact.id.hasPrefix("optional") { uploadedIDs.append(artifact.id) }
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                return .scan(status: 200, id: remoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(scan: scan, name: "Tea House", client: makeClient(), pollInterval: .milliseconds(5))
        model.start()
        try await waitUntil { model.optionalUploadErrorMessage != nil }

        XCTAssertFalse(uploadedIDs.contains("optional-2"), "the manifest must not upload while a frame upload has failed")
        XCTAssertEqual(uploadedIDs, ["optional-0"])

        model.retry()
        try await waitUntil { model.pendingOptionalUploadCount == 0 }

        XCTAssertEqual(uploadedIDs, ["optional-0", "optional-1", "optional-2"])
    }

    func testPhotoManifestUploadsLastRegardlessOfArtifactArrayOrder() async throws {
        let directory = try makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        // The manifest sits BEFORE its frame in the scan's own artifact
        // array, so only an explicit dependency check (not array order)
        // can keep it from uploading too early.
        let scan = try makeScan(in: directory, optionalArtifactKinds: [.photoManifest, .frames])
        let remoteID = UUID()
        var uploadedIDs: [String] = []

        UploadURLProtocolStub.handler = { request in
            let path = request.url!.path
            switch request.httpMethod {
            case "POST" where path == "/api/scans":
                return .scan(status: 201, id: remoteID, state: .uploading)
            case "PUT":
                let artifact = try XCTUnwrap(scan.artifacts.first { path.hasSuffix("/\($0.id)") })
                if artifact.id.hasPrefix("optional") { uploadedIDs.append(artifact.id) }
                return StubResponse(status: 201, data: Data("{}".utf8))
            case "POST" where path.hasSuffix("/complete"):
                return .scan(status: 200, id: remoteID, state: .ready)
            default:
                return StubResponse(status: 500, data: Data())
            }
        }

        let model = UploadViewModel(scan: scan, name: "Tea House", client: makeClient(), pollInterval: .milliseconds(5))
        model.start()
        try await waitUntil { model.state == .ready && model.pendingOptionalUploadCount == 0 }

        XCTAssertEqual(uploadedIDs, ["optional-1", "optional-0"])
    }

    private func makeClient() -> ScanUploadClient {
        ScanUploadClient(
            baseURL: URL(string: "https://standard.physics")!,
            session: makeSession()
        )
    }

    private func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [UploadURLProtocolStub.self]
        return URLSession(configuration: configuration)
    }

    private func makeScan(
        in directory: URL,
        optionalArtifactKinds: [ArtifactKind] = []
    ) throws -> CapturedScan {
        let core = [
            ("room-usdz", ArtifactKind.roomUSDZ),
            ("room-json", .roomJSON),
            ("room-metadata", .roomMetadata),
            ("poses", .poses),
            ("coverage", .coverage)
        ]
        let optional = optionalArtifactKinds.enumerated().map { index, kind in
            ("optional-\(index)", kind)
        }
        let artifacts = try (core + optional).map { id, kind in
            let fileURL = directory.appendingPathComponent("\(id).bin")
            try Data(id.utf8).write(to: fileURL)
            return CaptureArtifact(id: id, kind: kind, fileURL: fileURL)
        }
        return CapturedScan(
            id: UUID(),
            directory: directory,
            roomURL: artifacts[0].fileURL,
            duration: 30,
            artifacts: artifacts,
            name: "Tea House"
        )
    }

    private func makeDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    private func waitUntil(
        timeout: TimeInterval = 2,
        _ condition: @escaping () -> Bool
    ) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() >= deadline {
                XCTFail("Timed out waiting for upload state")
                return
            }
            try await Task.sleep(for: .milliseconds(10))
        }
    }
}

private struct StubResponse {
    let status: Int
    let data: Data
    let delay: TimeInterval

    init(status: Int, data: Data, delay: TimeInterval = 0) {
        self.status = status
        self.data = data
        self.delay = delay
    }

    static func scan(status: Int, id: UUID, state: ScanState, delay: TimeInterval = 0) -> StubResponse {
        let payload = #"{"id":"\#(id.uuidString)","state":"\#(state.rawValue)"}"#
        return StubResponse(status: status, data: Data(payload.utf8), delay: delay)
    }
}

private final class UploadURLProtocolStub: URLProtocol {
    // XCTest installs one handler at a time. URLProtocol invokes it on its own
    // loading thread, so this test-only shared hook cannot be actor-isolated.
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> StubResponse)?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            let result = try Self.handler?(request) ?? StubResponse(status: 500, data: Data())
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: result.status,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            let protocolStub = TestSendableBox(value: self)
            let finishLoading: @Sendable () -> Void = {
                let stub = protocolStub.value
                stub.client?.urlProtocol(stub, didReceive: response, cacheStoragePolicy: .notAllowed)
                stub.client?.urlProtocol(stub, didLoad: result.data)
                stub.client?.urlProtocolDidFinishLoading(stub)
            }
            if result.delay > 0 {
                DispatchQueue.global().asyncAfter(deadline: .now() + result.delay, execute: finishLoading)
            } else {
                finishLoading()
            }
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private struct TestSendableBox<Value>: @unchecked Sendable {
    let value: Value
}
