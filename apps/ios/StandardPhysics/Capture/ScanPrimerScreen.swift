import SwiftUI

/// What to do, before the camera is in the way of reading it.
///
/// Owners were standing still and sweeping the phone around, which measures a
/// room but not the paths through it, and the paths are what the aisle and
/// doorway rules are about. The four points below are the four things people
/// were seen getting wrong, in the order they go wrong.
struct ScanPrimerScreen: View {
    @ObservedObject var model: AppModel

    var body: some View {
        NavigationStack {
            ZStack {
                DraftingPaper()
                ScrollView {
                    VStack(alignment: .leading, spacing: AppTheme.Spacing.section) {
                        Text("Walk once around the room")
                            .font(AppTheme.Typography.hero)
                            .foregroundStyle(AppTheme.ink)

                        WalkPlan()
                            .frame(height: 210)
                            .accessibilityLabel(
                                "A plan of a room with a dashed path running all the way around "
                                + "the inside, a stride away from the walls."
                            )

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
                                detail: "About one step a second. Walking at normal pace is the most "
                                    + "common reason a scan comes out thin."
                            )
                            Advice(
                                symbol: "checkmark.circle",
                                title: "We'll say when a wall is done",
                                detail: "The phone taps once for each wall, and that wall turns solid "
                                    + "blue on the map. An arrow points at what is still missing."
                            )
                        }

                        Button("Start scanning") { model.beginCapture() }
                            .buttonStyle(AppButtonStyle())
                    }
                    .padding(.horizontal, AppTheme.Spacing.page)
                    .padding(.top, AppTheme.Spacing.section)
                    .padding(.bottom, AppTheme.Spacing.page)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Back") { model.showStart() }
                }
            }
            .navigationTitle("Before you start")
            .navigationBarTitleDisplayMode(.inline)
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

/// The walk itself, drawn as the plan it produces.
///
/// The path is inset from the walls by the stride the copy asks for, so the
/// picture and the instruction are the same claim. The counter is there
/// because a route has to get around something to be worth measuring.
private struct WalkPlan: View {
    var body: some View {
        Canvas { context, size in
            let margin: CGFloat = 26
            let room = CGRect(
                x: margin, y: margin,
                width: size.width - margin * 2,
                height: size.height - margin * 2
            )
            context.stroke(Path(room), with: .color(AppTheme.ink), lineWidth: 3)

            let counter = CGRect(
                x: room.minX + room.width * 0.52,
                y: room.minY,
                width: room.width * 0.34,
                height: room.height * 0.22
            )
            context.fill(Path(counter), with: .color(AppTheme.ink.opacity(0.14)))
            context.stroke(Path(counter), with: .color(AppTheme.ink), lineWidth: 1.5)

            let walk = room.insetBy(dx: room.width * 0.17, dy: room.height * 0.20)
            context.stroke(
                Path(walk),
                with: .color(AppTheme.accent),
                style: StrokeStyle(lineWidth: 2.5, lineCap: .round, dash: [7, 7])
            )

            for point in strideMarks(on: walk) {
                context.fill(
                    Path(ellipseIn: CGRect(x: point.x - 3.5, y: point.y - 3.5, width: 7, height: 7)),
                    with: .color(AppTheme.accent)
                )
            }
        }
    }

    /// Where the walker stands, spaced evenly so the drawing reads as a pace.
    private func strideMarks(on path: CGRect) -> [CGPoint] {
        [
            CGPoint(x: path.minX, y: path.midY),
            CGPoint(x: path.midX, y: path.minY),
            CGPoint(x: path.maxX, y: path.midY),
            CGPoint(x: path.midX, y: path.maxY),
        ]
    }
}
