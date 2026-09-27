import UIKit

/// The taps the phone gives, each tied to one kind of moment so the same
/// moment always feels the same.
@MainActor
enum Haptics {
    /// A wall on the walk is done.
    static func wallDone() {
        UIImpactFeedbackGenerator(style: .rigid).impactOccurred()
    }

    /// The walk has everything it needs.
    static func walkComplete() {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    /// Something needs the owner's attention soon, like the time limit.
    static func warning() {
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
    }

    /// An answer or a photo went through.
    static func sent() {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }
}
