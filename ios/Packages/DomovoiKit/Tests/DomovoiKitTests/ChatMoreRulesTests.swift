import Foundation
import XCTest
@testable import DomovoiKit

/// The pure half of the rest of Chat: what the server's own JSON decodes to,
/// and every rule's boundaries.
///
/// The shapes are asserted against **the server's own bodies** — each one
/// below is the shape its route writes (`chat.py`, `chat_documents.py`,
/// `chat_drafts.py`, `chat_doc_jobs.py`, `phone_voice.py`), not a shape
/// convenient to decode. Android's JVM suite specifies the rules
/// (`ChatPhotoPlanTest`, `SpeakerMarkerTest`); the decoders and the two
/// stores are specified here for the first time on either phone, because
/// Kotlin's `@Serializable` writes them.
final class ChatMoreRulesTests: XCTestCase {

    // MARK: - the parallel read

    /// `GET /api/chat/threads/{id}/messages` with one of everything on it.
    private let detailBody = """
    {"messages":[
      {"id":10,"role":"user","content":"what does the lease say about pets?","created_at":"2026-11-02T17:59:00+00:00",
       "source":"voice","speaker_person_id":4,"speaker_name":"Ana","speaker_confidence":0.41,
       "speaker_confident":false,
       "documents":[{"token":"a1","name":"lease.pdf","kind":"pdf","pages":9,"words":3100,"parts":4,
                     "scanned_pages":[3],"garbled_pages":[],"summary":"lease.pdf · 9 pages",
                     "notes":[{"code":"scanned","text":"page 3 is a scan"}],"size":820104}],
       "images":[{"token":"p1","name":"kitchen.jpg"},{"token":"p2","name":"hall.jpg"}]},
      {"id":11,"role":"assistant","content":"Cats are allowed with written consent.",
       "model":"llama3.2:latest","created_at":"2026-11-02T18:00:00+00:00",
       "doc":{"mode":"answer","found":true,
              "sources":[{"token":"a1","name":"lease.pdf","label":"p. 7","page":7,
                          "text":"The tenant may keep a cat.","match":"may keep a cat","cited":false},
                         {"token":"a1","name":"lease.pdf","label":"p. 2","page":2,
                          "text":"Written consent is required.","match":"Written consent","cited":true}],
              "notes":[{"code":"scanned","text":"page 3 is a scan"}]},
       "stats":{"window_from_id":10}},
      {"id":12,"role":"assistant","content":"May I look this up online?",
       "stats":{"model_role":"lookup",
                "lookup_card":{"id":12,"kind":"search","query":"pet clause tenancy england",
                               "destination":"Wikipedia","state":"pending"}}},
      {"id":13,"role":"assistant","content":"the draft is ready",
       "draft":{"id":4,"version":2,"kind":"cover_letter","title":"To the landlord",
                "template":"letter-block","words":210,"action":"create","change":"two sentences added"}}
    ],
    "jobs":[{"id":"j7","state":"running","waiting_for_voice":false,"part":4,"parts":8,"read":3,
             "eta_s":62.5,"estimate_s":120.0,"line":"reading part 4 of 8 · about 1 min left",
             "private":false,"thread_id":11,"question":"summarise the lease","covers":"pages 1-3",
             "elapsed_s":31.2}]}
    """

    private func detail() throws -> ChatMoreDetail {
        try JSONDecoder().decode(ChatMoreDetail.self, from: Data(detailBody.utf8))
    }

    func testTheReadKeepsEveryExtraTheTranscriptDrops() throws {
        let detail = try detail()
        XCTAssertEqual(detail.rows.count, 4)

        let question = try XCTUnwrap(detail.extras(for: 10))
        XCTAssertEqual(question.documents.first?.name, "lease.pdf")
        XCTAssertEqual(question.documents.first?.notes.first?.text, "page 3 is a scan")
        XCTAssertEqual(question.documents.first?.scannedPages, [3])
        XCTAssertEqual(question.documents.first?.size, 820_104)
        // `images` and all four speaker fields: decoded on ``ChatMessage`` and
        // dropped by `ChatMessage.live`, which is half the reason this read
        // exists.
        XCTAssertEqual(question.images.map(\.token), ["p1", "p2"])
        XCTAssertEqual(question.speakerPersonID, 4)
        XCTAssertEqual(question.speakerConfidence, 0.41)
        XCTAssertFalse(question.speakerConfident)

        let answer = try XCTUnwrap(detail.extras(for: 11))
        XCTAssertEqual(answer.doc?.sources.count, 2)
        XCTAssertEqual(answer.doc?.notes.first?.code, "scanned")
        XCTAssertNil(answer.lookupCard)

        // `stats.lookup_card`, which ``ChatStats`` does not keep at all.
        let card = try XCTUnwrap(detail.extras(for: 12)?.lookupCard)
        XCTAssertEqual(card.query, "pet clause tenancy england")
        XCTAssertEqual(card.destination, "Wikipedia")
        XCTAssertTrue(card.isPending)

        let draft = try XCTUnwrap(detail.extras(for: 13)?.draft)
        XCTAssertEqual(draft.id, 4)
        XCTAssertEqual(draft.version, 2)
        XCTAssertEqual(draft.kind, "cover_letter")
        // A cover letter saves as .docx; only a letter saves as a PDF.
        XCTAssertEqual(draft.cardFormat, "docx")
    }

    func testTheReadKeepsTheJobsArrayChatMessageListThrowsAway() throws {
        let detail = try detail()
        let job = try XCTUnwrap(detail.jobs.first)
        XCTAssertEqual(job.id, "j7")
        XCTAssertTrue(job.live)
        XCTAssertEqual(job.read, 3)
        XCTAssertEqual(job.part, 4)
        XCTAssertEqual(job.parts, 8)
        // The two fields the Kotlin model does not declare and the server
        // sends anyway.
        XCTAssertEqual(job.question, "summarise the lease")
        XCTAssertEqual(job.elapsedSeconds, 31.2)
        XCTAssertNil(job.result)
        XCTAssertNil(job.error)
    }

    /// The server omits a field on an older build; Kotlin's defaults read it
    /// and Swift's synthesised decoder would refuse the whole thread.
    func testAnOlderServersRowStillDecodes() throws {
        let body = #"{"messages":[{"id":5,"role":"user"}]}"#
        let detail = try JSONDecoder().decode(ChatMoreDetail.self, from: Data(body.utf8))
        let row = try XCTUnwrap(detail.extras(for: 5))
        XCTAssertEqual(row.content, "")
        XCTAssertTrue(row.documents.isEmpty)
        XCTAssertNil(row.doc)
        XCTAssertFalse(row.isCard)
        XCTAssertTrue(detail.jobs.isEmpty)
    }

    /// A row with no id is not a row anything can be keyed by.
    func testARowWithNoIdIsDropped() throws {
        let body = #"{"messages":[{"role":"assistant","content":"hello"}]}"#
        let detail = try JSONDecoder().decode(ChatMoreDetail.self, from: Data(body.utf8))
        XCTAssertTrue(detail.rows.isEmpty)
    }

    func testADocumentOnlyUserRowDrawsNoBubble() {
        let only = ChatMoreRowExtras(
            id: 1, role: "user", content: "   ",
            documents: [ChatMoreDocument(token: "a", name: "lease.pdf")]
        )
        XCTAssertTrue(only.documentOnly)
        let withWords = ChatMoreRowExtras(
            id: 2, role: "user", content: "what about pets?",
            documents: [ChatMoreDocument(token: "a", name: "lease.pdf")]
        )
        XCTAssertFalse(withWords.documentOnly)
    }

    func testAMarkerFallsBackToTheFileNameWhenTheServerWroteNoSummary() {
        XCTAssertEqual(
            ChatMoreDocument(token: "a", name: "lease.pdf", summary: "lease.pdf · 9 pages").markerSummary,
            "lease.pdf · 9 pages"
        )
        XCTAssertEqual(ChatMoreDocument(token: "a", name: "lease.pdf").markerSummary, "lease.pdf")
        XCTAssertEqual(ChatMoreStrings.attached("lease.pdf"), "attached lease.pdf")
    }

    // MARK: - sources

    func testCitedSourcesComeFirstAndOnePageIsOneChip() throws {
        let doc = try XCTUnwrap(detail().extras(for: 11)?.doc)
        let ordered = ChatMoreSourceRules.ordered(doc.sources)
        XCTAssertEqual(ordered.map(\.label), ["p. 2", "p. 7"])
        XCTAssertEqual(ChatMoreSourceRules.chipText(ordered[0]), "p. 2")
        XCTAssertEqual(ChatMoreSourceRules.chipText(ordered[1]), "p. 7 · read")
        XCTAssertEqual(
            ChatMoreSourceRules.chipDescription(ordered[1]),
            "source p. 7, read but not cited"
        )
    }

    /// Two passages of one page that overlap by a sentence are one chip — and
    /// the **cited** one, wherever it was in the list.
    func testOnePageCitedOnceIsOneCitedChip() {
        let read = ChatMoreSource(label: "p. 4", text: "a", cited: false)
        let cited = ChatMoreSource(label: "p. 4", text: "b", cited: true)
        let ordered = ChatMoreSourceRules.ordered([read, cited])
        XCTAssertEqual(ordered.count, 1)
        XCTAssertTrue(ordered[0].cited)
        XCTAssertEqual(ChatMoreSourceRules.chipText(ordered[0]), "p. 4")
    }

    func testTheMatchedSentenceIsFoundOrIsNothing() throws {
        let text = "The tenant may keep a cat with consent."
        let range = try XCTUnwrap(ChatMoreSourceRules.matchRange(in: text, match: "may keep a cat"))
        XCTAssertEqual(String(text[range]), "may keep a cat")
        XCTAssertNil(ChatMoreSourceRules.matchRange(in: text, match: ""))
        XCTAssertNil(ChatMoreSourceRules.matchRange(in: text, match: "   "))
        XCTAssertNil(ChatMoreSourceRules.matchRange(in: text, match: "a dog"))
    }

    func testOnlyAPdfSourceWithAPageCanDrawThePage() {
        XCTAssertTrue(ChatMoreSource(name: "lease.PDF", page: 7).rendersPage)
        XCTAssertFalse(ChatMoreSource(name: "lease.pdf", page: nil).rendersPage)
        XCTAssertFalse(ChatMoreSource(name: "rent.xlsx", page: 2).rendersPage)
    }

    // MARK: - jobs

    func testCarryOnCountsThePartsLeftAndNotThePartInHand() throws {
        let job = try XCTUnwrap(detail().jobs.first)
        // `part` is 4 and `read` is 3: a bar or a count read from `part`
        // would say one part too many.
        XCTAssertEqual(ChatMoreJobRules.left(job), 5)
        XCTAssertEqual(ChatMoreJobRules.carryOnText(5), "carry on (5 parts left)")
        XCTAssertEqual(ChatMoreJobRules.carryOnText(1), "carry on (1 part left)")
        XCTAssertEqual(ChatMoreJobRules.progress(job), 3.0 / 8.0, accuracy: 0.0001)
        XCTAssertEqual(ChatMoreJobRules.progress(ChatMoreJob(parts: 0, read: 0)), 0)
    }

    func testAnInterruptedJobSaysSoEvenWhenTheServerSentNoLine() {
        let silent = ChatMoreJob(id: "j1", state: "interrupted", parts: 8, read: 3)
        XCTAssertEqual(ChatMoreJobRules.line(silent), "this summary was interrupted — carry on?")
        let worded = ChatMoreJob(id: "j1", state: "interrupted", parts: 8, read: 3, line: "stopped (AI is off)")
        XCTAssertEqual(ChatMoreJobRules.line(worded), "stopped (AI is off)")
    }

    func testEachJobStateIsDrawnInItsOwnPlace() {
        let jobs = [
            ChatMoreJob(id: "a", state: "running"),
            ChatMoreJob(id: "b", state: "reducing"),
            ChatMoreJob(id: "c", state: "stopped"),
            ChatMoreJob(id: "d", state: "failed"),
            ChatMoreJob(id: "e", state: "interrupted"),
            ChatMoreJob(id: "f", state: "done"),
        ]
        XCTAssertEqual(ChatMoreJobRules.live(jobs).map(\.id), ["a", "b"])
        XCTAssertEqual(ChatMoreJobRules.stopped(jobs).map(\.id), ["c", "d"])
        XCTAssertEqual(ChatMoreJobRules.interrupted(jobs).map(\.id), ["e"])
    }

    /// "carry on" is offered on the job's **newest** reply only, so a thread
    /// that summarised the same document twice grows one button.
    func testCarryOnIsOfferedOnTheNewestReplyOfThatJob() {
        let job = ChatMoreJob(id: "j7", state: "stopped", parts: 8, read: 3)
        let rows = [
            ChatMoreRowExtras(id: 20, role: "assistant",
                              doc: ChatMoreDocReply(job: ChatMoreJobRef(id: "j7"))),
            ChatMoreRowExtras(id: 31, role: "assistant",
                              doc: ChatMoreDocReply(job: ChatMoreJobRef(id: "j7"))),
            ChatMoreRowExtras(id: 40, role: "assistant"),
        ]
        XCTAssertEqual(ChatMoreJobRules.carryOnRow(for: job, rows: rows), 31)
        XCTAssertNil(ChatMoreJobRules.carryOnRow(for: ChatMoreJob(id: "j9"), rows: rows))
    }

    // MARK: - lookups and the chip

    func testACardShowsItsAnswerBeforeTheStreamSaysAnything() {
        let card = ChatMoreLookupCard(kind: "search", query: "lentils", destination: "Wikipedia")
        XCTAssertEqual(card.answered("once").state, "allowed_once")
        XCTAssertEqual(card.answered("chat").state, "allowed_chat")
        XCTAssertEqual(card.answered("no").state, "declined")
        XCTAssertFalse(card.answered("no").isPending)
        XCTAssertEqual(ChatMoreStrings.cardState("allowed_once"), "allowed once")
        XCTAssertEqual(ChatMoreStrings.cardState("allowed_chat"), "allowed in this chat")
        XCTAssertEqual(ChatMoreStrings.cardState("declined"), "not looked up")
        XCTAssertEqual(ChatMoreStrings.cardState("asked_again"), "asked again below")
    }

    func testASearchIsQuotedAndAPageIsNot() {
        let search = ChatMoreLookupCard(kind: "search", query: "pet clause", destination: "Wikipedia")
        let page = ChatMoreLookupCard(kind: "page", query: "https://example.org/pets", destination: "example.org")
        XCTAssertEqual(ChatMoreLookupRules.title(search), "May I look this up online?")
        XCTAssertEqual(ChatMoreLookupRules.title(page), "May I read this page?")
        XCTAssertEqual(ChatMoreLookupRules.queryText(search), "\u{201C}pet clause\u{201D}")
        XCTAssertEqual(ChatMoreLookupRules.queryText(page), "https://example.org/pets")
        XCTAssertEqual(ChatMoreStrings.goesTo("Wikipedia"), "goes to Wikipedia")
    }

    func testTheChipReadsTheHouseholdAndTheThread() {
        XCTAssertEqual(
            ChatMoreInternetChip.words(policy: "never", alwaysAllow: false, chatAllowed: true).label,
            "internet off"
        )
        XCTAssertFalse(ChatMoreInternetChip.words(policy: "never", alwaysAllow: true, chatAllowed: true).on)
        XCTAssertEqual(
            ChatMoreInternetChip.words(policy: "ask", alwaysAllow: true, chatAllowed: false).label,
            "internet always on"
        )
        XCTAssertEqual(
            ChatMoreInternetChip.words(policy: "ask", alwaysAllow: false, chatAllowed: true).label,
            "internet on for this chat"
        )
        XCTAssertEqual(
            ChatMoreInternetChip.words(policy: "", alwaysAllow: false, chatAllowed: false).label,
            "internet off"
        )
    }

    /// A chip a tap cannot change is not a switch — the no-misleading-controls
    /// rule in one assertion.
    func testTheChipIsNotTappableWhenTheHouseHasAlreadyDecided() {
        XCTAssertTrue(ChatMoreInternetChip.tappable(policy: "ask", alwaysAllow: false))
        XCTAssertFalse(ChatMoreInternetChip.tappable(policy: "never", alwaysAllow: false))
        XCTAssertFalse(ChatMoreInternetChip.tappable(policy: "ask", alwaysAllow: true))
    }

    func testTheHouseholdsAnswerDecodesBooleanOrString() throws {
        let asBool = try JSONDecoder().decode(
            ChatMoreInternet.self,
            from: Data(#"{"internet":"ask","always_allow":true,"lookups":true}"#.utf8)
        )
        XCTAssertTrue(asBool.alwaysAllow)
        // An older server, and the shape Android's own reader expects.
        let asString = try JSONDecoder().decode(
            ChatMoreInternet.self,
            from: Data(#"{"internet":"ask","always_allow":"true"}"#.utf8)
        )
        XCTAssertTrue(asString.alwaysAllow)
        XCTAssertTrue(asString.lookups)
        let missing = try JSONDecoder().decode(ChatMoreInternet.self, from: Data("{}".utf8))
        XCTAssertFalse(missing.alwaysAllow)
        XCTAssertEqual(missing.internet, "")
    }

    // MARK: - heads-up cards

    private let headsUpBody = """
    {"messages":[{"id":21,"role":"assistant","content":"this is a long document",
      "doc":{"mode":"heads_up","found":true,
             "heads_up":{"kind":"long","text":"This will take a while to read properly.",
                         "options":[{"id":"full","label":"read all of it"},
                                    {"id":"skim","label":"skim it"},
                                    {"id":"choose_another","label":"choose another picture"}],
                         "never_label":null,"chosen":null}}}]}
    """

    func testAHeadsUpCardIsTheReplyAndDropsTheOptionThisAppCannotCarryOut() throws {
        let detail = try JSONDecoder().decode(ChatMoreDetail.self, from: Data(headsUpBody.utf8))
        let row = try XCTUnwrap(detail.extras(for: 21))
        XCTAssertTrue(row.isCard)
        let card = try XCTUnwrap(row.headsUp)
        XCTAssertEqual(card.text, "This will take a while to read properly.")
        XCTAssertEqual(card.options.count, 3)
        // `choose_another` re-opens the picture picker, which this app has
        // not got: an option it cannot carry out is not offered.
        XCTAssertEqual(card.offerable.map(\.id), ["full", "skim"])
        XCTAssertEqual(card.neverText, "don't warn me about this again")
        XCTAssertNil(card.chosenText)
    }

    func testAnAnsweredCardShowsThePicksLabelAndNoButtons() {
        let card = ChatMoreHeadsUp(
            kind: "long",
            text: "slow",
            options: [ChatMoreHeadsUpOption(id: "skim", label: "skim it")],
            chosen: "skim"
        )
        XCTAssertEqual(card.chosenText, "skim it")
        // An option the server picked that this build does not know by label
        // still shows, as the id.
        XCTAssertEqual(
            ChatMoreHeadsUp(kind: "long", text: "slow", chosen: "something_new").chosenText,
            "something_new"
        )
    }

    func testTheServersOwnNeverLabelWins() {
        XCTAssertEqual(
            ChatMoreHeadsUp(kind: "calc", text: "sums", neverLabel: "stop telling me").neverText,
            "stop telling me"
        )
        XCTAssertEqual(
            ChatMoreHeadsUp(kind: "calc", text: "sums", neverLabel: "   ").neverText,
            "don't warn me about this again"
        )
    }

    func testSilencingAKindNamesItInWordsAndIsKeptOnThisPhone() {
        let place = SavedStateBox()
        let store = ChatMoreHeadsUpStore(place: place)
        XCTAssertFalse(store.silenced("scanned"))
        store.silence("scanned")
        XCTAssertTrue(store.silenced("scanned"))
        XCTAssertFalse(store.silenced("calc"))
        XCTAssertEqual(
            ChatMoreStrings.silenced("scanned"),
            "no more heads-ups about scanned pages on this phone"
        )
        // A kind this build has no words for still reads as a sentence.
        XCTAssertEqual(
            ChatMoreStrings.silenced("something-new"),
            "no more heads-ups about this on this phone"
        )
        // And it survives the launch: a second store on the same bytes.
        let second = ChatMoreHeadsUpStore(place: place)
        XCTAssertTrue(second.silenced("scanned"))
        XCTAssertEqual(second.prefs.skip, ["scanned"])
        XCTAssertFalse(second.prefs.off)
    }

    func testHeadsUpOffSilencesEveryKind() {
        let store = ChatMoreHeadsUpStore(place: SavedStateBox())
        store.setOff(true)
        XCTAssertTrue(store.silenced("anything"))
    }

    /// A blob this build cannot read starts blank rather than throwing, as
    /// the pins do.
    func testAnUnreadableBlobStartsBlank() {
        let store = ChatMoreHeadsUpStore(place: SavedStateBox(Data("not json".utf8)))
        XCTAssertFalse(store.prefs.off)
        XCTAssertTrue(store.prefs.skip.isEmpty)
    }

    func testThePickBodyCarriesTheSortedSkipList() throws {
        let prefs = ChatMoreHeadsUpPrefs(off: false, skip: ["scanned", "calc", "long"])
        let body = ChatPath.headsUpBody(messageID: 21, option: "skim", prefs: prefs)
        let object = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        XCTAssertEqual(object["message_id"] as? Int, 21)
        XCTAssertEqual(object["option"] as? String, "skim")
        let headsUp = try XCTUnwrap(object["heads_up"] as? [String: Any])
        XCTAssertEqual(headsUp["off"] as? Bool, false)
        XCTAssertEqual(headsUp["skip"] as? [String], ["calc", "long", "scanned"])
    }

    // MARK: - the speaker marker

    func testWhoDomovoiHeardIsSaidFourWays() throws {
        // Nothing for a typed row, or for a reply.
        XCTAssertNil(ChatMoreSpeaker.mark(source: "typed", role: "user", personID: 4, name: "Ana", confident: true))
        XCTAssertNil(ChatMoreSpeaker.mark(source: "voice", role: "assistant", personID: 4, name: "Ana", confident: true))

        let unknown = try XCTUnwrap(
            ChatMoreSpeaker.mark(source: "voice", role: "user", personID: nil, name: nil, confident: false)
        )
        XCTAssertEqual(unknown.label, "voice not recognized")
        XCTAssertEqual(unknown.description, "spoken; Domovoi didn't recognize the voice")
        XCTAssertTrue(unknown.soft)
        XCTAssertNil(unknown.name)

        let weak = try XCTUnwrap(
            ChatMoreSpeaker.mark(source: "voice", role: "user", personID: 4, name: "Ana", confident: false)
        )
        XCTAssertEqual(weak.label, "maybe Ana")
        XCTAssertEqual(weak.description, "spoken; Domovoi thinks it was Ana, but isn't sure")
        XCTAssertTrue(weak.soft)

        let sure = try XCTUnwrap(
            ChatMoreSpeaker.mark(source: "voice", role: "user", personID: 4, name: "Ana", confident: true)
        )
        XCTAssertEqual(sure.label, "Ana")
        XCTAssertEqual(sure.description, "spoken by Ana")
        XCTAssertFalse(sure.soft)

        // A person the house knows but has no name for.
        let nameless = ChatMoreSpeaker.mark(source: "voice", role: "user", personID: 9, name: "  ", confident: true)
        XCTAssertEqual(nameless?.label, "someone it knows")
    }

    /// The branch this app never takes, and Android does not either with a
    /// null owner — ported so it is there when a preference holds one.
    func testTheNotOwnerBranchIsPortedAndIsOffWithNoOwner() {
        let withOwner = ChatMoreSpeaker.mark(
            source: "voice", role: "user", personID: 4, name: "Ana", confident: true, ownerID: "7"
        )
        XCTAssertEqual(withOwner?.description, "spoken by Ana, not this phone's owner")
        XCTAssertEqual(withOwner?.soft, true)
        let sameOwner = ChatMoreSpeaker.mark(
            source: "voice", role: "user", personID: 4, name: "Ana", confident: true, ownerID: "4"
        )
        XCTAssertEqual(sameOwner?.soft, false)
        // Nil, which is this app everywhere.
        XCTAssertEqual(
            ChatMoreSpeaker.mark(source: "voice", role: "user", personID: 4, name: "Ana", confident: true)?.soft,
            false
        )
    }

    // MARK: - pictures

    func testEveryKindIsNamedByItsBytesAndNotItsName() {
        func head(_ bytes: [UInt8]) -> Data { Data(bytes) }
        XCTAssertEqual(ChatMorePicture.sniff(head([0xFF, 0xD8, 0xFF, 0xE0])), .jpeg)
        XCTAssertEqual(
            ChatMorePicture.sniff(head([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])),
            .png
        )
        XCTAssertEqual(ChatMorePicture.sniff(Data("GIF89a…".utf8)), .gif)
        XCTAssertEqual(ChatMorePicture.sniff(Data("RIFF0000WEBPVP8 ".utf8)), .webp)
        XCTAssertEqual(ChatMorePicture.sniff(Data(Array("BM".utf8) + [UInt8](repeating: 0, count: 12))), .bmp)
        XCTAssertEqual(ChatMorePicture.sniff(head([0xFF, 0x0A])), .jpegXL)
        XCTAssertEqual(ChatMorePicture.sniff(Data("II*\u{0}".utf8)), .tiff)
        XCTAssertEqual(ChatMorePicture.sniff(Data("MM\u{0}*".utf8)), .tiff)
        XCTAssertEqual(ChatMorePicture.sniff(Data("hello, world".utf8)), .unknown)
        XCTAssertEqual(ChatMorePicture.sniff(Data()), .unknown)
    }

    /// A phone photo: `ftyp` and then the brands, major first.
    func testAnIsoBoxIsNamedByItsBrands() {
        func box(_ brands: [String], size: UInt8 = 24) -> Data {
            var bytes: [UInt8] = [0, 0, 0, size]
            bytes += Array("ftyp".utf8)
            bytes += Array(brands[0].utf8)
            bytes += [0, 0, 0, 0] // the minor version, skipped
            for brand in brands.dropFirst() { bytes += Array(brand.utf8) }
            return Data(bytes)
        }
        XCTAssertEqual(ChatMorePicture.sniff(box(["heic"])), .heic)
        XCTAssertEqual(ChatMorePicture.sniff(box(["mif1", "heic"], size: 28)), .heic)
        XCTAssertEqual(ChatMorePicture.sniff(box(["mif1"])), .heif)
        XCTAssertEqual(ChatMorePicture.sniff(box(["avif"])), .avif)
        // AVIF wins over a HEIC compatible brand, as the Kotlin orders them.
        XCTAssertEqual(ChatMorePicture.sniff(box(["heic", "avif"], size: 28)), .avif)
        XCTAssertEqual(ChatMorePicture.sniff(box(["mp42"])), .unknown)
    }

    func testTheFourKindsTheHouseTakesGoAsTheyAreUntilALimitIsPassed() {
        XCTAssertEqual(ChatMorePicture.plan(kind: .jpeg, width: 4032, height: 3024, byteCount: 3_000_000),
                       .asIs(.jpeg))
        XCTAssertEqual(ChatMorePicture.plan(kind: .heic, width: 4032, height: 3024, byteCount: 2_000_000),
                       .convert(.heic, .format))
        XCTAssertEqual(ChatMorePicture.plan(kind: .bmp, width: 10, height: 10, byteCount: 400),
                       .convert(.bmp, .format))
        // 40 MP exactly is allowed; one pixel more is not.
        XCTAssertEqual(ChatMorePicture.plan(kind: .png, width: 8000, height: 5000, byteCount: 1000),
                       .asIs(.png))
        XCTAssertEqual(ChatMorePicture.plan(kind: .png, width: 8000, height: 5001, byteCount: 1000),
                       .convert(.png, .pixels))
        XCTAssertEqual(
            ChatMorePicture.plan(kind: .jpeg, width: 100, height: 100, byteCount: 20 * 1024 * 1024),
            .asIs(.jpeg)
        )
        XCTAssertEqual(
            ChatMorePicture.plan(kind: .jpeg, width: 100, height: 100, byteCount: 20 * 1024 * 1024 + 1),
            .convert(.jpeg, .bytes)
        )
        // A size the phone could not read leaves the verdict to the server.
        XCTAssertEqual(ChatMorePicture.plan(kind: .jpeg, width: 0, height: 0, byteCount: 1000), .asIs(.jpeg))
    }

    func testAConvertedPhotoKeepsItsShapeInsideBothLimits() throws {
        let phone = try XCTUnwrap(ChatMorePicture.size(width: 4032, height: 3024))
        XCTAssertEqual(phone.width, 2560)
        // Rounded, not truncated: 3024 × (2560/4032) is 1920 exactly.
        XCTAssertEqual(phone.height, 1920)
        // Never enlarged.
        let small = try XCTUnwrap(ChatMorePicture.size(width: 800, height: 600))
        XCTAssertEqual(small.width, 800)
        XCTAssertEqual(small.height, 600)
        // A panorama is bound by its long edge.
        let panorama = try XCTUnwrap(ChatMorePicture.size(width: 20000, height: 1000))
        XCTAssertEqual(panorama.width, 2560)
        XCTAssertEqual(panorama.height, 128)
        XCTAssertLessThanOrEqual(Int64(panorama.width * panorama.height), ChatMorePictureLimits.maxPixels)
        // No size at all is nil here, where the Kotlin throws.
        XCTAssertNil(ChatMorePicture.size(width: 0, height: 100))
    }

    func testTheRefusalsSayWhichPhotoAndWhatToDo() {
        XCTAssertTrue(ChatMorePicture.unreadableText(.unknown).hasPrefix("That file isn't a picture"))
        XCTAssertEqual(
            ChatMorePicture.unreadableText(.jpeg),
            "This JPEG picture couldn't be opened on this phone; it may be damaged."
        )
        XCTAssertEqual(
            ChatMorePicture.unreadableText(.bmp),
            "This BMP picture couldn't be opened on this phone; it may be damaged."
        )
        // HEIC: this phone decodes it, so one file is the trouble.
        XCTAssertEqual(
            ChatMorePicture.unreadableText(.heic),
            "This HEIC photo couldn't be opened on this phone; it may be damaged. "
            + "Save it as a JPEG and send it again."
        )
        // JPEG XL: no Apple decoder reads it, so the kind is.
        XCTAssertEqual(
            ChatMorePicture.unreadableText(.jpegXL),
            "This photo is in JPEG XL format, which this phone can't convert. "
            + "Save it as a JPEG and send it again."
        )
    }

    /// The server's own sentence first, as `uploadRefusalText` prefers it.
    func testAnUploadRefusalPrefersTheServersSentence() {
        XCTAssertEqual(
            ChatMorePicture.uploadRefusalText(
                status: 415,
                body: #"{"detail":"HEIC pictures are not read here","code":"unsupported_format","format":"HEIC"}"#
            ),
            "HEIC pictures are not read here"
        )
        // A coded refusal with no sentence falls back by status.
        XCTAssertEqual(
            ChatMorePicture.uploadRefusalText(status: 413, body: #"{"code":"file_too_large","max_mb":20}"#),
            "That picture is too large for the chat."
        )
        XCTAssertEqual(
            ChatMorePicture.uploadRefusalText(status: 415, body: "not json"),
            "That file isn't a picture the chat can read. Send a JPEG, PNG, GIF, WebP or BMP."
        )
        // A code this app does not know about is not spoken for.
        XCTAssertNil(ChatMorePicture.uploadRefusalText(status: 500, body: #"{"detail":"boom"}"#))
    }

    // MARK: - the voice turn

    func testTheCoresAnswerDecodesWithItsRowsAndItsSpeaker() throws {
        let body = """
        {"heard":true,"reason":null,"transcript":"what's for dinner",
         "reply":"There are lentils in the cupboard.","audio":{"mime":"audio/wav","b64":"UklGRg=="},
         "speaker":{"person_id":4,"name":"Ana","confidence":0.92,"confident":true},
         "expect_followup":true,"actions":[{"type":"play","items":[]}],
         "matched_handler":"kitchen","matched_path":"/v1/intent","session_id":"s1","room_id":"kitchen",
         "messages":[{"id":30,"role":"user","content":"what's for dinner","source":"voice",
                      "speaker_name":"Ana","speaker_confident":true},
                     {"id":31,"role":"assistant","content":"There are lentils in the cupboard.",
                      "model":"llama3.2:latest"}],
         "error":null}
        """
        let reply = try JSONDecoder().decode(ChatMoreVoiceReply.self, from: Data(body.utf8))
        XCTAssertTrue(reply.heard)
        XCTAssertEqual(reply.transcript, "what's for dinner")
        XCTAssertEqual(reply.speaker?.personID, 4)
        XCTAssertTrue(reply.speaker?.confident ?? false)
        XCTAssertTrue(reply.expectFollowup)
        XCTAssertEqual(reply.messages.count, 2)
        XCTAssertEqual(reply.messages.first?.source, "voice")
        XCTAssertEqual(reply.messages.last?.model, "llama3.2:latest")
        XCTAssertEqual(reply.playback, .serverAudio)
        XCTAssertNotNil(reply.wav)
    }

    /// A clip too short comes back **200** with `heard: false` and no rows.
    func testATooShortClipIsAnAnswerAndNotARefusal() throws {
        let reply = try JSONDecoder().decode(
            ChatMoreVoiceReply.self,
            from: Data(#"{"heard":false,"reason":"too_short"}"#.utf8)
        )
        XCTAssertFalse(reply.heard)
        XCTAssertEqual(reply.reason, "too_short")
        XCTAssertTrue(reply.messages.isEmpty)
        XCTAssertEqual(reply.playback, .none)
        XCTAssertNil(reply.wav)
    }

    /// The server's `detail` is what a person reads; the fallbacks are for a
    /// server too old to send one.
    func testARefusalPrefersTheServersSentence() {
        let core = ChatMoreVoiceRefusal.of(
            status: 413,
            body: #"{"detail":"a voice turn is at most 30 seconds","code":"clip_too_long"}"#
        )
        XCTAssertEqual(core.text, "a voice turn is at most 30 seconds")
        XCTAssertEqual(core.code, "clip_too_long")
        XCTAssertEqual(
            ChatMoreVoiceRefusal.of(status: 413, body: "{}").text,
            "That was too long — a voice message is at most 30 seconds."
        )
        XCTAssertEqual(
            ChatMoreVoiceRefusal.of(status: 409, body: "").text,
            "Domovoi is still answering the last one."
        )
        XCTAssertEqual(
            ChatMoreVoiceRefusal.of(status: 502, body: "<html>").text,
            "Domovoi couldn't take that (502)."
        )
    }

    /// A 401 is the pairing question and not a turn's refusal: the client has
    /// already raised it, and a sentence under the talk button would be the
    /// second answer to one event.
    func testAPairingRefusalIsNotWordedAsATurnsRefusal() {
        let pairing = DomovoiError.refused(
            ApiFailure(status: 401, message: "401: pair me", deviceTokenRequired: true)
        )
        XCTAssertNil(ChatMoreVoiceRefusal.of(pairing))
        XCTAssertNil(ChatMoreVoiceRefusal.of(.unreachable("no route")))
        let tooLong = DomovoiError.httpError(
            ApiFailure(status: 413, message: "413", body: #"{"detail":"too long"}"#)
        )
        XCTAssertEqual(ChatMoreVoiceRefusal.of(tooLong)?.text, "too long")
    }

    func testTheReplysWavIsWalkedChunkByChunk() throws {
        let pcm = Data([0x01, 0x00, 0xFF, 0x7F, 0x00, 0x80, 0x10, 0x00])
        let wav = Self.wav(pcm: pcm, rate: 16_000, channels: 1)
        let parsed = try XCTUnwrap(ChatMoreWav.parse(wav))
        XCTAssertEqual(parsed.sampleRate, 16_000)
        XCTAssertEqual(parsed.channels, 1)
        XCTAssertEqual(parsed.data, pcm)
        XCTAssertEqual(parsed.seconds, 4.0 / 16_000.0, accuracy: 0.000001)

        // A `LIST` chunk before the data, which some writers add.
        let withList = Self.wav(pcm: pcm, rate: 22_050, channels: 2, extraChunk: ("LIST", Data("INFOhey!".utf8)))
        let listed = try XCTUnwrap(ChatMoreWav.parse(withList))
        XCTAssertEqual(listed.sampleRate, 22_050)
        XCTAssertEqual(listed.channels, 2)
        XCTAssertEqual(listed.data, pcm)

        // A streamed writer's placeholder size, bigger than the file: read to
        // the end rather than refused.
        let placeholder = Self.wav(pcm: pcm, rate: 16_000, channels: 1, dataSize: 0x7FFF_FFF0)
        XCTAssertEqual(ChatMoreWav.parse(placeholder)?.data, pcm)
        // A size with its top bit set is a **negative** size in the format's
        // own signed field, and is refused rather than clamped — Kotlin's
        // `if (size < 0) return null` (`ReplyPlayer.kt:47`). It is the one
        // case where "bigger than the file" and "nonsense" look alike, and
        // the sane answer is to play nothing.
        XCTAssertNil(ChatMoreWav.parse(Self.wav(pcm: pcm, rate: 16_000, channels: 1, dataSize: 0xFFFF_FFF0)))

        // Not a WAV at all, and a WAV that is not PCM16.
        XCTAssertNil(ChatMoreWav.parse(Data("hello".utf8)))
        XCTAssertNil(ChatMoreWav.parse(Self.wav(pcm: pcm, rate: 16_000, channels: 1, bits: 24)))
        XCTAssertNil(ChatMoreWav.parse(Self.wav(pcm: pcm, rate: 0, channels: 1)))
        XCTAssertNil(ChatMoreWav.parse(Self.wav(pcm: Data(), rate: 16_000, channels: 1)))
    }

    /// A RIFF/WAVE file, built here so the parser is tested against bytes and
    /// not against a second copy of itself.
    private static func wav(
        pcm: Data,
        rate: Int,
        channels: Int,
        bits: Int = 16,
        format: Int = 1,
        dataSize: UInt32? = nil,
        extraChunk: (String, Data)? = nil
    ) -> Data {
        func u32(_ value: UInt32) -> [UInt8] {
            [UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF), UInt8((value >> 16) & 0xFF), UInt8((value >> 24) & 0xFF)]
        }
        func u16(_ value: Int) -> [UInt8] { [UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF)] }
        var body: [UInt8] = Array("WAVE".utf8)
        body += Array("fmt ".utf8) + u32(16)
        body += u16(format) + u16(channels) + u32(UInt32(rate))
        body += u32(UInt32(rate * channels * bits / 8)) + u16(channels * bits / 8) + u16(bits)
        if let extra = extraChunk {
            body += Array(extra.0.utf8) + u32(UInt32(extra.1.count)) + [UInt8](extra.1)
            if extra.1.count % 2 == 1 { body += [0] }
        }
        body += Array("data".utf8) + u32(dataSize ?? UInt32(pcm.count)) + [UInt8](pcm)
        return Data(Array("RIFF".utf8) + u32(UInt32(body.count)) + body)
    }

    // MARK: - the talk button

    func testEachPhaseSaysItsOwnThing() {
        XCTAssertEqual(
            ChatMoreVoiceRules.hint(phase: .recording, followup: false, note: nil),
            "listening — release to send, slide away to cancel"
        )
        XCTAssertEqual(ChatMoreVoiceRules.hint(phase: .cancelArmed, followup: false, note: nil), "release to cancel")
        XCTAssertEqual(ChatMoreVoiceRules.hint(phase: .sending, followup: false, note: nil), "thinking…")
        XCTAssertEqual(ChatMoreVoiceRules.hint(phase: .speaking, followup: false, note: nil), "press to stop")
        XCTAssertEqual(ChatMoreVoiceRules.hint(phase: .idle, followup: false, note: nil), "hold to talk")
        XCTAssertEqual(
            ChatMoreVoiceRules.hint(phase: .idle, followup: true, note: nil),
            "Domovoi asked you something — hold to answer"
        )
        // The last turn's own answer outlives the invitation.
        XCTAssertEqual(
            ChatMoreVoiceRules.hint(phase: .idle, followup: true, note: "a voice turn is at most 30 seconds"),
            "a voice turn is at most 30 seconds"
        )
        // …but a note only shows where there is nothing better to say.
        XCTAssertEqual(
            ChatMoreVoiceRules.hint(phase: .recording, followup: false, note: "stale"),
            "listening — release to send, slide away to cancel"
        )
    }

    /// The defect this machine has already had: a cancelled touch read as
    /// neither a hold nor a release, so the recording ran to its cap and was
    /// sent.
    func testACancelledTouchIsACancelAndNotASend() {
        XCTAssertEqual(ChatMoreVoiceRules.holdStep(present: true, pressed: true, consumed: false), .hold)
        XCTAssertEqual(ChatMoreVoiceRules.holdStep(present: true, pressed: false, consumed: false), .release)
        XCTAssertEqual(ChatMoreVoiceRules.holdStep(present: false, pressed: false, consumed: false), .cancel)
        XCTAssertEqual(ChatMoreVoiceRules.holdStep(present: true, pressed: false, consumed: true), .cancel)
    }

    func testAFingerIsOffTheButtonOnlyPastTheMargin() {
        XCTAssertFalse(ChatMoreVoiceRules.slidOff(x: 10, y: 10, width: 100, height: 100, margin: 20))
        XCTAssertFalse(ChatMoreVoiceRules.slidOff(x: -20, y: 50, width: 100, height: 100, margin: 20))
        XCTAssertTrue(ChatMoreVoiceRules.slidOff(x: -21, y: 50, width: 100, height: 100, margin: 20))
        XCTAssertTrue(ChatMoreVoiceRules.slidOff(x: 50, y: 121, width: 100, height: 100, margin: 20))
    }

    func testAPressWhileAnswerSaysSoAndStartsNothing() {
        XCTAssertFalse(ChatMoreVoiceRules.press(.sending).starts)
        XCTAssertEqual(ChatMoreVoiceRules.press(.sending).note, "still answering the last one")
        XCTAssertFalse(ChatMoreVoiceRules.press(.recording).starts)
        XCTAssertNil(ChatMoreVoiceRules.press(.recording).note)
        XCTAssertTrue(ChatMoreVoiceRules.press(.idle).starts)
        // A press while Domovoi speaks stops the reply and starts a turn.
        XCTAssertTrue(ChatMoreVoiceRules.press(.speaking).starts)
    }

    func testATapIsNotSpeechAndSaysSoUnlessItOnlyStoppedAReply() {
        let tap = ChatMoreVoiceRules.recorded(seconds: 0.2, interrupted: false)
        XCTAssertFalse(tap.sends)
        XCTAssertEqual(tap.note, "hold the button while you talk")
        // 0.3 s exactly is the core's floor and is sent.
        XCTAssertTrue(ChatMoreVoiceRules.recorded(seconds: 0.3, interrupted: false).sends)
        let stopped = ChatMoreVoiceRules.recorded(seconds: 0.1, interrupted: true)
        XCTAssertFalse(stopped.sends)
        XCTAssertNil(stopped.note)
    }

    func testTheMicIsOfferedOnlyWithAnEmptyComposer() {
        XCTAssertTrue(ChatMoreVoiceRules.micOffered(typed: ""))
        XCTAssertTrue(ChatMoreVoiceRules.micOffered(typed: "   \n"))
        XCTAssertFalse(ChatMoreVoiceRules.micOffered(typed: "hello"))
    }

    func testTheLatestExchangeIsAReplyAndWhatItAnswered() {
        let rows = ["q1", "a1", "q2", "a2"]
        XCTAssertEqual(ChatMoreVoiceRules.latestExchange(rows) { $0.hasPrefix("q") }, ["q2", "a2"])
        XCTAssertEqual(ChatMoreVoiceRules.latestExchange(["q1"]) { $0.hasPrefix("q") }, ["q1"])
        // A question with no answer yet is on its own.
        XCTAssertEqual(ChatMoreVoiceRules.latestExchange(["a1", "q2"]) { $0.hasPrefix("q") }, ["q2"])
        XCTAssertTrue(ChatMoreVoiceRules.latestExchange([String]()) { _ in true }.isEmpty)
    }

    func testTheLevelRingReadsRmsAndIsBounded() {
        XCTAssertEqual(ChatMoreVoiceRules.level([]), 0)
        XCTAssertEqual(ChatMoreVoiceRules.level([0, 0, 0]), 0)
        XCTAssertEqual(ChatMoreVoiceRules.level([Int16.max, Int16.min]), 1, accuracy: 0.001)
        let quiet = ChatMoreVoiceRules.level([1000, -1000])
        XCTAssertGreaterThan(quiet, 0.1)
        XCTAssertLessThan(quiet, 0.2)
    }

    func testSecondsAndTheCap() {
        // 16 kHz mono PCM16: two bytes a sample.
        XCTAssertEqual(ChatMoreVoiceRules.seconds(ofPCM: 32_000), 1.0, accuracy: 0.0001)
        XCTAssertFalse(ChatMoreVoiceRules.atCap(seconds: 29.9))
        XCTAssertTrue(ChatMoreVoiceRules.atCap(seconds: 30.0))
    }

    // MARK: - the multipart body

    func testTheTurnsBodyIsOneWellFormedMultipart() throws {
        var form = ChatMoreMultipart(boundary: "BOUND")
        form.add(name: "audio", filename: "turn.pcm", bytes: Data([0, 1, 2]))
        form.add(name: "phone_id", value: "ios-1")
        let text = try XCTUnwrap(String(data: form.body, encoding: .isoLatin1))
        XCTAssertEqual(form.contentType, "multipart/form-data; boundary=BOUND")
        XCTAssertTrue(text.hasPrefix("--BOUND\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"turn.pcm\"\r\n"))
        XCTAssertTrue(text.contains("Content-Type: application/octet-stream\r\n\r\n"))
        XCTAssertTrue(text.contains("--BOUND\r\nContent-Disposition: form-data; name=\"phone_id\"\r\n\r\nios-1\r\n"))
        XCTAssertTrue(text.hasSuffix("--BOUND--\r\n"))
    }

    /// A file name with a quotation mark or a line break in it would end the
    /// header early, which is how a crafted name becomes an extra part.
    func testAHeaderCannotBeEndedByAFileName() throws {
        var form = ChatMoreMultipart(boundary: "B")
        form.add(name: "audio", filename: "a\"\r\nX-Evil: 1\".pcm", bytes: Data([9]))
        let text = try XCTUnwrap(String(data: form.body, encoding: .isoLatin1))
        // The words are still in the file name — that is what the caller
        // asked to send. What matters is that they are not a **header**: the
        // quote is escaped and the CR LF dropped, so there is no line of its
        // own for them to start.
        XCTAssertFalse(text.contains("\r\nX-Evil: 1"))
        XCTAssertTrue(text.contains("filename=\"a\\\"X-Evil: 1\\\".pcm\""))
        XCTAssertEqual(text.components(separatedBy: "Content-Disposition").count - 1, 1)
    }

    func testEachBodyIsFresh() {
        XCTAssertNotEqual(ChatMoreMultipart.freshBoundary(), ChatMoreMultipart.freshBoundary())
    }

    // MARK: - report a message

    func testAReportHoldsExactlyWhatTheSheetShows() throws {
        let answered = Date(timeIntervalSince1970: 1_762_106_645)
        let made = Date(timeIntervalSince1970: 1_762_106_700)
        let report = try XCTUnwrap(ChatMoreReport.draft(
            question: "what does the lease say about pets?",
            spoken: false,
            answer: "Cats are allowed with written consent.",
            answeredAt: answered,
            model: "llama3.2:latest",
            appVersion: "1.0.0",
            now: made
        )).with(note: "it cited the wrong page")
        let text = report.asText()
        XCTAssertTrue(text.hasPrefix("Domovoi on the home server: a reported reply\n"))
        XCTAssertTrue(text.contains("Model: llama3.2:latest, on the home server\n"))
        XCTAssertTrue(text.contains("App: 1.0.0\n"))
        XCTAssertTrue(text.contains("Question:\nwhat does the lease say about pets?\n"))
        XCTAssertTrue(text.contains("Reply (written \(ChatMoreReport.utc(answered))):\n"))
        XCTAssertTrue(text.contains("Note:\nit cited the wrong page"))
        XCTAssertTrue(text.hasSuffix("\n"))
        XCTAssertFalse(text.hasSuffix("\n\n"))
        // Nothing of the conversation but the pair, and nothing of the phone.
        XCTAssertFalse(text.contains("device"))
        XCTAssertEqual(report.fields.map(\.label), ["your question", "the reply", "written", "app"])
    }

    func testASpokenQuestionSaysSoAndAMissingOneSaysThat() throws {
        let report = try XCTUnwrap(ChatMoreReport.draft(
            question: nil, spoken: true, answer: "about twenty minutes",
            answeredAt: Date(timeIntervalSince1970: 0), model: nil, appVersion: "", now: Date()
        ))
        XCTAssertTrue(report.asText().contains("Question (spoken):\n(none: the reply was the first message)"))
        XCTAssertEqual(report.fields.first?.label, "your question (spoken)")
        // No app version: no line for it at all.
        XCTAssertFalse(report.asText().contains("App:"))
        XCTAssertEqual(report.modelLine, "on the home server")
    }

    func testOnlyAFinishedReplyWithWordsCanBeReported() {
        XCTAssertTrue(ChatMoreReport.canReport(role: "assistant", pending: false, content: "yes"))
        XCTAssertFalse(ChatMoreReport.canReport(role: "user", pending: false, content: "yes"))
        XCTAssertFalse(ChatMoreReport.canReport(role: "assistant", pending: true, content: "yes"))
        XCTAssertFalse(ChatMoreReport.canReport(role: "assistant", pending: false, content: "  "))
        XCTAssertNil(ChatMoreReport.draft(
            question: "q", spoken: false, answer: "   ", answeredAt: Date(),
            model: "m", appVersion: "1", now: Date()
        ))
    }

    func testANoteIsAFewSentencesAndNotADocument() {
        let long = String(repeating: "a", count: 5_000)
        let report = ChatMoreReport(
            created: Date(), question: nil, spoken: false, answer: "x",
            answeredAt: Date(), model: "m", note: long, appVersion: "1"
        )
        XCTAssertEqual(report.note.count, ChatMoreReport.noteMaxChars)
    }

    func testTheEmailDraftCarriesTheWholeReportAndEscapesIt() throws {
        let report = ChatMoreReport(
            created: Date(timeIntervalSince1970: 0), question: "cats & dogs?", spoken: false,
            answer: "a+b=c", answeredAt: Date(timeIntervalSince1970: 0), model: "m", appVersion: "1"
        )
        let url = try XCTUnwrap(report.mailtoURL())
        XCTAssertEqual(url.scheme, "mailto")
        let text = url.absoluteString
        XCTAssertTrue(text.hasPrefix("mailto:info@codersfarm.org?subject="))
        // An ampersand would otherwise end the body and start a field.
        XCTAssertFalse(text.dropFirst("mailto:info@codersfarm.org?subject=".count).contains("cats & dogs"))
        XCTAssertTrue(text.contains("%26"))
        XCTAssertTrue(text.contains("%2B"))
    }

    func testTheSinksOfferedAreTheOwnersAddressThenTheShareSheet() {
        XCTAssertEqual(ChatMoreReportSink.available(), [.email, .share])
        XCTAssertEqual(ChatMoreReportSink.available(destination: "not an address"), [.share])
        XCTAssertEqual(ChatMoreReportSink.email.label, "email draft to coders farm")
        XCTAssertTrue(ChatMoreReportSink.email.explains.contains("info@codersfarm.org"))
        XCTAssertEqual(ChatMoreReportSink.share.label, "share as text")
        // Handing it on says nothing: the sheet that opened is the answer.
        XCTAssertNil(ChatMoreReportOutcome.handedOn.toast)
        XCTAssertEqual(ChatMoreReportOutcome.noApp.toast, "no app on this phone can take the report")
        XCTAssertEqual(ChatMoreReportOutcome.failed.toast, "couldn't send the report; try again")
    }

    // MARK: - drafts, the pure half

    func testChangesAreOnForAVersionThisPhoneHasNotSeenAndNeverForV1() {
        XCTAssertFalse(ChatMoreDraftRules.changesOn(version: 1, seen: 0))
        XCTAssertTrue(ChatMoreDraftRules.changesOn(version: 2, seen: 0))
        XCTAssertTrue(ChatMoreDraftRules.changesOn(version: 3, seen: 2))
        XCTAssertFalse(ChatMoreDraftRules.changesOn(version: 2, seen: 2))
        XCTAssertFalse(ChatMoreDraftRules.changesOn(version: 2, seen: 3))
    }

    func testACardOpensAtItsOwnVersionAndNotTheLatest() {
        let view = ChatMoreDraftView(
            draft: ChatMoreDraft(id: 4, title: "To the landlord"),
            versions: [
                ChatMoreDraftVersion(version: 1),
                ChatMoreDraftVersion(version: 2),
                ChatMoreDraftVersion(version: 3),
            ]
        )
        XCTAssertEqual(ChatMoreDraftRules.opensAt(view, asked: 2), 2)
        XCTAssertEqual(ChatMoreDraftRules.opensAt(view, asked: nil), 3)
        // A version the draft no longer has (deleted, or a card from another
        // house) falls back to the newest rather than showing nothing.
        XCTAssertEqual(ChatMoreDraftRules.opensAt(view, asked: 9), 3)
        XCTAssertEqual(ChatMoreDraftRules.opensAt(ChatMoreDraftView(), asked: nil), 1)
    }

    func testEachVersionSaysWhereItCameFrom() {
        XCTAssertEqual(
            ChatMoreDraftRules.sourceLabel(ChatMoreDraftVersion(version: 1, source: "first"), thisDeviceID: "ios-1"),
            "first draft"
        )
        XCTAssertEqual(
            ChatMoreDraftRules.sourceLabel(
                ChatMoreDraftVersion(version: 2, source: "edited", deviceID: "ios-1"),
                thisDeviceID: "ios-1"
            ),
            "edited by you"
        )
        XCTAssertEqual(
            ChatMoreDraftRules.sourceLabel(
                ChatMoreDraftVersion(version: 2, source: "edited", deviceID: "web-9", deviceName: "Sam's laptop"),
                thisDeviceID: "ios-1"
            ),
            "edited on Sam's laptop"
        )
        // A device the house has no name for.
        XCTAssertEqual(
            ChatMoreDraftRules.sourceLabel(
                ChatMoreDraftVersion(version: 2, source: "edited", deviceID: nil),
                thisDeviceID: "ios-1"
            ),
            "edited on another device"
        )
        // A revision quotes what was asked for, and names a base only when it
        // is not the version straight before.
        XCTAssertEqual(
            ChatMoreDraftRules.sourceLabel(
                ChatMoreDraftVersion(version: 3, source: "revision", instruction: "shorter", basedOn: 2),
                thisDeviceID: "ios-1"
            ),
            "\u{201C}shorter\u{201D}"
        )
        XCTAssertEqual(
            ChatMoreDraftRules.sourceLabel(
                ChatMoreDraftVersion(version: 4, source: "revision", instruction: "shorter", basedOn: 2),
                thisDeviceID: "ios-1"
            ),
            "\u{201C}shorter\u{201D} · based on v2"
        )
        XCTAssertEqual(
            ChatMoreDraftRules.sourceLabel(
                ChatMoreDraftVersion(version: 3, source: "revision", instruction: nil, basedOn: nil),
                thisDeviceID: "ios-1"
            ),
            "\u{201C}revised\u{201D}"
        )
        XCTAssertEqual(ChatMoreDraftRules.sourceLabel(nil, thisDeviceID: nil), "")
    }

    func testTheSwitchIsLiveOnlyForAVersionWithChanges() {
        let withChanges = ChatMoreDraftVersion(
            version: 2,
            changes: [ChatMoreDraftChange(op: "+", text: "new")]
        )
        XCTAssertTrue(ChatMoreDraftRules.changesSwitchLive(withChanges, editing: false))
        XCTAssertFalse(ChatMoreDraftRules.changesSwitchLive(withChanges, editing: true))
        XCTAssertFalse(ChatMoreDraftRules.changesSwitchLive(ChatMoreDraftVersion(version: 1), editing: false))
        XCTAssertFalse(ChatMoreDraftRules.changesSwitchLive(nil, editing: false))
    }

    func testADraftsVersionsDecodeWithTheirChangesAndSavedFiles() throws {
        let body = """
        {"draft":{"id":4,"thread_id":11,"kind":"cover_letter","template":"letter-block",
                  "title":"To the landlord","format":"docx","kind_label":"cover letter"},
         "versions":[
           {"version":1,"body":"# Dear landlord\\n\\nAbout the cat.","display":"Dear landlord\\nAbout the cat.",
            "changes":null,"author":"model","instruction":null,"based_on":null,"device_id":null,
            "device_name":null,"words":5,"created_at":"2026-11-02T18:00:00+00:00","saved":[]},
           {"version":2,"body":"# Dear landlord\\n\\nAbout the cat, with consent.",
            "display":"Dear landlord\\nAbout the cat, with consent.",
            "changes":[["=","Dear landlord"],["+",", with consent"],["-","!"]],
            "author":"person","source":"edited","device_id":"ios-1","device_name":"Jordan's iPhone","words":7,
            "saved":[{"format":"docx","rel_path":"From Chat/To the landlord v2.docx",
                      "check":{"ok":true,"line":"text readable · single column"},"template":"letter-block"}]}],
         "templates":[{"id":"letter-block","label":"Block letter"}],
         "formats":["docx","pdf"]}
        """
        let view = try JSONDecoder().decode(ChatMoreDraftView.self, from: Data(body.utf8))
        XCTAssertEqual(view.draft.title, "To the landlord")
        XCTAssertEqual(view.draft.format, "docx")
        XCTAssertEqual(view.otherFormat, "pdf")
        XCTAssertEqual(view.formats, ["docx", "pdf"])
        XCTAssertEqual(view.versions.count, 2)
        // v1 has no changes at all, which is what the switch is gated on.
        XCTAssertNil(view.version(1)?.changes)
        let second = try XCTUnwrap(view.version(2))
        XCTAssertEqual(second.changes?.count, 3)
        XCTAssertEqual(second.changes?.filter(\.added).map(\.text), [", with consent"])
        XCTAssertEqual(second.changes?.filter(\.removed).map(\.text), ["!"])
        XCTAssertEqual(second.source, "edited")
        XCTAssertEqual(second.saved.first?.relPath, "From Chat/To the landlord v2.docx")
        XCTAssertTrue(second.saved.first?.checkOK ?? false)
        XCTAssertEqual(
            second.saved.first?.line,
            "home server · From Chat/To the landlord v2.docx · text readable · single column"
        )
        XCTAssertEqual(view.latest?.version, 2)
    }

    /// A diff step that is not a pair is dropped, not thrown: the changes
    /// switch off is a draft read normally, and a throw is a canvas that says
    /// "couldn't read the draft" over one odd step.
    func testAnOddDiffStepDoesNotLoseTheVersion() throws {
        let body = #"{"draft":{"id":1},"versions":[{"version":2,"changes":[["="],["+","ok"],["-","x","y"]]}]}"#
        let view = try JSONDecoder().decode(ChatMoreDraftView.self, from: Data(body.utf8))
        XCTAssertEqual(view.version(2)?.changes?.count, 1)
        XCTAssertEqual(view.version(2)?.changes?.first?.text, "ok")
    }

    func testASaveAnswersWithWhereItWentAndItsCheck() throws {
        let body = """
        {"rel_path":"From Chat/To the landlord v2.docx","name":"To the landlord v2.docx","format":"docx",
         "version":2,"check":{"ok":false,"line":"two columns — some readers skip the second"},
         "library_id":"core:documents"}
        """
        let result = try JSONDecoder().decode(ChatMoreDraftSaveResult.self, from: Data(body.utf8))
        XCTAssertEqual(result.version, 2)
        XCTAssertFalse(result.checkOK)
        XCTAssertEqual(result.checkLine, "two columns — some readers skip the second")
        XCTAssertEqual(result.whereText, "home server · Documents/From Chat/To the landlord v2.docx")
    }

    func testAnExportedFileCannotBecomeAPath() {
        // Each run of forbidden characters becomes **one** space, and the
        // spaces already around it stay — exactly what the Kotlin's regex
        // leaves. Collapsing them here would be a second difference from the
        // file name the other phone writes.
        XCTAssertEqual(
            ChatMoreDraftRules.exportName(title: "Re: tenancy / 2026", version: 3, format: "pdf"),
            "Re  tenancy   2026 v3.pdf"
        )
        XCTAssertEqual(
            ChatMoreDraftRules.exportName(title: "   ", version: 1, format: "docx"),
            "Draft v1.docx"
        )
        // A traversal attempt comes out as a name and not a path.
        XCTAssertEqual(
            ChatMoreDraftRules.exportName(title: #"..\..\etc"#, version: 1, format: "docx"),
            ".. .. etc v1.docx"
        )
    }

    func testTheFirstLinesAreTheWordsAReaderSees() {
        let markdown = """
        # Dear landlord

        I am writing about **the cat**.
        It is a quiet cat.

        - it sleeps
        ---
        ```
        code
        ```
        [a link](https://example.org) at the end
        """
        XCTAssertEqual(
            ChatMoreDraftBlocks.lines(of: markdown),
            [
                "Dear landlord",
                "I am writing about the cat.",
                "It is a quiet cat.",
                "it sleeps",
                // A fence's own line goes; what is inside it stays, exactly
                // as `DraftBlocks.kt:62` leaves it — a draft is prose, and a
                // reader who typed a code block meant to keep the words.
                "code",
                "a link at the end",
            ]
        )
        XCTAssertEqual(
            ChatMoreDraftRules.firstLines(markdown),
            "Dear landlord\nI am writing about the cat.\nIt is a quiet cat."
        )
        XCTAssertEqual(ChatMoreDraftRules.firstLines(markdown, 1), "Dear landlord")
        XCTAssertEqual(ChatMoreDraftRules.firstLines(""), "")
    }

    func testInlineMarksAreDroppedAndSpacesCollapsed() {
        XCTAssertEqual(ChatMoreDraftBlocks.inline("**bold** and _thin_"), "bold and thin")
        XCTAssertEqual(ChatMoreDraftBlocks.inline("`code` here"), "code here")
        XCTAssertEqual(ChatMoreDraftBlocks.inline("a\t \u{00a0}b"), "a b")
        // An escaped asterisk keeps its backslashes: the italic pass runs
        // **before** the escape pass (`DraftBlocks.kt:26-33`), eats the pair
        // of asterisks, and leaves the escape regex nothing to match. Ported
        // order, ported result — the reader of a draft sees the same thing on
        // both phones, which is the point.
        XCTAssertEqual(ChatMoreDraftBlocks.inline("\\*not italic\\*"), "\\not italic\\")
        // An escape the italic pass does not touch is undone, as it should be.
        // Two hashes on the literal: in a one-hash raw string `\#` is itself
        // the escape introducer.
        XCTAssertEqual(ChatMoreDraftBlocks.inline(##"\#heading"##), "#heading")
    }

    func testTheKindsNameAndTheVersionPill() {
        XCTAssertEqual(ChatMoreStrings.kindLabel("resume"), "resume")
        XCTAssertEqual(ChatMoreStrings.kindLabel("cover_letter"), "cover letter")
        XCTAssertEqual(ChatMoreStrings.kindLabel("letter"), "letter")
        XCTAssertEqual(ChatMoreStrings.kindLabel("something_new"), "letter")
        XCTAssertEqual(ChatMoreStrings.version(3), "v3")
        XCTAssertEqual(ChatMoreStrings.latestVersion(4), "latest v4")
        XCTAssertEqual(ChatMoreStrings.saveAs("pdf"), "save PDF")
        XCTAssertEqual(ChatMoreStrings.saveAs("docx"), "save .docx")
        XCTAssertEqual(ChatMoreStrings.saveAsVersion(3), "save as v3")
        XCTAssertEqual(ChatMoreStrings.savedAsVersion(3), "saved as v3 · edited by you")
    }

    // MARK: - the paths and their bodies

    func testEveryPathIsTheRoutesOwnSpelling() {
        XCTAssertEqual(ChatPath.documents(thread: 11), "/api/chat/threads/11/documents")
        XCTAssertEqual(ChatPath.documentFile("a1"), "/api/chat/documents/a1/file")
        XCTAssertEqual(ChatPath.documentParts("a1"), "/api/chat/documents/a1/parts")
        XCTAssertEqual(ChatPath.upload(token: "p1"), "/api/chat/uploads/p1")
        XCTAssertEqual(ChatPath.docJob("j7"), "/api/chat/doc-jobs/j7")
        XCTAssertEqual(ChatPath.docJobEvents("j7"), "/api/chat/doc-jobs/j7/events")
        XCTAssertEqual(ChatPath.docJobStop("j7"), "/api/chat/doc-jobs/j7/stop")
        XCTAssertEqual(ChatPath.docJobResume("j7"), "/api/chat/doc-jobs/j7/resume")
        XCTAssertEqual(ChatPath.draft(4), "/api/chat/drafts/4")
        XCTAssertEqual(ChatPath.threadDrafts(thread: 11), "/api/chat/threads/11/drafts")
        XCTAssertEqual(ChatPath.draftVersions(4), "/api/chat/drafts/4/versions")
        XCTAssertEqual(ChatPath.draftSave(4), "/api/chat/drafts/4/save")
        XCTAssertEqual(
            ChatPath.draftExport(4, version: 2, format: "pdf"),
            "/api/chat/drafts/4/export?version=2&format=pdf"
        )
        XCTAssertEqual(ChatPath.lookups(thread: 11, message: 12), "/api/chat/threads/11/lookups/12")
        XCTAssertEqual(ChatPath.headsUp(thread: 11), "/api/chat/threads/11/heads-up")
        XCTAssertEqual(ChatPath.voice(thread: 11), "/api/chat/threads/11/voice")
        XCTAssertEqual(ChatPath.internet, "/api/chat/internet")
    }

    func testEachBodyIsTheShapeTheRouteValidates() throws {
        func object(_ data: Data) throws -> [String: Any] {
            try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        }
        XCTAssertEqual(try object(ChatPath.internetBody(allowed: true))["internet_allowed"] as? Bool, true)

        let decision = try object(ChatPath.lookupBody(decision: "chat", deviceID: "ios-1"))
        XCTAssertEqual(decision["decision"] as? String, "chat")
        XCTAssertEqual(decision["device_id"] as? String, "ios-1")
        // No device id rather than an empty one: the route's field is
        // optional and `""` would fail its own length check.
        XCTAssertNil(try object(ChatPath.lookupBody(decision: "no", deviceID: ""))["device_id"])

        let edit = try object(ChatPath.draftEditBody(text: "hello", baseVersion: 2, deviceID: "ios-1"))
        XCTAssertEqual(edit["text"] as? String, "hello")
        XCTAssertEqual(edit["base_version"] as? Int, 2)

        let save = try object(ChatPath.draftSaveBody(version: 2, format: "pdf", deviceID: nil))
        XCTAssertEqual(save["version"] as? Int, 2)
        XCTAssertEqual(save["format"] as? String, "pdf")
        XCTAssertNil(save["template"])
        XCTAssertNil(save["device_id"])
        // Everything defaulted: the route takes an empty body and picks the
        // latest version and the kind's format.
        XCTAssertTrue(try object(ChatPath.draftSaveBody(version: nil, format: nil, deviceID: nil)).isEmpty)
    }
}
