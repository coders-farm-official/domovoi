import Foundation

// The setup network's password for a Wi-Fi setup card, fetched from the
// Domovoi on this Mac so nobody types it: the port of the Windows tray's
// windows/tray/CardKeys.cs.
//
// The dashboard puts a one-time pass in the card files (domovoi/claim.json:
// the build's number, a random secret, and when it stops working, a day
// after the build). The card helper trades it once for the password, over
// this Mac's loopback only: POST /api/satellites/media/claim, which the
// server answers to a loopback peer only. The password is kept in the card
// window's memory only, and goes nowhere else but the card's
// domovoi/ap.json.
//
// When that can't work (the pass was used, it expired, the server
// restarted and forgot the password, the server doesn't answer, or the
// files are from before passes existed) the window says which, in plain
// words, and offers a sign-in with the dashboard's admin password (POST
// /api/auth/login, then the build's credentials or a new password for the
// same network, then sign-out). A new password comes with a new console
// login too: the server sends the matching userconf.txt line, written in
// place of the zip's. Typing the password the dashboard showed stays the
// last way.

public enum KeyProblem: Equatable {
    case none
    case noClaim        // older card files: no pass in them
    case used
    case expired
    case forgotten      // the server restarted since the card was prepared
    case unreachable
    case refused        // the server said no for another reason
    case wrongPassword  // the admin sign-in was refused (detail: the server's words)
    case signInRefused  // any other sign-in refusal (detail: the server's words)
    case unrecoverable  // signed in, and the server can't make a password for these files
}

public struct KeyAnswer: Equatable {
    public var psk: String? = nil
    /// A new password the server made: the dashboard's old one is wrong now.
    public var reissued = false
    public var problem: KeyProblem = .none
    public var detail: String? = nil
    /// The console login to tell the person (nil: the dashboard's still
    /// applies), and, with a new one, the userconf.txt line with its hash.
    public var consoleUser: String? = nil
    public var consolePassword: String? = nil
    public var userconf: String? = nil

    public var ok: Bool { psk != nil }

    public static func got(_ psk: String, reissued: Bool = false) -> KeyAnswer { KeyAnswer(psk: psk, reissued: reissued) }
    public static func fail(_ p: KeyProblem, _ detail: String? = nil) -> KeyAnswer { KeyAnswer(problem: p, detail: detail) }
}

/// The server side, behind a protocol so the tests can answer for it.
public protocol CardKeys: AnyObject {
    func claim(_ c: CardClaim) -> KeyAnswer
    func adminFetch(jobId: Int, ssid: String, adminPassword: String) -> KeyAnswer
}

public enum CardKeyWords {
    public static let used = "This card's one-time pass was already used."
    public static let expired = "This card was prepared more than a day ago, so its pass expired."
    public static let forgotten = "Your Domovoi server restarted since this card was prepared, so it no longer has the password."
    public static let unreachable = "Can't reach your Domovoi server."
    public static let noClaim = "These card files are from an older Domovoi, so they don't carry a one-time pass."
    public static let refused = "Your Domovoi server didn't accept this card's one-time pass."
    public static let unrecoverable = "Your Domovoi server can't recover this card's password. Prepare the card again on the dashboard."

    public static let signInOrPrepare = "Sign in with the dashboard's admin password to fetch it, or prepare the card again on the dashboard."
    public static let checkRunning = "Check that Domovoi is running (its menu-bar icon says so), then try again."
    public static let orType = "Or type the password the dashboard showed."

    /// Why the helper couldn't fetch it.
    public static func reason(_ a: KeyAnswer) -> String {
        switch a.problem {
        case .used: return used
        case .expired: return expired
        case .forgotten: return forgotten
        case .unreachable: return unreachable
        case .noClaim: return noClaim
        case .unrecoverable: return unrecoverable
        case .wrongPassword, .signInRefused: return (a.detail ?? "").isEmpty ? "wrong password" : a.detail!
        default: return refused
        }
    }

    /// Then what to do next.
    public static func nextStep(_ a: KeyAnswer) -> String {
        switch a.problem {
        case .unreachable: return checkRunning + " " + orType
        case .unrecoverable: return orType
        case .wrongPassword, .signInRefused: return "Try the admin password again. " + orType
        default: return signInOrPrepare + " " + orType
        }
    }

    /// Whether signing in can help (not without a pass, nor when the
    /// server can't recover anything).
    public static func offersSignIn(_ a: KeyAnswer) -> Bool { a.problem != .noClaim && a.problem != .unrecoverable }
    public static func offersRetry(_ a: KeyAnswer) -> Bool { a.problem == .unreachable }
}

public enum CardKeyFlow {
    /// The pass traded for the password, or why not. Files with no pass,
    /// and a pass past its time, are answered here without asking.
    public static func fetch(_ keys: CardKeys, _ pkg: CardPackage, now: Date) -> KeyAnswer {
        guard let c = pkg.claim else { return .fail(.noClaim) }
        if let e = c.expiresAt, now >= e { return .fail(.expired) }
        return checked(keys.claim(c))
    }

    public static func fetchAsAdmin(_ keys: CardKeys, _ pkg: CardPackage, adminPassword: String) -> KeyAnswer {
        guard let c = pkg.claim else { return .fail(.unrecoverable) }
        return checked(keys.adminFetch(jobId: c.jobId, ssid: pkg.apSsid ?? "", adminPassword: adminPassword))
    }

    /// A password the card can't use is no password.
    static func checked(_ a: KeyAnswer) -> KeyAnswer {
        if let p = a.psk, CardSetupPassword.problem(p) != nil { return .fail(.refused) }
        if a.ok, let u = a.userconf, !CardChecks.userconfOK(u) { return .fail(.refused) }
        return a
    }
}

/// The real server: the dashboard on this Mac's loopback. `send` is the
/// one HTTP call (the tests answer for it): method, path, JSON body,
/// bearer token; nil when nothing answered.
public final class HTTPCardKeys: CardKeys {
    public typealias Send = (_ method: String, _ path: String, _ json: [String: Any]?, _ bearer: String?) -> (status: Int, body: [String: Any])?

    let send: Send

    public init(send: @escaping Send) { self.send = send }

    /// Straight to http://127.0.0.1:PORT, never through a proxy, no redirects.
    public convenience init(webPort: Int) {
        let base = "http://127.0.0.1:\(webPort)"
        self.init(send: { method, path, json, bearer in HTTPCardKeys.loopback(base, method, path, json, bearer) })
    }

    public func claim(_ c: CardClaim) -> KeyAnswer {
        guard let r = send("POST", "/api/satellites/media/claim", ["job_id": c.jobId, "secret": c.secret], nil) else { return .fail(.unreachable) }
        if r.status == 200 { return Self.psk(r.body, otherwise: .refused, reissued: false) }
        switch r.body["reason"] as? String {
        case "used": return .fail(.used)
        case "expired": return .fail(.expired)
        case "forgotten": return .fail(.forgotten)
        default: return .fail(.refused)
        }
    }

    public func adminFetch(jobId: Int, ssid: String, adminPassword: String) -> KeyAnswer {
        guard let login = send("POST", "/api/auth/login", ["password": adminPassword, "label": "menu-bar card helper"], nil) else { return .fail(.unreachable) }
        var token: String?
        if login.status == 200 {
            token = login.body["token"] as? String
        } else if login.status == 401 {
            return .fail(.wrongPassword, login.body["detail"] as? String)
        } else if login.status != 409 {
            // 409: no admin password set yet; the routes are open until then.
            return .fail(.signInRefused, login.body["detail"] as? String ?? "the server answered \(login.status)")
        }
        // The session was for this one call: ended at once.
        defer { if let t = token { _ = send("POST", "/api/auth/logout", [:], t) } }
        let path = "/api/satellites/media/jobs/\(jobId)"
        guard let got = send("GET", path + "/credentials", nil, token) else { return .fail(.unreachable) }
        // The build's own (not made again): the zip's userconf.txt already
        // matches its console login.
        if got.status == 200, ((got.body["ap"] as? [String: Any])?["ssid"] as? String) == ssid, (got.body["reissued"] as? Bool) != true {
            return Self.psk(got.body, otherwise: .unrecoverable, reissued: false)
        }
        if got.status != 200 && got.status != 404 { return .fail(.signInRefused, got.body["detail"] as? String) }
        // Forgotten (404), made again earlier, or no such network: setup-key.
        guard let made = send("POST", path + "/setup-key", ["ssid": ssid], token) else { return .fail(.unreachable) }
        if made.status == 200 { return Self.psk(made.body, otherwise: .unrecoverable, reissued: (made.body["reissued"] as? Bool) == true) }
        return .fail(.unrecoverable)
    }

    static func psk(_ body: [String: Any], otherwise: KeyProblem, reissued: Bool) -> KeyAnswer {
        guard let p = (body["ap"] as? [String: Any])?["psk"] as? String else { return .fail(otherwise) }
        var a = KeyAnswer.got(p, reissued: reissued)
        let console = body["console"] as? [String: Any]
        a.consoleUser = console?["username"] as? String
        a.consolePassword = console?["password"] as? String
        if a.consoleUser == nil || a.consolePassword == nil { a.consoleUser = nil; a.consolePassword = nil }
        a.userconf = body["userconf"] as? String
        return a
    }

    static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.connectionProxyDictionary = [:]
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        c.httpShouldSetCookies = false
        return URLSession(configuration: c, delegate: NoRedirects(), delegateQueue: nil)
    }()

    final class NoRedirects: NSObject, URLSessionTaskDelegate {
        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
            completionHandler(nil)
        }
    }

    static func loopback(_ base: String, _ method: String, _ path: String, _ json: [String: Any]?, _ bearer: String?) -> (status: Int, body: [String: Any])? {
        guard let u = URL(string: base + path) else { return nil }
        var req = URLRequest(url: u)
        req.httpMethod = method
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.setValue("DomovoiMac", forHTTPHeaderField: "User-Agent")
        // The dashboard's write guard (WEB-6) wants it on every write.
        req.setValue("DomovoiMac", forHTTPHeaderField: "X-Requested-With")
        if let b = bearer { req.setValue("Bearer " + b, forHTTPHeaderField: "Authorization") }
        if let j = json {
            req.httpBody = try? JSONSerialization.data(withJSONObject: j)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let done = DispatchSemaphore(value: 0)
        var out: (status: Int, body: [String: Any])?
        session.dataTask(with: req) { data, resp, _ in
            if let h = resp as? HTTPURLResponse {
                let body = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any] ?? [:]
                out = (h.statusCode, body)
            }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 20)
        return out
    }

    /// The server's identity, from the dashboard's open /api/health.
    public static func serverFingerprint(http: HTTPClient, webPort: Int) -> String? {
        guard let r = http.get("http://127.0.0.1:\(webPort)/api/health", timeout: 5), r.status == 200,
              let o = try? JSONSerialization.jsonObject(with: r.body) as? [String: Any] else { return nil }
        return (o["identity"] as? [String: Any])?["fingerprint"] as? String
    }
}
