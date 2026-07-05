import AppKit
import Foundation

// MARK: - Shared pending-user approval UI
//
// WeChat, QQ and Feishu each surface "pending users" — first-time senders that
// were rejected and now wait for the operator to Allow or Remove them. The
// storage format, the row layout and the allow/remove flow are identical across
// channels, so they share one implementation here. Each channel only differs in
// its JSON file name, its display name, and which stack / allowed-users field it
// is wired to (see PendingUserChannel and the accessors below).

/// One pending sender, persisted in <channel>_pending_users.json.
struct PendingUser: Codable {
    let firstSeenAt: String
    let lastSeenAt: String
    let messageCount: Int
    let lastMessagePreview: String

    enum CodingKeys: String, CodingKey {
        case firstSeenAt = "first_seen_at"
        case lastSeenAt = "last_seen_at"
        case messageCount = "message_count"
        case lastMessagePreview = "last_message_preview"
    }
}

/// On-disk envelope: `{ "users": { "<id>": PendingUser } }`.
struct PendingUsersFile: Codable {
    let users: [String: PendingUser]
}

/// Allow / Remove button that remembers which user and channel it acts on.
final class PendingActionButton: NSButton {
    var userId: String = ""
    var channel: PendingUserChannel = .wechat
}

/// A messaging channel that supports the pending-user approval flow.
enum PendingUserChannel: CaseIterable {
    case wechat
    case qqbot
    case feishu
    case telegram
    case slack
    case line

    /// File under the data dir holding this channel's pending users.
    var fileName: String {
        switch self {
        case .wechat: return "wechat_pending_users.json"
        case .qqbot: return "qqbot_pending_users.json"
        case .feishu: return "feishu_pending_users.json"
        case .telegram: return "telegram_pending_users.json"
        case .slack: return "slack_pending_users.json"
        case .line: return "line_pending_users.json"
        }
    }

    /// Human-readable channel name used in empty-state and log messages.
    var displayName: String {
        switch self {
        case .wechat: return "WeChat"
        case .qqbot: return "QQ"
        case .feishu: return "Feishu"
        case .telegram: return "Telegram"
        case .slack: return "Slack"
        case .line: return "LINE"
        }
    }

    /// Stable identifier matching the `channel` field the pending stores
    /// write into macos_notifications.jsonl (`_CHANNEL_KEY` in Python).
    var key: String {
        switch self {
        case .wechat: return "wechat"
        case .qqbot: return "qqbot"
        case .feishu: return "feishu"
        case .telegram: return "telegram"
        case .slack: return "slack"
        case .line: return "line"
        }
    }

    init?(key: String) {
        switch key {
        case "wechat": self = .wechat
        case "qqbot": self = .qqbot
        case "feishu": self = .feishu
        case "telegram": self = .telegram
        case "slack": self = .slack
        case "line": self = .line
        default: return nil
        }
    }

    /// .env key holding this channel's comma-separated allowlist.
    var allowedUsersEnvKey: String {
        switch self {
        case .wechat: return "FLUXION_WECHAT_ALLOWED_USERS"
        case .qqbot: return "FLUXION_QQBOT_ALLOWED_USERS"
        case .feishu: return "FLUXION_FEISHU_ALLOWED_USERS"
        case .telegram: return "FLUXION_TELEGRAM_ALLOWED_USERS"
        case .slack: return "FLUXION_SLACK_ALLOWED_USERS"
        case .line: return "FLUXION_LINE_ALLOWED_USERS"
        }
    }

    /// Index of this channel in the Messaging page segmented control
    /// (must match the label order in buildWindowIfNeeded).
    var messagingSegmentIndex: Int {
        switch self {
        case .slack: return 0
        case .telegram: return 1
        case .line: return 2
        case .qqbot: return 3
        case .wechat: return 4
        case .feishu: return 5
        }
    }
}

extension PreferencesWindow {

    // MARK: - Channel wiring

    /// The vertical stack that hosts this channel's pending-user rows.
    func pendingUsersStack(for channel: PendingUserChannel) -> NSStackView? {
        switch channel {
        case .wechat: return weChatPendingUsersStack
        case .qqbot: return qqbotPendingUsersStack
        case .feishu: return feishuPendingUsersStack
        case .telegram: return telegramPendingUsersStack
        case .slack: return slackPendingUsersStack
        case .line: return linePendingUsersStack
        }
    }

    /// The "Allowed Users" text field that Allow appends approved IDs to.
    func allowedUsersEntry(for channel: PendingUserChannel) -> NSTextField? {
        switch channel {
        case .wechat: return weChatAllowedUsersEntry
        case .qqbot: return qqbotAllowedUsersEntry
        case .feishu: return feishuAllowedUsersEntry
        case .telegram: return telegramAllowedUsersEntry
        case .slack: return slackAllowedUsersEntry
        case .line: return lineAllowedUsersEntry
        }
    }

    // MARK: - Rendering

    func rebuildPendingUsersStack(_ channel: PendingUserChannel) {
        guard let stack = pendingUsersStack(for: channel) else { return }
        for view in stack.arrangedSubviews {
            stack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }

        let pending = loadPendingUsers(channel)
        if pending.isEmpty {
            let empty = NSTextField(labelWithString: L10n.tr("preferences.pending.none", channel.displayName))
            empty.font = NSFont.systemFont(ofSize: 11.5)
            empty.textColor = Palette.secondaryText
            empty.isEditable = false
            empty.isSelectable = false
            empty.isBordered = false
            empty.drawsBackground = false
            stack.addArrangedSubview(empty)
            return
        }

        for (userId, user) in pending.sorted(by: { $0.value.lastSeenAt > $1.value.lastSeenAt }) {
            stack.addArrangedSubview(
                makePendingUserView(channel: channel, userId: userId, user: user)
            )
        }
    }

    func makePendingUserView(channel: PendingUserChannel, userId: String, user: PendingUser) -> NSView {
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 10
        row.translatesAutoresizingMaskIntoConstraints = false

        let labels = NSStackView()
        labels.orientation = .vertical
        labels.alignment = .leading
        labels.spacing = 2
        labels.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: userId)
        title.font = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .medium)
        title.textColor = Palette.primaryText
        title.lineBreakMode = .byTruncatingMiddle
        title.maximumNumberOfLines = 1

        let preview = user.lastMessagePreview.isEmpty ? L10n.tr("preferences.pending.no_preview") : user.lastMessagePreview
        let detail = NSTextField(labelWithString: L10n.tr("preferences.pending.detail", user.messageCount, preview))
        detail.font = NSFont.systemFont(ofSize: 11)
        detail.textColor = Palette.secondaryText
        detail.lineBreakMode = .byTruncatingTail
        detail.maximumNumberOfLines = 1

        labels.addArrangedSubview(title)
        labels.addArrangedSubview(detail)
        labels.setContentHuggingPriority(.defaultLow, for: .horizontal)

        let allow = PendingActionButton(
            title: L10n.tr("preferences.pending.allow"),
            target: self,
            action: #selector(allowPendingUser(_:))
        )
        allow.userId = userId
        allow.channel = channel
        allow.bezelStyle = .rounded
        allow.controlSize = .small

        let remove = PendingActionButton(
            title: L10n.tr("preferences.pending.remove"),
            target: self,
            action: #selector(removePendingUserClicked(_:))
        )
        remove.userId = userId
        remove.channel = channel
        remove.bezelStyle = .rounded
        remove.controlSize = .small

        row.addArrangedSubview(labels)
        row.addArrangedSubview(allow)
        row.addArrangedSubview(remove)

        labels.widthAnchor.constraint(greaterThanOrEqualToConstant: 260).isActive = true

        return row
    }

    // MARK: - Live refresh

    /// Watch the data directory so pending-user rows update while the window
    /// is open. The stores replace their JSON files atomically (rename into
    /// the directory), so a directory-level write event fires on every change;
    /// per-file mtimes then narrow the rebuild to the channels that changed.
    func startPendingUsersWatcher() {
        stopPendingUsersWatcher()
        let fd = open(appDelegate.dataDirPath, O_EVTONLY)
        guard fd >= 0 else {
            NSLog("FluxionMenu: failed to watch data dir for pending users: %@", appDelegate.dataDirPath)
            return
        }
        pendingUsersMtimes = currentPendingUsersMtimes()
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write],
            queue: .main
        )
        source.setEventHandler { [weak self] in
            self?.refreshPendingUsersIfChanged()
        }
        source.setCancelHandler {
            close(fd)
        }
        pendingUsersWatcher = source
        source.resume()
    }

    func stopPendingUsersWatcher() {
        pendingUsersWatcher?.cancel()
        pendingUsersWatcher = nil
    }

    private func refreshPendingUsersIfChanged() {
        let mtimes = currentPendingUsersMtimes()
        for channel in PendingUserChannel.allCases
        where mtimes[channel.fileName] != pendingUsersMtimes[channel.fileName] {
            rebuildPendingUsersStack(channel)
        }
        pendingUsersMtimes = mtimes
    }

    private func currentPendingUsersMtimes() -> [String: Date] {
        var mtimes: [String: Date] = [:]
        for channel in PendingUserChannel.allCases {
            let path = (appDelegate.dataDirPath as NSString).appendingPathComponent(channel.fileName)
            if let attrs = try? FileManager.default.attributesOfItem(atPath: path),
               let mtime = attrs[.modificationDate] as? Date {
                mtimes[channel.fileName] = mtime
            }
        }
        return mtimes
    }

    // MARK: - Persistence

    func loadPendingUsers(_ channel: PendingUserChannel) -> [String: PendingUser] {
        let path = (appDelegate.dataDirPath as NSString).appendingPathComponent(channel.fileName)
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let decoded = try? JSONDecoder().decode(PendingUsersFile.self, from: data) else {
            return [:]
        }
        return decoded.users
    }

    func savePendingUsers(_ users: [String: PendingUser], for channel: PendingUserChannel) {
        let path = (appDelegate.dataDirPath as NSString).appendingPathComponent(channel.fileName)
        let url = URL(fileURLWithPath: path)
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let data = try JSONEncoder().encode(PendingUsersFile(users: users))
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
        } catch {
            NSLog("FluxionMenu: failed to save %@ pending users: %@", channel.displayName, error.localizedDescription)
        }
    }

    // MARK: - Actions

    @objc func allowPendingUser(_ sender: PendingActionButton) {
        approvePendingUser(sender.userId, channel: sender.channel)
    }

    /// Append the user to the channel's allowlist and drop them from the
    /// pending list. Works with or without the Preferences window built, so
    /// the notification "Allow" action can share it with the UI button.
    func approvePendingUser(_ rawUserId: String, channel: PendingUserChannel) {
        let userId = rawUserId.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !userId.isEmpty else { return }

        // Only trust the text fields while the window is on screen: after a
        // close the stale controls linger until the next show() rebuild, and
        // autosave would write every field's old value back to .env.
        if window?.isVisible == true, let entry = allowedUsersEntry(for: channel) {
            // Window is visible: go through the text field + autosave so any
            // unsaved edits in the field are kept.
            var allowed = splitAllowedUsers(entry.stringValue)
            if !allowed.contains(userId) {
                allowed.append(userId)
                entry.stringValue = allowed.joined(separator: ",")
                autosave()
            }
        } else {
            // No window: update .env directly (saveEnv reloads envVals and
            // bounces the gateway, which re-reads the allowlist on startup).
            appDelegate.loadEnv()
            var allowed = splitAllowedUsers(appDelegate.envVals[channel.allowedUsersEnvKey] ?? "")
            if !allowed.contains(userId) {
                allowed.append(userId)
                appDelegate.saveEnv(updates: [
                    channel.allowedUsersEnvKey: allowed.joined(separator: ",")
                ])
            }
        }
        removePendingUser(userId, from: channel)
        notifyPendingUserAllowed(userId, channel: channel)
    }

    private func splitAllowedUsers(_ raw: String) -> [String] {
        return raw
            .split(separator: ",")
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    @objc func removePendingUserClicked(_ sender: PendingActionButton) {
        removePendingUser(sender.userId, from: sender.channel)
    }

    func removePendingUser(_ userId: String, from channel: PendingUserChannel) {
        var pending = loadPendingUsers(channel)
        pending.removeValue(forKey: userId)
        savePendingUsers(pending, for: channel)
        rebuildPendingUsersStack(channel)
    }

    func notifyPendingUserAllowed(_ userId: String, channel: PendingUserChannel) {
        let pythonBin = (appDelegate.repoPath as NSString).appendingPathComponent(".venv/bin/python")
        guard FileManager.default.fileExists(atPath: pythonBin) else { return }

        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            proc.arguments = [
                pythonBin,
                "-m",
                "fluxion.channels.approval_notify",
                "--channel",
                channel.key,
                userId,
                "--locale",
                L10n.pythonLocale,
            ]
            proc.currentDirectoryURL = URL(fileURLWithPath: self.appDelegate.repoPath)
            var env = ProcessInfo.processInfo.environment
            env["FLUXION_ENV_FILE"] = self.appDelegate.envPath
            proc.environment = env
            do {
                try proc.run()
                proc.waitUntilExit()
            } catch {
                NSLog(
                    "FluxionMenu: failed to send %@ approval notice: %@",
                    channel.displayName,
                    error.localizedDescription
                )
            }
        }
    }
}
