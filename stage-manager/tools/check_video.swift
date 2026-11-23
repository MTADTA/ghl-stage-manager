// check_video: per-frame stats of a video, to catch glitched frames in the stage loop.
//   check_video <video.mp4>
// Prints one line per frame: index, mean brightness, mean change from the previous frame, and the count of
// strongly coloured pixels (note gems, flames, Star Power blue). A frame captured half-loaded stands out.
// Build: swiftc -O check_video.swift -o check_video
import AVFoundation
import Foundation

guard CommandLine.arguments.count >= 2 else { print("usage: check_video <video.mp4>"); exit(2) }
let asset = AVURLAsset(url: URL(fileURLWithPath: CommandLine.arguments[1]))
let sem = DispatchSemaphore(value: 0)
var track: AVAssetTrack?
Task {
    track = try? await asset.loadTracks(withMediaType: .video).first
    sem.signal()
}
sem.wait()
guard let t = track, let reader = try? AVAssetReader(asset: asset) else { print("cannot read"); exit(1) }
let out = AVAssetReaderTrackOutput(track: t, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(out)
reader.startReading()
var prev: [UInt8]? = nil, first: [UInt8]? = nil
var idx = 0
while let sb = out.copyNextSampleBuffer(), let pb = CMSampleBufferGetImageBuffer(sb) {
    CVPixelBufferLockBaseAddress(pb, .readOnly)
    let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb), bpr = CVPixelBufferGetBytesPerRow(pb)
    let base = CVPixelBufferGetBaseAddress(pb)!.assumingMemoryBound(to: UInt8.self)
    // sample every 4th pixel in both directions
    var cur = [UInt8](); cur.reserveCapacity((w / 4) * (h / 4))
    var sum = 0, colored = 0
    for y in stride(from: 0, to: h, by: 4) {
        let row = base + y * bpr
        for x in stride(from: 0, to: w, by: 4) {
            let b = Int(row[x * 4]), g = Int(row[x * 4 + 1]), r = Int(row[x * 4 + 2])
            let l = (r * 3 + g * 6 + b) / 10
            sum += l
            cur.append(UInt8(l))
            if max(r, g, b) - min(r, g, b) > 90 && max(r, g, b) > 120 { colored += 1 }
        }
    }
    CVPixelBufferUnlockBaseAddress(pb, .readOnly)
    var diff = 0
    if let p = prev { for i in 0..<min(p.count, cur.count) { diff += abs(Int(p[i]) - Int(cur[i])) } }
    let n = max(1, cur.count)
    print(String(format: "%3d mean %6.2f change %6.3f colored %5d", idx, Double(sum) / Double(n), Double(diff) / Double(n), colored))
    if first == nil { first = cur }
    prev = cur
    idx += 1
}
if let f = first, let l = prev {  // loop seam: last frame -> first frame should look like any other step
    var d = 0
    for i in 0..<min(f.count, l.count) { d += abs(Int(f[i]) - Int(l[i])) }
    print(String(format: "seam change %6.3f", Double(d) / Double(max(1, f.count))))
}
print("frames:", idx)
