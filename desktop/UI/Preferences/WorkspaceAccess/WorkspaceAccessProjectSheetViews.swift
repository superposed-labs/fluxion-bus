import AppKit
import Foundation

// Building blocks of the add/edit project sheet: the access-choice rows, the
// scrollable form body and the text fields. They are split out so the sheet
// controller itself stays about flow and validation.

private final class WorkspaceProjectAccessBadgeView: NSView {
    private let iconView = NSImageView()
    private let label = NSTextField(labelWithString: "")

    // Both access choices should align as a visual pair. Calculate the width
    // from the wider localized title so this remains correct outside Chinese.
    static var uniformWidth: CGFloat {
        let font = NSFont.systemFont(ofSize: 12, weight: .medium)
        let titles = [
            L10n.tr("preferences.workspace_access.access.read_only"),
            L10n.tr("preferences.workspace_access.access.read_write")
        ]
        let titleWidth = titles
            .map { ($0 as NSString).size(withAttributes: [.font: font]).width }
            .max() ?? 24
        return ceil(titleWidth) + 7 + 13 + 5 + 8
    }

    init(isWrite: Bool) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 6
        layer?.borderWidth = 0.5

        let writeBackground = NSColor.dynamicColor(
            light: NSColor(hex: "#FFF4DF"),
            dark: NSColor(hex: "#4A3920")
        )
        let writeBorder = NSColor.dynamicColor(
            light: NSColor(hex: "#F2C078"),
            dark: NSColor(hex: "#A96E22")
        )
        let writeText = NSColor.dynamicColor(
            light: NSColor(hex: "#8A5200"),
            dark: NSColor(hex: "#FFD28A")
        )

        if isWrite {
            layer?.backgroundColor = writeBackground.cgColor
            layer?.borderColor = writeBorder.cgColor
            label.textColor = writeText
            iconView.image = WorkspaceAccessIcons.pencilImage(color: writeText)
            label.stringValue = L10n.tr("preferences.workspace_access.access.read_write")
        } else {
            layer?.backgroundColor = NSColor.clear.cgColor
            layer?.borderColor = Palette.separator.withAlphaComponent(0.75).cgColor
            label.textColor = Palette.primaryText
            iconView.image = WorkspaceAccessIcons.eyeImage(color: Palette.primaryText)
            label.stringValue = L10n.tr("preferences.workspace_access.access.read_only")
        }

        label.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        label.translatesAutoresizingMaskIntoConstraints = false

        iconView.translatesAutoresizingMaskIntoConstraints = false
        iconView.imageScaling = .scaleProportionallyUpOrDown

        addSubview(iconView)
        addSubview(label)

        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 22),
            iconView.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 7),
            iconView.centerYAnchor.constraint(equalTo: centerYAnchor),
            iconView.widthAnchor.constraint(equalToConstant: 13),
            iconView.heightAnchor.constraint(equalToConstant: 13),
            label.leadingAnchor.constraint(equalTo: iconView.trailingAnchor, constant: 5),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -8),
            label.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

final class WorkspaceProjectAccessOptionRow: NSView {
    let radioButton = NSButton()
    var onSelect: (() -> Void)?

    private let labelStack = NSStackView()
    private let separator = NSView()

    init(description: String, isWrite: Bool, isFirst: Bool) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        let badge = WorkspaceProjectAccessBadgeView(isWrite: isWrite)
        labelStack.orientation = .vertical
        labelStack.alignment = .leading
        labelStack.spacing = 4
        labelStack.translatesAutoresizingMaskIntoConstraints = false
        labelStack.addArrangedSubview(badge)
        badge.widthAnchor.constraint(equalToConstant: WorkspaceProjectAccessBadgeView.uniformWidth).isActive = true

        let descriptionLabel = NSTextField(wrappingLabelWithString: description)
        descriptionLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        descriptionLabel.textColor = Palette.secondaryText
        descriptionLabel.isEditable = false
        descriptionLabel.isSelectable = false
        descriptionLabel.isBordered = false
        descriptionLabel.drawsBackground = false
        descriptionLabel.cell?.wraps = true
        descriptionLabel.cell?.isScrollable = false
        descriptionLabel.maximumNumberOfLines = 0
        descriptionLabel.lineBreakMode = .byWordWrapping
        descriptionLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        descriptionLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        descriptionLabel.translatesAutoresizingMaskIntoConstraints = false
        labelStack.addArrangedSubview(descriptionLabel)

        radioButton.setButtonType(.radio)
        radioButton.title = ""
        radioButton.isBordered = false
        radioButton.controlSize = .regular
        radioButton.target = self
        radioButton.action = #selector(radioClicked)
        radioButton.translatesAutoresizingMaskIntoConstraints = false

        separator.translatesAutoresizingMaskIntoConstraints = false
        separator.wantsLayer = true
        separator.layer?.backgroundColor = Palette.separator.cgColor
        separator.isHidden = isFirst

        addSubview(labelStack)
        addSubview(radioButton)
        addSubview(separator)

        NSLayoutConstraint.activate([
            heightAnchor.constraint(greaterThanOrEqualToConstant: 61),
            labelStack.topAnchor.constraint(equalTo: topAnchor, constant: 8),
            labelStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            labelStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -8),
            labelStack.trailingAnchor.constraint(lessThanOrEqualTo: radioButton.leadingAnchor, constant: -14),
            radioButton.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            radioButton.centerYAnchor.constraint(equalTo: centerYAnchor),
            radioButton.widthAnchor.constraint(equalToConstant: 20),
            radioButton.heightAnchor.constraint(equalToConstant: 20),
            separator.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            separator.trailingAnchor.constraint(equalTo: trailingAnchor),
            separator.topAnchor.constraint(equalTo: topAnchor),
            separator.heightAnchor.constraint(equalToConstant: 0.5),
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        separator.layer?.backgroundColor = Palette.separator.cgColor
    }

    func setSelected(_ selected: Bool) {
        radioButton.state = selected ? .on : .off
    }

    // Make the whole option row selectable, while preserving the native
    // radio button's own accessibility and keyboard behavior.
    override func hitTest(_ point: NSPoint) -> NSView? {
        guard bounds.contains(point) else { return nil }
        let radioPoint = convert(point, to: radioButton)
        if radioButton.bounds.contains(radioPoint) {
            return radioButton
        }
        return self
    }

    override func mouseDown(with event: NSEvent) {
        onSelect?()
    }

    @objc private func radioClicked() {
        onSelect?()
    }
}

final class WorkspaceProjectFormDocumentView: NSView {
    override var isFlipped: Bool { true }
}

class VerticallyCenteredTextFieldCell: NSTextFieldCell {
    var horizontalPadding: CGFloat = 8

    override func drawingRect(forBounds rect: NSRect) -> NSRect {
        var textRect = super.drawingRect(forBounds: rect)
        let textSize = cellSize(forBounds: rect)
        let delta = floor((textRect.height - textSize.height) / 2.0)
        if delta > 0 {
            textRect.origin.y += delta
            textRect.size.height = textSize.height
        }
        textRect.origin.x += horizontalPadding
        textRect.size.width = max(0, textRect.width - (horizontalPadding * 2))
        return textRect
    }

    override func edit(withFrame rect: NSRect, in controlView: NSView, editor textObj: NSText, delegate: Any?, event: NSEvent?) {
        super.edit(withFrame: drawingRect(forBounds: rect), in: controlView, editor: textObj, delegate: delegate, event: event)
    }

    override func select(withFrame rect: NSRect, in controlView: NSView, editor textObj: NSText, delegate: Any?, start selStart: Int, length selLength: Int) {
        super.select(withFrame: drawingRect(forBounds: rect), in: controlView, editor: textObj, delegate: delegate, start: selStart, length: selLength)
    }
}

class WorkspaceAccessInputField: NSTextField {
    init(placeholder: String = "") {
        super.init(frame: .zero)
        let cell = VerticallyCenteredTextFieldCell(textCell: "")
        cell.isScrollable = true
        cell.placeholderString = placeholder
        self.cell = cell
        self.font = NSFont.systemFont(ofSize: 12.5)
        self.isBordered = false
        self.focusRingType = .none
        self.drawsBackground = false
        self.wantsLayer = true
        self.layer?.cornerRadius = 6
        self.layer?.masksToBounds = true
        self.translatesAutoresizingMaskIntoConstraints = false
        updateAppearance()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }

    private func updateAppearance() {
        layer?.backgroundColor = Palette.cardBackground.cgColor
        layer?.borderColor = Palette.cardBorder.cgColor
        layer?.borderWidth = 0.5
    }
}

final class WorkspaceProjectPathField: NSTextField {
    init() {
        super.init(frame: .zero)
        let cell = VerticallyCenteredTextFieldCell(textCell: "")
        cell.horizontalPadding = 10
        self.cell = cell
        self.font = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
        self.textColor = Palette.primaryText
        self.lineBreakMode = .byTruncatingMiddle
        self.usesSingleLineMode = true
        self.isEditable = false
        self.isSelectable = true
        self.isBordered = false
        self.focusRingType = .none
        self.drawsBackground = false
        self.alignment = .left
        self.wantsLayer = true
        self.layer?.cornerRadius = 6
        self.layer?.masksToBounds = true
        self.translatesAutoresizingMaskIntoConstraints = false
        updateAppearance()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layout() {
        super.layout()
        updateAppearance()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }

    private func updateAppearance() {
        layer?.backgroundColor = Palette.cardBackground.cgColor
        layer?.borderColor = Palette.cardBorder.cgColor
        layer?.borderWidth = 0.5
    }
}
