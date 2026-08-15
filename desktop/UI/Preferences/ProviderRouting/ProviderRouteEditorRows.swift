import AppKit
import Foundation

// Row-level building blocks for the route editor sheet: the flattened catalog
// item the pickers bind to, the "runs via" callout, the executor filter, and the
// model row. Kept apart from the controller so the sheet file stays about layout
// and the save flow.

struct ProviderCatalogModelItem {
    let providerId: String
    let executor: String
    let providerDisplayName: String
    let baseModelId: String
    let displayName: String
    let supportedEfforts: [String]
    let runtimeEfforts: [String]
    let inputPrice: Double?
    let outputPrice: Double?
    let pricingSource: String?
    let promo: String?
    let note: String?
    let tag: String?
    let isRetired: Bool
    let effortCapabilitiesKnown: Bool

    var storesEffortInModelId: Bool { !supportedEfforts.isEmpty }

    var configurableEfforts: [String] {
        storesEffortInModelId ? supportedEfforts : runtimeEfforts
    }

    var supportsEffort: Bool { !configurableEfforts.isEmpty }

    var storesEffortInRoute: Bool {
        supportsEffort && !storesEffortInModelId
    }

    func candidateId(forEffort effort: String) -> String {
        if storesEffortInModelId {
            let requestedEffort = effort.lowercased()
            let resolvedEffort = supportedEfforts.contains(requestedEffort)
                ? requestedEffort
                : (supportedEfforts.first ?? requestedEffort)
            return "\(providerId):\(baseModelId)-\(resolvedEffort)"
        } else {
            return "\(providerId):\(baseModelId)"
        }
    }

    func matches(candidate: String) -> (matched: Bool, effort: String?) {
        let parts = candidate.split(separator: ":", maxSplits: 1).map(String.init)
        let prov = parts.count == 2 ? parts[0] : ""
        let modelPart = parts.count == 2 ? parts[1] : candidate

        if !prov.isEmpty && prov != providerId {
            return (false, nil)
        }

        if modelPart == baseModelId {
            return (true, nil)
        }

        if storesEffortInModelId && modelPart.hasPrefix(baseModelId + "-") {
            let suffix = String(modelPart.dropFirst(baseModelId.count + 1)).lowercased()
            if supportedEfforts.contains(suffix) {
                return (true, suffix.capitalized)
            }
        }

        return (false, nil)
    }
}

class ProviderRunsViaCalloutView: NSView {
    private let label = NSTextField(wrappingLabelWithString: "")
    private var isCross: Bool = false

    init() {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 6
        layer?.borderWidth = 0.5

        label.translatesAutoresizingMaskIntoConstraints = false
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        label.font = NSFont.systemFont(ofSize: 12)
        addSubview(label)

        NSLayoutConstraint.activate([
            label.topAnchor.constraint(equalTo: topAnchor, constant: 9),
            label.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -9),
            label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
        ])
        updateColors()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(selectedExecutor: String, originalExecutor: String) {
        isCross = !originalExecutor.isEmpty && selectedExecutor.lowercased() != originalExecutor.lowercased()

        let attr = NSMutableAttributedString()
        let runsVia = String(format: L10n.tr("preferences.provider.editor.runs_via"), selectedExecutor)
        let boldRange = (runsVia as NSString).range(of: selectedExecutor)
        let baseAttr: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .regular),
            .foregroundColor: Palette.primaryText
        ]
        attr.append(NSAttributedString(string: runsVia, attributes: baseAttr))
        if boldRange.location != NSNotFound {
            attr.addAttributes([.font: NSFont.systemFont(ofSize: 12, weight: .bold)], range: boldRange)
        }

        if isCross {
            let warnText = String(
                format: L10n.tr("preferences.provider.editor.cross_executor_warning"),
                originalExecutor,
                selectedExecutor)
            let warnAttr: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 11.5, weight: .regular),
                .foregroundColor: Palette.secondaryText
            ]
            attr.append(NSAttributedString(string: warnText, attributes: warnAttr))
        }

        label.attributedStringValue = attr
        updateColors()
    }

    func updateColors() {
        if isCross {
            layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(hex: "#FFF9E6"),
                dark: NSColor(hex: "#382D14")
            ).cgColor
            layer?.borderColor = NSColor.dynamicColor(
                light: NSColor(hex: "#FFE082"),
                dark: NSColor(hex: "#6D5220")
            ).cgColor
        } else {
            layer?.backgroundColor = Palette.cardBackground.cgColor
            layer?.borderColor = Palette.cardBorder.cgColor
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColors()
    }
}

final class ProviderExecutorFilterButton: NSControl {
    private let title: String
    private let isTabSelected: Bool
    private let containsSelection: Bool
    private let hasWarning: Bool
    private let onSelect: () -> Void
    private var trackingArea: NSTrackingArea?
    private var fillLayer: CAShapeLayer?
    private var strokeLayer: CAShapeLayer?

    override var isFlipped: Bool { true }

    init(
        title: String,
        isSelected: Bool,
        containsSelection: Bool,
        hasWarning: Bool,
        onSelect: @escaping () -> Void
    ) {
        self.title = title
        self.isTabSelected = isSelected
        self.containsSelection = containsSelection
        self.hasWarning = hasWarning
        self.onSelect = onSelect
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true

        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 5
        stack.edgeInsets = NSEdgeInsets(top: 5, left: 9, bottom: 6, right: 9)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor)
        ])

        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = NSFont.systemFont(ofSize: 11.5, weight: isSelected ? .semibold : .medium)
        titleLabel.textColor = isSelected ? Palette.primaryText : Palette.secondaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        stack.addArrangedSubview(titleLabel)

        if containsSelection {
            let dot = NSView()
            dot.translatesAutoresizingMaskIntoConstraints = false
            dot.wantsLayer = true
            dot.layer?.cornerRadius = 2.5
            dot.layer?.backgroundColor = NSColor.controlAccentColor.cgColor
            NSLayoutConstraint.activate([
                dot.widthAnchor.constraint(equalToConstant: 5),
                dot.heightAnchor.constraint(equalToConstant: 5)
            ])
            stack.addArrangedSubview(dot)
        } else if hasWarning {
            let dot = NSView()
            dot.translatesAutoresizingMaskIntoConstraints = false
            dot.wantsLayer = true
            dot.layer?.cornerRadius = 2.5
            dot.layer?.backgroundColor = NSColor.systemRed.cgColor
            NSLayoutConstraint.activate([
                dot.widthAnchor.constraint(equalToConstant: 5),
                dot.heightAnchor.constraint(equalToConstant: 5)
            ])
            stack.addArrangedSubview(dot)
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layout() {
        super.layout()
        updateShapeLayer()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateShapeLayer()
    }

    private func updateShapeLayer() {
        fillLayer?.removeFromSuperlayer()
        fillLayer = nil
        strokeLayer?.removeFromSuperlayer()
        strokeLayer = nil

        guard isTabSelected else {
            layer?.backgroundColor = NSColor.clear.cgColor
            return
        }

        layer?.backgroundColor = NSColor.clear.cgColor

        let r: CGFloat = 6.0
        let w = bounds.width
        let h = bounds.height
        guard w > 0 && h > 0 else { return }

        // 1. Fill Layer: Solid white background extending 2pt down to completely mask out the bottom border line
        let fillPath = CGMutablePath()
        fillPath.move(to: CGPoint(x: 0, y: h + 2))
        fillPath.addLine(to: CGPoint(x: 0, y: r))
        fillPath.addArc(tangent1End: CGPoint(x: 0, y: 0), tangent2End: CGPoint(x: r, y: 0), radius: r)
        fillPath.addLine(to: CGPoint(x: w - r, y: 0))
        fillPath.addArc(tangent1End: CGPoint(x: w, y: 0), tangent2End: CGPoint(x: w, y: r), radius: r)
        fillPath.addLine(to: CGPoint(x: w, y: h + 2))
        fillPath.closeSubpath()

        let fill = CAShapeLayer()
        fill.path = fillPath
        fill.fillColor = Palette.cardBackground.cgColor
        fill.strokeColor = nil
        layer?.insertSublayer(fill, at: 0)
        self.fillLayer = fill

        // 2. Stroke Layer: 3-sided OPEN path (Left -> Top-Left Arc -> Top -> Top-Right Arc -> Right)
        // Strictly NO bottom line, and endpoints stop precisely at the baseline (h - 0.5) with ZERO overshoot!
        let strokePath = CGMutablePath()
        strokePath.move(to: CGPoint(x: 0.25, y: h - 0.5))
        strokePath.addLine(to: CGPoint(x: 0.25, y: r))
        strokePath.addArc(tangent1End: CGPoint(x: 0.25, y: 0.25), tangent2End: CGPoint(x: r, y: 0.25), radius: r)
        strokePath.addLine(to: CGPoint(x: w - r, y: 0.25))
        strokePath.addArc(tangent1End: CGPoint(x: w - 0.25, y: 0.25), tangent2End: CGPoint(x: w - 0.25, y: r), radius: r)
        strokePath.addLine(to: CGPoint(x: w - 0.25, y: h - 0.5))
        // Note: Do NOT call closeSubpath() so the bottom remains completely open!

        let stroke = CAShapeLayer()
        stroke.path = strokePath
        stroke.fillColor = nil
        stroke.strokeColor = Palette.cardBorder.cgColor
        stroke.lineWidth = 0.5
        layer?.insertSublayer(stroke, at: 1)
        self.strokeLayer = stroke
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let ta = trackingArea { removeTrackingArea(ta) }
        let ta = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeInActiveApp], owner: self, userInfo: nil)
        addTrackingArea(ta)
        trackingArea = ta
    }

    override func mouseEntered(with event: NSEvent) {
        if !isTabSelected {
            layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(white: 0.0, alpha: 0.04),
                dark: NSColor(white: 1.0, alpha: 0.06)
            ).cgColor
            layer?.cornerRadius = 6
        }
    }

    override func mouseExited(with event: NSEvent) {
        if !isTabSelected {
            layer?.backgroundColor = NSColor.clear.cgColor
        }
    }

    override func mouseUp(with event: NSEvent) {
        let loc = convert(event.locationInWindow, from: nil)
        if bounds.contains(loc) {
            onSelect()
        }
    }
}

class ProviderModelRowView: NSView {
    let item: ProviderCatalogModelItem
    private let effort: String
    let isRowSelected: Bool
    private let isUsed: Bool
    private let onPick: () -> Void

    private var trackingArea: NSTrackingArea?

    init(
        item: ProviderCatalogModelItem,
        effort: String,
        isSelected: Bool,
        isUsed: Bool,
        onPick: @escaping () -> Void
    ) {
        self.item = item
        self.effort = effort
        self.isRowSelected = isSelected
        self.isUsed = isUsed
        self.onPick = onPick
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 5

        let isBad = item.isRetired || isUsed

        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 8
        stack.edgeInsets = NSEdgeInsets(top: 6, left: 12, bottom: 6, right: 12)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            heightAnchor.constraint(greaterThanOrEqualToConstant: 32)
        ])

        // Left: Name + Tags
        let nameLabel = NSTextField(labelWithString: item.displayName)
        nameLabel.font = NSFont.systemFont(ofSize: 12.5, weight: isSelected ? .semibold : .regular)
        nameLabel.textColor = isSelected ? NSColor.controlAccentColor : Palette.primaryText
        nameLabel.isEditable = false
        nameLabel.isSelectable = false
        nameLabel.isBordered = false
        nameLabel.drawsBackground = false
        stack.addArrangedSubview(nameLabel)

        if let tag = item.tag, !tag.isEmpty {
            let tagView = ProviderTagView(text: tag, isAccent: true, isBold: true)
            stack.addArrangedSubview(tagView)
        }

        if item.isRetired {
            let retView = ProviderTagView(text: L10n.tr("preferences.provider.editor.retired"), isBold: true, isUppercase: true)
            stack.addArrangedSubview(retView)
        } else if isUsed {
            let usedView = ProviderTagView(text: L10n.tr("preferences.provider.editor.in_use"), isBold: true, isUppercase: true)
            stack.addArrangedSubview(usedView)
        }

        if isSelected && item.supportsEffort {
            let effView = ProviderTagView(text: effort, isAccent: true)
            stack.addArrangedSubview(effView)
        }

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        stack.addArrangedSubview(spacer)

        // Right: Price + Provider
        let priceText: String
        if let inputPrice = item.inputPrice, let outputPrice = item.outputPrice {
            priceText = String(format: "$%.2f / $%.2f", inputPrice, outputPrice)
        } else {
            priceText = "—"
        }
        let priceLabel = NSTextField(labelWithString: priceText)
        priceLabel.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        priceLabel.textColor = Palette.secondaryText
        priceLabel.isEditable = false
        priceLabel.isSelectable = false
        priceLabel.isBordered = false
        priceLabel.drawsBackground = false
        stack.addArrangedSubview(priceLabel)

        let provLabel = NSTextField(labelWithString: item.providerDisplayName)
        provLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        provLabel.textColor = Palette.secondaryText
        provLabel.isEditable = false
        provLabel.isSelectable = false
        provLabel.isBordered = false
        provLabel.drawsBackground = false
        stack.addArrangedSubview(provLabel)

        if isBad {
            alphaValue = 0.45
        } else if isSelected {
            layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.12).cgColor
            layer?.borderColor = NSColor.controlAccentColor.withAlphaComponent(0.35).cgColor
            layer?.borderWidth = 1
        }

        // Overlay click button
        let clickBtn = NSButton(frame: .zero)
        clickBtn.isTransparent = true
        clickBtn.target = self
        clickBtn.action = #selector(clicked(_:))
        clickBtn.isEnabled = !isBad
        clickBtn.translatesAutoresizingMaskIntoConstraints = false
        addSubview(clickBtn)
        NSLayoutConstraint.activate([
            clickBtn.topAnchor.constraint(equalTo: topAnchor),
            clickBtn.bottomAnchor.constraint(equalTo: bottomAnchor),
            clickBtn.leadingAnchor.constraint(equalTo: leadingAnchor),
            clickBtn.trailingAnchor.constraint(equalTo: trailingAnchor)
        ])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func clicked(_ sender: Any) {
        onPick()
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let ta = trackingArea {
            removeTrackingArea(ta)
        }
        let ta = NSTrackingArea(
            rect: .zero,
            options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(ta)
        trackingArea = ta
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        NotificationCenter.default.removeObserver(self, name: NSView.boundsDidChangeNotification, object: nil)
        if window != nil, let clipView = enclosingScrollView?.contentView {
            clipView.postsBoundsChangedNotifications = true
            NotificationCenter.default.addObserver(
                self,
                selector: #selector(clipViewDidScrollNotification(_:)),
                name: NSView.boundsDidChangeNotification,
                object: clipView
            )
        }
        updateHoverBackground(isHovered: false)
    }

    override func viewWillMove(toWindow newWindow: NSWindow?) {
        super.viewWillMove(toWindow: newWindow)
        if newWindow == nil {
            NotificationCenter.default.removeObserver(self, name: NSView.boundsDidChangeNotification, object: nil)
        }
    }

    @objc private func clipViewDidScrollNotification(_ notification: Notification) {
        checkCurrentHover()
    }

    private func checkCurrentHover() {
        guard !isRowSelected && !isUsed && !item.isRetired else { return }
        guard let win = window else {
            updateHoverBackground(isHovered: false)
            return
        }
        let mouseInScreen = NSEvent.mouseLocation
        let mouseInWin = win.convertPoint(fromScreen: mouseInScreen)
        let mouseInView = convert(mouseInWin, from: nil)
        let isInside = bounds.contains(mouseInView) && visibleRect.contains(mouseInView)
        updateHoverBackground(isHovered: isInside)
    }

    private func updateHoverBackground(isHovered: Bool) {
        guard !isRowSelected && !isUsed && !item.isRetired else { return }
        if isHovered {
            layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(white: 0.0, alpha: 0.04),
                dark: NSColor(white: 1.0, alpha: 0.06)
            ).cgColor
        } else {
            layer?.backgroundColor = NSColor.clear.cgColor
        }
    }

    override func mouseEntered(with event: NSEvent) {
        super.mouseEntered(with: event)
        updateHoverBackground(isHovered: true)
    }

    override func mouseExited(with event: NSEvent) {
        super.mouseExited(with: event)
        updateHoverBackground(isHovered: false)
    }
}
