import AppKit

// Draws the app icon: caption lines on a teal tile. Usage: make_icon.swift <size> <output.png>
let size = CGFloat(Int(CommandLine.arguments[1])!)
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let unit = size / 1024
let tile = NSBezierPath(roundedRect: NSRect(x: 100 * unit, y: 100 * unit, width: 824 * unit, height: 824 * unit),
                        xRadius: 185 * unit, yRadius: 185 * unit)
NSGradient(starting: NSColor(red: 0.04, green: 0.50, blue: 0.47, alpha: 1),
           ending: NSColor(red: 0.02, green: 0.27, blue: 0.29, alpha: 1))!.draw(in: tile, angle: -90)
let bars: [(CGFloat, CGFloat, CGFloat)] = [(610, 624, 1), (470, 624, 1), (330, 390, 0.62)]
for (y, width, alpha) in bars {
    NSColor.white.withAlphaComponent(alpha).setFill()
    NSBezierPath(roundedRect: NSRect(x: 200 * unit, y: y * unit, width: width * unit, height: 84 * unit),
                 xRadius: 42 * unit, yRadius: 42 * unit).fill()
}
NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
