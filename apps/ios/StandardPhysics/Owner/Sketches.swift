import SwiftUI

/// The walk itself, drawn as the plan it produces.
///
/// The path is inset from the walls by the stride the copy asks for, so the
/// picture and the instruction are the same claim. The counter is there
/// because a route has to get around something to be worth measuring.
struct WalkPlan: View {
    var body: some View {
        Canvas { context, size in
            let margin: CGFloat = 26
            let room = CGRect(
                x: margin, y: margin,
                width: size.width - margin * 2,
                height: size.height - margin * 2
            )
            context.stroke(Path(room), with: .color(AppTheme.ink), lineWidth: AppTheme.Sketch.wall)

            let counter = CGRect(
                x: room.minX + room.width * 0.52,
                y: room.minY,
                width: room.width * 0.34,
                height: room.height * 0.22
            )
            context.fill(Path(counter), with: .color(AppTheme.Sketch.furnitureFill))
            context.stroke(Path(counter), with: .color(AppTheme.ink), lineWidth: AppTheme.Sketch.furniture)

            let walk = room.insetBy(dx: room.width * 0.17, dy: room.height * 0.32)
            context.stroke(
                Path(walk),
                with: .color(AppTheme.accent),
                style: StrokeStyle(lineWidth: AppTheme.Sketch.path, lineCap: .round, dash: AppTheme.Sketch.dash)
            )

            for point in strideMarks(on: walk) {
                context.fill(
                    Path(ellipseIn: CGRect(x: point.x - 3.5, y: point.y - 3.5, width: 7, height: 7)),
                    with: .color(AppTheme.accent)
                )
            }
        }
        .accessibilityElement()
        .accessibilityLabel(
            "A plan of a room with a dashed path running all the way around the inside, a stride away from the walls."
        )
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

/// Strokes shared by the small drawings, so a wall is a wall in every one.
struct SketchPen {
    let context: GraphicsContext
    let space: SketchSpace

    func line(_ points: [(CGFloat, CGFloat)], width: CGFloat = AppTheme.Sketch.furniture,
              colour: Color = AppTheme.ink, dash: [CGFloat] = []) {
        guard let first = points.first else { return }
        var path = Path()
        path.move(to: space.point(first.0, first.1))
        for point in points.dropFirst() { path.addLine(to: space.point(point.0, point.1)) }
        context.stroke(path, with: .color(colour), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round, dash: dash))
    }

    func wall(_ points: [(CGFloat, CGFloat)], colour: Color = AppTheme.ink) {
        line(points, width: AppTheme.Sketch.wall, colour: colour)
    }

    func box(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat,
             fill: Color = AppTheme.Sketch.furnitureFill, stroke: Color = AppTheme.ink) {
        let rect = Path(space.rect(x, y, width, height))
        context.fill(rect, with: .color(fill))
        context.stroke(rect, with: .color(stroke), lineWidth: AppTheme.Sketch.furniture)
    }

    func oval(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat,
              fill: Color = AppTheme.Sketch.furnitureFill, stroke: Color = AppTheme.ink, dash: [CGFloat] = []) {
        let oval = Path(ellipseIn: space.rect(x, y, width, height))
        context.fill(oval, with: .color(fill))
        context.stroke(oval, with: .color(stroke), style: StrokeStyle(lineWidth: AppTheme.Sketch.furniture, dash: dash))
    }

    /// A door in plan: the leaf standing open from its hinge, and the arc its
    /// free edge sweeps.
    func doorSwing(hinge: (CGFloat, CGFloat), width: CGFloat, from start: Angle, to end: Angle,
                   colour: Color = AppTheme.ink, clockwise: Bool = true) {
        let centre = space.point(hinge.0, hinge.1)
        let radius = space.length(width)
        var leaf = Path()
        leaf.move(to: centre)
        leaf.addLine(to: CGPoint(x: centre.x + radius * cos(end.radians), y: centre.y + radius * sin(end.radians)))
        var arc = Path()
        arc.addArc(center: centre, radius: radius, startAngle: start, endAngle: end, clockwise: clockwise)
        context.stroke(leaf, with: .color(colour), lineWidth: AppTheme.Sketch.furniture + 0.5)
        context.stroke(arc, with: .color(colour.opacity(0.7)), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
    }

    /// Measurements are set in the measuring face; a word like "step" is not
    /// a measurement and is set in the body face.
    func label(_ text: String, at x: CGFloat, _ y: CGFloat, colour: Color = AppTheme.accent,
               anchor: UnitPoint = .center, isMeasurement: Bool = true) {
        context.draw(
            Text(text)
                .font(isMeasurement ? AppTheme.Typography.measurement : AppTheme.Typography.secondary)
                .foregroundStyle(colour),
            at: space.point(x, y),
            anchor: anchor
        )
    }
}

/// A drawing on its own sheet, the frame every sketch on a question screen
/// sits in.
struct SketchSheet<Content: View>: View {
    var height: CGFloat = 200
    @ViewBuilder let content: Content

    var body: some View {
        content
            .frame(maxWidth: .infinity)
            .frame(height: height)
            .raisedPanel(AppTheme.Sketch.paper)
    }
}

/// The plan behind each yes or no question: where a customer restroom sits,
/// or which doors count as inside doors.
struct QuestionSketch: View {
    let requestID: String

    var body: some View {
        Canvas { context, size in
            let pen = SketchPen(context: context, space: SketchSpace(size: size, inset: 16))
            if requestID == "restroom" {
                drawRestroom(pen)
            } else {
                drawInsideDoors(pen)
            }
        }
        .accessibilityElement()
        .accessibilityLabel(requestID == "restroom"
            ? "A shop plan with a small restroom in the back corner."
            : "A shop plan. The door between the shop and a back room is highlighted. The front door is not.")
    }

    private func drawShell(_ pen: SketchPen) {
        pen.wall([(40, 66), (6, 66), (6, 6), (94, 6), (94, 66), (54, 66)])
        pen.doorSwing(hinge: (40, 66), width: 14, from: .degrees(0), to: .degrees(-90), colour: AppTheme.faintInk)
    }

    private func drawRestroom(_ pen: SketchPen) {
        drawShell(pen)
        pen.box(66, 6, 28, 26, fill: AppTheme.accent.opacity(0.10), stroke: AppTheme.transparent)
        pen.wall([(66, 6), (66, 32), (74, 32)], colour: AppTheme.accent)
        pen.wall([(86, 32), (94, 32)], colour: AppTheme.accent)
        pen.doorSwing(hinge: (74, 32), width: 12, from: .degrees(0), to: .degrees(90), colour: AppTheme.accent,
            clockwise: false)
        pen.box(84, 9, 7, 4)
        pen.oval(84.5, 13, 6, 8)
        pen.box(70, 9, 8, 6)
        pen.box(14, 12, 20, 8)
    }

    private func drawInsideDoors(_ pen: SketchPen) {
        drawShell(pen)
        pen.wall([(62, 6), (62, 24)])
        pen.wall([(62, 38), (62, 66)])
        pen.doorSwing(hinge: (62, 38), width: 14, from: .degrees(-90), to: .degrees(0), colour: AppTheme.accent,
            clockwise: false)
        pen.box(14, 12, 20, 8)
        pen.box(20, 34, 16, 14)
        pen.box(72, 12, 16, 10)
    }
}

/// What an inside door's push force is: the gauge pressed against the door
/// at the handle, pushing it open.
struct PushGaugeSketch: View {
    private static let hinge = (x: CGFloat(20), y: CGFloat(56))
    private static let leafLength: CGFloat = 50
    private static let openAngle = Angle.degrees(-30)

    var body: some View {
        Canvas { context, size in
            let pen = SketchPen(context: context, space: SketchSpace(size: size, inset: 14))
            let hinge = Self.hinge
            pen.wall([(0, hinge.y), (hinge.x, hinge.y)])
            pen.wall([(hinge.x + Self.leafLength, hinge.y), (100, hinge.y)])
            pen.doorSwing(hinge: (hinge.x, hinge.y), width: Self.leafLength, from: .degrees(0), to: Self.openAngle)
            drawGauge(pen)
        }
        .accessibilityElement()
        .accessibilityLabel("A door in plan, pushed open at the handle with a small gauge that reads in pounds.")
    }

    /// The gauge sits against the door near its free edge, on the side it is
    /// pushed from, with an arrow for the push.
    private func drawGauge(_ pen: SketchPen) {
        let along = (cos(Self.openAngle.radians), sin(Self.openAngle.radians))
        let pushSide = (-along.1, along.0)
        let contact = point(from: (Self.hinge.x, Self.hinge.y), along, Self.leafLength * 0.86)
        let dial = point(from: contact, pushSide, 15)
        pen.line([point(from: contact, pushSide, 9), point(from: contact, pushSide, 1.5)],
            width: 3, colour: AppTheme.accent)
        let tip = point(from: contact, pushSide, 1.5)
        pen.line([point(from: tip, pushSide, 4, sideways: 3), tip, point(from: tip, pushSide, 4, sideways: -3)],
            width: 3, colour: AppTheme.accent)
        pen.oval(dial.0 - 7, dial.1 - 7, 14, 14, fill: AppTheme.Sketch.paper, stroke: AppTheme.ink)
        pen.line([dial, (dial.0 + 3.5, dial.1 - 4)], width: 2, colour: AppTheme.problem)
        pen.label("lb", at: dial.0 + 10, dial.1, colour: AppTheme.ink, anchor: .leading, isMeasurement: false)
    }

    private func point(from start: (CGFloat, CGFloat), _ direction: (Double, Double), _ distance: CGFloat,
                       sideways: CGFloat = 0) -> (CGFloat, CGFloat) {
        let across = (-direction.1, direction.0)
        return (
            start.0 + CGFloat(direction.0) * distance + CGFloat(across.0) * sideways,
            start.1 + CGFloat(direction.1) * distance + CGFloat(across.1) * sideways
        )
    }
}

/// The camera looking at a shop, for the screen that asks for the camera.
struct CameraSketch: View {
    let allowed: Bool

    var body: some View {
        Canvas { context, size in
            let space = SketchSpace(size: size, inset: 12)
            let pen = SketchPen(context: context, space: space)
            pen.wall([(40, 58), (18, 58), (18, 14), (82, 14), (82, 58), (54, 58)], colour: AppTheme.ink)
            pen.box(58, 18, 20, 8)
            pen.box(28, 30, 14, 12)
            let corners: [(CGFloat, CGFloat, CGFloat, CGFloat)] = [(8, 4, 1, 1), (92, 4, -1, 1), (8, 68, 1, -1), (92, 68, -1, -1)]
            for (x, y, dx, dy) in corners {
                pen.line([(x, y + 8 * dy), (x, y), (x + 8 * dx, y)], width: 3,
                    colour: allowed ? AppTheme.accent : AppTheme.faintInk)
            }
        }
        .accessibilityHidden(true)
    }
}
