// Deterministic macOS icon packaging. The original brand artwork is never changed.
// Usage: swift tools/desktop/build_app_icon.swift apps/desktop/assets
import AppKit
import Foundation
let assets = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
guard let artwork = NSImage(contentsOf: assets.appendingPathComponent("verity-eye-fullframe.png")) else { fatalError("Missing original artwork") }
let iconset = assets.appendingPathComponent("AppIcon.iconset", isDirectory: true)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = points * scale
        guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0), let context = NSGraphicsContext(bitmapImageRep: bitmap) else { fatalError("Bitmap allocation") }
        NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
        context.imageInterpolation = .high
        let factor = CGFloat(pixels) / 1024
        let tile = NSRect(x: 80*factor, y: 80*factor, width: 864*factor, height: 864*factor)
        let mask = NSBezierPath(roundedRect: tile, xRadius: 190*factor, yRadius: 190*factor)
        mask.addClip(); NSColor.black.setFill(); mask.fill()
        // Uniform scaling preserves all eye/network artwork; no new logo or tracing.
        artwork.draw(in: tile.insetBy(dx: 16*factor, dy: 16*factor), from: .zero, operation: .sourceOver, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        let path = iconset.appendingPathComponent("icon_\(points)x\(points)\(suffix).png")
        try bitmap.representation(using: .png, properties: [:])!.write(to: path)
    }
}
let task = Process(); task.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
task.arguments = ["-c", "icns", iconset.path, "-o", assets.appendingPathComponent("AppIcon.icns").path]
try task.run(); task.waitUntilExit(); guard task.terminationStatus == 0 else { fatalError("iconutil failed") }
