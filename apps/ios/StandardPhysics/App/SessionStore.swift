import Foundation
import Security

/// The signed-in owner's session token, and the calls that get one.
///
/// The token goes in the Keychain rather than UserDefaults. UserDefaults is a
/// plist in the app container: readable from a backup of the phone, and not
/// protected when the device is locked. A credential belongs behind the
/// Keychain's `WhenUnlockedThisDeviceOnly`, which also keeps it from riding a
/// backup onto a different phone.
///
/// The token is scoped to the server it came from. Point the phone at a
/// different workspace and the old token is dropped rather than sent somewhere
/// it does not belong.
@MainActor
final class SessionStore: ObservableObject {
    struct Owner: Decodable, Equatable, Sendable {
        let email: String
        let shopName: String

        enum CodingKeys: String, CodingKey {
            case email
            case shopName = "shop_name"
        }
    }

    enum SignInError: LocalizedError {
        case noServer
        case refused(String)
        case unreachable

        var errorDescription: String? {
            switch self {
            case .noServer: "Set the upload address first, then sign in."
            case .refused(let reason): reason
            case .unreachable: "That server did not answer. Check the address and your Wi-Fi."
            }
        }
    }

    @Published private(set) var owner: Owner?

    private let service = "app.standardphysics.session"
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
        owner = token == nil ? nil : storedOwner
    }

    var isSignedIn: Bool { token != nil }

    /// The bearer token for the server the phone is pointed at, if there is one.
    var token: String? {
        guard let account = accountKey else { return nil }
        return Keychain.read(service: service, account: account)
    }

    func signIn(email: String, password: String) async throws {
        guard let baseURL = AppEnvironment.apiBaseURL, let account = accountKey else {
            throw SignInError.noServer
        }
        var request = URLRequest(url: baseURL.appendingPathComponent("api/auth/sign-in"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["email": email, "password": password])

        let (data, response) = try await dataOrUnreachable(for: request)
        guard let http = response as? HTTPURLResponse else { throw SignInError.unreachable }
        guard http.statusCode == 200 else { throw SignInError.refused(Self.reason(in: data, status: http.statusCode)) }

        guard let bearer = Self.bearerToken(in: response) else { throw SignInError.unreachable }
        let signedIn = try JSONDecoder().decode(Owner.self, from: data)
        Keychain.write(bearer, service: service, account: account)
        storedOwner = signedIn
        owner = signedIn
    }

    func signOut() {
        if let account = accountKey { Keychain.delete(service: service, account: account) }
        storedOwner = nil
        owner = nil
    }

    /// Called when the upload address changes: a token for one server is
    /// meaningless at another, and must never be sent there.
    func serverChanged() {
        owner = token == nil ? nil : storedOwner
    }

    private var accountKey: String? {
        AppEnvironment.apiBaseURL?.absoluteString
    }

    private var storedOwner: Owner? {
        get {
            guard let account = accountKey,
                  let data = UserDefaults.standard.data(forKey: "OWNER_\(account)") else { return nil }
            return try? JSONDecoder().decode(Owner.self, from: data)
        }
        set {
            guard let account = accountKey else { return }
            let key = "OWNER_\(account)"
            guard let newValue, let data = try? JSONEncoder().encode(newValue) else {
                UserDefaults.standard.removeObject(forKey: key)
                return
            }
            UserDefaults.standard.set(data, forKey: key)
        }
    }

    private func dataOrUnreachable(for request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch {
            throw SignInError.unreachable
        }
    }

    /// The API sets the session as a cookie. The phone keeps no cookie jar, so
    /// the same token is lifted out and sent back as a bearer header.
    static func bearerToken(in response: URLResponse) -> String? {
        guard let http = response as? HTTPURLResponse, let url = http.url else { return nil }
        let fields = http.allHeaderFields as? [String: String] ?? [:]
        let cookies = HTTPCookie.cookies(withResponseHeaderFields: fields, for: url)
        return cookies.first { $0.name == "sp_session" }?.value
    }

    private struct Problem: Decodable {
        let error: String
    }

    static func reason(in data: Data, status: Int) -> String {
        if let problem = try? JSONDecoder().decode(Problem.self, from: data), !problem.error.isEmpty {
            return problem.error.prefix(1).uppercased() + problem.error.dropFirst() + "."
        }
        return status == 429 ? "Too many tries. Wait a few minutes." : "That did not work. Try again."
    }
}

enum Keychain {
    static func read(service: String, account: String) -> String? {
        var result: AnyObject?
        let status = SecItemCopyMatching(query(service, account, returning: true) as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ value: String, service: String, account: String) {
        delete(service: service, account: account)
        var attributes = query(service, account, returning: false)
        attributes[kSecValueData as String] = Data(value.utf8)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        SecItemAdd(attributes as CFDictionary, nil)
    }

    static func delete(service: String, account: String) {
        SecItemDelete(query(service, account, returning: false) as CFDictionary)
    }

    private static func query(_ service: String, _ account: String, returning: Bool) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if returning {
            query[kSecReturnData as String] = true
            query[kSecMatchLimit as String] = kSecMatchLimitOne
        }
        return query
    }
}
