import SwiftUI

/// Screens 5 to 8: one request per screen while the walk uploads, then the
/// measuring wait.
struct ShopSetupScreen: View {
    @ObservedObject var app: AppModel
    @ObservedObject var setup: ShopSetupModel

    var body: some View {
        Group {
            switch setup.step {
            case .preparing:
                PreparingView()
            case .question(let request):
                QuickAnswerView(setup: setup, request: request)
                    .id(request.id)
            case .photo(let request):
                QuickPhotoView(setup: setup, request: request)
                    .id(request.id)
            case .pushForce(let request):
                DoorPushView(setup: setup, request: request)
                    .id(request.id)
            case .measuring:
                MeasuringView(app: app, setup: setup)
            }
        }
        .transition(.opacity)
        .animation(AppTheme.Motion.quick, value: setup.step)
        .task { await setup.begin() }
    }
}

private struct PreparingView: View {
    var body: some View {
        ZStack {
            DraftingPaper()
            ProgressView()
                .controlSize(.large)
                .tint(AppTheme.accent)
        }
    }
}

/// Screen 5: a yes or no the scan can't see, like whether customers use a
/// restroom.
private struct QuickAnswerView: View {
    @ObservedObject var setup: ShopSetupModel
    let request: OwnerRequest

    var body: some View {
        FlowPage {
            StepProgress(done: setup.progress.done, total: setup.progress.total)
            SketchSheet(height: 200) { QuestionSketch(requestID: request.id) }
            FlowTitle(request.title)
            FlowDetail(request.detail)
            if let problem = setup.problem { FlowProblem(message: problem) }
        } actions: {
            HStack(spacing: AppTheme.Spacing.small) {
                Button("Yes") { setup.answer(true) }
                    .buttonStyle(AppButtonStyle())
                Button("No") { setup.answer(false) }
                    .buttonStyle(AppButtonStyle())
            }
            .disabled(setup.isSending)
        }
    }
}

/// Screen 6: one photo, with a drawing of the shot to take and the line the
/// server writes about what it will be checked for.
private struct QuickPhotoView: View {
    @ObservedObject var setup: ShopSetupModel
    let request: OwnerRequest
    @State private var takingPhoto = false

    var body: some View {
        FlowPage {
            StepProgress(done: setup.progress.done, total: setup.progress.total)
            SketchSheet(height: 230) { SamplePhoto(requestID: request.id) }
            FlowTitle(request.title)
            FlowDetail(request.detail)
            if let problem = setup.problem { FlowProblem(message: problem) }
        } actions: {
            Button(setup.isSending ? "Sending your photo" : "Take photo") { takingPhoto = true }
                .buttonStyle(AppButtonStyle())
            Button("Skip for now") { setup.skip() }
                .buttonStyle(AppButtonStyle(.link))
        }
        .disabled(setup.isSending)
        .fullScreenCover(isPresented: $takingPhoto) {
            PhotoCapture { image in
                takingPhoto = false
                guard let image, let jpeg = PhotoEncoding.jpeg(from: image) else { return }
                setup.sendPhoto(jpeg)
            }
            .ignoresSafeArea()
        }
    }
}

/// Screen 7: how hard the inside doors are to push, in pounds, asked only
/// when customers go through one.
private struct DoorPushView: View {
    @ObservedObject var setup: ShopSetupModel
    let request: OwnerRequest
    @State private var pounds = ""
    @FocusState private var fieldFocused: Bool

    private var reading: Double? {
        Double(pounds.replacingOccurrences(of: ",", with: ".")).flatMap { $0 > 0 && $0 < 10_000 ? $0 : nil }
    }

    var body: some View {
        FlowPage {
            StepProgress(done: setup.progress.done, total: setup.progress.total)
            SketchSheet(height: 220) { PushGaugeSketch() }
            FlowTitle(request.title)
            FlowDetail(request.detail)
            HStack(alignment: .firstTextBaseline, spacing: AppTheme.Spacing.small) {
                TextField("", text: $pounds)
                    .keyboardType(.decimalPad)
                    .font(AppTheme.Typography.title)
                    .monospacedDigit()
                    .focused($fieldFocused)
                    .accessibilityLabel("Push force in pounds")
                Text("lb")
                    .font(AppTheme.Typography.title)
                    .foregroundStyle(AppTheme.mutedInk)
                    .accessibilityHidden(true)
            }
            .fieldSurface(focused: fieldFocused)
            .onAppear { fieldFocused = true }
            if let problem = setup.problem { FlowProblem(message: problem) }
        } actions: {
            Button(setup.isSending ? "Saving" : "Save") {
                if let reading { setup.savePushForce(reading) }
            }
            .buttonStyle(AppButtonStyle())
            .disabled(reading == nil)
            Button("Skip for now") { setup.skip() }
                .buttonStyle(AppButtonStyle(.link))
        }
        .disabled(setup.isSending)
    }
}
