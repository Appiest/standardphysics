import XCTest
@testable import StandardPhysics

/// The owner's first run against a real API, from a guest account to the
/// measuring wait. Skipped unless a server is named:
///
///     TEST_RUNNER_SP_LIVE_API=http://127.0.0.1:8798 xcodebuild test ... \
///         -only-testing:StandardPhysicsTests/LiveOwnerFlowTests
@MainActor
final class LiveOwnerFlowTests: XCTestCase {
    private var server: URL!

    override func setUp() async throws {
        guard let address = ProcessInfo.processInfo.environment["SP_LIVE_API"], let url = URL(string: address) else {
            throw XCTSkip("Set SP_LIVE_API to run against a server")
        }
        server = url
        setenv("API_BASE_URL", address, 1)
        SessionStore().signOut()
    }

    func testAGuestWalksAnswersSavesAndKeepsTheShop() async throws {
        let session = SessionStore()
        try await session.startGuest()
        let token = try XCTUnwrap(session.token)
        XCTAssertEqual(session.owner?.guest, true)
        XCTAssertFalse(session.hasSavedAccount)

        let scanID = try await uploadPhoneWalk(token: token)
        let app = AppModel(canScan: true)
        let setup = ShopSetupModel(scanID: scanID, app: app)
        await setup.refresh()
        let asked = try await walkThroughRequests(setup)
        XCTAssertEqual(asked.first, "restroom")
        XCTAssertFalse(asked.contains("restroom_turning_space"), "a no to the restroom closes its photo")
        XCTAssertEqual(setup.step, .measuring)

        let api = try XCTUnwrap(app.api())
        let requests = try await api.requests(scanID: scanID)
        XCTAssertEqual(requests.first { $0.id == "restroom_turning_space" }?.status, "not_applicable")
        XCTAssertEqual(requests.first { $0.id == "door_hardware" }?.status, "answered")
        let journeys = try await api.journeys()
        XCTAssertEqual(journeys.first?.scanID, scanID)
        try await api.registerDevice(String(repeating: "ab", count: 32), environment: PushRegistration.environment)

        let email = "phone-\(UUID().uuidString.prefix(8).lowercased())@example.com"
        try await session.save(email: email, password: "a long password")
        XCTAssertTrue(session.hasSavedAccount)
        XCTAssertEqual(session.token, token, "saving keeps the same session")

        session.signOut()
        try await session.startGuest()
        do {
            try await session.save(email: email, password: "a long password")
            XCTFail("the email is taken")
        } catch SessionStore.SaveError.emailTaken {}
        try await session.signIn(email: email, password: "a long password")
        let signedIn = try XCTUnwrap(app.api())
        let shops = try await signedIn.journeys().map(\.scanID)
        XCTAssertTrue(shops.contains(scanID))
    }

    /// Answers everything the phone asks, the way an owner might: no restroom,
    /// yes to inside doors, one photo sent, the rest skipped, and a push force.
    private func walkThroughRequests(_ setup: ShopSetupModel) async throws -> [String] {
        var asked: [String] = []
        for _ in 0..<12 {
            switch setup.step {
            case .question(let request):
                asked.append(request.id)
                setup.answer(request.id != "restroom")
            case .photo(let request):
                asked.append(request.id)
                if request.id == "door_hardware" {
                    setup.sendPhoto(try XCTUnwrap(PhotoEncoding.jpeg(from: samplePhoto())))
                } else {
                    setup.skip()
                }
            case .pushForce(let request):
                asked.append(request.id)
                setup.savePushForce(4.5)
            case .measuring, .preparing:
                return asked
            }
            try await waitForTheAnswer(setup)
            XCTAssertNil(setup.problem)
        }
        return asked
    }

    private func waitForTheAnswer(_ setup: ShopSetupModel) async throws {
        try await Task.sleep(for: .milliseconds(50))
        for _ in 0..<200 where setup.isSending {
            try await Task.sleep(for: .milliseconds(50))
        }
    }

    private func samplePhoto() -> UIImage {
        UIGraphicsImageRenderer(size: CGSize(width: 800, height: 600)).image { context in
            UIColor.darkGray.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 800, height: 600))
        }
    }

    /// The test1 walk from datasets/phone, uploaded the way the phone does.
    private func uploadPhoneWalk(token: String) async throws -> UUID {
        let folder = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../../datasets/phone/test1").standardizedFileURL
        let client = ScanUploadClient(baseURL: server, token: token)
        let remote = try await client.createScan(name: "Tea House", duration: 120)
        let artifacts: [(String, ArtifactKind, String)] = [
            ("room-metadata", .roomMetadata, "room.metadata.plist"),
            ("poses", .poses, "poses.json"),
            ("coverage", .coverage, "coverage.json"),
            ("room-usdz", .roomUSDZ, "room.usdz"),
            ("room-json", .roomJSON, "room.json"),
        ]
        for (id, kind, file) in artifacts {
            try await client.upload(CaptureArtifact(id: id, kind: kind, fileURL: folder.appendingPathComponent(file)), to: remote.id)
        }
        _ = try await client.complete(scanID: remote.id)
        return remote.id
    }
}
