import AppKit
import Foundation

// Reusable chrome shared by every Workspace Access surface: access/status
// badges, the origin pill, and the accent/standard/link buttons. Nothing here
// knows about workspace state — callers pass in text and tone.

final class WorkspaceAccessBadgeView: NSView {
    private let iconView = NSImageView()
    private let label = NSTextField(labelWithString: "")

    init(isWrite: Bool) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 5

        let writeBg = NSColor.dynamicColor(
            light: NSColor(hex: "#EEF2FF"),
            dark: NSColor(hex: "#2B2D42")
        )
        let writeBorder = NSColor.dynamicColor(
            light: NSColor(hex: "#C7D2FE"),
            dark: NSColor(hex: "#4F46E5").withAlphaComponent(0.6)
        )
        let writeText = NSColor.dynamicColor(
            light: NSColor(hex: "#3730A3"),
            dark: NSColor(hex: "#E0E7FF")
        )

        let readBorder = Palette.separator.withAlphaComponent(0.6)
        let readText = Palette.primaryText

        if isWrite {
            layer?.backgroundColor = writeBg.cgColor
            layer?.borderColor = writeBorder.cgColor
            layer?.borderWidth = 0.5
            label.textColor = writeText
            iconView.image = WorkspaceAccessIcons.pencilImage(color: writeText)
            label.stringValue = L10n.tr("preferences.workspace_access.access.read_write")
        } else {
            layer?.backgroundColor = NSColor.clear.cgColor
            layer?.borderColor = readBorder.cgColor
            layer?.borderWidth = 0.5
            label.textColor = readText
            iconView.image = WorkspaceAccessIcons.eyeImage(color: readText)
            label.stringValue = L10n.tr("preferences.workspace_access.access.read_only")
        }

        label.font = NSFont.systemFont(ofSize: 11, weight: .medium)
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
            heightAnchor.constraint(equalToConstant: 20),
            iconView.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 6),
            iconView.centerYAnchor.constraint(equalTo: centerYAnchor),
            iconView.widthAnchor.constraint(equalToConstant: 12),
            iconView.heightAnchor.constraint(equalToConstant: 12),

            label.leadingAnchor.constraint(equalTo: iconView.trailingAnchor, constant: 4),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -6),
            label.centerYAnchor.constraint(equalTo: centerYAnchor)
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

final class WorkspaceStatusBadgeView: NSView {
    private let iconView = NSImageView()
    private let label = NSTextField(labelWithString: "")

    init(entry: WorkspaceAccessEntryRow) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        if entry.isBlocked {
            iconView.image = WorkspaceAccessIcons.blockImage()
            label.stringValue = L10n.tr("preferences.workspace_access.status.blocked")
            label.textColor = NSColor.systemRed
        } else if entry.isMissing {
            iconView.image = WorkspaceAccessIcons.warnImage()
            label.stringValue = L10n.tr("preferences.workspace_access.status.missing")
            label.textColor = NSColor.systemOrange
        } else {
            iconView.image = WorkspaceAccessIcons.checkImage()
            label.stringValue = L10n.tr("preferences.workspace_access.status.available")
            label.textColor = Palette.primaryText
        }

        label.font = NSFont.systemFont(ofSize: 11, weight: .regular)
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
            heightAnchor.constraint(equalToConstant: 20),
            iconView.leadingAnchor.constraint(equalTo: leadingAnchor),
            iconView.centerYAnchor.constraint(equalTo: centerYAnchor),
            iconView.widthAnchor.constraint(equalToConstant: 12),
            iconView.heightAnchor.constraint(equalToConstant: 12),

            label.leadingAnchor.constraint(equalTo: iconView.trailingAnchor, constant: 5),
            label.trailingAnchor.constraint(equalTo: trailingAnchor),
            label.centerYAnchor.constraint(equalTo: centerYAnchor)
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

final class WorkspacePillView: NSView {
    init(text: String, isWarning: Bool = false) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 9
        layer?.masksToBounds = true

        let dot = NSView()
        dot.translatesAutoresizingMaskIntoConstraints = false
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 3

        let label = NSTextField(labelWithString: text)
        label.font = NSFont.systemFont(ofSize: 10, weight: .semibold)
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        label.translatesAutoresizingMaskIntoConstraints = false

        if isWarning {
            layer?.backgroundColor = NSColor.systemOrange.withAlphaComponent(0.14).cgColor
            dot.layer?.backgroundColor = NSColor.systemOrange.cgColor
            label.textColor = NSColor.systemOrange
        } else {
            layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.12).cgColor
            dot.layer?.backgroundColor = NSColor.controlAccentColor.cgColor
            label.textColor = NSColor.controlAccentColor
        }

        addSubview(dot)
        addSubview(label)

        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 18),
            dot.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 6),
            dot.centerYAnchor.constraint(equalTo: centerYAnchor),
            dot.widthAnchor.constraint(equalToConstant: 6),
            dot.heightAnchor.constraint(equalToConstant: 6),

            label.leadingAnchor.constraint(equalTo: dot.trailingAnchor, constant: 4),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -7),
            label.centerYAnchor.constraint(equalTo: centerYAnchor)
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

enum WorkspaceAccessButtonStyle {
    case accent
    case standard
    case plainLink
}

class WorkspaceAccessStyledButton: NSButton {
    let style: WorkspaceAccessButtonStyle
    var isHovered: Bool = false { didSet { needsDisplay = true } }
    var isPressed: Bool = false { didSet { needsDisplay = true } }
    private var trackingArea: NSTrackingArea?

    init(title: String, style: WorkspaceAccessButtonStyle = .standard, target: AnyObject? = nil, action: Selector? = nil) {
        self.style = style
        super.init(frame: .zero)
        self.title = title
        self.target = target
        self.action = action
        self.isBordered = false
        self.wantsLayer = true
        self.translatesAutoresizingMaskIntoConstraints = false
        self.focusRingType = .none
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func updateTrackingAreas() {
        if let trackingArea = trackingArea {
            removeTrackingArea(trackingArea)
        }
        let options: NSTrackingArea.Options = [.mouseEnteredAndExited, .activeAlways]
        trackingArea = NSTrackingArea(rect: bounds, options: options, owner: self, userInfo: nil)
        addTrackingArea(trackingArea!)
        super.updateTrackingAreas()
    }

    override func mouseEntered(with event: NSEvent) {
        isHovered = true
        if style == .plainLink {
            NSCursor.pointingHand.push()
        }
    }

    override func mouseExited(with event: NSEvent) {
        isHovered = false
        if style == .plainLink {
            NSCursor.pop()
        }
    }

    override func mouseDown(with event: NSEvent) {
        isPressed = true
        super.mouseDown(with: event)
        isPressed = false
    }

    override var intrinsicContentSize: NSSize {
        if style == .plainLink {
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 11, weight: .medium)
            ]
            let size = title.size(withAttributes: attrs)
            return NSSize(width: ceil(size.width) + 4, height: 20)
        } else {
            let font = NSFont.systemFont(ofSize: 12, weight: style == .accent ? .medium : .regular)
            let size = title.size(withAttributes: [.font: font])
            return NSSize(width: max(ceil(size.width) + 24, 76), height: 26)
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        switch style {
        case .plainLink:
            let paragraphStyle = NSMutableParagraphStyle()
            paragraphStyle.alignment = .right
            let textColor = isHovered ? NSColor.controlAccentColor.withAlphaComponent(0.8) : NSColor.controlAccentColor
            let font = NSFont.systemFont(ofSize: 11, weight: .medium)
            let attrs: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: textColor,
                .paragraphStyle: paragraphStyle
            ]
            let size = title.size(withAttributes: attrs)
            let rect = NSRect(
                x: bounds.width - size.width,
                y: (bounds.height - size.height) / 2 - 0.5,
                width: size.width,
                height: size.height
            )
            title.draw(in: rect, withAttributes: attrs)

        case .accent:
            let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: 6, yRadius: 6)
            var baseColor = NSColor.controlAccentColor
            if isPressed {
                baseColor = baseColor.shadow(withLevel: 0.15) ?? baseColor
            } else if isHovered {
                baseColor = baseColor.highlight(withLevel: 0.08) ?? baseColor
            }
            baseColor.setFill()
            path.fill()

            let paragraphStyle = NSMutableParagraphStyle()
            paragraphStyle.alignment = .center
            let font = NSFont.systemFont(ofSize: 12, weight: .medium)
            let attrs: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: NSColor.white,
                .paragraphStyle: paragraphStyle
            ]
            let size = title.size(withAttributes: attrs)
            let rect = NSRect(
                x: (bounds.width - size.width) / 2,
                y: (bounds.height - size.height) / 2 - 0.5,
                width: size.width,
                height: size.height
            )
            title.draw(in: rect, withAttributes: attrs)

        case .standard:
            let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: 6, yRadius: 6)
            let bgColor = isPressed
                ? NSColor.dynamicColor(light: NSColor(hex: "#E5E6E8"), dark: NSColor(hex: "#2B2C2E"))
                : (isHovered
                    ? NSColor.dynamicColor(light: NSColor(hex: "#F0F1F2"), dark: NSColor(hex: "#38393B"))
                    : Palette.cardBackground)
            bgColor.setFill()
            path.fill()

            let borderColor = Palette.cardBorder
            borderColor.setStroke()
            path.lineWidth = 0.5
            path.stroke()

            let paragraphStyle = NSMutableParagraphStyle()
            paragraphStyle.alignment = .center
            let font = NSFont.systemFont(ofSize: 12, weight: .regular)
            let attrs: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: Palette.primaryText,
                .paragraphStyle: paragraphStyle
            ]
            let size = title.size(withAttributes: attrs)
            let rect = NSRect(
                x: (bounds.width - size.width) / 2,
                y: (bounds.height - size.height) / 2 - 0.5,
                width: size.width,
                height: size.height
            )
            title.draw(in: rect, withAttributes: attrs)
        }
    }
}

final class WorkspaceRequestActionButton: WorkspaceAccessStyledButton {
    let request: WorkspaceAccessRequestRow
    let actionHandler: (WorkspaceAccessRequestRow) -> Void

    init(title: String, style: WorkspaceAccessButtonStyle = .standard, request: WorkspaceAccessRequestRow, actionHandler: @escaping (WorkspaceAccessRequestRow) -> Void) {
        self.request = request
        self.actionHandler = actionHandler
        super.init(title: title, style: style, target: nil, action: nil)
        self.target = self
        self.action = #selector(btnClicked)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    @objc private func btnClicked() {
        actionHandler(request)
    }
}

/// One-line "who / what access / when" summary shared by the pending-request
/// card and the request sheets.
func makeWorkspaceRequestMetaLabel(req: WorkspaceAccessRequestRow) -> NSTextField {
    let accessName = req.isWrite
        ? L10n.tr("preferences.workspace_access.access.read_write")
        : L10n.tr("preferences.workspace_access.access.read_only")
    let expiresText = WorkspaceAccessTimeFormatter.formatExpires(from: req.expiresAt)

    let metaText: String
    if !req.createdAt.isEmpty, let createdAt = WorkspaceAccessTimeFormatter.formatRelativeAt(from: req.createdAt) {
        metaText = L10n.tr("preferences.workspace_access.requests.meta_with_at", req.requesterDisplayName, accessName, createdAt, expiresText)
    } else {
        metaText = L10n.tr("preferences.workspace_access.requests.meta", req.requesterDisplayName, accessName, expiresText)
    }

    let label = NSTextField(labelWithString: "")
    label.isEditable = false
    label.isSelectable = false
    label.isBordered = false
    label.drawsBackground = false

    let font = NSFont.systemFont(ofSize: 11, weight: .regular)
    let boldFont = NSFont.systemFont(ofSize: 11, weight: .semibold)
    let primaryColor = Palette.primaryText
    let secondaryColor = Palette.secondaryText

    let attr = NSMutableAttributedString(string: metaText, attributes: [
        .font: font,
        .foregroundColor: secondaryColor
    ])

    let range = (metaText as NSString).range(of: req.requesterDisplayName)
    if range.location != NSNotFound {
        attr.addAttributes([
            .font: boldFont,
            .foregroundColor: primaryColor
        ], range: range)
    }

    label.attributedStringValue = attr
    return label
}
