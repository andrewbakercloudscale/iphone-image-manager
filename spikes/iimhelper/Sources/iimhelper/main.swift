import Foundation
import ImageCaptureCore

let usage = """
iimhelper: read-only ImageCaptureCore probe for the iPhone Image Manager P0 spike.

USAGE
    iimhelper probe [options]

OPTIONS
    --presentation original|converted   Which asset variant the device should expose.
                                        Default: original. "converted" is what
                                        ImageCaptureCore hands you if you never set
                                        this, and it transcodes HEIC to JPEG and
                                        HEVC to H.264.
    --timeout SECONDS                   Give up if the content catalog has not
                                        completed. Default: 900.
    --metadata-sample N                 Dump available metadata keys for the first
                                        N assets. Default: 5.
    -h, --help                          This text.

OUTPUT
    JSON Lines on stdout, one object per line, each with an "event" field.
    Diagnostics on stderr.

SAFETY
    This binary is read-only. It contains no call to requestDeleteFiles and no
    call to requestUploadFile. The delete question is answered by reading the
    device capability list.
"""

var args = Array(CommandLine.arguments.dropFirst())

if args.isEmpty || args.contains("-h") || args.contains("--help") {
    print(usage)
    exit(args.isEmpty ? 64 : 0)
}

let command = args.removeFirst()
guard command == "probe" else {
    FileHandle.standardError.write("unknown command: \(command)\n\n\(usage)\n".data(using: .utf8)!)
    exit(64)
}

func option(_ name: String, default def: String) -> String {
    guard let i = args.firstIndex(of: name) else { return def }
    guard i + 1 < args.count else {
        Out.fail("option \(name) needs a value", code: 64)
    }
    return args[i + 1]
}

let presentationArg = option("--presentation", default: "original")
let presentation: ICMediaPresentation
switch presentationArg {
case "original":  presentation = .originalAssets
case "converted": presentation = .convertedAssets
default:          Out.fail("--presentation must be original or converted", code: 64)
}

guard let timeout = TimeInterval(option("--timeout", default: "900")), timeout > 0 else {
    Out.fail("--timeout must be a positive number of seconds", code: 64)
}
guard let metadataSample = Int(option("--metadata-sample", default: "5")), metadataSample >= 0 else {
    Out.fail("--metadata-sample must be zero or more", code: 64)
}

Out.log("probing, presentation=\(presentationArg), timeout=\(Int(timeout))s")
Out.log("the iPhone must be plugged in, unlocked, and trusting this Mac")

let probe = Probe(presentation: presentation, timeout: timeout, metadataSample: metadataSample)
exit(probe.run())
