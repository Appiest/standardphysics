import Foundation

/// Whether to show work that is not ready for a shop owner.
///
/// Kept in UserDefaults rather than behind a build configuration, because the
/// people who need it are testing a TestFlight build on their own phones and a
/// Release build is exactly what they are holding.
@MainActor
enum DeveloperMode {
    private static let key = "SP_DEVELOPER_MODE"

    static var isOn: Bool {
        get { UserDefaults.standard.bool(forKey: key) }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }
}
