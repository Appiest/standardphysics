import CoreImage
import CoreImage.CIFilterBuiltins
import SwiftUI
import UIKit

/// The surface every screen that is not the camera sits on: ruled paper with a
/// grain in it, the same sheet the workspace draws on in the browser.
struct DraftingPaper: View {
    var body: some View {
        AppTheme.canvas
            .overlay(DraftingGrid())
            .overlay(PaperGrain())
            .ignoresSafeArea()
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

/// Two rules, a fine one every 24 points and a heavy one every 120, drawn edge
/// to edge so the sheet reads as squared paper rather than as a pattern placed
/// on it.
private struct DraftingGrid: View {
    var body: some View {
        Canvas(rendersAsynchronously: false) { context, size in
            draw(spacing: AppTheme.Grid.minor, colour: AppTheme.gridMinor, in: &context, size: size)
            draw(spacing: AppTheme.Grid.major, colour: AppTheme.gridMajor, in: &context, size: size)
        }
    }

    private func draw(spacing: CGFloat, colour: Color, in context: inout GraphicsContext, size: CGSize) {
        var path = Path()
        for x in stride(from: 0, through: size.width, by: spacing) {
            path.move(to: CGPoint(x: x, y: 0))
            path.addLine(to: CGPoint(x: x, y: size.height))
        }
        for y in stride(from: 0, through: size.height, by: spacing) {
            path.move(to: CGPoint(x: 0, y: y))
            path.addLine(to: CGPoint(x: size.width, y: y))
        }
        context.stroke(path, with: .color(colour), lineWidth: AppTheme.Size.hairline)
    }
}

/// A tile of monochrome noise, multiplied into the sheet so the paper has tooth.
///
/// The tile is rendered once and reused. Generating it per frame costs a
/// Core Image pass on every layout pass, which is visible while scrolling.
private struct PaperGrain: View {
    private static let tile = makeTile(side: 160)

    var body: some View {
        if let tile = Self.tile {
            Image(uiImage: tile)
                .resizable(resizingMode: .tile)
                .blendMode(.multiply)
                .opacity(0.09)
        }
    }

    private static func makeTile(side: CGFloat) -> UIImage? {
        let noise = CIFilter.randomGenerator().outputImage
        guard let noise else { return nil }
        let grey = CIFilter.photoEffectMono()
        grey.inputImage = noise
        let extent = CGRect(x: 0, y: 0, width: side, height: side)
        guard let output = grey.outputImage?.cropped(to: extent),
              let cgImage = CIContext().createCGImage(output, from: extent) else { return nil }
        return UIImage(cgImage: cgImage)
    }
}

/// A ruled hairline, the way a divider is drawn on a sheet rather than a gap
/// between two floating cards.
struct DraftingRule: View {
    var body: some View {
        Rectangle()
            .fill(AppTheme.rule)
            .frame(height: AppTheme.Size.hairline)
            .accessibilityHidden(true)
    }
}
