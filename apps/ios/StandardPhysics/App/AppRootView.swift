import RoomPlan
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    enum Screen {
        case start
        case capture
        case review(CapturedScan)
        case upload(UploadViewModel)
        case workspace(UUID)
        case connection
        case signIn
    }

    let session = SessionStore()
    @Published var screen: Screen = .start
    @Published private(set) var savedScans = CaptureLibrary.all()
    @Published var deletionMessage: String?
    @Published var accountDeletionMessage: String?
    @Published private(set) var captureSessionID = UUID()
    @Published private(set) var recoveryDirectories: [URL] = []
    @Published private(set) var recoveryMessage: String?
    private var uploads: [UUID: UploadViewModel] = [:]

    func beginCapture() {
        captureSessionID = UUID()
        screen = .capture
    }

    func showStart() {
        savedScans = CaptureLibrary.all()
        screen = .start
    }

    func connectionChanged() {
        uploads.values.forEach { $0.cancel() }
        uploads.removeAll()
        // A token is only good at the server that issued it.
        session.serverChanged()
        showStart()
    }

    func signOut() {
        uploads.values.forEach { $0.cancel() }
        uploads.removeAll()
        session.signOut()
        screen = .signIn
    }

    /// Ends the account, then clears what this phone was holding for it.
    ///
    /// The server goes first. If it refuses, the scans are still on the phone
    /// and still on the server, and the owner can try again knowing nothing
    /// was half-done.
    func deleteAccount() async {
        do {
            try await session.deleteAccount()
        } catch {
            accountDeletionMessage = error.localizedDescription
            return
        }
        uploads.values.forEach { $0.cancel() }
        uploads.removeAll()
        CaptureLibrary.all().forEach { try? CaptureLibrary.remove($0) }
        savedScans = CaptureLibrary.all()
        accountDeletionMessage = nil
        screen = .signIn
    }

    func recoverSavedRoom(_ directory: URL) async {
        recoveryMessage = "Saving your room"
        let result = await Task.detached(priority: .userInitiated) {
            Result { try CaptureRecovery.recover(from: directory) }
        }.value
        switch result {
        case .success(let scan):
            recoveryMessage = nil
            recoveryDirectories.removeAll { $0 == directory }
            screen = .review(scan)
        case .failure: recoveryMessage = "Free some space on this phone, then save again."
        }
    }

    func upload(scan: CapturedScan, name: String) {
        guard let baseURL = AppEnvironment.apiBaseURL else {
            screen = .connection
            return
        }
        guard let token = session.token else {
            screen = .signIn
            return
        }
        if let existing = uploads[scan.id], existing.totalCount == scan.artifacts.count {
            screen = .upload(existing)
            return
        }
        uploads[scan.id]?.cancel()
        let model = UploadViewModel(
            scan: scan,
            name: name,
            client: ScanUploadClient(baseURL: baseURL, token: token)
        )
        uploads[scan.id] = model
        model.start()
        screen = .upload(model)
    }

    /// Removes a scan from this phone, and from the server if it got there.
    ///
    /// The phone is cleared first and the server is not reported on. The owner
    /// asked for the scan to be gone, and where the bytes were is our problem:
    /// a line about a server still holding a copy answers a question nobody
    /// asked and leaves them worrying about it.
    func deleteScan(_ scan: CapturedScan) async {
        let remoteID = ResumableUploadStore(captureDirectory: scan.directory).scanID
        uploads[scan.id] = nil
        do { try CaptureLibrary.remove(scan) } catch {
            deletionMessage = "That scan could not be removed. Try again."
            return
        }
        savedScans = CaptureLibrary.all()

        guard let remoteID, let baseURL = AppEnvironment.apiBaseURL else { return }
        try? await ScanUploadClient(baseURL: baseURL, token: session.token).delete(id: remoteID)
    }

    func refreshSavedScanStates() async {
        if let root = try? FileManager.default.url(for: .applicationSupportDirectory,
            in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("Captures") {
            recoveryDirectories = CaptureRecovery.directories(in: root)
        }
        while !Task.isCancelled {
            for scan in savedScans {
                let store = ResumableUploadStore(captureDirectory: scan.directory)
                guard store.scanID != nil, uploads[scan.id] == nil,
                      let baseURL = AppEnvironment.apiBaseURL,
                      let token = session.token else { continue }
                let model = UploadViewModel(scan: scan, name: scan.name ?? "Shop scan",
                    client: ScanUploadClient(baseURL: baseURL, token: token))
                uploads[scan.id] = model
                model.start()
            }
            savedScans = CaptureLibrary.all()
            do { try await Task.sleep(for: .seconds(2)) } catch { return }
        }
    }
}

struct AppRootView: View {
    @StateObject private var model = AppModel()

    var body: some View {
        Group {
            if DeviceSupport.canCaptureRooms {
                SupportedAppView(model: model)
            } else {
                UnsupportedDeviceView()
            }
        }
        .tint(AppTheme.accent)
        .preferredColorScheme(.light)
    }
}

private enum DeviceSupport {
    static var canCaptureRooms: Bool {
#if targetEnvironment(simulator)
        if ProcessInfo.processInfo.environment["SIMULATOR_CAPTURE_DEMO"] == "1" { return true }
#endif
        return RoomCaptureSession.isSupported
    }
}

private struct SupportedAppView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        switch model.screen {
        case .start:
            StartView(model: model)
        case .capture:
            CaptureScreen(model: model)
                .id(model.captureSessionID)
        case .review(let scan):
            ReviewScreen(model: model, scan: scan)
        case .upload(let uploadModel):
            UploadStatusScreen(appModel: model, uploadModel: uploadModel)
        case .workspace(let scanID):
            WorkspaceScreen(appModel: model, scanID: scanID)
        case .connection:
            ConnectionScreen(model: model)
        case .signIn:
            SignInScreen(model: model, session: model.session)
        }
    }
}

private struct StartView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        NavigationStack {
            ZStack {
                DraftingPaper()
                List {
                    masthead
                    controls
                    AccountRow(model: model, session: model.session)
                        .plainRow(top: AppTheme.Spacing.section)
                    if !model.savedScans.isEmpty {
                        SavedScansSection(
                            scans: model.savedScans,
                            select: { model.screen = .review($0) },
                            delete: { scan in Task { await model.deleteScan(scan) } }
                        )
                    }
                    if let message = model.deletionMessage {
                        Text(message)
                            .font(AppTheme.Typography.secondary)
                            .foregroundStyle(AppTheme.mutedInk)
                            .plainRow(top: AppTheme.Spacing.compact)
                    }
                    recovery
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .environment(\.defaultMinListRowHeight, 0)
                .contentMargins(.horizontal, AppTheme.Spacing.page, for: .scrollContent)
            }
            .toolbar(.hidden, for: .navigationBar)
            .task { await model.refreshSavedScanStates() }
        }
    }

    private var masthead: some View {
        VStack(alignment: .leading, spacing: AppTheme.Spacing.compact) {
            Image(systemName: "viewfinder")
                .font(.system(size: 54, weight: .light))
                .foregroundStyle(AppTheme.accent)
                .accessibilityHidden(true)
            Text("Measure your shop")
                .font(AppTheme.Typography.hero)
                .foregroundStyle(AppTheme.ink)
            Text("Walk once around the room. We\u{2019}ll show you where to point.")
                .font(AppTheme.Typography.lead)
                .foregroundStyle(AppTheme.mutedInk)
        }
        .plainRow(top: 64)
    }

    private var controls: some View {
        VStack(spacing: AppTheme.Spacing.small) {
            Button("Start scanning") { model.beginCapture() }
                .buttonStyle(AppButtonStyle())
            Button("Change the upload address") { model.screen = .connection }
                .buttonStyle(AppButtonStyle(.secondary))
        }
        .plainRow(top: AppTheme.Spacing.page)
    }

    @ViewBuilder private var recovery: some View {
        ForEach(model.recoveryDirectories, id: \.self) { directory in
            Button("Recover saved room") {
                Task { await model.recoverSavedRoom(directory) }
            }
            .buttonStyle(AppButtonStyle(.secondary))
            .plainRow(top: AppTheme.Spacing.small)
        }
        if let message = model.recoveryMessage {
            Text(message)
                .font(AppTheme.Typography.secondary)
                .foregroundStyle(AppTheme.mutedInk)
                .plainRow(top: AppTheme.Spacing.compact)
        }
    }
}

private extension View {
    /// A list row that carries none of a list row's furniture: no separator, no
    /// grey backing, no inset. The list is here for its swipe actions and its
    /// row recycling, not for its looks.
    func plainRow(top: CGFloat = 0) -> some View {
        listRowBackground(AppTheme.transparent)
            .listRowSeparator(.hidden)
            .listRowInsets(EdgeInsets(top: top, leading: 0, bottom: 0, trailing: 0))
    }
}

/// The scans on this phone, each one swipeable the way every other iOS list is.
///
/// The swipe is the system's rather than a gesture of our own: it rubber-bands,
/// rests open, closes when the list scrolls, completes on a full swipe, and
/// arrives in VoiceOver as an action on the row. None of that is worth
/// rebuilding, and a rebuild is what made the old one feel broken.
///
/// Deleting takes the room, the walkthrough and the findings with it and there
/// is no undo, so the swipe asks first.
private struct SavedScansSection: View {
    let scans: [CapturedScan]
    let select: (CapturedScan) -> Void
    let delete: (CapturedScan) -> Void

    @State private var pendingDeletion: CapturedScan?

    var body: some View {
        Section {
            ForEach(scans) { scan in
                SavedScanRow(scan: scan, select: { select(scan) })
                    .listRowBackground(AppTheme.panel)
                    .listRowSeparatorTint(AppTheme.rule)
                    .listRowInsets(EdgeInsets(
                        top: AppTheme.Spacing.compact,
                        leading: AppTheme.Spacing.card,
                        bottom: AppTheme.Spacing.compact,
                        trailing: AppTheme.Spacing.card
                    ))
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button(role: .destructive) {
                            pendingDeletion = scan
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                        .labelStyle(.iconOnly)
                    }
            }
        } header: {
            Text("Saved scans")
                .font(AppTheme.Typography.title)
                .foregroundStyle(AppTheme.ink)
                .textCase(nil)
                .padding(.top, AppTheme.Spacing.section)
                .padding(.bottom, AppTheme.Spacing.small)
                .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
        }
        .listRowBackground(AppTheme.transparent)
        .confirmationDialog(
            "Delete this scan?",
            isPresented: .init(
                get: { pendingDeletion != nil },
                set: { if !$0 { pendingDeletion = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                if let scan = pendingDeletion { delete(scan) }
                pendingDeletion = nil
            }
            Button("Keep it", role: .cancel) { pendingDeletion = nil }
        } message: {
            Text("The room, the walkthrough and the findings all go with it.")
        }
    }
}

private struct SavedScanRow: View {
    let scan: CapturedScan
    let select: () -> Void

    var body: some View {
        Button(action: select) {
            HStack(spacing: AppTheme.Spacing.compact) {
                Image(systemName: "cube.transparent")
                    .font(.title2)
                    .foregroundStyle(AppTheme.accent)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(scan.name ?? "Shop scan")
                        .font(AppTheme.Typography.heading)
                        .foregroundStyle(AppTheme.ink)
                    Text(ResumableUploadStore(captureDirectory: scan.directory).historyText)
                        .font(AppTheme.Typography.measurement)
                        .foregroundStyle(AppTheme.mutedInk)
                }
                Spacer(minLength: AppTheme.Spacing.small)
                Image(systemName: "chevron.right")
                    .foregroundStyle(AppTheme.faintInk)
                    .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Who this phone uploads as, and the way to change it.
///
/// Scans go to whichever account is signed in here, so the shop's name is on
/// screen before anyone starts a scan rather than after the upload fails.
private struct AccountRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var session: SessionStore

    @State private var confirmingDeletion = false

    var body: some View {
        if let owner = session.owner {
            VStack(alignment: .leading, spacing: AppTheme.Spacing.small) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(owner.shopName)
                        .font(AppTheme.Typography.heading)
                        .foregroundStyle(AppTheme.ink)
                    Text(owner.email)
                        .font(AppTheme.Typography.measurement)
                        .foregroundStyle(AppTheme.mutedInk)
                }
                Button("Sign out") { model.signOut() }
                    .buttonStyle(AppButtonStyle(.secondary))
                Button("Delete account") { confirmingDeletion = true }
                    .buttonStyle(AppButtonStyle(.destructive))
                if let message = model.accountDeletionMessage {
                    Text(message)
                        .font(AppTheme.Typography.secondary)
                        .foregroundStyle(AppTheme.problem)
                }
            }
            .confirmationDialog(
                "Delete your account?",
                isPresented: $confirmingDeletion,
                titleVisibility: .visible
            ) {
                Button("Delete account", role: .destructive) {
                    Task { await model.deleteAccount() }
                }
                Button("Keep it", role: .cancel) { confirmingDeletion = false }
            } message: {
                Text("Every shop you have scanned, and every measurement taken in one, goes from this phone and from the server. There is no undo.")
            }
        } else {
            Button("Sign in to upload") { model.screen = .signIn }
                .buttonStyle(AppButtonStyle(.secondary))
        }
    }
}

private struct UnsupportedDeviceView: View {
    var body: some View {
        ZStack {
            DraftingPaper()
            Text("Use an iPhone Pro or Pro Max with LiDAR (12 or later), or an iPad Pro with LiDAR (2020 or later).")
                .font(AppTheme.Typography.title)
                .foregroundStyle(AppTheme.ink)
                .multilineTextAlignment(.center)
                .padding(AppTheme.Spacing.page)
        }
    }
}
