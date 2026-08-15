import AppKit
import Foundation

// MARK: - Card Container View
class CardView: NSView {
    let stackView = NSStackView()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 10
        layer?.masksToBounds = true

        stackView.orientation = .vertical
        stackView.spacing = 0
        stackView.alignment = .leading
        stackView.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stackView)

        NSLayoutConstraint.activate([
            stackView.topAnchor.constraint(equalTo: topAnchor),
            stackView.leadingAnchor.constraint(equalTo: leadingAnchor),
            stackView.trailingAnchor.constraint(equalTo: trailingAnchor),
            stackView.bottomAnchor.constraint(equalTo: bottomAnchor)
        ])

        updateColors()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func updateColors() {
        layer?.backgroundColor = Palette.cardBackground.cgColor
        layer?.borderColor = Palette.cardBorder.cgColor
        layer?.borderWidth = 0.5
    }

    override func layout() {
        super.layout()
        updateColors()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColors()
    }
}

// MARK: - Accent Banner Card View (Left accent border)
class AccentBannerCardView: CardView {
    private let accentLayer = CALayer()
    var accentColor: NSColor = NSColor.controlAccentColor {
        didSet { updateColors() }
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        accentLayer.zPosition = 10
        layer?.addSublayer(accentLayer)
        updateColors()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func updateColors() {
        super.updateColors()
        accentLayer.backgroundColor = accentColor.cgColor
    }

    override func layout() {
        super.layout()
        accentLayer.frame = CGRect(x: 0, y: 0, width: 3.5, height: bounds.height)
    }
}

// MARK: - Card Row Base
/// Shared scaffolding for a settings row: a top separator plus a leading label
/// stack (title + optional description). Subclasses add their control layout.
class CardRowBase: NSView {
    let labelStack = NSStackView()
    let titleLabel = NSTextField(labelWithString: "")
    var descLabel: NSTextField?
    let separator = NSView()

    init(title: String, desc: String?, isFirst: Bool) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        // Separator line
        separator.translatesAutoresizingMaskIntoConstraints = false
        separator.wantsLayer = true
        separator.layer?.backgroundColor = Palette.separator.cgColor
        addSubview(separator)

        NSLayoutConstraint.activate([
            separator.topAnchor.constraint(equalTo: topAnchor),
            separator.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            separator.trailingAnchor.constraint(equalTo: trailingAnchor),
            separator.heightAnchor.constraint(equalToConstant: 0.5)
        ])
        separator.isHidden = isFirst

        // Label Stack
        labelStack.orientation = .vertical
        labelStack.alignment = .leading
        labelStack.spacing = 2
        labelStack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(labelStack)

        titleLabel.stringValue = title
        titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        titleLabel.textColor = Palette.primaryText
        Self.configureStaticLabel(titleLabel)
        labelStack.addArrangedSubview(titleLabel)

        if let desc = desc {
            let dLabel = NSTextField(wrappingLabelWithString: desc)
            dLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
            dLabel.textColor = Palette.secondaryText
            dLabel.cell?.wraps = true
            dLabel.cell?.isScrollable = false
            dLabel.maximumNumberOfLines = 0
            dLabel.lineBreakMode = .byWordWrapping
            // Let the description wrap to the available width instead of
            // stretching under the trailing control and overlapping it.
            dLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            dLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
            Self.configureStaticLabel(dLabel)
            labelStack.addArrangedSubview(dLabel)
            self.descLabel = dLabel
        }
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private static func configureStaticLabel(_ label: NSTextField) {
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        separator.layer?.backgroundColor = Palette.separator.cgColor
    }
}

// MARK: - Standard Card Row (control trailing)
class CardRow: CardRowBase {
    let controlContainer = NSView()

    init(title: String, desc: String? = nil, control: NSView, isFirst: Bool) {
        if let sw = control as? NSSwitch {
            sw.controlSize = .small
        }
        super.init(title: title, desc: desc, isFirst: isFirst)

        labelStack.setContentHuggingPriority(.defaultLow, for: .horizontal)

        controlContainer.translatesAutoresizingMaskIntoConstraints = false
        addSubview(controlContainer)

        control.translatesAutoresizingMaskIntoConstraints = false
        controlContainer.addSubview(control)

        NSLayoutConstraint.activate([
            // Center vertically and pin trailing so the control keeps its
            // intrinsic size.  Four-edge pinning can cause NSSwitch to
            // render with an incorrect frame (all-blue, no knob) when the
            // parent view transitions from hidden to visible.
            control.centerYAnchor.constraint(equalTo: controlContainer.centerYAnchor),
            control.trailingAnchor.constraint(equalTo: controlContainer.trailingAnchor),
            controlContainer.heightAnchor.constraint(greaterThanOrEqualTo: control.heightAnchor),
            controlContainer.widthAnchor.constraint(equalTo: control.widthAnchor),

            labelStack.topAnchor.constraint(equalTo: topAnchor, constant: 11),
            labelStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            labelStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -11),

            controlContainer.centerYAnchor.constraint(equalTo: centerYAnchor),
            controlContainer.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            controlContainer.leadingAnchor.constraint(greaterThanOrEqualTo: labelStack.trailingAnchor, constant: 16),

            heightAnchor.constraint(greaterThanOrEqualToConstant: 46)
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

// MARK: - Stacked Card Row (control below label)
class CardRowStacked: CardRowBase {
    init(title: String, desc: String? = nil, control: NSView, isFirst: Bool) {
        super.init(title: title, desc: desc, isFirst: isFirst)

        control.translatesAutoresizingMaskIntoConstraints = false
        addSubview(control)

        NSLayoutConstraint.activate([
            labelStack.topAnchor.constraint(equalTo: topAnchor, constant: 11),
            labelStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            labelStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),

            control.topAnchor.constraint(equalTo: labelStack.bottomAnchor, constant: 9),
            control.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            control.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            control.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -11)
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

// MARK: - Click-To-Unfocus Window
class ClickToUnfocusWindow: NSWindow {
    override func mouseDown(with event: NSEvent) {
        self.makeFirstResponder(nil)
        super.mouseDown(with: event)
    }
}
