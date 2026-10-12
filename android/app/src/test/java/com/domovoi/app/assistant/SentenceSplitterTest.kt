package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SentenceSplitterTest {

    private fun split(vararg deltas: String): List<String> {
        val s = SentenceSplitter()
        return deltas.flatMap { s.push(it) } + s.finish()
    }

    @Test fun wholeTextSplitsIntoSentences() {
        assertEquals(
            listOf("It is sunny.", "Take a hat!", "Want more?"),
            split("It is sunny. Take a hat! Want more?"),
        )
    }

    @Test fun aSentenceIsReleasedAsSoonAsWhatFollowsItsStopArrives() {
        val s = SentenceSplitter()
        assertEquals(emptyList<String>(), s.push("It is"))
        assertEquals(emptyList<String>(), s.push(" sunny."))      // could still be "sunny.com"
        assertEquals(listOf("It is sunny."), s.push(" Take"))
        assertEquals(emptyList<String>(), s.push(" a hat"))
        assertEquals(listOf("Take a hat"), s.finish())
    }

    @Test fun tokenByTokenStreamingGivesTheSameSentences() {
        val text = "Bread keeps best in a cloth bag. Freeze what you won't eat in two days! Toast it straight from frozen."
        val tokens = text.chunked(3)
        assertEquals(split(text), split(*tokens.toTypedArray()))
        assertEquals(3, split(*tokens.toTypedArray()).size)
    }

    @Test fun abbreviationsAndInitialsDoNotEndASentence() {
        assertEquals(
            listOf("Dr. Smith lives on Elm St. near the park.", "J. R. wrote it, e.g. the first one."),
            split("Dr. Smith lives on Elm St. near the park. J. R. wrote it, e.g. the first one."),
        )
    }

    @Test fun thePronounIEndsASentence() {
        assertEquals(listOf("Neither do I.", "Sorry."), split("Neither do I. Sorry."))
    }

    @Test fun numbersDoNotSplit() {
        assertEquals(listOf("It costs 3.50 dollars, about 2.5 percent more."),
            split("It costs 3.", "50 dollars, about 2.", "5 percent more."))
        assertEquals(listOf("It was built in 1999.", "Then it grew."), split("It was built in 1999. Then it grew."))
    }

    @Test fun repeatedStopsAndClosingQuotesStayWithTheirSentence() {
        assertEquals(listOf("Wait...", "He said \"go!\"", "Fine."), split("Wait... He said \"go!\" Fine."))
    }

    @Test fun lineBreaksEndSentencesAndListItemsLoseTheirMarkers() {
        assertEquals(
            listOf("Three tips:", "Keep it cool", "Wrap it", "Freeze extra"),
            split("Three tips:\n1. Keep it cool\n2. Wrap it\n- Freeze extra\n"),
        )
    }

    @Test fun markdownIsStrippedBeforeSpeech() {
        assertEquals(
            listOf("Use a bread box.", "It works well.", "See the guide."),
            split("## Use a **bread box**.\n*It* works `well`. See [the guide](https://example.org/x)."),
        )
    }

    @Test fun snakeCaseKeepsItsUnderscoresButEmphasisLosesThem() {
        assertEquals("set max_tokens now", SentenceSplitter.speakable("set _max_tokens_ now"))
        assertEquals("very important", SentenceSplitter.speakable("__very__ important"))
    }

    @Test fun codeBlocksAreNotRead() {
        assertEquals(listOf("Run this:", "Done."), split("Run this:\n```kotlin\nprintln(1)\nval x = 2. y\n```\nDone."))
        val streamed = split("Run this:\n``", "`\nprint", "ln(1)\n``", "`\nDone.")
        assertTrue(streamed.none { it.contains("println") })
    }

    @Test fun blankDeltasAndLeadingNewlinesProduceNothing() {
        assertEquals(emptyList<String>(), split("", "\n", "  ", "\n"))
    }
}
