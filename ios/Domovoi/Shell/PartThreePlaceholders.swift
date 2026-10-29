import DomovoiKit
import SwiftUI

// Part 3's four screens, each a destination of part 2's graph and nothing
// more. They are here so the graph is whole and so a Simulator run reaches
// every state; the batch that builds them replaces each file-local view and
// changes nothing about how it is reached.
//
// What holds the graph to reaching them, test by test — there is no
// `ShellGraphTests` and there never was, which a review caught and which is a
// named defect class on this project: a comment citing a test that does not
// exist is worse than no comment, because the next reader trusts it and stops
// looking.
//
//  - the picker opens from either shell, from the pill or from a forget:
//    `ShellModelTests.testThePickerOpensFromEitherShell`;
//  - the trust-again dialog is only raised where a key was proved:
//    `ShellModelTests.testTrustAgainCannotBeRaisedWithNothingToTrust`;
//  - a screen that replaces the whole shell leaves an out-of-band route
//    request pending rather than dropping it:
//    `ShellModelTests.testAScreenThatReplacesTheShellLeavesTheRequestPending`.
//
// The pairing screen's own arrival from `pairingRequired` is part 1's
// `shellScreen`, asserted in `ShellModeTests` and, through a real refusal on
// the wire, in
// `ShellModelTests.testNotNowLastsAsLongAsTheTokenItWasTappedWith`.
//
// Part 3 also waits on two wording decisions (`IOS-ONLY-STRINGS.md`), which
// is the other reason nothing here writes a sentence a person will read: the
// one marked line each of these shows is the placeholder's own, not copy.

/// The connection dialog and the server picker inside it (map §3, §4).
///
/// What it owes part 3: the phone/server switch, the sweep with its progress,
/// the three "nothing found" lines — which are not interchangeable — manual
/// entry, and the trust dialog in front of any new address.
struct ConnectionDialogPlaceholder: View {

    let model: ShellModel

    var body: some View {
        VStack(spacing: 12) {
            Text(ShellStrings.connectionTitle)
                .font(.headline)
            // The one thing already decided and worth showing: whether the
            // server side can be picked right now, and if not, why
            // (`ServerChoice.reason`).
            VStack(spacing: 4) {
                Text(model.serverChoice.title)
                Text(model.serverChoice.reason)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
            }
            Text("the picker is part 3")
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
            Button(ShellStrings.close) { model.dismissSheet() }
        }
        .padding(24)
    }
}

/// `TrustAgainDialog` (map §4.5): the yes that pins a reinstalled Domovoi's
/// new key, with both fingerprints in view.
///
/// It can only be reached where a key was **proved**
/// (``ShellModel/askTrustAgain()``), which is why the two fingerprints below
/// can be shown unconditionally.
struct TrustAgainPlaceholder: View {

    let model: ShellModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(trustAgainTitle).font(.headline)
            if let changed = model.trustAgain {
                Text(trustAgainText(pinned: changed.expected, now: changed.seen))
                    .font(.footnote)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("the yes itself is part 3")
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
            Button(trustAgainNo) { model.dismissTrustAgain() }
        }
        .padding(24)
    }
}

/// The pairing screen (map §5): the explanation, the field, the hint, and
/// `not now`.
///
/// **`not now` works, and that is deliberate.** It is the one way out of this
/// state other than pasting a token, on both platforms, and what it does —
/// ``ShellModel/dismissPairing()`` — is part 2's because its lifetime is:
/// Android keys it on the token and does not save it across process death, so
/// the screen comes back.
///
/// Android's own back button does **nothing** in this state (map §11.1: the
/// screen registers no `BackHandler`, so a press exits the app). Nothing here
/// copies that: an iPhone has no back button, this screen is drawn in place
/// of the whole stack so there is nothing to swipe back to, and whether back
/// should do what `not now` does is Kamron's open question — deliberately not
/// changed during the v1 soak.
struct PairingPlaceholder: View {

    let model: ShellModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(ShellStrings.pairTitle).font(.headline)
                Text(ShellStrings.pairExplanation)
                    .font(.footnote)
                    .fixedSize(horizontal: false, vertical: true)
                PairingFieldPlaceholder(label: ShellStrings.pair)
                Text(ShellStrings.pairHint)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Button(ShellStrings.notNow) { model.dismissPairing() }
                    .font(.footnote)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// Where the token field goes, on the pairing screen and on the
/// token-trouble card — Android's `CheckedPairingField`, one component used
/// by both.
///
/// Part 1 ported its rules, hints and check messages
/// (``TokenPasteField``); what it still needs is the check call and the vault
/// write, which are part 3's. It draws as a disabled row rather than as a
/// field, because a field that accepts a paste and loses it is worse than no
/// field.
struct PairingFieldPlaceholder: View {

    let label: String

    var body: some View {
        HStack {
            Text(ShellStrings.householdTokenField)
                .foregroundStyle(Color.fgMuted)
            Spacer()
            Text(label)
                .foregroundStyle(Color.fgMuted)
        }
        .font(.footnote)
        .padding(10)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
        .accessibilityLabel("\(ShellStrings.householdTokenField) — part 3")
    }
}
