import Foundation

/// Every line that goes into the copies "Save logs for support" makes for
/// the household, and into the menu-bar icon's own log: secrets blanked as
/// far as they can be recognised. The same rules as the Windows side
/// (windows/common/Text.cs `Redact.Line`, in the same order): a setup code
/// (SetupCode.scrub); what sits before the @ of a URL (scheme://user:password@,
/// scheme://:password@, a bare scheme://token@), keeping a user name; an
/// Authorization header or a Bearer token; and the value of a key that names
/// a password, token, secret or API key (password=..., POSTGRES_PASSWORD=...,
/// "token": "...", api_key: ...). A pattern match, not a guarantee: the
/// README in every copy says to look through the files before sharing them.
public enum Redact {
    static func re(_ pattern: String) -> NSRegularExpression {
        // The patterns are constants: a typo is a programming error.
        try! NSRegularExpression(pattern: pattern)
    }

    // Up to the first @ after the scheme, '/' included (a password may hold
    // one); over-blanking an @ in a path is the safe mistake.
    static let urlUserInfo = re(#"(?i)\b([a-z][a-z0-9+.\-]*://)([^\s@"'<>]+)@"#)
    static let keyValue = re(#"(?i)\b((?:[a-z0-9]+[_-])*(?:password|passwd|pwd|passphrase|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)(?:[_-][a-z0-9]+)*["']?\s*[=:]\s*)("[^"]*"|'[^']*'|\S+)"#)
    static let authorization = re(#"(?i)\b(authorization\s*[=:]\s*)(?:(bearer|basic|token|digest)\s+)?\S+"#)
    static let bearer = re(#"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"#)

    public static let hidden = "[hidden]"

    public static func line(_ line: String) -> String {
        if line.isEmpty { return line }
        var s = SetupCode.scrub(line)
        s = replace(urlUserInfo, in: s) { g in
            let info = g(2) ?? ""
            let user = info.firstIndex(of: ":").map { String(info[...$0]) } ?? ""
            return (g(1) ?? "") + user + hidden + "@"
        }
        s = replace(authorization, in: s) { g in
            (g(1) ?? "") + (g(2).map { $0 + " " } ?? "") + hidden
        }
        s = replace(bearer, in: s) { g in (g(1) ?? "") + hidden }
        s = replace(keyValue, in: s) { g in
            let v = g(2) ?? ""
            if v.hasPrefix(hidden) { return g(0) ?? "" }
            let q = v.count > 1 && (v.first == "\"" || v.first == "'") ? String(v.first!) : ""
            return (g(1) ?? "") + q + hidden + q
        }
        return s
    }

    /// A whole text, line by line (the line ends kept).
    public static func text(_ text: String) -> String {
        text.split(separator: "\n", omittingEmptySubsequences: false).map { line(String($0)) }.joined(separator: "\n")
    }

    /// Every match of `re` in `s`, replaced by what `f` makes of its groups
    /// (`g(i)`: group i, nil when it took no part).
    static func replace(_ re: NSRegularExpression, in s: String, _ f: (_ g: (Int) -> String?) -> String) -> String {
        let ns = s as NSString
        let matches = re.matches(in: s, range: NSRange(location: 0, length: ns.length))
        guard !matches.isEmpty else { return s }
        let out = NSMutableString(string: s)
        for m in matches.reversed() {
            let g: (Int) -> String? = { i in
                guard i < m.numberOfRanges else { return nil }
                let r = m.range(at: i)
                return r.location == NSNotFound ? nil : ns.substring(with: r)
            }
            out.replaceCharacters(in: m.range, with: f(g))
        }
        return out as String
    }
}
