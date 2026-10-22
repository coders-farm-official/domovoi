package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Near spellings of a library name (Fold check 2026-10-21: "Outcast"
 *  meant OutKast): one name, close enough, or nothing. */
class MusicNearMatchTest {
    private val names = listOf(
        "OutKast", "Hey Ya!", "Speakerboxxx/The Love Below", "The Beatles", "Let It Be", "Radiohead", "Paranoid Android",
        "Adele", "Abel", "Muse", "Mase", "Home", "Happy", "Survivor", "Eye of the Tiger", "Miles Davis", "So What", "Daft Punk",
    )

    @Test fun nearSpellingsFindTheOneName() {
        assertEquals("OutKast", MusicNearMatch.best("outcast", names))
        assertEquals("OutKast", MusicNearMatch.best("Out Cast", names))
        assertEquals("The Beatles", MusicNearMatch.best("beatls", names))
        assertEquals("Radiohead", MusicNearMatch.best("radiohed", names))
        assertEquals("Paranoid Android", MusicNearMatch.best("paranoid andriod", names))
        assertEquals("Miles Davis", MusicNearMatch.best("miles daviss", names))
        assertEquals("Daft Punk", MusicNearMatch.best("daft punck", names))
    }

    @Test fun nothingWhenUnsureOrFar() {
        assertNull("exact: not a guess", MusicNearMatch.best("outkast", names))
        assertEquals("Adele", MusicNearMatch.best("adel", names))
        assertNull("two names as close", MusicNearMatch.best("mose", names))
        assertNull("another first letter", MusicNearMatch.best("kutkast", names))
        assertNull("too short", MusicNearMatch.best("hom", names))
        for (q in listOf("my shoes", "the kids", "a sweater", "some coffee", "hope", "happiness", "radio", "android", "ya", "tiger")) {
            assertNull(q, MusicNearMatch.best(q, names))
        }
    }

    @Test fun theSoundKeyHearsCAndKAlike() {
        assertEquals(MusicNearMatch.sound("outcast"), MusicNearMatch.sound("outkast"))
        assertEquals(1, MusicNearMatch.distance("outcast", "outkast"))
        assertEquals(1, MusicNearMatch.distance("andriod", "android"))
    }
}
