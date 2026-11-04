import DomovoiKit
import SwiftUI

/// The top bar both shells have: the brand (or breadcrumb), the server pill
/// with its status, and whatever trailing control that shell has.
///
/// Every word comes from ``ShellStrings`` or from ``ShellPill``; nothing is
/// written here. The **two leading spaces** in `  available`, `  offline`,
/// `  live` and `  domovoi / local media` are the chrome's spacing and are
/// part of the strings, which is why there is no `.padding` standing in for
/// them.
/// ### Why this bar reflows instead of staying one line
///
/// Apple's own `performAccessibilityAudit` failed the first screen with
/// **"Text of this element may be clipped at larger Dynamic Type sizes"**,
/// naming two elements: `  domovoi / local media` and `connect`. Both were
/// `.lineLimit(1)` inside this `HStack`, and at an accessibility type size a
/// single line of that text does not fit the width — so it was cut, not
/// shrunk.
///
/// `minimumScaleFactor` is **not** the fix: it answers a clipping audit by
/// making the text too small to read, which the I0 gate already rejected once
/// for the same reason. Instead the bar changes shape. Below the accessibility
/// sizes it is the one-line bar it was. At `.isAccessibilitySize` the brand
/// and the pill stack, each wrapping to as many lines as it needs
/// (`fixedSize(horizontal: false, vertical: true)` is what stops a parent
/// from squeezing them flat again), and the bar simply gets taller.
///
/// Measured, not reasoned: the audit named the two elements, and it passes
/// with this. See `design-notes/ios/I1C-SIMULATOR-PASS.md`.
struct ShellTopBar<Trailing: View>: View {

    let leading: String
    let pill: ShellPill?
    let onPill: () -> Void
    @ViewBuilder let trailing: () -> Trailing

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if typeSize.isAccessibilitySize {
            // **One line each, measured.** This had the pill, `unverified`
            // and the status word sharing a row, which answered the clipping
            // audit and produced something worse: a Simulator tree at
            // `AccessibilityXXXL` measured the pill's address at **62.7 points
            // wide and 306 tall** — wrapped to roughly a character a line —
            // and the whole bar took **444 of 852 points** before Home's
            // content began. Three labels dividing 361 points cannot each
            // have a readable width, so they stop dividing it.
            //
            // The trailing control stays beside the brand, top right, where
            // it is on every other screen; it is an icon and a word, not a
            // paragraph, so it is the one thing here that does fit a row.
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    brand
                    Spacer(minLength: 8)
                    trailing()
                }
                pillAndStatus
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
        } else {
            oneLine
        }
    }

    /// The brand or breadcrumb. It wraps rather than clips; the two leading
    /// spaces are the string's, as the note on this type says.
    private var brand: some View {
        Text(leading)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var oneLine: some View {
        HStack(spacing: 0) {
            // No `.lineLimit(1)`. It does not keep this to one line — the
            // text fits on one line when it fits — it only decides what
            // happens when it does not, and truncating is the answer this
            // project has already rejected twice. Apple's audit named it:
            // "Text of this element may be clipped at larger Dynamic Type
            // sizes", twice over, on a screen whose two `.lineLimit(1)`
            // labels were this one and the pill's.
            Text(leading)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            pillAndStatus
            trailing()
                .padding(.leading, 6)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
    }

    /// The server pill and its status word, which both layouts share so there
    /// is one copy of what the pill is rather than two that can drift.
    ///
    /// The labels wrap at every size, for the reason on this type: the
    /// audit's complaint was a cut line, and a wrapped line is not cut. The
    /// comment inside says why that is not the same as the bar growing taller
    /// at ordinary sizes.
    @ViewBuilder private var pillAndStatus: some View {
        if let pill {
            // These used to keep `.lineLimit(1)` below the accessibility
            // sizes and wrap above them, on the reasoning that the bar is one
            // line at ordinary sizes. The reasoning was sound and the
            // mechanism was not: a label with no line limit still draws on
            // one line whenever it fits, so the limit was never what kept the
            // bar to one line — it only chose truncation over wrapping for
            // the sizes in between, which Apple's audit flags as "Text of
            // this element may be clipped at larger Dynamic Type sizes" and
            // which the project has rejected twice on its own terms. So they
            // wrap at every size now, and the bar is still one line whenever
            // one line fits.
            Button(action: onPill) {
                HStack(spacing: 6) {
                    Image(systemName: "server.rack")
                        .font(.caption2)
                    Text(pill.label)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(.quaternary, in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(pill.accessibilityLabel)
            // **Outside the capsule, measured.** This sat inside it, and
            // `Color.warnInk` is a 5.73:1 ink — on **white**. The capsule is
            // `.quaternary`, which a Simulator screenshot measures at
            // `#DDDDDE`, and the same ink on that grey is **4.21:1**: under
            // the 4.5:1 body text needs, and exactly Apple's "Contrast is not
            // high enough for element unless font size is larger".
            //
            // The ink is not darkened to fix it, because it is not ours to
            // change: `--warn-ink` is `web/static/colors_and_type.css`'s and
            // the stylesheet is the spec. What is ours is the capsule, which
            // is an iOS invention with no counterpart on the web — so the
            // word moves off it, onto the canvas the ink was measured
            // against, beside `live`, which has always sat there.
            //
            // **Train R001 flagged it again, and the measurement says it is
            // still not this.** Four `Contrast failed` against Home, naming
            // `domovoi / home`, `127.0.0.1:6601`, `unverified` and `  live` —
            // the whole bar, including two labels that are plain primary ink
            // on white. A bar where *every* leaf fails and none of them share
            // a colour is not a colour result. The run's own evidence says
            // what it was: a notification banner at
            // `{{8.0, 56.0}, {377.0, 78.7}}`
            // (`overnight-trains/R001/BaseAudit/att/testTheStatesTheAuditHadNeverSeen/`
            // `Debug_description_for_NotificationShortLookView…`), and all
            // four elements sit at `y ≈ 69`, inside it.
            // `performAccessibilityAudit` samples pixels, so it measured the
            // banner's material. Darkening `warnInk` to quiet that would be
            // editing `web/static/colors_and_type.css`'s spec to answer a
            // measurement of SpringBoard. `StubHouseUITests.auditIssues` is
            // what changed instead: it retakes an audit taken through a
            // banner.
            if pill.unverified {
                Text(ShellStrings.unverified)
                    .font(.caption2)
                    .foregroundStyle(Color.warnInk)
                    // The gap that separates it from the capsule on one line.
                    // Stacked there is nothing to its left, so the indent
                    // would read as an indent.
                    .padding(.leading, typeSize.isAccessibilitySize ? 0 : 6)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let status = pill.status {
                Text(status)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// The local shell's banner: one of the three faces of "this server can't be
/// used", with the yes beside it where there is one to offer.
///
/// Which text, and whether the button is there at all, are
/// ``localBanner(for:canTrustAgain:)`` and ``ShellChrome/offersTrustAgain``.
struct ShellBanner: View {

    let text: String
    let offersTrustAgain: Bool
    let onTrustAgain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(text)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if offersTrustAgain {
                Button(trustAgainButton, action: onTrustAgain)
                    .font(.footnote)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
    }
}

/// The phone bar: this phone's tabs, left to right.
///
/// Which tabs (``ShellModel/phoneBar``) and which one is lit
/// (``PhoneBar/selects(tab:route:bar:)``) are both decided in the package. A
/// tap is ``ShellModel/navigate(_:)``, so a tab that is really a Settings
/// section opens Settings on it — which is how Rooms stays off the bar and
/// still reachable.
struct PhoneBarView: View {

    let model: ShellModel

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Four equal columns at ordinary sizes; a horizontally scrolling row of
    /// natural-width tabs at the accessibility ones.
    ///
    /// Apple's audit failed Home with **"Dynamic Type font sizes are
    /// partially unsupported"**, and a Simulator at
    /// `AccessibilityXXXL` showed what it meant: `mus…` and `vide…`. Four
    /// equal columns of a 393-point screen is 98 points each, and `music` at
    /// that type size does not fit in 98 points, so `.lineLimit(1)` cut it.
    ///
    /// Text that shrinks to an ellipsis is not text that scaled. The two
    /// obvious answers are both wrong for a household: `minimumScaleFactor`
    /// makes the word smaller for the person who asked for bigger words —
    /// rejected once already at the I0 gate — and dropping to icons only
    /// takes the word away from the sighted person who turned it up, while
    /// leaving it for VoiceOver.
    ///
    /// So the bar scrolls instead. Every tab keeps its whole word at its
    /// natural width and its 44-point target, and a person who needs four
    /// large tabs swipes for the fourth.
    var body: some View {
        Group {
            if typeSize.isAccessibilitySize {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 0) { tabs(equalWidth: false) }
                }
            } else {
                HStack(alignment: .top, spacing: 0) { tabs(equalWidth: true) }
            }
        }
        .padding(.top, 4)
        // **Opaque, with its own edge.** This was `.background(.bar)`, the
        // translucent tab-bar material, and the one accessibility finding
        // nobody could place for five runs was Apple failing the `music`
        // caption on contrast — on Home and on Settings, the same element
        // both times, while `videos` and `chat` beside it in the same colour
        // and the same font passed.
        //
        // `fgMuted` measures 6.98:1 against the bar in a screenshot, so the
        // colour was never the answer, and nothing about one word of four
        // explains it. What is true of all four is that they were drawn on a
        // material whose colour is whatever is behind it, which is not
        // something a contrast check can read. Now the bar is a surface, the
        // captions sit on a known colour, and `Divider` gives the edge the
        // material used to imply.
        .background(Color(.secondarySystemBackground))
        .overlay(alignment: .top) { Divider() }
    }

    @ViewBuilder private func tabs(equalWidth: Bool) -> some View {
        ForEach(model.phoneBar, id: \.self) { route in
            let selected = model.barSelects(route)
            Button {
                model.navigate(route)
            } label: {
                VStack(spacing: 2) {
                    Image(systemName: route.systemImage)
                        .font(.body)
                    // No `.lineLimit`. `music` at an accessibility type size
                    // does not fit 98 points, and the horizontal scroll above
                    // is what makes room for it; a limit here would cut the
                    // word instead, which is the "Text clipped" the bar's own
                    // labels were just fixed for.
                    Text(route.label.lowercased())
                        .font(.caption2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, equalWidth ? 0 : 14)
                .frame(maxWidth: equalWidth ? .infinity : nil, minHeight: 44)
                // The frame is the space; `contentShape` is the target.
                // Without it SwiftUI hit-tests the drawn icon and caption
                // only, and a Simulator measured these at
                // `{{34.5, 779.7}, {29.3, 33.8}}` — under 44 in both
                // directions, on the app's primary navigation. Same
                // defect as the header's live dot, found the same way.
                .contentShape(Rectangle())
                .foregroundStyle(selected ? Color.brandInk : Color.fgMuted)
            }
            .buttonStyle(.plain)
            // ### The contrast finding this bar used to carry is fixed
            //
            // Four explanations were wrong about it before the audit's
            // element was legible: the colours (6.98:1 and 5.31:1, measured
            // off a screenshot), "a button that is mostly background" (the
            // system `TabBar` in ``LocalShellView`` is emptier and passes),
            // the pill's capsule (that shell has it too), and a synthetic
            // `.accessibilityLabel` (removing it changed the tree and not one
            // byte of the audit).
            //
            // It was `.background(.bar)`: Apple failed the `music` caption,
            // one word of four in the same colour and the same font, because
            // a translucent material's colour is whatever is behind it. The
            // bar's background is a surface now and both screens that carried
            // the finding are clean of it. `StubHouseUITests` asserts the
            // counts, so this comment is the record of a fix rather than of a
            // limit — if a contrast finding ever returns here, that is where
            // it will say so.
            .accessibilityLabel(route.label)
            .accessibilityAddTraits(selected ? [.isButton, .isSelected] : [.isButton])
        }
    }
}

/// The server pill where there is room for a glyph and not for an address:
/// the navigation toolbar, at the accessibility type sizes, where
/// ``ShellScrollAwayChrome`` has taken ``ShellTopBar`` off the screen.
///
/// It does the one thing the bar's pill does — open the connection sheet —
/// and it says everything the bar said: the address, `unverified` and the
/// status word all reach VoiceOver through
/// ``ShellPill/toolbarAccessibilityLabel``, because a toolbar item that drew
/// them would be back to wrapping an address a character at a line, which is
/// the measurement the bar's own reflow exists for.
///
/// **The warning is drawn, not only spoken.** `unverified` is the one of the
/// three that is a warning rather than a status, so it keeps a glyph.
///
/// ### The glyph's surface is a reading, not a claim
///
/// This said the large-title navigation bar's background is the system
/// background — "the surface that ink was measured against, unlike the
/// `.quaternary` capsule the word had to be moved off of". Review 2 was right
/// that it is an argument about UIKit's own drawing with nothing behind it: as
/// soon as the large title **collapses**, which is the whole point of answer
/// 29 and what the cases make it do, UIKit draws the inline bar as a
/// translucent material over the page scrolling under it. That is the same
/// class of surface that measured ``Color/warnInk`` at **4.21:1** on the
/// `.quaternary` capsule (`ShellChromeViews.swift:134-146`), and
/// `--warn-ink` is not ours to darken — the stylesheet is the spec.
///
/// So it is measured instead: `assertTheHeaderScrollsAway` runs Apple's audit
/// at AX5 **after** the swipe that collapses the title, and fails on any
/// finding naming the breadcrumb or this pill (the rest belongs to whoever
/// owns the screen). If the glyph comes back flagged, the fix is the shape the
/// capsule got: the warning leaves the bar's material for the canvas — a glyph
/// in the page's own chrome, or `unverified` spoken by
/// ``ShellPill/toolbarAccessibilityLabel`` alone, which it already is.
struct ShellPillToolbarButton: View {

    let pill: ShellPill
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 4) {
                Image(systemName: "server.rack")
                if pill.unverified {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(Color.warnInk)
                }
            }
            .font(.footnote)
            // The frame is the space and `contentShape` is the target: the
            // same pair the phone bar's tabs and the header's live dot both
            // needed after Apple's audit measured them under 44 points.
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(pill.toolbarAccessibilityLabel)
    }
}

/// At the accessibility type sizes, the shell's header is the navigation
/// bar's own **large title** rather than a band above the stack — so it
/// scrolls away with the page (owner answer 29: "shell chrome (header and
/// plugin band) scrolls away at accessibility sizes, app-wide").
///
/// ### The measurement
///
/// On a 393 x 852 phone at AX5, train S001 (`testImages_AtTheLargestTypeSize`)
/// measured the shell taking 300 points for ``ShellTopBar``, 44 for the
/// navigation bar, 256 for the plugin band and 146 for the phone bar — a
/// **102-point** window over 1,626 points of content. It is usable, and it is
/// a straw to read a page through.
///
/// ### Why Apple's mechanism and not ours
///
/// A large navigation title is already the thing that scrolls away: UIKit
/// collapses it into the inline bar as the page's own scroll view moves, at no
/// cost to any screen's code. The alternative — inserting the header into each
/// page's scroll view — is 30-odd files, and the one modifier that looked like
/// a shortcut, `safeAreaInset`, has produced two occlusion bugs in
/// ``WorkspaceView`` already (both written up at `WorkspaceView.swift:52-105`:
/// a bar drawn over the navigation bar's back button, and a `back to …` button
/// drawn under the phone bar).
///
/// ### What changes, and what does not
///
/// Below the accessibility sizes this modifier does **nothing**: there is no
/// title, no toolbar item, the navigation bar collapses to nothing as it
/// always did, and ``ShellTopBar``'s existing reflow is untouched
/// (`ShellChromeViews.swift:12-26`). At AX5 the shell draws no `ShellTopBar`
/// at all, and this carries both halves of it — the breadcrumb as the title,
/// the pill as a toolbar item.
///
/// ### The one thing to measure next
///
/// A `UINavigationBar` large title is a single line, and a long breadcrumb at
/// AX5 does not fit one: UIKit shrinks it toward the inline size rather than
/// wrapping. That is Apple's own behaviour for Apple's own chrome, and it is
/// the reason the breadcrumb is handed in whole rather than being padded out —
/// but the house rule against shrinking text means it is worth a reading
/// rather than an assumption, so `testShell_*` measures the title's height at
/// AX5 and the report says what it found. If it reads as shrunk, the answer is
/// the route's label alone ("music") in place of the breadcrumb
/// ("domovoi / music"), which is the same information one word shorter.
struct ShellScrollAwayChrome: ViewModifier {

    /// The breadcrumb ``ShellTopBar`` would have drawn, as handed to that bar:
    /// the chrome's leading spaces are trimmed here, because a screen reader
    /// reads a title and not a layout.
    let crumb: String

    let pill: ShellPill?
    let onPill: () -> Void

    /// False only for ``AuditStrip/topBar``, the diagnostic variant whose job
    /// is the shell **without its header**. Below the accessibility sizes that
    /// variant drops ``ShellTopBar``; above them the header is this title and
    /// this toolbar item, so without this the variant was identical to the
    /// control at AX5 and the bisect lost it (review 1, N5).
    var drawn = true

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Both halves of answer 29 are one rule in ``ShellHeaderFold``, read
    /// here and in ``WorkspaceView/stack``: review 2's sabotage took the
    /// branch off this body and the size gate off that `if` together, and no
    /// check that runs on Windows read either. Both bodies are read as source
    /// now, by `ShellFoldViewBodyTests` — a nil title here is "the header is
    /// not in the bar", which this body has to say to the bar **out loud**
    /// rather than by falling silent (see ``body(content:)``).
    ///
    /// The rule, asked once per body. A computed property rather than a `let`
    /// inside the `ViewBuilder`, to match the rest of this file.
    private var fold: ShellHeaderFold {
        ShellHeaderFold.forHeader(
            drawn: drawn,
            isAccessibilitySize: typeSize.isAccessibilitySize
        )
    }

    /// ### The bar is always told, and `content` never changes branches
    ///
    /// **A modifier that goes away does not undo itself.** `.navigationTitle`
    /// and `.navigationBarTitleDisplayMode` are SwiftUI *preferences*: while a
    /// branch applies them, SwiftUI writes `navigationItem.title` and
    /// `largeTitleDisplayMode` into the UIKit bar underneath. Stop applying
    /// them and SwiftUI stops writing — it does not write the default back.
    /// UIKit keeps the last pair it was given.
    ///
    /// Train I003 photographed what that costs. `performAccessibilityAudit`
    /// overrides the content-size trait while it looks for clipping, so
    /// `typeSize.isAccessibilitySize` turns true inside the audit, this body
    /// takes its title branch, and the bar becomes a 96-point large-title bar.
    /// The audit ends, the trait reverts, the branch goes — and the bar stays
    /// 96 points with a title label squeezed to 7.33 x 40.67 at y=141, drawing
    /// nothing:
    ///
    ///  * `att/testRooms_Audit/` and `att/testAiSettings_TheChatSectionAudits/`
    ///    attached their trees **before** the audit: `NavigationBar … {393.0,
    ///    44.0}`, no such element.
    ///  * `att/testCalendar_AuditHasNothingNew/calendar-17-audit-agenda-tree…`
    ///    and `att/testPodcasts_…/podcasts-80-grid-audited-tree…` attached
    ///    theirs **after**: `NavigationBar … {393.0, 96.0}` with
    ///    `StaticText … {{16.0, 141.0}, {7.3, 40.7}}` inside it, and the page
    ///    pushed from 137.3 down to 189.3.
    ///  * `calendar-17-audit-agenda….png` is the picture of it: `Back`, then
    ///    an empty 52-point band, then the page. A blank row no one can use,
    ///    on about twenty screens, until the app is relaunched.
    ///
    /// Nothing about that is the audit's: a person who changes the text size
    /// in Settings and comes back drives the same trait change. So with no
    /// title this **writes the opposite** — an empty title and `.inline` —
    /// which is the one thing that puts `navigationItem` back. An empty title
    /// at `.inline` reserves nothing (the 44-point bar below the
    /// accessibility sizes is the bar that was already there); an empty title
    /// at `.large` would reserve the row on every screen, and that is the
    /// alternative N3 rejected — it is the display mode, not the empty
    /// string, that was the problem with it.
    ///
    /// ### Why the values flip and the view does not
    ///
    /// Saying both of those things used to mean two branches —
    /// `if let title { content.navigationTitle(title)… } else { content }` —
    /// and that shape cost far more than the stale bar it fixed. The two arms
    /// of a `ViewBuilder` `if` are a `_ConditionalContent`, so `content` had a
    /// different **structural identity** in each: flipping the branch does not
    /// re-apply a modifier, it discards the subtree and builds a new one.
    /// Every `@State` under the `NavigationStack` goes with it, and every
    /// presented sheet and full-screen cover is dismissed.
    ///
    /// `performAccessibilityAudit` overrides the content-size trait on every
    /// call, so it crossed that boundary twice per audit and tore down the
    /// screen it was measuring. Train I003 paid for it in four areas at once:
    /// `testAssistant_Audit` lost the open conversation (and, through the
    /// re-run wipe seam, the whole phone chat store), `testChatMore_Audits`
    /// popped back to the conversation list mid-audit and sampled four
    /// phantom `Contrast failed` findings off the arriving list,
    /// `testCalendar_*` reverted to agenda with its editor sheet gone, and
    /// rooms' kitchen card and the manual's map pill "did not open" because
    /// what opened them was rebuilt underneath. Audits are only where it was
    /// *caught*: a person who changes their text size loses their typed
    /// search and their open conversation the same way.
    ///
    /// So the modifiers are applied **unconditionally** and their arguments
    /// carry the fold's answer instead. `content` appears once, there is no
    /// `_ConditionalContent` around it, and the only `if` left is inside the
    /// `ToolbarItem`, around the pill alone.
    ///
    /// The same shape is still live one level out, in
    /// ``LocalShellView/tab(_:)``, which wraps its screen in a
    /// `NavigationStack` only at the accessibility sizes — so that screen's
    /// state does not survive the boundary either. It is left alone here
    /// deliberately: removing that branch means a `NavigationStack`, and so a
    /// navigation bar, on every tab of the unpaired shell at every size, which
    /// `ShellFoldViewBodyTests.testTheLocalShellWrapsATabOnlyWhenTheHeaderGoesInside`
    /// pins on purpose. It is recorded in this batch's integration notes
    /// rather than fixed in the same change.
    func body(content: Content) -> some View {
        // **One question, asked once** — and `content` appears once, in one
        // chain, on purpose. See the note above: the two values below flip
        // with the fold, but the view they are applied to never changes
        // branches, so nothing under it is rebuilt when they do.
        let title = fold.navigationTitle(crumb: crumb)
        // `Text(verbatim:)` and not a `String`: the `LocalizedStringKey`
        // overload would send the crumb — and, with no title, an empty key —
        // through the string table on every screen in the app, and a
        // breadcrumb is a word the app already composed, not one to look up.
        return content
            .navigationTitle(Text(verbatim: title ?? ""))
            .navigationBarTitleDisplayMode(title == nil ? .inline : .large)
            // **The back button's word, at the accessibility sizes only** —
            // train I004's one unattributed finding, and a defect in its own
            // right. A value, not a branch: see the note above about what a
            // `ViewBuilder` `if` around `content` costs.
            //
            // `UINavigationBar` caps the font of its back-button title: at
            // AX5 `home` is drawn at roughly the ordinary 17 points beside a
            // page whose body text is near 50, which is both unreadable at
            // the size the person asked for and what Apple's audit reports as
            // `Text clipped`. It cannot be styled up — the bar owns that
            // font — so the word comes off and the chevron, which VoiceOver
            // still announces as `Back`, stays. Android's top bar is a bare
            // arrow (`AppShell.kt:1021`), so this is also the faithful shape.
            //
            // **So the back button's name changes with the type size, and a
            // reader diffing two trees from one train will see it.** In a bare
            // run it is `Back` (the chevron's own name, no word drawn); in any
            // run where a case has already called
            // `performAccessibilityAudit`, the audit leaves
            // `isAccessibilitySize` true, `isDrawn` answers yes from then on,
            // and the button is named `home` — the root's `navigationTitle`,
            // written two lines above. Both are this rule working as built,
            // not two shells; nobody should have to re-derive that from a
            // tree diff.
            //
            // ### How that finding was attributed, since the audit could not
            //
            // `performAccessibilityAudit` returned one `Text clipped` whose
            // element would not resolve (`| element: nil`) on every pushed
            // screen at AX5 and on no root. The back button is the only thing
            // a pushed screen has here that a root does not, and the UILabel
            // inside a `UIBarButtonItem` is not an accessibility element of
            // its own — which is exactly why the audit could name it and
            // XCUITest could not. Train I004, five readings, one train:
            //
            //  * pushed at AX5, one `Text clipped` each —
            //    `testShell_TheHeaderScrollsAwayOnAPushedScreen`
            //    (`shell-settings-ax5-audited`, floor 3 rest 1),
            //    `testTheAX5WordingIsTheSameBarFindings` (`ax5-none`, Music),
            //    `testWhetherTheEightAreSizesRatherThanElements`
            //    (`sizes-AX5`, Phones & browsers: `dynamic=3 total=4`);
            //  * a root at AX5, none — `testShell_TheHeaderScrollsAwayOnHome`
            //    (`shell-home-ax5-audited`, floor 6 rest 1, and that one rest
            //    is a named contrast finding on Home's own card) and
            //    `testShell_TheHeaderScrollsAwayOnTheLocalShell`
            //    (`shell-local-ax5-audited`, floor 0 rest 0);
            //  * the same pushed screen at `S` and at `large`, none —
            //    `SIZES SUMMARY S=8 large=8 AX5=3`, where only the AX5 run
            //    carries a fourth, clipped finding.
            //
            // Roots have the large title and the toolbar pill too, so both of
            // those are eliminated by the same readings.
            //
            // **`toolbarRole` and not `navigationBarBackButtonDisplayMode`**:
            // the latter is `UINavigationItem`'s and SwiftUI does not surface
            // it (`appcheck`: "value of type 'some View' has no member"). The
            // documented SwiftUI spelling of "a back button with no label" is
            // `ToolbarRole.editor`. Its other effect on iPhone is that an
            // *inline* title sits leading rather than centred, which is where
            // the large title above it already is — so the bar reads the same
            // through the collapse instead of the word jumping to the middle.
            // The toolbar pill is unaffected: it is placed `.topBarTrailing`
            // explicitly, and the role only moves items whose placement is
            // automatic.
            .toolbarRole(
                ShellBackButtonWord.isDrawn(
                    isAccessibilitySize: typeSize.isAccessibilitySize
                ) ? .automatic : .editor
            )
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    // **`title != nil` is load-bearing here, and was not
                    // before.** While this body branched, the pill sat inside
                    // the title branch and its only remaining question was
                    // whether there was a pill; a second clause there was
                    // dead and read as a guard that did something (review 3,
                    // N2). With the branch gone the clause is the only thing
                    // left saying "the fold put the header in the bar", and
                    // without it this draws a pill beside `ShellTopBar`'s own
                    // on every screen at every size. `ShellFoldViewBodyTests`
                    // was widened to require it rather than ban it, and says
                    // so where it reads this gate.
                    if title != nil, let pill {
                        ShellPillToolbarButton(pill: pill, onTap: onPill)
                    }
                }
            }
    }
}

extension View {

    /// ``ShellScrollAwayChrome``, applied where a page is the content of a
    /// `NavigationStack`: both shells' roots and the workspace's destinations.
    func shellScrollAwayChrome(
        crumb: String,
        pill: ShellPill?,
        onPill: @escaping () -> Void,
        drawn: Bool = true
    ) -> some View {
        modifier(
            ShellScrollAwayChrome(crumb: crumb, pill: pill, onPill: onPill, drawn: drawn)
        )
    }
}
