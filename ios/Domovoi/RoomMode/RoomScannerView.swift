import AVFoundation
import SwiftUI
import UIKit

/// The room code's scanner (`roommode/RoomScanner.kt`, ROOMMODE-MAP D12):
/// the camera's preview and Apple's own QR reader,
/// `AVCaptureMetadataOutput` with `.qr` — no dependency, and lighter than
/// VisionKit's scanner on an old phone. Hands back the first code's text
/// once; nothing is stored, and the camera closes with the view. The caller
/// holds the camera permission.
struct RoomScannerView: UIViewControllerRepresentable {
    let onCode: (String) -> Void

    func makeUIViewController(context: Context) -> RoomScannerController {
        let controller = RoomScannerController()
        controller.onCode = onCode
        return controller
    }

    func updateUIViewController(_ controller: RoomScannerController, context: Context) {
        controller.onCode = onCode
    }

    static func dismantleUIViewController(_ controller: RoomScannerController, coordinator: ()) {
        controller.stop()
    }
}

/// `AVCaptureSession` is not `Sendable`; its start and stop block, so they
/// run on one serial queue, and only there.
private final class RoomCaptureBox: @unchecked Sendable {
    let session = AVCaptureSession()
    let queue = DispatchQueue(label: "org.codersfarm.domovoi.room-scanner")
}

final class RoomScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onCode: ((String) -> Void)?
    private let box = RoomCaptureBox()
    private var preview: AVCaptureVideoPreviewLayer?
    private var done = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        let session = box.session
        guard let camera = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: camera),
              session.canAddInput(input)
        else { return }
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        if output.availableMetadataObjectTypes.contains(.qr) {
            output.metadataObjectTypes = [.qr]
        }
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        preview = layer
        let box = self.box
        box.queue.async { box.session.startRunning() }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview?.frame = view.bounds
    }

    func stop() {
        let box = self.box
        box.queue.async { if box.session.isRunning { box.session.stopRunning() } }
    }

    nonisolated func metadataOutput(
        _ output: AVCaptureMetadataOutput,
        didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        let text = metadataObjects
            .compactMap { ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }
            .first
        guard let text else { return }
        // The delegate queue is `.main` (set above).
        MainActor.assumeIsolated {
            guard !done else { return }
            done = true
            stop()
            onCode?(text)
        }
    }
}
