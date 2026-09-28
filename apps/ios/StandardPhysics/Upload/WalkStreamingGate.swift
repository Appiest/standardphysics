import Foundation
import Network
import UIKit

/// Whether a walk's keyframes may go to the server right now.
protocol WalkStreamingGate: AnyObject, Sendable {
    var allowsStreaming: Bool { get }
}

/// Opens only on Wi-Fi-like networks while the app is in front.
///
/// A cellular or Low Data Mode connection keeps the walk to the old behavior,
/// where every photo goes up after the walk, so a four-minute walk never
/// spends a few hundred megabytes of someone's data plan behind their back.
final class UnmeteredForegroundGate: WalkStreamingGate, @unchecked Sendable {
    private let lock = NSLock()
    private let monitor = NWPathMonitor()
    private var isUnmetered = false
    private var isInForeground = true
    private var observers: [NSObjectProtocol] = []

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let unmetered = Self.allowsStreaming(
                isSatisfied: path.status == .satisfied,
                isExpensive: path.isExpensive,
                isConstrained: path.isConstrained
            )
            guard let self else { return }
            lock.withLock { self.isUnmetered = unmetered }
        }
        monitor.start(queue: DispatchQueue(label: "com.standardphysics.walk-network", qos: .utility))
        observeForeground(UIApplication.didEnterBackgroundNotification, isInForeground: false)
        observeForeground(UIApplication.willEnterForegroundNotification, isInForeground: true)
    }

    deinit {
        monitor.cancel()
        observers.forEach(NotificationCenter.default.removeObserver)
    }

    var allowsStreaming: Bool {
        lock.withLock { isUnmetered && isInForeground }
    }

    static func allowsStreaming(isSatisfied: Bool, isExpensive: Bool, isConstrained: Bool) -> Bool {
        isSatisfied && !isExpensive && !isConstrained
    }

    private func observeForeground(_ name: Notification.Name, isInForeground foreground: Bool) {
        let observer = NotificationCenter.default.addObserver(forName: name, object: nil, queue: nil) { [weak self] _ in
            guard let self else { return }
            lock.withLock { self.isInForeground = foreground }
        }
        observers.append(observer)
    }
}
