import UIKit
import UserNotifications

/// Notifications, asked for only during the measuring wait.
///
/// The token Apple hands back arrives in the app delegate, which knows
/// nothing about accounts, so it is posted here and `AppModel` sends it to
/// the server for whoever is signed in.
@MainActor
enum PushRegistration {
    static let deviceTokenArrived = Notification.Name("StandardPhysicsDeviceTokenArrived")
    /// The owner tapped a notification about one shop.
    static let shopOpened = Notification.Name("StandardPhysicsShopOpened")

    /// Which of Apple's push servers the token belongs to. A Debug build is
    /// signed for development and gets sandbox tokens.
    static var environment: String {
#if DEBUG
        "sandbox"
#else
        "production"
#endif
    }

    static func status() async -> UNAuthorizationStatus {
        await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
    }

    /// Opens the system's question. Returns whether the owner said yes.
    @discardableResult
    static func request() async -> Bool {
        let granted = (try? await UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        if granted { UIApplication.shared.registerForRemoteNotifications() }
        return granted
    }

    /// Registers again at launch and after a sign-in, when the owner already
    /// said yes, so the server always has this phone under the current
    /// account.
    static func registerIfAllowed() {
        Task {
            guard await status() == .authorized else { return }
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    nonisolated static func hex(_ token: Data) -> String {
        token.map { String(format: "%02x", $0) }.joined()
    }
}

/// Pushes carry the shop's `scan_id` beside the alert (notifications.py on
/// the server), so tapping one opens that shop.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        guard let text = info["scan_id"] as? String, let scanID = UUID(uuidString: text) else { return }
        await MainActor.run {
            NotificationCenter.default.post(name: PushRegistration.shopOpened, object: scanID)
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        NotificationCenter.default.post(name: PushRegistration.deviceTokenArrived, object: PushRegistration.hex(deviceToken))
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {}
}
