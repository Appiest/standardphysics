import SwiftUI

struct ConnectionScreen: View {
    @ObservedObject var model: AppModel
    @State private var api = AppEnvironment.apiBaseURL?.absoluteString ?? ""
    @State private var workspace = AppEnvironment.workspaceBaseURL?.absoluteString ?? ""
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Connect this phone") {
                    addressField("Upload address", value: $api)
                    addressField("Workspace address", value: $workspace)
                    Text("Use your Mac’s network address for a local server. Keep both devices on the same Wi-Fi.")
                        .foregroundStyle(AppTheme.mutedInk)
                }
                if error != nil {
                    ProgressView()
                        .tint(AppTheme.accent)
                        .accessibilityLabel("Working")
                }
                Button("Save connection") {
                    do {
                        try AppEnvironment.save(api: api, workspace: workspace)
                        model.connectionChanged()
                    } catch { self.error = error.localizedDescription }
                }
                .buttonStyle(AppButtonStyle())
            }
            .navigationTitle("Connection")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Back") { model.showStart() }
                }
            }
        }
    }

    private func addressField(_ title: String, value: Binding<String>) -> some View {
        TextField(title, text: value)
            .keyboardType(.URL)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .accessibilityLabel(title)
    }
}
