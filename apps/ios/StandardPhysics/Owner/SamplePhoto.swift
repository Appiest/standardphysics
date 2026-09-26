import SwiftUI

/// What a good photo for one request looks like, drawn rather than
/// photographed, inside a viewfinder so it reads as the shot to take.
///
/// Each drawing marks the thing the team will check in blue: the step at the
/// doorway, the height band a handle has to sit in, the mat, the turning
/// circle. The owner sees what to get in frame before they lift the phone.
struct SamplePhoto: View {
    let requestID: String

    var body: some View {
        Canvas { context, size in
            let space = SketchSpace(size: size, inset: 20)
            let pen = SketchPen(context: context, space: space)
            Self.drawing(for: requestID)(pen)
            drawViewfinder(pen)
        }
        .accessibilityElement()
        .accessibilityLabel(Self.description(for: requestID))
    }

    private static func drawing(for requestID: String) -> (SketchPen) -> Void {
        switch requestID {
        case "entrance_threshold": drawDoorwayFromTheSide
        case "door_hardware": drawDoorHandle
        case "floor_surface": drawFloorInsideTheDoor
        case "restroom_turning_space": drawRestroomFromTheDoorway
        default: drawSomethingInFrame
        }
    }

    private static func description(for requestID: String) -> String {
        switch requestID {
        case "entrance_threshold":
            "A doorway seen from the side, with the floor and the bottom of the door in frame and the step marked."
        case "door_hardware":
            "A door seen straight on, with its handle inside a band marked 34 to 48 inches up."
        case "floor_surface":
            "The floor just inside a door, with a mat lying on it."
        case "restroom_turning_space":
            "A restroom seen from its doorway, with a 60 inch circle marked on the floor."
        default:
            "A viewfinder around the thing to photograph."
        }
    }

    private func drawViewfinder(_ pen: SketchPen) {
        let corners: [(CGFloat, CGFloat, CGFloat, CGFloat)] = [
            (-2, -2, 1, 1), (102, -2, -1, 1), (-2, 74, 1, -1), (102, 74, -1, -1),
        ]
        for (x, y, dx, dy) in corners {
            pen.line([(x, y + 9 * dy), (x, y), (x + 9 * dx, y)], width: 3, colour: AppTheme.ink)
        }
    }

    /// Side on: the pavement outside, the threshold, the floor inside, the
    /// door frame and the open door's edge, with the step between the two
    /// floor levels marked.
    private static func drawDoorwayFromTheSide(_ pen: SketchPen) {
        var ground = Path()
        for (index, corner) in [(0, 60), (40, 60), (46, 56), (100, 56), (100, 72), (0, 72)].enumerated() {
            let point = pen.space.point(CGFloat(corner.0), CGFloat(corner.1))
            if index == 0 { ground.move(to: point) } else { ground.addLine(to: point) }
        }
        ground.closeSubpath()
        pen.context.fill(ground, with: .color(AppTheme.ink.opacity(0.07)))
        pen.wall([(0, 60), (40, 60), (46, 56), (100, 56)])
        pen.box(46, 4, 4, 52, fill: AppTheme.ink.opacity(0.28))
        pen.box(51, 6, 3, 49)
        pen.line([(34, 56), (46, 56)], width: 1, colour: AppTheme.accent, dash: [2, 2])
        pen.line([(36, 56), (36, 60)], width: 2, colour: AppTheme.accent)
        pen.line([(34, 56), (38, 56)], width: 2, colour: AppTheme.accent)
        pen.line([(34, 60), (38, 60)], width: 2, colour: AppTheme.accent)
        pen.label("step", at: 32, 52, anchor: .trailing)
    }

    private static func drawDoorHandle(_ pen: SketchPen) {
        pen.box(20, 30, 62, 12, fill: AppTheme.accent.opacity(0.12), stroke: AppTheme.transparent)
        pen.line([(20, 30), (82, 30)], width: 1, colour: AppTheme.accent, dash: [4, 3])
        pen.line([(20, 42), (82, 42)], width: 1, colour: AppTheme.accent, dash: [4, 3])
        pen.label("48 in", at: 18, 30, anchor: .trailing)
        pen.label("34 in", at: 18, 42, anchor: .trailing)
        pen.wall([(30, 72), (30, 2), (74, 2), (74, 72)])
        pen.box(33, 5, 38, 67, fill: AppTheme.Sketch.furnitureFill)
        pen.oval(62, 35, 5, 5, fill: AppTheme.Sketch.paper)
        pen.line([(64.5, 37.5), (52, 37.5)], width: 3.5, colour: AppTheme.ink)
        pen.line([(0, 72), (100, 72)], width: 2, colour: AppTheme.ink)
    }

    private static func drawFloorInsideTheDoor(_ pen: SketchPen) {
        pen.wall([(0, 30), (100, 30)])
        pen.box(38, 4, 24, 26, fill: AppTheme.Sketch.paper)
        pen.line([(38, 30), (62, 30)], width: 3, colour: AppTheme.ink)
        pen.line([(0, 30), (-10, 72)], width: 1, colour: AppTheme.faintInk)
        pen.line([(100, 30), (110, 72)], width: 1, colour: AppTheme.faintInk)
        let mat: [(CGFloat, CGFloat)] = [(34, 36), (66, 36), (74, 62), (26, 62), (34, 36)]
        pen.line(mat, width: 2, colour: AppTheme.accent)
        for step in 1..<6 {
            let t = CGFloat(step) / 6
            pen.line([(34 - 8 * t, 36 + 26 * t), (66 + 8 * t, 36 + 26 * t)], width: 1, colour: AppTheme.accent.opacity(0.5))
        }
    }

    private static func drawRestroomFromTheDoorway(_ pen: SketchPen) {
        pen.line([(30, 14), (70, 14), (70, 46), (30, 46), (30, 14)], width: 2)
        pen.line([(30, 14), (0, 0)], width: 2)
        pen.line([(70, 14), (100, 0)], width: 2)
        pen.line([(30, 46), (0, 72)], width: 2)
        pen.line([(70, 46), (100, 72)], width: 2)
        pen.box(34, 28, 9, 8)
        pen.oval(33, 36, 11, 8)
        pen.box(60, 30, 9, 4)
        pen.line([(64.5, 34), (64.5, 44)], width: 2)
        pen.oval(26, 50, 48, 16, fill: AppTheme.accent.opacity(0.08), stroke: AppTheme.accent, dash: [5, 4])
        pen.label("60 in", at: 50, 58)
    }

    private static func drawSomethingInFrame(_ pen: SketchPen) {
        pen.box(30, 20, 40, 32)
    }
}
