import AuthenticationServices
import SwiftUI

/// Sign in to an account that already exists, with Apple or with an email.
///
/// There is no account creation here: a first walk makes a guest account on
/// its own, and the measuring wait is where it gets saved. A guest who signs
/// in brings the shops walked on this phone along.
struct SignInScreen: View {
    @ObservedObject var model: AppModel
    @ObservedObject var session: SessionStore

    @State private var email = ""
    @State private var password = ""
    @State private var error: String?
    @State private var working = false
    @FocusState private var focused: Field?

    private enum Field { case email, password }

    private var canSubmit: Bool {
        !working && !email.trimmingCharacters(in: .whitespaces).isEmpty && !password.isEmpty
    }

    var body: some View {
        FlowPage(back: { model.leaveSignIn() }) {
            FlowTitle("Sign in")
            SignInWithAppleButton(.signIn) { request in
                request.requestedScopes = [.fullName, .email]
            } onCompletion: { result in
                finishApple(result)
            }
            .signInWithAppleButtonStyle(.black)
            .frame(height: 52)
            .clipShape(RoundedRectangle(cornerRadius: AppTheme.Radius.control, style: .continuous))
            VStack(alignment: .leading, spacing: AppTheme.Spacing.small) {
                TextField("Email", text: $email)
                    .keyboardType(.emailAddress)
                    .textContentType(.username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focused, equals: .email)
                    .submitLabel(.next)
                    .onSubmit { focused = .password }
                    .fieldSurface(focused: focused == .email)
                SecureField("Password", text: $password)
                    .textContentType(.password)
                    .focused($focused, equals: .password)
                    .submitLabel(.go)
                    .onSubmit { if canSubmit { submit() } }
                    .fieldSurface(focused: focused == .password)
                Text("Use the email and password you saved your shop with.")
                    .font(AppTheme.Typography.secondary)
                    .foregroundStyle(AppTheme.mutedInk)
            }
            if let error { FlowProblem(message: error) }
        } actions: {
            Button(working ? "Signing in" : "Sign in with email") { submit() }
                .buttonStyle(AppButtonStyle())
                .disabled(!canSubmit)
            if !AppEnvironment.addressesAreCompiledIn {
                Button("Change the upload address") { model.screen = .connection }
                    .buttonStyle(AppButtonStyle(.link))
            }
        }
    }

    private func submit() {
        run { try await session.signIn(email: email, password: password) }
    }

    private func finishApple(_ result: Result<ASAuthorization, Error>) {
        switch result {
        case .success(let authorization):
            guard let credential = AppleCredential(authorization) else {
                error = "Sign in with Apple didn\u{2019}t go through. Try again, or sign in with email."
                return
            }
            run {
                try await session.signInWithApple(identityToken: credential.identityToken, fullName: credential.fullName)
            }
        case .failure(let failure):
            guard (failure as? ASAuthorizationError)?.code != .canceled else { return }
            error = "Sign in with Apple didn\u{2019}t go through. Try again, or sign in with email."
        }
    }

    private func run(_ signIn: @escaping () async throws -> Void) {
        working = true
        error = nil
        Task {
            do {
                try await signIn()
                password = ""
                model.didSignIn()
            } catch {
                self.error = error.localizedDescription
            }
            working = false
        }
    }
}
