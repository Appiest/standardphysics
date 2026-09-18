import SwiftUI
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
                   origin.allowsLocalDemo,
                   message == nil {
                    WorkspaceWebView(
                        url: workspaceURL.appendingPathComponent("scans").appendingPathComponent(scanID.uuidString),
                        allowedOrigin: origin,
                        onScanRequested: { appModel.beginCapture() },
                        onFailure: { message = $0 }
                    )
                } else {
                    VStack(spacing: AppTheme.Spacing.card) {
                        Text(message ?? "Set the workspace address on the Connection screen, then sign in.")
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
        context.coordinator.load(url, in: webView)
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

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        private let allowedOrigin: WebOrigin
        private let onScanRequested: () -> Void
        private let onFailure: (String) -> Void
        private var requestedURL: URL?

        init(allowedOrigin: WebOrigin,
             onScanRequested: @escaping () -> Void, onFailure: @escaping (String) -> Void) {
            self.allowedOrigin = allowedOrigin
            self.onScanRequested = onScanRequested
            self.onFailure = onFailure
        }

        func load(_ url: URL, in webView: WKWebView) {
            guard requestedURL != url, allowedOrigin.allowsLocalDemo, allowedOrigin.contains(url) else { return }
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
            onFailure("Couldn’t open your shop. Keep your Mac running and connect to the same Wi-Fi.")
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse)
            async -> WKNavigationResponsePolicy {
            guard let response = navigationResponse.response as? HTTPURLResponse else { return .allow }
            if navigationResponse.isForMainFrame && response.statusCode >= 400 {
                onFailure("The workspace returned an error (\(response.statusCode)). Try again after it is running on your Mac.")
                return .cancel
            }
            return .allow
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction
        ) async -> WKNavigationActionPolicy {
            guard let target = navigationAction.request.url else { return .cancel }
            guard allowedOrigin.allowsLocalDemo else { return .cancel }
            if target.absoluteString == "about:blank" || allowedOrigin.contains(target) {
                return .allow
            } else {
                return .cancel
            }
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "nativeCapture",
                  allowedOrigin.allowsLocalDemo,
                  message.frameInfo.isMainFrame,
                  let sourceURL = message.frameInfo.request.url,
                  allowedOrigin.contains(sourceURL),
                  let action = message.body as? String,
                  action == "scanShop" else { return }
            DispatchQueue.main.async { self.onScanRequested() }
        }
    }
}

struct WebOrigin: Equatable, Sendable {
    let scheme: String
    let host: String
    let port: Int?

    var allowsLocalDemo: Bool { ServiceAddress.isLocalHost(host) }

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
