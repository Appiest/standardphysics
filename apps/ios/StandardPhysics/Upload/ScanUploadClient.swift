import CryptoKit
import Foundation

enum ScanState: String, Codable, Equatable, Sendable {
    case uploading
    case measuring
    case checking
    case ready
    case failed

    var displayText: String {
        switch self {
        case .uploading: "Uploading"
        case .measuring: "Measuring your shop"
        case .checking: "Checking"
        case .ready: "Ready"
        case .failed: "Try the upload again"
        }
    }
}

struct RemoteScan: Decodable, Equatable, Sendable {
    let id: UUID
    let state: ScanState
}

struct ScanUploadClient {
    private struct CreateScanRequest: Encodable {
        let name: String
        let deviceModel: String
        let durationSeconds: TimeInterval

        enum CodingKeys: String, CodingKey {
            case name
            case deviceModel = "device_model"
            case durationSeconds = "duration_seconds"
        }
    }

    let baseURL: URL
    let session: URLSession
    /// The signed-in owner's session. Every scan route needs one; without it
    /// the server cannot tell whose shop this is and answers 401.
    let token: String?

    init(baseURL: URL, session: URLSession = .shared, token: String? = nil) {
        self.baseURL = baseURL
        self.session = session
        self.token = token
    }

    private func authorized(_ url: URL, method: String) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return request
    }

    func createScan(name: String, duration: TimeInterval) async throws -> RemoteScan {
        var request = authorized(baseURL.appendingPathComponent("api/scans"), method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(CreateScanRequest(
            name: name,
            deviceModel: DeviceModel.current,
            durationSeconds: duration
        ))
        return try await send(request, expectedStatus: 201)
    }

    func upload(_ artifact: CaptureArtifact, to scanID: UUID) async throws {
        let url = baseURL
            .appendingPathComponent("api/scans")
            .appendingPathComponent(scanID.uuidString)
            .appendingPathComponent("artifacts")
            .appendingPathComponent(artifact.id)
        var request = authorized(url, method: "PUT")
        request.setValue(try SHA256Digest.hexDigest(of: artifact.fileURL), forHTTPHeaderField: "X-Checksum-SHA256")
        request.setValue(artifact.kind.rawValue, forHTTPHeaderField: "X-Artifact-Kind")
        let (_, response) = try await session.upload(for: request, fromFile: artifact.fileURL)
        try validate(response, expectedStatus: 200...201)
    }

    func complete(scanID: UUID) async throws -> RemoteScan {
        let request = authorized(
            baseURL.appendingPathComponent("api/scans")
                .appendingPathComponent(scanID.uuidString)
                .appendingPathComponent("complete"),
            method: "POST"
        )
        return try await send(request, expectedStatus: 200, expectedScanID: scanID)
    }

    func scan(id: UUID) async throws -> RemoteScan {
        let request = authorized(
            baseURL.appendingPathComponent("api/scans").appendingPathComponent(id.uuidString),
            method: "GET"
        )
        return try await send(request, expectedStatus: 200, expectedScanID: id)
    }

    private func send(
        _ request: URLRequest,
        expectedStatus: Int,
        expectedScanID: UUID? = nil
    ) async throws -> RemoteScan {
        let (data, response) = try await session.data(for: request)
        try validate(response, expectedStatus: expectedStatus...expectedStatus)
        let remote = try JSONDecoder().decode(RemoteScan.self, from: data)
        if let expectedScanID, remote.id != expectedScanID {
            throw UploadClientError.mismatchedScanIdentifier
        }
        return remote
    }

    private func validate(_ response: URLResponse, expectedStatus: ClosedRange<Int>) throws {
        guard let response = response as? HTTPURLResponse else {
            throw UploadClientError.unexpectedResponse
        }
        if response.statusCode == 401 {
            throw UploadClientError.signedOut
        }
        if response.statusCode == 404 || response.statusCode == 410 {
            throw UploadClientError.remoteScanMissing
        }
        guard expectedStatus.contains(response.statusCode) else {
            throw UploadClientError.unexpectedResponse
        }
    }

    /// Removes a scan from the server. A scan that is already gone counts as
    /// deleted: the phone asked for it to not be there, and it is not there.
    func delete(id: UUID) async throws {
        let request = authorized(baseURL.appendingPathComponent("api/scans/\(id.uuidString)"), method: "DELETE")
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { return }
        guard http.statusCode == 204 || http.statusCode == 404 else {
            throw UploadClientError.unexpectedResponse
        }
    }
}

enum SHA256Digest {
    static func hexDigest(of fileURL: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: fileURL)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let data = try handle.read(upToCount: 1_048_576), !data.isEmpty {
            hasher.update(data: data)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

enum UploadClientError: Error, Equatable {
    case unexpectedResponse
    case mismatchedScanIdentifier
    case remoteScanMissing
    /// The session expired or was signed out elsewhere. Retrying the upload
    /// will not help; the owner has to sign in again.
    case signedOut
}

private enum DeviceModel {
    static var current: String {
        var systemInfo = utsname()
        uname(&systemInfo)
        let mirror = Mirror(reflecting: systemInfo.machine)
        return mirror.children.reduce(into: "") { result, element in
            guard let value = element.value as? Int8, value != 0 else { return }
            result.append(Character(UnicodeScalar(UInt8(value))))
        }
    }
}
