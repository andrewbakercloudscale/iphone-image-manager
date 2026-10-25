import Foundation

/// New versions, from the GitHub releases. Copied in spirit from System Sentinel's.
public enum Updates {
    public static let repo = "andrewbakercloudscale/iphone-image-manager"
    public static let releasesPage = URL(string: "https://github.com/\(repo)/releases")!

    public struct Release: Equatable, Sendable {
        /// "0.2.0", without the "v".
        public let version: String
        public let tag: String
        public let notes: String
        public let page: URL?
    }

    /// `.none` when the repo has no release yet: nothing to compare against, not an error.
    public static func latest() -> Result<Release?, JobError> {
        let r = run("/usr/bin/curl", ["-sSL", "--proto", "=https", "--max-time", "15", "-w", "\n%{http_code}",
                                     "https://api.github.com/repos/\(repo)/releases/latest"])
        guard r.status == 0 else { return .failure(.message("Could not reach GitHub. Check the network and try again.")) }
        let lines = r.out.split(separator: "\n", omittingEmptySubsequences: false)
        let code = lines.last.map(String.init) ?? ""
        let body = lines.dropLast().joined(separator: "\n")
        if code == "404" { return .success(nil) }
        guard code == "200", let rel = parse(Data(body.utf8)) else {
            return .failure(.message("GitHub answered \(code), not with a release."))
        }
        return .success(rel)
    }

    public static func parse(_ data: Data) -> Release? {
        guard let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tag = o["tag_name"] as? String, isNewer(tag, than: "0") || tag.hasPrefix("v") else { return nil }
        return Release(version: tag.trimmingCharacters(in: CharacterSet(charactersIn: "v")), tag: tag,
                       notes: o["body"] as? String ?? "",
                       page: (o["html_url"] as? String).flatMap(URL.init(string:)))
    }

    public static func isNewer(_ a: String, than b: String) -> Bool {
        let x = parts(a), y = parts(b)
        for i in 0..<max(x.count, y.count) {
            let p = i < x.count ? x[i] : 0, q = i < y.count ? y[i] : 0
            if p != q { return p > q }
        }
        return false
    }

    static func parts(_ v: String) -> [Int] {
        v.trimmingCharacters(in: CharacterSet(charactersIn: "v")).split(separator: ".")
            .map { Int($0.prefix { $0.isNumber }) ?? 0 }
    }

    /// The release notes' first bullets as short lines, for the banner and notification.
    public static func highlights(_ notes: String, max: Int = 2) -> [String] {
        notes.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { $0.hasPrefix("- ") || $0.hasPrefix("* ") }
            .prefix(max)
            .map { String($0.dropFirst(2)).replacingOccurrences(of: "**", with: "").replacingOccurrences(of: "`", with: "") }
    }

    static func run(_ exe: String, _ args: [String]) -> (status: Int32, out: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return (-1, "") }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(decoding: data, as: UTF8.self))
    }
}
