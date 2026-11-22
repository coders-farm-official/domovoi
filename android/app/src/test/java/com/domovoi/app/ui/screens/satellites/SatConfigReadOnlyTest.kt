package com.domovoi.app.ui.screens.satellites

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

/** A room's settings are shown, not edited, on a phone (saving them is
 *  admin): each value reads as words, as on the iPhone. */
class SatConfigReadOnlyTest {

    private fun field(type: String, value: kotlinx.serialization.json.JsonElement?, unit: String? = null,
                      labels: Map<String, String>? = null) =
        SatConfigField(name = "x", type = type, value = value, unit = unit, choice_labels = labels)

    @Test fun valuesReadAsWords() {
        assertEquals("on", configValueText(field("bool", JsonPrimitive(true))))
        assertEquals("off", configValueText(field("bool", JsonPrimitive(false))))
        assertEquals("12 dB", configValueText(field("int", JsonPrimitive(12), unit = "dB")))
        assertEquals("0.5", configValueText(field("float", JsonPrimitive(0.5))))
        assertEquals(
            "Spoken greeting, then listen",
            configValueText(field("choice", JsonPrimitive("greeting"), labels = mapOf("greeting" to "Spoken greeting, then listen"))),
        )
        assertEquals("plughw:1", configValueText(field("str", JsonPrimitive("plughw:1"))))
    }

    @Test fun nothingReadsAsADash() {
        assertEquals("—", configValueText(field("str", null)))
        assertEquals("—", configValueText(field("str", JsonNull)))
        assertEquals("—", configValueText(field("str", JsonPrimitive(""))))
    }
}
