import ARKit
import SceneKit
import SwiftUI

/// The room painting itself in as it is scanned.
///
/// A transparent SceneKit layer over RoomPlan's own view, sharing its AR
/// session, so SceneKit does the camera tracking and the paint stays on the
/// wall without this file doing any per-frame work. The cells are placed once
/// and only touched again when their coverage changes.
///
/// Nothing here drives the session. RoomPlan owns it; this only watches.
struct PaintedCoverageView: UIViewRepresentable {
    let session: ARSession?
    let paint: [PaintedSample]

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.backgroundColor = .clear
        view.scene.background.contents = UIColor.clear
        view.isOpaque = false
        view.isUserInteractionEnabled = false
        view.rendersContinuously = true
        // Its own renderer, but never its own session: calling run here would
        // fight RoomPlan for the camera.
        if let session { view.session = session }
        view.scene.rootNode.addChildNode(context.coordinator.paintRoot)
        return view
    }

    func updateUIView(_ view: ARSCNView, context: Context) {
        if let session, view.session !== session { view.session = session }
        context.coordinator.apply(paint)
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    static func dismantleUIView(_ view: ARSCNView, coordinator: Coordinator) {
        view.session.delegate = nil
    }

    @MainActor
    final class Coordinator {
        let paintRoot = SCNNode()
        private var placed: [PaintedSample] = []

        /// Rebuilds only when the paint actually changed, because coverage
        /// arrives twice a second and the scene should not.
        func apply(_ paint: [PaintedSample]) {
            guard paint != placed else { return }
            placed = paint
            paintRoot.childNodes.forEach { $0.removeFromParentNode() }
            for sample in paint where sample.isObserved {
                paintRoot.addChildNode(Self.cell(at: sample))
            }
        }

        private static func cell(at sample: PaintedSample) -> SCNNode {
            let plane = SCNPlane(width: CellSize.side, height: CellSize.side)
            plane.firstMaterial?.diffuse.contents = UIColor(AppTheme.scanLine).withAlphaComponent(0.55)
            plane.firstMaterial?.isDoubleSided = true
            plane.firstMaterial?.lightingModel = .constant
            plane.firstMaterial?.writesToDepthBuffer = false

            let node = SCNNode(geometry: plane)
            // Lifted off the wall so it does not fight the surface for depth.
            let normal = simd_normalize(sample.worldNormal)
            let lifted = sample.worldPoint + normal * Float(CellSize.lift)
            node.simdPosition = lifted
            node.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 0, 1), to: normal)
            return node
        }
    }

    private enum CellSize {
        /// A hand's width, so a wall fills in at about the pace someone walks.
        static let side: CGFloat = 0.18
        static let lift: CGFloat = 0.01
    }
}
