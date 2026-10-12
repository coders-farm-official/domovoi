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
        listOf("no internet", "timers", "music", "files").forEach { assertTrue(it, p.contains(it)) }
    }
}
