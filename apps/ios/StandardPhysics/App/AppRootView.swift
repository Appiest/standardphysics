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
                AppTheme.canvas.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: AppTheme.Spacing.page) {
                        Spacer(minLength: 64)
                        Image(systemName: "viewfinder")
                            .font(.system(size: 54, weight: .light))
                            .foregroundStyle(AppTheme.accent)
                            .accessibilityHidden(true)
                        Text("Measure your shop")
                            .font(AppTheme.Typography.hero)
                            .foregroundStyle(AppTheme.ink)
                        Text("Walk once around the room. We’ll show you where to point.")
                            .font(.title3)
                            .foregroundStyle(AppTheme.mutedInk)
                        Button("Start scanning") { model.beginCapture() }
                            .buttonStyle(AppButtonStyle())
                        Button("Connection") { model.screen = .connection }
                            .buttonStyle(AppButtonStyle(.secondary))
                        AccountRow(model: model, session: model.session)
                        if !model.savedScans.isEmpty {
                            SavedScansView(
                                scans: model.savedScans,
                                select: { model.screen = .review($0) },
                                delete: { scan in Task { await model.deleteScan(scan) } }
                            )
                        }
                        if model.deletionMessage != nil {
                            ProgressView()
                                .tint(AppTheme.accent)
                                .accessibilityLabel("Working")
                        }
                        ForEach(model.recoveryDirectories, id: \.self) { directory in
                            Button("Recover saved room") {
                                Task { await model.recoverSavedRoom(directory) }
                            }.buttonStyle(AppButtonStyle(.secondary))
                        }
                        if model.recoveryMessage != nil {
                            ProgressView()
                                .tint(AppTheme.accent)
                                .accessibilityLabel("Working")
                        }
                    }
                    .padding(AppTheme.Spacing.page)
                }
            }
            .toolbar(.hidden, for: .navigationBar)
            .task { await model.refreshSavedScanStates() }
        }
    }
}

/// Who this phone uploads as, and the way to change it.
///
/// Scans go to whichever account is signed in here, so the shop's name is on
/// screen before anyone starts a scan rather than after the upload fails.
private struct AccountRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var session: SessionStore

    var body: some View {
        if let owner = session.owner {
            VStack(alignment: .leading, spacing: 4) {
                Text(owner.shopName)
                    .font(.headline)
                    .foregroundStyle(AppTheme.ink)
                Text(owner.email)
                    .font(.subheadline)
                    .foregroundStyle(AppTheme.mutedInk)
                Button("Sign out") { model.signOut() }
                    .buttonStyle(AppButtonStyle(.secondary))
            }
        } else {
            Button("Sign in") { model.screen = .signIn }
                .buttonStyle(AppButtonStyle(.secondary))
        }
    }
}

private struct SavedScansView: View {
    let scans: [CapturedScan]
    let select: (CapturedScan) -> Void
    let delete: (CapturedScan) -> Void

    @State private var pendingDeletion: CapturedScan?

    var body: some View {
        VStack(alignment: .leading, spacing: AppTheme.Spacing.small) {
            Text("Saved scans")
                .font(.title2.bold())
            ForEach(scans) { scan in
                SavedScanRow(
                    scan: scan,
                    select: { select(scan) },
                    requestDelete: { pendingDeletion = scan }
                )
            }
        }
        .padding(.top, AppTheme.Spacing.compact)
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

/// One saved scan. Swipe it left, or press the trash.
///
/// Swipe alone is not enough: it is invisible until someone already knows to
/// try it. The button is what makes the gesture discoverable, so the two ship
/// together rather than either on its own.
private struct SavedScanRow: View {
    let scan: CapturedScan
    let select: () -> Void
    let requestDelete: () -> Void

    @State private var offset: CGFloat = 0
    @GestureState private var dragging: CGFloat = 0

    private let revealWidth: CGFloat = 96
    private let triggerDistance: CGFloat = 72

    var body: some View {
        ZStack(alignment: .trailing) {
            deleteTrack
            card
                .offset(x: min(0, offset + dragging))
                .gesture(swipe)
                .animation(.snappy(duration: 0.22), value: offset)
        }
        .clipShape(RoundedRectangle(cornerRadius: AppTheme.Radius.control, style: .continuous))
    }

    private var deleteTrack: some View {
        Button(action: confirm) {
            Text("Delete")
                .font(.headline)
                .foregroundStyle(.white)
                .frame(width: revealWidth)
                .frame(maxHeight: .infinity)
                .background(AppTheme.warning)
        }
        .buttonStyle(.plain)
        .accessibilityHidden(offset == 0)
    }

    private var swipe: some Gesture {
        DragGesture(minimumDistance: 18)
            .updating($dragging) { value, state, _ in
                state = min(0, value.translation.width)
            }
            .onEnded { value in
                let travelled = -value.translation.width
                if travelled > triggerDistance {
                    offset = 0
                    confirm()
                } else {
                    offset = 0
                }
            }
    }

    private func confirm() {
        offset = 0
        requestDelete()
    }

    private var card: some View {
        HStack(spacing: 0) {
            Button(action: select) {
                        HStack(spacing: AppTheme.Spacing.compact) {
                            Image(systemName: "cube.transparent")
                                .font(.title2)
                                .foregroundStyle(AppTheme.accent)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(scan.name ?? "Shop scan")
                                    .font(.headline)
                                Text(ResumableUploadStore(captureDirectory: scan.directory).historyText)
                                    .font(.subheadline)
                                    .foregroundStyle(AppTheme.mutedInk)
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .foregroundStyle(.secondary)
                        }
                    }
                    .buttonStyle(.plain)

            Button(action: confirm) {
                Image(systemName: "trash")
                    .font(.title3)
                    .foregroundStyle(AppTheme.mutedInk)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Delete \(scan.name ?? "this scan")")
        }
        .padding(AppTheme.Spacing.card)
        .background(AppTheme.panel)
    }
}

private struct UnsupportedDeviceView: View {
    var body: some View {
        ZStack {
            AppTheme.canvas.ignoresSafeArea()
            Text("Use an iPhone Pro or Pro Max with LiDAR (12 or later), or an iPad Pro with LiDAR (2020 or later).")
                .font(.title2.weight(.semibold))
                .foregroundStyle(AppTheme.ink)
                .multilineTextAlignment(.center)
                .padding(AppTheme.Spacing.page)
        }
    }
}
