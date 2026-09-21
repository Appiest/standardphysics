import SceneKit
import SwiftUI

struct ScannedRoomView: UIViewRepresentable {
    let scene: SCNScene?
    let locator: ScanObjectLocator
    let onSelect: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.allowsCameraControl = true
        // Every surface is drawn flat, so there is nothing for a light to do.
        view.autoenablesDefaultLighting = false
        view.backgroundColor = UIColor(AppTheme.canvas)
        view.accessibilityLabel = "Scanned room. Drag to rotate. Pinch to zoom."
        view.addGestureRecognizer(UITapGestureRecognizer(target: context.coordinator,
            action: #selector(Coordinator.selectSurface(_:))))
        return view
    }

    func updateUIView(_ view: SCNView, context: Context) {
        context.coordinator.parent = self
        guard view.scene !== scene else { return }
        if let scene { Self.ink(scene) }
        view.scene = scene
    }

    /// Redraw the room the way the workspace draws it: flat paper faces with
    /// the edges inked over them, rather than a shaded grey model.
    ///
    /// The lines are a second copy of each mesh drawn in wireframe. A single
    /// material cannot fill and stroke at once, and stroking alone leaves a
    /// wall you can see straight through.
    private static func ink(_ scene: SCNScene) {
        guard scene.rootNode.childNode(withName: inkedMarker, recursively: false) == nil else { return }
        scene.rootNode.addChildNode(SCNNode(named: inkedMarker))
        scene.background.contents = UIColor(AppTheme.canvas)

        for node in scene.rootNode.childNodes(passingTest: { node, _ in node.geometry != nil }) {
            guard let geometry = node.geometry else { continue }
            geometry.materials = [paper]
            node.addChildNode(outline(of: geometry))
        }
    }

    private static let inkedMarker = "standardphysics.inked"

    private static var paper: SCNMaterial {
        let material = SCNMaterial()
        material.diffuse.contents = UIColor(AppTheme.panel)
        material.lightingModel = .constant
        material.isDoubleSided = true
        return material
    }

    private static func outline(of geometry: SCNGeometry) -> SCNNode {
        let lines = geometry.copy() as! SCNGeometry
        let material = SCNMaterial()
        material.diffuse.contents = UIColor(AppTheme.ink)
        material.lightingModel = .constant
        material.fillMode = .lines
        material.readsFromDepthBuffer = false
        lines.materials = [material]
        return SCNNode(geometry: lines)
    }

    @MainActor final class Coordinator: NSObject {
        var parent: ScannedRoomView
        init(parent: ScannedRoomView) { self.parent = parent }

        @objc func selectSurface(_ gesture: UITapGestureRecognizer) {
            guard let view = gesture.view as? SCNView,
                  let hit = view.hitTest(gesture.location(in: view)).first else { return }
            let point = SIMD3<Float>(hit.worldCoordinates.x, hit.worldCoordinates.y, hit.worldCoordinates.z)
            parent.onSelect(parent.locator.object(at: point)?.friendlyName.capitalized ?? "Scanned surface")
        }
    }
}

private extension SCNNode {
    convenience init(named name: String) {
        self.init()
        self.name = name
    }
}
