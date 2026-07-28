import AppKit
import Foundation
import ServiceManagement

final class FlippedStackView: NSStackView {
    override var isFlipped: Bool { true }
}

// MARK: - Preferences Window Layout & Section Builders
//
// The view construction for the Preferences window lives here, split out of
// PreferencesWindow.swift so the controller file stays focused on lifecycle,
// persistence (autosave), and visibility logic. `show()` assembles the window
// and then calls the `build…Section` methods below in order.
extension PreferencesWindow {

    /// Builds the window chrome — custom titlebar, scrollable body, and footer —
    /// and returns a dummy stack view. Setups sidebar and right page stacks.
    func buildContentLayout(in win: NSWindow) -> NSStackView {
        // Main view assembly
        let contentView = win.contentView!

        let mainLayout = NSStackView()
        mainLayout.orientation = .vertical
        mainLayout.alignment = .leading
        mainLayout.distribution = .fill
        mainLayout.spacing = 0
        mainLayout.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(mainLayout)

        NSLayoutConstraint.activate([
            mainLayout.topAnchor.constraint(equalTo: contentView.topAnchor),
            mainLayout.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            mainLayout.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            mainLayout.bottomAnchor.constraint(equalTo: contentView.bottomAnchor)
        ])



        // 1. Shell View (contains Sidebar + verticalDivider + rightColumn)
        let shellView = NSStackView()
        shellView.orientation = .horizontal
        shellView.alignment = .top
        shellView.distribution = .fill
        shellView.spacing = 0
        shellView.translatesAutoresizingMaskIntoConstraints = false
        mainLayout.addArrangedSubview(shellView)
        
        NSLayoutConstraint.activate([
            shellView.widthAnchor.constraint(equalTo: mainLayout.widthAnchor)
        ])
        
        // 1a. Sidebar (Full height, matching design mockup solid color)
        let sidebar = NSStackView()
        sidebar.orientation = .vertical
        sidebar.alignment = .leading
        sidebar.distribution = .fill
        sidebar.spacing = 4
        // Top edgeInsets has 38pt padding to leave room for the window traffic lights
        sidebar.edgeInsets = NSEdgeInsets(top: 38, left: 10, bottom: 12, right: 10)
        sidebar.translatesAutoresizingMaskIntoConstraints = false
        sidebar.wantsLayer = true
        sidebar.layer?.backgroundColor = Palette.sidebarBackground.cgColor
        
        shellView.addArrangedSubview(sidebar)
        
        NSLayoutConstraint.activate([
            sidebar.widthAnchor.constraint(equalToConstant: 204),
            sidebar.topAnchor.constraint(equalTo: shellView.topAnchor),
            sidebar.bottomAnchor.constraint(equalTo: shellView.bottomAnchor)
        ])
        
        // Search Bar in Sidebar
        searchField = NSSearchField()
        searchField.placeholderString = L10n.tr("preferences.search")
        searchField.translatesAutoresizingMaskIntoConstraints = false
        searchField.delegate = self
        sidebar.addArrangedSubview(searchField)
        
        NSLayoutConstraint.activate([
            searchField.widthAnchor.constraint(equalTo: sidebar.widthAnchor, constant: -20),
            searchField.heightAnchor.constraint(equalToConstant: 24)
        ])
        
        // Spacer below search field
        let searchSpacer = NSView()
        searchSpacer.translatesAutoresizingMaskIntoConstraints = false
        searchSpacer.heightAnchor.constraint(equalToConstant: 6).isActive = true
        sidebar.addArrangedSubview(searchSpacer)
        
        // Build navigation items
        let navData = [
            ("general", L10n.tr("preferences.nav.general"), "slider.horizontal.3", NSColor.systemGray),
            ("agents", L10n.tr("preferences.nav.agents"), "cpu", NSColor.systemIndigo),
            ("automation", L10n.tr("preferences.nav.automation"), "clock", NSColor.systemOrange),
            ("messaging", L10n.tr("preferences.nav.messaging"), "message", NSColor.systemGreen),
            ("services", L10n.tr("preferences.nav.services"), "server.rack", NSColor.systemTeal)
        ]
        
        for (id, label, icon, color) in navData {
            let item = SidebarNavItem(id: id, title: label, iconSymbol: icon, iconBgColor: color)
            item.onClick = { [weak self] pageId in
                self?.switchPage(to: pageId)
            }
            sidebar.addArrangedSubview(item)
            sidebarNavItems.append(item)
            
            NSLayoutConstraint.activate([
                item.widthAnchor.constraint(equalTo: sidebar.widthAnchor, constant: -20)
            ])
        }
        
        // Flexible spacer at the bottom of the sidebar to allow stretching
        let sidebarSpacer = NSView()
        sidebarSpacer.translatesAutoresizingMaskIntoConstraints = false
        sidebarSpacer.setContentHuggingPriority(.defaultLow, for: .vertical)
        sidebar.addArrangedSubview(sidebarSpacer)
        
        // Vertical Separator (Full height of the window body)
        let verticalDivider = NSView()
        verticalDivider.translatesAutoresizingMaskIntoConstraints = false
        verticalDivider.wantsLayer = true
        verticalDivider.layer?.backgroundColor = Palette.separator.cgColor
        shellView.addArrangedSubview(verticalDivider)
        
        NSLayoutConstraint.activate([
            verticalDivider.widthAnchor.constraint(equalToConstant: 0.5),
            verticalDivider.topAnchor.constraint(equalTo: shellView.topAnchor),
            verticalDivider.bottomAnchor.constraint(equalTo: shellView.bottomAnchor)
        ])
        
        // 1b. Right Column (holds right title bar + scrollable content)
        let rightColumn = NSStackView()
        rightColumn.orientation = .vertical
        rightColumn.alignment = .leading
        rightColumn.distribution = .fill
        rightColumn.spacing = 0
        rightColumn.translatesAutoresizingMaskIntoConstraints = false
        rightColumn.wantsLayer = true
        rightColumn.layer?.backgroundColor = Palette.windowBackground.cgColor
        shellView.addArrangedSubview(rightColumn)
        
        NSLayoutConstraint.activate([
            rightColumn.topAnchor.constraint(equalTo: shellView.topAnchor),
            rightColumn.bottomAnchor.constraint(equalTo: shellView.bottomAnchor),
            rightColumn.trailingAnchor.constraint(equalTo: shellView.trailingAnchor)
        ])
        
        // Custom titlebar for right column (with horizontal bottom separator)
        let rightTitlebarView = NSView()
        rightTitlebarView.translatesAutoresizingMaskIntoConstraints = false
        rightTitlebarView.wantsLayer = true
        rightTitlebarView.layer?.backgroundColor = Palette.windowBackground.cgColor
        rightColumn.addArrangedSubview(rightTitlebarView)
        
        NSLayoutConstraint.activate([
            rightTitlebarView.heightAnchor.constraint(equalToConstant: 48),
            rightTitlebarView.widthAnchor.constraint(equalTo: rightColumn.widthAnchor)
        ])
        
        let titlebarSep = NSView()
        titlebarSep.translatesAutoresizingMaskIntoConstraints = false
        titlebarSep.wantsLayer = true
        titlebarSep.layer?.backgroundColor = Palette.separator.cgColor
        rightTitlebarView.addSubview(titlebarSep)
        
        NSLayoutConstraint.activate([
            titlebarSep.leadingAnchor.constraint(equalTo: rightTitlebarView.leadingAnchor),
            titlebarSep.trailingAnchor.constraint(equalTo: rightTitlebarView.trailingAnchor),
            titlebarSep.bottomAnchor.constraint(equalTo: rightTitlebarView.bottomAnchor),
            titlebarSep.heightAnchor.constraint(equalToConstant: 0.5)
        ])
        
        let winTitleLabel = NSTextField(labelWithString: L10n.tr("preferences.title"))
        winTitleLabel.font = NSFont.systemFont(ofSize: 13, weight: .bold)
        winTitleLabel.textColor = Palette.primaryText
        winTitleLabel.isEditable = false
        winTitleLabel.isSelectable = false
        winTitleLabel.isBordered = false
        winTitleLabel.drawsBackground = false
        winTitleLabel.translatesAutoresizingMaskIntoConstraints = false
        rightTitlebarView.addSubview(winTitleLabel)

        NSLayoutConstraint.activate([
            winTitleLabel.centerXAnchor.constraint(equalTo: rightTitlebarView.centerXAnchor),
            winTitleLabel.centerYAnchor.constraint(equalTo: rightTitlebarView.centerYAnchor)
        ])
        

        
        // Right Scroll View (with refined small scroller sizing and custom clean track)
        let scrollView = NSScrollView()
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        let customScroller = CleanScroller()
        customScroller.controlSize = .small
        scrollView.verticalScroller = customScroller
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = true
        scrollView.drawsBackground = false
        settingsScrollView = scrollView
        rightColumn.addArrangedSubview(scrollView)
        
        NSLayoutConstraint.activate([
            scrollView.widthAnchor.constraint(equalTo: rightColumn.widthAnchor)
        ])
        
        let clipView = scrollView.contentView
        
        let rightPaneContainer = FlippedStackView()
        rightPaneContainer.orientation = .vertical
        rightPaneContainer.alignment = .leading
        rightPaneContainer.spacing = 0
        rightPaneContainer.translatesAutoresizingMaskIntoConstraints = false
        scrollView.documentView = rightPaneContainer
        
        NSLayoutConstraint.activate([
            rightPaneContainer.topAnchor.constraint(equalTo: clipView.topAnchor),
            rightPaneContainer.leadingAnchor.constraint(equalTo: clipView.leadingAnchor),
            rightPaneContainer.trailingAnchor.constraint(equalTo: clipView.trailingAnchor),
            rightPaneContainer.widthAnchor.constraint(equalTo: clipView.widthAnchor),
            rightPaneContainer.heightAnchor.constraint(greaterThanOrEqualTo: clipView.heightAnchor)
        ])
        
        // Create the 5 page stack views inside the scrollable documentView
        let pageMeta = [
            ("general", L10n.tr("preferences.nav.general"), L10n.tr("preferences.page.general.desc")),
            ("agents", L10n.tr("preferences.nav.agents"), L10n.tr("preferences.page.agents.desc")),
            ("automation", L10n.tr("preferences.nav.automation"), L10n.tr("preferences.page.automation.desc")),
            ("messaging", L10n.tr("preferences.nav.messaging"), L10n.tr("preferences.page.messaging.desc")),
            ("services", L10n.tr("preferences.nav.services"), L10n.tr("preferences.page.services.desc"))
        ]
        
        for (id, title, desc) in pageMeta {
            let pageStack = FlippedStackView()
            pageStack.orientation = .vertical
            pageStack.alignment = .leading
            pageStack.spacing = 20
            pageStack.edgeInsets = NSEdgeInsets(top: 22, left: 22, bottom: 22, right: 22)
            pageStack.translatesAutoresizingMaskIntoConstraints = false
            pageStack.isHidden = (id != "general")
            rightPaneContainer.addArrangedSubview(pageStack)
            
            NSLayoutConstraint.activate([
                pageStack.widthAnchor.constraint(equalTo: rightPaneContainer.widthAnchor)
            ])
            
            pageStackViews[id] = pageStack
            
            // Build the Page Header
            let h1 = NSTextField(labelWithString: title)
            h1.font = NSFont.systemFont(ofSize: 21, weight: .bold)
            h1.textColor = Palette.primaryText
            h1.isEditable = false
            h1.isSelectable = false
            h1.isBordered = false
            h1.drawsBackground = false
            pageStack.addArrangedSubview(h1)
            
            let p = NSTextField(labelWithString: desc)
            p.font = NSFont.systemFont(ofSize: 12.5, weight: .regular)
            p.textColor = Palette.secondaryText
            p.cell?.wraps = true
            p.isEditable = false
            p.isSelectable = false
            p.isBordered = false
            p.drawsBackground = false
            pageStack.addArrangedSubview(p)
            
            // Balanced title/description and section spacing with elegant breathing room
            pageStack.setCustomSpacing(10, after: h1)
            pageStack.setCustomSpacing(22, after: p)
        }

        // 3. FooterView Assembly (spanning the entire width at the bottom)
        let footerView = NSView()
        footerView.translatesAutoresizingMaskIntoConstraints = false
        footerView.wantsLayer = true
        footerView.layer?.backgroundColor = Palette.chromeBackground.cgColor
        mainLayout.addArrangedSubview(footerView)

        NSLayoutConstraint.activate([
            footerView.heightAnchor.constraint(equalToConstant: 40),
            footerView.widthAnchor.constraint(equalTo: mainLayout.widthAnchor)
        ])

        let footerSep = NSView()
        footerSep.translatesAutoresizingMaskIntoConstraints = false
        footerSep.wantsLayer = true
        footerSep.layer?.backgroundColor = Palette.separator.cgColor
        footerView.addSubview(footerSep)

        NSLayoutConstraint.activate([
            footerSep.leadingAnchor.constraint(equalTo: footerView.leadingAnchor),
            footerSep.trailingAnchor.constraint(equalTo: footerView.trailingAnchor),
            footerSep.topAnchor.constraint(equalTo: footerView.topAnchor),
            footerSep.heightAnchor.constraint(equalToConstant: 0.5)
        ])

        let dotView = NSView()
        dotView.translatesAutoresizingMaskIntoConstraints = false
        dotView.wantsLayer = true
        dotView.layer?.cornerRadius = 3
        dotView.layer?.backgroundColor = NSColor.controlAccentColor.cgColor
        dotView.widthAnchor.constraint(equalToConstant: 6).isActive = true
        dotView.heightAnchor.constraint(equalToConstant: 6).isActive = true

        let verLabel = NSTextField(labelWithString: "Fluxion \(PreferencesWindow.appVersion)")
        verLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        verLabel.textColor = Palette.secondaryText
        verLabel.isEditable = false
        verLabel.isSelectable = false
        verLabel.isBordered = false
        verLabel.drawsBackground = false

        let verStack = NSStackView(views: [dotView, verLabel])
        verStack.orientation = .horizontal
        verStack.spacing = 7
        verStack.alignment = .centerY
        verStack.translatesAutoresizingMaskIntoConstraints = false
        footerView.addSubview(verStack)

        let quitBtn = TxtButton()
        quitBtn.title = L10n.tr("app.quit")
        quitBtn.isQuitButton = true
        quitBtn.isBordered = false
        quitBtn.translatesAutoresizingMaskIntoConstraints = false
        quitBtn.target = self
        quitBtn.action = #selector(quitApp)

        // Manual "Check for Updates" — shown only in distribution builds where
        // the updater is actually configured (dev builds leave it inert).
        var footerButtons: [NSView] = []
        if appDelegate.updaterController?.isConfigured == true {
            let updateBtn = TxtButton()
            updateBtn.title = L10n.tr("preferences.check_updates")
            updateBtn.isBordered = false
            updateBtn.translatesAutoresizingMaskIntoConstraints = false
            updateBtn.target = self
            updateBtn.action = #selector(checkForUpdates)
            updateBtn.heightAnchor.constraint(equalToConstant: 24).isActive = true
            footerButtons.append(updateBtn)
        }
        footerButtons.append(quitBtn)

        let actionStack = NSStackView(views: footerButtons)
        actionStack.orientation = .horizontal
        actionStack.spacing = 6
        actionStack.alignment = .centerY
        actionStack.translatesAutoresizingMaskIntoConstraints = false
        footerView.addSubview(actionStack)

        NSLayoutConstraint.activate([
            verStack.leadingAnchor.constraint(equalTo: footerView.leadingAnchor, constant: 16),
            verStack.centerYAnchor.constraint(equalTo: footerView.centerYAnchor),

            actionStack.trailingAnchor.constraint(equalTo: footerView.trailingAnchor, constant: -16),
            actionStack.centerYAnchor.constraint(equalTo: footerView.centerYAnchor),

            quitBtn.heightAnchor.constraint(equalToConstant: 24),
        ])

        return NSStackView()
    }

    /// Wraps a list of rows in a titled card and appends it to `documentStack`.
    @discardableResult
    func addSection(title: String, rows: [NSView], into documentStack: NSStackView) -> NSStackView {
        let card = CardView()
        for row in rows {
            card.stackView.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true
        }

        let sectionStack = NSStackView()
        sectionStack.orientation = .vertical
        sectionStack.alignment = .leading
        sectionStack.spacing = 7
        sectionStack.translatesAutoresizingMaskIntoConstraints = false

        let header = NSTextField(labelWithString: title.uppercased())
        header.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        header.textColor = Palette.sectionHeader
        header.isEditable = false
        header.isSelectable = false
        header.isBordered = false
        header.drawsBackground = false
        header.translatesAutoresizingMaskIntoConstraints = false

        let headerContainer = NSView()
        headerContainer.translatesAutoresizingMaskIntoConstraints = false
        headerContainer.addSubview(header)
        NSLayoutConstraint.activate([
            header.topAnchor.constraint(equalTo: headerContainer.topAnchor),
            header.leadingAnchor.constraint(equalTo: headerContainer.leadingAnchor, constant: 6),
            header.trailingAnchor.constraint(equalTo: headerContainer.trailingAnchor),
            header.bottomAnchor.constraint(equalTo: headerContainer.bottomAnchor)
        ])

        sectionStack.addArrangedSubview(headerContainer)
        sectionStack.addArrangedSubview(card)

        headerContainer.widthAnchor.constraint(equalTo: sectionStack.widthAnchor).isActive = true
        card.widthAnchor.constraint(equalTo: sectionStack.widthAnchor).isActive = true

        documentStack.addArrangedSubview(sectionStack)
        sectionStack.widthAnchor.constraint(equalTo: documentStack.widthAnchor, constant: -44).isActive = true

        return sectionStack
    }

    // MARK: - Section 0: Interface Language
    func buildLanguageSection(into documentStack: NSStackView) {
        languagePopup = NSPopUpButton(frame: .zero, pullsDown: false)
        languagePopup.addItems(withTitles: [
            L10n.tr("preferences.language.system"),
            L10n.tr("preferences.language.zh_hans"),
            L10n.tr("preferences.language.en"),
            L10n.tr("preferences.language.ja")
        ])
        languagePopup.translatesAutoresizingMaskIntoConstraints = false
        languagePopup.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true

        let languageCodes = ["system", "zh-Hans", "en", "ja"]
        let selectedIndex = languageCodes.firstIndex(of: L10n.savedAppLanguage) ?? 0
        languagePopup.selectItem(at: selectedIndex)
        languagePopup.target = self
        languagePopup.action = #selector(languageChanged)

        let languageRow = CardRow(
            title: L10n.tr("preferences.language.title"),
            desc: L10n.tr("preferences.language.desc"),
            control: languagePopup,
            isFirst: true
        )

        languageRestartNotice = makeLanguageRestartNotice()
        languageRestartNotice.isHidden = (L10n.savedAppLanguage == initialAppLanguage)

        addSection(
            title: L10n.tr("preferences.section.interface"),
            rows: [languageRow, languageRestartNotice],
            into: documentStack
        )
    }

    func makeLanguageRestartNotice() -> NSView {
        let view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.10).cgColor

        let message = NSTextField(wrappingLabelWithString: L10n.tr("preferences.language.restart_notice"))
        message.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        message.textColor = Palette.primaryText
        message.isEditable = false
        message.isSelectable = false
        message.isBordered = false
        message.drawsBackground = false
        message.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        message.translatesAutoresizingMaskIntoConstraints = false

        let restartButton = NSButton(title: L10n.tr("preferences.language.restart_now"), target: self, action: #selector(restartAppNow))
        restartButton.bezelStyle = .rounded
        restartButton.controlSize = .small
        restartButton.setContentHuggingPriority(.required, for: .horizontal)
        restartButton.setContentCompressionResistancePriority(.required, for: .horizontal)
        restartButton.translatesAutoresizingMaskIntoConstraints = false

        view.addSubview(message)
        view.addSubview(restartButton)

        NSLayoutConstraint.activate([
            message.topAnchor.constraint(equalTo: view.topAnchor, constant: 10),
            message.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            message.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -10),

            restartButton.leadingAnchor.constraint(greaterThanOrEqualTo: message.trailingAnchor, constant: 12),
            restartButton.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),
            restartButton.centerYAnchor.constraint(equalTo: view.centerYAnchor),

            view.heightAnchor.constraint(greaterThanOrEqualToConstant: 44)
        ])

        return view
    }

    // MARK: - Section 1: Menu Bar Display
    func buildMenuBarSection(into documentStack: NSStackView) {

        displayStyleSegmented = NSSegmentedControl(labels: [L10n.tr("preferences.display.menu_bar"), L10n.tr("preferences.display.notch")], trackingMode: .selectOne, target: self, action: #selector(autosave))
        displayStyleSegmented.segmentStyle = .rounded
        displayStyleSegmented.selectedSegment = (appDelegate.envVals["FLUXION_NOTCH_MODE"] ?? "false") == "true" ? 1 : 0
        displayStyleSegmented.translatesAutoresizingMaskIntoConstraints = false
        if #available(macOS 10.13, *) {
            displayStyleSegmented.segmentDistribution = .fillEqually
        }

        appearanceSegmented = NSSegmentedControl(labels: [L10n.tr("preferences.appearance.native"), L10n.tr("preferences.appearance.rich")], trackingMode: .selectOne, target: self, action: #selector(autosave))
        appearanceSegmented.segmentStyle = .rounded
        appearanceSegmented.selectedSegment = (appDelegate.envVals["FLUXION_MENU_APPEARANCE"] ?? "rich") == "rich" ? 1 : 0
        appearanceSegmented.translatesAutoresizingMaskIntoConstraints = false
        if #available(macOS 10.13, *) {
            appearanceSegmented.segmentDistribution = .fillEqually
        }

        silentStylePopup = NSPopUpButton(frame: .zero, pullsDown: false)
        silentStylePopup.addItems(withTitles: [
            L10n.tr("preferences.notch.lowest"),
            L10n.tr("preferences.notch.all_models"),
            L10n.tr("preferences.notch.ambient")
        ])
        silentStylePopup.translatesAutoresizingMaskIntoConstraints = false
        silentStylePopup.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true

        let activeSilent = appDelegate.envVals["FLUXION_NOTCH_COLLAPSED_MODE"] ?? "all"
        if activeSilent == "lowest" { silentStylePopup.selectItem(at: 0) }
        else if activeSilent == "ambient" { silentStylePopup.selectItem(at: 2) }
        else { silentStylePopup.selectItem(at: 1) }

        silentStylePopup.target = self
        silentStylePopup.action = #selector(autosave)

        let displayStyleRow = CardRowStacked(
            title: L10n.tr("preferences.display.style"),
            desc: L10n.tr("preferences.display.style.desc"),
            control: displayStyleSegmented,
            isFirst: true
        )

        appearanceRow = CardRowStacked(
            title: L10n.tr("preferences.menu_appearance"),
            desc: L10n.tr("preferences.menu_appearance.desc"),
            control: appearanceSegmented,
            isFirst: false
        )

        silentStyleRow = CardRow(
            title: L10n.tr("preferences.notch_silent"),
            desc: L10n.tr("preferences.notch_silent.desc"),
            control: silentStylePopup,
            isFirst: true
        )

        gaugeStylePopup = NSPopUpButton(frame: .zero, pullsDown: false)
        gaugeStylePopup.addItems(withTitles: [
            L10n.tr("preferences.notch.gauge.dot"),
            L10n.tr("preferences.notch.gauge.ring"),
            L10n.tr("preferences.notch.gauge.liquid")
        ])
        gaugeStylePopup.translatesAutoresizingMaskIntoConstraints = false
        gaugeStylePopup.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true

        let activeShape = appDelegate.envVals["FLUXION_NOTCH_GAUGE_STYLE"] ?? "ring"
        let shapeOrder = ["dot", "ring", "liquid"]
        gaugeStylePopup.selectItem(at: shapeOrder.firstIndex(of: activeShape) ?? 1)

        gaugeStylePopup.target = self
        gaugeStylePopup.action = #selector(autosave)

        gaugeStyleRow = CardRow(
            title: L10n.tr("preferences.notch.gauge"),
            desc: L10n.tr("preferences.notch.gauge.desc"),
            control: gaugeStylePopup,
            isFirst: false
        )

        gaugeValuePopup = NSPopUpButton(frame: .zero, pullsDown: false)
        gaugeValuePopup.addItems(withTitles: [
            L10n.tr("preferences.notch.gauge_value.beside"),
            L10n.tr("preferences.notch.gauge_value.inside"),
            L10n.tr("preferences.notch.gauge_value.hidden")
        ])
        gaugeValuePopup.translatesAutoresizingMaskIntoConstraints = false
        gaugeValuePopup.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true

        let activeGaugeValue = appDelegate.envVals["FLUXION_NOTCH_GAUGE_VALUE_POSITION"] ?? "beside"
        let valueOrder = ["beside", "inside", "hidden"]
        gaugeValuePopup.selectItem(at: valueOrder.firstIndex(of: activeGaugeValue) ?? 0)
        // The dot has no interior for a number; the placement axis applies to
        // ring/liquid only.
        gaugeValuePopup.isEnabled = activeShape != "dot"

        gaugeValuePopup.target = self
        gaugeValuePopup.action = #selector(autosave)

        gaugeValueRow = CardRow(
            title: L10n.tr("preferences.notch.gauge_value"),
            desc: L10n.tr("preferences.notch.gauge_value.desc"),
            control: gaugeValuePopup,
            isFirst: false
        )

        expandedStylePopup = NSPopUpButton(frame: .zero, pullsDown: false)
        expandedStylePopup.addItems(withTitles: [
            L10n.tr("preferences.notch.expanded_style.compact"),
            L10n.tr("preferences.notch.expanded_style.detailed")
        ])
        expandedStylePopup.translatesAutoresizingMaskIntoConstraints = false
        expandedStylePopup.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true
        let activeExpandedStyle = appDelegate.envVals["FLUXION_NOTCH_SINGLE_MODEL_LAYOUT"] ?? "detailed"
        expandedStylePopup.selectItem(at: activeExpandedStyle == "compact" ? 0 : 1)
        expandedStylePopup.target = self
        expandedStylePopup.action = #selector(autosave)

        expandedStyleRow = CardRow(
            title: L10n.tr("preferences.notch.expanded_style"),
            desc: L10n.tr("preferences.notch.expanded_style.desc"),
            control: expandedStylePopup,
            isFirst: true
        )

        peekResetPopup = NSPopUpButton(frame: .zero, pullsDown: false)
        peekResetPopup.addItems(withTitles: [
            L10n.tr("preferences.reset.5h"),
            L10n.tr("preferences.notch.weekly"),
            L10n.tr("preferences.notch.both")
        ])
        peekResetPopup.translatesAutoresizingMaskIntoConstraints = false
        peekResetPopup.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true

        let activePeekReset = appDelegate.envVals["FLUXION_NOTCH_PEEK_WINDOWS"] ?? "both"
        if activePeekReset == "weekly" { peekResetPopup.selectItem(at: 1) }
        else if activePeekReset == "both" { peekResetPopup.selectItem(at: 2) }
        else { peekResetPopup.selectItem(at: 0) }

        peekResetPopup.target = self
        peekResetPopup.action = #selector(autosave)

        peekResetRow = CardRow(
            title: L10n.tr("preferences.notch.peek_countdown"),
            desc: L10n.tr("preferences.notch.peek_countdown.desc"),
            control: peekResetPopup,
            isFirst: false
        )

        checkHideOnFullscreen = NSSwitch()
        checkHideOnFullscreen.state = (appDelegate.envVals["FLUXION_NOTCH_HIDE_ON_FULLSCREEN"] ?? "true").lowercased() == "true" ? .on : .off
        checkHideOnFullscreen.target = self
        checkHideOnFullscreen.action = #selector(autosave)
        hideOnFullscreenRow = CardRow(
            title: L10n.tr("preferences.hide_fullscreen"),
            desc: L10n.tr("preferences.hide_fullscreen.desc"),
            control: checkHideOnFullscreen,
            isFirst: true
        )

        addSection(
            title: L10n.tr("preferences.section.display"),
            rows: [displayStyleRow, appearanceRow],
            into: documentStack
        )
        notchGlanceSection = addSection(
            title: L10n.tr("preferences.section.notch_glance"),
            rows: [silentStyleRow, gaugeStyleRow, gaugeValueRow, peekResetRow],
            into: documentStack
        )
        notchExpandedSection = addSection(
            title: L10n.tr("preferences.section.notch_expanded"),
            rows: [expandedStyleRow],
            into: documentStack
        )
        notchBehaviorSection = addSection(
            title: L10n.tr("preferences.section.notch_behavior"),
            rows: [hideOnFullscreenRow],
            into: documentStack
        )
    }

    // MARK: - Section 2: Usage Display
    func buildUsageSection(into documentStack: NSStackView, availability: AvailabilitySnapshot?) {
        let activeStr = appDelegate.envVals["FLUXION_USAGE_PROVIDERS"] ?? "claude,codex,antigravity"
        let provs = activeStr.components(separatedBy: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }

        func usageDescription(_ provider: String, _ base: String) -> String {
            guard let entry = availability?.usage[provider] else {
                return "\(base) \(L10n.tr("preferences.status.detection_pending"))"
            }
            if entry.status == "ok" {
                return "\(base) \(L10n.tr("preferences.status.available"))."
            }
            if provider == "claude", entry.detail.hasPrefix("No Claude OAuth token") {
                return "\(base) \(L10n.tr("preferences.usage.claude.token_hint"))"
            }
            return "\(base) \(L10n.tr("preferences.status.not_available")): \(entry.detail)"
        }

        checkClaude = NSSwitch()
        checkClaude.state = provs.contains("claude") ? .on : .off
        checkClaude.target = self
        checkClaude.action = #selector(autosave)
        let claudeRow = CardRow(
            title: "Claude Code",
            desc: usageDescription("claude", L10n.tr("preferences.usage.claude.desc")),
            control: checkClaude,
            isFirst: true
        )

        checkCodex = NSSwitch()
        checkCodex.state = provs.contains("codex") ? .on : .off
        checkCodex.target = self
        checkCodex.action = #selector(autosave)
        let codexRow = CardRow(
            title: "ChatGPT (Codex)",
            desc: usageDescription("codex", L10n.tr("preferences.usage.codex.desc")),
            control: checkCodex,
            isFirst: false
        )

        checkAntigravity = NSSwitch()
        checkAntigravity.state = provs.contains("antigravity") ? .on : .off
        checkAntigravity.target = self
        checkAntigravity.action = #selector(autosave)
        let antiRow = CardRow(
            title: "Antigravity",
            desc: usageDescription("antigravity", L10n.tr("preferences.usage.antigravity.desc")),
            control: checkAntigravity,
            isFirst: false
        )

        addSection(title: L10n.tr("preferences.usage.display"), rows: [claudeRow, codexRow, antiRow], into: documentStack)
    }

    // MARK: - Section 3: Task Executors
    func buildExecutorsSection(into documentStack: NSStackView, availability: AvailabilitySnapshot?) {
        let enabledStr = appDelegate.envVals["FLUXION_ENABLED_EXECUTORS"] ?? "claude,codex,antigravity"
        let enabledExecutors = Set(
            enabledStr
                .components(separatedBy: ",")
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
        )

        executorsPreservedWhileUnavailable = []

        func executorRow(
            _ provider: String,
            _ title: String,
            control: NSSwitch,
            isFirst: Bool
        ) -> CardRow {
            let entry = availability?.executors[provider]
            let isAvailable = entry?.status == "available"
            let isEnabled = enabledExecutors.contains(provider)
            let status = isAvailable ? L10n.tr("preferences.status.available") : L10n.tr("preferences.status.not_available")
            let detail = entry?.path?.isEmpty == false ? entry!.path! : (entry?.detail ?? L10n.tr("preferences.status.detection_pending"))
            // Don't present an uninstalled executor as enabled: force the toggle
            // off and disable it. The preference is not lost — autosave restores
            // it from executorsPreservedWhileUnavailable.
            if isAvailable {
                control.state = isEnabled ? .on : .off
                control.isEnabled = true
            } else {
                control.state = .off
                control.isEnabled = false
                if isEnabled { executorsPreservedWhileUnavailable.insert(provider) }
            }
            control.target = self
            control.action = #selector(autosave)
            return CardRow(title: title, desc: "\(status) · \(detail)", control: control, isFirst: isFirst)
        }

        checkExecutorClaude = NSSwitch()
        checkExecutorCodex = NSSwitch()
        checkExecutorAntigravity = NSSwitch()

        defaultExecutorPopup = NSPopUpButton(frame: .zero, pullsDown: false)
        defaultExecutorPopup.autoenablesItems = false
        defaultExecutorPopup.addItems(withTitles: ["Claude Code", "Codex", "Antigravity"])
        // Mirror the welcome picker: only installed executors are selectable as
        // the default, and the shown selection snaps to what will actually run.
        let availableDefaults = PreferencesWindow.executorProviderKeys.filter {
            availability?.executors[$0]?.status == "available"
        }
        if !availableDefaults.isEmpty {
            for (idx, provider) in PreferencesWindow.executorProviderKeys.enumerated() {
                defaultExecutorPopup.item(at: idx)?.isEnabled = availableDefaults.contains(provider)
            }
        }
        let currentDefault = (appDelegate.envVals["FLUXION_DEFAULT_EXECUTOR"] ?? "codex").lowercased()
        let effectiveDefault = availableDefaults.contains(currentDefault)
            ? currentDefault
            : (availableDefaults.first ?? currentDefault)
        defaultExecutorPopup.selectItem(at: PreferencesWindow.executorProviderKeys.firstIndex(of: effectiveDefault) ?? 1)
        defaultExecutorPopup.target = self
        defaultExecutorPopup.action = #selector(autosave)
        let defaultRow = CardRow(
            title: L10n.tr("preferences.executors.default.title"),
            desc: L10n.tr("preferences.executors.default.desc"),
            control: defaultExecutorPopup,
            isFirst: false
        )

        checkAvailabilityButton = NSButton(title: L10n.tr("preferences.check_again"), target: self, action: #selector(checkAvailability))
        checkAvailabilityButton.bezelStyle = .rounded
        let checkRow = CardRow(
            title: L10n.tr("preferences.executors.title"),
            desc: L10n.tr("preferences.executors.desc"),
            control: checkAvailabilityButton!,
            isFirst: false
        )
        addSection(title: L10n.tr("preferences.section.task_executors"), rows: [
            executorRow("claude", "Claude Code", control: checkExecutorClaude, isFirst: true),
            executorRow("codex", "Codex", control: checkExecutorCodex, isFirst: false),
            executorRow("antigravity", "Antigravity", control: checkExecutorAntigravity, isFirst: false),
            defaultRow,
            checkRow,
        ], into: documentStack)
    }

    // MARK: - Section 4: API & Models
    func buildApiModelsSection(into documentStack: NSStackView) {
        checkKeychain = NSSwitch()
        checkKeychain.state = (appDelegate.envVals["FLUXION_CLAUDE_USAGE_KEYCHAIN"] ?? "false").lowercased() == "true" ? .on : .off
        checkKeychain.target = self
        checkKeychain.action = #selector(autosave)
        let keychainRow = CardRow(
            title: L10n.tr("preferences.keychain.title"),
            desc: L10n.tr("preferences.keychain.desc"),
            control: checkKeychain,
            isFirst: true
        )

        checkClaudeAutoRefresh = NSSwitch()
        checkClaudeAutoRefresh.state = (appDelegate.envVals["FLUXION_CLAUDE_USAGE_AUTO_REFRESH"] ?? "false").lowercased() == "true" ? .on : .off
        checkClaudeAutoRefresh.target = self
        checkClaudeAutoRefresh.action = #selector(autosave)
        let claudeAutoRefreshRow = CardRow(
            title: L10n.tr("preferences.claude_refresh.title"),
            desc: L10n.tr("preferences.claude_refresh.desc"),
            control: checkClaudeAutoRefresh,
            isFirst: false
        )

        self.keychainRow = keychainRow
        self.claudeAutoRefreshRow = claudeAutoRefreshRow
        self.apiModelsSection = addSection(title: L10n.tr("preferences.section.api_models"), rows: [keychainRow, claudeAutoRefreshRow], into: documentStack)
    }

    // MARK: - Section 4b: Sub-agent Projects
    func buildSubagentProjectsSection(into documentStack: NSStackView) {
        projectsContainerStack = NSStackView()
        projectsContainerStack.orientation = .vertical
        projectsContainerStack.spacing = 0
        projectsContainerStack.alignment = .leading
        projectsContainerStack.translatesAutoresizingMaskIntoConstraints = false
        
        let rawProjects = appDelegate.envVals["FLUXION_PROJECTS"] ?? ""
        let parsed = parseProjects(from: rawProjects)
        
        projectRowViews.removeAll()
        
        // Add Button
        addProjectButton = NSButton(title: L10n.tr("preferences.add_project"), target: self, action: #selector(addProjectClicked))
        addProjectButton.bezelStyle = .rounded
        addProjectButton.controlSize = .small
        addProjectButton.translatesAutoresizingMaskIntoConstraints = false
        
        let addRowView = NSStackView(views: [addProjectButton])
        addRowView.orientation = .horizontal
        addRowView.alignment = .centerY
        addRowView.translatesAutoresizingMaskIntoConstraints = false
        addRowView.edgeInsets = NSEdgeInsets(top: 8, left: 16, bottom: 8, right: 16)
        
        addRowSep = NSView()
        addRowSep.translatesAutoresizingMaskIntoConstraints = false
        addRowSep.wantsLayer = true
        addRowSep.layer?.backgroundColor = Palette.separator.cgColor
        addRowView.addSubview(addRowSep)
        NSLayoutConstraint.activate([
            addRowSep.topAnchor.constraint(equalTo: addRowView.topAnchor),
            addRowSep.leadingAnchor.constraint(equalTo: addRowView.leadingAnchor, constant: 16),
            addRowSep.trailingAnchor.constraint(equalTo: addRowView.trailingAnchor),
            addRowSep.heightAnchor.constraint(equalToConstant: 0.5)
        ])
        
        addSection(
            title: L10n.tr("preferences.projects.title"),
            rows: [projectsContainerStack, addRowView],
            into: documentStack
        )
        
        // Add initial rows
        for item in parsed {
            addProjectRow(key: item.key, workspace: item.workspace, executor: item.executor, description: item.description)
        }
        
        updateSeparators()
    }

    // MARK: - Section 5: Companion Services
    func buildCompanionServicesSection(into documentStack: NSStackView) {
        checkWeb = NSSwitch()
        checkWeb.state = (appDelegate.envVals["FLUXION_MENU_AUTOSTART_WEB"] ?? "true").lowercased() == "true" ? .on : .off
        checkWeb.target = self
        checkWeb.action = #selector(autosave)
        let webRow = CardRow(
            title: L10n.tr("preferences.service.web.title"),
            desc: L10n.tr("preferences.service.web.desc"),
            control: checkWeb,
            isFirst: true
        )

        checkScheduler = NSSwitch()
        let schedulerEnabled = (appDelegate.envVals["FLUXION_SCHEDULER_ENABLED"] ?? "true").lowercased() == "true"
        let schedulerAutostart = (appDelegate.envVals["FLUXION_MENU_AUTOSTART_SCHEDULER"] ?? "true").lowercased() == "true"
        checkScheduler.state = (schedulerEnabled && schedulerAutostart) ? .on : .off
        checkScheduler.target = self
        checkScheduler.action = #selector(autosave)
        let schedRow = CardRow(
            title: L10n.tr("preferences.service.scheduler.title"),
            desc: L10n.tr("preferences.service.scheduler.desc"),
            control: checkScheduler,
            isFirst: false
        )

        checkSlack = NSSwitch()
        checkSlack.state = (appDelegate.envVals["FLUXION_MENU_AUTOSTART_GATEWAY"] ?? "false").lowercased() == "true" ? .on : .off
        checkSlack.target = self
        checkSlack.action = #selector(autosave)
        let slackRow = CardRow(
            title: L10n.tr("preferences.service.gateway.title"),
            desc: L10n.tr("preferences.service.gateway.desc"),
            control: checkSlack,
            isFirst: false
        )

        checkProvider = NSSwitch()
        checkProvider.state =
            (appDelegate.envVals["FLUXION_PROVIDER_ENABLED"] ?? "false").lowercased() == "true" ? .on : .off
        checkProvider.target = self
        checkProvider.action = #selector(autosave)
        let providerRow = CardRow(
            title: L10n.tr("preferences.service.provider.title"),
            desc: L10n.tr("preferences.service.provider.desc"),
            control: checkProvider,
            isFirst: false
        )

        addSection(
            title: L10n.tr("preferences.section.services"),
            rows: [webRow, schedRow, slackRow, providerRow],
            into: documentStack
        )
    }

    // MARK: - Section 6: Quota Reset Automation
    func buildQuotaResetSection(into documentStack: NSStackView) {
        checkClaudeAutoping = NSPopUpButton(frame: .zero, pullsDown: false)
        checkClaudeAutoping.addItems(withTitles: PreferencesWindow.autoPingTitles)
        checkClaudeAutoping.translatesAutoresizingMaskIntoConstraints = false
        checkClaudeAutoping.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true
        checkClaudeAutoping.selectItem(
            at: PreferencesWindow.autoPingModeIndex(appDelegate.autoPingModes["claude"] ?? "off"))
        checkClaudeAutoping.target = self
        checkClaudeAutoping.action = #selector(autosave)
        let claudeAutopingRow = CardRow(
            title: "Claude",
            desc: L10n.tr("preferences.quota.watch.desc", "Claude"),
            control: checkClaudeAutoping,
            isFirst: true
        )

        checkCodexAutoping = NSPopUpButton(frame: .zero, pullsDown: false)
        checkCodexAutoping.addItems(withTitles: PreferencesWindow.autoPingTitles)
        checkCodexAutoping.translatesAutoresizingMaskIntoConstraints = false
        checkCodexAutoping.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true
        checkCodexAutoping.selectItem(
            at: PreferencesWindow.autoPingModeIndex(appDelegate.autoPingModes["codex"] ?? "off"))
        checkCodexAutoping.target = self
        checkCodexAutoping.action = #selector(autosave)
        let codexAutopingRow = CardRow(
            title: "Codex",
            desc: L10n.tr("preferences.quota.watch.desc", "Codex"),
            control: checkCodexAutoping,
            isFirst: false
        )

        checkAntigravityAutoping = NSPopUpButton(frame: .zero, pullsDown: false)
        checkAntigravityAutoping.addItems(withTitles: PreferencesWindow.autoPingTitles)
        checkAntigravityAutoping.translatesAutoresizingMaskIntoConstraints = false
        checkAntigravityAutoping.widthAnchor.constraint(lessThanOrEqualToConstant: 230).isActive = true
        checkAntigravityAutoping.selectItem(
            at: PreferencesWindow.autoPingModeIndex(appDelegate.autoPingModes["antigravity"] ?? "off"))
        checkAntigravityAutoping.target = self
        checkAntigravityAutoping.action = #selector(autosave)
        let antigravityAutopingRow = CardRow(
            title: "Antigravity",
            desc: L10n.tr("preferences.quota.watch.desc", "Antigravity"),
            control: checkAntigravityAutoping,
            isFirst: false
        )

        checkAutoPingEnabled = NSSwitch()
        checkAutoPingEnabled.state = (appDelegate.envVals["FLUXION_AUTOPING_ENABLED"] ?? "false").lowercased() == "true" ? .on : .off
        checkAutoPingEnabled.target = self
        checkAutoPingEnabled.action = #selector(autosave)
        let autoPingEnabledRow = CardRow(
            title: L10n.tr("preferences.autoping.title"),
            desc: L10n.tr("preferences.autoping.desc"),
            control: checkAutoPingEnabled,
            isFirst: true
        )

        checkMacOSNotifyRefresh = NSSwitch()
        checkMacOSNotifyRefresh.state = (appDelegate.envVals["FLUXION_MENU_MACOS_NOTIFY_REFRESH"] ?? "true").lowercased() != "false" ? .on : .off
        checkMacOSNotifyRefresh.target = self
        checkMacOSNotifyRefresh.action = #selector(autosave)
        let macOSNotifyRefreshRow = CardRow(
            title: "macOS",
            desc: L10n.tr("preferences.notify.macos.desc"),
            control: checkMacOSNotifyRefresh,
            isFirst: true
        )

        checkSlackNotifyRefresh = NSSwitch()
        checkSlackNotifyRefresh.state = (appDelegate.envVals["FLUXION_MENU_SLACK_NOTIFY_REFRESH"] ?? "false").lowercased() == "true" ? .on : .off
        checkSlackNotifyRefresh.target = self
        checkSlackNotifyRefresh.action = #selector(autosave)
        slackNotifyRefreshRow = CardRow(
            title: "Slack",
            desc: L10n.tr("preferences.notify.slack.desc"),
            control: checkSlackNotifyRefresh,
            isFirst: false
        )

        checkTelegramNotifyRefresh = NSSwitch()
        checkTelegramNotifyRefresh.state = (appDelegate.envVals["FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH"] ?? "false").lowercased() == "true" ? .on : .off
        checkTelegramNotifyRefresh.target = self
        checkTelegramNotifyRefresh.action = #selector(autosave)
        telegramNotifyRefreshRow = CardRow(
            title: "Telegram",
            desc: L10n.tr("preferences.notify.telegram.desc"),
            control: checkTelegramNotifyRefresh,
            isFirst: false
        )

        checkQQBotNotifyRefresh = NSSwitch()
        checkQQBotNotifyRefresh.state = (appDelegate.envVals["FLUXION_MENU_QQBOT_NOTIFY_REFRESH"] ?? "false").lowercased() == "true" ? .on : .off
        checkQQBotNotifyRefresh.target = self
        checkQQBotNotifyRefresh.action = #selector(autosave)
        qqbotNotifyRefreshRow = CardRow(
            title: "QQ",
            desc: L10n.tr("preferences.notify.qq.desc"),
            control: checkQQBotNotifyRefresh,
            isFirst: false
        )

        checkFeishuNotifyRefresh = NSSwitch()
        checkFeishuNotifyRefresh.state = (appDelegate.envVals["FLUXION_MENU_FEISHU_NOTIFY_REFRESH"] ?? "false").lowercased() == "true" ? .on : .off
        checkFeishuNotifyRefresh.target = self
        checkFeishuNotifyRefresh.action = #selector(autosave)
        feishuNotifyRefreshRow = CardRow(
            title: "Feishu",
            desc: L10n.tr("preferences.notify.feishu.desc"),
            control: checkFeishuNotifyRefresh,
            isFirst: false
        )

        checkWeChatNotifyRefresh = NSSwitch()
        checkWeChatNotifyRefresh.state = (appDelegate.envVals["FLUXION_MENU_WECHAT_NOTIFY_REFRESH"] ?? "false").lowercased() == "true" ? .on : .off
        checkWeChatNotifyRefresh.target = self
        checkWeChatNotifyRefresh.action = #selector(autosave)
        weChatNotifyRefreshRow = CardRow(
            title: "WeChat",
            desc: L10n.tr("preferences.notify.wechat.desc"),
            control: checkWeChatNotifyRefresh,
            isFirst: false
        )

        checkLineNotifyRefresh = NSSwitch()
        checkLineNotifyRefresh.state = (appDelegate.envVals["FLUXION_MENU_LINE_NOTIFY_REFRESH"] ?? "false").lowercased() == "true" ? .on : .off
        checkLineNotifyRefresh.target = self
        checkLineNotifyRefresh.action = #selector(autosave)
        lineNotifyRefreshRow = CardRow(
            title: "LINE",
            desc: L10n.tr("preferences.notify.line.desc"),
            control: checkLineNotifyRefresh,
            isFirst: false
        )

        checkNotifyCreditGrant = NSSwitch()
        checkNotifyCreditGrant.state = (appDelegate.envVals["FLUXION_NOTIFY_CREDIT_GRANT"] ?? "false").lowercased() == "true" ? .on : .off
        checkNotifyCreditGrant.target = self
        checkNotifyCreditGrant.action = #selector(autosave)
        let notifyCreditGrantRow = CardRow(
            title: L10n.tr("preferences.credit_grant.title"),
            desc: L10n.tr("preferences.credit_grant.desc"),
            control: checkNotifyCreditGrant,
            isFirst: true
        )

        checkNotifyCreditExpiry = NSSwitch()
        checkNotifyCreditExpiry.state = (appDelegate.envVals["FLUXION_NOTIFY_CREDIT_EXPIRY"] ?? "false").lowercased() == "true" ? .on : .off
        checkNotifyCreditExpiry.target = self
        checkNotifyCreditExpiry.action = #selector(autosave)
        let notifyCreditExpiryRow = CardRow(
            title: L10n.tr("preferences.credit_expiry.title"),
            desc: L10n.tr("preferences.credit_expiry.desc"),
            control: checkNotifyCreditExpiry,
            isFirst: false
        )

        addSection(title: L10n.tr("preferences.section.quota_monitor"), rows: [claudeAutopingRow, codexAutopingRow, antigravityAutopingRow], into: documentStack)
        addSection(title: L10n.tr("preferences.section.auto_ping"), rows: [autoPingEnabledRow], into: documentStack)
        addSection(title: L10n.tr("preferences.section.reset_notifications"), rows: [macOSNotifyRefreshRow, slackNotifyRefreshRow, telegramNotifyRefreshRow, qqbotNotifyRefreshRow, weChatNotifyRefreshRow, lineNotifyRefreshRow, feishuNotifyRefreshRow], into: documentStack)
        addSection(title: L10n.tr("preferences.section.credit_grants"), rows: [notifyCreditGrantRow, notifyCreditExpiryRow], into: documentStack)
    }

    // Sections 7-9.5 (Slack / Telegram / LINE / QQ / WeChat) live in
    // PreferencesWindow+Integrations.swift.

    // MARK: - Section 10: Startup
    func buildStartupSection(into documentStack: NSStackView) {
        checkLaunchAtLogin = NSSwitch()
        if #available(macOS 13.0, *) {
            checkLaunchAtLogin.state = (SMAppService.mainApp.status == .enabled) ? .on : .off
        } else {
            // Launch at Login relies on SMAppService (macOS 13+); on older
            // systems the toggle stays off and disabled.
            checkLaunchAtLogin.state = .off
            checkLaunchAtLogin.isEnabled = false
        }
        lastLaunchAtLoginState = checkLaunchAtLogin.state
        checkLaunchAtLogin.target = self
        checkLaunchAtLogin.action = #selector(autosave)
        let launchAtLoginRow = CardRow(
            title: L10n.tr("preferences.launch_login.title"),
            desc: L10n.tr("preferences.launch_login.desc"),
            control: checkLaunchAtLogin,
            isFirst: true
        )

        var startupRows: [NSView] = [launchAtLoginRow]

        // Auto-update toggle — only in distribution builds where the updater is
        // configured. Off = no background checks (manual "Check for Updates"
        // only); on = gentle background checks. Never installs without asking.
        if appDelegate.updaterController?.isConfigured == true {
            let autoUpdateSwitch = NSSwitch()
            autoUpdateSwitch.state =
                appDelegate.updaterController.automaticallyChecksForUpdates ? .on : .off
            autoUpdateSwitch.target = self
            autoUpdateSwitch.action = #selector(toggleAutoUpdate(_:))
            startupRows.append(CardRow(
                title: L10n.tr("preferences.auto_update"),
                desc: L10n.tr("preferences.auto_update.desc"),
                control: autoUpdateSwitch,
                isFirst: false
            ))
        }

        addSection(title: L10n.tr("preferences.section.startup"), rows: startupRows, into: documentStack)
    }

    // MARK: - Section 11: Fluxion Repository
    func buildRepositorySection(into documentStack: NSStackView) {
        repositoryPathLabel = NSTextField(labelWithString: appDelegate.repoPath)
        repositoryPathLabel.font = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
        repositoryPathLabel.textColor = Palette.primaryText
        repositoryPathLabel.lineBreakMode = .byTruncatingMiddle
        repositoryPathLabel.isSelectable = true
        repositoryPathLabel.isEditable = false
        repositoryPathLabel.isBordered = false
        repositoryPathLabel.drawsBackground = false
        repositoryPathLabel.translatesAutoresizingMaskIntoConstraints = false
        repositoryPathLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        repositoryPathLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        let pathBox = NSView()
        pathBox.translatesAutoresizingMaskIntoConstraints = false
        pathBox.wantsLayer = true
        pathBox.layer?.cornerRadius = 6
        pathBox.layer?.borderWidth = 0.5
        pathBox.layer?.borderColor = Palette.cardBorder.cgColor
        pathBox.layer?.backgroundColor = Palette.windowBackground.cgColor
        pathBox.addSubview(repositoryPathLabel)

        NSLayoutConstraint.activate([
            repositoryPathLabel.leadingAnchor.constraint(equalTo: pathBox.leadingAnchor, constant: 10),
            repositoryPathLabel.trailingAnchor.constraint(equalTo: pathBox.trailingAnchor, constant: -10),
            repositoryPathLabel.topAnchor.constraint(equalTo: pathBox.topAnchor, constant: 6),
            repositoryPathLabel.bottomAnchor.constraint(equalTo: pathBox.bottomAnchor, constant: -6)
        ])

        let changeRepositoryButton = NSButton(
            title: L10n.tr("preferences.change"),
            target: self,
            action: #selector(changeRepository)
        )
        changeRepositoryButton.bezelStyle = .rounded
        changeRepositoryButton.setContentHuggingPriority(.required, for: .horizontal)
        changeRepositoryButton.setContentCompressionResistancePriority(.required, for: .horizontal)

        let repairBackendButton = NSButton(
            title: L10n.tr("preferences.repair_backend"),
            target: self,
            action: #selector(repairBackend)
        )
        repairBackendButton.bezelStyle = .rounded
        repairBackendButton.setContentHuggingPriority(.required, for: .horizontal)
        repairBackendButton.setContentCompressionResistancePriority(.required, for: .horizontal)

        let repositoryControl = NSStackView(views: [pathBox, repairBackendButton, changeRepositoryButton])
        repositoryControl.orientation = .horizontal
        repositoryControl.alignment = .centerY
        repositoryControl.distribution = .fill
        repositoryControl.spacing = 10

        let repositoryRow = CardRowStacked(
            title: L10n.tr("preferences.repository.title"),
            desc: L10n.tr("preferences.repository.desc"),
            control: repositoryControl,
            isFirst: true
        )
        addSection(title: L10n.tr("preferences.section.repository"), rows: [repositoryRow], into: documentStack)
    }
}
