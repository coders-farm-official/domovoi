import DomovoiKit
import SwiftUI

/// Home's **first-run hint** — a port of `HomeFirstRun`
/// (`HomeSections.kt:986`), map §3.8.
///
/// `try saying`, then the phrase **in monospace wrapped in curly double
/// quotes**, then `start here` to the Manual. The quotes are U+201C and
/// U+201D with no spaces inside them, and the monospace is part of the design
/// rather than a style choice: the phrase is a thing to say out loud word for
/// word, so it is set apart from the prose around it.
///
/// Whether the card appears at all is ``homeFirstRun(rooms:timers:activeTimers:events:)``'s
/// — all four conditions, and two of them need their read to have answered
/// **with data**, so an unpaired phone whose rooms and calendar reads were
/// refused is not a first run. ``HomeView`` asks ``HomeModel/isFirstRun(active:)``.
///
/// ### The phrase is always the fallback on iOS, and that is a named gap
///
/// Android reads the manual once the house is known to be empty and uses its
/// Start here phrase, falling back to `set a timer for 10 minutes`. **There is
/// no manual repository in `DomovoiKit`** — the Manual screen is a later
/// batch's — so ``HomeHint`` is constructed with a nil phrase and
/// ``hintPhrase(_:)`` gives the fallback.
///
/// That is honest rather than lossy: the fallback was *chosen* rather than
/// picked, because a timer works on a fresh install by the fast path with no
/// internet and no plugins, so the first thing the card ever suggests is the
/// first thing that will work. And §5's "the card waits for the manual read
/// rather than flashing the fallback" has nothing to wait for here, which is
/// the same shape as part 1's `countsReady: true` on a screen with no rooms
/// read: a skeleton is a promise, and a promise nothing can keep is worse
/// than the plain answer. In `I1D3B-REPORT.md` as left undone, with the one
/// line it will take when the Manual lands.
struct HomeFirstRunView: View {

    let hint: HomeHint

    let onOpenManual: () -> Void

    var body: some View {
        HomeCard(title: HomeFirstRunStrings.trySaying) {
            VStack(alignment: .leading, spacing: 10) {
                Text(HomeFirstRunStrings.quoted(hint.text))
                    .font(.body.monospaced())
                    // A phrase a person is meant to say aloud must be
                    // readable in full at every type size, so it wraps.
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                    .textSelection(.enabled)
                HomeLink(HomeFirstRunStrings.startHere, action: onOpenManual)
            }
        }
    }
}

#Preview("first run, the fallback phrase") {
    HomeFirstRunView(hint: HomeHint(phrase: nil), onOpenManual: {})
        .padding(16)
}
