import AVFoundation
import SwiftUI

/// Screen 1: what the app is for, shown by a shop drawing itself.
struct WelcomeScreen: View {
    @ObservedObject var model: AppModel

    var body: some View {
        FlowPage {
            SketchSheet(height: 320) { RoomSketch() }
                .padding(.top, AppTheme.Spacing.small)
            FlowTitle("See what gets in the way of customers in your shop, and how to fix it.")
        } actions: {
            Button("Get started") { model.startWalk() }
                .buttonStyle(AppButtonStyle())
            Button("See an example shop") { model.openExample() }
                .buttonStyle(AppButtonStyle(.secondary))
            Button("Sign in") { model.screen = .signIn }
                .buttonStyle(AppButtonStyle(.link))
        }
    }
}

/// Screen 2: the few things to do before walking, as a checklist the owner
/// can tick off. Ticking is for them; "I'm ready" works either way.
struct BeforeYouWalkScreen: View {
    @ObservedObject var model: AppModel
    @State private var ticked: Set<Int> = []

    private let items: [(symbol: String, title: String, detail: String)] = [
        ("lightbulb", "Turn on all the lights", "The camera measures a bright room best."),
        ("door.left.hand.open", "Open the doors customers use", "Then we can measure each doorway."),
        ("cart", "Clear the aisles", "Move carts and boxes that aren\u{2019}t usually there."),
        ("timer", "Set aside about 3 minutes", "The walk stops on its own at 4 minutes."),
    ]

    var body: some View {
        FlowPage(back: { model.showStart() }) {
            FlowTitle("Before you walk")
            SketchSheet(height: 150) { WalkPlan() }
            VStack(spacing: 0) {
                ForEach(items.indices, id: \.self) { index in
                    ChecklistRow(
                        symbol: items[index].symbol,
                        title: items[index].title,
                        detail: items[index].detail,
                        isTicked: ticked.contains(index),
                        toggle: { toggle(index) }
                    )
                    if index < items.count - 1 { DraftingRule() }
                }
            }
        } actions: {
            Button("I\u{2019}m ready") { model.finishChecklist() }
                .buttonStyle(AppButtonStyle())
            Button("How the walk works") { model.screen = .howItWorks }
                .buttonStyle(AppButtonStyle(.link))
        }
    }

    private func toggle(_ index: Int) {
        if ticked.contains(index) { ticked.remove(index) } else { ticked.insert(index) }
    }
}

private struct ChecklistRow: View {
    let symbol: String
    let title: String
    let detail: String
    let isTicked: Bool
    let toggle: () -> Void

    var body: some View {
        Button(action: toggle) {
            HStack(alignment: .top, spacing: AppTheme.Spacing.compact) {
                Image(systemName: symbol)
                    .font(.title3)
                    .foregroundStyle(AppTheme.accent)
                    .frame(width: 28)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(AppTheme.Typography.heading)
                        .foregroundStyle(AppTheme.ink)
                    Text(detail)
                        .font(AppTheme.Typography.secondary)
                        .foregroundStyle(AppTheme.mutedInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: isTicked ? "checkmark.circle.fill" : "circle")
                    .font(.title2)
                    .foregroundStyle(isTicked ? AppTheme.pass : AppTheme.faintInk)
                    .contentTransition(.symbolEffect(.replace))
                    .accessibilityHidden(true)
            }
            .padding(.vertical, AppTheme.Spacing.small)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isTicked ? .isSelected : [])
    }
}

/// Screen 3: the camera, asked for right before the walk with one button
/// that opens the system's own question.
struct CameraAccessScreen: View {
    @ObservedObject var model: AppModel
    @State private var status = CameraAccessScreen.currentStatus

    private static var currentStatus: AVAuthorizationStatus {
#if DEBUG
        if ProcessInfo.processInfo.environment["SP_DEBUG_CAMERA"] == "denied" { return .denied }
#endif
        return AVCaptureDevice.authorizationStatus(for: .video)
    }
    @Environment(\.scenePhase) private var scenePhase

    private var isRefused: Bool { status == .denied || status == .restricted }

    var body: some View {
        FlowPage(back: { model.screen = .beforeYouWalk }) {
            SketchSheet(height: 220) {
                ZStack {
                    CameraSketch(allowed: !isRefused)
                    if isRefused {
                        Image(systemName: "video.slash")
                            .font(.system(size: 36, weight: .semibold))
                            .foregroundStyle(AppTheme.mutedInk)
                            .frame(width: 84, height: 84)
                            .background(Circle().fill(AppTheme.Sketch.paper))
                            .accessibilityHidden(true)
                    }
                }
            }
            if isRefused {
                FlowTitle("Camera access is off")
                FlowDetail("Turn on Camera for Standard Physics in Settings, then come back here.")
            } else {
                FlowTitle("Standard Physics measures your shop with the camera.")
            }
        } actions: {
            if isRefused {
                Button("Open Settings") { openSettings() }
                    .buttonStyle(AppButtonStyle())
            } else {
                Button("Continue") { askForTheCamera() }
                    .buttonStyle(AppButtonStyle())
            }
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            status = Self.currentStatus
            if status == .authorized { model.beginCapture() }
        }
    }

    private func askForTheCamera() {
        Task {
            let granted = await AVCaptureDevice.requestAccess(for: .video)
            status = AVCaptureDevice.authorizationStatus(for: .video)
            if granted { model.beginCapture() }
        }
    }

    private func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}

/// For a phone without LiDAR: which devices can walk a shop, and the two
/// things this one can still do.
struct UnsupportedDeviceScreen: View {
    @ObservedObject var model: AppModel

    var body: some View {
        FlowPage {
            SketchSheet(height: 220) { RoomSketch() }
                .padding(.top, AppTheme.Spacing.small)
            FlowTitle("Measure your shop with an iPhone Pro")
            FlowDetail("Walking a shop takes an iPhone Pro or Pro Max from the 12 on, or an iPad Pro from 2020 on. "
                + "On this phone you can open the results of a shop you walked on one.")
        } actions: {
            Button("Sign in to see your results") { model.screen = .signIn }
                .buttonStyle(AppButtonStyle())
            Button("See an example shop") { model.openExample() }
                .buttonStyle(AppButtonStyle(.secondary))
        }
    }
}
