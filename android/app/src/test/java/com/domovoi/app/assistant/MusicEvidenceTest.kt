package com.domovoi.app.assistant

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Review round 2: a "play" acts on library evidence, by phrase, not on a
 *  word list ([MusicEvidence]). */
class MusicEvidenceTest {
    @Test fun aPhraseMatchesWholeNamesNotStrayWords() {
        // Equal, a leading "the" aside.
        assertTrue(MusicEvidence.phrase("the smile", "The Smile"))
        assertTrue(MusicEvidence.phrase("smile", "The Smile"))
        assertTrue(MusicEvidence.phrase("beatles", "The Beatles"))
        assertTrue(MusicEvidence.phrase("that's life", "That’s Life"))
        // Most of a name, or a name that is most of the words.
        assertTrue(MusicEvidence.phrase("velvet kites", "The Velvet Kites Band"))
        assertTrue(MusicEvidence.phrase("lo fi beats", "Lo-Fi Beats"))
        // One word of a longer name is not evidence.
        assertFalse(MusicEvidence.phrase("play", "Play That Funky Music"))
        assertFalse(MusicEvidence.phrase("home", "To Build a Home"))
        assertFalse(MusicEvidence.phrase("rules", "New Rules"))
        assertFalse(MusicEvidence.phrase("happy face", "Happy"))
        assertFalse(MusicEvidence.phrase("kids", "Kids in America"))
        // Words, not letters; and too short to say anything.
        assertFalse(MusicEvidence.phrase("kid", "Kids"))
        assertFalse(MusicEvidence.phrase("me", "Me"))
    }

    @Test fun byNamesATitleAndAnArtist() {
        val a = PhoneAction(ActionKind.PLAY_MUSIC, query = "smile by lily allen", phrase = "smile by lily allen")
        assertTrue(MusicEvidence.matches(a, com.domovoi.app.player.PlayItem.fromDeviceAudio(1, "Smile", "Lily Allen", null, 1.0, "x", null)))
        assertFalse(MusicEvidence.matches(a, com.domovoi.app.player.PlayItem.fromDeviceAudio(2, "The Smile", "Someone Else", null, 1.0, "x", null)))
    }

    @Test fun theWordsAsSaidCountBesideTheQuery() {
        // "play some nights": the query is "nights", the title "Some Nights".
        val a = PhoneAction(ActionKind.PLAY_MUSIC, query = "nights", phrase = "some nights")
        assertTrue(MusicEvidence.matches(a, com.domovoi.app.player.PlayItem.fromDeviceAudio(1, "Some Nights", "fun.", null, 1.0, "x", null)))
        assertTrue(MusicEvidence.genre(PhoneAction(ActionKind.PLAY_MUSIC, query = "jazz", phrase = "some jazz"), "Jazz"))
    }
}
