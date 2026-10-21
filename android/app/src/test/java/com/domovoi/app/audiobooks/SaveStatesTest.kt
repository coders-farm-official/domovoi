package com.domovoi.app.audiobooks

import androidx.work.WorkInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The download state machine as the app shows it: the record on disk plus the job. */
class SaveStatesTest {
    private val meta = SavedBookMeta(bookId = 7, title = "Dune", sizeBytes = 1000)

    private fun job(state: WorkInfo.State, attempts: Int = 0, done: Long? = null, total: Long? = null, error: String? = null) =
        BookJob("b7-x", state, attempts, done, total, error)

    @Test fun noRecordIsNotOnThePhone() {
        assertEquals(SaveState.None, SaveStates.of(null, null))
        assertEquals(SaveState.None, SaveStates.of(null, job(WorkInfo.State.RUNNING)))
    }

    @Test fun aCompleteRecordIsSavedWhateverTheJobSays() {
        val done = meta.copy(complete = true)
        assertEquals(SaveState.Saved, SaveStates.of(done, null))
        assertEquals(SaveState.Saved, SaveStates.of(done, job(WorkInfo.State.CANCELLED)))
    }

    @Test fun aRunningJobIsSavingWithItsProgress() {
        val s = SaveStates.of(meta, job(WorkInfo.State.RUNNING, done = 250, total = 1000)) as SaveState.Saving
        assertEquals(25, s.percent)
        // Before the first report: the record's size.
        val early = SaveStates.of(meta, job(WorkInfo.State.RUNNING)) as SaveState.Saving
        assertEquals(1000L, early.total)
        assertEquals(0, early.percent)
        assertNull(SaveState.Saving(10, null).percent)
    }

    @Test fun aQueuedJobWaitsAndSaysWhetherItWasInterrupted() {
        assertEquals(SaveState.Waiting(false), SaveStates.of(meta, job(WorkInfo.State.ENQUEUED)))
        assertEquals(SaveState.Waiting(true), SaveStates.of(meta, job(WorkInfo.State.ENQUEUED, attempts = 2)))
        assertEquals(SaveState.Waiting(false), SaveStates.of(meta, job(WorkInfo.State.BLOCKED)))
    }

    @Test fun aFailedOrCancelledJobIsStopped() {
        assertEquals(SaveState.Stopped("no space"), SaveStates.of(meta, job(WorkInfo.State.FAILED, error = "no space")))
        assertEquals(SaveState.Stopped(null), SaveStates.of(meta, job(WorkInfo.State.CANCELLED)))
        assertEquals(SaveState.Stopped(null), SaveStates.of(meta, job(WorkInfo.State.SUCCEEDED)))
        assertEquals(SaveState.Stopped(null), SaveStates.of(meta, null))
    }

    @Test fun theLabelsAreShortAndLowercase() {
        assertEquals("on this phone", SaveStates.label(SaveState.Saved))
        assertEquals("saving 25%", SaveStates.label(SaveState.Saving(250, 1000)))
        assertEquals("saving…", SaveStates.label(SaveState.Saving(250, null)))
        assertEquals("waiting for Domovoi", SaveStates.label(SaveState.Waiting(false), "Domovoi"))
        assertEquals("paused · resumes when the server is in reach", SaveStates.label(SaveState.Waiting(true)))
        assertEquals("stopped: no space", SaveStates.label(SaveState.Stopped("no space")))
        assertEquals("", SaveStates.label(SaveState.None))
    }
}
