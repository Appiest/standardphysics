import Foundation

/// One thing the app asks the owner for, as `OwnerRequest` in
/// packages/contracts/standardphysics_contracts/owner.py describes it.
///
/// Kind and status stay strings on the wire so a value the phone has not
/// heard of yet is skipped rather than failing the whole list.
struct OwnerRequest: Decodable, Identifiable, Equatable, Sendable {
    let id: String
    let kind: String
    let timing: String
    let title: String
    let detail: String
    let unit: String?
    let status: String

    var isOpen: Bool { status == "open" }
    var isInShop: Bool { timing == "in_shop" }
    var isYesOrNo: Bool { kind == "yes_no" }
    var isPhoto: Bool { kind == "photo" }
    var isPushForce: Bool { kind == "number" && unit == "lb" }
}

/// Where one shop is, and the one next step, as `Journey` in the contracts.
struct Journey: Decodable, Identifiable, Equatable, Sendable {
    struct NextStep: Decodable, Equatable, Sendable {
        let kind: String
        let title: String
        let count: Int?
    }

    let scanID: UUID
    let shopName: String
    let stage: String
    let nextStep: NextStep
    let toolsUnlocked: Bool

    var id: UUID { scanID }

    enum CodingKeys: String, CodingKey {
        case scanID = "scan_id"
        case shopName = "shop_name"
        case stage
        case nextStep = "next_step"
        case toolsUnlocked = "tools_unlocked"
    }

    /// Still on the phone's side of the journey: walking, answering in the
    /// shop, or waiting for the measurements.
    var isBeforeResults: Bool { ["upload", "answers", "photos", "measuring", "failed"].contains(nextStep.kind) }
}

enum OwnerAPIError: LocalizedError, Equatable {
    case signedOut
    case refused(String)
    case unreachable

    var errorDescription: String? {
        switch self {
        case .signedOut: "Your session ended. Sign in again to keep going."
        case .refused(let reason): reason
        case .unreachable: "We couldn\u{2019}t reach Standard Physics. Check your connection and try again."
        }
    }
}

/// The owner's side of the API: the requests asked in the shop, the journey
/// behind the home card, and the device that gets notifications.
struct OwnerAPI: Sendable {
    let baseURL: URL
    let token: String
    var session: URLSession = .shared

    func requests(scanID: UUID) async throws -> [OwnerRequest] {
        struct Requests: Decodable { let requests: [OwnerRequest] }
        let data = try await send(scanPath(scanID, "requests"), method: "GET")
        return try JSONDecoder().decode(Requests.self, from: data).requests
    }

    func answer(scanID: UUID, requestID: String, yes: Bool) async throws {
        try await sendJSON(["yes": yes], to: requestPath(scanID, requestID, "answer"), method: "PUT")
    }

    func answer(scanID: UUID, requestID: String, number: Double) async throws {
        try await sendJSON(["number": number], to: requestPath(scanID, requestID, "answer"), method: "PUT")
    }

    func sendPhoto(scanID: UUID, requestID: String, jpeg: Data) async throws {
        var request = authorized(requestPath(scanID, requestID, "photo"), method: "PUT")
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        _ = try await perform(request, uploading: jpeg)
    }

    func skip(scanID: UUID, requestID: String) async throws {
        _ = try await send(requestPath(scanID, requestID, "skip"), method: "POST")
    }

    func journeys() async throws -> [Journey] {
        struct Journeys: Decodable { let journeys: [Journey] }
        let data = try await send(baseURL.appendingPathComponent("api/journeys"), method: "GET")
        return try JSONDecoder().decode(Journeys.self, from: data).journeys
    }

    func journey(scanID: UUID) async throws -> Journey {
        let data = try await send(scanPath(scanID, "journey"), method: "GET")
        return try JSONDecoder().decode(Journey.self, from: data)
    }

    func registerDevice(_ deviceToken: String, environment: String) async throws {
        let url = baseURL.appendingPathComponent("api/devices").appendingPathComponent(deviceToken)
        try await sendJSON(["environment": environment], to: url, method: "PUT")
    }

    private func scanPath(_ scanID: UUID, _ leaf: String) -> URL {
        baseURL.appendingPathComponent("api/scans")
            .appendingPathComponent(scanID.uuidString.lowercased())
            .appendingPathComponent(leaf)
    }

    private func requestPath(_ scanID: UUID, _ requestID: String, _ leaf: String) -> URL {
        scanPath(scanID, "requests").appendingPathComponent(requestID).appendingPathComponent(leaf)
    }

    private func authorized(_ url: URL, method: String) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    private func sendJSON<Body: Encodable>(_ body: Body, to url: URL, method: String) async throws {
        var request = authorized(url, method: method)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        _ = try await perform(request)
    }

    private func send(_ url: URL, method: String) async throws -> Data {
        try await perform(authorized(url, method: method))
    }

    private func perform(_ request: URLRequest, uploading body: Data? = nil) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            if let body {
                (data, response) = try await session.upload(for: request, from: body)
            } else {
                (data, response) = try await session.data(for: request)
            }
        } catch {
            throw OwnerAPIError.unreachable
        }
        guard let http = response as? HTTPURLResponse else { throw OwnerAPIError.unreachable }
        if http.statusCode == 401 { throw OwnerAPIError.signedOut }
        guard (200..<300).contains(http.statusCode) else {
            throw OwnerAPIError.refused(SessionStore.reason(in: data, status: http.statusCode))
        }
        return data
    }
}
