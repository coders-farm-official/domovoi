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
}
