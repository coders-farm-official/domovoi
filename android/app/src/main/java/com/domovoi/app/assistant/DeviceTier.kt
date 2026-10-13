package com.domovoi.app.assistant

import android.app.ActivityManager
import android.content.Context

/**
 * Whether this phone can run the on-device assistant, from its memory.
 *
 * - [Unsupported]: under 8 GB, or a phone Android itself calls low-RAM. The
 *   assistant says so plainly and offers no download (owner rule: no control
 *   that cannot do what it says).
 * - [Standard]: 8 GB up to 12 GB; Gemma 4 E2B on the CPU (the default) or,
 *   if the owner turns it on, the GPU.
 * - [High]: 12 GB or more. The same model in v1; room for a larger one later.
 *
 * M0 measured the model at about 1.7 GB of app memory at its peak, with
 * 5.4-5.9 GB of the phone still free on a 16 GB phone; the 8 GB floor is the
 * plan's starting threshold, kept by the owner on 2026-10-12.
 */
enum class DeviceTier { Unsupported, Standard, High }

/** What [DeviceTiers.of] decides from: Android's own numbers. */
data class PhoneMemory(val totalBytes: Long, val lowRamDevice: Boolean) {
    /** The size the phone is sold as ("8 GB"): [DeviceTiers.marketedGb]. */
    val marketedGb: Int get() = DeviceTiers.marketedGb(totalBytes)
}

object DeviceTiers {
    /** The smallest phone that runs the assistant, as sold. */
    const val MIN_GB = 8

    /** From here up, [DeviceTier.High]. */
    const val HIGH_GB = 12

    /** Memory sizes phones are sold in, GB. */
    private val SOLD_AS = listOf(1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 24, 32, 48, 64)

    private const val GIB = 1L shl 30

    /**
     * The size a phone is sold as, from the total memory Android reports.
     * Android's total is always a little under the chips' size (the kernel,
     * the modem and the GPU keep some: an 8 GB phone reports 7.2 to 7.8 GiB,
     * a 16 GB one about 15.8 GiB), and never over it, so the smallest sold
     * size at or above the reported total is the phone's own. Comparing that,
     * not the raw total, with [MIN_GB] keeps every 8 GB phone in and every
     * 6 GB phone out.
     */
    fun marketedGb(totalBytes: Long): Int {
        if (totalBytes <= 0) return 0
        return SOLD_AS.firstOrNull { it.toLong() * GIB >= totalBytes } ?: ((totalBytes + GIB - 1) / GIB).toInt()
    }

    fun of(memory: PhoneMemory): DeviceTier {
        val gb = memory.marketedGb
        return when {
            memory.lowRamDevice || gb < MIN_GB -> DeviceTier.Unsupported
            gb >= HIGH_GB -> DeviceTier.High
            else -> DeviceTier.Standard
        }
    }

    /** This phone's memory, as Android reports it. */
    fun read(context: Context): PhoneMemory {
        val activity = context.getSystemService(ActivityManager::class.java)
        val info = ActivityManager.MemoryInfo().also { activity.getMemoryInfo(it) }
        return PhoneMemory(info.totalMem, activity.isLowRamDevice)
    }
}
