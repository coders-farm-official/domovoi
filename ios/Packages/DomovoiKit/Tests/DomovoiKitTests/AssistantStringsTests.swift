import Foundation
import XCTest
@testable import DomovoiKit

/// Every word this milestone ships, against the Kotlin on disk.
///
/// ### Why this reads the Kotlin and does not hold a copy of it
///
/// `ShellStringsTests` learned the lesson on 2026-10-29: a **containment**
/// check on a bag of literals proves only that ours is a *subset* of what
/// Android says, so a sentence shortened at either edge passes. The answer
/// there was to compare whole strings, and that is what
/// ``assertKotlinLiteral(_:in:file:line:)`` does here — it looks for ours
/// **inside its quotes**, so a clause dropped off either end cannot match.
///
/// The three sentences this milestone had to change are asserted the other way
/// round: the clause that was cut is shown to be in the Kotlin and *not* in
/// ours, which is the only way a deliberate difference can be tested at all.
/// Each is in `overnight/assistant/IOS-ONLY-STRINGS.md` with its reason.
final class AssistantStringsTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so the
    /// repository root is six directories up — the same walk
    /// `ShellStringsTests` and `LicencesTests` make.
    static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static func kotlin(_ name: String) -> String {
        let url = repositoryRoot
            .appendingPathComponent("android/app/src/main/java/com/domovoi/app")
            .appendingPathComponent(name)
        return (try? String(contentsOf: url, encoding: .utf8)) ?? ""
    }

    private static let chatPlace = kotlin("ui/screens/chat/ChatPlace.kt")
    private static let chatHome = kotlin("ui/screens/chat/ChatHome.kt")
    private static let assistantChat = kotlin("ui/screens/assistant/AssistantChat.kt")
    private static let phoneChatPlace = kotlin("ui/screens/assistant/PhoneChatPlace.kt")
    private static let controller = kotlin("assistant/AssistantController.kt")
    private static let chatTitles = kotlin("assistant/ChatTitles.kt")
    private static let persona = kotlin("assistant/Persona.kt")
    private static let chatExport = kotlin("assistant/ChatExport.kt")

    func testTheKotlinWasActuallyRead() {
        for (name, text) in [
            ("ChatPlace.kt", Self.chatPlace), ("ChatHome.kt", Self.chatHome),
            ("AssistantChat.kt", Self.assistantChat), ("PhoneChatPlace.kt", Self.phoneChatPlace),
            ("AssistantController.kt", Self.controller), ("ChatTitles.kt", Self.chatTitles),
            ("Persona.kt", Self.persona), ("ChatExport.kt", Self.chatExport),
        ] {
            XCTAssertFalse(text.isEmpty, "\(name) was not read — every assertion below would pass vacuously")
        }
    }

    /// `text` is a **whole** Kotlin literal in `source`: found with a quote on
    /// each side, so a truncation at either end fails.
    private func assertKotlinLiteral(
        _ text: String,
        in source: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(
            source.contains("\"\(text)\""),
            "the Kotlin does not say exactly: \(text)",
            file: file, line: line
        )
    }

    /// `text` is a **piece** of a Kotlin literal — for a string the Kotlin
    /// interpolates a value into, where the edges are all there is to compare.
    private func assertKotlinSays(
        _ text: String,
        in source: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(source.contains(text), "the Kotlin does not contain: \(text)", file: file, line: line)
    }

    // MARK: - the chips and the away lines (`ChatPlace.kt:38-50`)

    func testTheChips() {
        assertKotlinLiteral(AssistantStrings.chipPhone, in: Self.chatPlace)
        assertKotlinLiteral(AssistantStrings.chipServer, in: Self.chatPlace)
        assertKotlinLiteral(AssistantStrings.chipServerShared, in: Self.chatPlace)
        assertKotlinLiteral(AssistantStrings.awayLine, in: Self.chatPlace)
        assertKotlinLiteral(AssistantStrings.localChosenLine, in: Self.chatPlace)
    }

    func testTheSeparatorInTheSharedChipIsTheHousesOwn() {
        XCTAssertTrue(AssistantStrings.chipServerShared.contains(" \u{00B7} "), "U+00B7, one space each side")
    }

    // MARK: - the list and the picker

    func testThePickerLabelAndTheTwoSubtitles() {
        assertKotlinLiteral(AssistantStrings.runsLabel, in: Self.chatHome)
        assertKotlinLiteral(AssistantStrings.subtitleBoth, in: Self.chatHome)
        assertKotlinLiteral(AssistantStrings.subtitlePhoneOnly, in: Self.chatHome)
        assertKotlinLiteral(AssistantStrings.emptyHintPhone, in: Self.chatHome)
    }

    func testTheDashesAreEmDashes() {
        for sentence in [
            AssistantStrings.subtitleBoth,
            AssistantStrings.subtitlePhoneOnly,
            AssistantStrings.emptyHintPhone,
        ] {
            XCTAssertTrue(sentence.contains("\u{2014}"), "an em dash, as the Kotlin's is: \(sentence)")
            XCTAssertFalse(sentence.contains(" - "), "not a hyphen")
        }
    }

    // MARK: - the conversation

    func testTheEmptyChatsTitleAndTheMarksOnAReply() {
        assertKotlinLiteral(AssistantStrings.askAnything, in: Self.assistantChat)
        assertKotlinLiteral(AssistantStrings.stopped, in: Self.assistantChat)
        assertKotlinLiteral(AssistantStrings.copy, in: Self.assistantChat)
    }

    func testTheSevenActionWordsAreChatsOwnAndNotASecondSpelling() {
        // The menu is built from ``ChatStrings``, which already holds them; if
        // this milestone had spelled them again, this is the test that would
        // have found the two copies drifting.
        for word in assistantRowActions(pinned: false, archived: false)
            + assistantRowActions(pinned: true, archived: true) {
            assertKotlinLiteral(word, in: Self.chatPlace)
        }
    }

    func testTheDeleteConfirmationIsThePhonesAndNotTheHouses() {
        assertKotlinSays("is deleted from this phone. This can't be undone.", in: Self.phoneChatPlace)
        XCTAssertEqual(
            AssistantStrings.deleteText("Quince"),
            "\"Quince\" is deleted from this phone. This can't be undone."
        )
        XCTAssertNotEqual(AssistantStrings.deleteText("Quince"), ChatStrings.deleteText("Quince"))
        XCTAssertFalse(
            AssistantStrings.deleteText("Quince").contains(ChatStrings.serverDeleteNote),
            "the household sentence belongs to the other place"
        )
    }

    func testClearAll() {
        assertKotlinLiteral(AssistantStrings.clearAllTitle, in: Self.assistantChat)
        assertKotlinLiteral(AssistantStrings.clearAllConfirm, in: Self.assistantChat)
        assertKotlinSays("Deletes every conversation kept on this phone (", in: Self.assistantChat)
        assertKotlinSays("). This can't be undone.", in: Self.assistantChat)
        XCTAssertEqual(
            AssistantStrings.clearAllBody(3),
            "Deletes every conversation kept on this phone (3). This can't be undone."
        )
    }

    // MARK: - a reply that did not arrive

    func testTheFailureSentencesAreTheControllersOwn() {
        assertKotlinSays("The model stopped: ", in: Self.controller)
        assertKotlinSays("The model could not start: ", in: Self.controller)
        assertKotlinLiteral(AssistantStrings.cutOff, in: Self.controller)
        XCTAssertEqual(AssistantStrings.modelStopped("out of memory"), "The model stopped: out of memory")
        XCTAssertEqual(
            AssistantStrings.modelCouldNotStart("Apple Intelligence is off in Settings"),
            "The model could not start: Apple Intelligence is off in Settings"
        )
    }

    // MARK: - sharing (`ChatExport.kt:56`)

    func testTheShareHeaderIsThisPhones() {
        assertKotlinSays("Chat with Domovoi on this phone, ", in: Self.chatExport)
        XCTAssertNotEqual(AssistantStrings.shareHeader, ChatStrings.shareHeader)
    }

    // MARK: - the title job (`ChatTitles.kt`)

    func testTheTitlePromptIsTheServersOwnSentence() {
        assertKotlinLiteral(AssistantPersona.titleSystem, in: Self.chatTitles)
        XCTAssertEqual(AssistantPersona.titleMaxTokens, 16)
        XCTAssertEqual(AssistantPersona.titleMaxTries, 2)
        XCTAssertEqual(AssistantPersona.titleExcerptChars, 800)
    }

    // MARK: - the persona (`Persona.kt:37-46`)

    func testThePromptKeepsEveryKeptClauseWordForWord() {
        for clause in [
            "You are $NAME, a helpful voice assistant. Keep responses concise and ",
            "conversational \u{2014} this is a voice interaction, so avoid markdown, bullet ",
            "lists, code blocks, and long structured responses. Speak naturally, in ",
            "short sentences. Most replies should be one to three sentences. You are ",
            "running on this phone with no internet. ",
            "You do have the phone's clock: when its reading is given, use it to answer ",
            "questions about the time, the date or the day and to work out other dates ",
            "and times from it, and don't mention it otherwise. ",
        ] {
            assertKotlinSays(clause, in: Self.persona)
        }
        // ...and ours is those clauses, with `$NAME` resolved.
        XCTAssertTrue(AssistantPersona.systemPrompt.hasPrefix("You are Domovoi, a helpful voice assistant."))
        XCTAssertEqual(AssistantPersona.name, "Domovoi")
    }

    /// **The two dropped sentences.** They are in the Kotlin, they are not in
    /// ours, and the one that replaces them is nowhere in the Kotlin — which
    /// is what "iOS-only" means and is the only way round to test it.
    func testTheActionsAndFactsSentencesAreInTheKotlinAndNotInOurs() {
        assertKotlinSays("The app itself, not you, handles a plainly worded request to set a timer", in: Self.persona)
        assertKotlinSays("You may also be given phone facts (what is playing", in: Self.persona)

        let prompt = AssistantPersona.systemPrompt
        XCTAssertFalse(prompt.contains("The app itself, not you, handles"))
        XCTAssertFalse(prompt.contains("You may also be given phone facts"))
        XCTAssertTrue(prompt.hasSuffix(AssistantStrings.cannotYet))
        XCTAssertFalse(Self.persona.contains(AssistantStrings.cannotYet), "iOS-only: Android has no such sentence")
    }

    // MARK: - the three iOS-only sentences

    /// The empty conversation's hint, with the mic clause cut: M1 draws no
    /// microphone, and a hint that sends somebody looking for one is the
    /// sentence half of [[no-misleading-controls]].
    func testTheEmptyChatHintKeepsEveryClauseButTheMic() {
        assertKotlinSays("Type, or tap the mic and speak. Answers come from a model running on this phone, with no server and no ", in: Self.assistantChat)
        assertKotlinSays("internet, and this chat stays on this phone.", in: Self.assistantChat)

        XCTAssertEqual(
            AssistantStrings.emptyChatHint,
            "Type a question. Answers come from a model running on this phone, with no server and no "
                + "internet, and this chat stays on this phone."
        )
        XCTAssertFalse(AssistantStrings.emptyChatHint.contains("mic"), "there is no microphone until M2")
        XCTAssertTrue(AssistantStrings.emptyChatHint.hasSuffix("and this chat stays on this phone."),
                      "the fact this place exists to make is kept")
    }

    /// The privacy line, with every clause this milestone has no feature for
    /// cut — and the two it can stand behind kept.
    func testThePrivacyLineKeepsWhatIsTrueAndDropsWhatIsNot() {
        assertKotlinSays("kept on this phone, in the app's private storage and outside backups.", in: Self.assistantChat)
        assertKotlinSays("contacts or location.", in: Self.assistantChat)

        let said = AssistantStrings.privacyLine(4)
        XCTAssertTrue(said.hasPrefix("4 kept on this phone, in the app's private storage and outside backups."))
        XCTAssertTrue(said.hasSuffix("not your other files, contacts or location."))
        for notYet in ["documents", "photos", "letters", "look up", "what's playing", "first name"] {
            XCTAssertFalse(said.contains(notYet), "\(notYet) is a later milestone's and must not be claimed")
        }
    }

    /// The failure sentence Android has no words for, because a local Gemma
    /// has no guardrail layer.
    func testTheRefusalSentenceIsOursAloneAndIsNotAStopped() {
        XCTAssertFalse(Self.controller.contains(AssistantStrings.refused))
        XCTAssertFalse(AssistantStrings.refused.hasPrefix("The model stopped"))
        XCTAssertTrue(AssistantStrings.refused.contains("wouldn't answer"))
    }

    func testTheWriteFailureNamesTheVerbAndNoServer() {
        XCTAssertEqual(AssistantStrings.writeFailed(ChatStrings.verbRename), "couldn't rename on this phone")
        XCTAssertFalse(AssistantStrings.writeFailed(ChatStrings.verbDelete).contains("offline"),
                       "there is no server in this failure at all")
    }

    /// The composer's stop, and who a bubble belongs to — the two words the
    /// views needed and the headless half had not transcribed.
    ///
    /// `stop` is the Kotlin's own word (`AssistantChat.kt:377`); what moved is
    /// the control it labels, from the voice row this milestone does not have
    /// to the composer, and DECISIONS.md carries that move. `bubbleState` is
    /// transcribed whole, all three branches.
    func testTheStopAndTheBubbleState() {
        assertKotlinLiteral(AssistantStrings.stop, in: Self.assistantChat)
        for branch in [
            AssistantStrings.bubbleState(fromUser: true, writing: false),
            AssistantStrings.bubbleState(fromUser: false, writing: true),
            AssistantStrings.bubbleState(fromUser: false, writing: false),
        ] {
            assertKotlinLiteral(branch, in: Self.assistantChat)
        }
        // The person's own message says so whether or not a reply is being
        // written somewhere: `fromUser` is the Kotlin's first branch.
        XCTAssertEqual(
            AssistantStrings.bubbleState(fromUser: true, writing: true),
            AssistantStrings.bubbleState(fromUser: true, writing: false)
        )
    }
}
