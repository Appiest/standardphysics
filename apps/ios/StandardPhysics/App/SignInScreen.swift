import SwiftUI

/// Sign in so the server knows whose shop this is.
///
/// There is no account creation here. Opening an account asks for a shop name
/// and an email that has to be typed accurately, which is the workspace's job
/// on a real keyboard; the phone's job is to scan.
struct SignInScreen: View {
    @ObservedObject var model: AppModel
    @ObservedObject var session: SessionStore

    @State private var email = ""
    @State private var password = ""
    @State private var error: String?
    @State private var working = false

    private var canSubmit: Bool {
        !working && !email.trimmingCharacters(in: .whitespaces).isEmpty && !password.isEmpty
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Sign in") {
                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Password", text: $password)
                        .textContentType(.password)
                    Text("Use the same email and password as your workspace.")
                        .foregroundStyle(AppTheme.mutedInk)
                }
                if let error {
                    Text(error)
                        .foregroundStyle(AppTheme.warning)
                        .accessibilityAddTraits(.isStaticText)
                }
                Button(working ? "Signing in" : "Sign in") { submit() }
                    .buttonStyle(AppButtonStyle())
                    .disabled(!canSubmit)
            }
            .navigationTitle("Sign in")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Connection") { model.screen = .connection }
                }
            }
        }
    }

    private func submit() {
        working = true
        error = nil
        Task {
            do {
                try await session.signIn(email: email, password: password)
                password = ""
                model.showStart()
            } catch {
                self.error = error.localizedDescription
            }
            working = false
        }
    }
}
