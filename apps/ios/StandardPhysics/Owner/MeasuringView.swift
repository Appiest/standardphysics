import SwiftUI
import UserNotifications

/// Screen 8: the wait while the shop is measured.
///
/// The wait is where saving the shop is offered, since there is nothing else
/// to do, and right after it where notifications are. Neither blocks: "Not
/// now" moves on, and the shop opens on the web on its own once it has a
/// model, where the owner marks the counter and the path.
struct MeasuringView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var setup: ShopSetupModel

    enum Prompt { case deciding, save, notifications, none }
    @State private var prompt = Prompt.deciding

    var body: some View {
        FlowPage {
            SketchSheet(height: 200) { RoomSketch(mode: .measuring) }
                .padding(.top, AppTheme.Spacing.small)
            if let upload = setup.upload {
                UploadProgressHeader(upload: upload, isMeasured: setup.isMeasured)
            } else {
                MeasuringHeader(isMeasured: setup.isMeasured)
            }
            promptPanel
        } actions: {
            actions
        }
        .task { await decidePrompt() }
        .task { await setup.watchForResults() }
        .onChange(of: setup.isMeasured) { _, measured in
            if measured, prompt == .none, let scanID = setup.scanID { app.openShop(scanID) }
        }
    }

    @ViewBuilder private var promptPanel: some View {
        switch prompt {
        case .save:
            VStack(alignment: .leading, spacing: AppTheme.Spacing.compact) {
                Text("Save your shop while you wait, and we\u{2019}ll let you know when it\u{2019}s ready.")
                    .font(AppTheme.Typography.heading)
                    .foregroundStyle(AppTheme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                SaveShopOptions(session: app.session) { Task { await afterSaving() } }
            }
            .padding(.top, AppTheme.Spacing.small)
            .transition(.opacity)
        case .notifications:
            VStack(alignment: .leading, spacing: AppTheme.Spacing.compact) {
                Label("Get a notification when your results are ready.", systemImage: "bell.badge")
                    .font(AppTheme.Typography.heading)
                    .foregroundStyle(AppTheme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Turn on notifications") {
                    Task {
                        await PushRegistration.request()
                        finishPrompts()
                    }
                }
                .buttonStyle(AppButtonStyle(.secondary))
            }
            .padding(.top, AppTheme.Spacing.small)
            .transition(.opacity)
        case .deciding, .none:
            EmptyView()
        }
    }

    @ViewBuilder private var actions: some View {
        if let handover = setup.handover, let scanID = setup.scanID {
            Button(handover.title) { app.openShop(scanID) }
                .buttonStyle(AppButtonStyle())
        } else if let upload = setup.upload, upload.errorMessage != nil {
            Button(upload.needsSignIn ? "Sign in and keep uploading" : "Try again") {
                upload.needsSignIn ? app.signInToContinue(upload) : upload.retry()
            }
            .buttonStyle(AppButtonStyle())
        }
        switch prompt {
        case .save:
            Button("Not now") { Task { await afterSaving() } }
                .buttonStyle(AppButtonStyle(.link))
        case .notifications:
            Button("Not now") { finishPrompts() }
                .buttonStyle(AppButtonStyle(.link))
        case .deciding, .none:
            if !setup.isMeasured {
                Button("Go to home") { app.showStart() }
                    .buttonStyle(AppButtonStyle(.link))
            }
        }
    }

    private func decidePrompt() async {
        guard prompt == .deciding else { return }
#if DEBUG
        if let debugPrompt = setup.debugPrompt {
            prompt = debugPrompt
            return
        }
#endif
        if !app.session.hasSavedAccount {
            prompt = .save
        } else {
            await afterSaving()
        }
    }

    private func afterSaving() async {
        let status = await PushRegistration.status()
        withAnimation(AppTheme.Motion.step) {
            prompt = status == .notDetermined ? .notifications : .none
        }
        openResultsIfReady()
    }

    private func finishPrompts() {
        withAnimation(AppTheme.Motion.step) { prompt = .none }
        openResultsIfReady()
    }

    private func openResultsIfReady() {
        guard prompt == .none, setup.isMeasured, let scanID = setup.scanID else { return }
        app.openShop(scanID)
    }
}

/// Where the walk's upload is, on this phone.
private struct UploadProgressHeader: View {
    @ObservedObject var upload: UploadViewModel
    let isMeasured: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: AppTheme.Spacing.small) {
            if isMeasured {
                FlowTitle("Your shop is measured")
            } else if let problem = upload.errorMessage {
                FlowTitle("Your walk didn\u{2019}t finish sending")
                FlowProblem(message: problem)
            } else if upload.state == .uploading {
                FlowTitle("Sending your walk")
                ProgressView(value: Double(upload.uploadedCount), total: Double(max(upload.totalCount, 1)))
                    .tint(AppTheme.accent)
                FlowDetail("Keep the app open until it\u{2019}s sent.")
            } else {
                MeasuringHeader(isMeasured: false)
            }
        }
    }
}

private struct MeasuringHeader: View {
    let isMeasured: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: AppTheme.Spacing.small) {
            if isMeasured {
                FlowTitle("Your shop is measured")
            } else {
                FlowTitle("Measuring your shop")
                FlowDetail("This takes a few minutes.")
            }
        }
    }
}
