import SceneKit
import SwiftUI
import RoomPlan

struct ReviewScreen: View {
    @ObservedObject var model: AppModel
    let scan: CapturedScan
    @State private var name: String
    @State private var roomScene: SCNScene?
    @State private var detailScene: SCNScene?
    @State private var saveError: String?
    @State private var selectedSurface = "Scanned surfaces"
    @State private var locator = ScanObjectLocator(objects: [])

    init(model: AppModel, scan: CapturedScan) {
        self.model = model
        self.scan = scan
        _name = State(initialValue: scan.name ?? "")
    }

    var body: some View {
        ZStack {
            DraftingPaper()
            VStack(spacing: 0) {
                ZStack(alignment: .topLeading) {
                    ScannedRoomView(scene: detailScene ?? roomScene, locator: locator,
                        onSelect: { selectedSurface = $0 })
                    .background(AppTheme.panel)
                    Button {
                        model.showStart()
                    } label: {
                        Image(systemName: "chevron.left")
                            .font(.headline)
                            .frame(width: AppTheme.Size.touchTarget, height: AppTheme.Size.touchTarget)
                            .background(AppTheme.panel)
                            .clipShape(Circle())
                            .shadow(
                                color: AppTheme.Shadow.color,
                                radius: AppTheme.Shadow.radius,
                                y: AppTheme.Shadow.y
                            )
                    }
                    .foregroundStyle(AppTheme.ink)
                    .padding(AppTheme.Spacing.card)
                    .accessibilityLabel("Back")
                }

                VStack(alignment: .leading, spacing: AppTheme.Spacing.card) {
                    Text(selectedSurface).font(AppTheme.Typography.heading).accessibilityAddTraits(.updatesFrequently)
                    if let notice = scan.captureNotice {
                        Text(notice).foregroundStyle(AppTheme.mutedInk)
                        Button("Record another pass") { model.beginCapture() }
                            .buttonStyle(AppButtonStyle(.secondary))
                    }
                    if detailScene != nil {
                        ShareLink("Share detailed scan", item: scan.directory.appendingPathComponent("lidar-mesh.json"))
                    } else {
                        Button("Scan detailed surfaces") { model.beginCapture() }
                            .buttonStyle(AppButtonStyle(.secondary))
                    }
                    Text("Name this shop")
                        .font(AppTheme.Typography.title)
                    TextField("Boba shop", text: $name)
                        .textInputAutocapitalization(.words)
                        .font(AppTheme.Typography.body)
                        .padding(AppTheme.Spacing.control)
                        .background(AppTheme.panel)
                        .overlay {
                            RoundedRectangle(cornerRadius: AppTheme.Radius.field, style: .continuous)
                                .stroke(AppTheme.fieldOutline, lineWidth: 1)
                        }
                    Button("Upload scan") {
                        do {
                            model.upload(scan: try scan.renamed(trimmedName), name: trimmedName)
                        } catch { saveError = "Free some space on this phone, then save again." }
                    }
                    .buttonStyle(AppButtonStyle())
                    .disabled(trimmedName.isEmpty)
                    if let saveError { Text(saveError).foregroundStyle(AppTheme.warning) }
                }
                .padding(AppTheme.Spacing.section)
            }
        }
        .task(id: scan.id) {
            roomScene = try? SCNScene(url: scan.roomURL)
            detailScene = try? LidarMesh.load(from: scan.directory).makeScene()
            if detailScene == nil { selectedSurface = "Simplified layout" }
            if let data = try? Data(contentsOf: scan.directory.appendingPathComponent("room.json")),
               let room = try? JSONDecoder().decode(CapturedRoom.self, from: data) {
                locator = ScanObjectLocator(objects: RoomCoverage.snapshots(from: room))
            }
        }
    }

    private var trimmedName: String {
        name.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
