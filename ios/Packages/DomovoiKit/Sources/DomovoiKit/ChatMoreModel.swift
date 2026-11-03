import Foundation
import Observation

/// The rest of the conversation: the per-row extras, the two cards a person
/// can answer, the thread's long-document jobs, the internet switch and a
/// spoken turn.
///
/// `ChatScreen.kt`'s `ConversationPane` minus everything ``ChatModel`` already
/// owns — the transcript, the composer, the send and the retry. It is a model
/// for the reason every one of these has been: which card may be answered,
/// which job offers "carry on", whether the chip can be tapped, and what a
/// refused turn says are all **decisions**, and a view cannot be asked whether
/// it got them right.
///
/// ### Why it reads the messages route a second time
///
/// ``ChatMessage`` keeps `documents`, `doc` and `draft` as presence booleans,
/// ``ChatStats`` drops `lookup_card` and ``ChatMessageList`` drops `jobs`, and
/// `ChatMessage.live(...)` drops `images` and three of the four speaker
/// fields on the way into the transcript. Every one of those files is out of
/// reach tonight (NIGHT-RULES §2), so this reads the same route and keeps the
/// extras keyed by message id — ``ChatMoreDetail`` says so at length, and
/// shared change 3 in `design-notes/ios/overnight/chatmore/DECISIONS.md` is
/// what removes the duplication.
///
/// ### What this does not do, and why there is no control for it
///
/// * **No composer gate.** While a card answer streams, Android's `sending`
///   blocks the composer (`ChatScreen.kt:886`); ``ChatModel/canSend``
///   (`ChatModel.swift:518`) is not this item's to gate, so the card's own
///   buttons go quiet and the composer stays live. A second send is served
///   normally by the server, so nothing breaks — it is just not Android's
///   behaviour. Shared change 2.
/// * **No attach, no Revise box, no print, no "send to home", no letters, no
///   export.** Each one is a send-body key this item cannot write, a route
///   another item owns, or a feature with no iOS subject at all; DECISIONS.md
///   gives the reason one by one.
@MainActor
@Observable
public final class ChatMoreModel {

    private let container: AppContainer
    private let deviceID: String?
    private let now: @Sendable () -> Date

    /// Re-read the thread after a card answer or a spoken turn.
    ///
    /// A closure rather than a reference to ``ChatModel``, so this type can be
    /// tested without one and so the dependency points one way. The view
    /// passes `{ chat.open(thread: id, title: chat.openTitle) }`, which is
    /// exactly what `ChatScreen.kt:917` does after a decision.
    private let reopen: @MainActor () -> Void

    /// The pictures a row carries. Held here because **a lazy row never holds
    /// a `Task`** (NIGHT-RULES §3): the fetch belongs to something that
    /// outlives the bubble.
    public let pictures: ChatMorePictureStore

    /// This phone's heads-up switch, which no route stores.
    public let headsUp: ChatMoreHeadsUpStore

    /// The thread open, or nil.
    public private(set) var thread: Int64?

    public private(set) var detail = ChatMoreDetail()

    /// The household's answer about going online, for the chip.
    public private(set) var internet: ChatMoreInternet?

    /// This thread's own switch, as the list row carried it
    /// (``ChatThread/internetAllowed``) and as a decision or the chip changes
    /// it.
    public private(set) var internetAllowed = false

    /// A card answer or a spoken turn is on its way out. The cards read it;
    /// the composer cannot (see above).
    public private(set) var answering = false

    /// The words of a card's answer as they arrive, drawn where the reply
    /// will be (`ChatScreen.kt:897`).
    public private(set) var cardReply = ""

    /// The refusal a card answer came back with, if it did.
    public private(set) var cardError: String?

    /// A card answered **here**, shown at once rather than after the re-read
    /// (`ChatScreen.kt:893`).
    public private(set) var answeredCards: [Int64: ChatMoreLookupCard] = [:]

    /// A heads-up option picked here, likewise (`ChatScreen.kt:907`).
    public private(set) var headsUpPicks: [Int64: String] = [:]

    /// The talk screen's phase, and the note under its button.
    public private(set) var talkPhase: ChatMoreTalkPhase = .idle
    public private(set) var talkNote: String?

    /// Domovoi asked something back, so the hint invites an answer
    /// (`VoiceView.kt:199`).
    public private(set) var followup = false

    /// The WAV the last turn came back with, for the view to play. Nil when
    /// the server's text-to-speech sent none — and then **nothing is
    /// spoken**: see ``ChatMoreVoiceReply/playback``.
    public private(set) var replyAudio: Data?

    public private(set) var toast: String?

    /// How many times this model has moved house **or thread**.
    ///
    /// **The mechanism for [[read-after-await-wrong-key]]**, the top defect
    /// class on this track. ``ChatModel`` counts houses, because a thread id
    /// is the house's; this counts houses *and* threads, because every read
    /// here is keyed by message id and two threads of one house can have no
    /// message in common but a job each — an answer for thread 7 landing in
    /// thread 8's card area is the same bug one level down.
    ///
    /// So **every task this type starts takes this number before its first
    /// await and checks it after**: ``load()``, ``decideLookup(message:decision:)``,
    /// ``pickHeadsUp(message:option:kind:)``, ``stopJob(_:)``, ``resumeJob(_:)``,
    /// ``follow(job:)``, ``setInternetAllowed(_:)`` and ``talk(pcm:)``. A task
    /// that no longer holds the thread it was started for writes nothing,
    /// says nothing and refreshes nothing.
    private var epoch = 0

    private var stream: Task<Void, Never>?
    private var follows: [String: Task<Void, Never>] = [:]

    private func stillHere(_ asked: Int) -> Bool { asked == epoch }

    public init(
        in container: AppContainer,
        deviceID: String?,
        headsUp: ChatMoreHeadsUpStore = ChatMoreHeadsUpStore(),
        pictures: ChatMorePictureStore? = nil,
        now: @Sendable @escaping () -> Date = { Date() },
        reopen: @MainActor @escaping () -> Void = {}
    ) {
        self.container = container
        self.deviceID = deviceID
        self.headsUp = headsUp
        self.pictures = pictures ?? ChatMorePictureStore(in: container)
        self.now = now
        self.reopen = reopen
    }

    // MARK: - opening and closing a thread

    /// A thread is on screen: read its extras, its jobs and the household's
    /// internet answer.
    ///
    /// - Parameter internetAllowed: the thread's own flag, which the list row
    ///   already carries (``ChatThread/internetAllowed``) — read from there
    ///   rather than asked for again, since `GET /threads/{id}` would be a
    ///   third read of a thing the screen already has.
    public func open(thread id: Int64, internetAllowed: Bool = false) {
        epoch += 1
        thread = id
        self.internetAllowed = internetAllowed
        detail = ChatMoreDetail()
        answeredCards = [:]
        headsUpPicks = [:]
        cardReply = ""
        cardError = nil
        // **Cleared here as well as in ``close()``.** A card answer or a turn
        // in flight for the old thread returns early on the epoch guard and
        // never reaches `answering = false`, so a thread opened mid-answer
        // used to arrive with every card and every job button dead.
        answering = false
        replyAudio = nil
        talkPhase = .idle
        talkNote = nil
        followup = false
        cancelFollows()
        load()
        loadInternet()
    }

    /// The conversation closed: nothing in flight may write anything.
    public func close() {
        epoch += 1
        thread = nil
        detail = ChatMoreDetail()
        answering = false
        cancelFollows()
        stream?.cancel()
        stream = nil
    }

    /// The app moved house.
    public func serverChanged() {
        epoch += 1
        thread = nil
        detail = ChatMoreDetail()
        internet = nil
        answering = false
        cancelFollows()
        stream?.cancel()
        stream = nil
        pictures.serverChanged()
    }

    /// Read the extras again — after a send, a retry, or a `chat.changed`.
    public func refresh() {
        guard thread != nil else { return }
        load()
    }

    private func load() {
        guard let id = thread else { return }
        let client = container.client
        let asked = epoch
        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let answer = try await client.get(ChatPath.messages(id), as: ChatMoreDetail.self)
                // The key was taken BEFORE the await: a detail read for
                // thread 7 must not land in thread 8.
                guard self.stillHere(asked) else { return }
                self.detail = answer
                self.wantPictures(of: answer)
                self.followLiveJobs()
            } catch {
                // Nothing is said. The transcript itself came from the same
                // route through ``ChatModel``, so a failure here is a
                // conversation that draws its words and none of its extras —
                // a toast about it would be the second one for one outage.
                return
            }
        }
    }

    private func loadInternet() {
        let client = container.client
        let asked = epoch
        Task { @MainActor [weak self] in
            guard let self else { return }
            // `getUntracked`: the chip is a background read, and a 401 from it
            // must not raise the pairing screen — the defect class
            // [[reasoning-that-hides-a-bug]] records.
            guard let answer = try? await client.getUntracked(ChatPath.internet, as: ChatMoreInternet.self),
                  self.stillHere(asked) else { return }
            self.internet = answer
        }
    }

    private func wantPictures(of detail: ChatMoreDetail) {
        for extras in detail.rows.values where !extras.images.isEmpty {
            pictures.want(extras.images)
        }
    }

    // MARK: - what a row draws

    /// The extras of a row, with a card answered here in place of the one the
    /// server last sent.
    public func extras(for id: Int64) -> ChatMoreRowExtras? { detail.rows[id] }

    /// The lookup card a row is, if it is one — answered here, or as read.
    public func lookupCard(for id: Int64) -> ChatMoreLookupCard? {
        answeredCards[id] ?? detail.rows[id]?.lookupCard
    }

    /// The heads-up option picked for a row: picked here, or as the server
    /// read it back.
    public func headsUpChosen(for id: Int64) -> String? {
        headsUpPicks[id] ?? detail.rows[id]?.headsUp?.chosen
    }

    /// Whether a card's buttons are live.
    ///
    /// A lookup card is answerable while it is pending and nothing else is
    /// answering (`ChatScreen.kt:988`); a heads-up card **only while it is the
    /// thread's last row** (`ChatScreen.kt:1008`), because the server 409s a
    /// pick on anything else (`stale`, `chat.py:1132`).
    public func lookupAnswerable(_ id: Int64) -> Bool {
        guard let card = lookupCard(for: id) else { return false }
        return card.isPending && !answering
    }

    public func headsUpAnswerable(_ id: Int64, isLastRow: Bool) -> Bool {
        guard isLastRow, !answering else { return false }
        guard let card = detail.rows[id]?.headsUp else { return false }
        return headsUpChosen(for: id) == nil && card.chosen == nil
    }

    /// The speaker marker for a row: nil for anything typed, for a reply, and
    /// for a row this phone has not read back yet.
    public func speakerMark(for id: Int64) -> ChatMoreSpeakerMark? {
        guard let extras = detail.rows[id] else { return nil }
        // `ownerID` is nil in this app, everywhere — ``ChatMoreSpeaker/mark``
        // says why that is Android-faithful rather than reduced.
        return ChatMoreSpeaker.mark(for: extras)
    }

    // MARK: - the two cards

    /// Answer a lookup card: the card shows the answer at once, the reply
    /// streams in, and the thread is read back after
    /// (`decideLookup`, `ChatScreen.kt:888`).
    @discardableResult
    public func decideLookup(message id: Int64, decision: String) -> Bool {
        guard let threadID = thread, !answering else { return false }
        guard let card = lookupCard(for: id), card.isPending else { return false }
        answering = true
        cardReply = ""
        cardError = nil
        answeredCards[id] = card.answered(decision)
        let asked = epoch
        let allowsChat = ChatMoreLookupRules.allowsChat(decision)
        let body = ChatPath.lookupBody(decision: decision, deviceID: deviceID)
        stream = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.run(
                path: ChatPath.lookups(thread: threadID, message: id),
                body: body,
                verb: ChatMoreStrings.verbLookItUp,
                on: asked
            )
            guard self.stillHere(asked) else { return }
            // "Allow in this chat" also sets the thread's own flag on the
            // server (`chat.py:2126`), so the chip follows the decision
            // without a read of its own (`ChatScreen.kt:918`).
            if allowsChat { self.internetAllowed = true }
        }
        return true
    }

    /// Pick an option on a heads-up card.
    ///
    /// `kind` is the card's, so that "don't warn me about this again" and the
    /// `skip` list this pick carries are the same kind
    /// (`pickHeadsUp`, `ChatScreen.kt:790`).
    ///
    /// `choose_another` is **not** accepted: it answers nothing and re-opens
    /// the picture picker, which this app does not have
    /// (``ChatMoreHeadsUp/offerable``).
    @discardableResult
    public func pickHeadsUp(message id: Int64, option: String) -> Bool {
        guard let threadID = thread, !answering else { return false }
        guard option != ChatMoreStrings.chooseAnother, !option.isEmpty else { return false }
        guard detail.rows[id]?.headsUp != nil else { return false }
        answering = true
        cardReply = ""
        cardError = nil
        headsUpPicks[id] = option
        let asked = epoch
        let body = ChatPath.headsUpBody(messageID: id, option: option, prefs: headsUp.prefs)
        stream = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.run(
                path: ChatPath.headsUp(thread: threadID),
                body: body,
                verb: ChatMoreStrings.verbHeadsUp,
                on: asked
            )
        }
        return true
    }

    /// "don't warn me about this again": the kind is silenced on this phone,
    /// and the **next** pick carries it (`ChatDocsApi.kt:105`).
    public func silenceHeadsUp(kind: String) {
        headsUp.silence(kind)
    }

    public func headsUpSilenced(kind: String) -> Bool { headsUp.silenced(kind) }

    /// One streamed round trip: open it, gather the words, then re-read the
    /// thread — which is what makes the kept row, and not the streamed text,
    /// the thing a case should assert.
    private func run(path: String, body: Data, verb: String, on asked: Int) async {
        let client = container.client
        do {
            let events = try await client.events(method: "POST", path: path, body: body)
            for try await event in events {
                guard !Task.isCancelled, stillHere(asked) else { break }
                switch event.name {
                case "delta":
                    if let payload: ChatDelta = event.payload() { cardReply += payload.text }
                case "error":
                    if let payload: ChatStreamError = event.payload(), let detail = payload.detail {
                        cardError = detail
                    }
                case "done":
                    // The row itself arrives in the re-read below; nothing is
                    // kept from it here, so a `done` that cannot be decoded
                    // is not a failure.
                    break
                default:
                    // `job`, `progress`, `replace`, `lookup_permission`: the
                    // server sends them and this model reads its own copies
                    // from the re-read. An event this app cannot use must not
                    // end a stream (``ServerEvent/payload(as:)`` says why).
                    break
                }
            }
        } catch let trouble as DomovoiError {
            guard stillHere(asked) else { return }
            cardError = failureText(verb, trouble)
        } catch {
            guard stillHere(asked) else { return }
            cardError = failureText(verb, .unreachable("\(error)"))
        }
        guard stillHere(asked) else { return }
        answering = false
        load()
        reopen()
    }

    // MARK: - the internet chip

    /// (label, on) for the chip, from the household's answer and this
    /// thread's flag. Nil until the household has answered — the chip draws
    /// nothing rather than guessing (`ChatScreen.kt:1275`).
    public var internetChip: (label: String, on: Bool)? {
        guard let internet else { return nil }
        return ChatMoreInternetChip.words(
            policy: internet.internet,
            alwaysAllow: internet.alwaysAllow,
            chatAllowed: internetAllowed
        )
    }

    /// Whether a tap on the chip can change anything.
    public var internetChipTappable: Bool {
        guard let internet else { return false }
        return ChatMoreInternetChip.tappable(policy: internet.internet, alwaysAllow: internet.alwaysAllow)
    }

    public var internetChipHint: String? {
        guard let internet, let chip = internetChip else { return nil }
        return ChatMoreInternetChip.hint(
            policy: internet.internet,
            alwaysAllow: internet.alwaysAllow,
            on: chip.on
        )
    }

    /// Turn this chat's lookups on or off (`PATCH /threads/{id}`,
    /// `ChatScreen.kt:954`).
    ///
    /// The flag is written **after** the server agrees, not before: a chip
    /// that flipped on a refused write would say this chat looks things up
    /// when it does not.
    @discardableResult
    public func setInternetAllowed(_ allowed: Bool) -> Bool {
        guard let id = thread, internetChipTappable else { return false }
        let client = container.client
        let asked = epoch
        let body = ChatPath.internetBody(allowed: allowed)
        Task { @MainActor [weak self] in
            guard let self else { return }
            do throws(DomovoiError) {
                try await client.send(method: "PATCH", path: ChatPath.thread(id), body: body)
                guard self.stillHere(asked) else { return }
                self.internetAllowed = allowed
            } catch {
                guard self.stillHere(asked) else { return }
                self.toast = failureText(ChatMoreStrings.verbChangeIt, error)
            }
        }
        return true
    }

    // MARK: - long-document jobs

    public var jobs: [ChatMoreJob] { detail.jobs }

    /// The stopped job whose newest reply is this row — the one row that
    /// offers "carry on", and how many parts are left (`ChatScreen.kt:991`).
    public func carryOn(for id: Int64) -> (job: ChatMoreJob, left: Int)? {
        guard !answering else { return nil }
        let rows = detail.rows.values.sorted { $0.id < $1.id }
        for job in ChatMoreJobRules.stopped(jobs) {
            guard ChatMoreJobRules.carryOnRow(for: job, rows: Array(rows)) == id else { continue }
            return (job, ChatMoreJobRules.left(job))
        }
        return nil
    }

    /// Stop a running job (`POST …/stop`, `ChatScreen.kt:1038`).
    public func stopJob(_ id: String) {
        write(ChatPath.docJobStop(id), verb: ChatMoreStrings.verbStop)
    }

    /// Carry a stopped or interrupted one on (`POST …/resume`,
    /// `ChatScreen.kt:1015`).
    public func resumeJob(_ id: String) {
        write(ChatPath.docJobResume(id), verb: ChatMoreStrings.verbCarryOn)
    }

    /// One POST whose answer is the job's new view, put back in place.
    private func write(_ path: String, verb: String) {
        let client = container.client
        let asked = epoch
        Task { @MainActor [weak self] in
            guard let self else { return }
            // `throws(DomovoiError)` on the `do` itself, as `ChatModel.swift:574`
            // explains: a bare `do` is typed `any Error` even when everything
            // in it throws the same thing, and `failureText` takes the one
            // type.
            do throws(DomovoiError) {
                let view = try await client.post(path, as: ChatMoreJob.self)
                guard self.stillHere(asked) else { return }
                self.replace(view)
                self.followLiveJobs()
            } catch {
                guard self.stillHere(asked) else { return }
                // The server's own sentence when it sent one, as
                // `docFailureText` prefers it (`ChatDocsApi.kt:257`).
                let failure = error.failure.flatMap { ChatMoreJSON.string(ChatMoreJSON.object($0.body), "detail") }
                self.toast = failure ?? failureText(verb, error)
            }
        }
    }

    /// Follow every live job's progress (`GET …/events`, SSE on a GET —
    /// `ChatScreen.kt:601-617` polls every two seconds instead, which is the
    /// same information one round trip at a time).
    public func followLiveJobs() {
        for job in ChatMoreJobRules.live(jobs) where follows[job.id] == nil {
            follow(job: job.id)
        }
        // A job that has stopped needs no follower.
        let living = Set(ChatMoreJobRules.live(jobs).map(\.id))
        for (id, task) in follows where !living.contains(id) {
            task.cancel()
            follows[id] = nil
        }
    }

    private func follow(job id: String) {
        let client = container.client
        let asked = epoch
        follows[id] = Task { @MainActor [weak self] in
            guard let self else { return }
            defer { if self.stillHere(asked) { self.follows[id] = nil } }
            do {
                // A **GET** stream, so ``ServerEvents`` sets no
                // `application/json` for it (`ServerEvents.swift:146-149`).
                let events = try await client.events(method: "GET", path: ChatPath.docJobEvents(id))
                for try await event in events {
                    guard !Task.isCancelled, self.stillHere(asked) else { break }
                    // `job`, `progress` and `done` all carry a whole view
                    // (`jobEventView`, `ChatDocsApi.kt:186`); anything else,
                    // an `error` among them, is left to the re-read.
                    guard ["job", "progress", "done"].contains(event.name),
                          let view: ChatMoreJob = event.payload() else { continue }
                    self.replace(view)
                }
            } catch {
                // A follower that fell over says nothing: the job's state is
                // read back with the thread, and a toast per dropped stream
                // on a flaky network would be a conversation full of them.
                return
            }
            guard self.stillHere(asked) else { return }
            // The job finished: its reply is a row now.
            self.load()
        }
    }

    private func replace(_ view: ChatMoreJob) {
        guard !view.id.isEmpty else { return }
        var next = detail.jobs
        if let at = next.firstIndex(where: { $0.id == view.id }) {
            next[at] = view
        } else {
            next.append(view)
        }
        detail = ChatMoreDetail(rows: detail.rows, jobs: next)
    }

    private func cancelFollows() {
        for task in follows.values { task.cancel() }
        follows = [:]
    }

    // MARK: - a spoken turn

    /// Whether the mic is offered in place of send (`ChatScreen.kt:1157`).
    public func micOffered(typed: String) -> Bool {
        ChatMoreVoiceRules.micOffered(typed: typed)
    }

    /// What the talk screen says under its button.
    public var talkHint: String {
        ChatMoreVoiceRules.hint(phase: talkPhase, followup: followup, note: talkNote)
    }

    /// A press on the talk button: whether a recording starts.
    @discardableResult
    public func pressTalk() -> Bool {
        let decision = ChatMoreVoiceRules.press(talkPhase)
        if let note = decision.note { talkNote = note }
        if decision.starts {
            if talkPhase == .speaking { replyAudio = nil }
            talkNote = nil
            talkPhase = .recording
        }
        return decision.starts
    }

    /// The finger slid off the button, or back onto it.
    public func slideTalk(off: Bool) {
        guard talkPhase == .recording || talkPhase == .cancelArmed else { return }
        talkPhase = off ? .cancelArmed : .recording
    }

    /// The recording was thrown away (the finger came up off the button, or
    /// the system cancelled the gesture).
    public func cancelTalk() {
        guard talkPhase == .recording || talkPhase == .cancelArmed else { return }
        talkPhase = .idle
    }

    /// One finished recording: 16 kHz mono PCM16, as the core takes it.
    ///
    /// A clip under ``ChatMoreVoiceTurn/minSeconds`` is not sent at all —
    /// `interrupted` says the press was only there to stop a reply, which
    /// needs no advice (`VoiceView.kt:293`).
    ///
    /// Afterwards the thread is **re-read**: the core wrote both rows itself
    /// (`phone_voice.py:632-648`), so the question and the reply appear in the
    /// transcript rather than being invented here.
    @discardableResult
    public func talk(pcm: Data, interrupted: Bool = false) -> Bool {
        guard let threadID = thread else { return false }
        let recorded = ChatMoreVoiceRules.recorded(
            seconds: ChatMoreVoiceRules.seconds(ofPCM: pcm.count),
            interrupted: interrupted
        )
        guard recorded.sends else {
            talkNote = recorded.note
            talkPhase = .idle
            return false
        }
        answering = true
        talkPhase = .sending
        talkNote = nil
        followup = false
        replyAudio = nil
        let asked = epoch
        let client = container.client
        let who = deviceID
        stream = Task { @MainActor [weak self] in
            guard let self else { return }
            let reply: ChatMoreVoiceReply
            do {
                reply = try await ChatMoreVoiceTurn.send(
                    using: client,
                    thread: threadID,
                    pcm: pcm,
                    phoneID: who
                )
            } catch let refusal as ChatMoreVoiceRefusal {
                guard self.stillHere(asked) else { return }
                // **The server's own sentence**, which for a turn too long is
                // the core's "a voice turn is at most 30 seconds" and not an
                // iOS one (``ChatMoreVoiceRefusal/of(status:body:)``).
                self.finishTalk(note: refusal.text, on: asked)
                return
            } catch let trouble as DomovoiError {
                guard self.stillHere(asked) else { return }
                self.finishTalk(note: failureText(ChatMoreStrings.verbVoice, trouble), on: asked)
                return
            } catch {
                guard self.stillHere(asked) else { return }
                self.finishTalk(
                    note: failureText(ChatMoreStrings.verbVoice, .unreachable("\(error)")),
                    on: asked
                )
                return
            }
            // The key was taken BEFORE the await. A turn spoken into thread 7
            // must not put its rows, its note or its audio into thread 8 —
            // which is the one case this model's own test switches threads
            // inside the round trip for.
            guard self.stillHere(asked) else { return }
            guard reply.heard else {
                self.finishTalk(note: ChatMoreStrings.didntCatchThat, on: asked)
                return
            }
            self.followup = reply.expectFollowup
            self.replyAudio = reply.wav
            self.talkPhase = reply.playback == .serverAudio ? .speaking : .idle
            self.answering = false
            self.load()
            self.reopen()
        }
        return true
    }

    /// The reply has been heard to the end, or a press cut it off.
    public func finishedSpeaking() {
        if talkPhase == .speaking { talkPhase = .idle }
        replyAudio = nil
    }

    private func finishTalk(note: String?, on asked: Int) {
        guard stillHere(asked) else { return }
        talkNote = note
        talkPhase = .idle
        answering = false
    }

    // MARK: - report a message

    /// The report for a reply, and the question above it.
    ///
    /// The question is the nearest **user** row before it in the transcript's
    /// own order (`reportableAt`, `ChatScreen.kt:716`) — seeded from the
    /// extras this model read, so a thread with two questions reports the
    /// right one.
    public func report(for id: Int64, appVersion: String) -> ChatMoreReport? {
        let rows = detail.rows.values.sorted { $0.id < $1.id }
        guard let at = rows.firstIndex(where: { $0.id == id }) else { return nil }
        let reply = rows[at]
        guard ChatMoreReport.canReport(role: reply.role, pending: false, content: reply.content) else {
            return nil
        }
        let asked = rows[..<at].last { $0.role == "user" }
        return ChatMoreReport.draft(
            question: asked?.content,
            spoken: asked?.source == "voice",
            answer: reply.content,
            answeredAt: Self.date(reply.createdAt) ?? now(),
            model: reply.model,
            appVersion: appVersion,
            now: now()
        )
    }

    /// An ISO 8601 stamp as the server writes it (`chat.py:415`).
    static func date(_ iso: String?) -> Date? {
        guard let iso else { return nil }
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let parsed = withFraction.date(from: iso) { return parsed }
        return ISO8601DateFormatter().date(from: iso)
    }

    public func setToast(_ said: String?) { toast = said }
}
