package com.domovoi.app.assistant

import com.domovoi.app.assistant.docs.LetterRead

/*
 * "What does this letter say?" in a phone chat (documents plan 6.4, D6).
 *
 * Where the phone's model reads photos on its GPU (N3), a letter photo is
 * read here: the blurry card before anything (measured on the phone), the
 * one-word printed-or-handwritten step and its card, the transcription,
 * then the phone's own model explains it and the app checks every date,
 * amount and reference number ([LetterRead.finish]).
 *
 * Where it doesn't, and a home server is paired with AI on there, the "+"
 * offers "a letter for the home server": the photo goes to
 * POST /api/chat/letters/read ([LetterServer]), which reads it and keeps
 * nothing, and the explanation is still written and checked on this
 * phone. With neither, a letter question says so plainly.
 *
 * Nothing is kept on the phone either (N3's rules): the photo lives in
 * memory until its reply ends (or, while a card waits for a pick, until
 * the pick, a new chat or the app closing), and the transcript is never
 * stored; the conversation keeps the "photo" marker and the checked
 * explanation.
 */

/** What the home server read from one photo (chat_letters.read_letter). */
data class LetterServerRead(val blur: Double?, val kind: String?, val text: String?, val stopped: Boolean)

/** The home server's letter reader, as the screen hands it over (null: none
 *  paired, or AI features are off there). */
fun interface LetterServer {
    suspend fun read(jpeg: ByteArray, stopIfHandwritten: Boolean): LetterServerRead
}

object PhoneLetters {
    /** A letter question with no photo, where this phone reads photos. */
    const val TAKE_A_PHOTO = "Take a photo of the letter with + (or choose one from your photos), then ask again. " +
        "One page per photo."

    /** ...where it can't, and a home server can. */
    const val SERVER_CAN = "This phone can't read photos itself: its graphics chip can't run Domovoi's picture model. " +
        "Your home server can read a letter for you and keeps nothing: tap +, choose “a letter for the home " +
        "server”, then ask."

    /** ...and with no home server to ask. */
    const val CANT = "This phone can't read photos: its graphics chip can't run Domovoi's picture model. Type the " +
        "part of the letter you'd like explained, or pair a home server, which can read photos of letters."

    /** A pick on a photo's card after the photo is gone (photos aren't kept). */
    const val PHOTO_GONE = "That photo isn't here any more: photos aren't kept. Add it again and ask."

    /** The question a letter for the home server goes with when nothing is typed. */
    const val DEFAULT_QUESTION = "What does this letter say? What do I need to do?"

    /** A question that asks about a letter by name ("what does this letter
     *  say?"), sent with no photo in a chat that has had none: answered with
     *  how to send one. A bare "what do I need to do?", or a follow-up about
     *  a letter already explained, stays the model's. */
    fun namesALetter(question: String): Boolean = LetterRead.namesALetter(question)

    /** The answer to [namesALetter] with no photo. */
    fun noPhotoReply(readsPhotos: Boolean, serverReads: Boolean): String = when {
        readsPhotos -> TAKE_A_PHOTO
        serverReads -> SERVER_CAN
        else -> CANT
    }

    /** "a letter for the home server" shows in the "+" menu: no photos on
     *  this phone, a home server that reads them, nothing else going on. */
    fun serverButtonShown(ui: AssistantUi): Boolean =
        ui.homeServerLetters && !(ui.modelTakesImages && ui.vision == VisionProbe.PASSED) && ui.installed &&
            !ui.replying && ui.voicePhase == VoicePhase.Idle && ui.pendingPhoto == null
}
