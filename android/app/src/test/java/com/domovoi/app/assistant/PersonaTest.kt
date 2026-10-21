package com.domovoi.app.assistant

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonaTest {

    @Test fun thePromptNamesDomovoi() {
        assertTrue(Persona.SYSTEM_PROMPT.startsWith("You are Domovoi,"))
    }

    /** The only product name is Domovoi (AGENTS.md): no reserved name, and
     *  no other assistant or model brand the model might adopt. */
    @Test fun thePromptNamesNoOtherProduct() {
        val banned = listOf(
            Regex("har[l]ey", RegexOption.IGNORE_CASE),
            Regex("ric[h]ard", RegexOption.IGNORE_CASE),
            Regex("orche[s]trator", RegexOption.IGNORE_CASE),
            Regex("\\b(gemma|gemini|google|siri|alexa|chatgpt|openai|assistant app)\\b", RegexOption.IGNORE_CASE),
        )
        listOf(Persona.SYSTEM_PROMPT, AssistantBench.HEAT_SYSTEM_PROMPT).forEach { prompt ->
            banned.forEach { assertFalse("${it.pattern} in: $prompt", it.containsMatchIn(prompt)) }
        }
    }

    @Test fun thePromptSaysWhatThePhoneCannotDo() {
        val p = Persona.SYSTEM_PROMPT
        listOf("no internet", "timer", "music", "files", "rooms of the house").forEach { assertTrue(it, p.contains(it)) }
    }

    /** N2: the app does the phone's actions, never the model on E2B; a
     *  request that reaches the model was not done, and it must not say so. */
    @Test fun thePromptSaysTheAppDoesTheActionsNotTheModel() {
        val p = Persona.SYSTEM_PROMPT
        assertTrue(p.contains("The app itself, not you"))
        assertTrue(p.contains("If such a request reaches you, it was not done: never say it was"))
        listOf("Clock app", "sleep timer", "audiobook", "video").forEach { assertTrue(it, p.contains(it)) }
    }

    /** The owner's report (2026-10-16): "I cannot tell you the time. I do not
     *  have access to that information." The prompt says it has the clock. */
    @Test fun thePromptSaysThePhoneHasAClock() {
        val p = Persona.SYSTEM_PROMPT
        assertTrue(p.contains("You do have the phone's clock"))
        listOf("the time", "the date", "the day").forEach { assertTrue(it, p.contains(it)) }
        assertFalse(Regex("(no|without|cannot|can't|don't have) (access to )?(the )?(time|clock|date)", RegexOption.IGNORE_CASE)
            .containsMatchIn(p))
    }
}
