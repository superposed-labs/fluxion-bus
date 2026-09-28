import AppKit
import Foundation

// MARK: - Service Process Management
//
// Split out of main.swift. Covers discovery, restart, and stray-cleanup of the
// supervised Fluxion services (scheduler / web / gateway). Several helpers were
// `private` in the original single-file class; they are `internal` here because
// Swift's `private` is file-scoped and these are now reached from main.swift
// (saveEnv, applicationWillTerminate) and across this extension.
extension AppDelegate {

    // A supervised service's `pkill -f` pattern: its full venv binary path, so
    // we can't hit an unrelated process that merely contains the short name in
    // its command line.
    func servicePattern(_ name: String) -> String {
        (repoPath as NSString).appendingPathComponent(".venv/bin/\(name)")
    }

    // All services, in one place so the restart/terminate paths can't drift (an
    // earlier copy listed dead/footgun patterns like "fluxion-ui"/"fluxion.app").
    var serviceProcessPatterns: [String] {
        var patterns = ["fluxion-scheduler", "fluxion-web", "fluxion-gateway", "fluxion-provider"]
            .map(servicePattern)
        let tunnelName = envVals["FLUXION_LINE_TUNNEL_NAME"] ?? "fluxion-line"
        patterns.append("cloudflared tunnel run \(tunnelName)")
        return patterns
    }

    // The venv-binary suffixes for each service, *without* a repo prefix. Used
    // only to detect strays launched from a different checkout — see
    // terminateForeignServices(). servicePattern() stays repo-scoped for the
    // normal restart/terminate paths.
    var serviceBinarySuffixes: [String] {
        ["fluxion-scheduler", "fluxion-web", "fluxion-gateway", "fluxion-provider"]
            .map { ".venv/bin/\($0)" }
    }

    // Module-style launches (e.g. `python -m fluxion.gateway`, as an IDE run or
    // debug session produces). The app ONLY ever starts services via the
    // .venv/bin console scripts, so any `-m` invocation of a long-running
    // service module is by definition a stray competing for the same bot
    // credentials / UI port — always swept, regardless of path. detect_cli /
    // usage / sub are short-lived and deliberately excluded.
    var serviceModuleInvocations: [String] {
        ["fluxion.gateway", "fluxion.scheduler", "fluxion.web"].map { "-m \($0)" }
    }

    // Flags that make a service binary a one-shot CLI call instead of its
    // daemon. fluxion-scheduler doubles as the auto-ping config reader/writer
    // (runAutoPingCommand), and the app fires --get-autoping on its own launch
    // path — so "is this binary running?" can answer yes for a 0.2s probe and
    // skip starting a daemon that was never up. Since the daemon never ran, it
    // wrote no log line either, leaving nothing to explain the silence.
    var oneShotServiceFlags: [String] {
        ["--get-autoping", "--set-autoping", "--once"]
    }

    /// (pid, full command line) for every running Fluxion service process —
    /// both .venv/bin console scripts and `-m` module launches, regardless of
    /// which checkout launched it.
    func runningServiceProcesses() -> [(pid: Int32, command: String)] {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = ["ps", "-axo", "pid=,command="]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        do {
            try task.run()
        } catch {
            return []
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        guard let out = String(data: data, encoding: .utf8) else { return [] }

        var result: [(pid: Int32, command: String)] = []
        for raw in out.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            let parts = line.split(separator: " ", maxSplits: 1, omittingEmptySubsequences: true)
            guard parts.count == 2, let pid = Int32(parts[0]) else { continue }
            let command = String(parts[1])
            let isService = serviceBinarySuffixes.contains(where: { command.contains($0) })
                || serviceModuleInvocations.contains(where: { command.contains($0) })
            guard isService else { continue }
            result.append((pid, command))
        }
        return result
    }

    /// Kill any Fluxion service launched from a *different* checkout than the
    /// current repoPath. The gateway long-polls Slack/Telegram/WeChat with
    /// shared bot credentials, and those backends allow only one poller per bot
    /// — a stray gateway from another checkout silently steals inbound messages
    /// (and a stray web fights over the UI port). servicePattern()-based cleanup
    /// can't see these because it's scoped to *this* repo's venv path, so we
    /// match by binary suffix and skip anything already under repoPath.
    func terminateForeignServices() {
        let mine = (repoPath as NSString).appendingPathComponent(".venv/bin/")
        for proc in runningServiceProcesses() {
            // A `-m fluxion.<service>` launch is never started by the app, so
            // it's always a stray. A console-script process is a stray only when
            // it lives outside the current repo's venv.
            let isModuleLaunch = serviceModuleInvocations.contains { proc.command.contains($0) }
            let isForeignConsole = !proc.command.contains(mine)
            guard isModuleLaunch || isForeignConsole else { continue }
            NSLog("FluxionMenu: killing stray service (pid %d): %@",
                  proc.pid, proc.command)
            let kill = Process()
            kill.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            kill.arguments = ["kill", "-9", String(proc.pid)]
            kill.standardOutput = Pipe()
            kill.standardError = Pipe()
            try? kill.run()
            kill.waitUntilExit()
        }
    }

    // Restart every supervised service — the explicit "Restart All" button.
    func restartServices() {
        restartServices(patterns: serviceProcessPatterns)
    }

    func restartServices(patterns: [String]) {
        stopServices(patterns: patterns)
        Thread.sleep(forTimeInterval: 0.2)
        startServicesIfNeeded()
    }

    /// Stop the given services and block until they are actually gone. Also
    /// used on its own before a backend install/upgrade swaps the source tree:
    /// a surviving service would keep executing the replaced code, and the
    /// pgrep-based autostart would then skip (not restart) it. Blocks up to
    /// ~6s, so call it off the main thread.
    func stopServices(patterns: [String]) {
        // Ask the services to exit (SIGTERM — the daemons handle it gracefully).
        for pattern in patterns {
            signalProcesses(pattern: pattern, signal: nil)  // default TERM
        }

        // Wait until they're actually gone. Graceful shutdown can take up to a
        // tick, and startServicesIfNeeded() skips any service it still sees
        // running — so a fixed sleep here used to leave a service dead when
        // shutdown outran it. Poll instead, then force-kill stragglers.
        let deadline = Date().addingTimeInterval(6.0)
        while Date() < deadline && patterns.contains(where: { isProcessRunning(pattern: $0) }) {
            Thread.sleep(forTimeInterval: 0.2)
        }
        for pattern in patterns where isProcessRunning(pattern: pattern) {
            signalProcesses(pattern: pattern, signal: "KILL")
        }
    }

    /// pkill helper. `signal` is a name like "KILL"; nil sends the default TERM.
    func signalProcesses(pattern: String, signal: String?) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        var args = ["pkill"]
        if let signal = signal { args.append("-\(signal)") }
        args.append(contentsOf: ["-f", pattern])
        task.arguments = args
        task.standardOutput = Pipe()
        task.standardError = Pipe()
        try? task.run()
        task.waitUntilExit()
    }

    // MARK: - Autostart Services
    func isProcessRunning(pattern: String) -> Bool {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = ["pgrep", "-f", pattern]

        // Suppress output
        task.standardOutput = Pipe()
        task.standardError = Pipe()

        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0
        } catch {
            return false
        }
    }

    func isPortListening(_ port: String) -> Bool {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
        task.arguments = ["-z", "-G", "1", "127.0.0.1", port]
        task.standardOutput = Pipe()
        task.standardError = Pipe()

        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0
        } catch {
            return false
        }
    }

    /// Whether this checkout's long-running `name` daemon is up. Unlike a bare
    /// pgrep on the binary path, a one-shot CLI call of the same binary does
    /// not count as the daemon — see oneShotServiceFlags. Spawns ps, so call it
    /// off the main thread.
    func isServiceDaemonRunning(_ name: String) -> Bool {
        let binary = servicePattern(name)
        return runningServiceProcesses().contains { proc in
            proc.command.contains(binary)
                && !oneShotServiceFlags.contains(where: { proc.command.contains($0) })
        }
    }

    /// Whether this checkout's gateway console script is running. Spawns ps, so
    /// call it off the main thread.
    func isGatewayRunning() -> Bool {
        isServiceDaemonRunning("fluxion-gateway")
    }

    /// Whether startServicesIfNeeded() starts the venv service `name`. Shared
    /// with restartStaleSessionServices() so a stale service is only stopped
    /// when something will bring it back.
    func isAutostartEnabled(_ name: String) -> Bool {
        func flag(_ key: String, _ fallback: String) -> Bool {
            (envVals[key] ?? fallback).lowercased() == "true"
        }
        switch name {
        case "fluxion-web":
            return flag("FLUXION_MENU_AUTOSTART_WEB", "true")
        case "fluxion-scheduler":
            return flag("FLUXION_SCHEDULER_ENABLED", "true")
                && flag("FLUXION_MENU_AUTOSTART_SCHEDULER", "true")
        case "fluxion-gateway":
            // Gates the whole messaging gateway (all channels), not just Slack.
            return flag("FLUXION_MENU_AUTOSTART_GATEWAY", "false")
        case "fluxion-provider":
            // Off unless asked for, like the messaging gateway and for a sharper
            // reason: this one runs a local agent CLI for whatever request
            // arrives, so an unattended start spends subscription quota.
            // Enabling it is the user saying they have pointed Codex at it.
            return flag("FLUXION_PROVIDER_ENABLED", "false")
        default:
            return false
        }
    }

    // MARK: - Stale Login Session

    /// Wall-clock start time of `pid`, from the kernel's process table.
    func processStartTime(pid: Int32) -> Date? {
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0, size > 0 else {
            return nil
        }
        let tv = info.kp_proc.p_un.__p_starttime
        return Date(timeIntervalSince1970: TimeInterval(tv.tv_sec) + TimeInterval(tv.tv_usec) / 1_000_000)
    }

    /// When the current GUI login began: the start time of this user's
    /// loginwindow, which is replaced on every logout/login.
    func currentLoginSessionStart() -> Date? {
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_UID, Int32(bitPattern: getuid())]
        var size = 0
        guard sysctl(&mib, u_int(mib.count), nil, &size, nil, 0) == 0, size > 0 else { return nil }
        // The table can grow between the size query and the read.
        size += 16 * MemoryLayout<kinfo_proc>.stride
        var procs = [kinfo_proc](repeating: kinfo_proc(), count: size / MemoryLayout<kinfo_proc>.stride)
        guard sysctl(&mib, u_int(mib.count), &procs, &size, nil, 0) == 0 else { return nil }
        let count = size / MemoryLayout<kinfo_proc>.stride

        var newest: Date?
        for var proc in procs.prefix(count) {
            let name = withUnsafeBytes(of: &proc.kp_proc.p_comm) { raw in
                String(decoding: raw.prefix(while: { $0 != 0 }), as: UTF8.self)
            }
            guard name == "loginwindow" else { continue }
            let tv = proc.kp_proc.p_un.__p_starttime
            let start = Date(timeIntervalSince1970: TimeInterval(tv.tv_sec) + TimeInterval(tv.tv_usec) / 1_000_000)
            if newest == nil || start > newest! { newest = start }
        }
        return newest
    }

    /// Stop this checkout's services that predate the current login so
    /// startServicesIfNeeded() relaunches them here. A service that survives a
    /// logout/login stays bound to the dead session: every `security` call it
    /// spawns fails at XPC bootstrap, so Keychain-backed usage probes (Claude,
    /// Antigravity) quietly report "no token" whenever that service is the one
    /// probing — e.g. while the screen is off and this app polls slowly. The
    /// app's own pgrep check would otherwise see it as running and keep it.
    /// Only services with autostart on are touched, so nothing is stopped
    /// without being restarted. Spawns ps, so call it off the main thread.
    func restartStaleSessionServices() {
        guard let sessionStart = currentLoginSessionStart() else { return }
        let running = runningServiceProcesses()
        var stale: [String] = []
        for name in ["fluxion-scheduler", "fluxion-web", "fluxion-gateway", "fluxion-provider"]
        where isAutostartEnabled(name) {
            let binary = servicePattern(name)
            let procs = running.filter { proc in
                proc.command.contains(binary)
                    && !oneShotServiceFlags.contains(where: { proc.command.contains($0) })
            }
            for proc in procs {
                guard let started = processStartTime(pid: proc.pid), started < sessionStart else { continue }
                NSLog("FluxionMenu: %@ (pid %d) predates this login session; restarting it",
                      name, proc.pid)
                stale.append(binary)
                break
            }
        }
        guard !stale.isEmpty else { return }
        stopServices(patterns: stale)
    }

    func startServicesIfNeeded() {
        let uiBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-web")
        let schedulerBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-scheduler")
        let gatewayBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-gateway")
        let providerBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-provider")
        let uiPort = envVals["FLUXION_UI_PORT"] ?? "8765"

        let autostartWeb = isAutostartEnabled("fluxion-web")
        let autostartSched = isAutostartEnabled("fluxion-scheduler")
        let autostartGateway = isAutostartEnabled("fluxion-gateway")
        let autostartLineTunnel = (envVals["FLUXION_LINE_ENABLED"] ?? "false").lowercased() == "true"
        let autostartProvider = isAutostartEnabled("fluxion-provider")

        if autostartWeb && FileManager.default.fileExists(atPath: uiBin) {
            // A terminating Uvicorn process can remain alive while waiting for
            // an SSE connection to close even though it no longer accepts new
            // requests. The listening port is the actual readiness signal.
            if !isPortListening(uiPort) {
                shell(args: [uiBin, "--port", uiPort])
            }
        }
        if autostartSched && FileManager.default.fileExists(atPath: schedulerBin) {
            // Daemon-scoped on purpose: this runs concurrently with the launch
            // path's --get-autoping probe (promptForUnwatchedProvidersIfNeeded),
            // which executes this very binary.
            if !isServiceDaemonRunning("fluxion-scheduler") {
                shell(args: [schedulerBin])
            }
        }
        if autostartGateway && FileManager.default.fileExists(atPath: gatewayBin) {
            if !isServiceDaemonRunning("fluxion-gateway") {
                shell(args: [gatewayBin])
            }
        }
        if autostartProvider && FileManager.default.fileExists(atPath: providerBin) {
            if !isServiceDaemonRunning("fluxion-provider") {
                shell(args: [providerBin, "serve"])
            }
        }
        if autostartLineTunnel {
            let tunnelName = envVals["FLUXION_LINE_TUNNEL_NAME"] ?? "fluxion-line"
            if !isProcessRunning(pattern: "cloudflared tunnel run \(tunnelName)") {
                shell(args: ["cloudflared", "tunnel", "run", tunnelName])
            }
        }
    }

    /// Fire-and-forget launch of a helper process. Failures are logged rather
    /// than silently swallowed.
    func shell(args: [String]) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = args
        task.currentDirectoryURL = URL(fileURLWithPath: repoPath)

        var env = ProcessInfo.processInfo.environment
        env["FLUXION_ENV_FILE"] = envPath
        let currentPath = env["PATH"] ?? ""
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + currentPath
        task.environment = env

        task.terminationHandler = { proc in
            if proc.terminationStatus != 0 {
                NSLog("FluxionMenu: command %@ exited with status %d", args.first ?? "?", proc.terminationStatus)
            }
        }

        do {
            try task.run()
        } catch {
            NSLog("FluxionMenu: failed to launch %@: %@", args.first ?? "?", error.localizedDescription)
        }
    }
}
