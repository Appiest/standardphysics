import SwiftUI

/// A plan drawn in a 100 by 72 box and scaled to fit whatever frame it gets,
/// so every sketch in the app shares one set of coordinates.
struct SketchSpace {
    static let width: CGFloat = 100
    static let height: CGFloat = 72

    let scale: CGFloat
    let origin: CGPoint

    init(size: CGSize, inset: CGFloat = 6) {
        let available = CGSize(width: max(size.width - inset * 2, 1), height: max(size.height - inset * 2, 1))
        scale = min(available.width / Self.width, available.height / Self.height)
        origin = CGPoint(
            x: (size.width - Self.width * scale) / 2,
            y: (size.height - Self.height * scale) / 2
        )
    }

    func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
        CGPoint(x: origin.x + x * scale, y: origin.y + y * scale)
    }

    func rect(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat) -> CGRect {
        CGRect(origin: point(x, y), size: CGSize(width: width * scale, height: height * scale))
    }

    func length(_ units: CGFloat) -> CGFloat { units * scale }
}

/// The shop on the welcome screen, drawing itself: the walls go down, the
/// furniture appears, the owner's walk traces around it, a narrow aisle is
/// measured in red, and one table slides over until the aisle measures wide
/// enough and turns green. It is the whole product in eight seconds.
///
/// The drawing is a pure function of time, so any moment can be rendered on
/// its own and the loop's last frame is its first: an empty sheet, reached at
/// 7.8 seconds and held until the loop restarts at 8.
struct RoomSketch: View {
    enum Mode { case story, measuring }

    var mode: Mode = .story
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let storyLength: Double = 8
    static let sweepLength: Double = 3.2

    var body: some View {
        TimelineView(.animation(paused: reduceMotion)) { timeline in
            Canvas { context, size in
                let time = Self.frozenTime ?? (reduceMotion ? restingTime : timeline.date.timeIntervalSinceReferenceDate)
                RoomDrawing(space: SketchSpace(size: size), mode: mode).draw(at: time, in: &context)
            }
        }
        .accessibilityElement()
        .accessibilityLabel(accessibilityDescription)
    }

    private var restingTime: Double { mode == .story ? 7 : 1.6 }

    /// A Debug build can hold the drawing at one moment, to check a frame.
    private static var frozenTime: Double? {
#if DEBUG
        ProcessInfo.processInfo.environment["SP_DEBUG_SKETCH_TIME"].flatMap(Double.init)
#else
        nil
#endif
    }

    private var accessibilityDescription: String {
        switch mode {
        case .story:
            "A floor plan of a shop. An aisle between two tables measures 31 inches in red. "
                + "One table moves over and the aisle measures 48 inches in green."
        case .measuring:
            "A floor plan of a shop with a line sweeping across it."
        }
    }
}

/// The frame-by-frame drawing behind `RoomSketch`.
struct RoomDrawing {
    let space: SketchSpace
    let mode: RoomSketch.Mode

    private typealias Motion = AppTheme.Motion

    private enum Plan {
        static let room = (left: CGFloat(6), top: CGFloat(6), right: CGFloat(94), bottom: CGFloat(66))
        static let doorway = (left: CGFloat(14), right: CGFloat(28))
        static let counter = (x: CGFloat(60), y: CGFloat(10), width: CGFloat(28), height: CGFloat(10))
        static let tableA = (x: CGFloat(30), y: CGFloat(28), width: CGFloat(16), height: CGFloat(12))
        static let tableB = (x: CGFloat(53), y: CGFloat(28), width: CGFloat(14), height: CGFloat(12))
        static let tableSlide: CGFloat = 5
        static let aisleLine: CGFloat = 44
        static let walk = (x: CGFloat(12), y: CGFloat(23), width: CGFloat(76), height: CGFloat(37))
    }

    func draw(at time: Double, in context: inout GraphicsContext) {
        switch mode {
        case .story:
            let t = time.truncatingRemainder(dividingBy: RoomSketch.storyLength)
            drawStory(at: t < 0 ? t + RoomSketch.storyLength : t, in: &context)
        case .measuring:
            drawMeasuring(at: time, in: &context)
        }
    }

    // MARK: The story

    private func drawStory(at t: Double, in context: inout GraphicsContext) {
        let fade = 1 - smoothstep(t, from: 7.1, to: 7.8)
        guard fade > 0 else { return }
        context.opacity = fade

        drawWalls(progress: Motion.settle(t, from: 0.15, rate: 4.5), in: &context)
        drawDoorSwing(opacity: Motion.settle(t, from: 1.3), in: &context)
        drawFurniture(at: t, in: &context)
        drawWalk(progress: smoothstep(t, from: 2.3, to: 4.5), in: &context)
        drawAisle(at: t, in: &context)
    }

    private func drawWalls(progress: Double, in context: inout GraphicsContext) {
        guard progress > 0 else { return }
        context.stroke(
            wallsPath.trimmedPath(from: 0, to: progress),
            with: .color(AppTheme.ink),
            style: StrokeStyle(lineWidth: AppTheme.Sketch.wall, lineCap: .square, lineJoin: .miter)
        )
    }

    private var wallsPath: Path {
        let room = Plan.room
        var path = Path()
        path.move(to: space.point(Plan.doorway.right, room.bottom))
        path.addLine(to: space.point(room.right, room.bottom))
        path.addLine(to: space.point(room.right, room.top))
        path.addLine(to: space.point(room.left, room.top))
        path.addLine(to: space.point(room.left, room.bottom))
        path.addLine(to: space.point(Plan.doorway.left, room.bottom))
        return path
    }

    private func drawDoorSwing(opacity: Double, in context: inout GraphicsContext) {
        guard opacity > 0 else { return }
        let hinge = space.point(Plan.doorway.left, Plan.room.bottom)
        let reach = space.length(Plan.doorway.right - Plan.doorway.left)
        var leaf = Path()
        leaf.move(to: hinge)
        leaf.addLine(to: CGPoint(x: hinge.x, y: hinge.y - reach))
        var arc = Path()
        arc.addArc(center: hinge, radius: reach, startAngle: .degrees(0), endAngle: .degrees(-90), clockwise: true)
        var layer = context
        layer.opacity *= opacity
        layer.stroke(leaf, with: .color(AppTheme.ink), lineWidth: AppTheme.Sketch.furniture)
        layer.stroke(arc, with: .color(AppTheme.mutedInk), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
    }

    private func drawFurniture(at t: Double, in context: inout GraphicsContext) {
        let pieces = [
            (Plan.counter, 1.6, CGFloat(0)),
            (Plan.tableA, 1.72, CGFloat(0)),
            (Plan.tableB, 1.84, tableSlide(at: t)),
        ]
        for (piece, start, slide) in pieces {
            let shown = Motion.settle(t, from: start)
            guard shown > 0 else { continue }
            let rect = space.rect(piece.x + slide, piece.y, piece.width, piece.height)
            drawFurniture(rect, shown: shown, in: &context)
        }
    }

    private func drawFurniture(_ rect: CGRect, shown: Double, in context: inout GraphicsContext) {
        let grow = 0.92 + 0.08 * shown
        let scaled = rect.insetBy(dx: rect.width * (1 - grow) / 2, dy: rect.height * (1 - grow) / 2)
        var layer = context
        layer.opacity *= shown
        layer.fill(Path(scaled), with: .color(AppTheme.Sketch.furnitureFill))
        layer.stroke(Path(scaled), with: .color(AppTheme.ink), lineWidth: AppTheme.Sketch.furniture)
    }

    private func tableSlide(at t: Double) -> CGFloat {
        Plan.tableSlide * CGFloat(Motion.settle(t, from: 5.6, rate: 7))
    }

    private func drawWalk(progress: Double, in context: inout GraphicsContext) {
        guard progress > 0 else { return }
        let walk = Path(
            roundedRect: space.rect(Plan.walk.x, Plan.walk.y, Plan.walk.width, Plan.walk.height),
            cornerRadius: space.length(6)
        )
        let traced = walk.trimmedPath(from: 0, to: progress)
        context.stroke(
            traced,
            with: .color(AppTheme.accent),
            style: StrokeStyle(lineWidth: AppTheme.Sketch.path, lineCap: .round, dash: AppTheme.Sketch.dash)
        )
        guard progress < 1, let walker = traced.currentPoint else { return }
        context.fill(
            Path(ellipseIn: CGRect(x: walker.x - 5, y: walker.y - 5, width: 10, height: 10)),
            with: .color(AppTheme.accent)
        )
    }

    /// The gap between the two tables, measured: red at 31 inches, then green
    /// at 48 once the right-hand table has moved over.
    private func drawAisle(at t: Double, in context: inout GraphicsContext) {
        let drawn = Motion.settle(t, from: 4.6)
        guard drawn > 0 else { return }
        let fixed = Motion.settle(t, from: 5.8, rate: 10)
        let left = Plan.tableA.x + Plan.tableA.width
        let right = Plan.tableB.x + tableSlide(at: t)
        let lineY = Plan.aisleLine
        let colour = fixed > 0.5 ? AppTheme.pass : AppTheme.problem

        var layer = context
        layer.opacity *= drawn
        var dimension = Path()
        for x in [left, right] {
            dimension.move(to: space.point(x, Plan.tableA.y + Plan.tableA.height + 1))
            dimension.addLine(to: space.point(x, lineY + 2))
        }
        dimension.move(to: space.point(left, lineY))
        dimension.addLine(to: space.point(left + (right - left) * CGFloat(drawn), lineY))
        layer.stroke(dimension, with: .color(colour), lineWidth: AppTheme.Sketch.dimension)

        let labelPoint = space.point((left + right) / 2, lineY + 3)
        drawLabel("31 in", colour: AppTheme.problem, opacity: Motion.settle(t, from: 4.8) * (1 - fixed),
            at: labelPoint, in: &layer)
        drawLabel("48 in", colour: AppTheme.pass, opacity: fixed, at: labelPoint, in: &layer)
    }

    private func drawLabel(_ text: String, colour: Color, opacity: Double, at point: CGPoint,
                           in context: inout GraphicsContext) {
        guard opacity > 0 else { return }
        var layer = context
        layer.opacity *= opacity
        let label = layer.resolve(Text(text).font(AppTheme.Typography.measurement).foregroundStyle(colour))
        let size = label.measure(in: CGSize(width: 200, height: 60))
        let backing = CGRect(x: point.x - size.width / 2 - 4, y: point.y - 1, width: size.width + 8, height: size.height + 2)
        layer.fill(Path(backing), with: .color(AppTheme.Sketch.paper))
        layer.draw(label, at: point, anchor: .top)
    }

    // MARK: Measuring

    /// The same shop, already drawn, with a line sweeping across it and the
    /// walls ticked off as it passes.
    private func drawMeasuring(at time: Double, in context: inout GraphicsContext) {
        let t = time.truncatingRemainder(dividingBy: RoomSketch.sweepLength)
        let room = Plan.room
        let sweepX = room.left + (room.right - room.left) * CGFloat(smoothstep(t, from: 0, to: 2.6))

        let swept = space.rect(room.left, room.top, sweepX - room.left, room.bottom - room.top)
        context.fill(Path(swept), with: .color(AppTheme.accent.opacity(0.07 * (1 - smoothstep(t, from: 2.6, to: 3.2)))))
        drawWalls(progress: 1, in: &context)
        drawDoorSwing(opacity: 1, in: &context)
        for piece in [Plan.counter, Plan.tableA, Plan.tableB] {
            drawFurniture(space.rect(piece.x, piece.y, piece.width, piece.height), shown: 1, in: &context)
        }
        drawTicks(upTo: sweepX, fading: 1 - smoothstep(t, from: 2.6, to: 3.2), in: &context)

        var sweep = Path()
        sweep.move(to: space.point(sweepX, room.top - 3))
        sweep.addLine(to: space.point(sweepX, room.bottom + 3))
        context.stroke(sweep, with: .color(AppTheme.accent.opacity(t < 2.6 ? 1 : 0)), lineWidth: 2)
    }

    private func drawTicks(upTo sweepX: CGFloat, fading opacity: Double, in context: inout GraphicsContext) {
        var ticks = Path()
        for x in stride(from: Plan.room.left + 8, to: Plan.room.right, by: 8) where x <= sweepX {
            ticks.move(to: space.point(x, Plan.room.top - 3))
            ticks.addLine(to: space.point(x, Plan.room.top + 1))
            ticks.move(to: space.point(x, Plan.room.bottom - 1))
            ticks.addLine(to: space.point(x, Plan.room.bottom + 3))
        }
        context.stroke(ticks, with: .color(AppTheme.accent.opacity(opacity)), lineWidth: AppTheme.Sketch.dimension)
    }

    private func smoothstep(_ t: Double, from start: Double, to end: Double) -> Double {
        let x = min(max((t - start) / (end - start), 0), 1)
        return x * x * (3 - 2 * x)
    }
}
