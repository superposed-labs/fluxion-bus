import AppKit
import Foundation

// Hand-drawn vector icons for the Workspace Access page. They are rendered at
// the exact pixel sizes the rows and badges use, so they stay crisp instead of
// relying on scaled SF Symbols.

enum WorkspaceAccessIcons {
    static func eyeImage(color: NSColor = Palette.primaryText) -> NSImage {
        let size = NSSize(width: 13, height: 13)
        return NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            ctx.setStrokeColor(color.cgColor)
            ctx.setLineWidth(1.3)
            ctx.setLineCap(.round)
            ctx.setLineJoin(.round)

            let scale = rect.width / 24.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            ctx.beginPath()
            ctx.move(to: CGPoint(x: 2.5, y: 12))
            ctx.addCurve(to: CGPoint(x: 12, y: 6), control1: CGPoint(x: 6, y: 6), control2: CGPoint(x: 9.5, y: 6))
            ctx.addCurve(to: CGPoint(x: 21.5, y: 12), control1: CGPoint(x: 14.5, y: 6), control2: CGPoint(x: 18, y: 6))
            ctx.addCurve(to: CGPoint(x: 12, y: 18), control1: CGPoint(x: 18, y: 18), control2: CGPoint(x: 14.5, y: 18))
            ctx.addCurve(to: CGPoint(x: 2.5, y: 12), control1: CGPoint(x: 9.5, y: 18), control2: CGPoint(x: 6, y: 18))
            ctx.strokePath()

            ctx.strokeEllipse(in: CGRect(x: 12 - 2.8, y: 12 - 2.8, width: 5.6, height: 5.6))
            return true
        }
    }

    static func pencilImage(color: NSColor) -> NSImage {
        let size = NSSize(width: 13, height: 13)
        return NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            ctx.setStrokeColor(color.cgColor)
            ctx.setLineWidth(1.3)
            ctx.setLineCap(.round)
            ctx.setLineJoin(.round)

            let scale = rect.width / 24.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            ctx.beginPath()
            ctx.move(to: CGPoint(x: 4, y: 20))
            ctx.addLine(to: CGPoint(x: 8, y: 20))
            ctx.addLine(to: CGPoint(x: 18, y: 10))
            ctx.addLine(to: CGPoint(x: 14, y: 6))
            ctx.addLine(to: CGPoint(x: 4, y: 16))
            ctx.closePath()
            ctx.strokePath()

            ctx.beginPath()
            ctx.move(to: CGPoint(x: 14.5, y: 5.5))
            ctx.addLine(to: CGPoint(x: 18.5, y: 9.5))
            ctx.strokePath()
            return true
        }
    }

    static func checkImage() -> NSImage {
        let size = NSSize(width: 12, height: 12)
        return NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            let okColor = NSColor(hex: "#30D158")
            ctx.setStrokeColor(okColor.cgColor)
            ctx.setLineWidth(1.5)
            ctx.setLineCap(.round)
            ctx.setLineJoin(.round)

            let scale = rect.width / 24.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            ctx.strokeEllipse(in: CGRect(x: 12 - 9, y: 12 - 9, width: 18, height: 18))
            ctx.beginPath()
            ctx.move(to: CGPoint(x: 8, y: 12.4))
            ctx.addLine(to: CGPoint(x: 10.7, y: 15.0))
            ctx.addLine(to: CGPoint(x: 16, y: 9.5))
            ctx.strokePath()
            return true
        }
    }

    static func warnImage() -> NSImage {
        let size = NSSize(width: 12, height: 12)
        return NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            let warnColor = NSColor.systemOrange
            ctx.setStrokeColor(warnColor.cgColor)
            ctx.setFillColor(warnColor.cgColor)
            ctx.setLineWidth(1.4)
            ctx.setLineCap(.round)
            ctx.setLineJoin(.round)

            let scale = rect.width / 24.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            ctx.beginPath()
            ctx.move(to: CGPoint(x: 12, y: 4))
            ctx.addLine(to: CGPoint(x: 20.5, y: 19))
            ctx.addLine(to: CGPoint(x: 3.5, y: 19))
            ctx.closePath()
            ctx.strokePath()

            ctx.beginPath()
            ctx.move(to: CGPoint(x: 12, y: 10))
            ctx.addLine(to: CGPoint(x: 12, y: 14.2))
            ctx.strokePath()

            ctx.fillEllipse(in: CGRect(x: 12 - 1.0, y: 16.8 - 1.0, width: 2.0, height: 2.0))
            return true
        }
    }

    static func blockImage() -> NSImage {
        let size = NSSize(width: 12, height: 12)
        return NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            let errColor = NSColor.systemRed
            ctx.setStrokeColor(errColor.cgColor)
            ctx.setLineWidth(1.4)
            ctx.setLineCap(.round)

            let scale = rect.width / 24.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            ctx.strokeEllipse(in: CGRect(x: 12 - 8.6, y: 12 - 8.6, width: 17.2, height: 17.2))
            ctx.beginPath()
            ctx.move(to: CGPoint(x: 6.5, y: 17.5))
            ctx.addLine(to: CGPoint(x: 17.5, y: 6.5))
            ctx.strokePath()
            return true
        }
    }

    static func folderGlyph() -> NSImage {
        let size = NSSize(width: 36, height: 36)
        return NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            ctx.setStrokeColor(Palette.secondaryText.cgColor)
            ctx.setLineWidth(1.6)
            ctx.setLineCap(.round)
            ctx.setLineJoin(.round)

            let scale = rect.width / 24.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            let body = CGPath(roundedRect: CGRect(x: 3, y: 7.5, width: 18, height: 12.0), cornerWidth: 2, cornerHeight: 2, transform: nil)
            ctx.addPath(body)
            ctx.strokePath()

            ctx.beginPath()
            ctx.move(to: CGPoint(x: 3, y: 7.5))
            ctx.addLine(to: CGPoint(x: 5, y: 5.5))
            ctx.addLine(to: CGPoint(x: 9, y: 5.5))
            ctx.addLine(to: CGPoint(x: 11, y: 7.5))
            ctx.strokePath()

            ctx.strokeEllipse(in: CGRect(x: 15.5 - 2.0, y: 13.0 - 2.0, width: 4.0, height: 4.0))
            ctx.beginPath()
            ctx.move(to: CGPoint(x: 15.5, y: 15.0))
            ctx.addLine(to: CGPoint(x: 15.5, y: 17.2))
            ctx.strokePath()
            return true
        }
    }

    static func disclosureTriangle(isOpen: Bool) -> NSImage {
        let size = NSSize(width: 10, height: 10)
        return NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            ctx.setStrokeColor(Palette.secondaryText.cgColor)
            ctx.setLineWidth(1.4)
            ctx.setLineCap(.round)
            ctx.setLineJoin(.round)

            let scale = rect.width / 12.0
            ctx.translateBy(x: 0, y: rect.height)
            ctx.scaleBy(x: scale, y: -scale)

            if isOpen {
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 3, y: 4.5))
                ctx.addLine(to: CGPoint(x: 6, y: 8.0))
                ctx.addLine(to: CGPoint(x: 9, y: 4.5))
                ctx.strokePath()
            } else {
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 4.5, y: 3.0))
                ctx.addLine(to: CGPoint(x: 8.0, y: 6.0))
                ctx.addLine(to: CGPoint(x: 4.5, y: 9.0))
                ctx.strokePath()
            }
            return true
        }
    }
}
