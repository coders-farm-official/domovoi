import Foundation

/// The tiny template language of `mac/vm/domovoi.yaml` and
/// `mac/launchd/*.plist`: `{{NAME}}` is replaced by a value the caller has
/// already quoted for the file's format, and a line holding only
/// `{{#NAME}}` ... a line holding only `{{/NAME}}` keeps what is between
/// them only when the section is on. A name the caller didn't give, a value
/// it gave that the template never uses, or a section left open is an
/// error: a half-filled VM or launchd file is never written.
public enum Template {
    public struct Failure: Error, CustomStringConvertible, Equatable {
        public let message: String
        public var description: String { message }
    }

    public static func render(_ text: String, values: [String: String], sections: [String: Bool] = [:]) throws -> String {
        var out: [Substring] = []
        var stack: [(name: String, on: Bool)] = []
        var usedSections = Set<String>()
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if let name = sectionTag(t, "#") {
                guard let on = sections[name] else { throw Failure(message: "no answer for section \(name)") }
                usedSections.insert(name)
                stack.append((name, on))
                continue
            }
            if let name = sectionTag(t, "/") {
                guard let top = stack.popLast(), top.name == name else { throw Failure(message: "section \(name) closed but not open") }
                continue
            }
            if stack.allSatisfy({ $0.on }) { out.append(line) }
        }
        if let open = stack.last { throw Failure(message: "section \(open.name) never closed") }
        for name in sections.keys where !usedSections.contains(name) {
            throw Failure(message: "the template has no section \(name)")
        }

        // One pass, so a value is never itself read as a template.
        let kept = out.joined(separator: "\n")
        var result = ""
        var rest = kept[...]
        while let open = rest.range(of: "{{") {
            let after = rest[open.upperBound...]
            guard let close = after.range(of: "}}"), isName(String(after[..<close.lowerBound])) else {
                result += rest[..<open.upperBound]
                rest = after
                continue
            }
            let name = String(after[..<close.lowerBound])
            guard let v = values[name] else { throw Failure(message: "no value for {{\(name)}}") }
            result += rest[..<open.lowerBound]
            result += v
            rest = after[close.upperBound...]
        }
        result += rest
        // A value for a name that only appears inside a section that is off
        // is fine; one the template never names at all is a caller's mistake.
        let everywhere = placeholders(in: text)
        for name in values.keys where !everywhere.contains(name) {
            throw Failure(message: "the template never uses {{\(name)}}")
        }
        return result
    }

    /// The `{{NAME}}` names in `text`, in order of first appearance.
    public static func placeholders(in text: String) -> [String] {
        var names: [String] = []
        var rest = text[...]
        while let open = rest.range(of: "{{") {
            let after = rest[open.upperBound...]
            guard let close = after.range(of: "}}") else { break }
            let name = String(after[..<close.lowerBound])
            if isName(name), !names.contains(name) { names.append(name) }
            rest = after[close.upperBound...]
        }
        return names
    }

    static func isName(_ s: String) -> Bool {
        guard let first = s.unicodeScalars.first, CharacterSet.uppercaseLetters.contains(first) else { return false }
        return s.unicodeScalars.allSatisfy { CharacterSet.uppercaseLetters.contains($0) || CharacterSet.decimalDigits.contains($0) || $0 == "_" }
    }

    static func sectionTag(_ t: String, _ mark: String) -> String? {
        guard t.hasPrefix("{{\(mark)"), t.hasSuffix("}}") else { return nil }
        let name = String(t.dropFirst(3).dropLast(2))
        return isName(name) ? name : nil
    }

    // ─── quoting ───────────────────────────────────────────────────────

    /// A YAML scalar for any string: a JSON string is valid YAML.
    public static func yamlString(_ s: String) -> String {
        var out = "\""
        for u in s.unicodeScalars {
            switch u {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if u.value < 0x20 || u.value == 0x7f {
                    out += String(format: "\\u%04x", u.value)
                } else {
                    out.unicodeScalars.append(u)
                }
            }
        }
        return out + "\""
    }

    /// Text for a plist `<string>`.
    public static func xmlEscape(_ s: String) -> String {
        var out = ""
        for c in s {
            switch c {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            case "'": out += "&apos;"
            default: out.append(c)
            }
        }
        return out
    }
}
