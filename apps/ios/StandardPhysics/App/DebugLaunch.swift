#if DEBUG
import Foundation
import simd

/// Opens one screen with made-up data, so every screen and state can be
/// looked at in the simulator without a server, a LiDAR phone or a walk.
///
///     SIMCTL_CHILD_SP_DEBUG_SCREEN=photo:door_hardware xcrun simctl launch booted com.standardphysics.capture
///
/// Debug builds only. The request wording is the server's, copied from
/// services/api/standardphysics_api/owner_requests.py and
/// packages/agents/standardphysics_agents/copy.py.
@MainActor
enum DebugLaunch {
    static func apply(to model: AppModel) -> Bool {
        if let token = ProcessInfo.processInfo.environment["SP_DEBUG_TOKEN"] {
            model.session.adoptForDebugging(token: token)
        }
        guard let value = ProcessInfo.processInfo.environment["SP_DEBUG_SCREEN"] else { return false }
        let parts = value.split(separator: ":", maxSplits: 1).map(String.init)
        guard let screen = screen(parts[0], argument: parts.count > 1 ? parts[1] : nil, model: model) else { return false }
        model.screen = screen
        return true
    }

    private static func screen(_ name: String, argument: String?, model: AppModel) -> AppModel.Screen? {
        if let simple = simpleScreens[name] { return simple }
        switch name {
        case "home":
            model.showForDebugging(journeys: journeys(argument))
            return .home
        case "question", "photo", "push", "measuring":
            return .setup(setup(name, argument: argument, model: model))
        case "walk":
            return .walkPreview(walk(argument ?? "progress"))
        case "setup":
            return argument.flatMap(UUID.init(uuidString:)).map { .setup(ShopSetupModel(scanID: $0, app: model)) }
        case "shop":
            return argument.flatMap(UUID.init(uuidString:)).map { .web(.owner($0)) }
        case "live":
            Task { await model.refreshJourneys() }
            return .home
        default:
            return nil
        }
    }

    private static let simpleScreens: [String: AppModel.Screen] = [
        "welcome": .welcome,
        "beforeYouWalk": .beforeYouWalk,
        "camera": .cameraAccess,
        "howItWorks": .howItWorks,
        "unsupported": .unsupported,
        "signIn": .signIn,
        "example": .web(.example),
        "capture": .capture,
    ]

    // MARK: Requests

    static let requests: [String: OwnerRequest] = [
        "restroom": ask("restroom", "yes_no", "Do customers use a restroom?", "Count it if customers can ask to use it."),
        "inside_doors": ask(
            "inside_doors", "yes_no", "Do customers go through any doors inside the shop, like a restroom door?",
            "Doors between rooms count. The front door doesn\u{2019}t."),
        "entrance_threshold": ask(
            "entrance_threshold", "photo", "Send a low photo of the front doorway",
            "Open the door, crouch just outside and hold your phone a few inches off the ground, pointed into the shop. If you have a tape measure, stand it next to the threshold so we can read its height against the half inch the standard allows."),
        "door_hardware": ask(
            "door_hardware", "photo", "Send a photo of the front door handle",
            "Straight on, close enough to see its shape. We\u{2019}ll check it opens with a closed fist and sits between 34 and 48 inches up."),
        "floor_surface": ask(
            "floor_surface", "photo", "Send a photo of the floor just inside the front door",
            "Include any mat. We\u{2019}ll check it lies flat, stays put, and that carpet is no thicker than half an inch."),
        "restroom_turning_space": ask(
            "restroom_turning_space", "photo", "Send a photo of the customer restroom from the doorway",
            "Stand in the door and get the whole room in. We\u{2019}ll check there\u{2019}s a 60 inch circle to turn around in."),
        "door_opening_force": ask(
            "door_opening_force", "number", "How hard are the inside doors to push open?",
            "A door pressure gauge from a hardware store measures it. Push each inside door open with it and send the highest number.",
            unit: "lb"),
    ]

    private static func ask(_ id: String, _ kind: String, _ title: String, _ detail: String, unit: String? = nil) -> OwnerRequest {
        OwnerRequest(id: id, kind: kind, timing: "in_shop", title: title, detail: detail, unit: unit, status: "open")
    }

    private static func setup(_ name: String, argument: String?, model: AppModel) -> ShopSetupModel {
        let order = ["restroom", "inside_doors", "entrance_threshold", "door_hardware", "floor_surface",
                     "restroom_turning_space", "door_opening_force"]
        let id = name == "push" ? "door_opening_force" : (argument ?? "restroom")
        let step: ShopSetupModel.Step = switch name {
        case "question": .question(requests[id] ?? requests["restroom"]!)
        case "photo": .photo(requests[id] ?? requests["door_hardware"]!)
        case "push": .pushForce(requests["door_opening_force"]!)
        default: .measuring
        }
        let done = order.firstIndex(of: id) ?? order.count
        let setup = ShopSetupModel(debugStep: step, progress: (done, order.count), app: model)
        setup.debugPrompt = name == "measuring" ? measuringPrompt(argument) : nil
        return setup
    }

    private static func measuringPrompt(_ argument: String?) -> MeasuringView.Prompt {
        switch argument {
        case "notifications": .notifications
        case "waiting": .none
        default: .save
        }
    }

    // MARK: Home

    private static func journeys(_ argument: String?) -> [Journey] {
        let shops: [(String, String, String, Int?)] = switch argument {
        case "photos": [("Tea House", "photos", "Take 2 more photos", 2)]
        case "measuring": [("Tea House", "measuring", "We\u{2019}re measuring your shop", nil)]
        case "many": [
            ("Tea House", "checklist", "1 of 3 done. Next: move the display case 6 inches toward the wall", 2),
            ("Tea House Annex", "results", "Your results are ready. 2 things to fix", 2),
            ("Corner Books", "photos", "Take 1 more photo", 1),
        ]
        default: [("Tea House", "results", "Your results are ready. 3 things to fix", 3)]
        }
        return shops.map { name, kind, title, count in
            Journey(scanID: UUID(), shopName: name, stage: "results",
                nextStep: .init(kind: kind, title: title, count: count), toolsUnlocked: kind != "photos")
        }
    }

    // MARK: The walk

    /// A 6 by 4 metre room with its four walls, some of them done.
    private static func walk(_ state: String) -> CaptureSessionStore {
        let walls: [(SIMD3<Float>, Float, Float)] = [
            (SIMD3(0, 1.4, -2), 0, 6), (SIMD3(3, 1.4, 0), -.pi / 2, 4),
            (SIMD3(0, 1.4, 2), .pi, 6), (SIMD3(-3, 1.4, 0), .pi / 2, 4),
        ]
        let surfaces = walls.map { centre, angle, width in
            var transform = simd_float4x4(simd_quatf(angle: angle, axis: SIMD3(0, 1, 0)))
            transform.columns.3 = SIMD4(centre, 1)
            return SurfaceSnapshot(id: UUID(), width: width, height: 2.8, transform: transform, confidence: .high)
        }
        let finished = finishedWalls(for: state)
        let coverage = zip(surfaces.indices, surfaces).map { index, surface in
            let segments = (0..<10).map { index < finished || (index == finished && $0 < 4) }
            let fraction = Double(segments.filter { $0 }.count) / 10
            return SurfaceCoverage(id: surface.id, observedFraction: fraction, observedSegments: segments,
                viewpointCount: index < finished ? 3 : 1, highConfidence: true, area: surface.width * 2.8)
        }
        var snapshot = CoverageSnapshot(surfaces: coverage)
        let started = state == "start"
        snapshot.unfinishedDirection = CoverageAngle(radians: started ? 0 : .pi)
        snapshot.instruction = started ? CoverageSnapshot.openingInstruction : "Point the phone at the wall behind you."
        let store = CaptureSessionStore()
        store.showForDebugging(coverage: snapshot, surfaces: surfaces, secondsLeft: state == "warning" ? 24 : nil)
        return store
    }

    private static func finishedWalls(for state: String) -> Int {
        switch state {
        case "start": 0
        case "complete": 4
        default: 2
        }
    }
}
#endif
