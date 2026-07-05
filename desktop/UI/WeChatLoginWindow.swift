import AppKit
import Foundation
import CoreImage

// MARK: - WeChat QR Login Subprocess Manager and Native UI Window
class WeChatLoginWindowController: NSObject, NSWindowDelegate {
    var window: NSWindow?
    var imageView: NSImageView?
    var statusLabel: NSTextField?
    var cancelButton: NSButton?
    var spinner: NSProgressIndicator?
    var process: Process?
    var outputPipe: Pipe?

    let repoPath: String
    let envPath: String
    let pythonBin: String

    init(repoPath: String, envPath: String, pythonBin: String) {
        self.repoPath = repoPath
        self.envPath = envPath
        self.pythonBin = pythonBin
        super.init()
    }

    func show() {
        if window != nil {
            window?.makeKeyAndOrderFront(nil)
            return
        }

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 350),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = L10n.tr("wechat.login.title")
        win.delegate = self
        win.isReleasedWhenClosed = false
        self.window = win

        // Main stack
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        win.contentView?.addSubview(stack)

        if let cv = win.contentView {
            NSLayoutConstraint.activate([
                stack.topAnchor.constraint(equalTo: cv.topAnchor, constant: 24),
                stack.bottomAnchor.constraint(equalTo: cv.bottomAnchor, constant: -24),
                stack.leadingAnchor.constraint(equalTo: cv.leadingAnchor, constant: 24),
                stack.trailingAnchor.constraint(equalTo: cv.trailingAnchor, constant: -24)
            ])
        }

        // 1. ImageView for QR code
        let imgView = NSImageView()
        imgView.imageAlignment = .alignCenter
        imgView.imageScaling = .scaleProportionallyUpOrDown
        imgView.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            imgView.widthAnchor.constraint(equalToConstant: 220),
            imgView.heightAnchor.constraint(equalToConstant: 220)
        ])
        imgView.wantsLayer = true
        imgView.layer?.cornerRadius = 8
        imgView.layer?.borderWidth = 1
        imgView.layer?.borderColor = NSColor.separatorColor.cgColor
        self.imageView = imgView
        stack.addArrangedSubview(imgView)

        // 2. Spinner
        let spin = NSProgressIndicator()
        spin.style = .spinning
        spin.controlSize = .regular
        spin.isDisplayedWhenStopped = false
        spin.translatesAutoresizingMaskIntoConstraints = false
        self.spinner = spin
        imgView.addSubview(spin)
        NSLayoutConstraint.activate([
            spin.centerXAnchor.constraint(equalTo: imgView.centerXAnchor),
            spin.centerYAnchor.constraint(equalTo: imgView.centerYAnchor)
        ])
        spin.startAnimation(nil)

        // 3. Status Label
        let lbl = NSTextField(labelWithString: L10n.tr("wechat.login.requesting"))
        lbl.alignment = .center
        lbl.font = NSFont.systemFont(ofSize: 12)
        lbl.textColor = .labelColor
        lbl.translatesAutoresizingMaskIntoConstraints = false
        lbl.preferredMaxLayoutWidth = 260
        lbl.lineBreakMode = .byWordWrapping
        self.statusLabel = lbl
        stack.addArrangedSubview(lbl)

        let buttonSpacer = NSView()
        buttonSpacer.translatesAutoresizingMaskIntoConstraints = false
        buttonSpacer.heightAnchor.constraint(equalToConstant: 6).isActive = true
        stack.addArrangedSubview(buttonSpacer)

        // 4. Cancel Button
        let btn = NSButton(title: L10n.tr("wechat.login.cancel"), target: self, action: #selector(cancelClicked))
        btn.bezelStyle = .rounded
        btn.translatesAutoresizingMaskIntoConstraints = false
        self.cancelButton = btn
        stack.addArrangedSubview(btn)

        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        startLoginProcess()
    }

    @objc func cancelClicked() {
        cleanup()
        window?.close()
    }

    func windowWillClose(_ notification: Notification) {
        cleanup()
    }

    private func cleanup() {
        if let proc = process, proc.isRunning {
            proc.terminate()
        }
        process = nil
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
    }

    private func startLoginProcess() {
        let proc = Process()
        self.process = proc

        let pipe = Pipe()
        self.outputPipe = pipe
        proc.standardOutput = pipe

        proc.executableURL = URL(fileURLWithPath: pythonBin)
        proc.arguments = [
            "-m", "fluxion.channels.wechat.wechat_login",
            "--json"
        ]
        proc.environment = [
            "FLUXION_ENV_FILE": envPath
        ]
        proc.currentDirectoryURL = URL(fileURLWithPath: repoPath)

        let handle = pipe.fileHandleForReading
        handle.readabilityHandler = { [weak self] h in
            let data = h.availableData
            if data.isEmpty {
                h.readabilityHandler = nil
                return
            }
            if let text = String(data: data, encoding: .utf8) {
                let lines = text.components(separatedBy: .newlines)
                for line in lines {
                    let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                    if trimmed.isEmpty { continue }
                    DispatchQueue.main.async {
                        self?.handleJsonLine(trimmed)
                    }
                }
            }
        }

        do {
            try proc.run()
        } catch {
            showError(L10n.tr("wechat.login.backend_failed", error.localizedDescription))
        }
    }

    private func handleJsonLine(_ line: String) {
        guard let data = line.data(using: .utf8) else { return }
        do {
            if let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               let event = json["event"] as? String {
                switch event {
                case "qrcode":
                    if let url = json["qrcode_url"] as? String {
                        spinner?.stopAnimation(nil)
                        spinner?.isHidden = true
                        showQrCode(url)
                    }
                case "scanned":
                    statusLabel?.stringValue = L10n.tr("wechat.login.scanned")
                case "confirmed":
                    statusLabel?.stringValue = L10n.tr("wechat.login.confirmed")
                    // Wait a bit and close
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                        self?.window?.close()
                    }
                case "failed":
                    let reason = json["reason"] as? String ?? L10n.tr("wechat.login.unknown_error")
                    showError(L10n.tr("wechat.login.failed", reason))
                default:
                    break
                }
            }
        } catch {
            // Ignore non-JSON lines (e.g. print statements or debug logs)
        }
    }

    private func showQrCode(_ urlString: String) {
        guard let data = urlString.data(using: .ascii) else { return }
        if let filter = CIFilter(name: "CIQRCodeGenerator") {
            filter.setValue(data, forKey: "inputMessage")
            filter.setValue("M", forKey: "inputCorrectionLevel")

            if let ciImage = filter.outputImage {
                let scale: CGFloat = 200.0 / ciImage.extent.width
                let transform = CGAffineTransform(scaleX: scale, y: scale)
                let scaledImage = ciImage.transformed(by: transform)

                let rep = NSCIImageRep(ciImage: scaledImage)
                let nsImage = NSImage(size: rep.size)
                nsImage.addRepresentation(rep)

                self.imageView?.image = nsImage
                self.statusLabel?.stringValue = L10n.tr("wechat.login.scan")
            }
        }
    }

    private func showError(_ msg: String) {
        spinner?.stopAnimation(nil)
        spinner?.isHidden = true
        statusLabel?.textColor = .systemRed
        statusLabel?.stringValue = msg
    }
}
