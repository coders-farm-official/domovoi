import Foundation
import XCTest
@testable import DomovoiKit

/// The Voices section's decoders and its two pure rules, against the bodies
/// the **server's route code** says it sends.
///
/// Every JSON string below was written from `web/backend/api/voice.py` and
/// `domovoi/voice_settings.py`, not from Android's `VoiceInfo` — which
/// defaults every field and so cannot tell a shape change from an empty
/// house. The ids are the real `model_ref`s and the sizes the real
/// `onnx_bytes`, so a test that passed on a trimmed body could not pass here.
final class VoiceSettingsModelsTests: XCTestCase {

    // MARK: - the bodies

    /// `GET /api/voice`, exactly as `voice_payload` builds it
    /// (`voice_settings.py:155-169`).
    private let realBody = """
    {
      "voices": [
        {"id": "en_US-norman-medium", "name": "Norman", "sex": "male",
         "sample_rate": 22050, "size_bytes": 63531379, "sha256": "b97394aa"},
        {"id": "en_US-kristin-medium", "name": "Kristin", "sex": "female",
         "sample_rate": 22050, "size_bytes": 63531379, "sha256": "c21180ff"},
        {"id": "en_US-ljspeech-medium", "name": "Linda", "sex": "female",
         "sample_rate": 22050, "size_bytes": 63531379, "sha256": "d44021ab"}
      ],
      "server_voice": "en_US-kristin-medium",
      "pronunciations": [{"word": "Nguyen", "say_like": "win"}],
      "updated_at": "2026-11-01T18:04:11Z"
    }
    """

    private func info(_ json: String) throws -> VoiceSettingsInfo {
        try JSONDecoder().decode(VoiceSettingsInfo.self, from: Data(json.utf8))
    }

    private func samples(_ json: String) throws -> VoiceSettingsSamples {
        try JSONDecoder().decode(VoiceSettingsSamples.self, from: Data(json.utf8))
    }

    // MARK: - the info read

    func testTheServersOwnBodyDecodesFieldForField() throws {
        let read = try info(realBody)
        XCTAssertEqual(read.voices.count, 3)
        XCTAssertEqual(read.voices.map(\.id), [
            "en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium",
        ])
        // The order is the server's `SHIPPED_VOICES` order and is drawn as
        // given, so it is asserted rather than sorted away.
        XCTAssertEqual(read.voices.map(\.name), ["Norman", "Kristin", "Linda"])
        XCTAssertEqual(read.voices.map(\.sex), ["male", "female", "female"])
        XCTAssertEqual(read.voices[0].sampleRate, 22050)
        XCTAssertEqual(read.voices[0].sizeBytes, 63_531_379)
        XCTAssertEqual(read.voices[0].sha256, "b97394aa")
        XCTAssertEqual(read.serverVoice, "en_US-kristin-medium")
        XCTAssertEqual(read.pronunciations, [VoiceSettingsSayItLike(word: "Nguyen", sayLike: "win")])
        XCTAssertEqual(read.updatedAt, "2026-11-01T18:04:11Z")
    }

    /// `updated_at` is what the section's "last changed" line reads, and the
    /// server's format has no fractional seconds and a bare `Z`. A parser
    /// that could not read it would leave the line blank, which is the
    /// failure that looks like missing data.
    func testUpdatedAtIsAnInstantThisAppCanRead() throws {
        let read = try info(realBody)
        // Built from components rather than from an epoch literal: a literal
        // I worked out by hand would be a test of my arithmetic, and the
        // first draft of this line had it wrong by five days.
        var parts = DateComponents()
        parts.year = 2026
        parts.month = 11
        parts.day = 1
        parts.hour = 18
        parts.minute = 4
        parts.second = 11
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(secondsFromGMT: 0)!
        XCTAssertEqual(parseInstant(read.updatedAt), utc.date(from: parts))
    }

    /// An empty list is the owner having emptied it — a real answer, and one
    /// the section words differently from "no list".
    func testAnEmptyPronunciationListDecodesAsEmptyAndNotAsAFailure() throws {
        let read = try info(realBody.replacingOccurrences(
            of: #"[{"word": "Nguyen", "say_like": "win"}]"#, with: "[]"
        ))
        XCTAssertEqual(read.pronunciations, [])
        XCTAssertEqual(read.entries, [])
    }

    /// The one key that may be absent, and why — see the file comment on
    /// ``VoiceSettingsModels``.
    func testAMissingPronunciationsKeyReadsAsNoEntries() throws {
        let read = try info("""
        {"voices": [], "server_voice": "en_US-kristin-medium", "updated_at": "2026-11-01T18:04:11Z"}
        """)
        XCTAssertEqual(read.pronunciations, [])
    }

    /// Blank halves are dropped before anything is drawn — `pairs()`
    /// (`PhoneVoice.kt:74`). Discriminating data: the kept entry's word
    /// appears in no dropped one, so "it kept the right one" cannot pass by
    /// keeping all three.
    func testBlankEntriesAreDroppedTheWayPairsDropsThem() throws {
        let read = try info("""
        {"voices": [], "server_voice": "x", "updated_at": "2026-11-01T18:04:11Z",
         "pronunciations": [{"word": "Caoimhe", "say_like": "kee-va"},
                            {"word": "", "say_like": "win"},
                            {"word": "Nguyen", "say_like": "   "}]}
        """)
        XCTAssertEqual(read.pronunciations.count, 3)
        XCTAssertEqual(read.entries.map(\.word), ["Caoimhe"])
    }

    /// Non-optional, deliberately: a body missing a voice field must fail the
    /// read rather than draw a blank row. This is the one assertion that
    /// keeps that decision from quietly becoming Android's defaulted
    /// decoder.
    func testAVoiceMissingAFieldFailsTheReadRatherThanDrawingBlanks() {
        let bodies = [
            #"{"voices": [{"id": "a", "sex": "male", "sample_rate": 1, "size_bytes": 1, "sha256": "x"}], "server_voice": "a", "updated_at": "t"}"#,
            #"{"voices": [{"id": "a", "name": "A", "sample_rate": 1, "size_bytes": 1, "sha256": "x"}], "server_voice": "a", "updated_at": "t"}"#,
            #"{"voices": [{"id": "a", "name": "A", "sex": "male", "size_bytes": 1, "sha256": "x"}], "server_voice": "a", "updated_at": "t"}"#,
            #"{"voices": [], "updated_at": "t"}"#,
        ]
        for body in bodies {
            XCTAssertThrowsError(try info(body), "this body should not decode: \(body)")
        }
    }

    /// `server_voice` can be anything `settings.tts_piper_voice` holds
    /// (`voice_settings.py:143-152`), so the body below is reachable and must
    /// decode — and ``VoiceSettingsInfo/houseVoice`` must say it is not one
    /// of the three rather than picking one.
    func testAServerVoiceOutsideTheThreeDecodesAndIsNotMatchedToOne() throws {
        let read = try info(realBody.replacingOccurrences(
            of: #""server_voice": "en_US-kristin-medium""#,
            with: #""server_voice": "en_GB-cori-high""#
        ))
        XCTAssertEqual(read.serverVoice, "en_GB-cori-high")
        XCTAssertNil(read.houseVoice)
        XCTAssertFalse(VoiceSettingsNaming.serverVoiceIsListed(read))
        // Named, not swallowed. Asserting the raw id *and* that no shipped
        // name was substituted: the bug this guards against prints "Kristin".
        XCTAssertEqual(VoiceSettingsNaming.houseVoiceLabel(read), "en_GB-cori-high")
        XCTAssertFalse(VoiceSettingsNaming.houseVoiceLabel(read).contains("Kristin"))
    }

    func testTheHousesVoiceIsNamedWithTheServersOwnSex() throws {
        let read = try info(realBody)
        XCTAssertEqual(VoiceSettingsNaming.houseVoiceLabel(read), "Kristin (female)")
        XCTAssertTrue(VoiceSettingsNaming.serverVoiceIsListed(read))
        XCTAssertEqual(VoiceSettingsNaming.label(for: read.voices[0]), "Norman (male)")
    }

    /// A voice with no sex is not a shape the server sends, but the label
    /// must not come out as `"Norman ()"` if one ever appears.
    func testAVoiceWithABlankSexIsNamedByNameAlone() {
        let voice = VoiceSettingsVoice(
            id: "x", name: "Norman", sex: "  ", sampleRate: 22050, sizeBytes: 1, sha256: "x"
        )
        XCTAssertEqual(VoiceSettingsNaming.label(for: voice), "Norman")
    }

    // MARK: - the samples read, which is the button's only predicate

    /// The body the fixture serves and the server really sends: one of each,
    /// so "a button per voice" and "a button where the server says yes" are
    /// different answers.
    func testOnlyAVoiceTheServerWillSynthesizeIsPlayable() throws {
        let read = try samples("""
        {"samples": {"en_US-norman-medium": "voice",
                     "en_US-kristin-medium": "clip",
                     "en_US-ljspeech-medium": null}}
        """)
        XCTAssertTrue(read.isPlayable("en_US-norman-medium"))
        // A clip is Ogg Opus, which AVFoundation does not decode: no button.
        XCTAssertFalse(read.isPlayable("en_US-kristin-medium"))
        XCTAssertFalse(read.isPlayable("en_US-ljspeech-medium"))
        // And a voice the server did not mention at all.
        XCTAssertFalse(read.isPlayable("en_GB-cori-high"))
    }

    /// `null` and "not mentioned" are both no, and both have to stay
    /// distinguishable — the map's fixture seeds a `null` voice precisely so
    /// a case can tell them apart.
    func testANullSourceIsAKeyWithNoValueAndNotAMissingKey() throws {
        let read = try samples("""
        {"samples": {"en_US-norman-medium": "voice", "en_US-ljspeech-medium": null}}
        """)
        XCTAssertTrue(read.samples.keys.contains("en_US-ljspeech-medium"))
        XCTAssertNil(read.source(of: "en_US-ljspeech-medium"))
        XCTAssertNil(read.source(of: "never-mentioned"))
        XCTAssertEqual(read.source(of: "en_US-norman-medium"), .voice)
    }

    /// A source string this app has never heard of must read as "no button",
    /// not as a failed read: the safe side of this rule is the quiet one.
    func testAnUnknownSourceStringIsNoButtonRatherThanAFailedRead() throws {
        let read = try samples(#"{"samples": {"en_US-norman-medium": "stream"}}"#)
        XCTAssertFalse(read.isPlayable("en_US-norman-medium"))
        XCTAssertNil(read.source(of: "en_US-norman-medium"))
    }

    func testAnEmptySamplesMapPlaysNothing() {
        XCTAssertFalse(VoiceSettingsSamples.empty.isPlayable("en_US-norman-medium"))
    }

    // MARK: - the strings whose shape is a rule

    func testTheEntryCountLineIsSingularForOne() {
        XCTAssertEqual(
            VoiceSettingsStrings.namesSaidByTheServer(count: 1),
            "Names are said the way the server's \"say it like\" list says (1 entry)."
        )
        XCTAssertEqual(
            VoiceSettingsStrings.namesSaidByTheServer(count: 2),
            "Names are said the way the server's \"say it like\" list says (2 entries)."
        )
    }

    /// Android's literal is `"Piper, 22 kHz"` (`VoicePanel.kt:181`); driving
    /// it from the server's `sample_rate` has to produce that same string for
    /// the rate the server actually sends.
    func testTheEngineLineMatchesAndroidsForTheRateTheServerSends() {
        XCTAssertEqual(VoiceSettingsStrings.engineAndRate(sampleRate: 22050), "Piper, 22 kHz")
        XCTAssertEqual(VoiceSettingsStrings.engineAndRate(sampleRate: 16000), "Piper, 16 kHz")
        XCTAssertEqual(VoiceSettingsStrings.engineAndRate(sampleRate: 0), "Piper")
    }
}
