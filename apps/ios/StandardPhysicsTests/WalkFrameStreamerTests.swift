import XCTest
@testable import StandardPhysics

final class WalkFrameStreamerTests: XCTestCase {
    private let baseURL = URL(string: "https://standard.physics")!
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory.appendingPathComponent("frames"),
            withIntermediateDirectories: true
        )
    }

    override func tearDown() {
        WalkStub.reset()
        try? FileManager.default.removeItem(at: directory)
        super.tearDown()
    }

    func testFramesGoUpInCaptureOrderWithTheirPoseUnderOneNewScan() async throws {
        let remoteID = UUID()
        WalkStub.handler = { request in
            if request.httpMethod == "POST" { return .scan(remoteID) }
            return .ok
        }
        let streamer = makeStreamer(gate: OpenGate(true), replaces: nil)
        await streamer.start()
        let frames = try (0..<3).map(makeFrame)
        frames.forEach { streamer.offer($0) }

        try await waitUntil { WalkStub.requests.filter { $0.httpMethod == "PUT" }.count == 3 }
        await streamer.stop()

        let creates = WalkStub.requests.filter { $0.httpMethod == "POST" }
        XCTAssertEqual(creates.count, 1)
        let body = try XCTUnwrap(creates.first.flatMap(WalkStub.body(of:)))
        XCTAssertEqual(body["name"] as? String, "Tea House")
        XCTAssertEqual(body["duration_seconds"] as? Double, 0)

        let puts = WalkStub.requests.filter { $0.httpMethod == "PUT" }
        XCTAssertEqual(puts.map { $0.url!.lastPathComponent }, ["frame-0000", "frame-0001", "frame-0002"])
        XCTAssertTrue(puts.allSatisfy { $0.url!.path.contains(remoteID.uuidString) })
        for (request, frame) in zip(puts, frames) {
            let header = try XCTUnwrap(request.value(forHTTPHeaderField: "X-Frame-Pose"))
            let sent = try JSONDecoder().decode(PoseRecord.self, from: Data(header.utf8))
            XCTAssertEqual(sent.frameID, frame.pose.frameID)
            XCTAssertEqual(sent.transform, frame.pose.transform)
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-Artifact-Kind"), "frames")
        }

        let receipt = ResumableUploadStore(captureDirectory: directory)
        XCTAssertEqual(receipt.scanID, remoteID)
        XCTAssertEqual(receipt.completedArtifactIDs, ["frame-0000", "frame-0001", "frame-0002"])
    }

    func testAWalkThatJoinsAShopMakesAScanThatReplacesIt() async throws {
        let shop = UUID()
        WalkStub.handler = { $0.httpMethod == "POST" ? .scan(UUID()) : .ok }
        let streamer = makeStreamer(gate: OpenGate(true), replaces: shop)
        await streamer.start()
        streamer.offer(try makeFrame(0))

        try await waitUntil { WalkStub.requests.contains { $0.httpMethod == "PUT" } }
        await streamer.stop()

        let body = try XCTUnwrap(WalkStub.requests.first.flatMap(WalkStub.body(of:)))
        XCTAssertEqual(body["replaces"] as? String, shop.uuidString.lowercased())
    }

    func testNothingIsSentOffWiFi() async throws {
        WalkStub.handler = { _ in .ok }
        let streamer = makeStreamer(gate: OpenGate(false))
        await streamer.start()
        for number in 0..<3 { streamer.offer(try makeFrame(number)) }
        streamer.finishOffering()
        try await Task.sleep(for: .milliseconds(100))
        await streamer.stop()

        XCTAssertTrue(WalkStub.requests.isEmpty)
        let receipt = ResumableUploadStore(captureDirectory: directory)
        XCTAssertNil(receipt.scanID)
        XCTAssertTrue(receipt.completedArtifactIDs.isEmpty)
    }

    func testAFullQueueDropsFramesInsteadOfHoldingUpCapture() async throws {
        WalkStub.handler = { $0.httpMethod == "POST" ? .scan(UUID(), delay: 0.5) : .ok }
        let streamer = makeStreamer(gate: OpenGate(true), capacity: 4)
        await streamer.start()
        streamer.offer(try makeFrame(0))
        try await waitUntil { !WalkStub.requests.isEmpty }

        let frames = try (1...20).map(makeFrame)
        let started = Date()
        let offers = frames.map { streamer.offer($0) }
        let elapsed = Date().timeIntervalSince(started)

        XCTAssertLessThan(elapsed, 0.05)
        XCTAssertEqual(offers.filter { $0 == .queued }.count, 4)
        XCTAssertEqual(offers.filter { $0 == .dropped }.count, 16)

        streamer.finishOffering()
        try await waitUntil(timeout: 3) {
            WalkStub.requests.filter { $0.httpMethod == "PUT" }.count == 5
        }
        await streamer.stop()
        let sent = WalkStub.requests.filter { $0.httpMethod == "PUT" }.map { $0.url!.lastPathComponent }
        XCTAssertEqual(sent, ["frame-0000", "frame-0001", "frame-0002", "frame-0003", "frame-0004"])
    }

    func testANetworkFailureIsRetriedThenTheFrameIsLeftForAfterTheWalk() async throws {
        var attemptsForFirstFrame = 0
        WalkStub.handler = { request in
            if request.httpMethod == "POST" { return .scan(UUID()) }
            guard request.url!.lastPathComponent == "frame-0000" else { return .ok }
            attemptsForFirstFrame += 1
            throw URLError(.networkConnectionLost)
        }
        let streamer = makeStreamer(gate: OpenGate(true))
        await streamer.start()
        streamer.offer(try makeFrame(0))
        streamer.offer(try makeFrame(1))

        try await waitUntil { ResumableUploadStore(captureDirectory: self.directory).completedArtifactIDs.count == 1 }
        await streamer.stop()

        XCTAssertEqual(attemptsForFirstFrame, 3)
        XCTAssertEqual(ResumableUploadStore(captureDirectory: directory).completedArtifactIDs, ["frame-0001"])
    }

    func testARefusedFrameCostsOnlyThatFrame() async throws {
        WalkStub.handler = { request in
            if request.httpMethod == "POST" { return .scan(UUID()) }
            return request.url!.lastPathComponent == "frame-0000" ? .status(400) : .ok
        }
        let streamer = makeStreamer(gate: OpenGate(true))
        await streamer.start()
        streamer.offer(try makeFrame(0))
        streamer.offer(try makeFrame(1))

        try await waitUntil { ResumableUploadStore(captureDirectory: self.directory).completedArtifactIDs.count == 1 }
        await streamer.stop()
        XCTAssertEqual(WalkStub.requests.filter { $0.httpMethod == "PUT" }.count, 2)
    }

    func testASignedOutSessionStopsStreamingForTheRestOfTheWalk() async throws {
        WalkStub.handler = { _ in .status(401) }
        let streamer = makeStreamer(gate: OpenGate(true))
        await streamer.start()
        for number in 0..<3 { streamer.offer(try makeFrame(number)) }
        streamer.finishOffering()
        try await Task.sleep(for: .milliseconds(150))
        await streamer.stop()

        XCTAssertEqual(WalkStub.requests.count, 1)
    }

    func testARelaunchedWalkKeepsItsScanAndItsReceipts() async throws {
        let remoteID = UUID()
        var receipt = ResumableUploadStore(captureDirectory: directory)
        try receipt.begin(scanID: remoteID, apiBaseURL: baseURL)
        try receipt.recordUploaded(artifactID: "frame-0000")
        WalkStub.handler = { request in
            if request.httpMethod == "POST" { XCTFail("The saved scan must be reused") }
            return .ok
        }

        let streamer = makeStreamer(gate: OpenGate(true))
        await streamer.start()
        streamer.offer(try makeFrame(1))
        try await waitUntil { WalkStub.requests.count == 1 }
        await streamer.stop()

        XCTAssertTrue(WalkStub.requests[0].url!.path.contains(remoteID.uuidString))
        XCTAssertEqual(
            ResumableUploadStore(captureDirectory: directory).completedArtifactIDs,
            ["frame-0000", "frame-0001"]
        )
        let restoredID = await streamer.remoteScanID
        XCTAssertEqual(restoredID, remoteID)
    }

    func testACancelledWalkDeletesItsScanAndForgetsIt() async throws {
        let remoteID = UUID()
        WalkStub.handler = { request in
            switch request.httpMethod {
            case "POST": .scan(remoteID)
            case "DELETE": .status(204)
            default: .ok
            }
        }
        let streamer = makeStreamer(gate: OpenGate(true))
        await streamer.start()
        streamer.offer(try makeFrame(0))
        try await waitUntil { WalkStub.requests.contains { $0.httpMethod == "PUT" } }

        await streamer.discard()

        let delete = try XCTUnwrap(WalkStub.requests.last)
        XCTAssertEqual(delete.httpMethod, "DELETE")
        XCTAssertEqual(delete.url!.lastPathComponent, remoteID.uuidString)
        XCTAssertNil(ResumableUploadStore(captureDirectory: directory).scanID)
    }

    @MainActor
    func testTheUploadAfterTheWalkSkipsFramesAlreadyStreamed() async throws {
        let remoteID = UUID()
        WalkStub.handler = { request in
            if request.httpMethod == "POST", request.url!.path.hasSuffix("/complete") { return .scan(remoteID, state: .ready, status: 200) }
            if request.httpMethod == "POST" { return .scan(remoteID) }
            return .ok
        }
        let streamer = makeStreamer(gate: OpenGate(true))
        await streamer.start()
        streamer.offer(try makeFrame(0))
        streamer.offer(try makeFrame(1))
        try await waitUntil { WalkStub.requests.filter { $0.httpMethod == "PUT" }.count == 2 }
        await streamer.stop()
        WalkStub.reset()
        WalkStub.handler = { request in
            if request.url!.path.hasSuffix("/complete") { return .scan(remoteID, state: .ready, status: 200) }
            if request.httpMethod == "POST" { XCTFail("The walk's scan already exists") }
            return .ok
        }

        let model = UploadViewModel(scan: try makeScan(frameCount: 3), name: "Tea House", client: makeClient(),
            pollInterval: .milliseconds(5))
        XCTAssertEqual(model.scanID, remoteID)
        model.start()
        try await waitUntil { model.uploadedCount == model.totalCount }

        let sent = WalkStub.requests.filter { $0.httpMethod == "PUT" }.map { $0.url!.lastPathComponent }
        XCTAssertEqual(sent, ["room-metadata", "poses", "coverage", "room-usdz", "room-json", "frame-0002", "photo-manifest"])
        XCTAssertTrue(WalkStub.requests.allSatisfy { $0.value(forHTTPHeaderField: "X-Frame-Pose") == nil })
    }

    func testThePoseHeaderIsCompactAndNamesItsOwnFrame() throws {
        let frame = try makeFrame(12)
        let header = try ScanUploadClient.poseHeader(for: frame.pose)
        XCTAssertFalse(header.contains("\n"))
        XCTAssertLessThan(header.utf8.count, 4096)
        let decoded = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(header.utf8)) as? [String: Any])
        XCTAssertEqual(decoded["frame_id"] as? String, "frame-0012")
        XCTAssertEqual(decoded["image"] as? String, "frames/frame_0012.jpg")
    }

    func testOnlyAnUnmeteredNetworkStreams() {
        XCTAssertTrue(UnmeteredForegroundGate.allowsStreaming(isSatisfied: true, isExpensive: false, isConstrained: false))
        XCTAssertFalse(UnmeteredForegroundGate.allowsStreaming(isSatisfied: true, isExpensive: true, isConstrained: false))
        XCTAssertFalse(UnmeteredForegroundGate.allowsStreaming(isSatisfied: true, isExpensive: false, isConstrained: true))
        XCTAssertFalse(UnmeteredForegroundGate.allowsStreaming(isSatisfied: false, isExpensive: false, isConstrained: false))
    }

    // MARK: Helpers

    private func makeStreamer(gate: WalkStreamingGate, replaces: UUID? = nil, capacity: Int = 8) -> WalkFrameStreamer {
        WalkFrameStreamer(
            captureDirectory: directory,
            plan: WalkUploadPlan(client: makeClient(), name: "Tea House", replaces: replaces),
            gate: gate,
            capacity: capacity,
            retryDelays: [.milliseconds(5), .milliseconds(10)]
        )
    }

    private func makeClient() -> ScanUploadClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [WalkStub.self]
        return ScanUploadClient(baseURL: baseURL, session: URLSession(configuration: configuration), token: "owner")
    }

    private func makeFrame(_ number: Int) throws -> WalkFrame {
        let pose = PoseRecord.keyframe(
            frameNumber: number,
            timestamp: Double(number) * 0.5,
            transform: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, Float(number) * 0.2, 0, 0, 1],
            intrinsics: [1400, 0, 0, 0, 1400, 0, 960, 720, 1],
            orientation: "portrait",
            imageWidth: 1920,
            imageHeight: 1440,
            calibrationWidth: 1920,
            calibrationHeight: 1440
        )
        let fileURL = directory.appendingPathComponent(pose.image)
        try Data("jpeg \(number)".utf8).write(to: fileURL)
        return WalkFrame(fileURL: fileURL, pose: pose)
    }

    private func makeScan(frameCount: Int) throws -> CapturedScan {
        let core: [(String, ArtifactKind)] = [
            ("room-usdz", .roomUSDZ), ("room-json", .roomJSON), ("room-metadata", .roomMetadata),
            ("poses", .poses), ("coverage", .coverage), ("photo-manifest", .photoManifest)
        ]
        var artifacts = try core.map { id, kind in
            let fileURL = directory.appendingPathComponent("\(id).bin")
            try Data(id.utf8).write(to: fileURL)
            return CaptureArtifact(id: id, kind: kind, fileURL: fileURL)
        }
        let frames = try (0..<frameCount).map { number in
            let frame = try makeFrame(number)
            return CaptureArtifact(id: frame.pose.frameID!, kind: .frames, fileURL: frame.fileURL)
        }
        artifacts.insert(contentsOf: frames, at: artifacts.count - 1)
        return CapturedScan(id: UUID(), directory: directory, roomURL: artifacts[0].fileURL, duration: 30,
            artifacts: artifacts, name: "Tea House")
    }

    private func waitUntil(timeout: TimeInterval = 2, _ condition: @escaping () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() >= deadline {
                XCTFail("Timed out")
                return
            }
            try await Task.sleep(for: .milliseconds(10))
        }
    }
}

private final class OpenGate: WalkStreamingGate, @unchecked Sendable {
    let allowsStreaming: Bool
    init(_ allowsStreaming: Bool) { self.allowsStreaming = allowsStreaming }
}

private struct WalkStubResponse {
    let status: Int
    let data: Data
    var delay: TimeInterval = 0

    static let ok = WalkStubResponse(status: 201, data: Data("{}".utf8))

    static func status(_ code: Int) -> WalkStubResponse {
        WalkStubResponse(status: code, data: Data("{}".utf8))
    }

    static func scan(
        _ id: UUID,
        state: ScanState = .uploading,
        status: Int = 201,
        delay: TimeInterval = 0
    ) -> WalkStubResponse {
        let payload = #"{"id":"\#(id.uuidString)","state":"\#(state.rawValue)"}"#
        return WalkStubResponse(status: status, data: Data(payload.utf8), delay: delay)
    }
}

private final class WalkStub: URLProtocol {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var recorded: [URLRequest] = []
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> WalkStubResponse)?

    static var requests: [URLRequest] { lock.withLock { recorded } }

    static func reset() {
        lock.withLock {
            recorded = []
            handler = nil
        }
    }

    static func body(of request: URLRequest) -> [String: Any]? {
        guard let stream = request.httpBodyStream else {
            return request.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            guard count > 0 else { break }
            data.append(buffer, count: count)
        }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let handler = Self.lock.withLock {
            Self.recorded.append(request)
            return Self.handler
        }
        do {
            let result = try handler?(request) ?? .status(500)
            let response = HTTPURLResponse(url: request.url!, statusCode: result.status, httpVersion: nil,
                headerFields: ["Content-Type": "application/json"])!
            let box = WalkStubBox(value: self)
            let finish: @Sendable () -> Void = {
                let stub = box.value
                stub.client?.urlProtocol(stub, didReceive: response, cacheStoragePolicy: .notAllowed)
                stub.client?.urlProtocol(stub, didLoad: result.data)
                stub.client?.urlProtocolDidFinishLoading(stub)
            }
            if result.delay > 0 {
                DispatchQueue.global().asyncAfter(deadline: .now() + result.delay, execute: finish)
            } else {
                finish()
            }
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private struct WalkStubBox<Value>: @unchecked Sendable {
    let value: Value
}

/// Frames streamed during a walk reach a real API with their poses accepted.
/// Skipped unless a server is named:
///
///     TEST_RUNNER_SP_LIVE_API=http://127.0.0.1:8899 xcodebuild test ... \
///         -only-testing:StandardPhysicsTests/LiveWalkStreamingTests
@MainActor
final class LiveWalkStreamingTests: XCTestCase {
    func testFramesStreamedDuringTheWalkAreStoredOnTheServer() async throws {
        guard let address = ProcessInfo.processInfo.environment["SP_LIVE_API"], let server = URL(string: address) else {
            throw XCTSkip("Set SP_LIVE_API to run against a server")
        }
        setenv("API_BASE_URL", address, 1)
        let session = SessionStore()
        session.signOut()
        try await session.startGuest()
        let token = try XCTUnwrap(session.token)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory.appendingPathComponent("frames"),
            withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let client = ScanUploadClient(baseURL: server, token: token)
        let streamer = WalkFrameStreamer(captureDirectory: directory,
            plan: WalkUploadPlan(client: client, name: "Tea House", replaces: nil), gate: AlwaysOpen())
        await streamer.start()
        for number in 0..<3 { streamer.offer(try Self.frame(number, in: directory)) }
        streamer.finishOffering()
        let receipt = { ResumableUploadStore(captureDirectory: directory).completedArtifactIDs }
        for _ in 0..<100 where receipt().count < 3 { try await Task.sleep(for: .milliseconds(50)) }
        await streamer.stop()

        XCTAssertEqual(receipt(), ["frame-0000", "frame-0001", "frame-0002"])
        let scanID = try XCTUnwrap(ResumableUploadStore(captureDirectory: directory).scanID)
        var request = URLRequest(url: server.appendingPathComponent("api/scans/\(scanID.uuidString)"))
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, _) = try await URLSession.shared.data(for: request)
        let scan = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let stored = (scan["artifacts"] as? [[String: Any]] ?? []).compactMap { $0["id"] as? String }
        XCTAssertEqual(Set(stored), ["frame-0000", "frame-0001", "frame-0002"])
        XCTAssertEqual(scan["state"] as? String, "uploading")

        await WalkFrameStreamer(captureDirectory: directory,
            plan: WalkUploadPlan(client: client, name: "Tea House", replaces: nil), gate: AlwaysOpen()).discard()
        let (_, gone) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((gone as? HTTPURLResponse)?.statusCode, 404)
    }

    private static func frame(_ number: Int, in directory: URL) throws -> WalkFrame {
        let pose = PoseRecord.keyframe(frameNumber: number, timestamp: Double(number) * 0.5,
            transform: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, Float(number) * 0.3, 0, 0, 1],
            intrinsics: [1400, 0, 0, 0, 1400, 0, 960, 720, 1], orientation: "portrait",
            imageWidth: 1920, imageHeight: 1440, calibrationWidth: 1920, calibrationHeight: 1440)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 1920, height: 1440), format: format).image { context in
            UIColor(white: 0.2 + 0.2 * CGFloat(number), alpha: 1).setFill()
            context.fill(CGRect(x: 0, y: 0, width: 1920, height: 1440))
        }
        let fileURL = directory.appendingPathComponent(pose.image)
        try XCTUnwrap(image.jpegData(compressionQuality: 0.8)).write(to: fileURL)
        return WalkFrame(fileURL: fileURL, pose: pose)
    }
}

private final class AlwaysOpen: WalkStreamingGate, @unchecked Sendable {
    var allowsStreaming: Bool { true }
}
