// encode_loop: image sequence -> H.264 MP4 with AVFoundation (no ffmpeg needed).
// Used for the in-game stage background loop (played by Unity's VideoPlayer inside Guitar Hero Live).
//
//   encode_loop <frames_dir> <fps> <out.mp4> [repeats=1] [mbps=30]
//
// Frames are the PNG/JPG files in <frames_dir>, in natural name order. `repeats` writes the sequence
// several times back to back, so the player reaches the loop point less often.
// Build: swiftc -O encode_loop.swift -o encode_loop
import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

let args = CommandLine.arguments
guard args.count >= 4, let fps = Int32(args[2]) else {
    print("usage: encode_loop <frames_dir> <fps> <out.mp4> [repeats=1] [mbps=30]")
    exit(2)
}
let dir = URL(fileURLWithPath: args[1])
let out = URL(fileURLWithPath: args[3])
let repeats = args.count > 4 ? max(1, Int(args[4]) ?? 1) : 1
let mbps = args.count > 5 ? (Double(args[5]) ?? 30) : 30

let files = ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
    .filter { ["png", "jpg", "jpeg"].contains($0.pathExtension.lowercased()) }
    .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }

func load(_ url: URL) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

guard let first = files.first, let img0 = load(first) else {
    print("no frames in", dir.path)
    exit(1)
}
let W = img0.width, H = img0.height
try? FileManager.default.removeItem(at: out)

do {
    let writer = try AVAssetWriter(outputURL: out, fileType: .mp4)
    let settings: [String: Any] = [
        AVVideoCodecKey: AVVideoCodecType.h264,
        AVVideoWidthKey: W,
        AVVideoHeightKey: H,
        AVVideoColorPropertiesKey: [
            AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
            AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
            AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
        ],
        AVVideoCompressionPropertiesKey: [
            AVVideoAverageBitRateKey: Int(mbps * 1_000_000),
            AVVideoMaxKeyFrameIntervalKey: Int(fps),   // a keyframe every second (and on frame 0)
            AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            AVVideoAllowFrameReorderingKey: false,     // no B-frames: simplest decode at the loop point
            AVVideoExpectedSourceFrameRateKey: Int(fps),
        ],
    ]
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
    input.expectsMediaDataInRealTime = false
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        kCVPixelBufferWidthKey as String: W,
        kCVPixelBufferHeightKey as String: H,
    ])
    writer.add(input)
    guard writer.startWriting() else { print("cannot start:", writer.error as Any); exit(1) }
    writer.startSession(atSourceTime: .zero)
    let cs = CGColorSpace(name: CGColorSpace.sRGB)!
    var n: Int64 = 0
    for _ in 0..<repeats {
        for f in files {
            let ok: Bool = autoreleasepool {
                guard let img = load(f), img.width == W, img.height == H else { print("bad frame", f.path); return false }
                while !input.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.002) }
                var pbo: CVPixelBuffer?
                guard let pool = adaptor.pixelBufferPool, CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pbo) == kCVReturnSuccess, let pb = pbo else { return false }
                CVPixelBufferLockBaseAddress(pb, [])
                let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pb), width: W, height: H, bitsPerComponent: 8,
                                    bytesPerRow: CVPixelBufferGetBytesPerRow(pb), space: cs,
                                    bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
                ctx.draw(img, in: CGRect(x: 0, y: 0, width: W, height: H))
                CVPixelBufferUnlockBaseAddress(pb, [])
                let appended = adaptor.append(pb, withPresentationTime: CMTime(value: n, timescale: fps))
                n += 1
                return appended
            }
            if !ok { print("encode failed at frame", n, writer.error as Any); exit(1) }
        }
    }
    input.markAsFinished()
    let done = DispatchSemaphore(value: 0)
    writer.finishWriting { done.signal() }
    done.wait()
    guard writer.status == .completed else { print("failed:", writer.error as Any); exit(1) }
    print("wrote \(out.path): \(n) frames, \(W)x\(H) @ \(fps) fps")
} catch {
    print("error:", error)
    exit(1)
}
