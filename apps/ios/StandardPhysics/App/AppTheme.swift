import SwiftUI

/// The drafting sheet, on a phone.
///
/// Every value here is the iOS half of a pair: the other half lives in
/// `apps/web/src/app/(workspace)/globals.css`, and the two have to agree or the
/// phone and the workspace stop looking like one product. Changing a colour or
/// a typeface means changing it in both places.
enum AppTheme {
    /// The sheet the drawing sits on.
    static let canvas = Color(hex: 0xF6F5F1)
    /// A raised sheet: rows, cards, anything that reads as a separate piece of paper.
    static let panel = Color(hex: 0xFCFBF8)
    /// The hairline a draughtsman rules between things.
    static let rule = Color(hex: 0xE3E0D8)
    static let ink = Color(hex: 0x1B1C1E)
    static let mutedInk = Color(hex: 0x5D5E61)
    static let faintInk = Color(hex: 0x8A8A88)
    static let accent = Color(hex: 0x2F5E9E)
    static let problem = Color(hex: 0xC8372D)
    static let pass = Color(hex: 0x2E7D4F)
    static let gridMinor = Color(hex: 0xECEBE5)
    static let gridMajor = Color(hex: 0xE0DED6)

    /// The old name for `problem`, kept because a violation and a destructive
    /// action want the same red and half the app already asks for this one.
    static let warning = problem

    static let scanLine = Color(hex: 0x8FB4DE)
    static let onDark = Color.white
    static let transparent = Color.clear

    // Camera chrome. These sit on the live feed rather than on paper, so they
    // stay dark and take no colour from the sheet.
    static let captureScrimTop = Color.black.opacity(0.56)
    static let captureScrimBottom = Color.black.opacity(0.50)
    static let captureChrome = Color.black.opacity(0.58)
    static let captureProgress = Color.black.opacity(0.72)
    static let captureMap = Color.black.opacity(0.62)
    static let coveragePendingBright = Color.white.opacity(0.90)
    static let coveragePendingDim = Color.white.opacity(0.40)

    /// A secondary control is a piece of the sheet, not a smudge on it: a
    /// translucent black fill lets the grid show through and reads as dirt.
    static let secondaryIdle = panel
    static let secondaryPressed = rule
    static let fieldOutline = rule

    /// Libre Franklin sets headings, Karla sets everything a person reads at
    /// length, and Atkinson Mono sets anything measured. Each role is tied to a
    /// text style so the whole app still answers to Dynamic Type.
    enum Typography {
        private static let display = "LibreFranklin-ExtraBold"
        private static let displayTitle = "LibreFranklin-Bold"
        private static let bodyFace = "Karla-Regular"
        private static let bodyBold = "Karla-Bold"
        private static let monoFace = "AtkinsonHyperlegibleMono-Regular"

        static let hero = Font.custom(display, size: 44, relativeTo: .largeTitle)
        static let status = Font.custom(display, size: 34, relativeTo: .title)
        static let title = Font.custom(displayTitle, size: 26, relativeTo: .title2)
        static let heading = Font.custom(displayTitle, size: 17, relativeTo: .headline)
        static let lead = Font.custom(bodyFace, size: 19, relativeTo: .title3)
        static let body = Font.custom(bodyFace, size: 17, relativeTo: .body)
        static let secondary = Font.custom(bodyFace, size: 15, relativeTo: .subheadline)
        static let control = Font.custom(bodyBold, size: 17, relativeTo: .headline)
        static let measurement = Font.custom(monoFace, size: 15, relativeTo: .subheadline)

        /// SF Symbols are drawn to the system font's metrics, so the marks keep it.
        static let guidanceSymbol = Font.system(size: 34, weight: .bold)
        static let statusSymbol = Font.system(size: 38, weight: .bold)
    }

    enum Spacing {
        static let page: CGFloat = 28
        static let section: CGFloat = 24
        static let card: CGFloat = 18
        static let control: CGFloat = 16
        static let compact: CGFloat = 14
        static let small: CGFloat = 12
    }

    /// Paper has square corners. Only the controls are cut, and barely.
    enum Radius {
        static let control: CGFloat = 4
        static let field: CGFloat = 4
        static let map: CGFloat = 4
        static let sheet: CGFloat = 0
    }

    enum Size {
        static let touchTarget: CGFloat = 44
        static let guidanceMark: CGFloat = 68
        static let statusMark: CGFloat = 82
        static let coverageMapHeight: CGFloat = 136
        static let hairline: CGFloat = 1
    }

    enum Grid {
        static let minor: CGFloat = 24
        static let major: CGFloat = 120
    }

    enum Shadow {
        static let color = Color.black.opacity(0.10)
        static let radius: CGFloat = 10
        static let y: CGFloat = 4
    }

    enum Motion {
        static let quick = Animation.easeOut(duration: 0.25)
        static let pulse = Animation.easeInOut(duration: 1).repeatForever(autoreverses: true)
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

struct AppButtonStyle: ButtonStyle {
    enum Variant { case primary, secondary, destructive, capture }
    let variant: Variant
    @Environment(\.isEnabled) private var isEnabled

    init(_ variant: Variant = .primary) { self.variant = variant }

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AppTheme.Typography.control)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppTheme.Spacing.control)
            .foregroundStyle(label)
            .background(background(pressed: configuration.isPressed))
            .clipShape(RoundedRectangle(cornerRadius: AppTheme.Radius.control, style: .continuous))
            .opacity(isEnabled ? 1 : 0.45)
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }

    /// Red names the consequence, it does not fill the button. A destructive
    /// action sits on the same quiet surface as any other secondary control.
    private var label: Color {
        switch variant {
        case .secondary: AppTheme.ink
        case .destructive: AppTheme.problem
        case .primary, .capture: AppTheme.onDark
        }
    }

    private func background(pressed: Bool) -> Color {
        switch variant {
        case .primary: pressed ? AppTheme.ink.opacity(0.78) : AppTheme.ink
        case .secondary, .destructive: pressed ? AppTheme.secondaryPressed : AppTheme.secondaryIdle
        case .capture: pressed ? AppTheme.captureProgress : AppTheme.captureChrome
        }
    }
}
