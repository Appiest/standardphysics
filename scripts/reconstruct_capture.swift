import Foundation
import RealityKit
import simd
import Darwin

private enum CLIError: Error, CustomStringConvertible {
    case usage(String)
    case unsupported
    case invalidInput(String)
    case invalidOutput(String)
    case processing(String)
    case cancelled

    var description: String {
        switch self {
        case .usage(let message):
            return message
        case .unsupported:
            return "RealityKit PhotogrammetrySession is not supported on this Mac."
        case .invalidInput(let message):
            return "Invalid input: \(message)"
        case .invalidOutput(let message):
            return "Invalid output: \(message)"
        case .processing(let message):
            return "Photogrammetry failed: \(message)"
        case .cancelled:
            return "Photogrammetry processing was cancelled."
        }
    }
}

private enum DetailChoice: String {
    case reduced
    case medium
    case full
    case raw

    var requestDetail: PhotogrammetrySession.Request.Detail {
        switch self {
        case .reduced:
            return .reduced
        case .medium:
            return .medium
        case .full:
            return .full
        case .raw:
            return .raw
        }
    }
}

private enum OrderingChoice: String {
    case sequential
    case unordered

    var sessionOrdering: PhotogrammetrySession.Configuration.SampleOrdering {
        switch self {
        case .sequential:
            return .sequential
        case .unordered:
            return .unordered
        }
    }
}

private struct Options {
    let inputDirectory: URL
    let outputURL: URL
    let detail: DetailChoice
    let ordering: OrderingChoice
    let checkpointDirectory: URL?
    let posesOutputURL: URL?
}

private struct PoseSidecar: Codable {
    let formatVersion: Int
    let generatedAt: String
    let inputDirectory: String
    let matrixLayout: String
    let samples: [PoseSample]
}

private struct PoseSample: Codable {
    let sampleID: Int
    let imagePath: String?
    let translation: [Float]
    let rotationQuaternionXYZW: [Float]
    let transform: [[Float]]
    let intrinsics: [[Float]]?
}

private enum Logger {
    static func info(_ message: String) {
        write("INFO", message, to: FileHandle.standardOutput)
    }

    static func warning(_ message: String) {
        write("WARN", message, to: FileHandle.standardError)
    }

    static func error(_ message: String) {
        write("ERROR", message, to: FileHandle.standardError)
    }

    private static func write(_ level: String, _ message: String, to handle: FileHandle) {
        let formatter = ISO8601DateFormatter()
        let line = "[\(formatter.string(from: Date()))] \(level) \(message)\n"
        handle.write(Data(line.utf8))
        try? handle.synchronize()
    }
}

private struct ReconstructionResult {
    let modelFileURL: URL
    let poses: PhotogrammetrySession.Poses?
}

private struct ReconstructCaptureCLI {
    static let programName = "reconstruct-capture"

    static func run(arguments: [String]) async throws {
        if arguments.contains("--help") || arguments.contains("-h") {
            printUsage()
            return
        }

        let options = try parse(arguments: arguments)
        try validateSupport()
        try validateInputDirectory(options.inputDirectory)
        let inputFileCount = try countRegularFiles(in: options.inputDirectory)
        let limits = PhotogrammetrySession.limits

        Logger.info("RealityKit PhotogrammetrySession is supported")
        Logger.info("SDK limits: maximum input images=\(limits.maximumNumberOfInputImages), maximum input image dimension=\(limits.maximumInputImageDimension) pixels")
        Logger.info("Input directory contains \(inputFileCount) regular file(s)")
        guard inputFileCount > 0 else {
            throw CLIError.invalidInput("directory contains no regular files")
        }
        guard inputFileCount <= limits.maximumNumberOfInputImages else {
            throw CLIError.invalidInput("directory contains \(inputFileCount) regular files, but this SDK supports at most \(limits.maximumNumberOfInputImages) input images per session")
        }

        try prepareOutputURL(options.outputURL, label: "USDZ output")
        if let posesOutputURL = options.posesOutputURL {
            try prepareOutputURL(posesOutputURL, label: "pose sidecar output")
            guard posesOutputURL.standardizedFileURL != options.outputURL.standardizedFileURL else {
                throw CLIError.invalidOutput("pose sidecar path must differ from the USDZ output path")
            }
        }
        if let checkpointDirectory = options.checkpointDirectory {
            try prepareDirectory(checkpointDirectory, label: "checkpoint directory")
        }

        Logger.info("detail=\(options.detail.rawValue), ordering=\(options.ordering.rawValue), object masking=false, feature sensitivity=high")
        if let checkpointDirectory = options.checkpointDirectory {
            Logger.info("checkpoint directory=\(checkpointDirectory.path)")
        }
        if let posesOutputURL = options.posesOutputURL {
            Logger.info("pose sidecar output=\(posesOutputURL.path)")
        }

        let result = try await reconstruct(options: options)
        try validateModelOutput(result.modelFileURL)
        Logger.info("USDZ output is ready: \(result.modelFileURL.path)")

        if let posesOutputURL = options.posesOutputURL {
            guard let poses = result.poses else {
                throw CLIError.processing("the SDK did not return a poses result")
            }
            if !FileManager.default.fileExists(atPath: posesOutputURL.path) {
                try writePoseSidecar(poses, inputDirectory: options.inputDirectory, to: posesOutputURL)
                Logger.info("pose sidecar is ready: \(posesOutputURL.path)")
            }
        }
    }

    private static func parse(arguments: [String]) throws -> Options {
        var positional: [String] = []
        var detail: DetailChoice = .reduced
        var ordering: OrderingChoice = .unordered
        var checkpointPath: String?
        var posesOutputPath: String?
        var index = 0

        while index < arguments.count {
            let argument = arguments[index]
            switch argument {
            case "--detail":
                index += 1
                detail = try parseDetail(value: try requiredValue(arguments, at: index, for: "--detail"))
            case let value where value.hasPrefix("--detail="):
                detail = try parseDetail(value: String(value.dropFirst("--detail=".count)))
            case "--ordering":
                index += 1
                ordering = try parseOrdering(value: try requiredValue(arguments, at: index, for: "--ordering"))
            case let value where value.hasPrefix("--ordering="):
                ordering = try parseOrdering(value: String(value.dropFirst("--ordering=".count)))
            case "--checkpoint":
                index += 1
                checkpointPath = try requiredValue(arguments, at: index, for: "--checkpoint")
            case let value where value.hasPrefix("--checkpoint="):
                checkpointPath = String(value.dropFirst("--checkpoint=".count))
            case "--poses-output":
                index += 1
                posesOutputPath = try requiredValue(arguments, at: index, for: "--poses-output")
            case let value where value.hasPrefix("--poses-output="):
                posesOutputPath = String(value.dropFirst("--poses-output=".count))
            case "--help", "-h":
                break
            default:
                if argument.hasPrefix("-") {
                    throw CLIError.usage("unknown option '\(argument)'; run with --help for usage")
                }
                positional.append(argument)
            }
            index += 1
        }

        guard positional.count == 2 else {
            throw CLIError.usage("expected exactly an input image directory and an output USDZ path; run with --help for usage")
        }

        let inputURL = URL(fileURLWithPath: positional[0]).standardizedFileURL
        let outputURL = URL(fileURLWithPath: positional[1]).standardizedFileURL
        let checkpointURL = checkpointPath.map { URL(fileURLWithPath: $0).standardizedFileURL }
        let posesOutputURL = posesOutputPath.map { URL(fileURLWithPath: $0).standardizedFileURL }
        return Options(
            inputDirectory: inputURL,
            outputURL: outputURL,
            detail: detail,
            ordering: ordering,
            checkpointDirectory: checkpointURL,
            posesOutputURL: posesOutputURL
        )
    }

    private static func requiredValue(_ arguments: [String], at index: Int, for option: String) throws -> String {
        guard arguments.indices.contains(index), !arguments[index].hasPrefix("-") else {
            throw CLIError.usage("option \(option) requires a value")
        }
        return arguments[index]
    }

    private static func parseDetail(value: String) throws -> DetailChoice {
        guard let detail = DetailChoice(rawValue: value.lowercased()) else {
            throw CLIError.usage("unsupported detail '\(value)'; use reduced, medium, full, or raw")
        }
        return detail
    }

    private static func parseOrdering(value: String) throws -> OrderingChoice {
        guard let ordering = OrderingChoice(rawValue: value.lowercased()) else {
            throw CLIError.usage("unsupported ordering '\(value)'; use sequential or unordered")
        }
        return ordering
    }

    private static func validateSupport() throws {
        guard PhotogrammetrySession.isSupported else {
            throw CLIError.unsupported
        }
    }

    private static func validateInputDirectory(_ url: URL) throws {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw CLIError.invalidInput("'\(url.path)' is not an existing directory")
        }
    }

    private static func countRegularFiles(in directory: URL) throws -> Int {
        let keys: [URLResourceKey] = [.isRegularFileKey, .isHiddenKey]
        let urls = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles])
        return try urls.reduce(into: 0) { count, url in
            let values = try url.resourceValues(forKeys: Set(keys))
            if values.isRegularFile == true {
                count += 1
            }
        }
    }

    private static func prepareDirectory(_ url: URL, label: String) throws {
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) {
            guard isDirectory.boolValue else {
                throw CLIError.invalidOutput("\(label) path '\(url.path)' is a file")
            }
            return
        }
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        } catch {
            throw CLIError.invalidOutput("could not create \(label) '\(url.path)': \(errorDescription(error))")
        }
    }

    private static func prepareOutputURL(_ url: URL, label: String) throws {
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) {
            if isDirectory.boolValue {
                throw CLIError.invalidOutput("\(label) path '\(url.path)' is a directory")
            }
            throw CLIError.invalidOutput("\(label) path '\(url.path)' already exists; choose a new path or remove the old output")
        }

        let parent = url.deletingLastPathComponent()
        do {
            try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        } catch {
            throw CLIError.invalidOutput("could not create output directory '\(parent.path)': \(errorDescription(error))")
        }
    }

    private static func reconstruct(options: Options) async throws -> ReconstructionResult {
        var configuration = PhotogrammetrySession.Configuration()
        configuration.isObjectMaskingEnabled = false
        configuration.featureSensitivity = .high
        configuration.sampleOrdering = options.ordering.sessionOrdering
        if let checkpointDirectory = options.checkpointDirectory {
            configuration.checkpointDirectory = checkpointDirectory
        }

        let session: PhotogrammetrySession
        do {
            session = try PhotogrammetrySession(input: options.inputDirectory, configuration: configuration)
        } catch {
            throw CLIError.processing("could not create session: \(errorDescription(error))")
        }

        var requests: [PhotogrammetrySession.Request] = [
            .modelFile(url: options.outputURL, detail: options.detail.requestDetail)
        ]
        if options.posesOutputURL != nil {
            requests.append(.poses)
        }

        do {
            Logger.info("starting photogrammetry session")
            try session.process(requests: requests)
            return try await monitor(
                session: session,
                expectedModelURL: options.outputURL,
                posesRequested: options.posesOutputURL != nil,
                inputDirectory: options.inputDirectory,
                posesOutputURL: options.posesOutputURL
            )
        } catch {
            session.cancel()
            if let cliError = error as? CLIError {
                throw cliError
            }
            throw CLIError.processing(errorDescription(error))
        }
    }

    private static func monitor(
        session: PhotogrammetrySession,
        expectedModelURL: URL,
        posesRequested: Bool,
        inputDirectory: URL,
        posesOutputURL: URL?
    ) async throws -> ReconstructionResult {
        var modelResultURL: URL?
        var posesResult: PhotogrammetrySession.Poses?
        var sawProcessingComplete = false
        var stitchingWasIncomplete = false

        outputLoop: for try await output in session.outputs {
            switch output {
            case .inputComplete:
                Logger.info("inputComplete")
            case .requestProgress(let request, let fractionComplete):
                Logger.info("requestProgress request=\(requestDescription(request)) fraction=\(formatPercent(fractionComplete))")
            case .requestProgressInfo(let request, let progressInfo):
                let stage = progressInfo.processingStage.map(stageDescription) ?? "unknown"
                let remaining = progressInfo.estimatedRemainingTime.map(formatDuration) ?? "unknown"
                Logger.info("requestProgressInfo request=\(requestDescription(request)) stage=\(stage) estimatedRemaining=\(remaining)")
            case .requestComplete(let request, let result):
                Logger.info("requestComplete request=\(requestDescription(request))")
                switch result {
                case .modelFile(let url):
                    modelResultURL = url
                    Logger.info("model file result=\(url.path)")
                case .poses(let poses):
                    posesResult = poses
                    Logger.info("poses result samples=\(poses.posesBySample.count)")
                    if let posesOutputURL {
                        try writePoseSidecar(poses, inputDirectory: inputDirectory, to: posesOutputURL)
                        Logger.info("pose sidecar is ready: \(posesOutputURL.path)")
                    }
                default:
                    Logger.warning("requestComplete returned an unexpected result for this CLI")
                }
            case .requestError(let request, let error):
                Logger.error("requestError request=\(requestDescription(request)) error=\(errorDescription(error))")
                throw CLIError.processing("request \(requestDescription(request)) failed: \(errorDescription(error))")
            case .processingComplete:
                Logger.info("processingComplete")
                sawProcessingComplete = true
                break outputLoop
            case .processingCancelled:
                Logger.error("processingCancelled")
                throw CLIError.cancelled
            case .invalidSample(let id, let reason):
                Logger.warning("invalidSample id=\(id) reason=\(reason)")
            case .skippedSample(let id):
                Logger.warning("skippedSample id=\(id)")
            case .automaticDownsampling:
                Logger.warning("automaticDownsampling: the SDK reduced input image dimensions")
            case .stitchingIncomplete:
                Logger.error("stitchingIncomplete")
                stitchingWasIncomplete = true
            @unknown default:
                Logger.warning("received an output event that this CLI does not recognize")
            }
        }

        guard sawProcessingComplete else {
            throw CLIError.processing("output stream ended before processingComplete")
        }
        if stitchingWasIncomplete {
            throw CLIError.processing("the SDK reported stitchingIncomplete")
        }
        guard let modelResultURL else {
            throw CLIError.processing("no model file requestComplete result was received")
        }
        if posesRequested && posesResult == nil {
            throw CLIError.processing("no poses requestComplete result was received")
        }
        return ReconstructionResult(modelFileURL: modelResultURL == expectedModelURL ? expectedModelURL : modelResultURL, poses: posesResult)
    }

    private static func validateModelOutput(_ url: URL) throws {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), !isDirectory.boolValue else {
            throw CLIError.processing("the model file was not written at '\(url.path)'")
        }
        do {
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            let size = (attributes[.size] as? NSNumber)?.int64Value ?? 0
            guard size > 0 else {
                throw CLIError.processing("the model file at '\(url.path)' is empty")
            }
        } catch let error as CLIError {
            throw error
        } catch {
            throw CLIError.processing("could not inspect model output '\(url.path)': \(errorDescription(error))")
        }
    }

    private static func writePoseSidecar(
        _ poses: PhotogrammetrySession.Poses,
        inputDirectory: URL,
        to outputURL: URL
    ) throws {
        let samples = poses.posesBySample.keys.sorted().compactMap { sampleID -> PoseSample? in
            guard let pose = poses.posesBySample[sampleID] else {
                return nil
            }
            return PoseSample(
                sampleID: sampleID,
                imagePath: poses.urlsBySample[sampleID]?.path,
                translation: [pose.translation.x, pose.translation.y, pose.translation.z],
                rotationQuaternionXYZW: [pose.rotation.vector.x, pose.rotation.vector.y, pose.rotation.vector.z, pose.rotation.vector.w],
                transform: matrix4x4Rows(pose.transform.matrix),
                intrinsics: intrinsicsRows(pose)
            )
        }

        let sidecar = PoseSidecar(
            formatVersion: 1,
            generatedAt: ISO8601DateFormatter().string(from: Date()),
            inputDirectory: inputDirectory.path,
            matrixLayout: "row-major",
            samples: samples
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        do {
            let data = try encoder.encode(sidecar)
            try data.write(to: outputURL, options: .atomic)
        } catch {
            throw CLIError.invalidOutput("could not write pose sidecar '\(outputURL.path)': \(errorDescription(error))")
        }
    }

    private static func matrix4x4Rows(_ matrix: simd_float4x4) -> [[Float]] {
        (0..<4).map { row in
            (0..<4).map { column in
                matrix[column][row]
            }
        }
    }

    private static func matrix3x3Rows(_ matrix: simd_float3x3) -> [[Float]] {
        (0..<3).map { row in
            (0..<3).map { column in
                matrix[column][row]
            }
        }
    }

    private static func intrinsicsRows(_ pose: PhotogrammetrySession.Pose) -> [[Float]]? {
        if #available(macOS 26.0, *) {
            guard let intrinsics = pose.intrinsics else {
                return nil
            }
            return matrix3x3Rows(intrinsics)
        }
        return nil
    }

    private static func requestDescription(_ request: PhotogrammetrySession.Request) -> String {
        switch request {
        case .modelFile(_, let detail, _):
            return "modelFile(\(detailDescription(detail)))"
        case .modelEntity(let detail, _):
            return "modelEntity(\(detailDescription(detail)))"
        case .bounds:
            return "bounds"
        case .pointCloud:
            return "pointCloud"
        case .poses:
            return "poses"
        @unknown default:
            return "unknown"
        }
    }

    private static func detailDescription(_ detail: PhotogrammetrySession.Request.Detail) -> String {
        switch detail {
        case .preview:
            return "preview"
        case .reduced:
            return "reduced"
        case .medium:
            return "medium"
        case .full:
            return "full"
        case .raw:
            return "raw"
        case .custom:
            return "custom"
        @unknown default:
            return "unknown"
        }
    }

    private static func stageDescription(_ stage: PhotogrammetrySession.Output.ProcessingStage) -> String {
        switch stage {
        case .preProcessing:
            return "preProcessing"
        case .imageAlignment:
            return "imageAlignment"
        case .pointCloudGeneration:
            return "pointCloudGeneration"
        case .meshGeneration:
            return "meshGeneration"
        case .textureMapping:
            return "textureMapping"
        case .optimization:
            return "optimization"
        @unknown default:
            return "unknown"
        }
    }

    private static func formatPercent(_ fraction: Double) -> String {
        String(format: "%.1f%%", min(max(fraction, 0.0), 1.0) * 100.0)
    }

    private static func formatDuration(_ seconds: TimeInterval) -> String {
        if seconds < 60 {
            return String(format: "%.0fs", seconds)
        }
        return String(format: "%.1fm", seconds / 60.0)
    }

    private static func errorDescription(_ error: Error) -> String {
        if let localized = error as? LocalizedError, let description = localized.errorDescription {
            return description
        }
        return String(describing: error)
    }

    static func printUsage() {
        let limits = PhotogrammetrySession.limits
        let supported = PhotogrammetrySession.isSupported
        let usage = """
        Usage: \(programName) <input-image-directory> <output.usdz> [options]

        Options:
          --detail <reduced|medium|full|raw>  Model detail (default: reduced)
          --ordering <sequential|unordered>   Input sample ordering (default: unordered)
          --checkpoint <directory>            Checkpoint directory for resumable work
          --poses-output <file.json>          Also request camera poses and write a sidecar
          --help                              Show this help

        This CLI disables object masking for whole-room captures and enables high feature sensitivity.
        The current SDK reports supported=\(supported), maximum input images=\(limits.maximumNumberOfInputImages), maximum input image dimension=\(limits.maximumInputImageDimension) pixels.
        The SDK exposes reduced, medium, full, and raw model details on macOS; preview and custom are not exposed by this CLI.
        """
        Logger.info(usage)
    }
}

@main
private struct Main {
    static func main() async {
        do {
            try await ReconstructCaptureCLI.run(arguments: Array(CommandLine.arguments.dropFirst()))
        } catch let error as CLIError {
            Logger.error(error.description)
            if case .usage = error {
                ReconstructCaptureCLI.printUsage()
            }
            Darwin.exit(isUsage(error) ? 2 : 1)
        } catch {
            Logger.error(String(describing: error))
            Darwin.exit(1)
        }
    }

    private static func isUsage(_ error: CLIError) -> Bool {
        if case .usage = error {
            return true
        }
        return false
    }
}
