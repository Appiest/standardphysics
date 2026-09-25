import SwiftUI
import UIKit
import WebKit

struct WorkspaceScreen: View {
    @ObservedObject var appModel: AppModel
    let scanID: UUID
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Group {
                if let workspaceURL = AppEnvironment.workspaceBaseURL,
                   let origin = WebOrigin(url: workspaceURL),
                   origin.allowsWorkspace,
                   message == nil {
                    WorkspaceWebView(
                        url: workspaceURL.appendingPathComponent("scans").appendingPathComponent(scanID.uuidString),
                        allowedOrigin: origin,
                        sessionToken: appModel.session.token,
                        onScanRequested: { appModel.showScanPrimer() },
                        onFailure: { message = $0 }
                    )
                } else {
                    VStack(spacing: AppTheme.Spacing.card) {
                        Text(message ?? "This build has no workspace address set.")
                            .font(AppTheme.Typography.lead)
                        if message != nil {
                            Button("Try again") { message = nil }
                                .buttonStyle(AppButtonStyle())
                        }
                        Button("Connection") { appModel.screen = .connection }
                            .buttonStyle(AppButtonStyle())
                    }.padding(AppTheme.Spacing.page)
                }
            }
            .ignoresSafeArea(edges: .bottom)
            .navigationTitle("Your shop")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Shops") { appModel.showStart() }
                }
            }
        }
    }
}

struct WorkspaceWebView: UIViewRepresentable {
    let url: URL
    let allowedOrigin: WebOrigin
    let sessionToken: String?
    let onScanRequested: () -> Void
    let onFailure: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(allowedOrigin: allowedOrigin,
            onScanRequested: onScanRequested, onFailure: onFailure)
    }

    func makeUIView(context: Context) -> WKWebView {
        let contentController = WKUserContentController()
        contentController.add(context.coordinator, name: "nativeCapture")
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.userContentController = contentController
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        context.coordinator.attach(webView)
        context.coordinator.signIn(with: sessionToken, then: url, in: webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.load(url, in: webView)
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "nativeCapture")
        webView.navigationDelegate = nil
        webView.stopLoading()
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler, WKDownloadDelegate {
        private let allowedOrigin: WebOrigin
        private let onScanRequested: () -> Void
        private let onFailure: (String) -> Void
        private var requestedURL: URL?
        private weak var webView: WKWebView?
        private var downloadDestinations: [ObjectIdentifier: URL] = [:]

        init(allowedOrigin: WebOrigin,
             onScanRequested: @escaping () -> Void, onFailure: @escaping (String) -> Void) {
            self.allowedOrigin = allowedOrigin
            self.onScanRequested = onScanRequested
            self.onFailure = onFailure
        }

        func attach(_ webView: WKWebView) {
            self.webView = webView
        }

        /// Hands the phone's session to the web view before the first load.
        ///
        /// The workspace authenticates with the `sp_session` cookie and the
        /// phone holds the very same token, lifted out of the cookie the API
        /// set when it signed in. Without this the owner reaches their own shop
        /// and is asked to sign in a second time, inside their own app, to see
        /// the room they just walked.
        func signIn(with token: String?, then url: URL, in webView: WKWebView) {
            guard let token, let cookie = Self.sessionCookie(token: token, for: url) else {
                load(url, in: webView)
                return
            }
            webView.configuration.websiteDataStore.httpCookieStore.setCookie(cookie) { [weak self] in
                self?.load(url, in: webView)
            }
        }

        private static func sessionCookie(token: String, for url: URL) -> HTTPCookie? {
            guard let host = url.host else { return nil }
            var properties: [HTTPCookiePropertyKey: Any] = [
                .name: "sp_session",
                .value: token,
                .domain: host,
                .path: "/",
            ]
            if url.scheme?.lowercased() == "https" {
                properties[.secure] = "TRUE"
            }
            return HTTPCookie(properties: properties)
        }

        func load(_ url: URL, in webView: WKWebView) {
            guard requestedURL != url, allowedOrigin.allowsWorkspace, allowedOrigin.contains(url) else { return }
            requestedURL = url
            webView.load(URLRequest(url: url))
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            report(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            report(error)
        }

        private func report(_ error: Error) {
            guard (error as NSError).code != NSURLErrorCancelled else { return }
            onFailure("Couldn’t open your shop. Check your connection and try again.")
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse)
            async -> WKNavigationResponsePolicy {
            guard let responseURL = navigationResponse.response.url, allowedOrigin.contains(responseURL) else {
                reportDownloadFailure("The workspace sent a file outside its configured address.")
                return .cancel
            }
            guard let response = navigationResponse.response as? HTTPURLResponse else { return .allow }
            if navigationResponse.isForMainFrame && response.statusCode >= 400 {
                onFailure("Your shop could not be loaded (error \(response.statusCode)). Try again in a moment.")
                return .cancel
            }
            if !navigationResponse.canShowMIMEType {
                return .download
            }
            return .allow
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction
        ) async -> WKNavigationActionPolicy {
            guard let target = navigationAction.request.url else { return .cancel }
            guard allowedOrigin.allowsWorkspace else { return .cancel }
            if target.absoluteString == "about:blank" || allowedOrigin.contains(target) {
                if navigationAction.shouldPerformDownload {
                    return .download
                }
                return .allow
            } else {
                return .cancel
            }
        }

        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
            prepare(download)
        }

        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
            prepare(download)
        }

        private func prepare(_ download: WKDownload) {
            guard let requestURL = download.originalRequest?.url, allowedOrigin.contains(requestURL) else {
                reportDownloadFailure("The workspace sent a file outside its configured address.")
                return
            }
            download.delegate = self
        }

        func download(
            _ download: WKDownload,
            decideDestinationUsing response: URLResponse,
            suggestedFilename: String,
            completionHandler: @escaping (URL?) -> Void
        ) {
            guard let responseURL = response.url, allowedOrigin.contains(responseURL) else {
                reportDownloadFailure("The workspace sent a file outside its configured address.")
                completionHandler(nil)
                return
            }
            do {
                let destination = try WorkspaceDownloadDestination.fileURL(suggestedFilename: suggestedFilename)
                downloadDestinations[ObjectIdentifier(download)] = destination
                completionHandler(destination)
            } catch {
                reportDownloadFailure("Couldn’t save the floor plan on this phone. Try again.")
                completionHandler(nil)
            }
        }

        func downloadDidFinish(_ download: WKDownload) {
            guard let destination = downloadDestinations.removeValue(forKey: ObjectIdentifier(download)) else {
                reportDownloadFailure("Couldn’t save the floor plan on this phone. Try again.")
                return
            }
            guard FileManager.default.fileExists(atPath: destination.path) else {
                removeDownloadDirectory(containing: destination)
                reportDownloadFailure("Couldn’t save the floor plan on this phone. Try again.")
                return
            }
            DispatchQueue.main.async { [weak self] in self?.share(destination) }
        }

        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            removeDownloadedFile(for: download)
            guard (error as NSError).code != NSURLErrorCancelled else { return }
            reportDownloadFailure("Couldn’t download the floor plan. Check your connection and try again.")
        }

        private func share(_ fileURL: URL) {
            guard let presenter = visibleViewController(from: webView?.window?.rootViewController) else {
                try? FileManager.default.removeItem(at: fileURL)
                removeDownloadDirectory(containing: fileURL)
                reportDownloadFailure("The floor plan downloaded, but it could not be opened for sharing.")
                return
            }
            let activity = UIActivityViewController(activityItems: [fileURL], applicationActivities: nil)
            activity.popoverPresentationController?.sourceView = webView
            activity.popoverPresentationController?.sourceRect = webView?.bounds ?? .zero
            activity.completionWithItemsHandler = { [weak self] _, _, _, _ in
                try? FileManager.default.removeItem(at: fileURL)
                self?.removeDownloadDirectory(containing: fileURL)
            }
            presenter.present(activity, animated: true)
        }

        private func removeDownloadedFile(for download: WKDownload) {
            guard let fileURL = downloadDestinations.removeValue(forKey: ObjectIdentifier(download)) else { return }
            try? FileManager.default.removeItem(at: fileURL)
            removeDownloadDirectory(containing: fileURL)
        }

        private func removeDownloadDirectory(containing fileURL: URL) {
            WorkspaceDownloadDestination.removeDirectory(containing: fileURL)
        }

        private func visibleViewController(from controller: UIViewController?) -> UIViewController? {
            if let presented = controller?.presentedViewController { return visibleViewController(from: presented) }
            if let navigation = controller as? UINavigationController { return visibleViewController(from: navigation.visibleViewController) }
            if let tabs = controller as? UITabBarController { return visibleViewController(from: tabs.selectedViewController) }
            return controller
        }

        private func reportDownloadFailure(_ message: String) {
            DispatchQueue.main.async { self.onFailure(message) }
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "nativeCapture",
                  allowedOrigin.allowsWorkspace,
                  message.frameInfo.isMainFrame,
                  let sourceURL = message.frameInfo.request.url,
                  allowedOrigin.contains(sourceURL),
                  let action = message.body as? String,
                  action == "scanShop" else { return }
            DispatchQueue.main.async { self.onScanRequested() }
        }
    }
}

enum WorkspaceDownloadDestination {
    private static let directoryName = "StandardPhysicsDownloads"

    static func fileURL(suggestedFilename: String, fileManager: FileManager = .default) throws -> URL {
        let root = fileManager.temporaryDirectory.appendingPathComponent(directoryName, isDirectory: true)
        let directory = root.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent(safeFilename(suggestedFilename))
    }

    static func removeDirectory(containing fileURL: URL, fileManager: FileManager = .default) {
        let directory = fileURL.deletingLastPathComponent()
        guard directory.deletingLastPathComponent().lastPathComponent == directoryName else { return }
        try? fileManager.removeItem(at: directory)
    }

    static func safeFilename(_ suggestedFilename: String) -> String {
        let normalized = suggestedFilename.replacingOccurrences(of: "\\", with: "/")
        guard !normalized.isEmpty, normalized != ".", normalized != ".." else { return "architecture.zip" }
        let filename = URL(fileURLWithPath: normalized).lastPathComponent
        return filename.isEmpty || filename == "." || filename == ".." || filename == "/" ? "architecture.zip" : filename
    }
}

struct WebOrigin: Equatable, Sendable {
    let scheme: String
    let host: String
    let port: Int?

    /// Whether a workspace may be opened at this origin.
    ///
    /// The same rule `ServiceAddress.parse` applies to an address someone
    /// types: HTTPS is trusted anywhere, and plain HTTP only on the local
    /// network, where a developer's laptop lives. This used to require a local
    /// host outright, from when the viewer was a sign-in-free demo of a Mac on
    /// the same Wi-Fi, which meant a hosted workspace could never be opened at
    /// all: the screen fell through to the connection form instead.
    var allowsWorkspace: Bool { scheme == "https" || ServiceAddress.isLocalHost(host) }

    init?(url: URL) {
        guard let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = url.host?.lowercased() else { return nil }
        self.scheme = scheme
        self.host = host
        port = Self.effectivePort(for: url)
    }

    func contains(_ url: URL) -> Bool {
        url.scheme?.lowercased() == scheme
            && url.host?.lowercased() == host
            && Self.effectivePort(for: url) == port
            && url.user == nil && url.password == nil
    }

    private static func effectivePort(for url: URL) -> Int? {
        if let port = url.port { return port }
        switch url.scheme?.lowercased() {
        case "http": return 80
        case "https": return 443
        default: return nil
        }
    }
}
