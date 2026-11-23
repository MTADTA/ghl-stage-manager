// santroller_leds: lights a Santroller guitar (RetroCultMods Revival Kit) from the game, the way YARG does.
//
// Reads lines on stdin and sends each to every matching guitar as a Santroller HID output report (report id 1):
//   "<command> <value>"   0x5A: the stage-kit / game-feedback channel (PlasticBand's PS3OutputCommand, what YARG
//                         sends). 0x08 star power meter (0-255), 0x09 star power on/off, 0x0A multiplier, 0x0B solo,
//                         0x0C note miss; stage kit 0x20 blue / 0x40 green / 0x60 yellow / 0x80 red (value = LED
//                         mask, 8 LEDs), 0xFF all off.
//   "rgb <r> <g> <b>"     0x5D: the RGB indicator LED (Santroller 2; a Santroller 1 ignores it).
//   "player <n>"          0x5C: the player LED (0-3, 0xFF none).
// Values are decimal or 0x hex. What each one lights up is the guitar's own LED setup (Santroller configurator /
// RCM tool); a guitar ignores what it doesn't do.
//
// The guitar is opened shared (never seized), so the game keeps reading it. It runs as its own process: whatever USB
// does, the game can't stall. Status lines go to stdout ("ready N", "guitar <name>", "gone <name>", "sent ...").
//   usage: santroller_leds [name-substring ...]     (default: "Revival Kit", "Santroller")
import Foundation
import IOKit.hid

let wanted = CommandLine.arguments.count > 1 ? Array(CommandLine.arguments.dropFirst()) : ["Revival Kit", "Santroller"]
var devices: [IOHIDDevice] = []
let lock = NSLock()

func say(_ s: String) { print(s); fflush(stdout) }
func name(_ d: IOHIDDevice) -> String {
    (IOHIDDeviceGetProperty(d, kIOHIDProductKey as CFString) as? String) ?? "?"
}
func matches(_ d: IOHIDDevice) -> Bool {
    let n = name(d), m = (IOHIDDeviceGetProperty(d, kIOHIDManufacturerKey as CFString) as? String) ?? ""
    return wanted.contains { n.localizedCaseInsensitiveContains($0) || m.localizedCaseInsensitiveContains($0) }
}

let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
let usages: [[String: Int]] = [
    [kIOHIDDeviceUsagePageKey: kHIDPage_GenericDesktop, kIOHIDDeviceUsageKey: kHIDUsage_GD_Joystick],
    [kIOHIDDeviceUsagePageKey: kHIDPage_GenericDesktop, kIOHIDDeviceUsageKey: kHIDUsage_GD_GamePad],
]
IOHIDManagerSetDeviceMatchingMultiple(manager, usages as CFArray)
IOHIDManagerRegisterDeviceMatchingCallback(manager, { _, _, _, device in
    guard matches(device) else { return }
    lock.lock(); if !devices.contains(where: { $0 == device }) { devices.append(device) }; lock.unlock()
    say("guitar \(name(device))")
}, nil)
IOHIDManagerRegisterDeviceRemovalCallback(manager, { _, _, _, device in
    lock.lock(); devices.removeAll { $0 == device }; lock.unlock()
    say("gone \(name(device))")
}, nil)
IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue)
let opened = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))  // shared: the game keeps the guitar
if opened != kIOReturnSuccess { say("open failed \(opened)") }

func send(_ report: [UInt8], _ what: String) {
    var report = report
    lock.lock(); let list = devices; lock.unlock()
    for d in list {
        let r = IOHIDDeviceSetReport(d, kIOHIDReportTypeOutput, 0x01, &report, report.count)
        if r != kIOReturnSuccess { say("failed \(what) \(r)") }
    }
}
func num(_ s: Substring) -> UInt8? {
    s.hasPrefix("0x") ? UInt8(s.dropFirst(2), radix: 16) : UInt8(s)
}

Thread {
    while let line = readLine() {
        let parts = line.split(separator: " ")
        if parts.first == "quit" { exit(0) }
        if parts.first == "rgb", parts.count == 4, let r = num(parts[1]), let g = num(parts[2]), let b = num(parts[3]) {
            DispatchQueue.main.async { send([0x01, 0x5D, r, g, b, 0, 0, 0, 0], "rgb") }; continue
        }
        if parts.first == "player", parts.count == 2, let p = num(parts[1]) {
            DispatchQueue.main.async { send([0x01, 0x5C, p, 0, 0, 0, 0, 0, 0], "player") }; continue
        }
        guard parts.count == 2, let c = num(parts[0]), let v = num(parts[1]) else { continue }
        DispatchQueue.main.async { send([0x01, 0x5A, v, c, 0, 0, 0, 0, 0], "\(c) \(v)") }
    }
    exit(0)  // the server went away
}.start()
DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { lock.lock(); let n = devices.count; lock.unlock(); say("ready \(n)") }
CFRunLoopRun()
