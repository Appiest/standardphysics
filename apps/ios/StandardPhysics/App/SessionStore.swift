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
    /// Who is signed in, as the server's `Session` describes them.
    ///
    /// Everything past the shop name is optional on the wire: a phone that
    /// signed in before roles existed has an owner saved without them, and an
    /// owner is the safe reading of a missing role.
    struct Owner: Codable, Equatable, Sendable {
        enum Role: String, Codable, Sendable { case owner, team }

        let email: String?
        let shopName: String
        let role: Role
        let guest: Bool
        let deletesAt: String?

        enum CodingKeys: String, CodingKey {
            case email
            case shopName = "shop_name"
            case role
            case guest
            case deletesAt = "deletes_at"
        }

        init(email: String?, shopName: String, role: Role = .owner, guest: Bool = false, deletesAt: String? = nil) {
            self.email = email
            self.shopName = shopName
            self.role = role
            self.guest = guest
            self.deletesAt = deletesAt
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            email = try container.decodeIfPresent(String.self, forKey: .email)
            shopName = try container.decodeIfPresent(String.self, forKey: .shopName) ?? ""
            role = (try? container.decodeIfPresent(Role.self, forKey: .role)) ?? .owner
            guest = try container.decodeIfPresent(Bool.self, forKey: .guest) ?? false
            deletesAt = try container.decodeIfPresent(String.self, forKey: .deletesAt)
        }

        var isTeam: Bool { role == .team }
    }

    enum ServerError: LocalizedError {
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
    /// Goes up whenever this phone's token changes, so work already running
    /// under the old one can move to the new one.
    @Published private(set) var credentialChanges = 0

    private let service = "app.standardphysics.session"
    private let session: URLSession

    init(session: URLSession = .api) {
        self.session = session
        owner = token == nil ? nil : storedOwner
    }

    var isSignedIn: Bool { token != nil }

    /// Signed in to an account with an email or an Apple ID, not a guest.
    var hasSavedAccount: Bool { owner.map { !$0.guest } ?? false }

    /// The bearer token for the server the phone is pointed at, if there is one.
    var token: String? {
        guard let account = accountKey else { return nil }
        return Keychain.read(service: service, account: account)
    }

    /// Signs in to an account that already exists.
    ///
    /// The guest's token rides along, so the server moves the shops walked as
    /// a guest into the account being signed in to.
    func signIn(email: String, password: String) async throws {
        let request = try post("api/auth/sign-in", body: ["email": email, "password": password])
        try await openSession(with: request, expecting: 200)
    }

    /// Makes a guest account so the first walk can upload with no sign-in.
    ///
    /// The server answers with whoever is already signed in when the phone
    /// still has a token, so calling this twice is harmless.
    func startGuest() async throws {
        let request = try post("api/auth/guest", body: [String: String]())
        try await openSession(with: request, expecting: 201)
    }

    /// Keeps the guest account under an email and password. The token stays
    /// the same; only who it belongs to changes.
    func save(email: String, password: String) async throws {
        let request = try post("api/auth/save", body: ["email": email, "password": password])
        let (data, response) = try await dataOrUnreachable(for: request)
        guard let http = response as? HTTPURLResponse else { throw ServerError.unreachable }
        if http.statusCode == 409 { throw SaveError.emailTaken(Self.reason(in: data, status: 409)) }
        guard http.statusCode == 200 else { throw ServerError.refused(Self.reason(in: data, status: http.statusCode)) }
        let saved = try JSONDecoder().decode(Owner.self, from: data)
        storedOwner = saved
        owner = saved
    }

    /// Signs in with Apple. A guest's shops move into the Apple account, or
    /// the guest becomes it.
    func signInWithApple(identityToken: String, fullName: String?) async throws {
        var body = ["identity_token": identityToken]
        if let fullName, !fullName.isEmpty { body["full_name"] = fullName }
        let request = try post("api/auth/apple", body: body)
        try await openSession(with: request, expecting: 200)
    }

    enum SaveError: LocalizedError {
        case emailTaken(String)

        var errorDescription: String? {
            switch self {
            case .emailTaken(let reason): reason
            }
        }
    }

    private func post(_ path: String, body: [String: String]) throws -> URLRequest {
        guard let baseURL = AppEnvironment.apiBaseURL else { throw ServerError.noServer }
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        request.httpBody = try JSONEncoder().encode(body)
        return request
    }

    /// Sends a call that answers with a `Session` and sets a new cookie, then
    /// keeps the cookie's token as this phone's credential.
    private func openSession(with request: URLRequest, expecting status: Int) async throws {
        guard let account = accountKey else { throw ServerError.noServer }
        let (data, response) = try await dataOrUnreachable(for: request)
        guard let http = response as? HTTPURLResponse else { throw ServerError.unreachable }
        guard http.statusCode == status else { throw ServerError.refused(Self.reason(in: data, status: http.statusCode)) }
        guard let bearer = Self.bearerToken(in: response) else { throw ServerError.unreachable }
        let signedIn = try JSONDecoder().decode(Owner.self, from: data)
        Keychain.write(bearer, service: service, account: account)
        storedOwner = signedIn
        owner = signedIn
        credentialChanges += 1
    }

#if DEBUG
    /// Signs this phone in with a token made elsewhere, so a Debug build can
    /// open a real shop's screens without typing on the simulator.
    func adoptForDebugging(token: String) {
        guard let account = accountKey else { return }
        Keychain.write(token, service: service, account: account)
        Task { await refresh() }
    }
#endif

    /// Asks the server who this token belongs to now.
    ///
    /// A role can change after sign-in, and a phone signed in before roles
    /// existed has none saved. A refused token is forgotten, since every call
    /// made with it would be refused too; a server that cannot be reached
    /// changes nothing.
    func refresh() async {
        guard let baseURL = AppEnvironment.apiBaseURL, let token, let account = accountKey else { return }
        var request = URLRequest(url: baseURL.appendingPathComponent("api/auth/session"))
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { return }
        if http.statusCode == 401 {
            Keychain.delete(service: service, account: account)
            storedOwner = nil
            owner = nil
            return
        }
        guard http.statusCode == 200, let current = try? JSONDecoder().decode(Owner.self, from: data) else { return }
        storedOwner = current
        owner = current
    }

    /// Ends the account on the server, then forgets it here.
    ///
    /// The server erases the shops and their scans in the same call, so there
    /// is nothing left to sign back in to. The local copy is cleared by the
    /// caller, which owns the list on screen.
    func deleteAccount() async throws {
        guard let baseURL = AppEnvironment.apiBaseURL, let token else { throw ServerError.noServer }
        var request = URLRequest(url: baseURL.appendingPathComponent("api/account"))
        request.httpMethod = "DELETE"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await dataOrUnreachable(for: request)
        guard let http = response as? HTTPURLResponse else { throw ServerError.unreachable }
        guard http.statusCode == 204 else {
            throw ServerError.refused(Self.reason(in: data, status: http.statusCode))
        }
        signOut()
    }

    func signOut() {
        if let account = accountKey { Keychain.delete(service: service, account: account) }
        storedOwner = nil
        owner = nil
        credentialChanges += 1
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
            throw ServerError.unreachable
        }
    }

    /// The API sets the session as a cookie. The phone keeps no cookie jar, so
    /// the same token is lifted out and sent back as a bearer header.
    nonisolated static func bearerToken(in response: URLResponse) -> String? {
        guard let http = response as? HTTPURLResponse, let url = http.url else { return nil }
        let fields = http.allHeaderFields as? [String: String] ?? [:]
        let cookies = HTTPCookie.cookies(withResponseHeaderFields: fields, for: url)
        return cookies.first { $0.name == "sp_session" }?.value
    }

    private struct Problem: Decodable {
        let error: String
    }

    nonisolated static func reason(in data: Data, status: Int) -> String {
        if let problem = try? JSONDecoder().decode(Problem.self, from: data), !problem.error.isEmpty {
            return problem.error.prefix(1).uppercased() + problem.error.dropFirst() + "."
        }
        return status == 429 ? "Too many tries. Wait a few minutes." : "That did not work. Try again."
    }
}

extension URLSession {
    /// Every call to our API. The token travels as a bearer header and
    /// nowhere else.
    ///
    /// The shared session keeps a cookie jar, so the `sp_session` cookie from
    /// a sign-in rode along on every later call, including after signing out:
    /// a guest made after sign-out was answered as the account just left, and
    /// with no new cookie the phone could not sign in at all.
    static let api: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.httpCookieAcceptPolicy = .never
        return URLSession(configuration: configuration)
    }()
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
