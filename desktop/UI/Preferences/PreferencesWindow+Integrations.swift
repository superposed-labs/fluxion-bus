import AppKit
import Foundation

// MARK: - Preferences messaging-integration sections
//
// Slack / Telegram / LINE / QQ / WeChat section builders, split out of
// PreferencesWindow+Sections.swift. Each is an independent feature with its
// own env keys and rows; they share only addSection() and the controls. show()
// still calls them in order. Section numbers are kept from the original order.
//
// The pending-user approval flow shared by WeChat / QQ / Feishu lives in
// PreferencesWindow+PendingUsers.swift.
extension PreferencesWindow {

    // MARK: - Section 7: Slack Integration
    func buildSlackSection(into documentStack: NSStackView) {
        checkSlackEnabled = NSSwitch()
        checkSlackEnabled.state = (appDelegate.envVals["FLUXION_SLACK_ENABLED"] ?? "true").lowercased() == "true" ? .on : .off
        checkSlackEnabled.target = self
        checkSlackEnabled.action = #selector(autosave)
        let slackEnableRow = CardRow(
            title: L10n.tr("integration.enable_slack.title"),
            desc: L10n.tr("integration.enable_slack.desc"),
            control: checkSlackEnabled,
            isFirst: true
        )

        slackBotTokenEntry = NSTextField()
        slackBotTokenEntry.stringValue = appDelegate.envVals["SLACK_BOT_TOKEN"] ?? ""
        slackBotTokenEntry.placeholderString = "xoxb-..."
        slackBotTokenEntry.bezelStyle = .roundedBezel
        slackBotTokenEntry.delegate = self
        let slackBotTokenRow = CardRowStacked(
            title: L10n.tr("integration.slack_bot_token.title"),
            desc: L10n.tr("integration.slack_bot_token.desc"),
            control: slackBotTokenEntry,
            isFirst: false
        )
        self.slackBotTokenRow = slackBotTokenRow

        slackAppTokenEntry = NSTextField()
        slackAppTokenEntry.stringValue = appDelegate.envVals["SLACK_APP_TOKEN"] ?? ""
        slackAppTokenEntry.placeholderString = "xapp-..."
        slackAppTokenEntry.bezelStyle = .roundedBezel
        slackAppTokenEntry.delegate = self
        let slackAppTokenRow = CardRowStacked(
            title: L10n.tr("integration.slack_app_token.title"),
            desc: L10n.tr("integration.slack_app_token.desc"),
            control: slackAppTokenEntry,
            isFirst: false
        )
        self.slackAppTokenRow = slackAppTokenRow

        slackSigningSecretEntry = NSTextField()
        slackSigningSecretEntry.stringValue = appDelegate.envVals["SLACK_SIGNING_SECRET"] ?? ""
        slackSigningSecretEntry.placeholderString = "Signing Secret"
        slackSigningSecretEntry.bezelStyle = .roundedBezel
        slackSigningSecretEntry.delegate = self
        let slackSigningSecretRow = CardRowStacked(
            title: L10n.tr("integration.slack_signing.title"),
            desc: L10n.tr("integration.slack_signing.desc"),
            control: slackSigningSecretEntry,
            isFirst: false
        )
        self.slackSigningSecretRow = slackSigningSecretRow

        slackAllowedUsersEntry = NSTextField()
        slackAllowedUsersEntry.stringValue = appDelegate.envVals["FLUXION_SLACK_ALLOWED_USERS"] ?? ""
        slackAllowedUsersEntry.placeholderString = "e.g. U0123ABCDEF,U0456GHIJKL (empty = deny everyone)"
        slackAllowedUsersEntry.bezelStyle = .roundedBezel
        slackAllowedUsersEntry.delegate = self
        let slackAllowedUsersRow = CardRowStacked(
            title: L10n.tr("integration.allowed_users.title", "FLUXION_SLACK_ALLOWED_USERS"),
            desc: L10n.tr("integration.slack_allowed.desc"),
            control: slackAllowedUsersEntry,
            isFirst: false
        )
        self.slackAllowedUsersRow = slackAllowedUsersRow

        slackPendingUsersStack = NSStackView()
        slackPendingUsersStack.orientation = .vertical
        slackPendingUsersStack.alignment = .leading
        slackPendingUsersStack.spacing = 8
        rebuildPendingUsersStack(.slack)
        let slackPendingUsersRow = CardRowStacked(
            title: L10n.tr("integration.pending_users.title"),
            desc: L10n.tr("integration.slack_pending.desc"),
            control: slackPendingUsersStack,
            isFirst: false
        )
        self.slackPendingUsersRow = slackPendingUsersRow

        slackChannelEntry = NSTextField()
        slackChannelEntry.stringValue = appDelegate.envVals["FLUXION_SCHEDULER_SLACK_CHANNEL"] ?? ""
        slackChannelEntry.placeholderString = "e.g. #alerts, C12345, or U12345"
        slackChannelEntry.bezelStyle = .roundedBezel
        slackChannelEntry.delegate = self
        let slackChannelRow = CardRowStacked(
            title: L10n.tr("integration.notification_destination.title"),
            desc: L10n.tr("integration.notification_destination.desc"),
            control: slackChannelEntry,
            isFirst: false
        )
        self.slackChannelRow = slackChannelRow

        let slackHintLabel = NSTextField(labelWithString: L10n.tr("integration.disabled"))
        slackHintLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        slackHintLabel.textColor = Palette.secondaryText
        slackHintLabel.isEditable = false
        slackHintLabel.isSelectable = false
        slackHintLabel.isBordered = false
        slackHintLabel.drawsBackground = false
        let slackHintRow = CardRow(
            title: L10n.tr("integration.slack_disabled.title"),
            desc: L10n.tr("integration.slack_disabled.desc"),
            control: slackHintLabel,
            isFirst: false
        )
        self.slackHintRow = slackHintRow

        self.slackSectionStack = addSection(title: L10n.tr("integration.slack.section"), rows: [slackEnableRow, slackBotTokenRow, slackAppTokenRow, slackSigningSecretRow, slackAllowedUsersRow, slackPendingUsersRow, slackChannelRow, slackHintRow], into: documentStack)
    }

    // MARK: - Section 8: Telegram Integration
    func buildTelegramSection(into documentStack: NSStackView) {
        checkTelegram = NSSwitch()
        checkTelegram.state = (appDelegate.envVals["FLUXION_TELEGRAM_ENABLED"] ?? "false").lowercased() == "true" ? .on : .off
        checkTelegram.target = self
        checkTelegram.action = #selector(autosave)
        let telegramEnableRow = CardRow(
            title: L10n.tr("integration.enable_telegram.title"),
            desc: L10n.tr("integration.enable_telegram.desc"),
            control: checkTelegram,
            isFirst: true
        )

        telegramBotTokenEntry = NSTextField()
        telegramBotTokenEntry.stringValue = appDelegate.envVals["TELEGRAM_BOT_TOKEN"] ?? ""
        telegramBotTokenEntry.placeholderString = "123456:ABC-..."
        telegramBotTokenEntry.bezelStyle = .roundedBezel
        telegramBotTokenEntry.delegate = self
        let telegramBotTokenRow = CardRowStacked(
            title: L10n.tr("integration.telegram_token.title"),
            desc: L10n.tr("integration.telegram_token.desc"),
            control: telegramBotTokenEntry,
            isFirst: false
        )
        self.telegramBotTokenRow = telegramBotTokenRow

        telegramAllowedUsersEntry = NSTextField()
        telegramAllowedUsersEntry.stringValue = appDelegate.envVals["FLUXION_TELEGRAM_ALLOWED_USERS"] ?? ""
        telegramAllowedUsersEntry.placeholderString = "e.g. 8636824255,123456789 (empty = deny everyone)"
        telegramAllowedUsersEntry.bezelStyle = .roundedBezel
        telegramAllowedUsersEntry.delegate = self
        let telegramAllowedUsersRow = CardRowStacked(
            title: L10n.tr("integration.allowed_users.title", "FLUXION_TELEGRAM_ALLOWED_USERS"),
            desc: L10n.tr("integration.telegram_allowed.desc"),
            control: telegramAllowedUsersEntry,
            isFirst: false
        )
        self.telegramAllowedUsersRow = telegramAllowedUsersRow

        telegramPendingUsersStack = NSStackView()
        telegramPendingUsersStack.orientation = .vertical
        telegramPendingUsersStack.alignment = .leading
        telegramPendingUsersStack.spacing = 8
        rebuildPendingUsersStack(.telegram)
        let telegramPendingUsersRow = CardRowStacked(
            title: L10n.tr("integration.pending_users.title"),
            desc: L10n.tr("integration.telegram_pending.desc"),
            control: telegramPendingUsersStack,
            isFirst: false
        )
        self.telegramPendingUsersRow = telegramPendingUsersRow

        telegramWorkspaceEntry = NSTextField()
        telegramWorkspaceEntry.stringValue = appDelegate.envVals["FLUXION_TELEGRAM_DEFAULT_WORKSPACE"] ?? ""
        telegramWorkspaceEntry.placeholderString = "e.g. /Users/you/Projects/MyApp (falls back to workspace root)"
        telegramWorkspaceEntry.bezelStyle = .roundedBezel
        telegramWorkspaceEntry.delegate = self
        let telegramWorkspaceRow = CardRowStacked(
            title: L10n.tr("integration.default_workspace.title", "FLUXION_TELEGRAM_DEFAULT_WORKSPACE"),
            desc: L10n.tr("integration.telegram_workspace.desc"),
            control: telegramWorkspaceEntry,
            isFirst: false
        )
        self.telegramWorkspaceRow = telegramWorkspaceRow

        let telegramHintLabel = NSTextField(labelWithString: L10n.tr("integration.disabled"))
        telegramHintLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        telegramHintLabel.textColor = Palette.secondaryText
        telegramHintLabel.isEditable = false
        telegramHintLabel.isSelectable = false
        telegramHintLabel.isBordered = false
        telegramHintLabel.drawsBackground = false
        let telegramHintRow = CardRow(
            title: L10n.tr("integration.telegram_disabled.title"),
            desc: L10n.tr("integration.telegram_disabled.desc"),
            control: telegramHintLabel,
            isFirst: false
        )
        self.telegramHintRow = telegramHintRow

        self.telegramSectionStack = addSection(title: L10n.tr("integration.telegram.section"), rows: [telegramEnableRow, telegramBotTokenRow, telegramAllowedUsersRow, telegramPendingUsersRow, telegramWorkspaceRow, telegramHintRow], into: documentStack)
    }

    // MARK: - Section 9: WeChat Integration
    func buildWeChatSection(into documentStack: NSStackView) {
        checkWeChat = NSSwitch()
        checkWeChat.state = (appDelegate.envVals["FLUXION_WECHAT_ENABLED"] ?? "false").lowercased() == "true" ? .on : .off
        checkWeChat.target = self
        checkWeChat.action = #selector(autosave)
        let weChatEnableRow = CardRow(
            title: L10n.tr("integration.enable_wechat.title"),
            desc: L10n.tr("integration.enable_wechat.desc"),
            control: checkWeChat,
            isFirst: true
        )

        let weChatLoginButton = NSButton(
            title: L10n.tr("integration.wechat_login.button"),
            target: self,
            action: #selector(openWeChatLogin)
        )
        weChatLoginButton.bezelStyle = .rounded
        let weChatLoginRow = CardRow(
            title: L10n.tr("integration.wechat_binding.title"),
            desc: L10n.tr("integration.wechat_binding.desc"),
            control: weChatLoginButton,
            isFirst: false
        )
        self.weChatLoginRow = weChatLoginRow

        weChatAllowedUsersEntry = NSTextField()
        weChatAllowedUsersEntry.stringValue = appDelegate.envVals["FLUXION_WECHAT_ALLOWED_USERS"] ?? ""
        weChatAllowedUsersEntry.placeholderString = "Comma-separated iLink user IDs; changes apply automatically"
        weChatAllowedUsersEntry.bezelStyle = .roundedBezel
        weChatAllowedUsersEntry.delegate = self
        let weChatAllowedUsersRow = CardRowStacked(
            title: L10n.tr("integration.allowed_users.title", "FLUXION_WECHAT_ALLOWED_USERS"),
            desc: L10n.tr("integration.wechat_allowed.desc"),
            control: weChatAllowedUsersEntry,
            isFirst: false
        )
        self.weChatAllowedUsersRow = weChatAllowedUsersRow

        weChatPendingUsersStack = NSStackView()
        weChatPendingUsersStack.orientation = .vertical
        weChatPendingUsersStack.alignment = .leading
        weChatPendingUsersStack.spacing = 8
        rebuildPendingUsersStack(.wechat)
        let weChatPendingUsersRow = CardRowStacked(
            title: L10n.tr("integration.pending_users.title"),
            desc: L10n.tr("integration.wechat_pending.desc"),
            control: weChatPendingUsersStack,
            isFirst: false
        )
        self.weChatPendingUsersRow = weChatPendingUsersRow

        weChatWorkspaceEntry = NSTextField()
        weChatWorkspaceEntry.stringValue = appDelegate.envVals["FLUXION_WECHAT_DEFAULT_WORKSPACE"] ?? ""
        weChatWorkspaceEntry.placeholderString = "e.g. /Users/you/Projects/MyApp (falls back to workspace root)"
        weChatWorkspaceEntry.bezelStyle = .roundedBezel
        weChatWorkspaceEntry.delegate = self
        let weChatWorkspaceRow = CardRowStacked(
            title: L10n.tr("integration.default_workspace.title", "FLUXION_WECHAT_DEFAULT_WORKSPACE"),
            desc: L10n.tr("integration.wechat_workspace.desc"),
            control: weChatWorkspaceEntry,
            isFirst: false
        )
        self.weChatWorkspaceRow = weChatWorkspaceRow

        weChatMessageMaxCharsEntry = NSTextField()
        weChatMessageMaxCharsEntry.stringValue = appDelegate.envVals["FLUXION_WECHAT_MESSAGE_MAX_CHARS"] ?? "4096"
        weChatMessageMaxCharsEntry.placeholderString = "4096"
        weChatMessageMaxCharsEntry.bezelStyle = .roundedBezel
        weChatMessageMaxCharsEntry.delegate = self
        let weChatMessageMaxCharsRow = CardRowStacked(
            title: L10n.tr("integration.wechat_limit.title"),
            desc: L10n.tr("integration.wechat_limit.desc"),
            control: weChatMessageMaxCharsEntry,
            isFirst: false
        )
        self.weChatMessageMaxCharsRow = weChatMessageMaxCharsRow

        weChatTypingHeartbeatEntry = NSTextField()
        weChatTypingHeartbeatEntry.stringValue = appDelegate.envVals["FLUXION_WECHAT_TYPING_HEARTBEAT_SEC"] ?? "8"
        weChatTypingHeartbeatEntry.placeholderString = "8"
        weChatTypingHeartbeatEntry.bezelStyle = .roundedBezel
        weChatTypingHeartbeatEntry.delegate = self
        let weChatTypingHeartbeatRow = CardRowStacked(
            title: L10n.tr("integration.wechat_typing.title"),
            desc: L10n.tr("integration.wechat_typing.desc"),
            control: weChatTypingHeartbeatEntry,
            isFirst: false
        )
        self.weChatTypingHeartbeatRow = weChatTypingHeartbeatRow

        let weChatHintLabel = NSTextField(labelWithString: L10n.tr("integration.disabled"))
        weChatHintLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        weChatHintLabel.textColor = Palette.secondaryText
        weChatHintLabel.isEditable = false
        weChatHintLabel.isSelectable = false
        weChatHintLabel.isBordered = false
        weChatHintLabel.drawsBackground = false
        let weChatHintRow = CardRow(
            title: L10n.tr("integration.wechat_disabled.title"),
            desc: L10n.tr("integration.wechat_disabled.desc"),
            control: weChatHintLabel,
            isFirst: false
        )
        self.weChatHintRow = weChatHintRow

        self.weChatSectionStack = addSection(title: L10n.tr("integration.wechat.section"), rows: [
            weChatEnableRow,
            weChatLoginRow,
            weChatAllowedUsersRow,
            weChatPendingUsersRow,
            weChatWorkspaceRow,
            weChatMessageMaxCharsRow,
            weChatTypingHeartbeatRow,
            weChatHintRow
        ], into: documentStack)
    }

    // MARK: - Section 9.5: LINE Integration
    func buildLineSection(into documentStack: NSStackView) {
        checkLine = NSSwitch()
        checkLine.state = (appDelegate.envVals["FLUXION_LINE_ENABLED"] ?? "false").lowercased() == "true" ? .on : .off
        checkLine.target = self
        checkLine.action = #selector(autosave)
        let lineEnableRow = CardRow(
            title: L10n.tr("integration.enable_line.title"),
            desc: L10n.tr("integration.enable_line.desc"),
            control: checkLine,
            isFirst: true
        )

        let lineWebhookUrlLabel = NSTextField(labelWithString: "https://<your-tunnel-domain>/line/webhook")
        lineWebhookUrlLabel.font = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
        lineWebhookUrlLabel.textColor = Palette.primaryText
        lineWebhookUrlLabel.isSelectable = true
        lineWebhookUrlLabel.isEditable = false
        lineWebhookUrlLabel.isBordered = false
        lineWebhookUrlLabel.drawsBackground = false
        lineWebhookUrlLabel.translatesAutoresizingMaskIntoConstraints = false
        let lineWebhookUrlRow = CardRow(
            title: L10n.tr("integration.webhook_url.title"),
            desc: L10n.tr("integration.webhook_url.desc"),
            control: lineWebhookUrlLabel,
            isFirst: false
        )
        self.lineWebhookUrlRow = lineWebhookUrlRow

        lineChannelSecretEntry = NSTextField()
        lineChannelSecretEntry.stringValue = appDelegate.envVals["LINE_CHANNEL_SECRET"] ?? ""
        lineChannelSecretEntry.placeholderString = "Channel Secret"
        lineChannelSecretEntry.bezelStyle = .roundedBezel
        lineChannelSecretEntry.delegate = self
        let lineChannelSecretRow = CardRowStacked(
            title: L10n.tr("integration.line_secret.title"),
            desc: L10n.tr("integration.line_secret.desc"),
            control: lineChannelSecretEntry,
            isFirst: false
        )
        self.lineChannelSecretRow = lineChannelSecretRow

        lineChannelAccessTokenEntry = NSTextField()
        lineChannelAccessTokenEntry.stringValue = appDelegate.envVals["LINE_CHANNEL_ACCESS_TOKEN"] ?? ""
        lineChannelAccessTokenEntry.placeholderString = "Long-lived Access Token"
        lineChannelAccessTokenEntry.bezelStyle = .roundedBezel
        lineChannelAccessTokenEntry.delegate = self
        let lineChannelAccessTokenRow = CardRowStacked(
            title: L10n.tr("integration.line_token.title"),
            desc: L10n.tr("integration.line_token.desc"),
            control: lineChannelAccessTokenEntry,
            isFirst: false
        )
        self.lineChannelAccessTokenRow = lineChannelAccessTokenRow

        lineAllowedUsersEntry = NSTextField()
        lineAllowedUsersEntry.stringValue = appDelegate.envVals["FLUXION_LINE_ALLOWED_USERS"] ?? ""
        lineAllowedUsersEntry.placeholderString = "e.g. U123...,U456... (empty = deny everyone)"
        lineAllowedUsersEntry.bezelStyle = .roundedBezel
        lineAllowedUsersEntry.delegate = self
        let lineAllowedUsersRow = CardRowStacked(
            title: L10n.tr("integration.allowed_users.title", "FLUXION_LINE_ALLOWED_USERS"),
            desc: L10n.tr("integration.line_allowed.desc"),
            control: lineAllowedUsersEntry,
            isFirst: false
        )
        self.lineAllowedUsersRow = lineAllowedUsersRow

        linePendingUsersStack = NSStackView()
        linePendingUsersStack.orientation = .vertical
        linePendingUsersStack.alignment = .leading
        linePendingUsersStack.spacing = 8
        rebuildPendingUsersStack(.line)
        let linePendingUsersRow = CardRowStacked(
            title: L10n.tr("integration.pending_users.title"),
            desc: L10n.tr("integration.line_pending.desc"),
            control: linePendingUsersStack,
            isFirst: false
        )
        self.linePendingUsersRow = linePendingUsersRow

        lineWorkspaceEntry = NSTextField()
        lineWorkspaceEntry.stringValue = appDelegate.envVals["FLUXION_LINE_DEFAULT_WORKSPACE"] ?? ""
        lineWorkspaceEntry.placeholderString = "e.g. /Users/you/Projects/MyApp (falls back to workspace root)"
        lineWorkspaceEntry.bezelStyle = .roundedBezel
        lineWorkspaceEntry.delegate = self
        let lineWorkspaceRow = CardRowStacked(
            title: L10n.tr("integration.default_workspace.title", "FLUXION_LINE_DEFAULT_WORKSPACE"),
            desc: L10n.tr("integration.line_workspace.desc"),
            control: lineWorkspaceEntry,
            isFirst: false
        )
        self.lineWorkspaceRow = lineWorkspaceRow

        let lineHintLabel = NSTextField(labelWithString: L10n.tr("integration.disabled"))
        lineHintLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        lineHintLabel.textColor = Palette.secondaryText
        lineHintLabel.isEditable = false
        lineHintLabel.isSelectable = false
        lineHintLabel.isBordered = false
        lineHintLabel.drawsBackground = false
        let lineHintRow = CardRow(
            title: L10n.tr("integration.line_disabled.title"),
            desc: L10n.tr("integration.line_disabled.desc"),
            control: lineHintLabel,
            isFirst: false
        )
        self.lineHintRow = lineHintRow

        self.lineSectionStack = addSection(title: L10n.tr("integration.line.section"), rows: [
            lineEnableRow,
            lineWebhookUrlRow,
            lineChannelSecretRow,
            lineChannelAccessTokenRow,
            lineAllowedUsersRow,
            linePendingUsersRow,
            lineWorkspaceRow,
            lineHintRow
        ], into: documentStack)
    }

    // MARK: - QQ Integration

    func buildQQBotSection(into documentStack: NSStackView) {
        checkQQBot = NSSwitch()
        checkQQBot.state = (appDelegate.envVals["FLUXION_QQBOT_ENABLED"] ?? "false").lowercased() == "true" ? .on : .off
        checkQQBot.target = self
        checkQQBot.action = #selector(autosave)
        let qqbotEnableRow = CardRow(
            title: L10n.tr("integration.enable_qq.title"),
            desc: L10n.tr("integration.enable_qq.desc"),
            control: checkQQBot,
            isFirst: true
        )

        qqbotTransportPopup = NSPopUpButton(frame: .zero, pullsDown: false)
        qqbotTransportPopup.addItems(withTitles: ["websocket", "webhook"])
        let savedTransport = (appDelegate.envVals["FLUXION_QQBOT_TRANSPORT"] ?? "websocket").lowercased()
        qqbotTransportPopup.selectItem(withTitle: savedTransport == "webhook" ? "webhook" : "websocket")
        qqbotTransportPopup.target = self
        qqbotTransportPopup.action = #selector(autosave)
        let qqbotTransportRow = CardRowStacked(
            title: L10n.tr("integration.transport.title"),
            desc: L10n.tr("integration.transport.desc"),
            control: qqbotTransportPopup,
            isFirst: false
        )
        self.qqbotTransportRow = qqbotTransportRow

        qqbotAppIdEntry = NSTextField()
        qqbotAppIdEntry.stringValue = appDelegate.envVals["QQBOT_APP_ID"] ?? ""
        qqbotAppIdEntry.placeholderString = "AppID (机器人ID)"
        qqbotAppIdEntry.bezelStyle = .roundedBezel
        qqbotAppIdEntry.delegate = self
        let qqbotAppIdRow = CardRowStacked(
            title: L10n.tr("integration.app_id.title", "QQBOT_APP_ID"),
            desc: L10n.tr("integration.qq_app_id.desc"),
            control: qqbotAppIdEntry,
            isFirst: false
        )
        self.qqbotAppIdRow = qqbotAppIdRow

        qqbotSecretEntry = NSTextField()
        qqbotSecretEntry.stringValue = appDelegate.envVals["QQBOT_CLIENT_SECRET"] ?? ""
        qqbotSecretEntry.placeholderString = "AppSecret (机器人密钥), not the Token"
        qqbotSecretEntry.bezelStyle = .roundedBezel
        qqbotSecretEntry.delegate = self
        let qqbotSecretRow = CardRowStacked(
            title: L10n.tr("integration.app_secret.title", "QQBOT_CLIENT_SECRET"),
            desc: L10n.tr("integration.qq_secret.desc"),
            control: qqbotSecretEntry,
            isFirst: false
        )
        self.qqbotSecretRow = qqbotSecretRow

        checkQQBotSandbox = NSSwitch()
        checkQQBotSandbox.state = (appDelegate.envVals["FLUXION_QQBOT_SANDBOX"] ?? "false").lowercased() == "true" ? .on : .off
        checkQQBotSandbox.target = self
        checkQQBotSandbox.action = #selector(autosave)
        let qqbotSandboxRow = CardRow(
            title: L10n.tr("integration.sandbox.title"),
            desc: L10n.tr("integration.sandbox.desc"),
            control: checkQQBotSandbox,
            isFirst: false
        )
        self.qqbotSandboxRow = qqbotSandboxRow

        checkQQBotGroupChat = NSSwitch()
        checkQQBotGroupChat.state = (appDelegate.envVals["FLUXION_QQBOT_ALLOW_GROUP_CHAT"] ?? "true").lowercased() == "true" ? .on : .off
        checkQQBotGroupChat.target = self
        checkQQBotGroupChat.action = #selector(autosave)
        let qqbotGroupChatRow = CardRow(
            title: L10n.tr("integration.allow_group.title", "FLUXION_QQBOT_ALLOW_GROUP_CHAT"),
            desc: L10n.tr("integration.qq_group.desc"),
            control: checkQQBotGroupChat,
            isFirst: false
        )
        self.qqbotGroupChatRow = qqbotGroupChatRow

        qqbotAllowedUsersEntry = NSTextField()
        qqbotAllowedUsersEntry.stringValue = appDelegate.envVals["FLUXION_QQBOT_ALLOWED_USERS"] ?? ""
        qqbotAllowedUsersEntry.placeholderString = "e.g. EC4B...,A91F... (empty = deny all, fail-closed)"
        qqbotAllowedUsersEntry.bezelStyle = .roundedBezel
        qqbotAllowedUsersEntry.delegate = self
        let qqbotAllowedUsersRow = CardRowStacked(
            title: L10n.tr("integration.allowed_users.title", "FLUXION_QQBOT_ALLOWED_USERS"),
            desc: L10n.tr("integration.qq_allowed.desc"),
            control: qqbotAllowedUsersEntry,
            isFirst: false
        )
        self.qqbotAllowedUsersRow = qqbotAllowedUsersRow

        qqbotPendingUsersStack = NSStackView()
        qqbotPendingUsersStack.orientation = .vertical
        qqbotPendingUsersStack.alignment = .leading
        qqbotPendingUsersStack.spacing = 8
        rebuildPendingUsersStack(.qqbot)
        let qqbotPendingUsersRow = CardRowStacked(
            title: L10n.tr("integration.pending_users.title"),
            desc: L10n.tr("integration.qq_pending.desc"),
            control: qqbotPendingUsersStack,
            isFirst: false
        )
        self.qqbotPendingUsersRow = qqbotPendingUsersRow

        qqbotWorkspaceEntry = NSTextField()
        qqbotWorkspaceEntry.stringValue = appDelegate.envVals["FLUXION_QQBOT_DEFAULT_WORKSPACE"] ?? ""
        qqbotWorkspaceEntry.placeholderString = "e.g. /Users/you/Projects/MyApp (falls back to workspace root)"
        qqbotWorkspaceEntry.bezelStyle = .roundedBezel
        qqbotWorkspaceEntry.delegate = self
        let qqbotWorkspaceRow = CardRowStacked(
            title: L10n.tr("integration.default_workspace.title", "FLUXION_QQBOT_DEFAULT_WORKSPACE"),
            desc: L10n.tr("integration.qq_workspace.desc"),
            control: qqbotWorkspaceEntry,
            isFirst: false
        )
        self.qqbotWorkspaceRow = qqbotWorkspaceRow

        let qqbotHintLabel = NSTextField(labelWithString: L10n.tr("integration.disabled"))
        qqbotHintLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        qqbotHintLabel.textColor = Palette.secondaryText
        qqbotHintLabel.isEditable = false
        qqbotHintLabel.isSelectable = false
        qqbotHintLabel.isBordered = false
        qqbotHintLabel.drawsBackground = false
        let qqbotHintRow = CardRow(
            title: L10n.tr("integration.qq_disabled.title"),
            desc: L10n.tr("integration.qq_disabled.desc"),
            control: qqbotHintLabel,
            isFirst: false
        )
        self.qqbotHintRow = qqbotHintRow

        self.qqbotSectionStack = addSection(title: L10n.tr("integration.qq.section"), rows: [
            qqbotEnableRow,
            qqbotTransportRow,
            qqbotAppIdRow,
            qqbotSecretRow,
            qqbotSandboxRow,
            qqbotGroupChatRow,
            qqbotAllowedUsersRow,
            qqbotPendingUsersRow,
            qqbotWorkspaceRow,
            qqbotHintRow
        ], into: documentStack)
    }

    // MARK: - Feishu Integration

    func buildFeishuSection(into documentStack: NSStackView) {
        checkFeishu = NSSwitch()
        checkFeishu.state = (appDelegate.envVals["FLUXION_FEISHU_ENABLED"] ?? "false").lowercased() == "true" ? .on : .off
        checkFeishu.target = self
        checkFeishu.action = #selector(autosave)
        let feishuEnableRow = CardRow(
            title: L10n.tr("integration.enable_feishu.title"),
            desc: L10n.tr("integration.enable_feishu.desc"),
            control: checkFeishu,
            isFirst: true
        )

        feishuAppIdEntry = NSTextField()
        feishuAppIdEntry.stringValue = appDelegate.envVals["FEISHU_APP_ID"] ?? ""
        feishuAppIdEntry.placeholderString = "App ID (cli_...)"
        feishuAppIdEntry.bezelStyle = .roundedBezel
        feishuAppIdEntry.delegate = self
        let feishuAppIdRow = CardRowStacked(
            title: L10n.tr("integration.app_id.title", "FEISHU_APP_ID"),
            desc: L10n.tr("integration.feishu_app_id.desc"),
            control: feishuAppIdEntry,
            isFirst: false
        )
        self.feishuAppIdRow = feishuAppIdRow

        feishuSecretEntry = NSTextField()
        feishuSecretEntry.stringValue = appDelegate.envVals["FEISHU_APP_SECRET"] ?? ""
        feishuSecretEntry.placeholderString = "App Secret"
        feishuSecretEntry.bezelStyle = .roundedBezel
        feishuSecretEntry.delegate = self
        let feishuSecretRow = CardRowStacked(
            title: L10n.tr("integration.app_secret.title", "FEISHU_APP_SECRET"),
            desc: L10n.tr("integration.feishu_secret.desc"),
            control: feishuSecretEntry,
            isFirst: false
        )
        self.feishuSecretRow = feishuSecretRow

        checkFeishuGroupChat = NSSwitch()
        checkFeishuGroupChat.state = (appDelegate.envVals["FLUXION_FEISHU_ALLOW_GROUP_CHAT"] ?? "true").lowercased() == "true" ? .on : .off
        checkFeishuGroupChat.target = self
        checkFeishuGroupChat.action = #selector(autosave)
        let feishuGroupChatRow = CardRow(
            title: L10n.tr("integration.allow_group.title", "FLUXION_FEISHU_ALLOW_GROUP_CHAT"),
            desc: L10n.tr("integration.feishu_group.desc"),
            control: checkFeishuGroupChat,
            isFirst: false
        )
        self.feishuGroupChatRow = feishuGroupChatRow

        feishuAllowedUsersEntry = NSTextField()
        feishuAllowedUsersEntry.stringValue = appDelegate.envVals["FLUXION_FEISHU_ALLOWED_USERS"] ?? ""
        feishuAllowedUsersEntry.placeholderString = "e.g. ou_abc...,ou_def... (empty = deny all, fail-closed)"
        feishuAllowedUsersEntry.bezelStyle = .roundedBezel
        feishuAllowedUsersEntry.delegate = self
        let feishuAllowedUsersRow = CardRowStacked(
            title: L10n.tr("integration.allowed_users.title", "FLUXION_FEISHU_ALLOWED_USERS"),
            desc: L10n.tr("integration.feishu_allowed.desc"),
            control: feishuAllowedUsersEntry,
            isFirst: false
        )
        self.feishuAllowedUsersRow = feishuAllowedUsersRow

        feishuPendingUsersStack = NSStackView()
        feishuPendingUsersStack.orientation = .vertical
        feishuPendingUsersStack.alignment = .leading
        feishuPendingUsersStack.spacing = 8
        rebuildPendingUsersStack(.feishu)
        let feishuPendingUsersRow = CardRowStacked(
            title: L10n.tr("integration.pending_users.title"),
            desc: L10n.tr("integration.feishu_pending.desc"),
            control: feishuPendingUsersStack,
            isFirst: false
        )
        self.feishuPendingUsersRow = feishuPendingUsersRow

        feishuWorkspaceEntry = NSTextField()
        feishuWorkspaceEntry.stringValue = appDelegate.envVals["FLUXION_FEISHU_DEFAULT_WORKSPACE"] ?? ""
        feishuWorkspaceEntry.placeholderString = "e.g. /Users/you/Projects/MyApp (falls back to workspace root)"
        feishuWorkspaceEntry.bezelStyle = .roundedBezel
        feishuWorkspaceEntry.delegate = self
        let feishuWorkspaceRow = CardRowStacked(
            title: L10n.tr("integration.default_workspace.title", "FLUXION_FEISHU_DEFAULT_WORKSPACE"),
            desc: L10n.tr("integration.feishu_workspace.desc"),
            control: feishuWorkspaceEntry,
            isFirst: false
        )
        self.feishuWorkspaceRow = feishuWorkspaceRow

        let feishuHintLabel = NSTextField(labelWithString: L10n.tr("integration.disabled"))
        feishuHintLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        feishuHintLabel.textColor = Palette.secondaryText
        feishuHintLabel.isEditable = false
        feishuHintLabel.isSelectable = false
        feishuHintLabel.isBordered = false
        feishuHintLabel.drawsBackground = false
        let feishuHintRow = CardRow(
            title: L10n.tr("integration.feishu_disabled.title"),
            desc: L10n.tr("integration.feishu_disabled.desc"),
            control: feishuHintLabel,
            isFirst: false
        )
        self.feishuHintRow = feishuHintRow

        self.feishuSectionStack = addSection(title: L10n.tr("integration.feishu.section"), rows: [
            feishuEnableRow,
            feishuAppIdRow,
            feishuSecretRow,
            feishuGroupChatRow,
            feishuAllowedUsersRow,
            feishuPendingUsersRow,
            feishuWorkspaceRow,
            feishuHintRow
        ], into: documentStack)
    }

    // MARK: - Gateway status banner (messaging page)

    /// Per-channel activation signals: the enabled-flag key with the same
    /// default the section builders use, plus the credential keys whose
    /// first fill-in counts as "user wants this channel live". WeChat logs in
    /// by QR scan and has no credential fields, so its toggle is the only
    /// signal; Slack's toggle defaults to on, so a fresh install that fills
    /// in tokens never flips it — hence the credential transition.
    private static let channelActivationSpecs: [(enabledKey: String, defaultValue: String, credentialKeys: [String])] = [
        ("FLUXION_SLACK_ENABLED", "true", ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]),
        ("FLUXION_TELEGRAM_ENABLED", "false", ["TELEGRAM_BOT_TOKEN"]),
        ("FLUXION_WECHAT_ENABLED", "false", []),
        ("FLUXION_LINE_ENABLED", "false", ["LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"]),
        ("FLUXION_QQBOT_ENABLED", "false", ["QQBOT_APP_ID", "QQBOT_CLIENT_SECRET"]),
        ("FLUXION_FEISHU_ENABLED", "false", ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]),
    ]

    /// True when this save turns a channel on, or supplies credentials to an
    /// already-on channel for the first time — the two moments a user clearly
    /// expects messaging to start working.
    func channelActivationRequested(previous: [String: String], updates: [String: String]) -> Bool {
        for spec in Self.channelActivationSpecs {
            guard updates[spec.enabledKey] == "true" else { continue }
            let wasOn = (previous[spec.enabledKey] ?? spec.defaultValue).lowercased() == "true"
            if !wasOn { return true }
            for key in spec.credentialKeys {
                let hadValue = !(previous[key] ?? "").trimmingCharacters(in: .whitespaces).isEmpty
                let hasValue = !(updates[key] ?? "").isEmpty
                if hasValue && !hadValue { return true }
            }
        }
        return false
    }

    /// Quiet-unless-actionable warning at the top of the messaging page:
    /// visible only when a channel is enabled but the gateway isn't running —
    /// i.e. it crashed, was killed by hand, or the channels were configured
    /// outside this window so autosave()'s auto-start never fired. The happy
    /// path (enabling a channel here) starts the gateway automatically, so
    /// new users never see this card.
    func buildGatewayStatusBanner(into documentStack: NSStackView) {
        gatewayActionButton = NSButton(
            title: L10n.tr("integration.gateway.start"),
            target: self,
            action: #selector(gatewayActionClicked)
        )
        gatewayActionButton.bezelStyle = .rounded

        let row = CardRow(
            title: L10n.tr("integration.gateway.stopped.title"),
            desc: L10n.tr("integration.gateway.stopped.desc"),
            control: gatewayActionButton,
            isFirst: true
        )
        row.titleLabel.textColor = .systemOrange

        let card = CardView()
        card.stackView.addArrangedSubview(row)
        row.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true
        // Hidden until the first background probe says otherwise, so the
        // common case never flashes a warning.
        card.isHidden = true
        documentStack.addArrangedSubview(card)
        card.widthAnchor.constraint(equalTo: documentStack.widthAnchor, constant: -44).isActive = true
        gatewayBannerCard = card

        gatewayDownProbeCount = 0
        gatewayFollowUpScheduled = false
        refreshGatewayBanner()
    }

    /// Re-check the gateway process off the main thread and show/hide the
    /// banner. `afterDelay` lets callers wait out an in-flight service bounce.
    func refreshGatewayBanner(afterDelay delay: TimeInterval = 0) {
        guard gatewayBannerCard != nil else { return }
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self else { return }
            let running = self.appDelegate.isGatewayRunning()
            DispatchQueue.main.async { self.applyGatewayBannerState(running: running) }
        }
    }

    private func applyGatewayBannerState(running: Bool) {
        gatewayFollowUpScheduled = false
        guard let card = gatewayBannerCard else { return }
        let wasHidden = card.isHidden
        let anyChannelEnabled = (checkSlackEnabled?.state == .on)
            || (checkTelegram?.state == .on)
            || (checkWeChat?.state == .on)
            || (checkLine?.state == .on)
            || (checkQQBot?.state == .on)
            || (checkFeishu?.state == .on)
        if running || !anyChannelEnabled {
            gatewayDownProbeCount = 0
            card.isHidden = true
            if wasHidden != card.isHidden {
                window?.contentView?.layoutSubtreeIfNeeded()
            }
            return
        }
        if Date() < gatewayBounceGraceUntil {
            // Mid-bounce: the process being down is expected, so don't alarm
            // (and don't reset an in-flight "Starting…" button). Check again
            // once the grace window has passed.
            scheduleGatewayFollowUp(after: gatewayBounceGraceUntil.timeIntervalSinceNow + 0.5)
            return
        }
        // Require several consecutive down-probes before alarming: right
        // after app launch the gateway legitimately takes a few seconds to
        // spawn, and a window opened in that gap must not stick a warning.
        gatewayDownProbeCount += 1
        if gatewayDownProbeCount >= 3 {
            card.isHidden = false
            gatewayActionButton.title = L10n.tr("integration.gateway.start")
            gatewayActionButton.isEnabled = true
            if wasHidden != card.isHidden, currentPageId == "messaging" {
                scheduleScrollSettingsToTop()
            }
        }
        // Keep watching — both to confirm a pending alarm and to hide a
        // visible one the moment the gateway comes back.
        scheduleGatewayFollowUp(after: 3.0)
    }

    /// At most one queued follow-up probe at a time; the chain stops on a
    /// "running" result, when no channel is enabled, or once the window is
    /// closed, and restarts from windowDidBecomeKey / autosave.
    private func scheduleGatewayFollowUp(after delay: TimeInterval) {
        guard !gatewayFollowUpScheduled, window?.isVisible == true else { return }
        gatewayFollowUpScheduled = true
        refreshGatewayBanner(afterDelay: delay)
    }

    @objc func gatewayActionClicked() {
        gatewayActionButton.isEnabled = false
        gatewayActionButton.title = L10n.tr("integration.gateway.starting")
        // Persisting autostart also brings the gateway back on future app
        // launches; saveEnv()'s service bounce starts it right away.
        gatewayBounceGraceUntil = Date().addingTimeInterval(10.0)
        checkSlack?.state = .on
        appDelegate.saveEnv(updates: ["FLUXION_MENU_AUTOSTART_GATEWAY": "true"])
        refreshGatewayBanner(afterDelay: 2.5)
    }
}
