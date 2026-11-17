import Foundation

/// Words of Setup's consent page ("What Setup will change") that depend on
/// what the package holds, kept here so the tests can read them.
public enum ConsentText {
    /// The background services in one bullet, counted (Train 1 finding 5:
    /// "three" and then "one more"), and named as Login Items shows them
    /// (finding 19: Ollama appears only as "Infra Technologies, Inc").
    public static func services(ownOllama: Bool, speech: Bool) -> String {
        var labels = ["com.domovoi.mac.vm"]
        if ownOllama { labels.append("com.domovoi.mac.ollama") }
        labels.append("com.domovoi.mac.helper")
        if speech { labels.append("com.domovoi.mac.stt") }
        let count = ["", "one", "two", "three", "four"][labels.count]
        var t = "Adds \(count) background services, \(labels.dropLast().joined(separator: ", ")) and \(labels.last!), in /Library/LaunchDaemons. They start with the Mac, before anyone signs in."
        if speech { t += " The speech one recognises speech on this Mac's graphics chip and listens on this Mac only (port 6371)." }
        var unidentified = ["\"limactl\"", "\"DomovoiHelper\""]
        if speech { unidentified.append("\"whisper-server\"") }
        let names = unidentified.count == 2 ? unidentified.joined(separator: " and ")
            : unidentified.dropLast().joined(separator: ", ") + " and " + unidentified.last!
        t += " In System Settings > General > Login Items & Extensions they are listed under the programs they run, not as Domovoi: \(names) (items from an unidentified developer)"
        t += ownOllama ? ", and Ollama is listed only as \"Infra Technologies, Inc\" (the company that makes it)." : "."
        t += " Switching off limactl stops Domovoi; switching off DomovoiHelper stops other devices from reaching it"
        if ownOllama { t += "; switching off Infra Technologies, Inc turns off AI answers" }
        if speech { t += "; switching off whisper-server doesn't stop Domovoi: it then recognises speech on the processor, a little slower, and its Health page says so" }
        t += "."
        if !ownOllama { t += " The Ollama already on this Mac answers Domovoi's AI questions." }
        return t
    }
}

/// The Installing page's list: one row for every step the helper runs
/// (Installer.steps, in its order) and the setup code read after them.
/// Train 1 finding 6: the speech (stt) and network (lan) steps ran with no
/// row, so the page skipped them silently; InstallStepsTests keeps the two
/// lists equal.
public enum InstallSteps {
    public static let labels: [String: String] = [
        "account": "Making Domovoi's hidden account",
        "folders": "Making Domovoi's folders",
        "media": "Setting up the media folder",
        "lima": "Unpacking the virtual machine tools",
        "ollama": "Starting the AI engine (Ollama)",
        "stt": "Setting up speech recognition on this Mac's graphics chip",
        "vm-config": "Downloading the Linux system",
        "vm-create": "Creating the virtual machine",
        "vm-start": "Starting the virtual machine",
        "provision": "Installing Domovoi in it (the longest step)",
        "host-files": "Telling it this Mac's address",
        "lan": "Letting phones and room speakers on your network reach it",
        "menu-bar": "Adding the Domovoi icon to the menu bar",
        "power": "Setting Keep this Mac awake",
        "health": "Checking that Domovoi answers",
        "setup-code": "Reading your setup code",
    ]

    /// The rows, in order. `speech` false (a package without the speech
    /// service) leaves the stt row out: that step only says it was skipped.
    public static func rows(speech: Bool) -> [(id: String, label: String)] {
        (Installer.steps + ["setup-code"])
            .filter { speech || $0 != "stt" }
            .map { ($0, labels[$0] ?? $0) }
    }
}

extension ProvisionProgress {
    /// The Installing page's line for a step inside the virtual machine, in
    /// words (Train 1 finding 6: it showed the raw "apt start 1% Installing
    /// system packages"): the step's own message, else a plain
    /// "Installing Domovoi", and how far the whole install is.
    public var plainLine: String {
        let words = (message?.trimmingCharacters(in: .whitespaces)).flatMap { $0.isEmpty ? nil : $0 } ?? "Installing Domovoi"
        guard let p = percent, p >= 0, p <= 100 else { return words }
        return "\(words) (\(p)% of the way)"
    }
}
