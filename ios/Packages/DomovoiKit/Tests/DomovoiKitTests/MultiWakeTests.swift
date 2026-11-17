import Foundation
import XCTest
@testable import DomovoiKit

/// Several wake words per room (1.4.0, design-notes MULTI-WAKE-MOBILE-HANDOFF;
/// the core's domovoi/wake_default.py, the Pi's satellite/wake_words.py):
/// the hello's `wake_words` / `wake_words_max`, `set_wake_word`'s `words`,
/// `utterance_start.wake_word`, the phone's limit, per-word thresholds and
/// gates, the kept list, and one openWakeWord front end under N
/// classifiers. The room end to end is in `RoomEngineTests`.
final class MultiWakeTests: XCTestCase {

    // MARK: - the hello

    func testTheHelloListsTheWordsMainFirstAndSaysTheMax() {
        let hello = RoomProtocol.hello(
            roomID: "kitchen", wakeWord: "domovoi_v1", pairingToken: "k", approvalCode: nil, echoCanceller: true,
            wakeWords: ["domovoi_v1", "hey_jarvis"], wakeWordsMax: 3
        )
        XCTAssertEqual(.string("domovoi_v1"), hello["wake_word"])
        XCTAssertEqual(.array([.string("domovoi_v1"), .string("hey_jarvis")]), hello["wake_words"])
        XCTAssertEqual(.int(3), hello["wake_words_max"])
        XCTAssertTrue(RoomProtocol.text(hello).contains(#""wake_words":["domovoi_v1","hey_jarvis"],"wake_words_max":3"#),
                      RoomProtocol.text(hello))
    }

    /// The list as given (Android's `helloWords`): slugs, no repeats, at
    /// most five, never padded with the main word.
    func testTheHelloSaysTheWordsAsGivenCleaned() {
        let hello = RoomProtocol.hello(
            roomID: "kitchen", wakeWord: "porch_cat", pairingToken: "k", approvalCode: nil, echoCanceller: true,
            wakeWords: ["hey_jarvis", "Bad Slug", "hey_jarvis", "alexa", "timer", "weather", "w6", "w7"], wakeWordsMax: 3
        )
        XCTAssertEqual(.array(["hey_jarvis", "alexa", "timer", "weather", "w6"].map { .string($0) }), hello["wake_words"])
        XCTAssertEqual(.int(3), hello["wake_words_max"])
        // A max over five is five, the most a room may have.
        let big = RoomProtocol.hello(roomID: "k", wakeWord: "a", pairingToken: "k", approvalCode: nil, echoCanceller: true,
                                     wakeWords: ["a"], wakeWordsMax: 9)
        XCTAssertEqual(.int(5), big["wake_words_max"])
    }

    /// Without the arguments the hello is the shape before 1.4.0 (what an
    /// older core and the old fixture know).
    func testWithoutWordsTheHelloHasNeitherKey() {
        let hello = RoomProtocol.hello(roomID: "kitchen", wakeWord: "hey_jarvis", pairingToken: "k", approvalCode: nil, echoCanceller: true)
        XCTAssertNil(hello["wake_words"])
        XCTAssertNil(hello["wake_words_max"])
    }

    /// Android's `reportedWords`: what is about to load, else what loaded,
    /// else the main word alone.
    func testTheHellosWordsAreWhatLoadedOrIsAboutTo() {
        XCTAssertEqual(["hey_jarvis"], RoomWakeWords.reported(main: "hey_jarvis", pending: nil, loaded: nil))
        // An extra word whose model would not load is not claimed.
        XCTAssertEqual(["hey_jarvis", "hey_house"],
                       RoomWakeWords.reported(main: "hey_jarvis", pending: nil, loaded: ["hey_jarvis", "hey_house"]))
        XCTAssertEqual(["hey_jarvis", "porch_cat"],
                       RoomWakeWords.reported(main: "hey_jarvis", pending: ["hey_jarvis", "porch_cat"], loaded: ["hey_jarvis"]))
        // A new main word not loaded yet: the room says what it still runs.
        XCTAssertEqual(["hey_jarvis", "porch_cat"],
                       RoomWakeWords.reported(main: "domovoi_v1", pending: nil, loaded: ["hey_jarvis", "porch_cat"]))
        let extras = [RoomWakeWord(slug: "porch_cat", threshold: 0.62), RoomWakeWord(slug: "hey_house")]
        XCTAssertEqual(["hey_jarvis", "porch_cat", "hey_house"], RoomWakeWords.configured(main: "hey_jarvis", extras: extras, max: 5))
        XCTAssertEqual(["hey_jarvis", "porch_cat"], RoomWakeWords.configured(main: "hey_jarvis", extras: extras, max: 2))
    }

    // MARK: - the phone's limit

    func testFiveWordsNormallyThreeOnALowMemoryPhone() {
        let gib: UInt64 = 1024 * 1024 * 1024
        // What iOS reports: a 2 GB iPhone a little under 2 GiB, a 3 GB one ~2.8.
        XCTAssertEqual(3, RoomWakeWords.deviceMax(physicalMemory: 2_075_000_000))
        XCTAssertEqual(3, RoomWakeWords.deviceMax(physicalMemory: 2 * gib))
        XCTAssertEqual(3, RoomWakeWords.deviceMax(physicalMemory: 1 * gib))
        XCTAssertEqual(5, RoomWakeWords.deviceMax(physicalMemory: 3_000_000_000))
        XCTAssertEqual(5, RoomWakeWords.deviceMax(physicalMemory: 6 * gib))
        XCTAssertEqual(5, RoomWakeWords.maxPerRoom)
        XCTAssertTrue((1...5).contains(RoomWakeWords.deviceMax()), "this machine's own")
    }

    // MARK: - set_wake_word

    func testSetWakeWordWithWordsParsesEachWithItsOwnThreshold() {
        let frame = #"{"type":"set_wake_word","slug":"domovoi_v1","words":[{"slug":"domovoi_v1"},{"slug":"porch_cat","threshold":0.62},{"slug":"hey_jarvis"}]}"#
        XCTAssertEqual(
            .setWakeWord(slug: "domovoi_v1", threshold: nil, words: [
                RoomWakeWord(slug: "domovoi_v1"),
                RoomWakeWord(slug: "porch_cat", threshold: 0.62),
                RoomWakeWord(slug: "hey_jarvis"),
            ]),
            RoomProtocol.parse(frame)
        )
    }

    func testSetWakeWordWithoutWordsIsTheSlugAlone() {
        XCTAssertEqual(.setWakeWord(slug: "hey_house", threshold: 0.6, words: nil),
                       RoomProtocol.parse(#"{"type":"set_wake_word","slug":"hey_house","threshold":0.6}"#))
        XCTAssertEqual([RoomWakeWord(slug: "hey_house", threshold: 0.6)],
                       RoomWakeWords.pushed(slug: "hey_house", threshold: 0.6, words: nil, max: 5))
    }

    /// The Pi's `parse_words`: slug-shaped, no repeats, a bad threshold is
    /// the room's, bare slugs count, at most five; nothing usable is no list.
    func testAPushedListIsCleaned() {
        let raw = RoomJSON.parse(#"[{"slug":" a1 "},{"slug":"../evil"},"b2",{"slug":"a1"},{"slug":"c3","threshold":1.5},{"slug":"d4","threshold":1},7,{"slug":"e5"},{"slug":"f6"},{"slug":"g7"}]"#)
        XCTAssertEqual([
            RoomWakeWord(slug: "a1"), RoomWakeWord(slug: "b2"), RoomWakeWord(slug: "c3"),
            RoomWakeWord(slug: "d4", threshold: 1), RoomWakeWord(slug: "e5"),
        ], RoomWakeWords.parse(raw))
        XCTAssertNil(RoomWakeWords.parse(RoomJSON.parse(#"[{"slug":"Bad Slug"}]"#)))
        XCTAssertNil(RoomWakeWords.parse(RoomJSON.parse(#"{"slug":"a1"}"#)))
        XCTAssertNil(RoomWakeWords.parse(nil))
    }

    /// The room loads at most its own limit whatever arrives, and a list
    /// that does not lead with the main word is not trusted.
    func testTheCapIsThePhonesAndAListMustLeadWithTheMainWord() {
        let five = ["m", "w1", "w2", "w3", "w4"].map { RoomWakeWord(slug: $0) }
        XCTAssertEqual(["m", "w1", "w2"], RoomWakeWords.pushed(slug: "m", threshold: nil, words: five, max: 3).map(\.slug))
        XCTAssertEqual(5, RoomWakeWords.pushed(slug: "m", threshold: nil, words: five, max: 5).count)
        XCTAssertEqual(5, RoomWakeWords.pushed(slug: "m", threshold: nil, words: five + [RoomWakeWord(slug: "w5")], max: 9).count)
        XCTAssertEqual(["m"], RoomWakeWords.pushed(slug: "m", threshold: nil, words: five, max: 1).map(\.slug))
        XCTAssertEqual(["other"], RoomWakeWords.pushed(slug: "other", threshold: nil, words: five, max: 5).map(\.slug))
        XCTAssertEqual(2, RoomEngine.capped(Array(five.dropFirst()), max: 3).count)
        XCTAssertEqual(0, RoomEngine.capped(Array(five.dropFirst()), max: 1).count)
    }

    // MARK: - per-word thresholds and gates

    func testTheMainWordWakesAtTheRoomsThresholdTheOthersAtTheirOwn() {
        let extras = [RoomWakeWord(slug: "porch_cat", threshold: 0.62), RoomWakeWord(slug: "hey_house")]
        XCTAssertEqual([0.5, 0.62, 0.5], RoomWakeWords.thresholds(for: ["hey_jarvis", "porch_cat", "hey_house"],
                                                                 room: 0.5, extras: extras))
        // A word the ears loaded that the list no longer names: the room's.
        XCTAssertEqual([0.7, 0.7], RoomWakeWords.thresholds(for: ["hey_jarvis", "ghost"], room: 0.7, extras: []))
        // The first loaded word is the main word, whatever its own threshold.
        XCTAssertEqual([0.7, 0.62], RoomWakeWords.thresholds(for: ["porch_cat", "porch_cat2"], room: 0.7,
                                                            extras: [RoomWakeWord(slug: "porch_cat", threshold: 0.9),
                                                                     RoomWakeWord(slug: "porch_cat2", threshold: 0.62)]))
    }

    func testEachWordHasItsOwnGateAndTheFirstToOpenWakes() {
        let g = WakeGateSet(words: ["hey_jarvis", "porch_cat", "hey_house"], thresholds: [0.5, 0.95, 0.5])
        XCTAssertEqual([0.5, 0.95, 0.5], g.thresholds)
        // porch_cat's 0.9 is under its own 0.95: no wake.
        XCTAssertNil(g.feed([0.01, 0.9, 0.01], now: 0))
        XCTAssertNil(g.feed([0.01, 0.01, 0.01], now: 80))
        // hey_house's 0.9 is over the room's 0.5.
        XCTAssertEqual(2, g.feed([0.01, 0.01, 0.9], now: 160))
        XCTAssertEqual(0.9, g.lastPeak(2))
        XCTAssertNil(g.lastPeak(0))
        // Two at once: the first in the room's order.
        XCTAssertEqual(0, g.feed([0.8, 0.99, 0.9], now: 240))
        // A word with no score scores nothing.
        XCTAssertNil(g.feed([0.01], now: 320))
    }

    func testPatienceAndCooldownAreEachWordsOwn() {
        let g = WakeGateSet(words: ["a", "b"], thresholds: [0.5, 0.5], patience: 2, cooldownSec: 2)
        XCTAssertNil(g.feed([0.9, 0.01], now: 0))
        // b's run starts here; a's carries on and completes.
        XCTAssertEqual(0, g.feed([0.9, 0.9], now: 80))
        // a's win forgot b's run: b needs two chunks again.
        XCTAssertNil(g.feed([0.01, 0.9], now: 160))
        XCTAssertEqual(1, g.feed([0.01, 0.9], now: 240))
        g.quietFor(now: 300)
        XCTAssertNil(g.feed([0.9, 0.9], now: 400))
        XCTAssertNil(g.feed([0.9, 0.9], now: 480))
        XCTAssertTrue(g.gates.allSatisfy { $0.inCooldown(now: 2_000) })
        XCTAssertFalse(g.gates.contains { $0.inCooldown(now: 2_300) })
    }

    /// A drop-in's "hang up": one chunk over a word's own threshold, any word.
    func testInACallAnyWordOverItsOwnThresholdIsHeard() {
        let g = WakeGateSet(words: ["hey_jarvis", "porch_cat", "hey_house"], thresholds: [0.5, 0.95, 0.5])
        XCTAssertNil(g.heard([0.4, 0.9, 0.3]))
        XCTAssertEqual(2, g.heard([0.4, 0.9, 0.6]))
        XCTAssertEqual(0, g.heard([0.6, 0.99, 0.6]))
        XCTAssertNil(g.heard([]))
    }

    // MARK: - which word fired

    func testTheWakeWordGoesWithAWakeWordCaptureOnly() {
        let wake = RoomProtocol.utteranceStart(trigger: "wake_word", utt: 4, backlogMS: 0, wakeMS: 410, wakeScore: 0.91, wakeWord: "hey_jarvis")
        XCTAssertEqual(.string("hey_jarvis"), wake["wake_word"])
        XCTAssertEqual(.double(0.91), wake["wake_score"])
        for trigger in ["followup", "barge_in", "push_to_talk"] {
            let f = RoomProtocol.utteranceStart(trigger: trigger, utt: 5, backlogMS: 0, wakeMS: nil, wakeScore: 0.9, wakeWord: "hey_jarvis")
            XCTAssertNil(f["wake_word"], trigger)
            XCTAssertNil(f["wake_score"], trigger)
        }
        XCTAssertNil(RoomProtocol.utteranceStart(trigger: "wake_word", utt: 6, backlogMS: 0, wakeMS: 1)["wake_word"])
        XCTAssertNil(RoomProtocol.utteranceStart(trigger: "wake_word", utt: 7, backlogMS: 0, wakeMS: 1, wakeWord: "../x")["wake_word"])
    }

    // MARK: - what the room keeps

    func testTheStoreKeepsTheListOnlyWhileItLeadsWithTheMainWord() {
        for store in [RoomStore.inMemory() as any RoomKeeping, InMemoryRoomStore() as any RoomKeeping] {
            XCTAssertEqual([], store.wakeExtras)
            store.setWakeWord("domovoi_v1", threshold: nil)
            store.setWakeWords([RoomWakeWord(slug: "domovoi_v1"), RoomWakeWord(slug: "porch_cat", threshold: 0.62), RoomWakeWord(slug: "hey_jarvis")])
            XCTAssertEqual([RoomWakeWord(slug: "porch_cat", threshold: 0.62), RoomWakeWord(slug: "hey_jarvis")], store.wakeExtras)
            // A later one-word push of another main word: the list is not used.
            store.setWakeWord("hey_house", threshold: nil)
            XCTAssertEqual([], store.wakeExtras)
            store.setWakeWord("domovoi_v1", threshold: nil)
            XCTAssertEqual(2, store.wakeExtras.count)
            // One word keeps none.
            store.setWakeWords([RoomWakeWord(slug: "domovoi_v1")])
            XCTAssertEqual([], store.wakeExtras)
        }
    }

    func testANewRoomForgetsTheList() {
        let store = RoomStore.inMemory()
        let kitchen = RoomEnrolment(coreURL: "http://192.168.1.20:6370", fingerprint: "SHA256:a", roomID: "kitchen", roomLabel: "Kitchen")
        XCTAssertTrue(store.enrol(kitchen))
        store.setWakeWords([RoomWakeWord(slug: "hey_jarvis"), RoomWakeWord(slug: "porch_cat")])
        XCTAssertEqual(1, store.wakeExtras.count)
        XCTAssertTrue(store.enrol(RoomEnrolment(coreURL: "http://192.168.1.20:6370", fingerprint: "SHA256:a", roomID: "study", roomLabel: "Study")))
        XCTAssertEqual([], store.wakeExtras)
    }

    // MARK: - one front end, N classifiers

    final class CountingFront: WakeFrontEnd {
        var mels: [Int] = []
        var embeddings = 0

        func melspectrogram(_ samples: [Float]) throws -> [[Float]] {
            mels.append(samples.count)
            let frames = Int((Double(samples.count) / 160 - 3).rounded(.up))
            return Array(repeating: Array(repeating: Float(0), count: 32), count: max(frames, 0))
        }

        func embedding(_ window: [[Float]]) throws -> [Float] {
            embeddings += 1
            return Array(repeating: Float(embeddings), count: 96)
        }
    }

    final class FixedClassifier: WakeClassifier {
        let inputFrames: Int
        let answer: Float
        var runs = 0
        var lastNewest: Float = -1

        init(_ answer: Float, frames: Int = 16) {
            self.answer = answer
            self.inputFrames = frames
        }

        func score(_ features: [[Float]]) throws -> Float {
            runs += 1
            XCTAssertEqual(inputFrames, features.count)
            lastNewest = features.last?.first ?? -1
            return answer
        }
    }

    private func zeros(_ n: Int) -> [[Float]] {
        Array(repeating: Array(repeating: Float(0), count: 96), count: n)
    }

    func testSeveralWordsShareOneFrontEnd() throws {
        let front = CountingFront()
        let words = [FixedClassifier(0.1), FixedClassifier(0.8, frames: 28), FixedClassifier(0.3)]
        let d = try WakeDetector(frontEnd: front, classifiers: words, seed: zeros(41))
        let chunk = [Int16](repeating: 0, count: WakeDetector.chunk)
        let scores = try (1...7).map { _ in try XCTUnwrap(d.acceptAll(chunk)) }
        // The five warm-up chunks are silent for every word, then each word's own.
        XCTAssertEqual(Array(repeating: [Float](repeating: 0, count: 3), count: 5), Array(scores.prefix(5)))
        XCTAssertEqual([0.1, 0.8, 0.3], scores[5])
        XCTAssertEqual([0.1, 0.8, 0.3], scores[6])
        // The melspectrogram and the embedding ran once per chunk, not per word.
        XCTAssertEqual(7, front.mels.count)
        XCTAssertEqual(7, front.embeddings)
        // Every classifier ran every chunk, on the same newest embedding.
        XCTAssertEqual([7, 7, 7], words.map(\.runs))
        XCTAssertEqual([7, 7, 7], words.map(\.lastNewest))
        XCTAssertEqual(7, d.chunksSeen)
        // The single-word view is the main word's.
        XCTAssertEqual(0.1, try d.accept(chunk))
    }

    func testTheNoiseHistoryIsComputedOnceForAllTheWords() throws {
        let front = CountingFront()
        let words = [FixedClassifier(0.5), FixedClassifier(0.5)]
        _ = try WakeDetector(frontEnd: front, classifiers: words)
        XCTAssertEqual([64_000], front.mels)
        XCTAssertEqual([0, 0], words.map(\.runs), "the noise seeds the front end's history, no word runs on it")
        XCTAssertThrowsError(try WakeDetector(frontEnd: front, classifiers: []))
    }

    func testTheOnnxEarsScoreEveryLoadedWordAndNameThem() throws {
        let front = CountingFront()
        let detector = try WakeDetector(frontEnd: front, classifiers: [FixedClassifier(0.2), FixedClassifier(0.7)], seed: zeros(41))
        let ears = OnnxRoomEars(files: nil, wake: detector, words: ["hey_jarvis", "porch_cat"], vad: RoomOnnxEarsTests.FakeVoice(answer: 0))
        XCTAssertEqual(["hey_jarvis", "porch_cat"], ears.wakeWords)
        var last: [Float]?
        for _ in 0..<24 { last = try ears.wakeScores([Int16](repeating: 0, count: RoomAudioFrame.samples)) ?? last }
        XCTAssertEqual([0.2, 0.7], last)
        XCTAssertEqual(9, ears.timing.snapshot.chunks)
        // Ears named from their files: the main word, then the extras.
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
        let files = RoomModelFiles(melspectrogram: dir, embedding: dir, vad: dir, wake: dir, wakeKey: "hey_jarvis",
                                   extras: [RoomWakeModelFile(slug: "porch_cat", file: dir), RoomWakeModelFile(slug: "hey_house", file: dir)])
        XCTAssertEqual(["hey_jarvis", "porch_cat", "hey_house"], files.wakeKeys)
        let fromFiles = OnnxRoomEars(files: files, wake: detector, vad: RoomOnnxEarsTests.FakeVoice(answer: 0))
        XCTAssertEqual(["hey_jarvis", "porch_cat"], fromFiles.wakeWords, "one name per classifier")
    }

    /// Ears that do not say (a one-word fake) give their one score as a list.
    func testOneWordEarsGiveOneScore() throws {
        let ears = FakeRoomEars()
        XCTAssertEqual([], ears.wakeWords)
        XCTAssertEqual([0.912], try ears.wakeScores([FakeRoomEars.wakeMark]))
    }
}
