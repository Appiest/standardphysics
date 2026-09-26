import RoomPlan
import SwiftUI

struct AppRootView: View {
    @StateObject private var model = AppModel(canScan: DeviceSupport.canCaptureRooms)

    var body: some View {
        AppScreens(model: model)
            .tint(AppTheme.accent)
            .preferredColorScheme(.light)
    }
}

enum DeviceSupport {
    static var canCaptureRooms: Bool {
#if targetEnvironment(simulator)
        if ProcessInfo.processInfo.environment["SIMULATOR_CAPTURE_DEMO"] == "1" { return true }
#endif
        return RoomCaptureSession.isSupported
    }
}

private struct AppScreens: View {
    @ObservedObject var model: AppModel

    var body: some View {
        switch model.screen {
        case .welcome:
            WelcomeScreen(model: model)
        case .home:
            HomeScreen(model: model)
        case .unsupported:
            UnsupportedDeviceScreen(model: model)
        case .beforeYouWalk:
            BeforeYouWalkScreen(model: model)
        case .cameraAccess:
            CameraAccessScreen(model: model)
        case .capture:
            CaptureScreen(model: model)
                .id(model.captureSessionID)
        case .review(let scan):
            ReviewScreen(model: model, scan: scan)
        case .upload(let uploadModel):
            UploadStatusScreen(appModel: model, uploadModel: uploadModel)
        case .setup(let setup):
            ShopSetupScreen(app: model, setup: setup)
                .id(setup.id)
        case .web(let destination):
            WorkspaceScreen(appModel: model, destination: destination)
        case .howItWorks:
            HowItWorksScreen(model: model)
        case .connection:
            ConnectionScreen(model: model)
        case .signIn:
            SignInScreen(model: model, session: model.session)
#if DEBUG
        case .walkPreview(let store):
            CaptureScreen(model: model, preview: store)
#endif
        }
    }
}
