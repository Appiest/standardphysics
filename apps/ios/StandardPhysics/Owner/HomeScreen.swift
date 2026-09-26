import SwiftUI

/// Home after the first walk: the newest shop's one next step, any other
/// shops on the account, and scans still only on this phone.
///
/// Shops come from the account, so a second phone signed in to it finds them
/// too. A scan saved here and never uploaded is listed until it is.
struct HomeScreen: View {
    @ObservedObject var model: AppModel

    private var primary: Journey? { model.journeys.first }
    private var otherShops: [Journey] { Array(model.journeys.dropFirst()) }

    /// Scans on this phone the account doesn't list yet: never uploaded, or
    /// uploaded to a shop the server hasn't answered about.
    private var phoneOnlyScans: [CapturedScan] {
        let known = Set(model.journeys.map(\.scanID))
        return model.savedScans.filter { scan in
            guard let remote = ResumableUploadStore(captureDirectory: scan.directory).scanID else { return true }
            return !known.contains(remote)
        }
    }

    var body: some View {
        NavigationStack {
            ZStack {
                DraftingPaper()
                List {
                    header
                    if !otherShops.isEmpty { OtherShopsSection(journeys: otherShops, open: model.open) }
                    if !phoneOnlyScans.isEmpty {
                        SavedScansSection(
                            scans: phoneOnlyScans,
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
                    walkAnotherShop
                    AccountRow(model: model, session: model.session)
                        .plainRow(top: AppTheme.Spacing.section)
                    Button("How the walk works") { model.screen = .howItWorks }
                        .buttonStyle(AppButtonStyle(.link))
                        .plainRow(top: AppTheme.Spacing.small)
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .environment(\.defaultMinListRowHeight, 0)
                .contentMargins(.horizontal, AppTheme.Spacing.page, for: .scrollContent)
                .contentMargins(.bottom, AppTheme.Spacing.page, for: .scrollContent)
                .refreshable { await model.refreshJourneys() }
            }
            .toolbar(.hidden, for: .navigationBar)
            .task { await model.refreshSavedScanStates() }
            .task { await model.refreshJourneys() }
        }
    }

    @ViewBuilder private var header: some View {
        VStack(alignment: .leading, spacing: AppTheme.Spacing.card) {
            Text(primary?.shopName ?? model.defaultShopName)
                .font(AppTheme.Typography.hero)
                .foregroundStyle(AppTheme.ink)
                .lineLimit(2)
                .minimumScaleFactor(0.7)
                .accessibilityAddTraits(.isHeader)
            if let primary {
                NextStepCard(journey: primary) { model.open(primary) }
            } else if !model.journeysLoaded {
                ProgressView()
                    .tint(AppTheme.accent)
                    .frame(maxWidth: .infinity, minHeight: 96)
            }
            GuestDeletionNotice(model: model, session: model.session)
        }
        .plainRow(top: 56)
    }

    @ViewBuilder private var walkAnotherShop: some View {
        if model.canScan {
            Button(primary == nil && phoneOnlyScans.isEmpty ? "Walk your shop" : "Walk another shop") {
                model.startWalk()
            }
            .buttonStyle(AppButtonStyle(primary == nil && phoneOnlyScans.isEmpty ? .primary : .secondary))
            .plainRow(top: AppTheme.Spacing.section)
        }
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

/// The one next step for a shop, the largest thing on home.
struct NextStepCard: View {
    let journey: Journey
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            HStack(alignment: .center, spacing: AppTheme.Spacing.card) {
                Image(systemName: NextStepMark.symbol(for: journey.nextStep.kind))
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(NextStepMark.colour(for: journey.nextStep.kind))
                    .frame(width: 36)
                    .accessibilityHidden(true)
                Text(journey.nextStep.title)
                    .font(AppTheme.Typography.cardTitle)
                    .foregroundStyle(AppTheme.ink)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.right")
                    .font(.headline)
                    .foregroundStyle(AppTheme.faintInk)
                    .accessibilityHidden(true)
            }
            .padding(AppTheme.Spacing.card)
            .frame(maxWidth: .infinity, minHeight: 96, alignment: .leading)
            .raisedPanel()
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens this step")
    }
}

/// A mark per kind of next step, so the card says what kind of thing it is
/// before its words are read.
enum NextStepMark {
    static func symbol(for kind: String) -> String {
        switch kind {
        case "answers": "questionmark.bubble"
        case "photos": "camera"
        case "measuring": "ruler"
        case "counter": "hand.tap"
        case "path": "figure.walk"
        case "follow_ups": "ruler"
        case "results": "list.bullet.clipboard"
        case "checklist": "checklist"
        case "done": "checkmark.seal"
        case "upload": "arrow.up.circle"
        case "failed": "arrow.clockwise"
        default: "arrow.right.circle"
        }
    }

    static func colour(for kind: String) -> Color {
        switch kind {
        case "failed": AppTheme.problem
        case "done": AppTheme.pass
        default: AppTheme.accent
        }
    }
}

private struct OtherShopsSection: View {
    let journeys: [Journey]
    let open: (Journey) -> Void

    var body: some View {
        Section {
            Text("Your other shops")
                .font(AppTheme.Typography.title)
                .foregroundStyle(AppTheme.ink)
                .accessibilityAddTraits(.isHeader)
                .plainRow(top: AppTheme.Spacing.section)
                .padding(.bottom, AppTheme.Spacing.small)
            ForEach(journeys) { journey in
                Button { open(journey) } label: {
                    HStack(spacing: AppTheme.Spacing.compact) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(journey.shopName)
                                .font(AppTheme.Typography.heading)
                                .foregroundStyle(AppTheme.ink)
                            Label(journey.nextStep.title, systemImage: NextStepMark.symbol(for: journey.nextStep.kind))
                                .font(AppTheme.Typography.secondary)
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
                .listRowBackground(AppTheme.panel)
                .listRowSeparatorTint(AppTheme.rule)
                .listRowInsets(EdgeInsets(
                    top: AppTheme.Spacing.compact, leading: AppTheme.Spacing.card,
                    bottom: AppTheme.Spacing.compact, trailing: AppTheme.Spacing.card
                ))
            }
        }
    }
}

/// A guest's shops are deleted 30 days after one was last opened. Three
/// days before that, home says when, with the way to keep them beside it.
private struct GuestDeletionNotice: View {
    @ObservedObject var model: AppModel
    @ObservedObject var session: SessionStore

    private var deletionDate: Date? {
        guard let owner = session.owner, owner.guest, let text = owner.deletesAt else { return nil }
        return GuestDeletion.date(from: text)
    }

    var body: some View {
        if let deletionDate, deletionDate.timeIntervalSinceNow < GuestDeletion.warningLead {
            Label(
                "Save your shop by \(deletionDate.formatted(.dateTime.month(.wide).day())) to keep it.",
                systemImage: "clock.badge.exclamationmark"
            )
            .font(AppTheme.Typography.heading)
            .foregroundStyle(AppTheme.ink)
            .padding(AppTheme.Spacing.control)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(AppTheme.coverageMissing.opacity(0.16))
        }
    }
}

enum GuestDeletion {
    static let warningLead: TimeInterval = 3 * 24 * 60 * 60

    static func date(from text: String) -> Date? {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return withFraction.date(from: text) ?? ISO8601DateFormatter().date(from: text)
    }
}

/// A guest's account row: one way to keep the shop, and sign-in for owners
/// who already have an account.
struct GuestAccountRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var session: SessionStore
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppTheme.Spacing.small) {
            if session.owner != nil {
                Button("Save your shop") { saving = true }
                    .buttonStyle(AppButtonStyle(.secondary))
            }
            Button("Sign in to your account") { model.screen = .signIn }
                .buttonStyle(AppButtonStyle(session.owner == nil ? .secondary : .link))
        }
        .sheet(isPresented: $saving) {
            NavigationStack {
                ZStack {
                    DraftingPaper()
                    VStack(alignment: .leading, spacing: AppTheme.Spacing.section) {
                        FlowTitle("Keep your shop on any phone and on the web.")
                        SaveShopOptions(session: session) {
                            saving = false
                            Task { await model.refreshJourneys() }
                        }
                        Spacer()
                    }
                    .padding(AppTheme.Spacing.page)
                }
                .navigationTitle("Save your shop")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("Cancel") { saving = false }
                    }
                }
            }
            .presentationDetents([.medium, .large])
        }
    }
}
