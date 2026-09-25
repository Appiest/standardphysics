import SwiftUI
import simd

struct CaptureScreen: View {
    @ObservedObject var model: AppModel
    @StateObject private var capture = CaptureSessionStore()
    @Environment(\.scenePhase) private var scenePhase

    /// The room paints itself in, so the arrow, the map and the tally all go:
    /// an unpainted wall says where to walk and how much is left at once.
    private var painting: Bool { model.developerMode }

    var body: some View {
        ZStack {
            RoomCaptureContainer(store: capture).ignoresSafeArea()
            LinearGradient(
                colors: [AppTheme.captureScrimTop, AppTheme.transparent, AppTheme.captureScrimBottom],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
            .allowsHitTesting(false)

            if painting {
                PaintedCoverageView(session: capture.controller?.arSession, paint: capture.paint)
                    .ignoresSafeArea()
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }

            VStack(spacing: AppTheme.Spacing.control) {
                captureHeader
                Spacer()
                if !painting && capture.phase == .scanning && !capture.coverage.isComplete {
                    GuidanceArrow(angle: capture.coverage.unfinishedDirection.radians)
                    Text("Walk this way. The yellow edges are still unscanned.")
                        .font(AppTheme.Typography.secondary)
                        .foregroundStyle(AppTheme.onDark)
                        .multilineTextAlignment(.center)
                }
                if !painting {
                    CoverageMapView(surfaces: capture.surfaces, coverage: capture.coverage)
                        .frame(height: AppTheme.Size.coverageMapHeight)
                    CoverageTally(coverage: capture.coverage)
                }
                if capture.hasDetailedGeometry && capture.phase == .scanning {
                    Label("Recording room details", systemImage: "checkmark")
                        .font(AppTheme.Typography.secondary).foregroundStyle(AppTheme.onDark)
                }
                finishButton
            }
            .padding(.horizontal, AppTheme.Spacing.section)
            .padding(.vertical, AppTheme.Spacing.small)
        }
        .onChange(of: capture.phase) { _, phase in
            if phase == .ready, let scan = capture.capturedScan {
                model.screen = .review(scan)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { capture.finish() }
        }
    }

    private var captureHeader: some View {
        HStack(spacing: AppTheme.Spacing.compact) {
            Button {
                capture.cancel()
                model.showStart()
            } label: {
                Image(systemName: "xmark")
                    .font(.headline)
                    .frame(width: AppTheme.Size.touchTarget, height: AppTheme.Size.touchTarget)
                    .background(AppTheme.captureChrome)
                    .clipShape(Circle())
            }
            .foregroundStyle(AppTheme.onDark)
            .accessibilityLabel("Cancel scan")

            Text(capture.instruction)
                .font(AppTheme.Typography.heading)
                .foregroundStyle(AppTheme.onDark)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, AppTheme.Spacing.card)
                .frame(minHeight: 52)
                .background(AppTheme.captureChrome)
                .clipShape(RoundedRectangle(cornerRadius: AppTheme.Radius.control, style: .continuous))
        }
    }

    @ViewBuilder private var finishButton: some View {
        switch capture.phase {
        case .preparing, .processing, .ready:
            ProgressView()
                .tint(AppTheme.onDark)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppTheme.Spacing.card)
                .background(AppTheme.captureProgress)
                .clipShape(RoundedRectangle(cornerRadius: AppTheme.Radius.control, style: .continuous))
        case .failed(let message):
            VStack(spacing: AppTheme.Spacing.small) {
                Text(message).font(AppTheme.Typography.heading).foregroundStyle(AppTheme.onDark)
                if capture.canRetrySave {
                    Button("Save again") { capture.retrySave() }
                        .buttonStyle(AppButtonStyle(.primary))
                }
                Button("Start a new scan") { model.showScanPrimer() }
                    .buttonStyle(AppButtonStyle(.capture))
                Button("Back to saved scans") { model.showStart() }
                    .buttonStyle(AppButtonStyle(.capture))
            }
        case .scanning:
            Button("Done") { capture.finish() }
                .buttonStyle(AppButtonStyle(capture.coverage.isComplete ? .primary : .capture))
        }
    }
}

/// How many surfaces are done, because the map shows where but not how many.
private struct CoverageTally: View {
    let coverage: CoverageSnapshot

    var body: some View {
        let done = coverage.surfaces.filter(\.isDone).count
        let total = coverage.surfaces.count
        Text(total == 0
            ? "Looking for the walls"
            : coverage.isComplete
                ? "Every surface covered"
                : "\(done) of \(total) surfaces covered")
            .font(AppTheme.Typography.measurement)
            .foregroundStyle(AppTheme.onDark)
            .accessibilityLabel(total == 0
                ? "Looking for the walls"
                : "\(done) of \(total) surfaces covered")
    }
}

private struct GuidanceArrow: View {
    let angle: Double

    var body: some View {
        Image(systemName: "arrow.up")
            .font(AppTheme.Typography.guidanceSymbol)
            .foregroundStyle(AppTheme.onDark)
            .frame(width: AppTheme.Size.guidanceMark, height: AppTheme.Size.guidanceMark)
            .background(AppTheme.accent.opacity(0.9))
            .clipShape(Circle())
            .rotationEffect(.radians(angle))
            .animation(AppTheme.Motion.quick, value: angle)
            .accessibilityLabel("Turn toward the unfinished area")
    }
}

private struct CoverageMapView: View {
    let surfaces: [SurfaceSnapshot]
    let coverage: CoverageSnapshot
    @State private var pulse = false

    var body: some View {
        Canvas { context, size in
            let coverageByID = Dictionary(
                coverage.surfaces.map { ($0.id, $0) }, uniquingKeysWith: { _, newer in newer })
            let walls = surfaces.filter(\.isWall)
            let points = walls.flatMap(endpoints)
            guard let bounds = MapBounds(points: points) else { return }

            for surface in walls {
                let line = endpoints(surface)
                guard line.count == 2 else { continue }
                let observedSegments = displayedSegments(for: coverageByID[surface.id])
                draw(
                    observedSegments: observedSegments,
                    along: line,
                    bounds: bounds,
                    size: size,
                    context: &context
                )
            }
        }
        .padding(AppTheme.Spacing.card)
        .background(AppTheme.captureMap)
        .clipShape(RoundedRectangle(cornerRadius: AppTheme.Radius.map, style: .continuous))
        .onAppear {
            withAnimation(AppTheme.Motion.pulse) { pulse.toggle() }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(coverage.isComplete ? "The room is covered" : "Room coverage map")
    }

    private func endpoints(_ surface: SurfaceSnapshot) -> [SIMD2<Float>] {
        let left = surface.transform * SIMD4(-surface.width / 2, 0, 0, 1)
        let right = surface.transform * SIMD4(surface.width / 2, 0, 0, 1)
        return [SIMD2(left.x, left.z), SIMD2(right.x, right.z)]
    }

    private func draw(
        observedSegments: [Bool],
        along line: [SIMD2<Float>],
        bounds: MapBounds,
        size: CGSize,
        context: inout GraphicsContext
    ) {
        for (index, isObserved) in observedSegments.enumerated() {
            let start = point(on: line, fraction: Float(index) / Float(observedSegments.count))
            let end = point(on: line, fraction: Float(index + 1) / Float(observedSegments.count))
            var path = Path()
            path.move(to: bounds.project(start, into: size))
            path.addLine(to: bounds.project(end, into: size))
            context.stroke(
                path,
                with: .color(isObserved
                    ? AppTheme.scanLine
                    : AppTheme.coverageMissing.opacity(pulse ? 1 : 0.55)),
                style: StrokeStyle(
                    lineWidth: isObserved ? 8 : 5,
                    lineCap: .round,
                    dash: isObserved ? [] : [7, 7]
                )
            )
        }
    }

    private func point(on line: [SIMD2<Float>], fraction: Float) -> SIMD2<Float> {
        line[0] + (line[1] - line[0]) * fraction
    }

    private func displayedSegments(for surfaceCoverage: SurfaceCoverage?) -> [Bool] {
        let segments = surfaceCoverage?.observedSegments ?? [false]
        return coverage.isComplete ? Array(repeating: true, count: segments.count) : segments
    }
}

private struct MapBounds {
    let minX: Float
    let maxX: Float
    let minY: Float
    let maxY: Float

    init?(points: [SIMD2<Float>]) {
        guard let first = points.first else { return nil }
        minX = points.reduce(first.x) { min($0, $1.x) }
        maxX = points.reduce(first.x) { max($0, $1.x) }
        minY = points.reduce(first.y) { min($0, $1.y) }
        maxY = points.reduce(first.y) { max($0, $1.y) }
    }

    func project(_ point: SIMD2<Float>, into size: CGSize) -> CGPoint {
        let width = max(maxX - minX, 0.5)
        let height = max(maxY - minY, 0.5)
        return CGPoint(
            x: CGFloat((point.x - minX) / width) * size.width,
            y: CGFloat((point.y - minY) / height) * size.height
        )
    }
}
