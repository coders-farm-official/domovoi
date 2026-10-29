import Foundation
import Observation

/// The paste field that **checks** a household token with the server before
/// it is saved, as state and decisions: `CheckedPairingField`
/// (`ui/screens/settings/PairingScreen.kt:144`) without its drawing.
///
/// One component for all three places a token is pasted — the pairing screen,
/// Settings → Phones & browsers → Household token, and the token-trouble
/// card — because that is what Android has and because the words have to be
/// the same in all three. The rules and the words are I1-B's
/// (``TokenPasteField``); what is here is the call, the save and the wait.
///
/// ### The two things that make this worth a type
///
///  - **Never retried.** One attempt per tap. A wrong paste costs one wrong
///    token on the server's count, no more — the server allows five per
///    address before it starts making this phone wait, and an app that
///    retried would spend them all on one paste.
///  - **Nothing is saved unless it was accepted.** The incident this came
///    from (2026-10-17) was an old token saved unchecked, after which every
///    page failed. ``submit()`` writes the vault on exactly one branch.
@MainActor
@Observable
public final class PairingFieldModel {

    /// The network call. Production is
    /// ``DomovoiClient/checkDeviceToken(_:)``, which refuses before sending
    /// when the identity gate does not admit the server — so a rogue's 401
    /// cannot turn a paste into a token on the wire (A6-03).
    public typealias Check = (String) async -> TokenCheck

    private let check: Check
    private let store: any ShellStore
    private let clearPairingRequired: () -> Void
    private let clock: @Sendable () -> Int64
    private let tick: @Sendable (Int64) async throws -> Void
    private let onSaved: (String) -> Void

    /// The word on the button when nothing is in flight: `pair` or `replace`.
    public let label: String

    public var typed = "" {
        didSet {
            guard typed != oldValue else { return }
            // Editing clears the message — **but only when no wait is
            // running**, because a wait is a fact about the server and not
            // about what is in the field (`PairingScreen.kt:174`).
            if result != nil, waitLeftMs == 0 { result = nil }
        }
    }

    public private(set) var checking = false
    public private(set) var result: TokenCheck?

    /// When the server will next look at a token from this address, on the
    /// same monotonic clock ``TokenHealth`` uses. Zero for "now".
    public private(set) var waitUntilMs: Int64 = 0

    /// The clock as the countdown last read it.
    ///
    /// **Stored rather than read live, and that is the whole reason the
    /// countdown exists.** A computed `clock()` would be correct and would
    /// never redraw: nothing observable changes, so `Observation` has no
    /// reason to tell a view to ask again, and "wait 0:42" would sit at 0:42
    /// until something else moved. Android's `now` is Compose state for the
    /// same reason (`PairingScreen.kt:168`).
    public private(set) var nowMs: Int64 = 0

    private var countdown: Task<Void, Never>?
    private var countdownGeneration = 0

    public init(
        store: any ShellStore,
        label: String = ShellStrings.pair,
        check: @escaping Check,
        clearPairingRequired: @escaping () -> Void = {},
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        tick: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        onSaved: @MainActor @escaping (String) -> Void = { _ in }
    ) {
        self.store = store
        self.label = label
        self.check = check
        self.clearPairingRequired = clearPairingRequired
        self.clock = clock
        self.tick = tick
        self.onSaved = onSaved
    }

    /// Over a real client.
    public convenience init(
        container: AppContainer,
        label: String = ShellStrings.pair,
        onSaved: @MainActor @escaping (String) -> Void = { _ in }
    ) {
        let client = container.client
        self.init(
            store: container.store,
            label: label,
            check: { await client.checkDeviceToken($0) },
            clearPairingRequired: { client.clearPairingRequired() },
            onSaved: onSaved
        )
    }

    // MARK: - what is drawn

    /// How long the server still makes this address wait.
    public var waitLeftMs: Int64 { max(0, waitUntilMs - nowMs) }

    /// The field and the button are off while a check is in flight and for
    /// the whole of a wait.
    public var isEnabled: Bool { !checking && waitLeftMs == 0 }

    /// Whether the button does anything: 12…128 printable-ASCII characters,
    /// **and** the field live. Not "not blank" — a value the HTTP layer would
    /// refuse used to be saved unchecked and then throw, with the token in
    /// the message, on every later request.
    public var canSubmit: Bool { TokenPasteField.isUsable(typed) && isEnabled }

    public var buttonLabel: String { checking ? ShellStrings.checking : label }

    /// The one hint under the field, or nil.
    ///
    /// ``TokenPasteField/hint(_:)``, which asks only whether the text is
    /// usable and **not** whether the field is live. Android folds `enabled`
    /// into the same question (`PairingScreen.kt:251`, `usable = isStorable
    /// && enabled`), so a perfectly good token shows "at least 12
    /// characters" while the check is in flight or a wait is counting down.
    /// I1-B ported the rule without that, which is the better behaviour;
    /// reported for Android rather than copied.
    public var hint: String? { TokenPasteField.hint(typed) }

    /// What the server said, in the person's words, or nil.
    public var message: String? { TokenPasteField.message(result, waitLeftMs: waitLeftMs) }

    /// Whether ``message`` is a refusal (drawn in the error colour) rather
    /// than a warning.
    public var messageIsRefusal: Bool { result == .refused }

    // MARK: - the tap

    /// Check the pasted token with the server, once, and save it only if it
    /// was accepted.
    @discardableResult
    public func submit() async -> TokenCheck? {
        guard canSubmit else { return nil }
        let candidate = TokenPasteField.trimmed(typed)
        checking = true
        let answer = await check(candidate)
        checking = false
        result = answer
        switch answer {
        case .accepted:
            // The one branch that writes. The token is keyed by the saved
            // spelling, which is the key `ServerBook` reads a request's token
            // from, so a server switch between the paste and the next request
            // cannot pair the new address with this household's token
            // (A6-01).
            store.setToken(candidate, for: store.serverURL)
            clearPairingRequired()
            typed = ""
            result = nil
            onSaved(candidate)
        case .throttled(let waitMs):
            nowMs = clock()
            waitUntilMs = nowMs + waitMs
            startCountdown()
        case .refused, .unavailable:
            break
        }
        return answer
    }

    /// Whether the countdown is actually being written, which is what makes
    /// "wait 0:42" a number that moves.
    public var isCountingDown: Bool { countdown != nil }

    public func stop() {
        countdown?.cancel()
        countdown = nil
    }

    /// Re-read the wait once a second until there is none left.
    ///
    /// **Bounded by a count and not only by the clock.** The loop's own
    /// condition is ``waitLeftMs``, which falls as the clock moves — but a
    /// clock that does not move (a test's, a `tick` that returns at once)
    /// would make the same loop spin for ever, and a spin loop in an earlier
    /// batch hung the suite instead of failing it. So the number of ticks a
    /// wait can take is computed from the wait: one a second, plus two for
    /// the edges. `generation` keeps a task that finished after it was
    /// superseded from emptying its successor's slot — the same shape as
    /// ``ShellModel``'s countdown, for the same reason.
    private func startCountdown() {
        countdown?.cancel()
        countdownGeneration += 1
        let generation = countdownGeneration
        let ticksAllowed = Int(waitLeftMs / tokenTroubleTickMs) + 2
        countdown = Task { @MainActor [weak self] in
            guard let model = self else { return }
            var ticked = 0
            while !Task.isCancelled, model.waitLeftMs > 0, ticked < ticksAllowed {
                do { try await model.tick(tokenTroubleTickMs) } catch { break }
                ticked += 1
                model.nowMs = model.clock()
            }
            guard model.countdownGeneration == generation else { return }
            model.countdown = nil
        }
    }
}
