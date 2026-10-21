package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.failureText
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.IOException
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/*
 * Photos from the phone into the chat (2026-10-16). Many phones save camera
 * photos as HEIC/HEIF (some as AVIF), which the server's chat upload refuses:
 * it reads only JPEG, PNG, GIF, WebP and BMP (domovoi/image_safety.py), with
 * no decoder for anything else (the licence policy keeps libheif out). So the
 * phone decides, by the picture's own bytes rather than the name or the type
 * the gallery reports, whether to send it as it is or to convert it to a JPEG
 * first (ChatPhotoConvert.kt). This file is the pure part: what the bytes
 * are, the decision, the size a converted photo gets, and the words a person
 * reads when a photo can't be sent. Unit-tested on the JVM (ChatPhotoPlanTest).
 */

/** The server's picture limits (web/backend/api/chat.py, image_safety.py). */
internal const val SERVER_MAX_PIXELS = 40_000_000L
internal const val SERVER_MAX_BYTES = 20L * 1024 * 1024

/**
 * The long edge of a converted photo. 2560 px: twice the 1280 px the vision
 * model is given (the server shrinks it cleanly from here), sharp full screen
 * on a phone and on a 1440p monitor, about 1 MB as a JPEG, and a 4:3 photo is
 * 4.9 MP, far under the server's 40 MP however the photo is shaped. The phone
 * decodes straight to this size, so even a 50 MP HEIC never sits in memory at
 * full size (26 MB at most for the decoded picture).
 */
internal const val CHAT_PHOTO_MAX_EDGE = 2560

/** The converted JPEG's quality: the server's own copy for the model uses 90. */
internal const val CHAT_PHOTO_JPEG_QUALITY = 90

/**
 * What a picture is, by its first bytes. [ext]/[mime] are set for the kinds
 * the phone sends as they are; every other kind is converted to a JPEG.
 * [label] names it in a message ("This phone can't convert HEIC photos").
 */
internal enum class PictureKind(val label: String, val ext: String?, val mime: String?) {
    JPEG("JPEG", "jpg", "image/jpeg"),
    PNG("PNG", "png", "image/png"),
    GIF("GIF", "gif", "image/gif"),
    WEBP("WebP", "webp", "image/webp"),
    // The server takes BMP, but it is uncompressed: a JPEG is a fraction of it.
    BMP("BMP", null, null),
    HEIC("HEIC", null, null),
    HEIF("HEIF", null, null),
    AVIF("AVIF", null, null),
    JPEG_XL("JPEG XL", null, null),
    TIFF("TIFF", null, null),
    UNKNOWN("", null, null),
    ;

    val sendsAsIs: Boolean get() = ext != null

    /** The Android version that first decodes this kind (ImageDecoder), when
     *  it is one some phones can't: [android.os.Build.VERSION_CODES.P] for
     *  HEIF/HEIC, [android.os.Build.VERSION_CODES.S] for AVIF. */
    val decodableFromSdk: Int?
        get() = when (this) {
            HEIC, HEIF -> 28
            AVIF -> 31
            else -> null
        }
}

private fun ByteArray.at(i: Int): Int = if (i < size) this[i].toInt() and 0xFF else -1

private fun ByteArray.ascii(from: Int, len: Int): String? =
    if (from + len > size) null else String(this, from, len, Charsets.ISO_8859_1)

private val HEIC_BRANDS = setOf("heic", "heix", "hevc", "hevx", "heim", "heis", "hevm", "hevs")
private val HEIF_BRANDS = setOf("mif1", "msf1")
private val AVIF_BRANDS = setOf("avif", "avis")

/** JPEG XL's container signature box. */
private val JXL_BOX = byteArrayOf(0, 0, 0, 0x0C, 0x4A, 0x58, 0x4C, 0x20, 0x0D, 0x0A, 0x87.toByte(), 0x0A)

private fun ByteArray.startsWith(prefix: ByteArray): Boolean =
    size >= prefix.size && prefix.indices.all { this[it] == prefix[it] }

/**
 * What kind of picture [head] (a file's first bytes; 64 is plenty) is, by
 * its magic bytes alone. The same rules as the server's `picture_format_of`
 * (web/backend/api/chat.py), so the two name a photo alike.
 */
internal fun sniffPicture(head: ByteArray): PictureKind {
    val b = head
    return when {
        b.at(0) == 0xFF && b.at(1) == 0xD8 && b.at(2) == 0xFF -> PictureKind.JPEG
        b.at(0) == 0x89 && b.ascii(1, 3) == "PNG" && b.at(4) == 0x0D && b.at(5) == 0x0A &&
            b.at(6) == 0x1A && b.at(7) == 0x0A -> PictureKind.PNG
        b.ascii(0, 6) == "GIF87a" || b.ascii(0, 6) == "GIF89a" -> PictureKind.GIF
        b.ascii(0, 4) == "RIFF" && b.ascii(8, 4) == "WEBP" -> PictureKind.WEBP
        b.ascii(0, 2) == "BM" && b.size >= 14 -> PictureKind.BMP
        b.ascii(4, 4) == "ftyp" -> {
            val size = (b.at(0) shl 24) or (b.at(1) shl 16) or (b.at(2) shl 8) or b.at(3)
            val end = min(b.size, if (size in 16..4096) size else 16)
            // The major brand, then the compatible ones (minor version skipped).
            val brands = listOfNotNull(b.ascii(8, 4)) +
                (16 until end - 3 step 4).mapNotNull { b.ascii(it, 4) }
            when {
                brands.any { it in AVIF_BRANDS } -> PictureKind.AVIF
                brands.any { it in HEIC_BRANDS } -> PictureKind.HEIC
                brands.any { it in HEIF_BRANDS } -> PictureKind.HEIF
                else -> PictureKind.UNKNOWN
            }
        }
        (b.at(0) == 0xFF && b.at(1) == 0x0A) || b.startsWith(JXL_BOX) -> PictureKind.JPEG_XL
        b.ascii(0, 4) == "II*\u0000" || b.ascii(0, 4) == "MM\u0000*" -> PictureKind.TIFF
        else -> PictureKind.UNKNOWN
    }
}

/** Why a picture is converted before it is sent. */
internal enum class ConvertReason { FORMAT, PIXELS, BYTES }

/** Send the picked picture as it is, or convert it to a JPEG first. */
internal sealed interface PhotoPlan {
    data class AsIs(val kind: PictureKind) : PhotoPlan
    data class Convert(val kind: PictureKind, val reason: ConvertReason) : PhotoPlan
}

/**
 * The decision. A JPEG, PNG, GIF or WebP the server takes as it is goes as
 * it is (byte for byte, its metadata with it, as before). Anything else is
 * converted: another kind (HEIC, HEIF, AVIF, BMP, one the phone can't name),
 * or one of those four with more than the server's 40 megapixels or 20 MB.
 * [width]/[height] are the picture's, read from its header; 0 (or less) when
 * the phone couldn't read them, and then a picture of a kind the server takes
 * goes as it is and the server says what is wrong with it.
 */
internal fun photoPlan(kind: PictureKind, width: Int, height: Int, byteCount: Long): PhotoPlan {
    if (!kind.sendsAsIs) return PhotoPlan.Convert(kind, ConvertReason.FORMAT)
    if (width > 0 && height > 0 && width.toLong() * height > SERVER_MAX_PIXELS) {
        return PhotoPlan.Convert(kind, ConvertReason.PIXELS)
    }
    if (byteCount > SERVER_MAX_BYTES) return PhotoPlan.Convert(kind, ConvertReason.BYTES)
    return PhotoPlan.AsIs(kind)
}

internal data class PhotoSize(val width: Int, val height: Int)

/**
 * The size a converted photo gets: its own shape, at most [maxEdge] on the
 * long edge and [maxPixels] in all, never enlarged; each side at least 1.
 */
internal fun chatPhotoSize(
    width: Int,
    height: Int,
    maxEdge: Int = CHAT_PHOTO_MAX_EDGE,
    maxPixels: Long = SERVER_MAX_PIXELS,
): PhotoSize {
    require(width > 0 && height > 0) { "no size: $width x $height" }
    val byEdge = maxEdge.toDouble() / max(width, height)
    val byPixels = sqrt(maxPixels.toDouble() / (width.toLong() * height))
    val scale = minOf(1.0, byEdge, byPixels)
    if (scale >= 1.0) return PhotoSize(width, height)
    // Rounded (4032 x 3024 is 2560 x 1920, not 1919), then trimmed back
    // inside both limits in the rare case rounding stepped over one.
    var w = max(1, (width * scale).roundToInt())
    var h = max(1, (height * scale).roundToInt())
    while (max(w, h) > maxEdge || w.toLong() * h > maxPixels) { if (w >= h) w-- else h-- }
    return PhotoSize(w, h)
}

/**
 * Why the phone couldn't send a picture it couldn't open: a kind this
 * Android version doesn't decode (HEIC before Android 9, AVIF before 12),
 * one no Android here decodes (JPEG XL, TIFF), a damaged one, or a file
 * that isn't a picture at all.
 */
internal fun photoUnreadableText(kind: PictureKind, sdk: Int): String {
    val since = kind.decodableFromSdk
    return when {
        since != null && sdk < since ->
            "This photo is in ${kind.label} format, and this phone can't convert ${kind.label} photos " +
                "(that needs Android ${androidVersionName(since)} or newer). " +
                "Save it as a JPEG and send it again."
        kind == PictureKind.UNKNOWN ->
            "That file isn't a picture this phone can open, so it can't be sent. " +
                "The chat takes JPEG, PNG, GIF, WebP and BMP pictures, and converts other phone photos."
        kind.sendsAsIs || kind == PictureKind.BMP ->
            "This ${kind.label} picture couldn't be opened on this phone; it may be damaged."
        // This Android decodes the kind, so this one file is the trouble.
        since != null ->
            "This ${kind.label} photo couldn't be opened on this phone; it may be damaged. " +
                "Save it as a JPEG and send it again."
        else ->
            "This photo is in ${kind.label} format, which this phone can't convert. " +
                "Save it as a JPEG and send it again."
    }
}

private fun androidVersionName(sdk: Int): String = when (sdk) {
    28 -> "9"
    31 -> "12"
    else -> "API $sdk"
}

/** The phone couldn't open a picture it had to convert ([kind]: what its
 *  bytes say it is). */
internal class PhotoUnreadable(val kind: PictureKind) :
    IOException("this phone can't open the picture (${kind.label.ifEmpty { "unknown kind" }})")

/**
 * The note a person reads when a picked picture didn't reach the chat:
 * the phone couldn't convert it, the server refused it (its own words), or
 * the upload failed ([failureText]: the server's status, or "offline?").
 */
internal fun photoFailureText(e: Throwable, sdk: Int): String = when (e) {
    is PhotoUnreadable -> photoUnreadableText(e.kind, sdk)
    // The household's AI features are off: no picture is taken for a model.
    is ApiException -> if (isAiOffRefusal(e)) AI_CHAT_OFF_TEXT else uploadRefusalText(e.status, e.body) ?: failureText("upload", e)
    else -> failureText("upload", e)
}

/** The codes of the server's upload refusals (chat.py UPLOAD_REFUSAL_CODES). */
internal val UPLOAD_REFUSAL_CODES = setOf("unsupported_format", "too_many_pixels", "file_too_large")

/**
 * What a person reads when the server refused an upload, from its status
 * and body: the server's own sentence for a coded refusal (`{detail, code}`),
 * the same words for an older server's bare 415/413, null for anything else
 * (the caller says "upload failed" with the reason).
 */
internal fun uploadRefusalText(status: Int, body: String): String? {
    val obj = runCatching { DomovoiJson.parseToJsonElement(body) as? JsonObject }.getOrNull()
    fun str(key: String) = (obj?.get(key) as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim()
    val code = str("code")
    val detail = str("detail")
    if (code in UPLOAD_REFUSAL_CODES && !detail.isNullOrEmpty()) return detail
    return when (status) {
        415 -> "That file isn't a picture the chat can read. Send a JPEG, PNG, GIF, WebP or BMP."
        413 -> "That picture is too large for the chat."
        else -> null
    }
}
