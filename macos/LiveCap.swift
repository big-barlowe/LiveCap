import AppKit
import ServiceManagement

// The menu bar app. It holds no caption logic: it fetches the speech model on first launch, runs the
// bundled Python server, keeps the Mac awake while that runs, and stops it on quit.

let modelBytesText = "2.4 GB"

enum Status: Equatable {
    case settingUp, starting, running, failed(String)
}

enum UpdateState {
    case idle, checking, downloading(Double), ready(Release)
}

@main
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }

    let fm = FileManager.default
    lazy var resources = Bundle.main.resourcePath!
    lazy var home: String = ProcessInfo.processInfo.environment["LIVECAP_HOME"]
        ?? NSHomeDirectory() + "/Library/Application Support/LiveCap"
    lazy var dataDir = home + "/data"
    lazy var modelDir = dataDir + "/parakeet-tdt-0.6b-v3"
    lazy var settingsFile = home + "/livecap.env"
    lazy var logFile: String = (ProcessInfo.processInfo.environment["LIVECAP_HOME"].map { $0 + "/livecap.log" })
        ?? NSHomeDirectory() + "/Library/Logs/LiveCap/livecap.log"

    var statusItem: NSStatusItem!
    var setupWindow: NSWindow?
    var setupLabel: NSTextField?
    var setupBar: NSProgressIndicator?
    var retryButton: NSButton?
    var setupProcess: Process?
    var server: Process?
    var status = Status.settingUp
    var quitting = false
    var failures = 0
    var awake: NSObjectProtocol?
    var healthTimer: Timer?
    var signalSource: DispatchSourceSignal?
    lazy var updater = Updater(appPath: Bundle.main.bundlePath, workDir: home + "/update",
                               installLog: (logFile as NSString).deletingLastPathComponent + "/update.log")
    var update = UpdateState.idle

    // MARK: Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        signal(SIGTERM, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        source.setEventHandler { [weak self] in self?.quit() }
        source.resume()
        signalSource = source

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let image = NSImage(systemSymbolName: "captions.bubble", accessibilityDescription: "LiveCap")
        image?.isTemplate = true
        statusItem.button?.image = image
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu

        if runningFromDiskImage() {
            activate()
            let alert = NSAlert()
            alert.messageText = "Move LiveCap to Applications first"
            alert.informativeText = "Drag LiveCap into the Applications folder, eject the disk image, then open it from Applications."
            alert.runModal()
            NSApp.terminate(nil)
            return
        }
        for folder in [dataDir, (logFile as NSString).deletingLastPathComponent] {
            try? fm.createDirectory(atPath: folder, withIntermediateDirectories: true)
        }
        writeDefaultSettings()
        updater.removeLeftovers()
        #if UPDATE_TEST
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.checkForUpdates() }
        #endif
        if modelIsPresent() {
            startServer()
        } else {
            showSetupWindow()
            downloadModel()
        }
    }

    func runningFromDiskImage() -> Bool {
        let path = Bundle.main.bundlePath
        return path.hasPrefix("/Volumes/") || path.contains("/AppTranslocation/")
    }

    func modelIsPresent() -> Bool {
        fm.fileExists(atPath: modelDir + "/config.json") && fm.fileExists(atPath: modelDir + "/model.safetensors")
    }

    func writeDefaultSettings() {
        guard !fm.fileExists(atPath: settingsFile) else { return }
        let text = """
        # LiveCap settings. Quit and reopen LiveCap after changing them.
        LIVECAP_INPUT_LANG=en
        # Set to obs to listen for OBS as soon as LiveCap opens
        LIVECAP_SOURCE=
        # Caption languages viewers may choose, such as es,fr. Empty offers them all.
        LIVECAP_LANGUAGES=
        # Caption languages to download when LiveCap opens, such as es,fr
        LIVECAP_PRELOAD=
        # Voice: F1 to F5 or M1 to M5
        LIVECAP_VOICE=M1
        # LIVECAP_ADMIN_PASSWORD=choose-your-own

        """
        try? text.write(toFile: settingsFile, atomically: true, encoding: .utf8)
    }

    func settings() -> [String: String] {
        var values: [String: String] = [:]
        let text = (try? String(contentsOfFile: settingsFile, encoding: .utf8)) ?? ""
        for line in text.split(separator: "\n") where !line.hasPrefix("#") {
            let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            if parts.count == 2 {
                let value = parts[1].trimmingCharacters(in: .whitespaces)
                if !value.isEmpty { values[parts[0].trimmingCharacters(in: .whitespaces)] = value }
            }
        }
        return values
    }

    func pythonEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env.merge(settings()) { _, new in new }
        env["PATH"] = resources + "/bin:/usr/bin:/bin"
        env["PYTHONPATH"] = resources
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["SSL_CERT_FILE"] = resources + "/python/lib/python3.13/site-packages/certifi/cacert.pem"
        env["LIVECAP_DATA"] = dataDir
        env["LIVECAP_FFMPEG"] = resources + "/bin/ffmpeg"
        env["LIVECAP_RECOGNIZER"] = "parakeet"
        env["LIVECAP_PARAKEET_DIR"] = modelDir
        env["LIVECAP_SUPERTONIC_DIR"] = resources + "/supertonic-3"
        env["LIVECAP_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        env["NUMBA_CACHE_DIR"] = home + "/cache"
        env["HF_HOME"] = home + "/huggingface"
        env["HF_HUB_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        return env
    }

    func pythonProcess(_ arguments: [String]) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: resources + "/python/bin/python3")
        process.arguments = arguments
        process.currentDirectoryURL = URL(fileURLWithPath: resources)
        process.environment = pythonEnvironment()
        return process
    }

    // MARK: First launch

    func showSetupWindow() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 440, height: 150),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Setting up LiveCap"
        window.isReleasedWhenClosed = false
        let label = NSTextField(wrappingLabelWithString: "")
        label.frame = NSRect(x: 20, y: 76, width: 400, height: 54)
        let bar = NSProgressIndicator(frame: NSRect(x: 20, y: 50, width: 400, height: 20))
        bar.minValue = 0
        bar.maxValue = 1
        bar.isIndeterminate = false
        let retry = NSButton(title: "Try Again", target: self, action: #selector(retryDownload))
        retry.frame = NSRect(x: 320, y: 12, width: 100, height: 30)
        retry.isHidden = true
        window.contentView?.addSubview(label)
        window.contentView?.addSubview(bar)
        window.contentView?.addSubview(retry)
        setupWindow = window
        setupLabel = label
        setupBar = bar
        retryButton = retry
        window.center()
        activate()
        window.makeKeyAndOrderFront(nil)
    }

    @objc func retryDownload() {
        retryButton?.isHidden = true
        showSetupWindowAgain()
        downloadModel()
    }

    func showSetupWindowAgain() {
        activate()
        setupWindow?.makeKeyAndOrderFront(nil)
    }

    func downloadModel() {
        status = .settingUp
        setupLabel?.stringValue = "Downloading the speech model. This happens once and needs about \(modelBytesText) of space."
        let process = pythonProcess(["-m", "app.setup_models"])
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        var pending = ""
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            pending += text
            let lines = pending.components(separatedBy: "\n")
            pending = lines.last ?? ""
            let complete = Array(lines.dropLast())
            DispatchQueue.main.async {
                complete.forEach { self?.handleSetupLine($0) }
            }
        }
        process.terminationHandler = { [weak self] finished in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                guard let self, !self.quitting else { return }
                if finished.terminationStatus == 0, self.modelIsPresent() {
                    self.startServer()
                    self.showWelcome()
                } else {
                    self.status = .failed("Speech model download failed")
                    self.retryButton?.isHidden = false
                    if self.setupLabel?.stringValue.hasPrefix("Couldn't") != true {
                        self.setupLabel?.stringValue = "Couldn't download the speech model. Check your internet connection and try again."
                    }
                }
            }
        }
        setupProcess = process
        do {
            try process.run()
        } catch {
            setupLabel?.stringValue = "Couldn't start the download: \(error.localizedDescription)"
            retryButton?.isHidden = false
        }
    }

    func handleSetupLine(_ line: String) {
        let parts = line.split(separator: " ")
        if parts.first == "PROGRESS", parts.count == 3, let have = Double(parts[1]), let total = Double(parts[2]) {
            setupBar?.doubleValue = have / total
            setupLabel?.stringValue = String(format: "Downloading the speech model: %.1f of %.1f GB. This happens once.", have / 1e9, total / 1e9)
        } else if parts.first == "VERIFYING" {
            setupLabel?.stringValue = "Checking the download."
        } else if parts.first == "ERROR" {
            setupLabel?.stringValue = "Couldn't download the speech model: \(parts.dropFirst().joined(separator: " ")). Check your internet connection and try again."
        }
    }

    func showWelcome() {
        setupWindow?.title = "LiveCap is running"
        setupBar?.isHidden = true
        setupLabel?.stringValue = "LiveCap is running. Find it in the menu bar at the top of the screen. From there you can open the pages, see your admin password, and quit."
        retryButton?.title = "OK"
        retryButton?.action = #selector(closeSetupWindow)
        retryButton?.isHidden = false
        activate()
    }

    @objc func closeSetupWindow() {
        setupWindow?.close()
        setupWindow = nil
    }

    // MARK: Server

    func startServer() {
        status = .starting
        // A server killed outright can leave ffmpeg holding the OBS ports.
        let cleanup = Process()
        cleanup.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        cleanup.arguments = ["-9", "-f", resources + "/bin/ffmpeg"]
        try? cleanup.run()
        cleanup.waitUntilExit()

        let process = pythonProcess(["-m", "app.main"])
        if !fm.fileExists(atPath: logFile) { fm.createFile(atPath: logFile, contents: nil) }
        if let log = FileHandle(forWritingAtPath: logFile) {
            log.seekToEndOfFile()
            process.standardOutput = log
            process.standardError = log
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { self?.serverStopped() }
        }
        do {
            try process.run()
            server = process
        } catch {
            status = .failed("Couldn't start: \(error.localizedDescription)")
        }
        if healthTimer == nil {
            let timer = Timer(timeInterval: 3, repeats: true) { [weak self] _ in self?.checkHealth() }
            // Common modes keep this firing while a dialog is open or the menu is showing.
            RunLoop.main.add(timer, forMode: .common)
            healthTimer = timer
        }
        checkHealth()
    }

    func serverStopped() {
        guard !quitting else { return }
        server = nil
        setAwake(false)
        failures += 1
        status = .failed(failures >= 3 ? "Couldn't start. Another app may be using port \(httpPort()). See the logs." : "Restarting…")
        let delay = min(30.0, 2.0 * Double(failures))
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, !self.quitting, self.server == nil else { return }
            let message = self.status
            self.startServer()
            if self.failures >= 3 { self.status = message }
        }
    }

    func httpPort() -> String { settings()["LIVECAP_HTTP_PORT"] ?? "8000" }
    func httpsPort() -> String { settings()["LIVECAP_HTTPS_PORT"] ?? "8443" }

    func checkHealth() {
        guard server != nil, let url = URL(string: "http://127.0.0.1:\(httpPort())/healthz") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            let healthy = (response as? HTTPURLResponse)?.statusCode == 200
                && String(data: data ?? Data(), encoding: .utf8)?.contains("true") == true
            DispatchQueue.main.async {
                guard let self, self.server != nil else { return }
                if healthy {
                    self.status = .running
                    self.failures = 0
                    self.setAwake(true)
                } else if self.status == .running {
                    self.status = .failed("Speech recognition has a problem. See the logs.")
                }
                self.statusItem.button?.appearsDisabled = !healthy
            }
        }.resume()
    }

    func setAwake(_ on: Bool) {
        if on, awake == nil {
            awake = ProcessInfo.processInfo.beginActivity(options: [.idleSystemSleepDisabled, .userInitiated],
                                                          reason: "Serving live captions")
        } else if !on, let activity = awake {
            ProcessInfo.processInfo.endActivity(activity)
            awake = nil
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        setupProcess?.terminate()
        guard let server, server.isRunning else { return }
        server.terminate()
        let deadline = Date().addingTimeInterval(6)
        while server.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
        if server.isRunning { kill(server.processIdentifier, SIGKILL) }
        let cleanup = Process()
        cleanup.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        cleanup.arguments = ["-9", "-f", resources + "/bin/ffmpeg"]
        try? cleanup.run()
        cleanup.waitUntilExit()
    }

    // MARK: Menu

    func statusText() -> String {
        switch status {
        case .settingUp: return "Setting up…"
        case .starting: return "Starting…"
        case .running: return "Running"
        case .failed(let message): return message
        }
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let heading = NSMenuItem(title: "LiveCap: \(statusText())", action: nil, keyEquivalent: "")
        heading.isEnabled = false
        menu.addItem(heading)
        menu.addItem(.separator())
        let ready = status == .running
        add(menu, "Open Public Page", #selector(openPublic), enabled: ready)
        add(menu, "Open Admin Page", #selector(openAdmin), enabled: ready)
        add(menu, "Copy Public Address", #selector(copyAddress), enabled: ready)
        add(menu, "Show Admin Password…", #selector(showPassword), enabled: ready)
        menu.addItem(.separator())
        let login = add(menu, "Open at Login", #selector(toggleLogin), enabled: true)
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        add(menu, "Open Settings File", #selector(openSettings), enabled: true)
        add(menu, "Show Logs", #selector(showLogs), enabled: true)
        add(menu, "About LiveCap", #selector(showAbout), enabled: true)
        menu.addItem(.separator())
        switch update {
        case .idle: add(menu, "Check for Updates…", #selector(checkForUpdates), enabled: true)
        case .checking: add(menu, "Checking for Updates…", #selector(checkForUpdates), enabled: false)
        case .downloading(let done): add(menu, "Downloading Update: \(Int(done * 100))%", #selector(checkForUpdates), enabled: false)
        case .ready(let release): add(menu, "Install LiveCap \(release.version) and Restart…", #selector(installUpdate), enabled: true)
        }
        menu.addItem(.separator())
        add(menu, "Quit LiveCap", #selector(quit), enabled: true)
    }

    @discardableResult
    func add(_ menu: NSMenu, _ title: String, _ action: Selector, enabled: Bool) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        item.isEnabled = enabled
        menu.addItem(item)
        return item
    }

    func activate() {
        NSApp.activate(ignoringOtherApps: true)
    }

    func localAddress() -> String {
        var pointer: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&pointer) == 0, let first = pointer else { return "localhost" }
        defer { freeifaddrs(pointer) }
        var found: [String: String] = [:]
        for entry in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let flags = Int32(entry.pointee.ifa_flags)
            guard let address = entry.pointee.ifa_addr, address.pointee.sa_family == UInt8(AF_INET),
                  flags & IFF_UP != 0, flags & IFF_LOOPBACK == 0 else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(address, socklen_t(address.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 {
                found[String(cString: entry.pointee.ifa_name)] = String(cString: host)
            }
        }
        // Ethernet, then Wi-Fi, on a Mac Mini.
        return found["en0"] ?? found["en1"] ?? found.values.first ?? "localhost"
    }

    @objc func openPublic() {
        NSWorkspace.shared.open(URL(string: "http://localhost:\(httpPort())/")!)
    }

    @objc func openAdmin() {
        // Plain HTTP is fine here: browsers treat localhost as secure, so the microphone works without a certificate.
        NSWorkspace.shared.open(URL(string: "http://localhost:\(httpPort())/admin")!)
    }

    @objc func copyAddress() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("http://\(localAddress()):\(httpPort())/", forType: .string)
    }

    @objc func showPassword() {
        activate()
        let chosen = settings()["LIVECAP_ADMIN_PASSWORD"]
        let password = chosen ?? (try? String(contentsOfFile: dataDir + "/admin-password", encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let alert = NSAlert()
        alert.messageText = "Admin password"
        alert.informativeText = "\(password)\n\nThe browser also asks for a username. Anything works. From another computer, open https://\(localAddress()):\(httpsPort())/admin and accept the certificate warning."
        alert.addButton(withTitle: "Copy Password")
        alert.addButton(withTitle: "Close")
        if alert.runModal() == .alertFirstButtonReturn {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(password, forType: .string)
        }
    }

    @objc func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            activate()
            let alert = NSAlert()
            alert.messageText = "Couldn't change Open at Login"
            alert.informativeText = "\(error.localizedDescription) Make sure LiveCap is in the Applications folder. You can also add it under System Settings, General, Login Items."
            alert.runModal()
        }
    }

    @objc func openSettings() {
        NSWorkspace.shared.open(URL(fileURLWithPath: settingsFile))
    }

    @objc func showLogs() {
        if !fm.fileExists(atPath: logFile) { fm.createFile(atPath: logFile, contents: nil) }
        NSWorkspace.shared.open(URL(fileURLWithPath: logFile))
    }

    @objc func showAbout() {
        activate()
        let credits = (try? String(contentsOfFile: resources + "/THIRD_PARTY.txt", encoding: .utf8)) ?? ""
        let text = NSAttributedString(string: credits, attributes: [.font: NSFont.systemFont(ofSize: 10),
                                                                    .foregroundColor: NSColor.labelColor])
        NSApp.orderFrontStandardAboutPanel(options: [.credits: text])
    }

    // MARK: Updates

    /// Asks a question in a dialog. True for the first button.
    func confirm(_ title: String, _ detail: String, yes: String, no: String) -> Bool {
        #if UPDATE_TEST
        NSLog("LiveCap update test: %@ / %@", title, detail)
        updateTestLog("ASKED: \(title)")
        return true
        #else
        activate()
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: yes)
        alert.addButton(withTitle: no)
        return alert.runModal() == .alertFirstButtonReturn
        #endif
    }

    func tell(_ title: String, _ detail: String) {
        #if UPDATE_TEST
        updateTestLog("TOLD: \(title) / \(detail)")
        #else
        activate()
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.runModal()
        #endif
    }

    #if UPDATE_TEST
    func updateTestLog(_ line: String) {
        let text = "\(Updater.currentVersion) \(line)\n"
        if let file = FileHandle(forWritingAtPath: home + "/update-test.log") {
            file.seekToEndOfFile()
            file.write(Data(text.utf8))
        } else {
            try? text.write(toFile: home + "/update-test.log", atomically: true, encoding: .utf8)
        }
    }
    #endif

    @objc func checkForUpdates() {
        update = .checking
        updater.latest { [weak self] result in
            DispatchQueue.main.async { self?.checked(result) }
        }
    }

    func checked(_ result: Result<Release?, Error>) {
        update = .idle
        switch result {
        case .failure(let error):
            tell("Couldn't check for updates", error.localizedDescription)
        case .success(let release):
            guard let release, isNewer(release.version, than: Updater.currentVersion) else {
                tell("LiveCap is up to date", "You have version \(Updater.currentVersion), the newest available.")
                return
            }
            let size = String(format: "%.1f GB", Double(release.diskImageBytes) / 1e9)
            let notes = release.notes.isEmpty ? "" : "\n\n\(release.notes)"
            if confirm("LiveCap \(release.version) is available",
                       "You have version \(Updater.currentVersion). The download is \(size), and captions keep running while it downloads.\(notes)",
                       yes: "Download", no: "Later") {
                download(release)
            }
        }
    }

    func download(_ release: Release) {
        update = .downloading(0)
        updater.progress = { [weak self] done in
            DispatchQueue.main.async {
                if case .downloading = self?.update { self?.update = .downloading(done) }
            }
        }
        updater.prepare(release) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .failure(let error):
                    self.update = .idle
                    self.tell("The update wasn't installed", error.localizedDescription)
                case .success:
                    self.update = .ready(release)
                    self.installUpdate()
                }
            }
        }
    }

    @objc func installUpdate() {
        guard case .ready(let release) = update else { return }
        guard confirm("Install LiveCap \(release.version) now?",
                      "LiveCap will restart, so captions stop for about a minute and viewers reconnect by themselves. Choose Later to install it from the LiveCap menu when nothing is live.",
                      yes: "Install and Restart", no: "Later") else { return }
        do {
            try updater.install()
            quit()
        } catch {
            tell("The update wasn't installed", error.localizedDescription)
        }
    }

    @objc func quit() {
        // macOS ignores a quit request while a dialog is open.
        if NSApp.modalWindow != nil { NSApp.abortModal() }
        DispatchQueue.main.async { NSApp.terminate(nil) }
    }
}
