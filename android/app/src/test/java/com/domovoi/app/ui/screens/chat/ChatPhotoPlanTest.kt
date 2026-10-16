package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.ApiException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * Chat photos from phones (2026-10-16): many phones save camera photos as
 * HEIC/HEIF (some AVIF), which the server's chat upload refuses. The app
 * decides by the bytes whether to send a picture as it is or convert it to a
 * JPEG, at a chat size under the server's 40 megapixels, and says in words
 * why one couldn't be sent. The sniffer's vectors are the server's own
 * (domovoi/tests/test_picture_safety.py), so both name a photo alike.
 */
class ChatPhotoPlanTest {

    private fun bytes(vararg b: Int) = ByteArray(b.size) { b[it].toByte() }
    private fun ascii(s: String) = s.toByteArray(Charsets.ISO_8859_1)

    /** An ISO-BMFF `ftyp` box (major brand, minor 0, compatible brands), as
     *  HEIC/HEIF/AVIF files start, then some padding. */
    private fun ftyp(major: String, vararg compatible: String): ByteArray {
        val body = ascii(major) + ByteArray(4) + compatible.fold(ByteArray(0)) { acc, b -> acc + ascii(b) }
        val size = 8 + body.size
        return bytes(size ushr 24, (size ushr 16) and 0xFF, (size ushr 8) and 0xFF, size and 0xFF) +
            ascii("ftyp") + body + bytes(0, 0, 0, 8) + ascii("free") + ByteArray(64)
    }

    // ── what the bytes are ────────────────────────────────────────────────

    @Test fun theKindsTheServerTakesAreNamedByTheirBytes() {
        assertEquals(PictureKind.JPEG, sniffPicture(bytes(0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10) + ascii("JFIF")))
        assertEquals(PictureKind.JPEG, sniffPicture(bytes(0xFF, 0xD8, 0xFF, 0xE1) + ascii("..Exif")))
        assertEquals(PictureKind.PNG, sniffPicture(bytes(0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0)))
        assertEquals(PictureKind.GIF, sniffPicture(ascii("GIF89a....")))
        assertEquals(PictureKind.GIF, sniffPicture(ascii("GIF87a....")))
        assertEquals(PictureKind.WEBP, sniffPicture(ascii("RIFF") + bytes(1, 2, 3, 4) + ascii("WEBPVP8 ")))
        assertEquals(PictureKind.BMP, sniffPicture(ascii("BM") + ByteArray(20)))
    }

    @Test fun phoneCameraFormatsAreNamed() {
        assertEquals(PictureKind.HEIC, sniffPicture(ftyp("heic", "mif1", "heic")))
        // Samsung and others: a generic major brand, HEVC among the compatible ones.
        assertEquals(PictureKind.HEIC, sniffPicture(ftyp("mif1", "mif1", "heic", "miaf")))
        assertEquals(PictureKind.HEIC, sniffPicture(ftyp("heix", "mif1", "heix")))
        assertEquals(PictureKind.HEIF, sniffPicture(ftyp("mif1", "mif1")))
        assertEquals(PictureKind.AVIF, sniffPicture(ftyp("avif", "mif1", "avif", "miaf")))
        assertEquals(PictureKind.AVIF, sniffPicture(ftyp("avis", "avis", "msf1")))
        assertEquals(PictureKind.TIFF, sniffPicture(ascii("II*") + ByteArray(20)))
        assertEquals(PictureKind.TIFF, sniffPicture(ascii("MM") + bytes(0, 0x2A) + ByteArray(20)))
        assertEquals(
            PictureKind.JPEG_XL,
            sniffPicture(bytes(0, 0, 0, 0x0C) + ascii("JXL ") + bytes(0x0D, 0x0A, 0x87, 0x0A) + ByteArray(8)),
        )
        assertEquals(PictureKind.JPEG_XL, sniffPicture(bytes(0xFF, 0x0A, 0, 0)))
    }

    @Test fun anythingElseIsUnknown() {
        assertEquals(PictureKind.UNKNOWN, sniffPicture(ByteArray(0)))
        assertEquals(PictureKind.UNKNOWN, sniffPicture(ascii("%PDF-1.7")))
        assertEquals(PictureKind.UNKNOWN, sniffPicture(ascii("<svg xmlns=")))
        assertEquals(PictureKind.UNKNOWN, sniffPicture(ftyp("mp42", "isom", "mp42")))   // a video
        assertEquals(PictureKind.UNKNOWN, sniffPicture(bytes(0xFF, 0xD8)))                // cut short
        assertEquals(PictureKind.UNKNOWN, sniffPicture(ascii("BM")))
    }

    // ── convert, or send as it is ─────────────────────────────────────────

    @Test fun aPhotoTheServerTakesGoesAsItIs() {
        for (kind in listOf(PictureKind.JPEG, PictureKind.PNG, PictureKind.GIF, PictureKind.WEBP)) {
            assertEquals(PhotoPlan.AsIs(kind), photoPlan(kind, 4032, 3024, 4_000_000))
        }
        // Exactly at the limits still fits.
        assertEquals(PhotoPlan.AsIs(PictureKind.JPEG), photoPlan(PictureKind.JPEG, 8000, 5000, SERVER_MAX_BYTES))
    }

    @Test fun anyOtherKindIsConverted() {
        for (kind in listOf(
            PictureKind.HEIC, PictureKind.HEIF, PictureKind.AVIF, PictureKind.BMP,
            PictureKind.TIFF, PictureKind.JPEG_XL, PictureKind.UNKNOWN,
        )) {
            assertEquals(PhotoPlan.Convert(kind, ConvertReason.FORMAT), photoPlan(kind, 4032, 3024, 2_000_000))
            // Whatever its size, even unknown.
            assertEquals(PhotoPlan.Convert(kind, ConvertReason.FORMAT), photoPlan(kind, -1, -1, 100))
        }
        assertTrue(PictureKind.entries.filter { it.sendsAsIs }.all { it.ext != null && it.mime != null })
    }

    @Test fun aPictureOverTheServersLimitsIsConvertedToo() {
        // 50 MP (a 50 MP phone camera's full-size JPEG): over 40.
        assertEquals(
            PhotoPlan.Convert(PictureKind.JPEG, ConvertReason.PIXELS),
            photoPlan(PictureKind.JPEG, 8160, 6120, 15_000_000),
        )
        assertEquals(
            PhotoPlan.Convert(PictureKind.PNG, ConvertReason.PIXELS),
            photoPlan(PictureKind.PNG, 8001, 5000, 1_000),
        )
        assertEquals(
            PhotoPlan.Convert(PictureKind.JPEG, ConvertReason.BYTES),
            photoPlan(PictureKind.JPEG, 6000, 4000, SERVER_MAX_BYTES + 1),
        )
    }

    @Test fun aPictureWhoseSizeTheHeaderDoesntGiveGoesAsItIs() {
        // The server then reads it, and says what is wrong in its own words.
        assertEquals(PhotoPlan.AsIs(PictureKind.JPEG), photoPlan(PictureKind.JPEG, -1, -1, 3_000))
        assertEquals(PhotoPlan.AsIs(PictureKind.WEBP), photoPlan(PictureKind.WEBP, 0, 0, 3_000))
    }

    // ── the size a converted photo gets ───────────────────────────────────

    @Test fun theChatSizeIs2560OnTheLongEdge() {
        assertEquals(2560, CHAT_PHOTO_MAX_EDGE)
        assertEquals(PhotoSize(2560, 1920), chatPhotoSize(4032, 3024))      // 12 MP, 4:3
        assertEquals(PhotoSize(1920, 2560), chatPhotoSize(3024, 4032))      // portrait
        assertEquals(PhotoSize(2560, 1920), chatPhotoSize(8160, 6120))      // 50 MP
        assertEquals(PhotoSize(2560, 1440), chatPhotoSize(4000, 2250))      // 16:9
        assertEquals(PhotoSize(2560, 512), chatPhotoSize(20000, 4000))      // a panorama
        assertEquals(PhotoSize(2560, 1), chatPhotoSize(100_000, 1))         // never 0 high
    }

    @Test fun aSmallPhotoIsNeverEnlarged() {
        assertEquals(PhotoSize(1000, 800), chatPhotoSize(1000, 800))
        assertEquals(PhotoSize(2560, 2560), chatPhotoSize(2560, 2560))
        assertEquals(PhotoSize(1, 1), chatPhotoSize(1, 1))
    }

    @Test fun theResultAlwaysFitsTheServersLimit() {
        assertTrue(CHAT_PHOTO_MAX_EDGE.toLong() * CHAT_PHOTO_MAX_EDGE <= SERVER_MAX_PIXELS)
        // With no edge limit, the pixel limit alone: 48 MP down to just under 40.
        val s = chatPhotoSize(8000, 6000, maxEdge = Int.MAX_VALUE)
        assertEquals(PhotoSize(7303, 5477), s)
        assertTrue(s.width.toLong() * s.height <= SERVER_MAX_PIXELS)
        for ((w, h) in listOf(9999 to 7777, 12345 to 6789, 40_000 to 1001, 7 to 9_000_000)) {
            val t = chatPhotoSize(w, h)
            assertTrue("$w x $h -> $t", t.width in 1..CHAT_PHOTO_MAX_EDGE && t.height in 1..CHAT_PHOTO_MAX_EDGE)
            val u = chatPhotoSize(w, h, maxEdge = Int.MAX_VALUE)
            assertTrue("$w x $h -> $u", u.width.toLong() * u.height <= SERVER_MAX_PIXELS)
        }
    }

    // ── what a person reads ───────────────────────────────────────────────

    @Test fun aHeicPhotoOnAnOldPhoneSaysWhatToDo() {
        assertEquals(
            "This photo is in HEIC format, and this phone can't convert HEIC photos " +
                "(that needs Android 9 or newer). Save it as a JPEG and send it again.",
            photoUnreadableText(PictureKind.HEIC, sdk = 27),
        )
        assertEquals(
            "This photo is in AVIF format, and this phone can't convert AVIF photos " +
                "(that needs Android 12 or newer). Save it as a JPEG and send it again.",
            photoUnreadableText(PictureKind.AVIF, sdk = 30),
        )
    }

    @Test fun aPhotoThisPhoneCantConvertSaysSo() {
        // Android 9+ decodes HEIC: one that fails is that file's trouble
        // (the emulator check's cut-short HEIC, 2026-10-16).
        assertEquals(
            "This HEIC photo couldn't be opened on this phone; it may be damaged. Save it as a JPEG and send it again.",
            photoUnreadableText(PictureKind.HEIC, sdk = 34),
        )
        assertEquals(
            "This AVIF photo couldn't be opened on this phone; it may be damaged. Save it as a JPEG and send it again.",
            photoUnreadableText(PictureKind.AVIF, sdk = 31),
        )
        assertEquals(
            "This photo is in TIFF format, which this phone can't convert. Save it as a JPEG and send it again.",
            photoUnreadableText(PictureKind.TIFF, sdk = 34),
        )
        assertEquals(
            "This JPEG picture couldn't be opened on this phone; it may be damaged.",
            photoUnreadableText(PictureKind.JPEG, sdk = 34),
        )
        assertEquals(
            "That file isn't a picture this phone can open, so it can't be sent. " +
                "The chat takes JPEG, PNG, GIF, WebP and BMP pictures, and converts other phone photos.",
            photoUnreadableText(PictureKind.UNKNOWN, sdk = 34),
        )
    }

    @Test fun theServersRefusalReadsInItsOwnWords() {
        val heic = """{"detail":"That picture is in HEIC format, which the chat can't read. Send a JPEG, PNG, GIF, WebP or BMP.","code":"unsupported_format","format":"HEIC"}"""
        assertEquals(
            "That picture is in HEIC format, which the chat can't read. Send a JPEG, PNG, GIF, WebP or BMP.",
            uploadRefusalText(415, heic),
        )
        val big = """{"detail":"That picture is too large for the chat: 9000 x 6000 is 54 megapixels; at most 40 are decoded.","code":"too_many_pixels","max_megapixels":40}"""
        assertEquals(
            "That picture is too large for the chat: 9000 x 6000 is 54 megapixels; at most 40 are decoded.",
            uploadRefusalText(413, big),
        )
        val file = """{"detail":"That picture is too large for the chat: more than 20 MB.","code":"file_too_large","max_mb":20}"""
        assertEquals("That picture is too large for the chat: more than 20 MB.", uploadRefusalText(413, file))
    }

    @Test fun anOlderServersBareRefusalStillReadsPlainly() {
        assertEquals(
            "That file isn't a picture the chat can read. Send a JPEG, PNG, GIF, WebP or BMP.",
            uploadRefusalText(415, """{"detail":"unsupported image type .heic"}"""),
        )
        // The body-size guard in front of every route (no code).
        assertEquals(
            "That picture is too large for the chat.",
            uploadRefusalText(413, """{"detail":"request body too large (limit 22020096 bytes)"}"""),
        )
        assertNull(uploadRefusalText(500, "Internal Server Error"))
        assertNull(uploadRefusalText(401, """{"detail":"device token required"}"""))
        // A code it doesn't know is not worded as one of these.
        assertNull(uploadRefusalText(409, """{"detail":"x","code":"something_else"}"""))
    }

    @Test fun everyFailureEndsAsASentence() {
        assertEquals(
            photoUnreadableText(PictureKind.HEIC, 26),
            photoFailureText(PhotoUnreadable(PictureKind.HEIC), sdk = 26),
        )
        val refused = ApiException(
            415, "415 Unsupported Media Type: {...}",
            body = """{"detail":"That file isn't a picture the chat can read. Send a JPEG, PNG, GIF, WebP or BMP.","code":"unsupported_format","format":null}""",
        )
        assertEquals(
            "That file isn't a picture the chat can read. Send a JPEG, PNG, GIF, WebP or BMP.",
            photoFailureText(refused, sdk = 34),
        )
        assertEquals("upload failed (offline?)", photoFailureText(IOException("reset"), sdk = 34))
        assertEquals(
            "upload failed: 500 Internal Server Error: boom",
            photoFailureText(ApiException(500, "500 Internal Server Error: boom", body = "boom"), sdk = 34),
        )
    }

    @Test fun theCodesAreTheServers() {
        // web/backend/api/chat.py UPLOAD_REFUSAL_CODES.
        assertEquals(setOf("unsupported_format", "too_many_pixels", "file_too_large"), UPLOAD_REFUSAL_CODES)
        assertEquals(40_000_000L, SERVER_MAX_PIXELS)
        assertEquals(20L * 1024 * 1024, SERVER_MAX_BYTES)
    }
}
