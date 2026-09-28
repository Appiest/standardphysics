import AppKit
import CoreImage
import Vision

// Usage: swift segment_people.swift <frames-dir> <masks-dir>
// Writes one grayscale person mask per frame, same file name, at the frame's resolution.

let arguments = CommandLine.arguments
let framesURL = URL(fileURLWithPath: arguments[1])
let masksURL = URL(fileURLWithPath: arguments[2])
try FileManager.default.createDirectory(at: masksURL, withIntermediateDirectories: true)
let context = CIContext()

func personMask(for image: CIImage) throws -> CIImage {
    let request = VNGeneratePersonSegmentationRequest()
    request.qualityLevel = .accurate
    request.outputPixelFormat = kCVPixelFormatType_OneComponent8
    try VNImageRequestHandler(ciImage: image).perform([request])
    let mask = CIImage(cvPixelBuffer: request.results!.first!.pixelBuffer)
    let scaleX = image.extent.width / mask.extent.width
    let scaleY = image.extent.height / mask.extent.height
    return mask.transformed(by: CGAffineTransform(scaleX: scaleX, y: scaleY))
}

let frames = try FileManager.default.contentsOfDirectory(at: framesURL, includingPropertiesForKeys: nil)
    .filter { $0.pathExtension == "png" }
    .sorted { $0.lastPathComponent < $1.lastPathComponent }

for frame in frames {
    guard let image = CIImage(contentsOf: frame) else { continue }
    let mask = try personMask(for: image)
    let target = masksURL.appendingPathComponent(frame.lastPathComponent)
    try context.writePNGRepresentation(of: mask, to: target, format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
}
print("masked \(frames.count) frames")
