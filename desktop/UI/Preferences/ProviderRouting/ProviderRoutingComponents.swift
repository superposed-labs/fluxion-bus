import AppKit
import Foundation

// Reusable chrome shared by every provider-routing surface: status pills,
// badges, tags and the two accent controls. Nothing here knows about routing
// state — callers pass in text and tone.

// MARK: - Flipped Containers

class ProviderFlippedClipView: NSClipView {
    override var isFlipped: Bool { true }
}

class ProviderFlippedStackView: NSStackView {
    override var isFlipped: Bool { true }
}

// MARK: - Helper UI Components

enum ProviderPillTone {
    case ok
    case warn
    case idle
    case error

    var textColor: NSColor {
        switch self {
        case .ok:
            return NSColor.dynamicColor(
                light: NSColor(hex: "#248A3D"), dark: NSColor(hex: "#30D158"))
        case .warn:
            return NSColor.dynamicColor(
                light: NSColor(hex: "#C96D00"), dark: NSColor(hex: "#FF9F0A"))
        case .idle:
            return Palette.secondaryText
        case .error:
            return NSColor.dynamicColor(
                light: NSColor(hex: "#D70015"), dark: NSColor(hex: "#FF453A"))
        }
    }

    var backgroundColor: NSColor {
        switch self {
        case .ok:
            return NSColor.dynamicColor(
                light: NSColor(hex: "#EAF7ED"), dark: NSColor(hex: "#1C3722"))
        case .warn:
            return NSColor.dynamicColor(
                light: NSColor(hex: "#FFF4E5"), dark: NSColor(hex: "#3A2A14"))
        case .idle:
            return NSColor.dynamicColor(
                light: NSColor(white: 0.5, alpha: 0.12),
                dark: NSColor(white: 0.5, alpha: 0.16))
        case .error:
            return NSColor.dynamicColor(
                light: NSColor(hex: "#FDEAE8"), dark: NSColor(hex: "#3E1E1E"))
        }
    }
}

class ProviderPillView: NSView {
    private let dot = NSView()
    private let label = NSTextField(labelWithString: "")

    var tone: ProviderPillTone = .ok {
        didSet { updateAppearance() }
    }

    var text: String = "" {
        didSet { label.stringValue = text }
    }

    init(tone: ProviderPillTone, text: String) {
        self.tone = tone
        self.text = text
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 10
        layer?.masksToBounds = true

        dot.translatesAutoresizingMaskIntoConstraints = false
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 3
        addSubview(dot)

        label.translatesAutoresizingMaskIntoConstraints = false
        label.stringValue = text
        label.font = NSFont.systemFont(ofSize: 11.5, weight: .semibold)
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        addSubview(label)

        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 20),
            dot.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 7),
            dot.centerYAnchor.constraint(equalTo: centerYAnchor),
            dot.widthAnchor.constraint(equalToConstant: 6),
            dot.heightAnchor.constraint(equalToConstant: 6),

            label.leadingAnchor.constraint(equalTo: dot.trailingAnchor, constant: 5),
            label.centerYAnchor.constraint(equalTo: centerYAnchor, constant: -0.5),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -8)
        ])

        updateAppearance()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func updateAppearance() {
        layer?.backgroundColor = tone.backgroundColor.cgColor
        dot.layer?.backgroundColor = tone.textColor.cgColor
        label.textColor = tone.textColor
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }
}

class ProviderBadgeView: NSView {
    private let label = NSTextField(labelWithString: "")

    init(text: String) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 4
        layer?.masksToBounds = true

        label.translatesAutoresizingMaskIntoConstraints = false
        label.stringValue = text
        label.font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular)
        label.textColor = Palette.secondaryText
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        addSubview(label)

        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 5),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -5),
            label.topAnchor.constraint(equalTo: topAnchor, constant: 1.5),
            label.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -1.5)
        ])

        updateColors()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func updateColors() {
        layer?.backgroundColor = NSColor.dynamicColor(
            light: NSColor(white: 0.5, alpha: 0.09),
            dark: NSColor(white: 0.5, alpha: 0.16)
        ).cgColor
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColors()
    }
}

class ProviderTagView: NSView {
    private let label = NSTextField(labelWithString: "")
    private let isAccent: Bool

    init(text: String, isAccent: Bool = false, isBold: Bool = false, isUppercase: Bool = false) {
        self.isAccent = isAccent
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 4
        layer?.borderWidth = 0.5

        label.translatesAutoresizingMaskIntoConstraints = false
        label.stringValue = isUppercase ? text.uppercased() : text
        label.font = isBold
            ? NSFont.systemFont(ofSize: 9.5, weight: .bold)
            : NSFont.systemFont(ofSize: 10, weight: .medium)
        label.textColor = isAccent ? NSColor.controlAccentColor : Palette.secondaryText
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        addSubview(label)

        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 5),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -5),
            label.topAnchor.constraint(equalTo: topAnchor, constant: 1.5),
            label.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -1.5)
        ])

        updateColors()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func updateColors() {
        layer?.borderColor = isAccent
            ? NSColor.controlAccentColor.withAlphaComponent(0.4).cgColor
            : Palette.cardBorder.cgColor
        layer?.backgroundColor = Palette.cardBackground.cgColor
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColors()
    }
}

class ProviderAccentButton: NSButton {
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 6
        layer?.masksToBounds = true
        isBordered = false
        font = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
        contentTintColor = .white
        updateAppearance()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func updateAppearance() {
        layer?.backgroundColor = NSColor.controlAccentColor.cgColor
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }

    override var intrinsicContentSize: NSSize {
        let size = super.intrinsicContentSize
        return NSSize(width: max(size.width + 24, 110), height: 28)
    }
}

class ProviderDisclosureButton: NSButton {
    var isOpen: Bool = false {
        didSet { updateButtonTitle() }
    }
    private let baseTitle: String

    init(title: String, isOpen: Bool, target: AnyObject?, action: Selector) {
        self.baseTitle = title
        self.isOpen = isOpen
        super.init(frame: .zero)
        self.target = target
        self.action = action
        self.isBordered = false
        self.font = NSFont.systemFont(ofSize: 12, weight: .regular)
        self.contentTintColor = Palette.secondaryText
        self.translatesAutoresizingMaskIntoConstraints = false
        updateButtonTitle()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func updateButtonTitle() {
        let arrow = isOpen ? "▾ " : "▸ "
        self.title = arrow + baseTitle
    }
}
