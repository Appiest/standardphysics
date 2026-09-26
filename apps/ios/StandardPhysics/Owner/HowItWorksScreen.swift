import SwiftUI

/// How to walk a shop, kept where the owner can always find it again.
///
/// Owners were standing still and sweeping the phone around, which measures a
/// room but not the paths through it, and the paths are what the aisle and
/// doorway rules are about. The four points below are the four things people
/// were seen getting wrong, in the order they go wrong. The first walk teaches
/// them in place; this is where they live after that.
struct HowItWorksScreen: View {
    @ObservedObject var model: AppModel

    var body: some View {
        FlowPage(back: { model.showStart() }) {
            FlowTitle("Walk once around the room")
            SketchSheet(height: 190) { WalkPlan() }
            VStack(alignment: .leading, spacing: AppTheme.Spacing.card) {
                Advice(
                    symbol: "figure.walk",
                    title: "Keep walking",
                    detail: "Turning on the spot measures the room but not the space to "
                        + "get through it, and the space is what we check."
                )
                Advice(
                    symbol: "arrow.left.and.right",
                    title: "Stay about a stride from the wall",
                    detail: "Closer than that and the phone sees only wall. Much further "
                        + "and it stops reading the surface at all."
                )
                Advice(
                    symbol: "tortoise",
                    title: "Go slower than feels necessary",
                    detail: "Take about one step a second. Walking at normal pace is the "
                        + "most common reason a scan comes out thin."
                )
                Advice(
                    symbol: "checkmark.circle",
                    title: "We\u{2019}ll say when a wall is done",
                    detail: "The phone taps once for each wall, and that wall turns solid "
                        + "blue on the map. An arrow points at what is still missing."
                )
            }
        } actions: {
            if model.canScan {
                Button("Walk your shop") { model.screen = .beforeYouWalk }
                    .buttonStyle(AppButtonStyle(.secondary))
            }
        }
    }
}

private struct Advice: View {
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: AppTheme.Spacing.compact) {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(AppTheme.accent)
                .frame(width: 28)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(AppTheme.Typography.heading)
                    .foregroundStyle(AppTheme.ink)
                Text(detail)
                    .font(AppTheme.Typography.secondary)
                    .foregroundStyle(AppTheme.mutedInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
