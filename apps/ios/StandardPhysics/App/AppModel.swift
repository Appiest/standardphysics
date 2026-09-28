import AVFoundation
import Combine
import SwiftUI

/// Everything the app is doing, and which screen shows it.
///
/// The first run is one path: welcome, the checklist, the camera, the walk,
/// then the quick answers and photos while the walk uploads under a guest
/// account, then the measuring wait. After that the home screen shows one
/// next step per shop, read from the server's journey for it.
@MainActor
final class AppModel: ObservableObject {
    enum Screen {
        case welcome
        case home
        case unsupported
        case beforeYouWalk
        case cameraAccess
        case capture
        case review(CapturedScan)
        case upload(UploadViewModel)
        case setup(ShopSetupModel)
        case web(WebDestination)
        case howItWorks
        case connection
        case signIn
#if DEBUG
        case walkPreview(CaptureSessionStore)
#endif
    }

    let session = SessionStore()
    let canScan: Bool
    @Published var screen: Screen
    @Published private(set) var savedScans = CaptureLibrary.all()
    @Published private(set) var journeys: [Journey] = []
    @Published private(set) var journeysLoaded = false
    @Published var deletionMessage: String?
    @Published var accountDeletionMessage: String?
    @Published var developerMode = DeveloperMode.isOn {
        didSet { DeveloperMode.isOn = developerMode }
    }
    @Published private(set) var captureSessionID = UUID()
    @Published private(set) var recoveryDirectories: [URL] = []
    @Published private(set) var recoveryMessage: String?
    @Published private(set) var walkProblem: String?
    private var uploads: [UUID: UploadViewModel] = [:]
    /// The upload that sent the owner to sign in, so signing in finishes it.
    private var pendingUpload: (scan: CapturedScan, name: String)?
    /// The shop the next walk joins, when the web asked for another room or
    /// another walk of the same shop.
    private var walkJoins: UUID?
    private var subscriptions: Set<AnyCancellable> = []

    init(canScan: Bool) {
        self.canScan = canScan
        screen = canScan ? .welcome : .unsupported
        if FirstRun.hasStartedAShop || !savedScans.isEmpty { screen = .home }
        enforceDeveloperModeRole()
        session.objectWillChange
            .sink { [weak self] in self?.objectWillChange.send() }
            .store(in: &subscriptions)
        session.$credentialChanges
            .dropFirst()
            .sink { [weak self] _ in Task { @MainActor in self?.handOverUploads() } }
            .store(in: &subscriptions)
        NotificationCenter.default.publisher(for: PushRegistration.deviceTokenArrived)
            .compactMap { $0.object as? String }
            .sink { [weak self] token in Task { await self?.registerDevice(token) } }
            .store(in: &subscriptions)
        NotificationCenter.default.publisher(for: PushRegistration.shopOpened)
            .compactMap { $0.object as? UUID }
            .sink { [weak self] scanID in self?.openShop(scanID) }
            .store(in: &subscriptions)
#if DEBUG
        if DebugLaunch.apply(to: self) { return }
#endif
        Task { await launch() }
    }

#if DEBUG
    /// Fills in what the server would have said, for a screen opened by
    /// `SP_DEBUG_SCREEN` in a Debug build.
    func showForDebugging(journeys: [Journey]) {
        self.journeys = journeys
        journeysLoaded = true
        isDebugPreview = true
    }

    private var isDebugPreview = false
#endif

    // MARK: Launch and account

    /// Refreshes who is signed in, makes a guest on a first launch so the
    /// walk can upload without a sign-in, then loads the shops.
    private func launch() async {
        await refreshSession()
        if canScan, !session.isSignedIn { try? await session.startGuest() }
        await refreshJourneys()
        if isOnFirstScreen, !journeys.isEmpty { screen = .home }
        PushRegistration.registerIfAllowed()
    }

    func refreshSession() async {
        await session.refresh()
        enforceDeveloperModeRole()
    }

    /// Developer mode is for the team. Anyone else who had it switched on
    /// before roles existed gets it switched off.
    private func enforceDeveloperModeRole() {
        guard session.owner?.isTeam != true, developerMode else { return }
        developerMode = false
    }

    var defaultShopName: String {
        guard let owner = session.owner, !owner.guest, !owner.shopName.isEmpty else { return "My shop" }
        return owner.shopName
    }

    func api() -> OwnerAPI? {
        guard let baseURL = AppEnvironment.apiBaseURL, let token = session.token else { return nil }
        return OwnerAPI(baseURL: baseURL, token: token)
    }

    func refreshJourneys() async {
#if DEBUG
        if isDebugPreview { return }
#endif
        guard let api = api() else {
            journeys = []
            journeysLoaded = true
            return
        }
        if let loaded = try? await api.journeys() { journeys = loaded }
        journeysLoaded = true
    }

    private var isOnFirstScreen: Bool {
        switch screen {
        case .welcome, .unsupported: true
        default: false
        }
    }

    /// After a sign-in the account's shops decide where home is, so they are
    /// read before leaving the sign-in screen.
    func didSignIn() {
        enforceDeveloperModeRole()
        PushRegistration.registerIfAllowed()
        guard let pending = pendingUpload else {
            Task {
                await refreshJourneys()
                showStart()
            }
            return
        }
        pendingUpload = nil
        uploads.removeValue(forKey: pending.scan.id)?.cancel()
        upload(scan: pending.scan, name: pending.name)
    }

    /// Back from sign-in goes to wherever sign-in interrupted.
    func leaveSignIn() {
        guard let pending = pendingUpload else {
            showStart()
            return
        }
        pendingUpload = nil
        screen = .review(pending.scan)
    }

    /// The server ended the session partway through an upload. Signing in
    /// again picks the same upload back up.
    func signInToContinue(_ upload: UploadViewModel) {
        pendingUpload = (upload.scan, upload.name)
        screen = .signIn
    }

    func connectionChanged() {
        uploads.values.forEach { $0.cancel() }
        uploads.removeAll()
        // A token is only good at the server that issued it.
        session.serverChanged()
        showStart()
    }

    func signOut() {
        uploads.values.forEach { $0.cancel() }
        uploads.removeAll()
        session.signOut()
        journeys = []
        enforceDeveloperModeRole()
        screen = .signIn
    }

    /// Ends the account, then clears what this phone was holding for it.
    ///
    /// The server goes first. If it refuses, the scans are still on the phone
    /// and still on the server, and the owner can try again knowing nothing
    /// was half-done.
    func deleteAccount() async {
        do {
            try await session.deleteAccount()
        } catch {
            accountDeletionMessage = error.localizedDescription
            return
        }
        uploads.values.forEach { $0.cancel() }
        uploads.removeAll()
        CaptureLibrary.all().forEach { try? CaptureLibrary.remove($0) }
        savedScans = CaptureLibrary.all()
        journeys = []
        FirstRun.hasStartedAShop = false
        accountDeletionMessage = nil
        screen = canScan ? .welcome : .unsupported
    }

    /// Moves every running upload to the session the phone holds now.
    private func handOverUploads() {
        guard let baseURL = AppEnvironment.apiBaseURL, let token = session.token else { return }
        for upload in uploads.values {
            upload.resume(with: ScanUploadClient(baseURL: baseURL, token: token))
        }
    }

    func registerDevice(_ deviceToken: String) async {
        try? await api()?.registerDevice(deviceToken, environment: PushRegistration.environment)
    }

    // MARK: Navigation

    /// The walk draws over the live camera, so the status bar goes light.
    var isOnCamera: Bool {
        switch screen {
        case .capture: true
#if DEBUG
        case .walkPreview: true
#endif
        default: false
        }
    }

    var hasShops: Bool { !journeys.isEmpty || !savedScans.isEmpty || FirstRun.hasStartedAShop }

    /// Home once there is a shop to show, and the start of the first run
    /// before that.
    func showStart() {
        walkJoins = nil
        savedScans = CaptureLibrary.all()
        if hasShops {
            screen = .home
        } else {
            screen = canScan ? .welcome : .unsupported
        }
        Task { await refreshJourneys() }
    }

    func startWalk(joining shop: UUID? = nil) {
        walkJoins = shop
        screen = .beforeYouWalk
    }

    /// The camera is asked for on the screen right before the walk, and only
    /// when the system has not been asked yet or said no.
    func finishChecklist() {
        if AVCaptureDevice.authorizationStatus(for: .video) == .authorized {
            beginCapture()
        } else {
            screen = .cameraAccess
        }
    }

    func beginCapture() {
        captureSessionID = UUID()
        walkProblem = nil
        screen = .capture
    }

    func openExample() {
        screen = .web(.example)
    }

    /// The owner's view of a shop, or the builders' workspace for the team.
    func openShop(_ scanID: UUID) {
        screen = .web(session.owner?.isTeam == true && developerMode ? .workspace(scanID) : .owner(scanID))
    }

    /// Where the next-step card goes: back into the phone's questions and
    /// photos, the measuring wait, or the shop on the web.
    func open(_ journey: Journey) {
        switch journey.nextStep.kind {
        case "answers", "photos", "measuring":
            screen = .setup(ShopSetupModel(scanID: journey.scanID, app: self))
        case "upload", "failed":
            openUpload(for: journey.scanID)
        default:
            openShop(journey.scanID)
        }
    }

    private func openUpload(for scanID: UUID) {
        if let local = savedScans.first(where: { ResumableUploadStore(captureDirectory: $0.directory).scanID == scanID }) {
            upload(scan: local, name: local.name ?? defaultShopName)
        } else if canScan {
            startWalk()
        } else {
            openShop(scanID)
        }
    }

    // MARK: The walk and its upload

    /// The walk is saved on the phone. Its upload starts straight away under
    /// whoever is signed in, a guest on a first run, and the owner moves on to
    /// the questions the walk can't answer.
    func walkFinished(_ scan: CapturedScan) {
        Task { await startSetup(for: scan) }
    }

    private func startSetup(for scan: CapturedScan) async {
        let joins = walkJoins
        walkJoins = nil
        let name = scan.name ?? walkName(joining: joins)
        let named: CapturedScan
        do {
            named = try scan.renamed(name, replacing: joins)
        } catch {
            walkProblem = "Free some space on this phone, then save again."
            screen = .review(scan)
            return
        }
        if !session.isSignedIn { try? await session.startGuest() }
        guard let model = makeUpload(for: named, name: named.name ?? defaultShopName) else { return }
        FirstRun.hasStartedAShop = true
        savedScans = CaptureLibrary.all()
        screen = .setup(ShopSetupModel(upload: model, app: self))
    }

    /// The walk's scan is made on the server as the walk starts, so its name
    /// is settled then: the shop it joins, or the account's shop name.
    func walkUploadPlan() -> WalkUploadPlan? {
        guard let baseURL = AppEnvironment.apiBaseURL, let token = session.token else { return nil }
        return WalkUploadPlan(
            client: ScanUploadClient(baseURL: baseURL, token: token),
            name: walkName(joining: walkJoins),
            replaces: walkJoins
        )
    }

    private func walkName(joining shop: UUID?) -> String {
        shop.flatMap(shopName(of:)) ?? defaultShopName
    }

    private func shopName(of scanID: UUID) -> String? {
        journeys.first { $0.scanID == scanID }?.shopName
    }

    func upload(scan: CapturedScan, name: String) {
        guard let model = makeUpload(for: scan, name: name) else { return }
        screen = .upload(model)
    }

    /// The upload for a scan, reusing one already running for it.
    ///
    /// With no address the phone asks for one, and with no session it asks
    /// the owner to sign in and remembers the scan so signing in finishes it.
    private func makeUpload(for scan: CapturedScan, name: String) -> UploadViewModel? {
        guard let baseURL = AppEnvironment.apiBaseURL else {
            screen = .connection
            return nil
        }
        guard let token = session.token else {
            pendingUpload = (scan, name)
            screen = .signIn
            return nil
        }
        if let existing = uploads[scan.id], existing.totalCount == scan.artifacts.count {
            existing.start()
            return existing
        }
        uploads[scan.id]?.cancel()
        let model = UploadViewModel(
            scan: scan,
            name: name,
            client: ScanUploadClient(baseURL: baseURL, token: token)
        )
        uploads[scan.id] = model
        model.start()
        return model
    }

    /// Removes a scan from this phone, and from the server if it got there.
    ///
    /// The phone is cleared first and the server is not reported on. The owner
    /// asked for the scan to be gone, and where the bytes were is our problem:
    /// a line about a server still holding a copy answers a question nobody
    /// asked and leaves them worrying about it.
    func deleteScan(_ scan: CapturedScan) async {
        let remoteID = ResumableUploadStore(captureDirectory: scan.directory).scanID
        uploads[scan.id] = nil
        do { try CaptureLibrary.remove(scan) } catch {
            deletionMessage = "That scan could not be removed. Try again."
            return
        }
        savedScans = CaptureLibrary.all()

        guard let remoteID, let baseURL = AppEnvironment.apiBaseURL else { return }
        try? await ScanUploadClient(baseURL: baseURL, token: session.token).delete(id: remoteID)
        await refreshJourneys()
    }

    /// Deletes a shop from the account, and this phone's copy of its walk
    /// with it. A shop still being measured is taken off the list at once and
    /// removed on the server when its measuring stops.
    func deleteShop(_ journey: Journey) async {
        guard let baseURL = AppEnvironment.apiBaseURL else { return }
        do {
            try await ScanUploadClient(baseURL: baseURL, token: session.token).delete(id: journey.scanID)
        } catch {
            deletionMessage = "That shop could not be deleted. Check your connection and try again."
            return
        }
        deletionMessage = nil
        await forgetShop(journey.scanID)
    }

    /// The owner deleted a shop on its web page: drop this phone's copy of it
    /// and go back home.
    func shopDeletedOnTheWeb(_ scanID: UUID) async {
        await forgetShop(scanID)
        screen = .home
    }

    private func forgetShop(_ scanID: UUID) async {
        for scan in savedScans where ResumableUploadStore(captureDirectory: scan.directory).scanID == scanID {
            uploads[scan.id] = nil
            try? CaptureLibrary.remove(scan)
        }
        savedScans = CaptureLibrary.all()
        journeys.removeAll { $0.scanID == scanID }
        await refreshJourneys()
    }

    func recoverSavedRoom(_ directory: URL) async {
        recoveryMessage = "Saving your room"
        let result = await Task.detached(priority: .userInitiated) {
            Result { try CaptureRecovery.recover(from: directory) }
        }.value
        switch result {
        case .success(let scan):
            recoveryMessage = nil
            recoveryDirectories.removeAll { $0 == directory }
            screen = .review(scan)
        case .failure: recoveryMessage = "Free some space on this phone, then save again."
        }
    }

    /// Keeps every half-finished upload on this phone moving while home is on
    /// screen, the way it did before the first-run flow existed.
    func refreshSavedScanStates() async {
        if let root = try? FileManager.default.url(for: .applicationSupportDirectory,
            in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("Captures") {
            recoveryDirectories = CaptureRecovery.directories(in: root)
        }
        while !Task.isCancelled {
            resumeUploads()
            savedScans = CaptureLibrary.all()
            do { try await Task.sleep(for: .seconds(2)) } catch { return }
        }
    }

    private func resumeUploads() {
        guard let baseURL = AppEnvironment.apiBaseURL, let token = session.token else { return }
        for scan in savedScans where uploads[scan.id] == nil {
            guard ResumableUploadStore(captureDirectory: scan.directory).scanID != nil else { continue }
            let model = UploadViewModel(scan: scan, name: scan.name ?? defaultShopName,
                client: ScanUploadClient(baseURL: baseURL, token: token))
            uploads[scan.id] = model
            model.start()
        }
    }
}

/// What the phone remembers about the first run.
enum FirstRun {
    private static let key = "SP_STARTED_A_SHOP"

    /// Set once a walk has started uploading, so the next launch opens on
    /// home even before the server has answered with the shop.
    static var hasStartedAShop: Bool {
        get { UserDefaults.standard.bool(forKey: key) }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }
}
