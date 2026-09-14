import Foundation

/// One JSON object per stdout line. Diagnostics on stderr.
enum Out {
    private static let lock = NSLock()

    static func emit(_ obj: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
              let line = String(data: data, encoding: .utf8) else {
            log("failed to serialise a record")
            return
        }
        lock.lock()
        print(line)
        fflush(stdout)
        lock.unlock()
    }

    static func log(_ message: String) {
        lock.lock()
        FileHandle.standardError.write("[iimphotos] \(message)\n".data(using: .utf8)!)
        lock.unlock()
    }

    static func fail(_ message: String, code: Int32 = 1) -> Never {
        emit(["event": "error", "message": message])
        log("FATAL: \(message)")
        exit(code)
    }
}

extension Date {
    var iso: String { ISO8601DateFormatter().string(from: self) }
}

func jsonSafe(_ value: Any?) -> Any {
    guard let value else { return NSNull() }
    if let d = value as? Date { return d.iso }
    if let s = value as? String { return s }
    if let n = value as? NSNumber { return n }
    if let a = value as? [Any] { return a.map { jsonSafe($0) } }
    return String(describing: value)
}
