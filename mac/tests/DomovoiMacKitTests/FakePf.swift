import Foundation
@testable import DomovoiMacKit

/// pfctl and sysctl as the network part uses them: the main ruleset's
/// anchor points, the com.apple/domovoi anchor's rules, the reference
/// tokens, net.inet.ip.forwarding and the boot. The answers' shapes are
/// macOS's (pfctl -E prints its token on stderr after the ALTQ lines).
final class FakePf {
    var mainLoaded = true
    var anchor = ""
    var refs: [String] = []
    var nextToken = 4000
    var forwarding = "0"
    var boot = "1761868281"
    var calls: [[String]] = []

    static let stockPfConf = """
    scrub-anchor "com.apple/*"
    nat-anchor "com.apple/*"
    rdr-anchor "com.apple/*"
    dummynet-anchor "com.apple/*"
    anchor "com.apple/*"
    load anchor "com.apple" from "/etc/pf.anchors/com.apple"

    """

    var enabled: Bool { !refs.isEmpty }

    /// Pretends the Mac restarted: pf's rules, references and forwarding
    /// are the new boot's.
    func reboot() {
        boot = String(Int(boot)! + 1000)
        anchor = ""
        refs = []
        forwarding = "0"
    }

    func ran(_ args: [String]) -> Bool { calls.contains(args) }

    func install(on r: FakeRunner) {
        r.on { [unowned self] c in
            let a = c.arguments
            if c.executable == PfController.sysctl {
                if a == ["-n", "kern.boottime"] {
                    return CommandResult(status: 0, stdout: "{ sec = \(self.boot), usec = 5 } Thu Oct 30 22:51:21 2026\n")
                }
                if a == ["-n", PfController.forwardingKey] { return CommandResult(status: 0, stdout: self.forwarding + "\n") }
                if a.first == "-w", let v = a.last?.split(separator: "=").last {
                    self.calls.append(["sysctl"] + a)
                    self.forwarding = String(v)
                    return CommandResult(status: 0)
                }
                return nil
            }
            guard c.executable == PfController.pfctl else { return nil }
            self.calls.append(a)
            if a == ["-s", "nat"] {
                return CommandResult(status: 0, stdout: self.mainLoaded ? "nat-anchor \"com.apple/*\" all\nrdr-anchor \"com.apple/*\" all\n" : "")
            }
            if a == ["-s", "rules"] {
                return CommandResult(status: 0, stdout: self.mainLoaded ? "scrub-anchor \"com.apple/*\" all fragment reassemble\nanchor \"com.apple/*\" all\n" : "")
            }
            if a == ["-s", "References"] {
                return CommandResult(status: 0, stdout: "TOKEN                PID    PROCESS\n" + self.refs.map { "\($0)    77     DomovoiHelper\n" }.joined())
            }
            if a == ["-E"] {
                self.nextToken += 1
                self.refs.append(String(self.nextToken))
                return CommandResult(status: 0, stderr: "No ALTQ support in kernel\nALTQ related functions disabled\npf enabled\nToken : \(self.nextToken)\n")
            }
            if a.count == 2, a[0] == "-X" {
                self.refs.removeAll { $0 == a[1] }
                return CommandResult(status: 0, stderr: self.refs.isEmpty ? "pf disabled\n" : "")
            }
            if a.count >= 3, a[0] == "-a", a[1] == PfRules.anchor {
                switch a[2] {
                case "-f":
                    self.anchor = (try? String(contentsOfFile: a[3], encoding: .utf8)) ?? ""
                    return CommandResult(status: 0)
                case "-F":
                    self.anchor = ""
                    return CommandResult(status: 0)
                case "-s":
                    // `-s nat`: the translation rules; `-s rules`: the filter rules.
                    let want = a.count > 3 && a[3] == "rules" ? "block" : "rdr"
                    return CommandResult(status: 0, stdout: self.anchor.split(separator: "\n").filter { $0.hasPrefix(want) }.joined(separator: "\n"))
                default:
                    break
                }
            }
            if a.count == 2, a[0] == "-f" {
                self.mainLoaded = true
                return CommandResult(status: 0)
            }
            return CommandResult(status: 1, stderr: "fake pfctl: unexpected \(a)")
        }
    }
}
