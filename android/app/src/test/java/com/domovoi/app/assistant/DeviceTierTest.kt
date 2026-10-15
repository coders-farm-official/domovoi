package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The RAM floor (DeviceTier): 8 GB as sold. Android reports a little less
 * than the chips hold, so the tier is decided on the size the phone is sold
 * as, read off the reported total.
 */
class DeviceTierTest {

    private val gib = 1L shl 30

    private fun tier(totalGib: Double, lowRam: Boolean = false) =
        DeviceTiers.of(PhoneMemory((totalGib * gib).toLong(), lowRam))

    @Test fun everyEightGigabytePhoneIsIn() {
        // What 8 GB phones report: 7.2-7.8 GiB (the kernel, modem and GPU keep some).
        listOf(7.2, 7.4, 7.6, 7.8, 8.0).forEach { assertEquals("$it GiB", DeviceTier.Standard, tier(it)) }
        assertEquals(8, DeviceTiers.marketedGb((7.3 * gib).toLong()))
    }

    @Test fun everySixGigabytePhoneIsOut() {
        listOf(5.3, 5.6, 5.8, 6.0).forEach { assertEquals("$it GiB", DeviceTier.Unsupported, tier(it)) }
        listOf(1.8, 2.8, 3.7).forEach { assertEquals("$it GiB", DeviceTier.Unsupported, tier(it)) }
        assertEquals(6, DeviceTiers.marketedGb((5.6 * gib).toLong()))
        assertEquals(4, DeviceTiers.marketedGb((3.7 * gib).toLong()))
    }

    @Test fun twelveGigabytesAndUpIsHigh() {
        assertEquals(DeviceTier.Standard, tier(9.5))   // a 10 GB phone
        listOf(11.2, 11.6, 15.8, 23.5).forEach { assertEquals("$it GiB", DeviceTier.High, tier(it)) }
        // The owner's 16 GB phone (M0): 15.8 GB reported.
        assertEquals(16, DeviceTiers.marketedGb((15.8 * gib).toLong()))
    }

    @Test fun aLowRamDeviceIsOutWhateverItsSize() {
        assertEquals(DeviceTier.Unsupported, tier(7.6, lowRam = true))
        assertEquals(DeviceTier.Unsupported, tier(15.8, lowRam = true))
    }

    @Test fun noReadingIsNoAssistant() {
        assertEquals(0, DeviceTiers.marketedGb(0))
        assertEquals(DeviceTier.Unsupported, DeviceTiers.of(PhoneMemory(0, false)))
        // Bigger than any size listed: rounded up to whole gigabytes.
        assertEquals(96, DeviceTiers.marketedGb(95L * gib + 1))
    }

    @Test fun theFloorIsTheOwnersEightGigabytes() {
        assertEquals(8, DeviceTiers.MIN_GB)
        assertEquals(12, DeviceTiers.HIGH_GB)
    }
}
