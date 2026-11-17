import Foundation

/// The system's administrator prompt, as the setup pages and the menu-bar
/// icon use it: `osascript -e 'do shell script "..." with prompt "..." with
/// administrator privileges'`. The apps never run as root themselves; what
/// runs as root is the helper (or domovoi-uninstall) from Domovoi's
/// root-only folder, with a command line from SetupCommands.
public enum AdminScript {
    public static let osascript = "/usr/bin/osascript"

    /// The shell line: every word single-quoted, standard output and error
    /// to the two files, nothing on standard input; `background` ends it
    /// with `&` (the prompt returns once the password is accepted). A word
    /// with a single quote in it is refused rather than escaped.
    public static func shell(_ program: String, _ args: [String], out: String, err: String, background: Bool) throws -> String {
        for s in [program, out, err] + args where s.contains("'") {
            throw HelperError("a path has a quote in it: \(s)")
        }
        func q(_ s: String) -> String { "'\(s)'" }
        var line = ([q(program)] + args.map(q)).joined(separator: " ") + " >\(q(out)) 2>\(q(err)) </dev/null"
        if background { line += " &" }
        return line
    }

    /// The AppleScript for osascript's -e.
    /// Which password macOS wants (Train 1 finding 15: "needs an
    /// administrator" read like the Domovoi admin password). Every Domovoi
    /// administrator prompt and the alert before it end with it.
    public static let macPasswordNote = "Type the name and password of an administrator of this Mac (the password you use to log in to the Mac), not your Domovoi admin password."

    /// A prompt for macOS's administrator dialog: what it is for, then which password.
    public static func prompt(_ what: String) -> String { what + " " + macPasswordNote }

    public static func appleScript(shell: String, prompt: String) -> String {
        func esc(_ s: String) -> String { s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") }
        return "do shell script \"\(esc(shell))\" with prompt \"\(esc(prompt))\" with administrator privileges"
    }

    /// osascript's way of saying the person chose Cancel.
    public static func cancelled(_ stderr: String) -> Bool { stderr.contains("-128") }

    /// The helper's own message from its standard error ("DomovoiHelper: why"),
    /// for a sentence in the menu.
    public static func reason(_ stderr: String) -> String {
        let lines = stderr.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard var last = lines.last else { return "it gave no reason" }
        for p in ["DomovoiHelper: ", "domovoi-uninstall: "] where last.hasPrefix(p) { last = String(last.dropFirst(p.count)) }
        return last
    }
}
