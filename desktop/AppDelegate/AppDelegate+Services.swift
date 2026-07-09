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
        var patterns = ["fluxion-scheduler", "fluxion-web", "fluxion-gateway"].map(servicePattern)
        let tunnelName = envVals["FLUXION_LINE_TUNNEL_NAME"] ?? "fluxion-line"
        patterns.append("cloudflared tunnel run \(tunnelName)")
        return patterns
    }

    // The venv-binary suffixes for each service, *without* a repo prefix. Used
    // only to detect strays launched from a different checkout — see
    // terminateForeignServices(). servicePattern() stays repo-scoped for the
    // normal restart/terminate paths.
    var serviceBinarySuffixes: [String] {
        ["fluxion-scheduler", "fluxion-web", "fluxion-gateway"]
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

    /// Whether this checkout's gateway console script is running. Spawns pgrep,
    /// so call it off the main thread.
    func isGatewayRunning() -> Bool {
        isProcessRunning(pattern: servicePattern("fluxion-gateway"))
    }

    func startServicesIfNeeded() {
        let uiBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-web")
        let schedulerBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-scheduler")
        let gatewayBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-gateway")
        let uiPort = envVals["FLUXION_UI_PORT"] ?? "8765"

        let autostartWeb = (envVals["FLUXION_MENU_AUTOSTART_WEB"] ?? "true").lowercased() == "true"
        let schedulerEnabled = (envVals["FLUXION_SCHEDULER_ENABLED"] ?? "true").lowercased() == "true"
        let autostartSched = schedulerEnabled && (envVals["FLUXION_MENU_AUTOSTART_SCHEDULER"] ?? "true").lowercased() == "true"
        // Gates the whole messaging gateway (all channels), not just Slack.
        let autostartGateway = (envVals["FLUXION_MENU_AUTOSTART_GATEWAY"] ?? "false").lowercased() == "true"
        let autostartLineTunnel = (envVals["FLUXION_LINE_ENABLED"] ?? "false").lowercased() == "true"

        if autostartWeb && FileManager.default.fileExists(atPath: uiBin) {
            // A terminating Uvicorn process can remain alive while waiting for
            // an SSE connection to close even though it no longer accepts new
            // requests. The listening port is the actual readiness signal.
            if !isPortListening(uiPort) {
                shell(args: [uiBin, "--port", uiPort])
            }
        }
        if autostartSched && FileManager.default.fileExists(atPath: schedulerBin) {
            if !isProcessRunning(pattern: schedulerBin) {
                shell(args: [schedulerBin])
            }
        }
        if autostartGateway && FileManager.default.fileExists(atPath: gatewayBin) {
            if !isProcessRunning(pattern: gatewayBin) {
                shell(args: [gatewayBin])
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
