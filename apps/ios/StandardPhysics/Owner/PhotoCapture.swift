import SwiftUI
import UIKit

/// The system camera, for one photo.
///
/// A phone with no camera, which in practice means the simulator, gets the
/// photo library instead so the flow can still be walked through end to end.
struct PhotoCapture: UIViewControllerRepresentable {
    let finish: (UIImage?) -> Void

    static var source: UIImagePickerController.SourceType {
        UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
    }

    func makeCoordinator() -> Coordinator { Coordinator(finish: finish) }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        PhotoCapture.picker(delegate: context.coordinator)
    }

    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}

    static func picker(delegate: UIImagePickerControllerDelegate & UINavigationControllerDelegate) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = source
        picker.mediaTypes = ["public.image"]
        picker.delegate = delegate
        return picker
    }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let finish: (UIImage?) -> Void

        init(finish: @escaping (UIImage?) -> Void) { self.finish = finish }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            finish(info[.originalImage] as? UIImage)
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            finish(nil)
        }
    }
}

/// A photo ready to send: upright, no longer than 2,400 pixels on its long
/// side, as JPEG. A phone photo straight off the sensor can pass the
/// server's 15 MB limit, and a person checking a handle's shape needs far
/// less than that.
enum PhotoEncoding {
    static let longestSide: CGFloat = 2_400

    static func jpeg(from image: UIImage) -> Data? {
        let size = image.size
        let scale = min(1, longestSide / max(size.width, size.height, 1))
        let target = CGSize(width: (size.width * scale).rounded(), height: (size.height * scale).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let resized = UIGraphicsImageRenderer(size: target, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
        return resized.jpegData(compressionQuality: 0.8)
    }
}
