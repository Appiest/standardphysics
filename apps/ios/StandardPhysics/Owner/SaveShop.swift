import AuthenticationServices
import SwiftUI

/// What Sign in with Apple hands back that the server needs.
struct AppleCredential {
    let identityToken: String
    let fullName: String?

    init?(_ authorization: ASAuthorization) {
        guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
              let data = credential.identityToken,
              let token = String(data: data, encoding: .utf8) else { return nil }
        identityToken = token
        let name = credential.fullName.map { PersonNameComponentsFormatter().string(from: $0) }
        fullName = name?.isEmpty == false ? name : nil
    }
}

/// Sign in with Apple without a button on screen, for the web view asking to
/// save a report.
@MainActor
final class AppleSignInPrompt: NSObject, ASAuthorizationControllerDelegate,
    ASAuthorizationControllerPresentationContextProviding {
    private var continuation: CheckedContinuation<AppleCredential, Error>?
    private weak var window: UIWindow?

    enum Failure: Error { case cancelled, unreadable }

    func run(over window: UIWindow?) async throws -> AppleCredential {
        self.window = window
        let request = ASAuthorizationAppleIDProvider().createRequest()
        request.requestedScopes = [.fullName, .email]
        let controller = ASAuthorizationController(authorizationRequests: [request])
        controller.delegate = self
        controller.presentationContextProvider = self
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            controller.performRequests()
        }
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        window ?? ASPresentationAnchor()
    }

    func authorizationController(controller: ASAuthorizationController,
                                 didCompleteWithAuthorization authorization: ASAuthorization) {
        guard let credential = AppleCredential(authorization) else {
            continuation?.resume(throwing: Failure.unreadable)
            continuation = nil
            return
        }
        continuation?.resume(returning: credential)
        continuation = nil
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        continuation?.resume(throwing: Failure.cancelled)
        continuation = nil
    }
}

/// The two ways to keep a guest's shop: Sign in with Apple, or an email and
/// a password. Used during the measuring wait and on home.
struct SaveShopOptions: View {
    @ObservedObject var session: SessionStore
    let saved: () -> Void
    @State private var savingWithEmail = false
    @State private var problem: String?

    var body: some View {
        VStack(spacing: AppTheme.Spacing.small) {
            SignInWithAppleButton(.continue) { request in
                request.requestedScopes = [.fullName, .email]
            } onCompletion: { result in
                finishApple(result)
            }
            .signInWithAppleButtonStyle(.black)
            .frame(height: 52)
            .clipShape(RoundedRectangle(cornerRadius: AppTheme.Radius.control, style: .continuous))
            Button("Save with email") { savingWithEmail = true }
                .buttonStyle(AppButtonStyle(.secondary))
            if let problem { FlowProblem(message: problem) }
        }
        .sheet(isPresented: $savingWithEmail) {
            EmailSaveSheet(session: session) {
                savingWithEmail = false
                saved()
            }
        }
    }

    private func finishApple(_ result: Result<ASAuthorization, Error>) {
        guard case .success(let authorization) = result else {
            if case .failure(let error) = result, (error as? ASAuthorizationError)?.code != .canceled {
                problem = "Sign in with Apple didn\u{2019}t go through. Try again, or save with email."
            }
            return
        }
        guard let credential = AppleCredential(authorization) else {
            problem = "Sign in with Apple didn\u{2019}t go through. Try again, or save with email."
            return
        }
        Task {
            do {
                try await session.signInWithApple(identityToken: credential.identityToken, fullName: credential.fullName)
                problem = nil
                saved()
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

/// An email and a password for the guest's account. When the email already
/// has an account, signing in to it moves this shop there.
struct EmailSaveSheet: View {
    @ObservedObject var session: SessionStore
    let saved: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var email = ""
    @State private var password = ""
    @State private var problem: String?
    @State private var emailTaken = false
    @State private var working = false

    private static let minimumPassword = 10

    private var canSubmit: Bool {
        !working && email.contains("@") && password.count >= Self.minimumPassword
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Password", text: $password)
                        .textContentType(emailTaken ? .password : .newPassword)
                } footer: {
                    Text("Use at least \(Self.minimumPassword) characters. You\u{2019}ll sign in with these on the web too.")
                        .font(AppTheme.Typography.secondary)
                }
                if let problem {
                    FlowProblem(message: problem)
                }
                if emailTaken {
                    Button(working ? "Signing in" : "Sign in with this email") { signIn() }
                        .buttonStyle(AppButtonStyle())
                        .disabled(working || password.isEmpty)
                } else {
                    Button(working ? "Saving" : "Save your shop") { save() }
                        .buttonStyle(AppButtonStyle())
                        .disabled(!canSubmit)
                }
            }
            .scrollContentBackground(.hidden)
            .background(DraftingPaper())
            .navigationTitle("Save with email")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private func save() {
        run {
            do {
                try await session.save(email: email, password: password)
                saved()
            } catch SessionStore.SaveError.emailTaken(let reason) {
                emailTaken = true
                problem = reason
            }
        }
    }

    private func signIn() {
        run {
            try await session.signIn(email: email, password: password)
            saved()
        }
    }

    private func run(_ work: @escaping () async throws -> Void) {
        working = true
        problem = nil
        Task {
            do { try await work() } catch { problem = error.localizedDescription }
            working = false
        }
    }
}
