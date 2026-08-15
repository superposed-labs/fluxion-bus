// MARK: - Pixel-Perfect Sidebar Vector Icons (matching prefs.jsx)
enum SidebarIcons {
    static func makeIcon(for id: String) -> NSImage {
        let size = NSSize(width: 14, height: 14)
        let image = NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            ctx.setStrokeColor(NSColor.white.cgColor)
            ctx.setFillColor(NSColor.white.cgColor)
            ctx.setLineWidth(1.6)
            ctx.setLineCap(.round)
            ctx.setLineJoin(.round)

            // Map from 24x24 SVG viewBox to 14x14
            let scale = rect.width / 24.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            switch id {
            case "general":
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 4, y: 8)); ctx.addLine(to: CGPoint(x: 20, y: 8))
                ctx.move(to: CGPoint(x: 4, y: 16)); ctx.addLine(to: CGPoint(x: 20, y: 16))
                ctx.strokePath()
                ctx.fillEllipse(in: CGRect(x: 9 - 2.4, y: 8 - 2.4, width: 4.8, height: 4.8))
                ctx.fillEllipse(in: CGRect(x: 15 - 2.4, y: 16 - 2.4, width: 4.8, height: 4.8))

            case "agents":
                let bodyPath = CGPath(roundedRect: CGRect(x: 6.5, y: 6.5, width: 11, height: 11), cornerWidth: 2.5, cornerHeight: 2.5, transform: nil)
                ctx.addPath(bodyPath)
                ctx.strokePath()
                let centerPath = CGPath(roundedRect: CGRect(x: 10, y: 10, width: 4, height: 4), cornerWidth: 1, cornerHeight: 1, transform: nil)
                ctx.addPath(centerPath)
                ctx.fillPath()
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 9, y: 3.5)); ctx.addLine(to: CGPoint(x: 9, y: 6))
                ctx.move(to: CGPoint(x: 15, y: 3.5)); ctx.addLine(to: CGPoint(x: 15, y: 6))
                ctx.move(to: CGPoint(x: 9, y: 18)); ctx.addLine(to: CGPoint(x: 9, y: 20.5))
                ctx.move(to: CGPoint(x: 15, y: 18)); ctx.addLine(to: CGPoint(x: 15, y: 20.5))
                ctx.strokePath()

            case "automation":
                ctx.strokeEllipse(in: CGRect(x: 12 - 7.5, y: 12 - 7.5, width: 15, height: 15))
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 12, y: 8))
                ctx.addLine(to: CGPoint(x: 12, y: 12))
                ctx.addLine(to: CGPoint(x: 14.8, y: 13.8))
                ctx.strokePath()

            case "messaging":
                let msgPath = CGPath(roundedRect: CGRect(x: 4, y: 5.5, width: 16, height: 11), cornerWidth: 3, cornerHeight: 3, transform: nil)
                ctx.addPath(msgPath)
                ctx.strokePath()
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 8.5, y: 16.5))
                ctx.addLine(to: CGPoint(x: 7.5, y: 19.5))
                ctx.addLine(to: CGPoint(x: 12, y: 16.5))
                ctx.strokePath()

            case "provider-routing", "provider":
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 8.2, y: 11.2)); ctx.addLine(to: CGPoint(x: 15.8, y: 7.3))
                ctx.move(to: CGPoint(x: 8.2, y: 12.8)); ctx.addLine(to: CGPoint(x: 15.8, y: 16.7))
                ctx.strokePath()
                ctx.strokeEllipse(in: CGRect(x: 6 - 2.2, y: 12 - 2.2, width: 4.4, height: 4.4))
                ctx.strokeEllipse(in: CGRect(x: 18 - 2.2, y: 6.5 - 2.2, width: 4.4, height: 4.4))
                ctx.strokeEllipse(in: CGRect(x: 18 - 2.2, y: 17.5 - 2.2, width: 4.4, height: 4.4))

            case "services":
                let r1 = CGPath(roundedRect: CGRect(x: 4.5, y: 6, width: 15, height: 5), cornerWidth: 1.6, cornerHeight: 1.6, transform: nil)
                let r2 = CGPath(roundedRect: CGRect(x: 4.5, y: 13, width: 15, height: 5), cornerWidth: 1.6, cornerHeight: 1.6, transform: nil)
                ctx.addPath(r1)
                ctx.addPath(r2)
                ctx.strokePath()
                ctx.fillEllipse(in: CGRect(x: 8 - 0.8, y: 8.5 - 0.8, width: 1.6, height: 1.6))
                ctx.fillEllipse(in: CGRect(x: 8 - 0.8, y: 15.5 - 0.8, width: 1.6, height: 1.6))

            default:
                break
            }
            return true
        }
        image.isTemplate = false
        return image
    }
}


import AppKit
import Foundation

// MARK: - NSColor Extension
extension NSColor {
    convenience init(hex: String) {
        var cleanHex = hex.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        if cleanHex.hasPrefix("#") {
            cleanHex.remove(at: cleanHex.startIndex)
        }

        var rgbValue: UInt64 = 0
        Scanner(string: cleanHex).scanHexInt64(&rgbValue)

        let r, g, b, a: CGFloat
        if cleanHex.count == 6 {
            r = CGFloat((rgbValue & 0xFF0000) >> 16) / 255.0
            g = CGFloat((rgbValue & 0x00FF00) >> 8) / 255.0
            b = CGFloat(rgbValue & 0x0000FF) / 255.0
            a = 1.0
        } else if cleanHex.count == 8 {
            r = CGFloat((rgbValue & 0xFF000000) >> 24) / 255.0
            g = CGFloat((rgbValue & 0x00FF0000) >> 16) / 255.0
            b = CGFloat((rgbValue & 0x0000FF00) >> 8) / 255.0
            a = CGFloat(rgbValue & 0x000000FF) / 255.0
        } else {
            r = 0
            g = 0
            b = 0
            a = 1.0
        }

        self.init(calibratedRed: r, green: g, blue: b, alpha: a)
    }

    static func dynamicColor(light: NSColor, dark: NSColor) -> NSColor {
        return NSColor(name: nil) { appearance in
            if appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua {
                return dark
            } else {
                return light
            }
        }
    }
}

// MARK: - Semantic Color Palette
/// Centralized light/dark color tokens so the same hex pair isn't repeated
/// across every view. Change a shade once here and it updates everywhere.
enum Palette {
    static let separator = NSColor.dynamicColor(light: NSColor(hex: "#D2D3D6"), dark: NSColor(hex: "#4A4B4D"))
    static let primaryText = NSColor.dynamicColor(light: NSColor(hex: "#343537"), dark: NSColor(hex: "#F7F7F7"))
    static let secondaryText = NSColor.dynamicColor(light: NSColor(hex: "#8F9093"), dark: NSColor(hex: "#949597"))
    static let sectionHeader = NSColor.dynamicColor(light: NSColor(hex: "#9E9FA2"), dark: NSColor(hex: "#848588"))
    static let cardBackground = NSColor.dynamicColor(light: NSColor(hex: "#FFFFFF"), dark: NSColor(hex: "#3D3E40"))
    static let cardBorder = NSColor.dynamicColor(light: NSColor(hex: "#DEDFE1"), dark: NSColor(hex: "#48494B"))
    static let windowBackground = NSColor.dynamicColor(light: NSColor(hex: "#F3F3F4"), dark: NSColor(hex: "#313234"))
    static let chromeBackground = NSColor.dynamicColor(light: NSColor(hex: "#F7F7F7"), dark: NSColor(hex: "#38393B"))
    static let sidebarBackground = NSColor.dynamicColor(light: NSColor(hex: "#F1F1F2"), dark: NSColor(hex: "#2B2C2E"))
    static let txtButtonNormal = NSColor.dynamicColor(light: NSColor(hex: "#6C6D70"), dark: NSColor(hex: "#B4B5B7"))
    static let txtButtonHover = primaryText
    static let txtButtonQuitHover = NSColor.dynamicColor(light: NSColor(hex: "#9E341B"), dark: NSColor(hex: "#FF6B57"))
    static let txtButtonHoverBackground = NSColor(white: 0.5, alpha: 0.08)
}

// MARK: - Brand Diamond View
class BrandDiamondView: NSView {
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layout() {
        super.layout()
        setupLayers()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        setupLayers()
    }

    private func setupLayers() {
        layer?.sublayers?.forEach { $0.removeFromSuperlayer() }

        let accentColor = NSColor.controlAccentColor.cgColor

        let beforeLayer = CALayer()
        beforeLayer.bounds = CGRect(x: 0, y: 0, width: 13, height: 13)
        beforeLayer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        beforeLayer.cornerRadius = 3
        beforeLayer.backgroundColor = accentColor
        beforeLayer.opacity = 0.5
        beforeLayer.transform = CATransform3DConcat(
            CATransform3DMakeRotation(.pi / 4, 0, 0, 1),
            CATransform3DMakeScale(0.7, 0.7, 1)
        )
        layer?.addSublayer(beforeLayer)

        let afterLayer = CALayer()
        afterLayer.bounds = CGRect(x: 0, y: 0, width: 13, height: 13)
        afterLayer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        afterLayer.cornerRadius = 3
        afterLayer.backgroundColor = accentColor
        afterLayer.opacity = 1.0
        afterLayer.transform = CATransform3DConcat(
            CATransform3DMakeRotation(.pi / 4, 0, 0, 1),
            CATransform3DMakeScale(0.42, 0.42, 1)
        )
        layer?.addSublayer(afterLayer)
    }
}

// MARK: - Custom TxtButton
class TxtButton: NSButton {
    var isQuitButton = false
    private var trackingArea: NSTrackingArea?
    private var isHovered = false

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
        needsDisplay = true
    }

    override func mouseExited(with event: NSEvent) {
        isHovered = false
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        // Draw custom background
        let path = NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6)
        if isHovered {
            Palette.txtButtonHoverBackground.setFill()
            path.fill()
        }

        // Draw text
        let paragraphStyle = NSMutableParagraphStyle()
        paragraphStyle.alignment = .center

        let textColor: NSColor
        if isHovered {
            textColor = isQuitButton ? Palette.txtButtonQuitHover : Palette.txtButtonHover
        } else {
            textColor = Palette.txtButtonNormal
        }

        let font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        let attrs: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: textColor,
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

// MARK: - Sidebar Navigation Item
class SidebarNavItem: NSView {
    let id: String
    let title: String
    let iconSymbol: String
    let iconBgColor: NSColor
    var isActive: Bool = false {
        didSet {
            updateAppearance()
        }
    }
    var isHovered: Bool = false {
        didSet {
            needsDisplay = true
        }
    }
    var showDot: Bool = false {
        didSet {
            dotView.isHidden = !showDot
        }
    }

    var onClick: ((String) -> Void)?

    private let iconTile = NSView()
    private let iconView = NSImageView()
    private let label = NSTextField(labelWithString: "")
    private let dotView = NSView()
    private var trackingArea: NSTrackingArea?

    init(id: String, title: String, iconSymbol: String, iconBgColor: NSColor) {
        self.id = id
        self.title = title
        self.iconSymbol = iconSymbol
        self.iconBgColor = iconBgColor
        super.init(frame: .zero)

        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 7

        // Icon Tile (22x22)
        iconTile.translatesAutoresizingMaskIntoConstraints = false
        iconTile.wantsLayer = true
        iconTile.layer?.cornerRadius = 6
        iconTile.layer?.backgroundColor = iconBgColor.cgColor
        addSubview(iconTile)

        // Icon View (Custom vector icon matching design)
        iconView.translatesAutoresizingMaskIntoConstraints = false
        iconView.imageScaling = .scaleProportionallyUpOrDown
        iconView.image = SidebarIcons.makeIcon(for: id)
        iconTile.addSubview(iconView)

        // Label
        label.translatesAutoresizingMaskIntoConstraints = false
        label.stringValue = title
        label.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        label.textColor = Palette.primaryText
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        addSubview(label)

        // Dot View (6x6 green circle)
        dotView.translatesAutoresizingMaskIntoConstraints = false
        dotView.wantsLayer = true
        dotView.layer?.cornerRadius = 3
        dotView.layer?.backgroundColor = NSColor.systemGreen.cgColor
        dotView.isHidden = true
        addSubview(dotView)

        // Constraints
        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 32),

            iconTile.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 8),
            iconTile.centerYAnchor.constraint(equalTo: centerYAnchor),
            iconTile.widthAnchor.constraint(equalToConstant: 22),
            iconTile.heightAnchor.constraint(equalToConstant: 22),

            iconView.centerXAnchor.constraint(equalTo: iconTile.centerXAnchor),
            iconView.centerYAnchor.constraint(equalTo: iconTile.centerYAnchor),
            iconView.widthAnchor.constraint(equalToConstant: 13),
            iconView.heightAnchor.constraint(equalToConstant: 13),

            label.leadingAnchor.constraint(equalTo: iconTile.trailingAnchor, constant: 9),
            label.centerYAnchor.constraint(equalTo: centerYAnchor),

            dotView.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -10),
            dotView.centerYAnchor.constraint(equalTo: centerYAnchor),
            dotView.widthAnchor.constraint(equalToConstant: 6),
            dotView.heightAnchor.constraint(equalToConstant: 6),

            label.trailingAnchor.constraint(lessThanOrEqualTo: dotView.leadingAnchor, constant: -8)
        ])
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
    }

    override func mouseExited(with event: NSEvent) {
        isHovered = false
    }

    override func mouseDown(with event: NSEvent) {
        onClick?(id)
    }

    private func updateAppearance() {
        if isActive {
            label.textColor = .white
            dotView.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.95).cgColor
            iconTile.layer?.backgroundColor = iconBgColor.cgColor
        } else {
            label.textColor = Palette.primaryText
            dotView.layer?.backgroundColor = NSColor.systemGreen.cgColor
            iconTile.layer?.backgroundColor = iconBgColor.cgColor
        }
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        if isActive {
            NSColor.controlAccentColor.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: 7, yRadius: 7).fill()
        } else if isHovered {
            Palette.txtButtonHoverBackground.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: 7, yRadius: 7).fill()
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }
}

// MARK: - Transparent Track Scroller
class CleanScroller: NSScroller {
    override func drawKnobSlot(in slotRect: NSRect, highlight flag: Bool) {
        // Do not draw the dark background track slot to keep it clean and transparent
    }

    override func drawKnob() {
        let knobRect = rect(for: .knob)
        guard knobRect.width > 0 && knobRect.height > 0 else { return }

        // Draw a custom, very light, semi-transparent pill
        let knobColor = NSColor.dynamicColor(
            light: NSColor.black.withAlphaComponent(0.22), // Soft black in light mode
            dark: NSColor.white.withAlphaComponent(0.28)   // Soft white in dark mode
        )
        knobColor.setFill()

        let pillWidth: CGFloat = 6
        let xOffset = (knobRect.width - pillWidth) / 2
        let pillRect = NSRect(
            x: knobRect.origin.x + xOffset,
            y: knobRect.origin.y + 2,
            width: pillWidth,
            height: knobRect.height - 4
        )

        let path = NSBezierPath(roundedRect: pillRect, xRadius: pillWidth / 2, yRadius: pillWidth / 2)
        path.fill()
    }
}
