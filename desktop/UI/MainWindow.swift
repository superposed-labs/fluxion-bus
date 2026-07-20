import AppKit
import Foundation
import WebKit

// Console-only colors mirror the Web console's dark neutral theme without
// changing the system-adaptive palette used by Preferences and the menu bar.
private enum ConsolePalette {
    static let windowBackground = NSColor(hex: "#101214")
    static let chromeBackground = NSColor(hex: "#15181B")
    static let separator = NSColor(hex: "#272D33")
    static let primaryText = NSColor(hex: "#E6E8EB")
    static let secondaryText = NSColor(hex: "#8F969F")
    static let icon = NSColor(hex: "#8F969F")
    static let iconHover = NSColor(hex: "#E6E8EB")
    static let buttonHoverBackground = NSColor(hex: "#FFFFFF14")
}

class MainWindow: NSObject, NSWindowDelegate, WKNavigationDelegate {
    static let shared = MainWindow()

    var window: NSWindow?
    var webView: WKWebView?
    var loadingView: NSView?
    var loadingLabel: NSTextField?
    var spinner: NSProgressIndicator?
    var retryButton: NSButton?
    
    // Status dot views
    var webStatusDot: NSView?
    var botStatusDot: NSView?
    var schedStatusDot: NSView?
    
    private var timer: Timer?
    private var connectionRetry: DispatchWorkItem?
    private var connectionAttempt = 0
    private let maxConnectionAttempts = 30
    // Console view to land on (?view= query param), set per show() call and
    // kept across connection retries so a retry doesn't lose the deep link.
    private var initialView: String?

    private var appDelegate: AppDelegate {
        return NSApp.delegate as! AppDelegate
    }

    /// Show the console window. `view` deep-links to a console page (e.g.
    /// "stats" for the usage view) — nil keeps the console's default.
    func show(view: String? = nil) {
        initialView = view
        if let win = window {
            win.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            startTimer()
            checkConnectionAndLoad()
            return
        }

        // Create the window
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1200, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = L10n.tr("console.title")
        win.titlebarAppearsTransparent = true
        win.titleVisibility = .hidden
        win.appearance = NSAppearance(named: .darkAqua)
        win.backgroundColor = ConsolePalette.windowBackground
        win.delegate = self
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 800, height: 600)
        self.window = win

        // Setup Main Layout Stack
        let mainStack = NSStackView()
        mainStack.orientation = .vertical
        mainStack.alignment = .centerX
        mainStack.spacing = 0
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        win.contentView?.addSubview(mainStack)

        if let contentView = win.contentView {
            NSLayoutConstraint.activate([
                mainStack.topAnchor.constraint(equalTo: contentView.topAnchor),
                mainStack.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
                mainStack.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
                mainStack.trailingAnchor.constraint(equalTo: contentView.trailingAnchor)
            ])
        }

        // 1. Setup Chrome Header Bar
        let chrome = NSView()
        chrome.wantsLayer = true
        chrome.layer?.backgroundColor = ConsolePalette.chromeBackground.cgColor
        chrome.translatesAutoresizingMaskIntoConstraints = false
        chrome.heightAnchor.constraint(equalToConstant: 50).isActive = true
        mainStack.addArrangedSubview(chrome)

        NSLayoutConstraint.activate([
            chrome.leadingAnchor.constraint(equalTo: mainStack.leadingAnchor),
            chrome.trailingAnchor.constraint(equalTo: mainStack.trailingAnchor)
        ])

        // Status Indicators Middle Stack
        let middleStack = NSStackView()
        middleStack.orientation = .horizontal
        middleStack.spacing = 16
        middleStack.alignment = .centerY
        middleStack.translatesAutoresizingMaskIntoConstraints = false
        chrome.addSubview(middleStack)

        let (webDot, webIndicator) = createIndicator(label: L10n.tr("console.web_console"))
        self.webStatusDot = webDot
        middleStack.addArrangedSubview(webIndicator)

        let (botDot, botIndicator) = createIndicator(label: L10n.tr("console.messaging_gateway"))
        self.botStatusDot = botDot
        middleStack.addArrangedSubview(botIndicator)

        let (schedDot, schedIndicator) = createIndicator(label: L10n.tr("console.scheduler"))
        self.schedStatusDot = schedDot
        middleStack.addArrangedSubview(schedIndicator)

        // Actions Right Stack
        let rightStack = NSStackView()
        rightStack.orientation = .horizontal
        rightStack.spacing = 4
        rightStack.alignment = .centerY
        rightStack.translatesAutoresizingMaskIntoConstraints = false
        chrome.addSubview(rightStack)

        let reloadBtn = createIconButton(symbol: "arrow.clockwise", tooltip: L10n.tr("console.reload_console"), action: #selector(reloadWebview))
        rightStack.addArrangedSubview(reloadBtn)

        let browserBtn = createIconButton(symbol: "arrow.up.right.square", tooltip: L10n.tr("console.open_in_browser"), action: #selector(openConsoleInBrowser))
        rightStack.addArrangedSubview(browserBtn)

        let prefsBtn = createIconButton(symbol: "slider.horizontal.3", tooltip: L10n.tr("console.open_preferences"), action: #selector(openPreferences))
        rightStack.addArrangedSubview(prefsBtn)

        let restartBtn = createIconButton(symbol: "arrow.triangle.2.circlepath", tooltip: L10n.tr("console.restart_services"), action: #selector(restartServices))
        rightStack.addArrangedSubview(restartBtn)

        // Layout constraints for chrome items
        NSLayoutConstraint.activate([
            middleStack.centerXAnchor.constraint(equalTo: chrome.centerXAnchor),
            middleStack.centerYAnchor.constraint(equalTo: chrome.centerYAnchor),
            
            rightStack.trailingAnchor.constraint(equalTo: chrome.trailingAnchor, constant: -16),
            rightStack.centerYAnchor.constraint(equalTo: chrome.centerYAnchor)
        ])

        // 2. Add Separator Line
        let separator = NSView()
        separator.wantsLayer = true
        separator.layer?.backgroundColor = ConsolePalette.separator.cgColor
        separator.translatesAutoresizingMaskIntoConstraints = false
        separator.heightAnchor.constraint(equalToConstant: 1).isActive = true
        mainStack.addArrangedSubview(separator)

        NSLayoutConstraint.activate([
            separator.leadingAnchor.constraint(equalTo: mainStack.leadingAnchor),
            separator.trailingAnchor.constraint(equalTo: mainStack.trailingAnchor)
        ])

        // 3. Body View Container
        let bodyContainer = NSView()
        bodyContainer.wantsLayer = true
        bodyContainer.layer?.backgroundColor = ConsolePalette.windowBackground.cgColor
        bodyContainer.translatesAutoresizingMaskIntoConstraints = false
        mainStack.addArrangedSubview(bodyContainer)

        NSLayoutConstraint.activate([
            bodyContainer.leadingAnchor.constraint(equalTo: mainStack.leadingAnchor),
            bodyContainer.trailingAnchor.constraint(equalTo: mainStack.trailingAnchor),
            bodyContainer.bottomAnchor.constraint(equalTo: mainStack.bottomAnchor)
        ])

        // Setup WebKit WebView
        let webConf = WKWebViewConfiguration()
        let web = WKWebView(frame: .zero, configuration: webConf)
        web.navigationDelegate = self
        web.translatesAutoresizingMaskIntoConstraints = false
        web.isHidden = true
        bodyContainer.addSubview(web)
        self.webView = web

        // Setup Native Loading / Offline View
        let loader = NSView()
        loader.wantsLayer = true
        loader.layer?.backgroundColor = ConsolePalette.windowBackground.cgColor
        loader.translatesAutoresizingMaskIntoConstraints = false
        bodyContainer.addSubview(loader)
        self.loadingView = loader

        let loadStack = NSStackView()
        loadStack.orientation = .vertical
        loadStack.spacing = 16
        loadStack.alignment = .centerX
        loadStack.translatesAutoresizingMaskIntoConstraints = false
        loader.addSubview(loadStack)

        let spin = NSProgressIndicator()
        spin.style = .spinning
        spin.controlSize = .large
        spin.translatesAutoresizingMaskIntoConstraints = false
        spin.startAnimation(nil)
        loadStack.addArrangedSubview(spin)
        self.spinner = spin

        let text = NSTextField(labelWithString: L10n.tr("console.connecting"))
        text.font = NSFont.systemFont(ofSize: 13)
        text.textColor = ConsolePalette.secondaryText
        text.alignment = .center
        loadStack.addArrangedSubview(text)
        self.loadingLabel = text

        let btnStack = NSStackView()
        btnStack.orientation = .horizontal
        btnStack.spacing = 12
        btnStack.alignment = .centerY
        btnStack.translatesAutoresizingMaskIntoConstraints = false
        loadStack.addArrangedSubview(btnStack)

        let retry = TxtButton()
        retry.title = L10n.tr("console.retry_connection")
        retry.target = self
        retry.action = #selector(retryConnection)
        btnStack.addArrangedSubview(retry)
        self.retryButton = retry

        let forceStart = TxtButton()
        forceStart.title = L10n.tr("console.start_services")
        forceStart.target = self
        forceStart.action = #selector(forceStartServices)
        btnStack.addArrangedSubview(forceStart)

        // Body constraints
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: bodyContainer.topAnchor),
            web.bottomAnchor.constraint(equalTo: bodyContainer.bottomAnchor),
            web.leadingAnchor.constraint(equalTo: bodyContainer.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: bodyContainer.trailingAnchor),

            loader.topAnchor.constraint(equalTo: bodyContainer.topAnchor),
            loader.bottomAnchor.constraint(equalTo: bodyContainer.bottomAnchor),
            loader.leadingAnchor.constraint(equalTo: bodyContainer.leadingAnchor),
            loader.trailingAnchor.constraint(equalTo: bodyContainer.trailingAnchor),

            loadStack.centerXAnchor.constraint(equalTo: loader.centerXAnchor),
            loadStack.centerYAnchor.constraint(equalTo: loader.centerYAnchor)
        ])

        // Show window and trigger refresh
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        
        startTimer()
        checkConnectionAndLoad()
    }

    // MARK: - WKNavigationDelegate
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        connectionRetry?.cancel()
        connectionRetry = nil
        connectionAttempt = 0
        webView.isHidden = false
        loadingView?.isHidden = true
        spinner?.stopAnimation(nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showOfflineState(message: L10n.tr("console.unable_connect", error.localizedDescription))
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showOfflineState(message: L10n.tr("console.connection_lost", error.localizedDescription))
    }

    // MARK: - Actions & Logic
    private func checkConnectionAndLoad() {
        guard let web = webView else { return }
        connectionRetry?.cancel()
        connectionRetry = nil
        webView?.isHidden = true
        loadingView?.isHidden = false
        spinner?.startAnimation(nil)
        loadingLabel?.stringValue = L10n.tr("console.connecting")
        
        let port = appDelegate.envVals["FLUXION_UI_PORT"] ?? "8765"
        let langCode = L10n.pythonLocale
        let viewParam = initialView.map { "&view=\($0)" } ?? ""
        let tokenParam = consoleTokenQueryParam()
        if let url = URL(string: "http://127.0.0.1:\(port)/?lang=\(langCode)\(viewParam)\(tokenParam)") {
            web.load(URLRequest(url: url))
        }
    }

    private func showOfflineState(message: String) {
        webView?.isHidden = true
        loadingView?.isHidden = false
        guard connectionAttempt < maxConnectionAttempts else {
            spinner?.stopAnimation(nil)
            loadingLabel?.stringValue = message
            return
        }

        connectionAttempt += 1
        loadingLabel?.stringValue = L10n.tr("console.waiting_service", connectionAttempt, maxConnectionAttempts)
        let retry = DispatchWorkItem { [weak self] in
            self?.checkConnectionAndLoad()
        }
        connectionRetry = retry
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0, execute: retry)
    }

    @objc func reloadWebview() {
        connectionAttempt = 0
        checkConnectionAndLoad()
    }

    @objc func retryConnection() {
        connectionAttempt = 0
        checkConnectionAndLoad()
    }

    @objc func openConsoleInBrowser() {
        let port = appDelegate.envVals["FLUXION_UI_PORT"] ?? "8765"
        let langCode = L10n.pythonLocale
        let tokenParam = consoleTokenQueryParam()
        guard let url = URL(string: "http://127.0.0.1:\(port)/?lang=\(langCode)\(tokenParam)") else { return }
        NSWorkspace.shared.open(url)
    }

    /// FLUXION_UI_TOKEN as a `&token=` suffix for the web console URL — the
    /// gateway's auth gate rejects /api/* once the token is set, and the
    /// WKWebView has no way to attach an Authorization header on page load,
    /// so the frontend picks it up from this query param instead (see
    /// `initTokenFromLocation` in web/src/main.tsx).
    private func consoleTokenQueryParam() -> String {
        guard let token = appDelegate.envVals["FLUXION_UI_TOKEN"], !token.isEmpty,
              let encoded = token.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)
        else { return "" }
        return "&token=\(encoded)"
    }

    @objc func forceStartServices() {
        loadingLabel?.stringValue = L10n.tr("console.starting_services")
        spinner?.startAnimation(nil)
        appDelegate.startServicesIfNeeded()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            self?.checkConnectionAndLoad()
        }
    }

    @objc func openPreferences() {
        PreferencesWindow.shared.show()
    }

    @objc func restartServices() {
        loadingLabel?.stringValue = L10n.tr("console.restarting_services")
        spinner?.startAnimation(nil)
        webView?.isHidden = true
        loadingView?.isHidden = false
        
        appDelegate.restartServices()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            self?.checkConnectionAndLoad()
        }
    }

    // MARK: - NSWindowDelegate
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        connectionRetry?.cancel()
        connectionRetry = nil
        stopTimer()
        return false
    }

    func windowDidBecomeKey(_ notification: Notification) {
        startTimer()
        updateStatusLights()
    }

    // MARK: - Timer & Status lights
    private func startTimer() {
        timer?.invalidate()
        let t = Timer(timeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.updateStatusLights()
        }
        RunLoop.main.add(t, forMode: .common)
        self.timer = t
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    @objc func updateStatusLights() {
        updateDot(webStatusDot, running: appDelegate.isPortListening(appDelegate.envVals["FLUXION_UI_PORT"] ?? "8765"))
        updateDot(botStatusDot, running: appDelegate.isProcessRunning(pattern: "fluxion-gateway"))
        updateDot(schedStatusDot, running: appDelegate.isProcessRunning(pattern: "fluxion-scheduler"))
    }

    private func updateDot(_ dot: NSView?, running: Bool) {
        guard let d = dot else { return }
        let color = running ? NSColor(hex: "#30D158") : NSColor(hex: "#FF453A") // iOS systemGreen / systemRed equivalent
        d.layer?.backgroundColor = color.cgColor
    }

    // MARK: - View Builders Helper
    private func createIndicator(label: String) -> (NSView, NSView) {
        let container = NSStackView()
        container.orientation = .horizontal
        container.spacing = 6
        container.alignment = .centerY

        let dot = NSView()
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 5
        dot.layer?.backgroundColor = NSColor(hex: "#FF453A").cgColor // Red default
        dot.translatesAutoresizingMaskIntoConstraints = false
        dot.widthAnchor.constraint(equalToConstant: 10).isActive = true
        dot.heightAnchor.constraint(equalToConstant: 10).isActive = true
        container.addArrangedSubview(dot)

        let tf = NSTextField(labelWithString: label)
        tf.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        tf.textColor = ConsolePalette.primaryText
        container.addArrangedSubview(tf)

        return (dot, container)
    }

    private func createIconButton(symbol: String, tooltip: String, action: Selector) -> NSButton {
        let btn = IconButton()
        btn.target = self
        btn.action = action
        btn.toolTip = tooltip
        btn.translatesAutoresizingMaskIntoConstraints = false
        btn.widthAnchor.constraint(equalToConstant: 32).isActive = true
        btn.heightAnchor.constraint(equalToConstant: 32).isActive = true
        
        if let img = NSImage(systemSymbolName: symbol, accessibilityDescription: tooltip) {
            img.isTemplate = true
            btn.image = img
        }
        return btn
    }
}

// MARK: - Custom IconButton
class IconButton: NSButton {
    private var trackingArea: NSTrackingArea?
    private var isHovered = false

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        isBordered = false
        title = ""
        bezelStyle = .regularSquare
        wantsLayer = true
        layer?.cornerRadius = 6
        layer?.masksToBounds = true
        contentTintColor = ConsolePalette.icon
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func updateTrackingAreas() {
        if let trackingArea = trackingArea {
            removeTrackingArea(trackingArea)
        }
        let options: NSTrackingArea.Options = [.mouseEnteredAndExited, .activeAlways]
        trackingArea = NSTrackingArea(rect: bounds, options: options, owner: self, userInfo: nil)
        addTrackingArea(trackingArea!)
        super.updateTrackingAreas()
    }

    override func mouseEntered(with event: NSEvent) {
        isHovered = true
        contentTintColor = ConsolePalette.iconHover
        needsDisplay = true
    }

    override func mouseExited(with event: NSEvent) {
        isHovered = false
        contentTintColor = ConsolePalette.icon
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        if isHovered {
            ConsolePalette.buttonHoverBackground.setFill()
            let path = NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6)
            path.fill()
        }
        super.draw(dirtyRect)
    }
}
