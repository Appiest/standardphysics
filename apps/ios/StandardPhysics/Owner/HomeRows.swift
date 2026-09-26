import SwiftUI

extension View {
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
struct SavedScansSection: View {
    let scans: [CapturedScan]
    let select: (CapturedScan) -> Void
    let delete: (CapturedScan) -> Void

    @State private var pendingDeletion: CapturedScan?

    var body: some View {
        Section {
            Text("On this phone")
                .font(AppTheme.Typography.title)
                .foregroundStyle(AppTheme.ink)
                .accessibilityAddTraits(.isHeader)
                .plainRow(top: AppTheme.Spacing.section)
                .padding(.bottom, AppTheme.Spacing.small)
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
struct AccountRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var session: SessionStore

    @State private var confirmingDeletion = false

    var body: some View {
        if let owner = session.owner, !owner.guest {
            VStack(alignment: .leading, spacing: AppTheme.Spacing.small) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(owner.shopName)
                        .font(AppTheme.Typography.heading)
                        .foregroundStyle(AppTheme.ink)
                    if let email = owner.email {
                        Text(email)
                            .font(AppTheme.Typography.secondary)
                            .foregroundStyle(AppTheme.mutedInk)
                    }
                }
                Button("Sign out") { model.signOut() }
                    .buttonStyle(AppButtonStyle(.secondary))
                if owner.isTeam {
                    Toggle("Developer mode", isOn: $model.developerMode)
                        .font(AppTheme.Typography.secondary)
                        .foregroundStyle(AppTheme.mutedInk)
                        .tint(AppTheme.accent)
                }
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
            GuestAccountRow(model: model, session: session)
        }
    }
}

