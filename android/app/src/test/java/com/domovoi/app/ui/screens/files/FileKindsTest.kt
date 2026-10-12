package com.domovoi.app.ui.screens.files

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Phone files are classified and opened as the same file on the server would
 * be (files.py `_entry_kind`, the Documents routing FilesScreen uses).
 */
class FileKindsTest {

    @Test fun kindsMatchTheServer() {
        assertEquals("folder", fileKind("Stuff", isDir = true))
        assertEquals("audio", fileKind("song.MP3", false))
        assertEquals(".mp4 is audio first, as on the server", "audio", fileKind("clip.mp4", false))
        assertEquals("video", fileKind("clip.mkv", false))
        assertEquals("pdf", fileKind("a.pdf", false))
        assertEquals("doc-office", fileKind("budget.xlsx", false))
        assertEquals("doc-office", fileKind("list.csv", false))
        assertEquals("doc-office", fileKind("letter.docx", false))
        assertEquals("image", fileKind("photo.JPG", false))
        assertEquals("image", fileKind("logo.svg", false))
        assertEquals("doc-text", fileKind("notes.md", false))
        assertEquals("doc-text", fileKind("scene.excalidraw", false))
        assertEquals("other", fileKind("archive.zip", false))
        assertEquals("other", fileKind("README", false))
    }

    @Test fun extensionsFollowPathSuffix() {
        assertEquals("md", extOf("a.b.MD"))
        assertEquals("", extOf(".bashrc"))
        assertEquals("", extOf("notes."))
        assertEquals("md", extOf(".hidden.md"))
    }

    @Test fun openRouting() {
        assertEquals(OpenWith.SheetEditor, openWith("b.xlsx", fileKind("b.xlsx", false)))
        assertEquals(OpenWith.SheetEditor, openWith("b.csv", fileKind("b.csv", false)))
        assertEquals(OpenWith.TextEditor, openWith("n.md", fileKind("n.md", false)))
        assertEquals(OpenWith.TextEditor, openWith("c.json", fileKind("c.json", false)))
        assertEquals(OpenWith.ImageViewer, openWith("p.png", fileKind("p.png", false)))
        assertEquals("xls and ods are not round-tripped", OpenWith.System, openWith("old.xls", fileKind("old.xls", false)))
        assertEquals(OpenWith.System, openWith("a.pdf", fileKind("a.pdf", false)))
        assertEquals(OpenWith.System, openWith("l.docx", fileKind("l.docx", false)))
    }

    @Test fun mimeTypesForOtherApps() {
        assertEquals("application/pdf", mimeFor("A.PDF"))
        assertEquals("image/jpeg", mimeFor("x.jpeg"))
        assertEquals("application/vnd.openxmlformats-officedocument.wordprocessingml.document", mimeFor("l.docx"))
        assertEquals("application/octet-stream", mimeFor("x.bin"))
    }

    private fun lib(id: String, editable: Boolean = true) = FileLibrary(id = id, label = id, editable = editable)

    @Test fun sendToHomeDefaultsByKind() {
        val libs = listOf(lib("core:music"), lib("core:documents"), lib("core:pictures"), lib("plugin:x"))
        assertEquals("core:pictures", defaultHomeLibrary(FileEntry(name = "p.jpg", kind = "image"), libs))
        assertEquals("core:music", defaultHomeLibrary(FileEntry(name = "s.mp3", kind = "audio"), libs))
        assertEquals("core:documents", defaultHomeLibrary(FileEntry(name = "n.md", kind = "doc-text"), libs))
    }

    @Test fun sendToHomeFallsBackToAnEditableLibrary() {
        val noPictures = listOf(lib("core:pictures", editable = false), lib("core:documents"))
        assertEquals("core:documents", defaultHomeLibrary(FileEntry(name = "p.jpg", kind = "image"), noPictures))
        val onlyPlugin = listOf(lib("core:documents", editable = false), lib("plugin:x"))
        assertEquals("plugin:x", defaultHomeLibrary(FileEntry(name = "n.md", kind = "doc-text"), onlyPlugin))
        assertNull(defaultHomeLibrary(FileEntry(name = "n.md"), listOf(lib("core:music", editable = false))))
    }

    private fun phoneEntry(name: String) = FileEntry(name = name, kind = fileKind(name, false))

    @Test fun aPhoneVideoGoesToTheServersVideosLibrary() {
        val libs = listOf(lib("core:music"), lib("core:documents"), lib("core:pictures"), lib("core:videos"))
        for (name in listOf("clip.mkv", "clip.mov", "clip.webm", "clip.m4v", "VID_20261011_120000.mp4")) {
            assertEquals(name, "core:videos", defaultHomeLibrary(phoneEntry(name), libs))
        }
        // Audio stays Music, .m4a included; only .mp4 is read as the camera's.
        assertEquals("core:music", defaultHomeLibrary(phoneEntry("voice.m4a"), libs))
        assertEquals("core:music", defaultHomeLibrary(phoneEntry("song.mp3"), libs))
        assertEquals("core:pictures", defaultHomeLibrary(phoneEntry("p.jpg"), libs))
        assertEquals("core:documents", defaultHomeLibrary(phoneEntry("n.md"), libs))
    }

    @Test fun aPhoneVideoFallsBackToDocumentsWithoutAVideosLibrary() {
        val older = listOf(lib("core:music"), lib("core:documents"), lib("core:pictures"))
        assertEquals("core:documents", defaultHomeLibrary(phoneEntry("clip.mkv"), older))
        assertEquals("core:documents", defaultHomeLibrary(phoneEntry("VID_1.mp4"), older))
        val readOnlyVideos = older + lib("core:videos", editable = false)
        assertEquals("core:documents", defaultHomeLibrary(phoneEntry("clip.mov"), readOnlyVideos))
    }

    @Test fun theSendSheetSaysUploadOnTheYourDeviceSide() {
        assertEquals(SendHomeWords("upload \"a.pdf\" to…", "upload here", "upload"), SendHomeWords.of("a.pdf", asUpload = true))
        assertEquals(
            "the local shell keeps its words",
            SendHomeWords("send \"a.pdf\" to…", "send here", "send"),
            SendHomeWords.of("a.pdf", asUpload = false),
        )
    }
}
