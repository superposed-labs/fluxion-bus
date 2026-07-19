import AppKit
import Foundation

// MARK: - Screen State, Hybrid Polling & Cache Watcher
//
// Split out of main.swift. Owns the dynamic refresh lifecycle: screen/session
// state observers, the monitor-tick activity heuristic that decides when to
// refresh, the adaptive interval back-off, and the event-driven watcher on the
// usage cache file. `cacheSource` / `watcherQueue` stay stored in the main
// class but are read here, so they were widened from `private` to internal.
extension AppDelegate {

    // MARK: - Screen State & Hybrid Dynamic Polling
    func setupScreenNotifications() {
        let wsNC = NSWorkspace.shared.notificationCenter

        wsNC.addObserver(self, selector: #selector(screenDidSleep), name: NSWorkspace.screensDidSleepNotification, object: nil)
        wsNC.addObserver(self, selector: #selector(screenDidWake), name: NSWorkspace.screensDidWakeNotification, object: nil)
        wsNC.addObserver(self, selector: #selector(sessionDidResignActive), name: NSWorkspace.sessionDidResignActiveNotification, object: nil)
        wsNC.addObserver(self, selector: #selector(sessionDidBecomeActive), name: NSWorkspace.sessionDidBecomeActiveNotification, object: nil)
    }

    @objc func screenDidSleep() {
        NSLog("FluxionMenu: Screen did sleep.")
        setScreenActive(false)
    }

    @objc func screenDidWake() {
        NSLog("FluxionMenu: Screen did wake.")
        setScreenActive(true)
    }

    @objc func sessionDidResignActive() {
        NSLog("FluxionMenu: Session resigned active (screen locked).")
        setScreenActive(false)
    }

    @objc func sessionDidBecomeActive() {
        NSLog("FluxionMenu: Session became active (screen unlocked).")
        setScreenActive(true)
    }

    func setScreenActive(_ active: Bool) {
        guard isScreenActive != active else { return }
        isScreenActive = active
        NSLog("FluxionMenu: Screen active state changed to: %{public}@", active ? "true" : "false")

        if active {
            currentInterval = PollingConfig.defaultInterval
            isPendingRefresh = false
            refresh(force: false)
            lastRefreshTime = Date()
            seedInitialCachedProviders()
            seedActivityTimestamps()
        } else {
            // No point animating a spinner while the screen is off.
            stopSpinner()
        }

        resetBackgroundTimer()
    }

    func resetBackgroundTimer() {
        monitorTimer?.invalidate()

        if isScreenActive {
            NSLog("FluxionMenu: Screen active. Starting 5s monitor timer.")
            monitorTimer = Timer.scheduledTimer(timeInterval: 5.0, target: self, selector: #selector(monitorTick), userInfo: nil, repeats: true)
            RunLoop.main.add(monitorTimer!, forMode: .common)
        } else {
            NSLog("FluxionMenu: Screen inactive. Starting 10-minute slow poll timer.")
            monitorTimer = Timer.scheduledTimer(withTimeInterval: PollingConfig.inactiveInterval, repeats: true) { [weak self] _ in
                self?.refresh(force: false)
            }
            RunLoop.main.add(monitorTimer!, forMode: .common)
        }
    }

    @objc func monitorTick() {
        guard isScreenActive else { return }

        // 1. Check frontmost application focus on the main thread (thread-safe)
        var focusActivity = false
        if let frontmost = NSWorkspace.shared.frontmostApplication {
            if frontmost.bundleIdentifier == "com.anthropic.claudefordesktop" {
                focusActivity = true
            }
        }

        // 2. Scan filesystem mtimes in the background
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }
            let claudeMtime = self.getMtime(path: self.claudeHistoryPath)
            let codexMtime = self.getNewestMtimeInDirectory(path: self.codexSessionsPath)
            let claudeCodeSessionsMtime = self.getNewestMtimeInDirectory(path: self.claudeCodeSessionsPath)
            let localAgentMtime = self.getNewestMtimeInDirectory(path: self.localAgentModeSessionsPath)
            DispatchQueue.main.async {
                self.processMonitorTick(
                    claudeMtime: claudeMtime,
                    codexMtime: codexMtime,
                    claudeCodeSessionsMtime: claudeCodeSessionsMtime,
                    localAgentMtime: localAgentMtime,
                    focusActivity: focusActivity
                )
            }
        }
    }

    func processMonitorTick(
        claudeMtime: Date?,
        codexMtime: Date?,
        claudeCodeSessionsMtime: Date?,
        localAgentMtime: Date?,
        focusActivity: Bool
    ) {
        let now = Date()
        let timeSinceLastRefresh = now.timeIntervalSince(lastRefreshTime)

        // 1. Check local file activity
        var claudeActivity = false
        var codexActivity = false

        if let cur = claudeMtime {
            if let last = lastClaudeHistoryMtime, cur > last {
                claudeActivity = true
            }
            lastClaudeHistoryMtime = cur
        }
        if let cur = claudeCodeSessionsMtime {
            if let last = lastClaudeCodeSessionsMtime, cur > last {
                claudeActivity = true
            }
            lastClaudeCodeSessionsMtime = cur
        }
        if let cur = localAgentMtime {
            if let last = lastLocalAgentModeSessionsMtime, cur > last {
                claudeActivity = true
            }
            lastLocalAgentModeSessionsMtime = cur
        }
        if let cur = codexMtime {
            if let last = lastCodexSessionsMtime, cur > last {
                codexActivity = true
            }
            lastCodexSessionsMtime = cur
        }

        let fileActivityDetected = claudeActivity || codexActivity

        if fileActivityDetected || focusActivity {
            if isPendingRefresh == false {
                if fileActivityDetected {
                    NSLog("FluxionMenu: Local CLI/Agent activity detected!")
                } else {
                    NSLog("FluxionMenu: Claude Desktop App focus detected!")
                }
            }
            if claudeActivity || focusActivity {
                pendingForceProviders.insert("claude")
            }
            if codexActivity {
                pendingForceProviders.insert("codex")
            }
            currentInterval = PollingConfig.defaultInterval
            isPendingRefresh = true
        }

        // 1b. Eager reset confirmation. When a locked provider's predicted reset
        // instant has elapsed but the cached snapshot still reports it spent, the
        // notch shows the transitional "confirming…" state. Force a
        // provider-scoped refresh so recovery lands within seconds instead of
        // waiting out the (possibly backed-off) poll interval. The 60s throttle
        // below still applies, so a chronically-past reset can't hammer the API,
        // and this only reads the predicted time — it never asserts recovery.
        let presenter = NotchQuotaPresenter(now: now)
        for provider in lastCachedProviders where provider.status == "ok" {
            if presenter.quotaState(for: provider).mode == .recovering {
                pendingForceProviders.insert(provider.provider)
                currentInterval = PollingConfig.defaultInterval
                isPendingRefresh = true
            }
        }

        // 2. Determine if we should refresh
        var shouldRefresh = false
        if isPendingRefresh {
            // Throttle: wait for at least 60s since the last refresh
            if timeSinceLastRefresh >= PollingConfig.defaultInterval {
                shouldRefresh = true
                isPendingRefresh = false
            }
        } else {
            // Polling: wait for current dynamic interval
            if timeSinceLastRefresh >= currentInterval {
                shouldRefresh = true
            }
        }

        if shouldRefresh {
            let forceProviders = pendingForceProviders.sorted()
            let providerList = forceProviders.isEmpty ? "none" : forceProviders.joined(separator: ",")
            NSLog("FluxionMenu: Triggering refresh. Current interval: %{public}.1f s, forceProviders: %{public}@", currentInterval, providerList)
            refresh(force: false, forceProviders: forceProviders)
            pendingForceProviders.removeAll()
            lastRefreshTime = Date()
            adjustIntervalBasedOnDataChange()
        }
    }

    // MARK: - Hybrid Refresh Helpers
    func getMtime(path: String) -> Date? {
        let attrs = try? FileManager.default.attributesOfItem(atPath: path)
        return attrs?[.modificationDate] as? Date
    }

    func getNewestMtimeInDirectory(path: String) -> Date? {
        let fm = FileManager.default
        guard let enumerator = fm.enumerator(atPath: path) else { return nil }
        var newest: Date? = nil
        while let file = enumerator.nextObject() as? String {
            let fullPath = (path as NSString).appendingPathComponent(file)
            var isDir: ObjCBool = false
            if fm.fileExists(atPath: fullPath, isDirectory: &isDir) && !isDir.boolValue {
                if let mtime = getMtime(path: fullPath) {
                    if newest == nil || mtime > newest! {
                        newest = mtime
                    }
                }
            }
        }
        return newest
    }

    /// Seeds the activity timestamps without flagging activity (background read).
    func seedActivityTimestamps() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }
            let claudeMtime = self.getMtime(path: self.claudeHistoryPath)
            let codexMtime = self.getNewestMtimeInDirectory(path: self.codexSessionsPath)
            let claudeCodeSessionsMtime = self.getNewestMtimeInDirectory(path: self.claudeCodeSessionsPath)
            let localAgentMtime = self.getNewestMtimeInDirectory(path: self.localAgentModeSessionsPath)
            DispatchQueue.main.async {
                self.lastClaudeHistoryMtime = claudeMtime
                self.lastCodexSessionsMtime = codexMtime
                self.lastClaudeCodeSessionsMtime = claudeCodeSessionsMtime
                self.lastLocalAgentModeSessionsMtime = localAgentMtime
            }
        }
    }

    func seedInitialCachedProviders() {
        let path = cachePath
        if FileManager.default.fileExists(atPath: path),
           let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
           let cache = try? JSONDecoder().decode(UsageCache.self, from: data) {
            lastCachedProviders = cache.providers
        }
    }

    func hasUsageChanged(old: [ProviderUsage], new: [ProviderUsage]) -> Bool {
        if old.count != new.count { return true }
        for (i, oldP) in old.enumerated() {
            let newP = new[i]
            if oldP.provider != newP.provider { return true }
            if oldP.status != newP.status { return true }
            if oldP.windows.count != newP.windows.count { return true }
            if oldP.resets != newP.resets { return true }
            for (j, oldW) in oldP.windows.enumerated() {
                let newW = newP.windows[j]
                if oldW.usedPercent != newW.usedPercent { return true }
                if oldW.remaining != newW.remaining { return true }
                if oldW.total != newW.total { return true }
            }
        }
        return false
    }

    func adjustIntervalBasedOnDataChange() {
        let path = cachePath
        guard FileManager.default.fileExists(atPath: path),
              let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let cache = try? JSONDecoder().decode(UsageCache.self, from: data) else {
            return
        }

        let newProviders = cache.providers
        if lastCachedProviders.isEmpty {
            lastCachedProviders = newProviders
            currentInterval = PollingConfig.defaultInterval
            return
        }

        if hasUsageChanged(old: lastCachedProviders, new: newProviders) {
            NSLog("FluxionMenu: Quota data has changed! Resetting interval to 60s.")
            currentInterval = PollingConfig.defaultInterval
        } else {
            let oldInterval = currentInterval
            currentInterval = min(PollingConfig.maxInterval, currentInterval * 1.5)
            if currentInterval != oldInterval {
                NSLog("FluxionMenu: No quota change. Backing off interval: %{public}.1f s -> %{public}.1f s", oldInterval, currentInterval)
            }
        }

        lastCachedProviders = newProviders
    }

    // MARK: - Cache File Watcher
    /// Watches `usage_cache.json` for in-place writes and re-renders on change,
    /// replacing the old "re-read the file on a 1.5s timer forever" loop.
    func startCacheWatcher() {
        guard cacheSource == nil else { return }
        let path = cachePath
        guard FileManager.default.fileExists(atPath: path) else {
            // File not written yet; will be re-attempted after the next refresh.
            return
        }
        let fd = open(path, O_EVTONLY)
        guard fd >= 0 else {
            NSLog("FluxionMenu: failed to open cache for watching: %@", path)
            return
        }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write, .extend, .delete, .rename],
            queue: watcherQueue
        )
        source.setEventHandler { [weak self] in
            guard let self = self else { return }
            let flags = source.data
            if flags.contains(.delete) || flags.contains(.rename) {
                // The file was replaced; tear down and re-arm on the new inode.
                DispatchQueue.main.async {
                    self.stopCacheWatcher()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                        self.startCacheWatcher()
                        self.reloadCacheFromDisk()
                        self.render()
                    }
                }
                return
            }
            DispatchQueue.main.async {
                self.reloadCacheFromDisk()
                self.render()
            }
        }
        source.setCancelHandler {
            close(fd)
        }
        cacheSource = source
        source.resume()
        NSLog("FluxionMenu: watching cache file %@", path)
    }

    func stopCacheWatcher() {
        cacheSource?.cancel() // cancel handler closes the descriptor
        cacheSource = nil
    }
}
