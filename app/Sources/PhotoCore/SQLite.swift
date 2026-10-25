import Foundation
import SQLite3

public enum SQLiteError: Error, CustomStringConvertible {
    case open(String, String)
    case prepare(String, String)

    public var description: String {
        switch self {
        case .open(let path, let m): "could not open \(path): \(m)"
        case .prepare(let sql, let m): "\(m) in: \(sql)"
        }
    }
}

/// A read-only connection. The Python engine owns the ledger and may be writing to it
/// (WAL mode) while this reads, so this never opens it for writing.
public final class SQLiteReader {
    private var db: OpaquePointer?
    public let path: String

    public init(path: String) throws {
        self.path = path
        let rc = sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, nil)
        guard rc == SQLITE_OK else {
            let m = db.map { String(cString: sqlite3_errmsg($0)) } ?? "code \(rc)"
            sqlite3_close(db)
            throw SQLiteError.open(path, m)
        }
        // A writer holding the lock briefly should not fail a refresh.
        sqlite3_busy_timeout(db, 3000)
    }

    /// For tests: an in-memory database that the test fills with `exec`.
    public init(memory: Void = ()) {
        path = ":memory:"
        sqlite3_open(":memory:", &db)
    }

    deinit { sqlite3_close(db) }

    public func exec(_ sql: String) throws {
        var err: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &err) != SQLITE_OK {
            let m = err.map { String(cString: $0) } ?? "error"
            sqlite3_free(err)
            throw SQLiteError.prepare(sql, m)
        }
    }

    /// Rows as arrays of column values: Int64, Double, String or nil.
    public func rows(_ sql: String, _ args: [Any?] = []) throws -> [[Any?]] {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw SQLiteError.prepare(sql, String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        for (i, a) in args.enumerated() {
            let n = Int32(i + 1)
            switch a {
            case let v as Int: sqlite3_bind_int64(stmt, n, Int64(v))
            case let v as Int64: sqlite3_bind_int64(stmt, n, v)
            case let v as Double: sqlite3_bind_double(stmt, n, v)
            case let v as String: sqlite3_bind_text(stmt, n, v, -1, transient)
            default: sqlite3_bind_null(stmt, n)
            }
        }
        var out: [[Any?]] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let count = sqlite3_column_count(stmt)
            var row: [Any?] = []
            row.reserveCapacity(Int(count))
            for c in 0..<count {
                switch sqlite3_column_type(stmt, c) {
                case SQLITE_INTEGER: row.append(sqlite3_column_int64(stmt, c))
                case SQLITE_FLOAT: row.append(sqlite3_column_double(stmt, c))
                case SQLITE_TEXT: row.append(String(cString: sqlite3_column_text(stmt, c)))
                default: row.append(nil)
                }
            }
            out.append(row)
        }
        return out
    }
}

extension Array where Element == Any? {
    func int(_ i: Int) -> Int {
        switch self[i] {
        case let v as Int64: Int(v)
        case let v as Double: Int(v)
        default: 0
        }
    }

    func str(_ i: Int) -> String { self[i] as? String ?? "" }
}
