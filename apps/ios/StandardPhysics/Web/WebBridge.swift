import UIKit
import WebKit

/// Which page of standardphysics.app the app's web view opens.
enum WebDestination: Equatable {
    /// The phone-first owner view of one shop.
    case owner(UUID)
    /// The builders' workspace, for the team.
    case workspace(UUID)
    /// A shop someone else walked, to show what the results look like.
    case example

    func url(on base: URL) -> URL {
        switch self {
        case .owner(let id): base.appendingPathComponent("shops").appendingPathComponent(id.uuidString.lowercased())
        case .workspace(let id): base.appendingPathComponent("scans").appendingPathComponent(id.uuidString)
        case .example: base.appendingPathComponent("example")
        }
    }

    var scanID: UUID? {
        switch self {
        case .owner(let id), .workspace(let id): id
        case .example: nil
        }
    }

    var title: String {
        switch self {
        case .owner, .workspace: "Your shop"
        case .example: "Example shop"
        }
    }
}

/// One message the owner view posts to `window.webkit.messageHandlers.standardPhysics`.
///
/// Every message is a JSON object with a `type`, and carries ids, never
/// tokens. Anything else is dropped without a word: the page is ours, so a
/// malformed message is a bug to find in the web, not a prompt to show the
/// owner.
enum WebBridgeMessage: Equatable {
    case takePhoto(requestID: String)
    case addRoom(scanID: UUID?)
    case saveReport
    case share(url: URL, title: String)
    case openLink(URL)
    case stageChanged(scanID: UUID?, stage: String)
    case shopDeleted(scanID: UUID)

    static let handlerName = "standardPhysics"

    init?(body: Any) {
        guard let fields = body as? [String: Any], let type = fields["type"] as? String else { return nil }
        guard let message = Self.parsers[type]?(fields) else { return nil }
        self = message
    }

    private static let parsers: [String: ([String: Any]) -> WebBridgeMessage?] = [
        "takePhoto": { fields in
            guard let id = fields["requestId"] as? String, Self.isRequestID(id) else { return nil }
            return .takePhoto(requestID: id)
        },
        "addRoom": { fields in .addRoom(scanID: (fields["scanId"] as? String).flatMap(UUID.init(uuidString:))) },
        "saveReport": { _ in .saveReport },
        "share": { fields in
            guard let url = webURL(fields["url"]) else { return nil }
            return .share(url: url, title: (fields["title"] as? String) ?? "Standard Physics report")
        },
        "openLink": { fields in webURL(fields["url"]).map(WebBridgeMessage.openLink) },
        "stageChanged": { fields in
            guard let stage = fields["stage"] as? String else { return nil }
            return .stageChanged(scanID: (fields["scanId"] as? String).flatMap(UUID.init(uuidString:)), stage: stage)
        },
        "shopDeleted": { fields in
            (fields["scanId"] as? String).flatMap(UUID.init(uuidString:)).map { .shopDeleted(scanID: $0) }
        },
    ]

    /// Request ids are words like "door_hardware" or "finding-<uuid>", and go
    /// into a path, so nothing else is accepted.
    static func isRequestID(_ id: String) -> Bool {
        !id.isEmpty && id.count <= 80 && id.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }
    }

    private static func webURL(_ value: Any?) -> URL? {
        guard let text = value as? String, let url = URL(string: text),
              let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "http",
              url.host?.isEmpty == false else { return nil }
        return url
    }
}

/// What the app does for each bridge message.
@MainActor
final class WebBridge {
    private weak var app: AppModel?
    private let scanID: UUID?
    private let reloadSignedIn: () -> Void
    private let appleSignIn = AppleSignInPrompt()
    private let pdfRenderer = LinkPDFRenderer()
    private var photoPicker: PhotoPickerDelegate?

    init(app: AppModel, scanID: UUID?, reloadSignedIn: @escaping () -> Void) {
        self.app = app
        self.scanID = scanID
        self.reloadSignedIn = reloadSignedIn
    }

    func handle(_ message: WebBridgeMessage, in webView: WKWebView) {
        switch message {
        case .takePhoto(let requestID): takePhoto(for: requestID, in: webView)
        case .addRoom(let shop): startWalk(joining: shop ?? scanID)
        case .saveReport: saveReport(in: webView)
        case .share(let url, let title): share(url, title: title, from: webView)
        case .openLink(let url): UIApplication.shared.open(url)
        case .stageChanged: Task { await app?.refreshJourneys() }
        case .shopDeleted(let scanID): Task { await app?.shopDeletedOnTheWeb(scanID) }
        }
    }

    /// The old home page's "Scan your shop": a walk of a new shop.
    func scanRequested() {
        startWalk(joining: nil)
    }

    /// Another room, or the shop walked again to clear "Needs another look".
    /// The walk's scan replaces the shop's once it's measured, and the
    /// owner's answers and photos carry over, so the quick questions are
    /// skipped when they're already answered.
    private func startWalk(joining shop: UUID?) {
        guard let app, app.canScan else { return }
        app.startWalk(joining: shop)
    }

    private func takePhoto(for requestID: String, in webView: WKWebView) {
        guard let scanID, let presenter = webView.window?.rootViewController?.topmostPresented else { return }
        let delegate = PhotoPickerDelegate { [weak self, weak webView] image in
            presenter.dismiss(animated: true)
            self?.photoPicker = nil
            guard let image, let webView else { return }
            self?.send(image, for: requestID, scanID: scanID, in: webView)
        }
        photoPicker = delegate
        presenter.present(PhotoCapture.picker(delegate: delegate), animated: true)
    }

    private func send(_ image: UIImage, for requestID: String, scanID: UUID, in webView: WKWebView) {
        guard let jpeg = PhotoEncoding.jpeg(from: image), let api = app?.api() else { return }
        Task {
            do {
                try await api.sendPhoto(scanID: scanID, requestID: requestID, jpeg: jpeg)
                Haptics.sent()
                let literal = String(decoding: try JSONEncoder().encode(requestID), as: UTF8.self)
                _ = try? await webView.evaluateJavaScript("window.standardPhysics?.photoSent(\(literal))")
            } catch {
                present(problem: error.localizedDescription, over: webView)
            }
        }
    }

    private func saveReport(in webView: WKWebView) {
        guard let session = app?.session else { return }
        Task {
            guard let credential = try? await appleSignIn.run(over: webView.window) else { return }
            do {
                try await session.signInWithApple(identityToken: credential.identityToken, fullName: credential.fullName)
                reloadSignedIn()
                await app?.refreshJourneys()
            } catch {
                present(problem: error.localizedDescription, over: webView)
            }
        }
    }

    /// The report link, with a PDF of the report beside it for anyone who
    /// wants a file rather than a link. The PDF is made from the link itself,
    /// a printable page that needs no sign-in, not from the owner view on
    /// screen with its 3D model. When it can't be made, the link goes alone.
    private func share(_ url: URL, title: String, from webView: WKWebView) {
        Task {
            var items: [Any] = [url]
            if isOurs(url), let pdf = await pdfRenderer.pdf(of: url), let file = try? ReportPDF.write(pdf, named: title) {
                items.append(file)
            }
            guard let presenter = webView.window?.rootViewController?.topmostPresented else { return }
            let activity = UIActivityViewController(activityItems: items, applicationActivities: nil)
            activity.popoverPresentationController?.sourceView = webView
            activity.popoverPresentationController?.sourceRect = webView.bounds
            activity.completionWithItemsHandler = { _, _, _, _ in
                items.compactMap { $0 as? URL }.filter(\.isFileURL).forEach(ReportPDF.remove)
            }
            presenter.present(activity, animated: true)
        }
    }

    private func isOurs(_ url: URL) -> Bool {
        AppEnvironment.workspaceBaseURL.flatMap(WebOrigin.init(url:))?.contains(url) ?? false
    }

    private func present(problem: String, over webView: WKWebView) {
        guard let presenter = webView.window?.rootViewController?.topmostPresented else { return }
        let alert = UIAlertController(title: nil, message: problem, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        presenter.present(alert, animated: true)
    }
}

final class PhotoPickerDelegate: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
    private let finish: (UIImage?) -> Void

    init(finish: @escaping (UIImage?) -> Void) { self.finish = finish }

    func imagePickerController(_ picker: UIImagePickerController,
                               didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
        finish(info[.originalImage] as? UIImage)
    }

    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
        finish(nil)
    }
}

/// Loads a page in a web view nobody sees and prints it to a PDF.
@MainActor
final class LinkPDFRenderer: NSObject, WKNavigationDelegate {
    /// US Letter, so the file prints on the paper a landlord or inspector has.
    private static let pageSize = CGSize(width: 612, height: 792)
    private static let settleTime: Duration = .milliseconds(800)
    private static let timeout: Duration = .seconds(20)

    private var webView: WKWebView?
    private var continuation: CheckedContinuation<Data?, Never>?

    func pdf(of url: URL) async -> Data? {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: CGRect(origin: .zero, size: Self.pageSize), configuration: configuration)
        view.navigationDelegate = self
        webView = view
        return await withCheckedContinuation { continuation in
            self.continuation = continuation
            view.load(URLRequest(url: url))
            Task {
                try? await Task.sleep(for: Self.timeout)
                self.finish(nil)
            }
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Task {
            try? await Task.sleep(for: Self.settleTime)
            finish(try? await webView.pdf())
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        finish(nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        finish(nil)
    }

    private func finish(_ data: Data?) {
        continuation?.resume(returning: data)
        continuation = nil
        webView?.navigationDelegate = nil
        webView = nil
    }
}

/// A PDF of the report page, in its own temporary folder so the share sheet
/// shows a readable file name.
enum ReportPDF {
    static func write(_ data: Data, named title: String) throws -> URL {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("StandardPhysicsReports", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent(fileName(for: title))
        try data.write(to: file, options: .atomic)
        return file
    }

    static func fileName(for title: String) -> String {
        let allowed = title.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) || $0 == " " ? Character($0) : " " }
        let cleaned = String(allowed).split(separator: " ").joined(separator: " ")
        return (cleaned.isEmpty ? "Standard Physics report" : String(cleaned.prefix(80))) + ".pdf"
    }

    static func remove(_ file: URL) {
        try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
    }
}

extension UIViewController {
    /// The controller on top, which is the only one that can present.
    var topmostPresented: UIViewController {
        presentedViewController?.topmostPresented ?? self
    }
}
