package com.domovoi.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The phone Files tab's folder list (Prefs `phone_folders`, data/PhoneFolders.kt). */
class PhoneFoldersTest {
    private val docs = PhoneFolder("content://com.android.externalstorage.documents/tree/primary%3ADocuments", "Documents")
    private val dl = PhoneFolder("content://com.android.externalstorage.documents/tree/primary%3ADownload", "Download")

    @Test fun encodesAndDecodes() {
        val list = listOf(docs, dl)
        assertEquals(list, PhoneFolders.decode(PhoneFolders.encode(list)))
    }

    @Test fun aMissingOrBrokenValueIsAnEmptyList() {
        assertEquals(emptyList<PhoneFolder>(), PhoneFolders.decode(null))
        assertEquals(emptyList<PhoneFolder>(), PhoneFolders.decode(""))
        assertEquals(emptyList<PhoneFolder>(), PhoneFolders.decode("{not json"))
    }

    @Test fun decodeDropsBlanksAndDuplicates() {
        val raw = """[{"uri":"${docs.uri}","name":"a"},{"uri":"${docs.uri}","name":"b"},{"uri":"","name":"x"},{"uri":"${dl.uri}","name":"Download","extra":1}]"""
        assertEquals(listOf(PhoneFolder(docs.uri, "a"), dl), PhoneFolders.decode(raw))
    }

    @Test fun addingAppendsOrRenamesInPlace() {
        val one = PhoneFolders.withFolder(emptyList(), docs)
        val two = PhoneFolders.withFolder(one, dl)
        assertEquals(listOf(docs, dl), two)
        val renamed = PhoneFolders.withFolder(two, docs.copy(name = "Docs"))
        assertEquals(listOf(docs.copy(name = "Docs"), dl), renamed)
    }

    @Test fun removingLeavesTheRest() {
        assertEquals(listOf(dl), PhoneFolders.without(listOf(docs, dl), docs.uri))
        assertEquals(listOf(docs, dl), PhoneFolders.without(listOf(docs, dl), "content://other"))
    }

    @Test fun aRevokedGrantDropsOut() {
        assertEquals(listOf(dl), PhoneFolders.stillGranted(listOf(docs, dl), setOf(dl.uri, "content://unrelated")))
        assertEquals(emptyList<PhoneFolder>(), PhoneFolders.stillGranted(listOf(docs, dl), emptySet()))
    }

    @Test fun libraryIdsRoundTrip() {
        val id = PhoneFolders.libraryId(docs)
        assertEquals(docs.uri, PhoneFolders.uriOf(id))
        assertNull(PhoneFolders.uriOf(PhoneFolders.PHOTOS_ID))
        assertNull(PhoneFolders.uriOf("core:documents"))
    }
}
