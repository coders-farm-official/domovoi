import XCTest
@testable import DomovoiKit

/// Answer 29 **as the three view bodies actually spell it**, read out of the
/// app target's source text on a machine with no Simulator.
///
/// ### Why a test reads source, of all things
///
/// Review 2 sabotaged answer 29 in two *view bodies* and left the rule alone:
/// `WorkspaceView.stack`'s `if` became `if AuditStrip.active != .topBar`, so
/// the bar was drawn above the stack at AX5 again, and
/// `ShellScrollAwayChrome.body` asked
/// `ShellHeaderFold.noHeaderAtAll.navigationTitle(crumb:)`, so its branch was
/// never taken and no large title replaced the bar. **Nothing in the
/// repository caught it, twice.** The first fix pass moved the decision into
/// ``ShellHeaderFold`` and `ShellFoldTests` reads it, which catches a sabotage
/// of the *rule* — the verifier re-applied the recorded sabotage with the rule
/// untouched and `ShellFoldTests` stayed 18 of 18 green. A rule with a test
/// and two call sites with none is not answer 29 having a reading.
///
/// What is available on this machine: `pkgtest.sh` builds the package and runs
/// `swift test`; `appcheck.sh`, the app-target type-checker, fails at its
/// first step on this branch **and on the base branch** (`HintInk.swift:4:
/// cannot load underlying module for 'UIKit'`), so it is a harness defect and
/// not something this branch can repair; and the four `testShell_*` cases that
/// read the real screens need a train and a Simulator. So this file reads the
/// source. `AlertsStringsTests` and `ShellStringsTests` already read Kotlin
/// out of the repository through `#filePath` for the same reason — a reading a
/// Windows agent can get a verdict on (NIGHT-RULES §5) beats an argument in a
/// doc comment.
///
/// ### What this can and cannot catch
///
/// It catches: the gate on `ShellTopBar` being changed to anything that is not
/// the rule, in either shell; the rule being asked with a hard-coded case
/// instead of the value; the fold being asked with a constant instead of the
/// environment's type size; and the `AuditStrip.topBar` variant losing its
/// wiring. Those are the two recorded sabotages and their near neighbours.
///
/// It cannot catch: anything that keeps the shape and changes the *meaning* —
/// a bar moved inside the gate but drawn by another type, a `safeAreaInset`
/// added elsewhere, a modifier applied to the wrong view. It is a shape
/// reading, not a compiler and not a screen. The four `testShell_*` cases
/// remain the behavioural half, and that limit is in
/// `design-notes/ios/batch-2026-11-03/shell/REPORT.md`.
///
/// It is also, deliberately, **brittle about formatting**: it asserts that a
/// particular question is asked in a particular place. A reformatting that
/// fails it is a one-line edit here; a reformatting that *silently* passes it
/// would be the defect.
final class ShellFoldViewBodyTests: XCTestCase {

    // MARK: - the three bodies, as text

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so the
    /// repository root is six directories up — the hop `AlertsStringsTests`
    /// makes to read the Kotlin.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    /// **Spelt `ShellFoldViewBodyTests.` and not `Self.` at the three call
    /// sites below.** `XCTestCase` is a class, so `Self` is covariant and
    /// Swift refuses it in a *stored* property's initializer: `error:
    /// covariant 'Self' type cannot be referenced from a stored property
    /// initializer`. Inside a function body, as here, `Self` is fine.
    private static func source(_ relativePath: String) -> String {
        let url = Self.repositoryRoot.appendingPathComponent(relativePath)
        return (try? String(contentsOf: url, encoding: .utf8)) ?? ""
    }

    /// `ios/Domovoi/Shell/WorkspaceView.swift`'s code, comments and string
    /// literals removed.
    private static let workspace = ShellFoldViewBodyTests.code(
        in: ShellFoldViewBodyTests.source("ios/Domovoi/Shell/WorkspaceView.swift")
    )

    /// `ios/Domovoi/Shell/ShellChromeViews.swift`'s code, likewise.
    private static let chrome = ShellFoldViewBodyTests.code(
        in: ShellFoldViewBodyTests.source("ios/Domovoi/Shell/ShellChromeViews.swift")
    )

    /// `ios/Domovoi/Shell/LocalShellView.swift`'s code, likewise.
    private static let localShell = ShellFoldViewBodyTests.code(
        in: ShellFoldViewBodyTests.source("ios/Domovoi/Shell/LocalShellView.swift")
    )

    /// The three, by the name a failure message should print.
    private static var all: [(String, String)] {
        [
            ("WorkspaceView.swift", Self.workspace),
            ("ShellChromeViews.swift", Self.chrome),
            ("LocalShellView.swift", Self.localShell),
        ]
    }

    /// **The guard that stops every other case in this file passing
    /// vacuously.** A renamed or moved file would make the source an empty
    /// string, every `contains` false and every ban satisfied; this is the
    /// case that goes red instead.
    func testTheThreeViewBodiesAreWhereThisFileLooksForThem() {
        for (name, text) in Self.all {
            XCTAssertFalse(
                text.isEmpty,
                "\(name) read as empty from \(Self.repositoryRoot.path) — the "
                    + "file moved or was renamed, and until this is fixed every "
                    + "other reading in this file passes on nothing"
            )
        }
        // Each file's own anchor, so that reading the *wrong* file is caught
        // as well as reading none.
        XCTAssertTrue(Self.workspace.contains("struct WorkspaceView"))
        XCTAssertTrue(Self.chrome.contains("struct ShellScrollAwayChrome"))
        XCTAssertTrue(Self.localShell.contains("struct LocalShellView"))
    }

    // MARK: - sabotage C's first half: the gate on the bar

    /// `ShellTopBar` is drawn in the workspace **only** behind
    /// ``ShellHeaderFold/drawsBarAboveTheStack``.
    ///
    /// This is review 2's recorded sabotage, first half, verbatim: the `if`
    /// became `if AuditStrip.active != .topBar`, which draws the bar above the
    /// stack at every size — answer 29 undone, the rule untouched, the package
    /// green.
    func testTheWorkspaceDrawsTheBarOnlyBehindTheRule() {
        assertTheBarIsGatedByTheRule(in: Self.workspace, named: "WorkspaceView.swift")
    }

    /// The same, in the other shell. `LocalShellView` mounts the same bar and
    /// is the shell Apple's audit passed in I1-C, so a sabotage there is a
    /// sabotage of answer 29 on a screen a fresh install lands on.
    func testTheLocalShellDrawsTheBarOnlyBehindTheRule() {
        assertTheBarIsGatedByTheRule(in: Self.localShell, named: "LocalShellView.swift")
    }

    /// One `ShellTopBar(`, and the innermost `if` above it reads the rule.
    private func assertTheBarIsGatedByTheRule(in code: String, named name: String) {
        XCTAssertEqual(
            Self.count(of: "ShellTopBar(", in: code),
            1,
            "\(name) builds `ShellTopBar` \(Self.count(of: "ShellTopBar(", in: code)) "
                + "times. This reading finds the gate above the first one, so a "
                + "second construction is a bar it does not cover"
        )
        guard let gate = Self.innermostGate(before: "ShellTopBar(", in: code) else {
            return XCTFail(
                "no `if` at all above `ShellTopBar(` in \(name): the bar is drawn "
                    + "unconditionally, so at AX5 it is above the `NavigationStack` "
                    + "again — 300 of 852 points that cannot scroll away (train S001)"
            )
        }
        XCTAssertTrue(
            gate.contains("drawsBarAboveTheStack"),
            "the gate above `ShellTopBar(` in \(name) is `\(gate)`, which does not "
                + "read `ShellHeaderFold.drawsBarAboveTheStack`. That is review 2's "
                + "sabotage exactly — it put `AuditStrip.active != .topBar` here, "
                + "so the bar drew above the stack at every size while "
                + "`ShellFoldTests` stayed green on the untouched rule"
        )
    }

    // MARK: - sabotage C's second half: whose answer the title is

    /// No view body hard-codes a ``ShellHeaderFold`` case.
    ///
    /// Review 2's sabotage, second half: `ShellScrollAwayChrome.body` asked
    /// `ShellHeaderFold.noHeaderAtAll.navigationTitle(crumb:)` instead of
    /// `fold.navigationTitle(crumb:)` — a value that always answers nil, so
    /// the branch was never taken, the large title never drew, and the rule
    /// the test reads was never consulted. Any literal case in a view body is
    /// that shape, so all three are banned outright: the bodies ask
    /// ``ShellHeaderFold/forHeader(drawn:isAccessibilitySize:)`` and read the
    /// answer's properties, which is the whole point of the type.
    func testNoViewBodyHardCodesAFoldCase() {
        for (name, code) in Self.all {
            for caseName in ["barAboveTheStack", "titleInsideTheStack", "noHeaderAtAll"] {
                // `.barAboveTheStack` the case, not `drawsBarAboveTheStack`
                // the property: the capital is what tells them apart.
                XCTAssertFalse(
                    code.contains(".\(caseName)"),
                    "\(name) names the fold case `.\(caseName)` in its code. A view "
                        + "body asks `ShellHeaderFold.forHeader(…)` and reads the "
                        + "answer; a literal case is review 2's second sabotage, "
                        + "which pinned this to `.noHeaderAtAll` so the branch was "
                        + "dead and the rule's own test could not see it"
                )
            }
            for occurrence in Self.occurrences(of: "ShellHeaderFold.", in: code) {
                XCTAssertTrue(
                    occurrence.hasPrefix("ShellHeaderFold.forHeader("),
                    "\(name) uses `ShellHeaderFold` as `\(occurrence)`. The only "
                        + "thing a view body may say to this type is "
                        + "`forHeader(drawn:isAccessibilitySize:)`"
                )
            }
        }
    }

    /// The title comes from the fold the body computed, by that name.
    ///
    /// `navigationTitle(crumb:)` is ``ShellHeaderFold``'s, so its receiver has
    /// to be the computed value. Together with the ban above this is the pair
    /// that catches the recorded sabotage: the ban stops a literal case and
    /// this stops the call going missing altogether.
    func testTheScrollAwayTitleComesFromTheComputedFold() {
        XCTAssertTrue(
            Self.chrome.contains("fold.navigationTitle(crumb:"),
            "ShellChromeViews.swift does not ask `fold.navigationTitle(crumb:)`. "
                + "That call is the whole of the title half of answer 29: without "
                + "it the breadcrumb is not the stack's large title and there is "
                + "nothing for UIKit to scroll away"
        )
        XCTAssertEqual(
            Self.count(of: "navigationTitle(crumb:", in: Self.chrome),
            1,
            "more than one `navigationTitle(crumb:)` in ShellChromeViews.swift: "
                + "this reading checks the receiver of the only one"
        )
        // The applied modifier is the rule's answer and not a string the body
        // picked: `.navigationTitle(` is applied once, to the bound `title`.
        //
        // **Why this is `Text(verbatim: title` and no longer
        // `.navigationTitle(title)`.** The body used to brace the title in a
        // `ViewBuilder` `if` and hand `content` to both arms, which gave it a
        // new structural identity on every flip and tore down every `@State`
        // and every presented sheet under the `NavigationStack` — including
        // under Apple's own audit, which crosses the accessibility-size
        // boundary on each call (train I003: chat, calendar, rooms and
        // documents all lost the screen they were measuring). The modifiers
        // are applied unconditionally now and the fold's answer rides in the
        // *argument*, so there is an empty-title case to spell and the
        // `Text(verbatim:)` overload spells it without sending an empty key
        // through the string table.
        //
        // What this still catches is what it always caught: the applied value
        // has to be the bound `title`. A crumb the body trimmed itself would
        // drop `ShellHeaderFold`'s trimming, which is part of the rule.
        XCTAssertTrue(
            Self.chrome.contains("navigationTitle(Text(verbatim: title"),
            "ShellChromeViews.swift applies `.navigationTitle` to something other "
                + "than the `title` the fold answered with — a crumb the body "
                + "trimmed itself would drop `ShellHeaderFold`'s trimming, which "
                + "is part of the rule (a screen reader reads a title, not a "
                + "layout)"
        )
        // And it is applied to `content` **once**, not once per branch: two
        // occurrences means the `if` came back and with it the teardown above.
        XCTAssertEqual(
            Self.count(of: "navigationTitle(", in: Self.chrome),
            2,
            "`navigationTitle(` appears "
                + "\(Self.count(of: "navigationTitle(", in: Self.chrome)) times in "
                + "ShellChromeViews.swift, not twice — `fold.navigationTitle(crumb:)` "
                + "asking the rule, and the one `.navigationTitle(Text(verbatim:))` "
                + "applying it. A third is `content` back in two arms of a "
                + "`_ConditionalContent`, which rebuilds every screen in the app "
                + "when the text size crosses the accessibility boundary"
        )
    }

    /// The toolbar pill hangs behind **both** whether there is a pill and
    /// whether the fold gave a title.
    ///
    /// Two `switch server` controls on one screen is `ShellTopBar` still being
    /// drawn with the toolbar item under it, which
    /// `testShell_TheHeaderScrollsAwayOnTheLocalShell` counts on the screen;
    /// here the shape is asserted instead.
    ///
    /// ### This reading was inverted on purpose, and why
    ///
    /// It used to require the gate to be `if let pill` **and nothing else**.
    /// That was right while `body(content:)` branched on the title: the pill
    /// sat inside the title arm, so a title clause there was already decided
    /// and a second condition was dead — `if let pill, fold.drawsTheToolbarPill`
    /// is review 3's N2, a gate that decides nothing reading as one that does.
    ///
    /// `body(content:)` does not branch any more (its note says what the
    /// `_ConditionalContent` cost: every `@State` under the stack, on every
    /// crossing of the accessibility-size boundary, including Apple's audit's
    /// own). With the arm gone, nothing upstream of this `if` knows whether
    /// the header is in the bar, so the title clause is the **only** thing
    /// standing between a nil fold and a pill drawn beside `ShellTopBar`'s own
    /// on every screen at every size. It is required here now rather than
    /// banned, and `.toolbar` is still applied exactly once.
    func testTheToolbarPillHangsOnlyWhereTheFoldGaveATitle() {
        guard let gate = Self.innermostGate(
            before: "ShellPillToolbarButton(",
            in: Self.chrome
        ) else {
            return XCTFail(
                "no `if` above `ShellPillToolbarButton(` in ShellChromeViews.swift: "
                    + "the toolbar item is built for a nil pill, which on the local "
                    + "shell with no server saved is an empty capsule where "
                    + "`connect` should be"
            )
        }
        XCTAssertTrue(
            gate.contains("let pill"),
            "the gate above `ShellPillToolbarButton(` is `\(gate)`, which does not "
                + "bind `pill`. Without it the toolbar item is built for a nil "
                + "pill, which on the local shell with no server saved is an empty "
                + "capsule where `connect` should be"
        )
        XCTAssertTrue(
            gate.contains("title != nil"),
            "the gate above `ShellPillToolbarButton(` is `\(gate)`, which does not "
                + "read `title != nil`. `body(content:)` no longer branches on the "
                + "title — it applies `.navigationTitle` and `.toolbar` "
                + "unconditionally so that `content` keeps one identity across the "
                + "accessibility-size boundary — so this clause is the only thing "
                + "left that knows whether the fold put the header in the bar. "
                + "Drop it and every screen draws a pill here beside "
                + "`ShellTopBar`'s own, at every size. (It was banned here while "
                + "the body did branch, where it was dead: review 3's N2.)"
        )
        XCTAssertEqual(
            Self.count(of: ".toolbar {", in: Self.chrome),
            1,
            "ShellChromeViews.swift applies `.toolbar` "
                + "\(Self.count(of: ".toolbar {", in: Self.chrome)) times, not once "
                + "— a second one outside the title branch is a pill on every "
                + "screen at every size, beside the bar's"
        )
    }

    // MARK: - what the rule is asked

    /// Every `forHeader` call reads the **environment's** type size.
    ///
    /// `isAccessibilitySize: false` would undo answer 29 everywhere with the
    /// rule, its test and the gates all intact — the cheapest sabotage left
    /// after the two above, and the one this catches.
    func testTheFoldIsAlwaysAskedWithTheEnvironmentsTypeSize() {
        var asked = 0
        for (name, code) in Self.all {
            for call in Self.arguments(ofCallsTo: "ShellHeaderFold.forHeader(", in: code) {
                asked += 1
                XCTAssertTrue(
                    call.contains("isAccessibilitySize: typeSize.isAccessibilitySize")
                        || call.contains(
                            "isAccessibilitySize: dynamicTypeSize.isAccessibilitySize"
                        ),
                    "\(name) asks `ShellHeaderFold.forHeader(\(call))`. The size "
                        + "argument must be the `@Environment(\\.dynamicTypeSize)` "
                        + "value, or the fold answers the same thing at every size "
                        + "and answer 29 is undone with every other piece of it "
                        + "still in place"
                )
            }
        }
        XCTAssertEqual(
            asked,
            3,
            "\(asked) `ShellHeaderFold.forHeader(` calls across the three bodies, "
                + "not 3 — one in `WorkspaceView.headerFold`, one in "
                + "`ShellScrollAwayChrome.fold`, one in `LocalShellView.headerFold`"
        )
    }

    /// The `AuditStrip.topBar` variant stays wired to the fold.
    ///
    /// Review 1's N5: that variant's job is the shell **without** its header,
    /// and when the header moved into the navigation bar the variant became
    /// identical to the control at AX5, so the bisect it exists for lost its
    /// one difference. Both the fold and the scroll-away modifier have to know
    /// about it — `drawn:` is the only input that carries it.
    func testTheHeaderlessVariantIsWiredFromTheAuditStrip() {
        let wired = Self.count(of: "drawn: AuditStrip.active != .topBar", in: Self.workspace)
        XCTAssertEqual(
            wired,
            3,
            "`drawn: AuditStrip.active != .topBar` appears \(wired) times in "
                + "WorkspaceView.swift, not three: once in the fold that gates "
                + "`ShellTopBar`, and once in each `shellScrollAwayChrome` — the "
                + "stack's root and the pushed destination. With any of them "
                + "missing the `topBar` variant still has a header at AX5 and is "
                + "the control, so the bisect it exists for loses its one "
                + "difference (review 1, N5)"
        )
        XCTAssertTrue(
            Self.localShell.contains("drawn: true"),
            "LocalShellView.swift no longer says `drawn: true`: that shell has no "
                + "`AuditStrip` variant, so its header is always drawn and the "
                + "argument is the place that says so"
        )
    }

    /// The local shell puts the header inside a stack **per tab**, which is
    /// the one thing that shell does differently.
    ///
    /// It is a `TabView`, so there is no one stack to title; below the
    /// accessibility sizes a tab is a screen with no stack at all, and the
    /// `NavigationStack` appears only when the rule says the header belongs
    /// inside it.
    func testTheLocalShellWrapsATabOnlyWhenTheHeaderGoesInside() {
        let gate = Self.innermostGate(before: "NavigationStack", in: Self.localShell)
        guard let gate else {
            return XCTFail(
                "no `if` above `NavigationStack` in LocalShellView.swift — every tab "
                    + "is wrapped in a stack at every size, so below the "
                    + "accessibility sizes each tab grew a navigation bar it never had"
            )
        }
        XCTAssertTrue(
            gate.contains("putsTheHeaderInsideTheStack"),
            "the gate above `NavigationStack` in LocalShellView.swift is `\(gate)`, "
                + "which does not read "
                + "`ShellHeaderFold.putsTheHeaderInsideTheStack`"
        )
    }

    // MARK: - the scanner

    /// `source` with line comments, block comments and string literals
    /// removed, so that prose quoting a rule cannot satisfy an assertion about
    /// the rule — the trap `AlertsStringsTests`' own scanner exists for, and
    /// the one that would matter most here: these three files' doc comments
    /// quote every symbol this file bans.
    static func code(in source: String) -> String {
        var out = ""
        var index = source.startIndex
        enum Scan { case plain, line, block, string }
        var state = Scan.plain
        func peek(_ offset: Int) -> Character? {
            guard let i = source.index(index, offsetBy: offset, limitedBy: source.endIndex),
                  i < source.endIndex
            else { return nil }
            return source[i]
        }
        while index < source.endIndex {
            let character = source[index]
            switch state {
            case .plain:
                if character == "/", peek(1) == "/" {
                    state = .line
                } else if character == "/", peek(1) == "*" {
                    state = .block
                    index = source.index(index, offsetBy: 2)
                    continue
                } else if character == "\"" {
                    state = .string
                } else {
                    out.append(character)
                }
            case .line:
                if character == "\n" {
                    state = .plain
                    out.append(character)
                }
            case .block:
                if character == "*", peek(1) == "/" {
                    state = .plain
                    index = source.index(index, offsetBy: 2)
                    continue
                }
                // Newlines inside a block comment are kept, so that a line
                // number in a failure message still means something.
                if character == "\n" { out.append(character) }
            case .string:
                if character == "\\" {
                    index = source.index(index, offsetBy: 1)
                    if index < source.endIndex { index = source.index(index, offsetBy: 1) }
                    continue
                }
                if character == "\"" { state = .plain }
            }
            index = source.index(after: index)
        }
        return out
    }

    private static func count(of needle: String, in code: String) -> Int {
        guard !needle.isEmpty else { return 0 }
        var total = 0
        var from = code.startIndex
        while let found = code.range(of: needle, range: from..<code.endIndex) {
            total += 1
            from = found.upperBound
        }
        return total
    }

    /// The text of the last `if` **condition** before `needle` — the gate a
    /// line of a view body sits behind, read from the source rather than
    /// inferred.
    ///
    /// The innermost one, because a `ViewBuilder` nests: the `if` nearest
    /// above a line is the branch that decides whether it is built. Returns
    /// nil when there is no `if` above it at all, which is its own finding.
    static func innermostGate(before needle: String, in code: String) -> String? {
        guard let site = code.range(of: needle) else { return nil }
        let head = code[code.startIndex..<site.lowerBound]
        guard let gate = head.range(of: "if ", options: .backwards) else { return nil }
        return head[gate.lowerBound...]
            .trimmingCharacters(in: CharacterSet(charactersIn: " \t\n{"))
    }

    /// Every use of `symbol` in `code`, each as `symbol` plus what follows it
    /// up to the first delimiter — so `ShellHeaderFold.forHeader(` and
    /// `ShellHeaderFold.noHeaderAtAll` are told apart.
    static func occurrences(of symbol: String, in code: String) -> [String] {
        var found: [String] = []
        var from = code.startIndex
        while let site = code.range(of: symbol, range: from..<code.endIndex) {
            var end = site.upperBound
            while end < code.endIndex, code[end].isLetter || code[end].isNumber {
                end = code.index(after: end)
            }
            if end < code.endIndex, code[end] == "(" { end = code.index(after: end) }
            found.append(String(code[site.lowerBound..<end]))
            from = site.upperBound
        }
        return found
    }

    /// The argument list of every call to `call` in `code`, whitespace
    /// collapsed to single spaces so that a multi-line call reads as one
    /// string.
    static func arguments(ofCallsTo call: String, in code: String) -> [String] {
        var found: [String] = []
        var from = code.startIndex
        while let site = code.range(of: call, range: from..<code.endIndex) {
            var depth = 1
            var index = site.upperBound
            var inner = ""
            while index < code.endIndex, depth > 0 {
                let character = code[index]
                if character == "(" { depth += 1 }
                if character == ")" {
                    depth -= 1
                    if depth == 0 { break }
                }
                inner.append(character)
                index = code.index(after: index)
            }
            found.append(
                inner
                    .split(whereSeparator: { $0 == " " || $0 == "\n" || $0 == "\t" })
                    .joined(separator: " ")
            )
            from = site.upperBound
        }
        return found
    }
}
