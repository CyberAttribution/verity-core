import AppKit
import WebKit

// Verity — development macOS shell for the FastAPI Node UI.
// macOS 11+ (WKDownloadDelegate requires macOS 11).

let startupTimeoutSeconds: TimeInterval = 30

struct DesktopConfig: Codable {
    let python: String
    let worker: String
    let repo: String
    let library_path: String?
    let bundled: Bool?
}

func loadConfig() -> DesktopConfig? {
    guard let exeURL = Bundle.main.executableURL else { return nil }
    let bundleURL = exeURL.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let configURL = bundleURL.appendingPathComponent("Contents/Resources/desktop-config.json")
    guard let data = try? Data(contentsOf: configURL) else { return nil }
    guard let cfg = try? JSONDecoder().decode(DesktopConfig.self, from: data) else { return nil }
    if cfg.bundled == true {
        let resources = bundleURL.appendingPathComponent("Contents/Resources")
        return DesktopConfig(python: resources.appendingPathComponent(cfg.python).path,
            worker: resources.appendingPathComponent(cfg.worker).path,
            repo: resources.path, library_path: nil, bundled: true)
    }
    return cfg
}

func appSupportLogURL() -> URL {
    let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
    let dir = base.appendingPathComponent("Verity", isDirectory: true)
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir.appendingPathComponent("worker-stderr.log")
}

class StartupErrorWindow {
    static func show(title: String, message: String) {
        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.alertStyle = .critical
            alert.messageText = title
            alert.informativeText = message
            alert.addButton(withTitle: "Quit")
            alert.runModal()
            NSApp.terminate(nil)
        }
    }
}

class WorkerProcess {
    let nonce = UUID().uuidString  // Readiness correlation, not human authorization.
    private var process: Process?
    private let queue = DispatchQueue(label: "ai.swarmtracker.verity.worker")
    private var buffer = Data()
    private var gotStartupLine = false
    var onExit: ((Int32) -> Void)?
    var onStartupLine: ((String) -> Void)?

    func launch(config: DesktopConfig) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: config.python)
        p.arguments = config.bundled == true ? ["--bundled"] : [config.worker, "--repo", config.repo]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (env["PATH"] ?? "")
        if let library = config.library_path, !library.isEmpty { env["DYLD_LIBRARY_PATH"] = library }
        env["PYTHONUNBUFFERED"] = "1"
        env["VERITY_DESKTOP_NONCE"] = nonce
        p.environment = env
        let out = Pipe(), err = Pipe()
        p.standardOutput = out; p.standardError = err
        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            self?.queue.async { [weak self] in
                guard let self = self, !self.gotStartupLine else { return }
                self.buffer.append(data)
                if self.buffer.count > 8192 {
                    self.gotStartupLine = true
                    DispatchQueue.main.async { self.onStartupLine?("invalid readiness") }
                    return
                }
                while let end = self.buffer.firstIndex(of: 10) {
                    let line = self.buffer.prefix(upTo: end)
                    self.buffer.removeSubrange(...end)
                    if line.isEmpty { continue }
                    let text = String(data: line, encoding: .utf8) ?? ""
                    guard StartupURL.parse(text, expectedNonce: self.nonce) != nil else { continue }
                    self.gotStartupLine = true
                    DispatchQueue.main.async { self.onStartupLine?(text) }
                    break
                }
            }
        }
        err.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
            // Consume stderr without storing SDK/evidence/credential payloads.
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.onExit?(proc.terminationStatus) }
        }
        do { try p.run(); process = p; return true }
        catch {
            StartupErrorWindow.show(title: "Verity could not start",
                message: "Verify the Python environment and rebuild the development app. No provider call was made.")
            return false
        }
    }

    func stop() {
        if let p = process, p.isRunning { p.terminate() }
    }

    func stopAndWait(_ completion: @escaping () -> Void) {
        guard let p = process, p.isRunning else { DispatchQueue.main.async(execute: completion); return }
        p.terminate()
        DispatchQueue.global().async {
            let limit = Date().addingTimeInterval(5)
            while p.isRunning && Date() < limit { Thread.sleep(forTimeInterval: 0.05) }
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            p.waitUntilExit()
            DispatchQueue.main.async { completion() }
        }
    }
}

struct StartupURL {
    static func parse(_ line: String, expectedNonce: String) -> URL? {
        guard let data = line.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let urlStr = obj["url"] as? String,
              obj["nonce"] as? String == expectedNonce else { return nil }
        guard let url = URL(string: urlStr) else { return nil }
        // Validate readiness contract: loopback, http, explicit port.
        guard url.scheme == "http",
              url.host == "127.0.0.1",
              let port = url.port, (1...65535).contains(port),
              url.user == nil, url.password == nil, url.query == nil,
              url.fragment == nil, url.path.isEmpty || url.path == "/" else { return nil }
        return url
    }
}

func sameOrigin(_ a: URL, _ b: URL) -> Bool {
    return a.scheme == b.scheme && a.host == b.host && a.port == b.port
}

class VerityAppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKDownloadDelegate, WKUIDelegate {
    var window: NSWindow?
    var webView: WKWebView?
    let worker = WorkerProcess()
    var initialOrigin: URL?
    var startupTimer: Timer?
    var startupComplete = false
    var terminating = false
    var pendingExternalURL: URL?
    var acceptanceTimer: Timer?
    var connectionSmokeSubmitted = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let config = loadConfig() else {
            StartupErrorWindow.show(title: "Verity configuration missing",
                message: "desktop-config.json was not found or is invalid in the app bundle Resources. Rebuild the app with build.py.")
            return
        }
        guard FileManager.default.fileExists(atPath: config.python),
              FileManager.default.fileExists(atPath: config.worker),
              FileManager.default.fileExists(atPath: config.repo) else {
            StartupErrorWindow.show(title: "Verity configuration invalid",
                message: "One of the configured python, worker, or repo paths does not exist. Rebuild with build.py using absolute paths.")
            return
        }

        buildWindow()
        showLoadingPage()

        worker.onStartupLine = { [weak self] line in
            guard let self = self else { return }
            guard let url = StartupURL.parse(line, expectedNonce: self.worker.nonce) else {
                self.failStartup(message: "The worker emitted an invalid startup line. Readiness could not be validated.")
                return
            }
            self.initialOrigin = url
            self.startupComplete = true
            self.startupTimer?.invalidate()
            let smokePages = ["--smoke-connection-test": "setup", "--smoke-governance": "governance",
                              "--smoke-agents": "agents", "--smoke-tasks": "tasks",
                              "--smoke-policy": "review-policy", "--smoke-notifications": "notifications",
                              "--smoke-demo": "demo", "--smoke-registry": "setup/registry"]
            let workflow = ProcessInfo.processInfo.environment["VERITY_SMOKE_WORKFLOW"] ?? ""
            let selected = (CommandLine.arguments.contains("--smoke-workflow") && workflow.hasPrefix("demo") && workflow.range(of: "^[A-Za-z0-9]+$", options: .regularExpression) != nil)
                ? "workflows/" + workflow : smokePages.first { CommandLine.arguments.contains($0.key) }?.value
            let destination = selected.map { url.appendingPathComponent($0) } ?? url
            self.webView?.load(URLRequest(url: destination))
        }
        worker.onExit = { [weak self] code in
            guard let self = self, !self.terminating else { return }
            if !self.startupComplete {
                self.failStartup(message: "The local worker exited before becoming ready (exit code \(code)). Check the repository, Python environment, and the configured runtime. Raw SDK diagnostics are not stored by the launcher.")
            } else {
                self.showBackendCrash()
            }
        }

        if worker.launch(config: config) {
            startupTimer = Timer.scheduledTimer(withTimeInterval: startupTimeoutSeconds, repeats: false) { [weak self] _ in
                guard let self = self, !self.startupComplete else { return }
                self.failStartup(message: "Timed out after 30 seconds waiting for the local worker to report readiness.")
            }
        }
    }

    func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1200, height: 850)
        let win = NSWindow(contentRect: rect,
                           styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: false)
        win.title = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? "Verity"
        win.center()

        let config = WKWebViewConfiguration()
        // No native command bridge is exposed to page content.
        let wv = WKWebView(frame: rect, configuration: config)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        win.contentView = wv
        self.webView = wv
        self.window = win
        win.makeKeyAndOrderFront(nil)
    }

    func showLoadingPage() {
        let html = "<!html><body style='font-family:-apple-system;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'><div style='text-align:center'><h1>Verity</h1><p>Starting local worker…</p></div></body></html>"
        webView?.loadHTMLString(html, baseURL: nil)
    }

    func failStartup(message: String) {
        guard !startupComplete else { return }
        startupTimer?.invalidate()
        worker.stop()
        StartupErrorWindow.show(title: "Verity startup failed", message: message)
    }

    func showBackendCrash() {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "The local backend stopped"
        alert.informativeText = "The Verity worker process exited unexpectedly. In-flight work may be lost. Restart the app; the launcher does not record raw SDK or evidence diagnostics."
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        NSApp.terminate(nil)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard CommandLine.arguments.contains("--smoke-test"),
              initialOrigin != nil else { return }
        if CommandLine.arguments.contains("--smoke-connection-test") {
            if !connectionSmokeSubmitted {
                connectionSmokeSubmitted = true
                webView.evaluateJavaScript("document.querySelector('input[value=test_connection]').form.requestSubmit()") { [weak self] _, error in
                    if error != nil { self?.failStartup(message: "Connection test form could not be submitted.") }
                }
                return
            }
            webView.evaluateJavaScript("document.querySelector('[role=status]')?.scrollIntoView()", completionHandler: nil)
        }
        webView.evaluateJavaScript("document.title") { [weak self] value, error in
            guard let self = self else { return }
            let support = ProcessInfo.processInfo.environment["VERITY_SMOKE_DIR"].map { URL(fileURLWithPath: $0) } ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!.appendingPathComponent("Verity", isDirectory: true)
            try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            let record: [String: Any] = ["ui_title": value as? String ?? "",
                "backend_url": self.initialOrigin?.absoluteString ?? "",
                "javascript_ok": error == nil]
            if let data = try? JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted]) {
                try? data.write(to: support.appendingPathComponent("desktop-smoke.json"))
            }
            // didFinish can precede WebKit's first painted stylesheet frame.
            // This delay is confined to smoke capture, not normal navigation.
            DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(250)) { [weak self] in
                self?.webView?.takeSnapshot(with: nil) { image, _ in
                    if let tiff = image?.tiffRepresentation,
                       let bitmap = NSBitmapImageRep(data: tiff),
                       let data = bitmap.representation(using: .png, properties: [:]) {
                        try? data.write(to: support.appendingPathComponent("desktop-smoke.png"))
                    }
                    if !CommandLine.arguments.contains("--smoke-hold") { NSApp.terminate(nil) }
                }
            }
            if CommandLine.arguments.contains("--smoke-hold"), self.acceptanceTimer == nil {
                self.acceptanceTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
                    guard let self = self else { return }
                    if FileManager.default.fileExists(atPath: support.appendingPathComponent("quit").path) {
                        NSApp.terminate(nil); return
                    }
                    let nav = support.appendingPathComponent("navigate")
                    if let path = try? String(contentsOf: nav, encoding: .utf8) {
                        try? FileManager.default.removeItem(at: nav)
                        if path == "/agents" || path == "/governance" || path == "/setup" || path == "/workflows" || path.hasPrefix("/workflows/") || path.hasPrefix("/decisions/") {
                            if let origin = self.initialOrigin, let url = URL(string: path, relativeTo: origin), sameOrigin(url.absoluteURL, origin) {
                                self.webView?.load(URLRequest(url: url.absoluteURL))
                            }
                        }
                    }
                }
            }
        }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.begin { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    // MARK: Navigation policy — restrict to exact initial origin

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 preferences: WKWebpagePreferences, decisionHandler: @escaping (WKNavigationActionPolicy, WKWebpagePreferences) -> Void) {
        let completionHandler = decisionHandler
        guard let target = navigationAction.request.url else {
            completionHandler(.cancel, preferences); return
        }
        if target.absoluteString == "about:blank" {
            completionHandler(.allow, preferences); return
        }
        if let origin = initialOrigin, sameOrigin(target, origin) {
            completionHandler(navigationAction.shouldPerformDownload ? .download : .allow, preferences); return
        }
        if navigationAction.targetFrame == nil, navigationAction.navigationType == .linkActivated {
            // target=_blank: same-origin opens inside this window; others handled below.
            if let origin = initialOrigin, sameOrigin(target, origin) {
                completionHandler(.allow, preferences); return
            }
        }
        if (target.scheme == "http" || target.scheme == "https") && navigationAction.navigationType == .linkActivated {
            confirmExternalOpen(target)
            completionHandler(.cancel, preferences); return
        }
        completionHandler(.cancel, preferences)
    }

    func confirmExternalOpen(_ url: URL) {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Open external link?"
        alert.informativeText = "Verity will open this link in your default browser:\n\(url.absoluteString)"
        alert.addButton(withTitle: "Open")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: target=_blank handling

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let target = navigationAction.request.url else { return nil }
        if let origin = initialOrigin, sameOrigin(target, origin) {
            webView.load(URLRequest(url: target))
            return nil
        }
        if navigationAction.navigationType == .linkActivated &&
            (target.scheme == "http" || target.scheme == "https") {
            confirmExternalOpen(target)
        }
        return nil
    }

    // MARK: Downloads (export links)

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let target = navigationResponse.response.url, let origin = initialOrigin,
              sameOrigin(target, origin) else { decisionHandler(.cancel); return }
        let disposition = (navigationResponse.response as? HTTPURLResponse)?
            .value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().contains("attachment") ||
                        !navigationResponse.canShowMIMEType ? .download : .allow)
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        let basename = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        let clean = String(basename.unicodeScalars.filter {
            !CharacterSet.controlCharacters.contains($0) &&
            !(0x202A...0x202E).contains($0.value) && !(0x2066...0x2069).contains($0.value)
        }).prefix(120)
        let ext = URL(fileURLWithPath: String(clean)).pathExtension.lowercased()
        panel.nameFieldStringValue = ["json", "md", "txt", "html", "svg"].contains(ext)
            ? String(clean) : "evidence-report.txt"
        panel.canCreateDirectories = true
        panel.begin { resp in
            if resp == .OK, let url = panel.url {
                completionHandler(url)
            } else {
                completionHandler(nil) // user cancelled — nothing written
            }
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Download failed"
        alert.informativeText = "The export could not be completed: \(error.localizedDescription)"
        alert.runModal()
    }

    func downloadDidFinish(_ download: WKDownload) {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Download complete"
        alert.informativeText = "The export was saved to the location you chose."
        alert.runModal()
    }

    // MARK: Lifecycle

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminating = true
        startupTimer?.invalidate()
        worker.stopAndWait { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        startupTimer?.invalidate()
        worker.stop() // only the owned worker
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

// MARK: Menus

class VerityMenuBuilder {
    static func install(delegate: VerityAppDelegate) {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Verity", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        let quit = appMenu.addItem(withTitle: "Quit Verity", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.keyEquivalentModifierMask = .command
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windowMenu

        NSApp.mainMenu = main
    }
}

// MARK: main

let app = NSApplication.shared
let delegate = VerityAppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
VerityMenuBuilder.install(delegate: delegate)
app.run()
