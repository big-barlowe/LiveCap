import CryptoKit
import Foundation

// Finds, downloads and installs new versions from the GitHub repository's releases.
//
// The free build has no Apple notarization vouching for it, so a release carries its own signature, made with a
// private key that only exists on the publisher's Mac. Nothing from a download runs or is unpacked until that
// signature checks out against the public key built into this app.

struct Release {
    let version: String
    let notes: String
    let page: URL
    let diskImage: URL
    let diskImageBytes: Int64
    let signature: URL
}

enum UpdateError: LocalizedError {
    case notConfigured, noDownload, badResponse(Int), badSignature, wrongApp(String), noPermission(String), noSpace(Int64), failed(String)

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "This copy of LiveCap isn't set up to look for updates."
        case .noDownload: return "The newest release has no disk image and signature attached."
        case .badResponse(let code): return "GitHub answered with error \(code). Try again later."
        case .badSignature: return "The download's signature doesn't match, so it was deleted and nothing was installed."
        case .wrongApp(let detail): return "The download wasn't installed: \(detail)"
        case .noPermission(let folder): return "LiveCap isn't allowed to replace itself in \(folder). Install the new version by hand from the release page."
        case .noSpace(let bytes): return String(format: "The update needs %.1f GB of free disk space.", Double(bytes) / 1e9)
        case .failed(let detail): return detail
        }
    }
}

func isNewer(_ version: String, than other: String) -> Bool {
    version.compare(other, options: .numeric) == .orderedDescending
}

func sha256(ofFile path: String) throws -> Data {
    let file = try FileHandle(forReadingFrom: URL(fileURLWithPath: path))
    defer { try? file.close() }
    var hash = SHA256()
    while let chunk = try file.read(upToCount: 1 << 20), !chunk.isEmpty { hash.update(data: chunk) }
    return Data(hash.finalize())
}

func signatureIsValid(_ signature: Data, forFile path: String, publicKey: String) throws -> Bool {
    guard let raw = Data(base64Encoded: publicKey),
          let key = try? Curve25519.Signing.PublicKey(rawRepresentation: raw) else { throw UpdateError.notConfigured }
    return key.isValidSignature(signature, for: try sha256(ofFile: path))
}

final class Updater: NSObject, URLSessionDownloadDelegate {
    static let currentVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"

    let appPath: String
    let workDir: String
    let installLog: String
    var progress: ((Double) -> Void)?
    private var finishDownload: ((Result<URL, Error>) -> Void)?
    private lazy var session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)

    private let fm = FileManager.default
    private var repo: String { Bundle.main.object(forInfoDictionaryKey: "LiveCapUpdateRepo") as? String ?? "" }
    private var publicKey: String { Bundle.main.object(forInfoDictionaryKey: "LiveCapUpdateKey") as? String ?? "" }
    private var folder: String { (appPath as NSString).deletingLastPathComponent }
    private var stagedApp: String { folder + "/.LiveCap-update.app" }
    private var previousApp: String { folder + "/.LiveCap-previous.app" }

    init(appPath: String, workDir: String, installLog: String) {
        self.appPath = appPath
        self.workDir = workDir
        self.installLog = installLog
    }

    /// Clears what an interrupted update left behind.
    func removeLeftovers() {
        for path in [workDir, stagedApp, previousApp] { try? fm.removeItem(atPath: path) }
    }

    // MARK: Checking

    private func feedRequest() throws -> URLRequest {
        #if UPDATE_TEST
        if let feed = ProcessInfo.processInfo.environment["LIVECAP_UPDATE_FEED"] { return URLRequest(url: URL(string: feed)!) }
        #endif
        guard repo.contains("/"), !repo.hasPrefix("OWNER/"), !publicKey.isEmpty,
              let url = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else { throw UpdateError.notConfigured }
        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("LiveCap/\(Updater.currentVersion)", forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 20
        return request
    }

    /// The newest published release, or nil if there isn't one yet.
    func latest(_ done: @escaping (Result<Release?, Error>) -> Void) {
        let request: URLRequest
        do { request = try feedRequest() } catch { return done(.failure(error)) }
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error { return done(.failure(error)) }
            let code = (response as? HTTPURLResponse)?.statusCode ?? 200
            if code == 404 { return done(.success(nil)) }
            guard code == 200 else { return done(.failure(UpdateError.badResponse(code))) }
            done(Result { try Self.parse(data ?? Data()) })
        }.resume()
    }

    static func parse(_ data: Data) throws -> Release {
        struct Asset: Decodable {
            let name: String
            let browser_download_url: URL
            let size: Int64
        }
        struct Feed: Decodable {
            let tag_name: String
            let body: String?
            let html_url: URL
            let assets: [Asset]
        }
        let feed = try JSONDecoder().decode(Feed.self, from: data)
        guard let image = feed.assets.first(where: { $0.name == "LiveCap.dmg" }),
              let signature = feed.assets.first(where: { $0.name == "LiveCap.dmg.sig" }) else { throw UpdateError.noDownload }
        let version = feed.tag_name.hasPrefix("v") ? String(feed.tag_name.dropFirst()) : feed.tag_name
        return Release(version: version, notes: feed.body ?? "", page: feed.html_url, diskImage: image.browser_download_url,
                       diskImageBytes: image.size, signature: signature.browser_download_url)
    }

    // MARK: Downloading and checking

    /// Downloads and checks the release, and leaves it unpacked beside the app, ready to swap in.
    func prepare(_ release: Release, _ done: @escaping (Result<Void, Error>) -> Void) {
        guard fm.isWritableFile(atPath: folder) else { return done(.failure(UpdateError.noPermission(folder))) }
        // The download, plus the unpacked app, which is about twice its size.
        let needed = release.diskImageBytes * 3
        if let free = try? URL(fileURLWithPath: folder).resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
            .volumeAvailableCapacityForImportantUsage, free < needed {
            return done(.failure(UpdateError.noSpace(needed)))
        }
        removeLeftovers()
        do { try fm.createDirectory(atPath: workDir, withIntermediateDirectories: true) } catch { return done(.failure(error)) }
        URLSession.shared.dataTask(with: release.signature) { [self] data, response, error in
            if let error {
                removeLeftovers()
                return done(.failure(error))
            }
            guard let data, let text = String(data: data, encoding: .utf8),
                  let signature = Data(base64Encoded: text.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                removeLeftovers()
                return done(.failure(UpdateError.badSignature))
            }
            download(release.diskImage) { [self] result in
                DispatchQueue.global(qos: .userInitiated).async { [self] in
                    let outcome = Result<Void, Error> {
                        let image = workDir + "/LiveCap.dmg"
                        try fm.moveItem(at: try result.get(), to: URL(fileURLWithPath: image))
                        try check(image, signature: signature, release: release)
                        try fm.removeItem(atPath: workDir)
                    }
                    if case .failure = outcome { removeLeftovers() }
                    done(outcome)
                }
            }
        }.resume()
    }

    private func download(_ url: URL, _ done: @escaping (Result<URL, Error>) -> Void) {
        finishDownload = done
        session.downloadTask(with: url).resume()
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesExpectedToWrite > 0 { progress?(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite)) }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // The file is gone once this returns, so it moves now.
        let kept = URL(fileURLWithPath: workDir + "/download")
        let code = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 200
        let result = Result<URL, Error> {
            guard code == 200 else { throw UpdateError.badResponse(code) }
            try fm.moveItem(at: location, to: kept)
            return kept
        }
        finishDownload?(result)
        finishDownload = nil
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error, let finish = finishDownload {
            finishDownload = nil
            finish(.failure(error))
        }
    }

    private func run(_ tool: String, _ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: tool)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw UpdateError.failed("\((tool as NSString).lastPathComponent) failed: \(output.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        return output
    }

    private func check(_ image: String, signature: Data, release: Release) throws {
        guard try signatureIsValid(signature, forFile: image, publicKey: publicKey) else { throw UpdateError.badSignature }
        let mount = workDir + "/mount"
        try fm.createDirectory(atPath: mount, withIntermediateDirectories: true)
        _ = try run("/usr/bin/hdiutil", ["attach", image, "-nobrowse", "-readonly", "-mountpoint", mount])
        defer { _ = try? run("/usr/bin/hdiutil", ["detach", mount, "-force"]) }
        _ = try run("/usr/bin/ditto", [mount + "/LiveCap.app", stagedApp])
        _ = try? run("/usr/bin/xattr", ["-dr", "com.apple.quarantine", stagedApp])
        do {
            _ = try run("/usr/bin/codesign", ["--verify", "--strict", stagedApp])
        } catch {
            throw UpdateError.wrongApp("its code signature isn't valid.")
        }
        guard let info = Bundle(path: stagedApp)?.infoDictionary,
              info["CFBundleIdentifier"] as? String == Bundle.main.bundleIdentifier else {
            throw UpdateError.wrongApp("it isn't LiveCap.")
        }
        // The signature covers what's in the image, not the release it was attached to, so an old signed
        // image put on a new release must not get through as a downgrade.
        guard info["CFBundleShortVersionString"] as? String == release.version, isNewer(release.version, than: Updater.currentVersion) else {
            throw UpdateError.wrongApp("its version doesn't match the release.")
        }
    }

    // MARK: Installing

    /// Starts the swap, which waits for this app to quit, so quit straight after calling this.
    func install() throws {
        let script = #"""
        pid=$1; app=$2; staged=$3; previous=$4; log=$5
        exec >>"$log" 2>&1
        echo "$(date): installing an update"
        waited=0
        while kill -0 "$pid" 2>/dev/null; do
          sleep 0.2
          waited=$((waited + 1))
          [ "$waited" -gt 150 ] && { echo "LiveCap didn't quit, so the update wasn't installed"; rm -rf "$staged"; exit 1; }
        done
        rm -rf "$previous"
        if mv "$app" "$previous" && mv "$staged" "$app"; then
          rm -rf "$previous"
          echo "installed"
        else
          echo "couldn't swap the app, so the old version stays"
          [ -d "$app" ] || mv "$previous" "$app"
          rm -rf "$staged"
        fi
        OPEN_COMMAND "$app"
        """#
        var open = "open"
        #if UPDATE_TEST
        if let home = ProcessInfo.processInfo.environment["LIVECAP_HOME"] {
            open = "open --env 'LIVECAP_HOME=\(home)' --env 'LIVECAP_UPDATE_FEED=\(ProcessInfo.processInfo.environment["LIVECAP_UPDATE_FEED"] ?? "")'"
        }
        #endif
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", script.replacingOccurrences(of: "OPEN_COMMAND", with: open), "livecap-update",
                             String(ProcessInfo.processInfo.processIdentifier), appPath, stagedApp, previousApp, installLog]
        try process.run()
    }
}
