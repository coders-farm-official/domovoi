@testable import DomovoiKit

/// A gate that admits every server: no identity rules at all.
///
/// **Why this exists instead of leaving the argument out.** It used to be
/// possible: ``DomovoiClient/gate`` was `(any TokenGate)? = nil`, and a nil
/// gate meant "nothing beyond being the active server". That default argument
/// was a fail-open — the easiest client to write was the one that put the
/// household token on the active server with no identity proof taken, which
/// is finding A6-03 reachable by omission. I1-C part 2 made the gate
/// non-optional with no default, so every client states its choice, and this
/// is what the choice "no identity rules" looks like when it is stated.
///
/// An explicit permissive gate is safer than an absent one for two reasons
/// that have nothing to do with what the code does at runtime (which is
/// identical):
///
///  * it cannot be reached by forgetting something. A test that wants no
///    identity rules names them away; a test that forgets the argument does
///    not compile. The old shape made the dangerous client the short one.
///  * it is greppable. "Which clients apply no identity rules?" has an
///    answer you can search for, and a reviewer reading a call site sees the
///    decision rather than an absence.
///
/// **It lives in the test target on purpose.** Nothing in `DomovoiKit` wants
/// a client that admits every server: the app's client has the real
/// ``IdentityGate`` and the identity gate's own probe client has
/// ``AdmitsNoServer``. A permissive gate exported from the module would be
/// the fail-open back again, one `import` away, so the module exports only
/// the refusing one.
///
/// Use it where the subject under test is one of the six rules *in front* of
/// the gate (scope, room mode, the saved token, token health, the wait) or
/// something else entirely — the capabilities fetch, device registration, the
/// sweep. Where the gate itself is the subject, the tests use their own
/// `ClosedGate` / `RecordingGate` stubs, which say more.
struct AdmitsAnyServer: TokenGate {
    func requireAdmitted(_ base: ServerURL) throws {}

    /// Unconditionally true, which is what the nil gate did
    /// (`isPairingRefusalFromOurServer` was `guard let gate else { return
    /// true }`) and **not** what ``TokenGate``'s default body does — that is
    /// Kotlin's `base != null`. Written out so the tests that assert the
    /// pairing screen rises keep meaning what they meant.
    func admitsPairingPrompt(_ base: ServerURL?) -> Bool { true }
}
