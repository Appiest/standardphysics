import CoreImage
import CoreVideo
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

private enum CLIError: Error, CustomStringConvertible {
    case usage(String)
    case invalidInput(String)
    case invalidOutput(String)
    case processing(String)

    var description: String {
        switch self {
        case .usage(let message):
            return message
        case .invalidInput(let message):
            return "Invalid input: \(message)"
        case .invalidOutput(let message):
            return "Invalid output: \(message)"
        case .processing(let message):
            return "Masking failed: \(message)"
        }
    }
}

private struct Options {
    let inputDirectory: URL
    let outputDirectory: URL
    let overwrite: Bool
    let excludeDetectedPeople: Bool
}

private struct ConfirmedPerson: Codable {
    let rectangleConfidence: Double
    let posePointCount: Int
    let normalizedBoundingBox: [Double]
}

private struct MaskResult {
    let image: CGImage
    let sourceSize: CGSize
    let maskSize: CGSize
    let personFraction: Double
    let confirmedPeople: [ConfirmedPerson]
}

private struct DetectionSummary: Codable {
    let mode: String
    let humanRectangleMinimumConfidence: Double
    let bodyPointMinimumConfidence: Double
    let bodyPointMinimumCount: Int
    let files: [String: [ConfirmedPerson]]
    let complete: Bool
    let processedFiles: [String]
    let skippedFiles: [String]

    private enum CodingKeys: String, CodingKey {
        case mode
        case humanRectangleMinimumConfidence
        case bodyPointMinimumConfidence
        case bodyPointMinimumCount
        case files
        case complete
        case processedFiles
        case skippedFiles
    }

    init(
        mode: String,
        humanRectangleMinimumConfidence: Double,
        bodyPointMinimumConfidence: Double,
        bodyPointMinimumCount: Int,
        files: [String: [ConfirmedPerson]],
        complete: Bool,
        processedFiles: [String],
        skippedFiles: [String]
    ) {
        self.mode = mode
        self.humanRectangleMinimumConfidence = humanRectangleMinimumConfidence
        self.bodyPointMinimumConfidence = bodyPointMinimumConfidence
        self.bodyPointMinimumCount = bodyPointMinimumCount
        self.files = files
        self.complete = complete
        self.processedFiles = processedFiles
        self.skippedFiles = skippedFiles
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        mode = try container.decode(String.self, forKey: .mode)
        humanRectangleMinimumConfidence = try container.decode(Double.self, forKey: .humanRectangleMinimumConfidence)
        bodyPointMinimumConfidence = try container.decode(Double.self, forKey: .bodyPointMinimumConfidence)
        bodyPointMinimumCount = try container.decode(Int.self, forKey: .bodyPointMinimumCount)
        files = try container.decode([String: [ConfirmedPerson]].self, forKey: .files)
        complete = try container.decodeIfPresent(Bool.self, forKey: .complete) ?? true
        processedFiles = try container.decodeIfPresent([String].self, forKey: .processedFiles) ?? []
        skippedFiles = try container.decodeIfPresent([String].self, forKey: .skippedFiles) ?? []
    }
}

private enum Log {
    static func info(_ message: String) {
        print("[mask-people] INFO \(message)")
    }

    static func warning(_ message: String) {
        FileHandle.standardError.write(Data("[mask-people] WARN \(message)\n".utf8))
    }
}

private struct MaskCapturePeopleCLI {
    static func run(arguments: [String]) throws {
        if arguments.contains("--help") || arguments.contains("-h") {
            printUsage()
            return
        }

        let options = try parse(arguments: arguments)
        try validateInput(options.inputDirectory)
        try prepareOutput(options.outputDirectory)

        let files = try imageFiles(in: options.inputDirectory)
        guard !files.isEmpty else {
            throw CLIError.invalidInput("directory contains no JPEG images")
        }

        Log.info("input=\(options.inputDirectory.path)")
        Log.info("output=\(options.outputDirectory.path)")
        Log.info("orientation=CGImagePropertyOrientation.right for Vision, .left for output")
        Log.info("quality=accurate, skipExisting=\(!options.overwrite)")
        if options.excludeDetectedPeople {
            Log.info(
                "detected-person exclusion=enabled rectangleConfidence>=\(humanRectangleMinimumConfidence) "
                + "posePoints>=\(bodyPointMinimumCount) at confidence>=\(bodyPointMinimumConfidence)"
            )
            Log.warning(
                "detected-person exclusion is experimental: confirmed rectangles can include chairs, furniture, "
                + "or occluded background, and this detector can still miss people"
            )
        }

        let context = CIContext()
        var processed = 0
        var skipped = 0
        let inputNames = Set(files.map(\.lastPathComponent))
        var detections = try initialDetections(
            in: options.outputDirectory,
            inputNames: inputNames,
            excludeDetectedPeople: options.excludeDetectedPeople,
            overwrite: options.overwrite
        )
        var processedFiles: [String] = []
        var skippedFiles: [String] = []
        for input in files {
            let output = options.outputDirectory
                .appendingPathComponent(input.deletingPathExtension().lastPathComponent)
                .appendingPathExtension("png")
            if !options.overwrite && FileManager.default.fileExists(atPath: output.path) {
                skipped += 1
                skippedFiles.append(input.lastPathComponent)
                Log.info("skip existing input=\(input.lastPathComponent) output=\(output.lastPathComponent)")
                continue
            }

            do {
                let result = try makeMask(
                    input: input,
                    context: context,
                    excludeDetectedPeople: options.excludeDetectedPeople
                )
                try writePNG(result.image, to: output)
                processed += 1
                processedFiles.append(input.lastPathComponent)
                if options.excludeDetectedPeople {
                    detections[input.lastPathComponent] = result.confirmedPeople
                }
                Log.info(
                    "wrote input=\(input.lastPathComponent) output=\(output.lastPathComponent) "
                    + "source=\(Int(result.sourceSize.width))x\(Int(result.sourceSize.height)) "
                    + "mask=\(Int(result.maskSize.width))x\(Int(result.maskSize.height)) "
                    + String(format: "personCoverage=%.3f", result.personFraction)
                    + (options.excludeDetectedPeople ? " confirmedPeople=\(result.confirmedPeople.count)" : "")
                )
            } catch {
                throw CLIError.processing("\(input.path): \(errorDescription(error))")
            }
        }
        if options.excludeDetectedPeople {
            let summary = DetectionSummary(
                mode: "exclude-detected-people",
                humanRectangleMinimumConfidence: Double(humanRectangleMinimumConfidence),
                bodyPointMinimumConfidence: Double(bodyPointMinimumConfidence),
                bodyPointMinimumCount: bodyPointMinimumCount,
                files: detections,
                complete: skippedFiles.isEmpty && Set(detections.keys) == inputNames,
                processedFiles: processedFiles,
                skippedFiles: skippedFiles
            )
            try writeDetectionSummary(summary, to: options.outputDirectory)
        }
        Log.info("complete processed=\(processed) skipped=\(skipped) total=\(files.count)")
    }

    private static func parse(arguments: [String]) throws -> Options {
        var positional: [String] = []
        var overwrite = false
        var excludeDetectedPeople = false
        for argument in arguments {
            switch argument {
            case "--overwrite":
                overwrite = true
            case "--exclude-detected-people":
                excludeDetectedPeople = true
            case "--help", "-h":
                break
            default:
                if argument.hasPrefix("-") {
                    throw CLIError.usage("unknown option '\(argument)'; run with --help for usage")
                }
                positional.append(argument)
            }
        }
        guard positional.count == 2 else {
            throw CLIError.usage("expected an input image directory and an output mask directory; run with --help for usage")
        }
        return Options(
            inputDirectory: URL(fileURLWithPath: positional[0]).standardizedFileURL,
            outputDirectory: URL(fileURLWithPath: positional[1]).standardizedFileURL,
            overwrite: overwrite,
            excludeDetectedPeople: excludeDetectedPeople
        )
    }

    private static func validateInput(_ url: URL) throws {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw CLIError.invalidInput("'\(url.path)' is not an existing directory")
        }
    }

    private static func prepareOutput(_ url: URL) throws {
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) {
            guard isDirectory.boolValue else {
                throw CLIError.invalidOutput("'\(url.path)' is a file")
            }
            return
        }
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        } catch {
            throw CLIError.invalidOutput("could not create '\(url.path)': \(errorDescription(error))")
        }
    }

    private static func imageFiles(in directory: URL) throws -> [URL] {
        let urls = try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        )
        return try urls
            .filter { ["jpg", "jpeg"].contains($0.pathExtension.lowercased()) }
            .filter { try $0.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true }
            .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
    }

    private static func initialDetections(
        in directory: URL,
        inputNames: Set<String>,
        excludeDetectedPeople: Bool,
        overwrite: Bool
    ) throws -> [String: [ConfirmedPerson]] {
        guard excludeDetectedPeople && !overwrite else {
            return [:]
        }
        let summaryURL = directory.appendingPathComponent("detection-summary.json")
        guard FileManager.default.fileExists(atPath: summaryURL.path) else {
            return [:]
        }
        let data: Data
        do {
            data = try Data(contentsOf: summaryURL)
        } catch {
            throw CLIError.invalidOutput(
                "could not read existing detection summary '\(summaryURL.path)': \(errorDescription(error))"
            )
        }
        let summary: DetectionSummary
        do {
            summary = try JSONDecoder().decode(DetectionSummary.self, from: data)
        } catch {
            throw CLIError.invalidOutput(
                "could not decode existing detection summary '\(summaryURL.path)': \(errorDescription(error)); "
                + "use --overwrite to regenerate it"
            )
        }
        guard summary.mode == "exclude-detected-people",
              abs(summary.humanRectangleMinimumConfidence - Double(humanRectangleMinimumConfidence)) < 0.000001,
              abs(summary.bodyPointMinimumConfidence - Double(bodyPointMinimumConfidence)) < 0.000001,
              summary.bodyPointMinimumCount == bodyPointMinimumCount else {
            throw CLIError.invalidOutput(
                "existing detection summary uses different exclusion thresholds; use --overwrite to regenerate it"
            )
        }
        return summary.files.filter { inputNames.contains($0.key) }
    }

    private static func makeMask(
        input: URL,
        context: CIContext,
        excludeDetectedPeople: Bool
    ) throws -> MaskResult {
        guard let source = CGImageSourceCreateWithURL(input as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw CLIError.processing("could not decode JPEG")
        }

        let handler = VNImageRequestHandler(cgImage: image, orientation: .right, options: [:])
        let request = VNGeneratePersonSegmentationRequest()
        request.qualityLevel = .accurate
        request.outputPixelFormat = kCVPixelFormatType_OneComponent8
        try handler.perform([request])
        guard let observation = request.results?.first else {
            throw CLIError.processing("Vision returned no person mask")
        }

        let buffer = observation.pixelBuffer
        let maskWidth = CVPixelBufferGetWidth(buffer)
        let maskHeight = CVPixelBufferGetHeight(buffer)
        let personFraction = try foregroundFraction(buffer)
        let mask = CIImage(cvPixelBuffer: buffer)
        let inverted = mask.applyingFilter("CIColorInvert")
        let restored = inverted.oriented(.left)
        let fitted = fit(restored, width: image.width, height: image.height)
        guard let output = context.createCGImage(
            fitted,
            from: CGRect(x: 0, y: 0, width: image.width, height: image.height)
        ) else {
            throw CLIError.processing("could not render the restored mask")
        }

        guard excludeDetectedPeople else {
            return MaskResult(
                image: output,
                sourceSize: CGSize(width: image.width, height: image.height),
                maskSize: CGSize(width: maskWidth, height: maskHeight),
                personFraction: personFraction,
                confirmedPeople: []
            )
        }

        let confirmedPeople = try confirmedPeople(handler: handler)
        let excluded = try applyConfirmedPeople(confirmedPeople, to: output)
        return MaskResult(
            image: excluded,
            sourceSize: CGSize(width: image.width, height: image.height),
            maskSize: CGSize(width: maskWidth, height: maskHeight),
            personFraction: personFraction,
            confirmedPeople: confirmedPeople
        )
    }

    private static let humanRectangleMinimumConfidence: Float = 0.60
    private static let bodyPointMinimumConfidence: Float = 0.15
    private static let bodyPointMinimumCount = 3

    private static func confirmedPeople(handler: VNImageRequestHandler) throws -> [ConfirmedPerson] {
        let rectangleRequest = VNDetectHumanRectanglesRequest()
        let poseRequest = VNDetectHumanBodyPoseRequest()
        try handler.perform([rectangleRequest, poseRequest])

        let poses = poseRequest.results ?? []
        var posePointSets: [[(x: CGFloat, y: CGFloat, confidence: Float)]] = []
        for pose in poses {
            let points = try pose.recognizedPoints(.all)
            posePointSets.append(points.values.map {
                (x: $0.location.x, y: $0.location.y, confidence: $0.confidence)
            })
        }

        return (rectangleRequest.results ?? []).compactMap { observation in
            guard observation.confidence >= humanRectangleMinimumConfidence else {
                return nil
            }
            let pointCount = posePointSets.map { points in
                points.filter { point in
                    point.confidence >= bodyPointMinimumConfidence
                        && observation.boundingBox.contains(CGPoint(x: point.x, y: point.y))
                }.count
            }.max() ?? 0
            guard pointCount >= bodyPointMinimumCount else {
                return nil
            }
            let box = observation.boundingBox
            return ConfirmedPerson(
                rectangleConfidence: Double(observation.confidence),
                posePointCount: pointCount,
                normalizedBoundingBox: [
                    Double(box.minX), Double(box.minY), Double(box.width), Double(box.height)
                ]
            )
        }
    }

    private static func applyConfirmedPeople(
        _ people: [ConfirmedPerson],
        to image: CGImage
    ) throws -> CGImage {
        let width = image.width
        let height = image.height
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            throw CLIError.processing("could not create drawing context for detected-person exclusions")
        }
        let bounds = CGRect(x: 0, y: 0, width: width, height: height)
        context.draw(image, in: bounds)
        context.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 1))
        for person in people {
            guard person.normalizedBoundingBox.count == 4 else { continue }
            let box = CGRect(
                x: person.normalizedBoundingBox[0],
                y: person.normalizedBoundingBox[1],
                width: person.normalizedBoundingBox[2],
                height: person.normalizedBoundingBox[3]
            )
            // Vision saw the source with .right. Convert its upright normalized box
            // back into the raw sensor orientation used by the output mask.
            let rawBox = CGRect(
                x: (1 - box.maxY) * CGFloat(width),
                y: box.minX * CGFloat(height),
                width: box.height * CGFloat(width),
                height: box.width * CGFloat(height)
            ).intersection(bounds)
            if !rawBox.isNull && !rawBox.isEmpty {
                context.fill(rawBox)
            }
        }
        guard let output = context.makeImage() else {
            throw CLIError.processing("could not render detected-person exclusions")
        }
        return output
    }

    private static func foregroundFraction(_ buffer: CVPixelBuffer) throws -> Double {
        guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_OneComponent8 else {
            throw CLIError.processing("Vision returned an unexpected mask pixel format")
        }
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else {
            throw CLIError.processing("Vision returned an empty mask")
        }
        let width = CVPixelBufferGetWidth(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(buffer)
        let bytes = base.assumingMemoryBound(to: UInt8.self)
        var foreground = 0
        for row in 0..<height {
            for column in 0..<width where bytes[row * bytesPerRow + column] >= 128 {
                foreground += 1
            }
        }
        return Double(foreground) / Double(width * height)
    }

    private static func fit(_ image: CIImage, width: Int, height: Int) -> CIImage {
        let extent = image.extent
        let translated = image.transformed(
            by: CGAffineTransform(translationX: -extent.origin.x, y: -extent.origin.y)
        )
        return translated.transformed(
            by: CGAffineTransform(scaleX: CGFloat(width) / extent.width, y: CGFloat(height) / extent.height)
        )
    }

    private static func writePNG(_ image: CGImage, to destination: URL) throws {
        let temporary = destination.deletingLastPathComponent()
            .appendingPathComponent(".\(destination.lastPathComponent).\(UUID().uuidString).tmp")
        guard let writer = CGImageDestinationCreateWithURL(
            temporary as CFURL,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            throw CLIError.invalidOutput("could not create PNG writer for '\(destination.path)'")
        }
        CGImageDestinationAddImage(writer, image, nil)
        guard CGImageDestinationFinalize(writer) else {
            try? FileManager.default.removeItem(at: temporary)
            throw CLIError.invalidOutput("could not write PNG '\(destination.path)'")
        }
        do {
            if FileManager.default.fileExists(atPath: destination.path) {
                try FileManager.default.removeItem(at: destination)
            }
            try FileManager.default.moveItem(at: temporary, to: destination)
        } catch {
            try? FileManager.default.removeItem(at: temporary)
            throw CLIError.invalidOutput("could not finalize '\(destination.path)': \(errorDescription(error))")
        }
    }

    private static func writeDetectionSummary(_ summary: DetectionSummary, to directory: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(summary)
        do {
            try data.write(to: directory.appendingPathComponent("detection-summary.json"), options: .atomic)
        } catch {
            throw CLIError.invalidOutput(
                "could not write detection summary in '\(directory.path)': \(errorDescription(error))"
            )
        }
    }

    private static func printUsage() {
        print("""
        Usage: mask-capture-people [--overwrite] [--exclude-detected-people] <input-images-directory> <output-masks-directory>

        Runs accurate Vision person segmentation on JPEGs in the input directory.
        Source JPEGs are never changed. Existing PNG masks are skipped unless --overwrite is supplied.
        Masks are black for detected people and white for background.

        --exclude-detected-people is opt-in. It additionally requires a human rectangle with confidence >= 0.60
        and at least 3 body-pose joints with confidence >= 0.15 before painting that detector box black.
        The coarse box can include chairs, furniture, or occluded background, and people can still be missed.
        The option writes detection-summary.json beside the generated masks; resumed runs preserve prior
        detections and mark the summary incomplete while any existing PNGs are skipped. Default output is unchanged.
        """)
    }
}

private func errorDescription(_ error: Error) -> String {
    if let localized = error as? LocalizedError, let description = localized.errorDescription {
        return description
    }
    return String(describing: error)
}

do {
    try MaskCapturePeopleCLI.run(arguments: Array(CommandLine.arguments.dropFirst()))
} catch {
    Log.warning(errorDescription(error))
    exit(1)
}
