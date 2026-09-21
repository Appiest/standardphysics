import SwiftUI

struct UploadStatusScreen: View {
    @ObservedObject var appModel: AppModel
    @ObservedObject var uploadModel: UploadViewModel

    var body: some View {
        ZStack {
            DraftingPaper()
            VStack(spacing: AppTheme.Spacing.page) {
                Spacer()
                statusMark
                Text(uploadModel.state.displayText)
                    .font(AppTheme.Typography.status)
                    .multilineTextAlignment(.center)
                if let message = uploadModel.errorMessage {
                    Text(message)
                        .font(AppTheme.Typography.body)
                        .foregroundStyle(AppTheme.mutedInk)
                        .multilineTextAlignment(.center)
                }
                if let message = uploadModel.optionalUploadErrorMessage {
                    Text(message).foregroundStyle(AppTheme.mutedInk)
                } else if uploadModel.state == .ready && uploadModel.pendingOptionalUploadCount > 0 {
                    Text("Your video and images are still uploading.").foregroundStyle(AppTheme.mutedInk)
                }
                Spacer()
                actions
            }
            .padding(AppTheme.Spacing.page)
        }
        .task { uploadModel.start() }
    }

    @ViewBuilder private var statusMark: some View {
        if uploadModel.state == .ready {
            Image(systemName: "checkmark")
                .font(AppTheme.Typography.statusSymbol)
                .foregroundStyle(AppTheme.onDark)
                .frame(width: AppTheme.Size.statusMark, height: AppTheme.Size.statusMark)
                .background(AppTheme.accent)
                .clipShape(Circle())
                .accessibilityHidden(true)
        } else if uploadModel.errorMessage != nil || uploadModel.state == .failed {
            Image(systemName: "arrow.clockwise")
                .font(AppTheme.Typography.statusSymbol)
                .foregroundStyle(AppTheme.warning)
                .accessibilityHidden(true)
        } else {
            ProgressView()
                .controlSize(.large)
                .tint(AppTheme.accent)
                .frame(width: AppTheme.Size.statusMark, height: AppTheme.Size.statusMark)
        }
    }

    @ViewBuilder private var actions: some View {
        if uploadModel.state == .ready, let scanID = uploadModel.scanID {
            Button("Open your shop") { appModel.screen = .workspace(scanID) }
                .buttonStyle(AppButtonStyle())
        } else if uploadModel.errorMessage != nil {
            Button("Try again") { uploadModel.retry() }
                .buttonStyle(AppButtonStyle())
        }
        if uploadModel.optionalUploadErrorMessage != nil {
            Button("Retry remaining uploads") { uploadModel.retry() }
                .buttonStyle(AppButtonStyle(.secondary))
        }
        Button("Back to saved scans") { appModel.showStart() }
            .buttonStyle(AppButtonStyle(.secondary))
    }
}
