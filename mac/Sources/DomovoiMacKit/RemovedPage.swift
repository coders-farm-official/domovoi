import Foundation

/// The last page of an uninstall, laid out from what domovoi-uninstall
/// printed (Sitting 3, S3-8 and S3-10).
///
/// The output is plain lines, which the command line shows as they are:
/// first what was put back on this Mac (the sleep and power-failure lines,
/// MacSideCleanup), then the result (UninstallReport.summary), whose
/// "Left to do by hand:" parts carry the line to paste, indented. Shown as
/// one paragraph, the result came after the sleep line and the line to
/// paste was easy to miss. The page shows the result first, each thing
/// left to do in its own box with the command set apart, and the Mac
/// settings last.
public struct RemovedPage: Equatable {
    public struct ToDo: Equatable {
        /// What is left and why, up to the line to paste.
        public var text: String
        /// The line to paste, when there is one.
        public var command: String?
        /// What follows the line (what to do if Terminal refuses it).
        public var after: String?
    }

    /// "Domovoi removed..." and what was left in place.
    public var result: [String] = []
    public var toDo: [ToDo] = []
    /// What was put back on this Mac (sleep, power failure).
    public var macSettings: [String] = []

    public static func parse(_ output: String) -> RemovedPage {
        let lines = output.components(separatedBy: "\n")
        var page = RemovedPage()
        let start = lines.firstIndex { $0.hasPrefix("Domovoi removed") || $0.hasPrefix("Uninstall did not finish") }
        guard let start else {
            page.result = lines.map(Self.trimmed).filter { !$0.isEmpty }
            return page
        }
        page.macSettings = lines[..<start].map(Self.trimmed).filter { !$0.isEmpty }
        for line in lines[start...] {
            if line.hasPrefix(UninstallReport.unfinishedMarker) {
                page.toDo.append(ToDo(text: trimmed(String(line.dropFirst(UninstallReport.unfinishedMarker.count)))))
                continue
            }
            let t = trimmed(line)
            if t.isEmpty { continue }
            guard var item = page.toDo.popLast() else { page.result.append(t); continue }
            if item.command == nil && item.after == nil && line.hasPrefix("    ") {
                item.command = t
            } else if item.command == nil {
                item.text += " " + t
            } else {
                item.after = item.after.map { $0 + " " + t } ?? t
            }
            page.toDo.append(item)
        }
        return page
    }

    /// Whether `command` is exactly the line the uninstaller gives for the
    /// role account (RoleAccount.finishCommand): only then does the page
    /// offer to open it in Terminal.
    public static func isFinishCommand(_ command: String) -> Bool {
        let one = #"sudo dscl \. -delete /(Users|Groups)/[A-Za-z0-9_]+"#
        return command.range(of: "^\(one)(; \(one))*$", options: .regularExpression) != nil
    }

    /// A script Terminal runs for "Open in Terminal": it says what it does,
    /// runs the line (sudo asks for the Mac password there), says how it
    /// went, and deletes itself. Nil for anything but the finish command.
    public static func terminalScript(for command: String) -> String? {
        guard isFinishCommand(command) else { return nil }
        return """
        #!/bin/sh
        echo "Finishing Domovoi's removal: deleting its hidden account."
        echo "Type your Mac password when asked (it doesn't show as you type)."
        echo
        if ( set -e; \(command) ); then
          echo
          echo "Done: Domovoi's hidden account is removed. You can close this window."
        else
          echo
          echo "That didn't finish. If it said eDSPermissionError, turn Terminal on in System Settings > Privacy & Security > Full Disk Access, then choose Open in Terminal in Domovoi Setup again."
        fi
        rm -f -- "$0"

        """
    }

    static func trimmed(_ s: String) -> String { s.trimmingCharacters(in: .whitespaces) }
}
