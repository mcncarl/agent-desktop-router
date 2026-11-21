// Window and process inspector for agent-desktop-router on macOS.
// Usage:
//   probe pid <pid>        -> "pid\tname\tbundle\texe"
//   probe window <number>  -> "pid\tname\tbundle\texe\towner" (exit 3 if window does not exist)
//   probe at <x> <y>       -> one line per window stacked at that screen point, front to back
import AppKit
import CoreGraphics
import Foundation

func procLine(_ pid: pid_t, owner: String? = nil) -> String {
    let app = NSRunningApplication(processIdentifier: pid)
    let name = app?.localizedName ?? ""
    let bundle = app?.bundleIdentifier ?? ""
    let exe = app?.executableURL?.lastPathComponent ?? ""
    if let o = owner {
        return "\(pid)\t\(name)\t\(bundle)\t\(exe)\t\(o)"
    }
    return "\(pid)\t\(name)\t\(bundle)\t\(exe)"
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    fputs("usage: probe pid <pid> | window <id> | at <x> <y>\n", stderr)
    exit(2)
}

switch args[1] {
case "pid":
    guard let p = Int32(args[2]) else { exit(2) }
    print(procLine(p))

case "window":
    guard let target = UInt32(args[2]) else { exit(2) }
    let list = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] ?? []
    for w in list {
        if let num = w[kCGWindowNumber as String] as? UInt32, num == target {
            let pid = (w[kCGWindowOwnerPID as String] as? Int32) ?? 0
            let owner = (w[kCGWindowOwnerName as String] as? String) ?? ""
            print(procLine(pid, owner: owner))
            exit(0)
        }
    }
    exit(3)

case "at":
    guard args.count >= 4, let x = Double(args[2]), let y = Double(args[3]) else { exit(2) }
    let pt = CGPoint(x: x, y: y)
    let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    for w in list {
        let layer = (w[kCGWindowLayer as String] as? Int) ?? 0
        if layer < 0 { continue }
        if let b = w[kCGWindowBounds as String] as? [String: Any],
           let rect = CGRect(dictionaryRepresentation: b as CFDictionary),
           rect.contains(pt) {
            let pid = (w[kCGWindowOwnerPID as String] as? Int32) ?? 0
            let owner = (w[kCGWindowOwnerName as String] as? String) ?? ""
            print(procLine(pid, owner: owner))
        }
    }

default:
    exit(2)
}
