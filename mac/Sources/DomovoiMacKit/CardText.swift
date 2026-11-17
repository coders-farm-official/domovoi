import Foundation

// The text a room's SD card needs on its boot partition, byte for byte what
// the server writes (domovoi/satellite_media/overlay.py: edit_config_txt,
// edit_cmdline_txt and the loop in write_overlay that applies them). A port
// of the Windows tray's windows/tray/BootText.cs and the two small parts of
// CardWriter.cs that make bytes (SetupPassword, ApJson), for the Mac card
// helper (mac-installer PHASE8-PARITY.md 2.4/2.5). The same goldens check
// both: windows/tests/card-goldens.json, made by
// domovoi/tests/test_windows_card_parity.py from the server's own code.
//
// What the server does:
// * reads the file as Python's Path.read_text(encoding="utf-8",
//   errors="replace") does: each maximal invalid UTF-8 subsequence becomes
//   one U+FFFD, and every CRLF or lone CR becomes LF (universal newlines);
// * config.txt: nothing if the marker is already there; else a newline if
//   the text doesn't end with one, then the block;
// * cmdline.txt: only the first line is kept, its tokens split on Python's
//   whitespace, each wanted token added unless the same token or the same
//   key= is already there, joined with single spaces, one LF at the end;
// * writes the result (UTF-8, LF) only when it differs from what it read,
//   so a card already carrying the edits keeps its bytes (CRLF and all).
//
// Everything here works on arrays of Unicode scalars, Python's code points,
// never on Swift Characters: a Character is a grapheme cluster, and "\r\n"
// is ONE Character, which would break the newline and whitespace rules.

public enum CardText {
    public typealias Text = [Unicode.Scalar]

    public static let marker = "# --- domovoi satellite (media-prep) ---"
    public static let endMarker = "# --- end domovoi satellite ---"
    /// The first-boot hook: without it the card boots as plain Pi OS.
    public static let firstrunToken = "systemd.run=/boot/firmware/domovoi/firstrun.sh"

    public static func text(_ s: String) -> Text { Array(s.unicodeScalars) }

    public static func string(_ t: Text) -> String {
        var v = String.UnicodeScalarView()
        v.append(contentsOf: t)
        return String(v)
    }

    public static func utf8(_ t: Text) -> [UInt8] { Array(string(t).utf8) }

    // ─── Python's str rules ────────────────────────────────────────────

    /// Python's str.isspace(): what str.split() and strip() treat as
    /// whitespace.
    public static func isPySpace(_ c: Unicode.Scalar) -> Bool {
        let v = c.value
        if v >= 0x09 && v <= 0x0D { return true }
        if v >= 0x1C && v <= 0x20 { return true }
        switch v {
        case 0x85, 0xA0, 0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000:
            return true
        default:
            return v >= 0x2000 && v <= 0x200A
        }
    }

    /// Python's str.splitlines() line boundaries.
    static func isPyLineBreak(_ c: Unicode.Scalar) -> Bool {
        switch c.value {
        case 0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029:
            return true
        default:
            return false
        }
    }

    public static func pyStrip(_ s: Text) -> Text {
        var a = 0, b = s.count
        while a < b && isPySpace(s[a]) { a += 1 }
        while b > a && isPySpace(s[b - 1]) { b -= 1 }
        return Array(s[a..<b])
    }

    public static func pySplit(_ s: Text) -> [Text] {
        var parts: [Text] = []
        var i = 0
        while i < s.count {
            while i < s.count && isPySpace(s[i]) { i += 1 }
            let start = i
            while i < s.count && !isPySpace(s[i]) { i += 1 }
            if i > start { parts.append(Array(s[start..<i])) }
        }
        return parts
    }

    public static func pyFirstLine(_ s: Text) -> Text {
        var i = 0
        while i < s.count && !isPyLineBreak(s[i]) { i += 1 }
        return Array(s[0..<i])
    }

    static func contains(_ hay: Text, _ needle: Text) -> Bool {
        guard !needle.isEmpty else { return true }
        guard hay.count >= needle.count else { return false }
        var i = 0
        while i + needle.count <= hay.count {
            if hay[i] == needle[0] && Array(hay[i..<(i + needle.count)]) == needle { return true }
            i += 1
        }
        return false
    }

    static func hasPrefix(_ s: Text, _ p: Text) -> Bool {
        s.count >= p.count && Array(s[0..<p.count]) == p
    }

    static func join(_ parts: [Text], _ sep: Unicode.Scalar) -> Text {
        var out: Text = []
        for (n, p) in parts.enumerated() {
            if n > 0 { out.append(sep) }
            out.append(contentsOf: p)
        }
        return out
    }

    static let lf: Unicode.Scalar = "\n"
    static let cr: Unicode.Scalar = "\r"
    static let space: Unicode.Scalar = " "
    static let equals: Unicode.Scalar = "="
    static let tilde: Unicode.Scalar = "~"

    // ─── reading ───────────────────────────────────────────────────────

    /// UTF-8 decoding with Python's errors="replace": each maximal invalid
    /// subsequence (Unicode's "maximal subpart" rule, which CPython follows)
    /// becomes one U+FFFD. Written out, as the C# helper does, rather than
    /// left to the platform's decoder.
    public static func decodeUTF8Replacing(_ b: [UInt8]) -> Text {
        var out: Text = []
        out.reserveCapacity(b.count)
        let bad: Unicode.Scalar = "\u{FFFD}"
        var i = 0
        let n = b.count
        while i < n {
            let c = Int(b[i])
            if c < 0x80 {
                out.append(Unicode.Scalar(UInt8(c)))
                i += 1
                continue
            }
            var need = 0, cp = 0, lo = 0x80, hi = 0xBF
            if c >= 0xC2 && c <= 0xDF {
                need = 1; cp = c & 0x1F
            } else if c >= 0xE0 && c <= 0xEF {
                need = 2; cp = c & 0x0F
                if c == 0xE0 { lo = 0xA0 } else if c == 0xED { hi = 0x9F }
            } else if c >= 0xF0 && c <= 0xF4 {
                need = 3; cp = c & 0x07
                if c == 0xF0 { lo = 0x90 } else if c == 0xF4 { hi = 0x8F }
            } else {
                out.append(bad)
                i += 1
                continue
            }
            var j = i + 1, got = 0
            var broken = false
            while got < need {
                if j >= n { broken = true; break }
                let d = Int(b[j])
                let l = got == 0 ? lo : 0x80, h = got == 0 ? hi : 0xBF
                if d < l || d > h { broken = true; break }
                cp = (cp << 6) | (d & 0x3F)
                j += 1
                got += 1
            }
            if broken {
                out.append(bad)
                i = j
                continue
            }
            out.append(Unicode.Scalar(UInt32(cp)) ?? bad)
            i = j
        }
        return out
    }

    /// Path.read_text(encoding="utf-8", errors="replace"), universal
    /// newlines included.
    public static func read(_ bytes: [UInt8]) -> Text {
        let t = decodeUTF8Replacing(bytes)
        guard t.contains(cr) else { return t }
        var out: Text = []
        out.reserveCapacity(t.count)
        var i = 0
        while i < t.count {
            let c = t[i]
            if c == cr {
                out.append(lf)
                if i + 1 < t.count && t[i + 1] == lf { i += 1 }
            } else {
                out.append(c)
            }
            i += 1
        }
        return out
    }

    // ─── the two edits ─────────────────────────────────────────────────

    /// edit_config_txt, with the block the zip carries.
    public static func editConfig(_ text: Text, block: Text) -> Text {
        if contains(text, self.text(marker)) { return text }
        var t = text
        if !t.isEmpty && t.last != lf { t.append(lf) }
        return t + block
    }

    /// edit_cmdline_txt, with the tokens the zip carries.
    public static func editCmdline(_ text: Text, wanted: [Text]) -> Text {
        let stripped = pyStrip(text)
        let line = stripped.isEmpty ? [] : pyFirstLine(stripped)
        var tokens = line.isEmpty ? [] : pySplit(line)
        for tok in wanted {
            let key: Text = tok.firstIndex(of: equals).map { Array(tok[0..<$0]) } ?? tok
            let keyEq = key + [equals]
            let have = tokens.contains { $0 == tok || hasPrefix($0, keyEq) }
            if !have { tokens.append(tok) }
        }
        return join(tokens, space) + [lf]
    }

    /// The loop in write_overlay: the bytes the file holds afterwards, and
    /// whether it is written at all.
    public static func apply(_ original: [UInt8], exists: Bool, edit: (Text) -> Text) -> (bytes: [UInt8], changed: Bool) {
        let before = exists ? read(original) : []
        let after = edit(before)
        let changed = after != before
        return (changed ? utf8(after) : original, changed)
    }

    // ─── what the zip carries ──────────────────────────────────────────

    static func printableASCII(_ c: Unicode.Scalar, allowSpace: Bool) -> Bool {
        let v = c.value
        return v <= tilde.value && (allowSpace ? v >= space.value : v > space.value)
    }

    /// The block in the zip's config.txt (edit_config_txt of an empty
    /// file): the marker line first, the end marker last, settings in
    /// between. Nil when it isn't that shape.
    public static func blockFromZip(_ zipConfig: String) -> Text? {
        let t = text(zipConfig)
        guard !t.contains(cr), t.last == lf else { return nil }
        let lines = t.dropLast().split(separator: lf, omittingEmptySubsequences: false).map { Array($0) }
        let m = text(marker), e = text(endMarker)
        guard lines.count >= 2, lines[0] == m, lines[lines.count - 1] == e else { return nil }
        for l in lines[1..<(lines.count - 1)] {
            guard !l.isEmpty, !contains(l, m), !contains(l, e) else { return nil }
            guard let eq = l.firstIndex(of: equals), eq > 0 else { return nil }
            guard l.allSatisfy({ printableASCII($0, allowSpace: true) }) else { return nil }
        }
        return t
    }

    /// The tokens in the zip's cmdline.txt (edit_cmdline_txt of an empty
    /// file): one line, the first-boot hook among them. Nil when it isn't
    /// that shape.
    public static func tokensFromZip(_ zipCmdline: String) -> [Text]? {
        let t = text(zipCmdline)
        guard !t.contains(cr), t.last == lf else { return nil }
        let line = Array(t.dropLast())
        guard !line.contains(lf) else { return nil }
        let tokens = pySplit(line)
        guard !tokens.isEmpty, join(tokens, space) == line, tokens.contains(text(firstrunToken)) else { return nil }
        for tok in tokens where !tok.allSatisfy({ printableASCII($0, allowSpace: false) }) { return nil }
        return tokens
    }
}

/// The setup network's password as the card needs it (overlay.py:
/// _PSK_ALPHABET and _AP_PSK_LEN), and why a typed one can't be it.
public enum CardSetupPassword {
    public static let alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    public static let length = 10

    /// Spaces dropped, lower case (the passwords never contain capitals).
    public static func normalize(_ typed: String) -> String {
        var out = String.UnicodeScalarView()
        for c in typed.unicodeScalars where !c.properties.isWhitespace {
            out.append(contentsOf: String(Character(c)).lowercased().unicodeScalars)
        }
        return String(out)
    }

    /// In household words; nil when it can be the password.
    public static func problem(_ typed: String) -> String? {
        let p = normalize(typed)
        if p.isEmpty { return "Type the setup network password the dashboard showed." }
        let ok = Set(alphabet.unicodeScalars)
        for c in p.unicodeScalars where !ok.contains(c) {
            if "01lo".unicodeScalars.contains(c) {
                return "These passwords never use 0, 1, l or o. Check that character against the dashboard."
            }
            return "\"\(String(Character(c)))\" isn't in these passwords: they're lower-case letters and the digits 2 to 9."
        }
        let n = p.unicodeScalars.count
        if n != length { return "That's \(n) characters; the password has \(length)." }
        return nil
    }
}

/// domovoi/ap.json as the server writes it on a card it prepares itself:
/// json.dumps({"ssid": ..., "psk": ...}, indent=2), UTF-8 without a BOM,
/// LF, no newline at the end.
public enum CardApJson {
    /// Nil when either value isn't printable ASCII (checked before this).
    public static func bytes(ssid: String, psk: String) -> [UInt8]? {
        guard let s = esc(ssid), let p = esc(psk) else { return nil }
        return Array("{\n  \"ssid\": \"\(s)\",\n  \"psk\": \"\(p)\"\n}".utf8)
    }

    static func esc(_ s: String) -> String? {
        var out = ""
        for c in s.unicodeScalars {
            guard c.value >= 0x20 && c.value <= 0x7E else { return nil }
            if c == "\"" || c == "\\" { out.append("\\") }
            out.unicodeScalars.append(c)
        }
        return out
    }
}
