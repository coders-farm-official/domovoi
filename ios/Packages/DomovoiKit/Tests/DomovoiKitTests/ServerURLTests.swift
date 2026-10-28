import XCTest
@testable import DomovoiKit

/// `ServerURL` is a hand-written authority parser, and both of the app's
/// network security rules are built on what it returns: ``CleartextPolicy``
/// decides from `host` whether plain http may leave the phone, and
/// ``TokenScope`` decides from `scheme`, `host` and `port` whether the
/// household token may. Android gets those three from OkHttp's `HttpUrl`,
/// which has its own test suite behind it; here the parser is ours, so this
/// file is the only thing standing behind both rules.
///
/// There is no Kotlin twin to mirror: on Android this file's job is done by a
/// library. The cases are therefore chosen by branch — every refusal in
/// `ServerURL.swift` has one, and the ones that would be a security bug if
/// they stopped refusing (userinfo, a malformed IPv6 literal) have several.
///
/// Nothing here has been compiled: there is no Mac yet.
final class ServerURLTests: XCTestCase {

    // ---- userinfo: the branch that matters most -----------------------------

    /// `http://evil.com@192.168.1.1/` is a request to 192.168.1.1 that reads
    /// as a request to evil.com. Both rules would be looking at the wrong
    /// half of it — ``CleartextPolicy`` at the host it is really talking to,
    /// ``TokenScope`` at a host that is not the saved server — so an address
    /// with userinfo is refused outright rather than parsed carefully.
    /// OkHttp would accept it; nothing in either app ever builds one.
    func testAnAddressWithUserinfoIsRefused() {
        for text in [
            "http://evil.com@192.168.1.1/",
            "http://evil.com@192.168.1.1",
            "http://evil.com@192.168.1.1:6369/",
            "https://evil.com@192.168.1.1:6369/",
            "http://user:pass@192.168.1.1:6369/",
            "http://user:@192.168.1.1/",
            "http://:pass@192.168.1.1/",
            "http://@192.168.1.1/",
            "http://evil.com@domovoi.lan/",
            "http://evil.com@[fc00::1]:6369/",
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    /// The same refusal through the socket spelling, because that is the one
    /// a stream URL or a drop-in upgrade could arrive in.
    func testUserinfoIsRefusedInASocketAddressToo() {
        XCTAssertNil(ServerURL.parseAllowingWebSocket("ws://evil.com@192.168.1.1:6369/ws/state"))
        XCTAssertNil(ServerURL.parseAllowingWebSocket("wss://user:pass@192.168.1.1:6369/ws/state"))
    }

    /// The `@` test is on the authority, not on the whole string: an `@` in
    /// the path is ordinary and must not refuse the address.
    func testAnAtSignAfterTheAuthorityIsNotUserinfo() throws {
        let url = try parse("http://192.168.1.20:6369/api/mail/to@example.com")
        XCTAssertEqual(url.host, "192.168.1.20")
        XCTAssertEqual(url.port, 6369)
    }

    // ---- the port -----------------------------------------------------------

    func testThePortBoundsAreOneToSixtyFiveThousandFiveHundredAndThirtyFive() throws {
        XCTAssertEqual(try parse("http://domovoi.lan:1/").port, 1)
        XCTAssertEqual(try parse("http://domovoi.lan:6369/").port, 6369)
        XCTAssertEqual(try parse("http://domovoi.lan:65535/").port, 65535)
        for text in [
            "http://domovoi.lan:0/",        // zero is not a port
            "http://domovoi.lan:65536/",    // one above the top
            "http://domovoi.lan:999999/",   // too many digits to even parse
            "http://domovoi.lan:/",         // the colon with nothing after it
            "http://domovoi.lan:abc/",
            "http://domovoi.lan:+1/",       // `Int("+1")` is 1; the parser is not `Int`
            "http://domovoi.lan:-1/",
            "http://domovoi.lan: 1/",
            "http://domovoi.lan:6 9/",
            "http://domovoi.lan:6369.0/",
            "http://domovoi.lan:\u{FF16}\u{FF13}\u{FF16}\u{FF19}/",   // fullwidth 6369
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    /// A leading zero is accepted and the value is read in decimal, which is
    /// what OkHttp does (`"06369".toInt()` is 6369), so Android accepts this
    /// address and iOS must agree with it.
    func testALeadingZeroInThePortIsAcceptedAsOkhttpAcceptsIt() throws {
        XCTAssertEqual(try parse("http://domovoi.lan:06369/").port, 6369)
        XCTAssertEqual(try parse("http://domovoi.lan:00001/").port, 1)
        XCTAssertNil(ServerURL.parse("http://domovoi.lan:006369/"))   // six digits
    }

    /// A port with nothing in front of it is not an address. The host check
    /// is what refuses it, after the port has already parsed, so this pins
    /// that the two guards are both in the `&&`.
    func testAPortWithNoHostIsRefused() {
        for text in ["http://:6369/", "https://:443/", "http://:6369", "http://:0/"] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    /// `lastIndex(of: ":")` splits at the last colon, so a second one lands
    /// in the host and the host check refuses it.
    func testTwoPortsAreNotAnAddress() {
        XCTAssertNil(ServerURL.parse("http://domovoi.lan:6369:80/"))
    }

    // ---- the default ports --------------------------------------------------

    /// 80 for http and 443 for https. These are not decoration: ``TokenScope``
    /// compares ports, so `https://domovoi.lan` and `https://domovoi.lan:443`
    /// have to be the same server, the way OkHttp's implied port makes them
    /// the same on Android.
    func testTheSchemeDefaultPortIsFilledIn() throws {
        XCTAssertEqual(try parse("http://domovoi.lan/").port, 80)
        XCTAssertEqual(try parse("http://domovoi.lan").port, 80)
        XCTAssertEqual(try parse("http://domovoi.lan?probe=1").port, 80)
        XCTAssertEqual(try parse("http://domovoi.lan#here").port, 80)
        XCTAssertEqual(try parse("https://domovoi.lan/").port, 443)
        XCTAssertEqual(try parse("https://domovoi.lan").port, 443)
        XCTAssertEqual(try parse("http://[fc00::1]").port, 80)
        XCTAssertEqual(try parse("https://[fc00::1]").port, 443)
        XCTAssertEqual(try parse("http://domovoi.lan/"), try parse("http://domovoi.lan:80/"))
        XCTAssertEqual(try parse("https://domovoi.lan/"), try parse("https://domovoi.lan:443/"))
    }

    // ---- the scheme ---------------------------------------------------------

    func testOnlyHttpAndHttpsParseAndTheSchemeIsLowercased() throws {
        XCTAssertEqual(try parse("HTTP://192.168.1.20:6369/").scheme, "http")
        XCTAssertEqual(try parse("HttpS://domovoi.lan:6369/").scheme, "https")
        XCTAssertFalse(try parse("HTTP://domovoi.lan/").isHttps)
        XCTAssertTrue(try parse("HTTPS://domovoi.lan/").isHttps)
        for text in [
            "ws://192.168.1.20:6369/", "wss://192.168.1.20:6369/",
            "ftp://192.168.1.20/", "file:///etc/hosts", "javascript://x/",
            "192.168.1.20:6369", "//192.168.1.20:6369/", "http:/192.168.1.20/",
            "://192.168.1.20/", "", "http://",
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    func testASocketSchemeIsReadAsItsHttpForm() throws {
        let plain = try XCTUnwrap(ServerURL.parseAllowingWebSocket("ws://192.168.1.20:6369/ws/state"))
        XCTAssertEqual(plain.scheme, "http")
        XCTAssertEqual(plain.host, "192.168.1.20")
        XCTAssertEqual(plain.port, 6369)
        let secure = try XCTUnwrap(ServerURL.parseAllowingWebSocket("WSS://Domovoi.LAN/ws/state"))
        XCTAssertEqual(secure.scheme, "https")
        XCTAssertEqual(secure.host, "domovoi.lan")
        XCTAssertEqual(secure.port, 443)
        // An http address still goes through the ordinary parse.
        XCTAssertEqual(ServerURL.parseAllowingWebSocket("http://domovoi.lan:6369/"),
                       ServerURL.parse("http://domovoi.lan:6369/"))
        XCTAssertNil(ServerURL.parseAllowingWebSocket("ftp://domovoi.lan/"))
    }

    // ---- the host name ------------------------------------------------------

    func testAHostNameIsAsciiLettersDigitsDotDashAndUnderscore() throws {
        XCTAssertEqual(try parse("http://domovoi.lan/").host, "domovoi.lan")
        XCTAssertEqual(try parse("http://Domovoi.LAN:6369/").host, "domovoi.lan")
        XCTAssertEqual(try parse("http://my_router.lan/").host, "my_router.lan")
        XCTAssertEqual(try parse("http://pi-4.domovoi.local/").host, "pi-4.domovoi.local")
        XCTAssertEqual(try parse("http://192.168.1.20/").host, "192.168.1.20")
    }

    /// Everything the host check refuses. A backslash and a `%` are the two
    /// that matter: both are ways to write a different authority than the one
    /// a reader sees, and both would travel into a `URLRequest` in a later
    /// batch. The tab and the non-ASCII character are there because Swift's
    /// `Character` ranges and `String` comparison take far more than ASCII,
    /// which is why the check is written over `utf8`.
    func testAHostNameWithAnythingElseIsRefused() {
        for text in [
            "http://domovoi\\.lan/",          // backslash
            "http://domovoi\\lan:6369/",
            "http://domovoi%2elan/",          // percent-encoding
            "http://domovoi%00.lan/",
            "http://domovoi\tlan/",           // tab
            "http://domovoi\nlan/",
            "http://domovoi lan/",            // space
            "http://d\u{00F6}movoi.lan/",     // ö, a non-ASCII character
            "http://\u{4F60}\u{597D}.lan/",   // CJK
            "http://domovoi\u{0000}.lan/",    // NUL
            "http://domovoi.lan\u{00A0}/",    // no-break space
            "http:///",                       // empty authority
            "http:///api/health",
            "http://?probe=1",
            "http://#here",
            "http://[fc00::1]extra/",         // rubbish after the brackets
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    func testAHostNameIsCappedAtTwoHundredAndFiftyThreeBytes() throws {
        let longest = String(repeating: "a", count: 253)
        XCTAssertEqual(try parse("http://\(longest)/").host, longest)
        XCTAssertNil(ServerURL.parse("http://\(String(repeating: "a", count: 254))/"))
    }

    /// A trailing dot survives, as the type documents: ``CleartextPolicy``
    /// strips one itself and that is the only place it matters.
    func testATrailingDotIsKeptAsTyped() throws {
        XCTAssertEqual(try parse("http://domovoi.local./").host, "domovoi.local.")
        XCTAssertTrue(CleartextPolicy.permits(try parse("http://domovoi.local./")))
    }

    // ---- IPv6 literals ------------------------------------------------------

    func testABracketedIpv6LiteralParsesWithoutItsBrackets() throws {
        XCTAssertEqual(try parse("http://[fc00::1]:6369/").host, "fc00::1")
        XCTAssertEqual(try parse("http://[FC00::1]:6369/").host, "fc00::1")
        XCTAssertEqual(try parse("http://[::1]:6369/").host, "::1")
        XCTAssertEqual(try parse("http://[::]/").host, "::")
        XCTAssertEqual(try parse("http://[fc00:0:0:0:0:0:0:1]/").host, "fc00:0:0:0:0:0:0:1")
        XCTAssertEqual(try parse("https://[2001:0db8:0000:0000:0000:0000:0000:0001]:443/").host,
                       "2001:0db8:0000:0000:0000:0000:0000:0001")
        // An embedded IPv4 in the low 32 bits.
        XCTAssertEqual(try parse("http://[::ffff:192.168.1.1]:6369/").host, "::ffff:192.168.1.1")
        XCTAssertEqual(try parse("http://[0:0:0:0:0:ffff:192.168.1.1]/").host, "0:0:0:0:0:ffff:192.168.1.1")
    }

    /// A zone id comes through, in either spelling, and nothing but RFC 6874's
    /// unreserved characters is allowed into it — the zone travels inside
    /// `host`, so a NUL or a control character in it would travel too.
    func testABracketedIpv6LiteralKeepsAWellFormedZone() throws {
        XCTAssertEqual(try parse("http://[fe80::1%en0]:6369/").host, "fe80::1%en0")
        XCTAssertEqual(try parse("http://[fe80::1%25en0]:6369/").host, "fe80::1%25en0")
        XCTAssertEqual(try parse("http://[FE80::1%En0]/").host, "fe80::1%en0")
        XCTAssertEqual(try parse("http://[fe80::1%wlan0]/").host, "fe80::1%wlan0")
        XCTAssertEqual(try parse("http://[fe80::1%3]/").host, "fe80::1%3")
        for text in [
            "http://[fe80::1%]/",                       // the zone is empty
            "http://[fe80::1%\u{0000}]/",               // a NUL
            "http://[fe80::1%en 0]/",                   // a space
            "http://[fe80::1%en\t0]/",
            "http://[fe80::1%en\u{00A0}0]/",
            "http://[fe80::1%en0*]/",
            "http://[fe80::1%en:0]/",
            "http://[fe80::1%\(String(repeating: "e", count: 65))]/",   // absurdly long
            "http://[%en0]/",                           // a zone with no address
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    /// The bracket guard: a `]` in an authority that did not start with `[`
    /// is refused before anything else looks at it.
    func testAClosingBracketWithoutAnOpeningOneIsRefused() {
        for text in [
            "http://fc00::1]:6369/",
            "http://domovoi.lan]/",
            "http://domovoi]lan:6369/",
            "http://[[fc00::1]/",
            "http://[]/",
            "http://[]:6369/",
            "http://[fc00::1/",      // never closed
            "http://[fc00::1]:/",    // the colon with no port
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    /// An IPv6 address must be bracketed. Unbracketed, its colons are read as
    /// a port separator and the host check refuses what is left — which is
    /// the same answer OkHttp gives, by a different route.
    func testAnUnbracketedIpv6AddressIsRefused() {
        for text in [
            "http://fc00::1/",
            "http://fc00::1:6369/",
            "http://::1/",
            "http://::1:6369/",
            "http://2001:db8::1/",
            "http://fe80::1%en0/",
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    /// The structural check. Before it existed, every one of these parsed —
    /// the first of them, `[fc00:::::::::::::]`, gave `host` as
    /// `fc00:::::::::::::`, which ``CleartextPolicy`` read the first hextet
    /// of and **permitted in the clear**. OkHttp refuses the string, so
    /// Android refuses the address, and the two apps must agree.
    func testAMalformedIpv6LiteralIsRefused() {
        for text in [
            "http://[fc00:::::::::::::]:6369/",   // the one that got through
            "http://[fc00:::1]/",                 // a stray third colon
            "http://[:::]/",
            "http://[fc00::1::2]/",               // two elisions
            "http://[::1::]/",
            "http://[1:2:3:4:5:6:7:8:9]/",        // nine hextets
            "http://[1:2:3:4:5:6:7]/",            // seven, with nothing elided
            "http://[1:2:3:4:5:6:7:8::]/",        // eight written AND an elision
            "http://[fc00::12345]/",              // a five-digit hextet
            "http://[fc00::g]/",                  // not hex
            "http://[fc00::1z]/",
            "http://[fc00:]/",                    // a trailing colon, not an elision
            "http://[:fc00::1]/",                 // a leading one
            "http://[fc00..1]/",                  // no colon at all
            "http://[fc00]/",
            "http://[192.168.1.20]/",             // a dotted quad is not an IPv6 literal
            "http://[fc00::1.2.3.4.5]/",          // five octets
            "http://[fc00::1.2.3]/",              // three
            "http://[fc00::256.1.1.1]/",          // out of range
            "http://[1.2.3.4::1]/",               // IPv4 outside the low 32 bits
            "http://[::ffff:192.168.1.1:1]/",     // IPv4 not last
            "http://[fc00::1 ]/",                 // a space
            "http://[fc00::1\u{0000}]/",
        ] {
            XCTAssertNil(ServerURL.parse(text), text)
        }
    }

    /// Plain http to a bracketed IPv6 literal, end to end: the host comes out
    /// of the brackets and ``CleartextPolicy`` reads *that*, not the scheme.
    ///
    /// `CleartextPolicyTests` has a bracketed case already, but it is https,
    /// so `permits` short-circuits on `isHttps` and the case proves nothing
    /// about what came out of the parser. These are http, so the host is the
    /// only thing that can decide them.
    func testPlainHttpToABracketedIpv6LiteralIsDecidedByTheHost() throws {
        for host in ["fc00::1", "fd12:3456::1", "fe80::1", "fe80::1%en0", "::1"] {
            let url = try parse("http://[\(host)]:6369/api/health")
            XCTAssertEqual(url.scheme, "http")
            XCTAssertFalse(url.isHttps)
            XCTAssertEqual(url.host, host)
            XCTAssertEqual(url.port, 6369)
            XCTAssertTrue(CleartextPolicy.permits(url), host)
        }
        for host in ["2001:db8::1", "2606:4700::1111", "fb00::1", "fec0::1"] {
            let url = try parse("http://[\(host)]:6369/api/health")
            XCTAssertEqual(url.host, host)
            XCTAssertFalse(CleartextPolicy.permits(url), host)
            // The same address over https is fine, which is the way out the
            // refusal message offers.
            XCTAssertTrue(CleartextPolicy.permits(try parse("https://[\(host)]:6369/")))
        }
    }

    // ---- the invariants the rules depend on ---------------------------------

    /// The initializer is internal and normalises, so `scheme` and `host` are
    /// always lowercased however a `ServerURL` was made. A value built with
    /// `"HTTPS"` and `"Domovoi.LAN"` used to have `isHttps` false and a host
    /// that ``TokenScope/sameServer(_:_:)`` could never match.
    func testTheInitializerLowercasesSchemeAndHost() throws {
        let built = ServerURL(scheme: "HTTPS", host: "Domovoi.LAN", port: 443)
        XCTAssertEqual(built.scheme, "https")
        XCTAssertEqual(built.host, "domovoi.lan")
        XCTAssertTrue(built.isHttps)
        XCTAssertEqual(built, try parse("https://Domovoi.LAN/"))
        XCTAssertTrue(TokenScope.sameServer(built, try parse("https://DOMOVOI.lan:443/api/health")))
    }

    // ---- helper ------------------------------------------------------------

    private func parse(_ text: String) throws -> ServerURL {
        try XCTUnwrap(ServerURL.parse(text), text)
    }
}
