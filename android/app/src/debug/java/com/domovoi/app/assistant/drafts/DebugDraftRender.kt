package com.domovoi.app.assistant.drafts

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import java.io.File

/**
 * DEBUG BUILDS ONLY: renders drafts with the phone's own PDF renderer
 * ([DraftPdf], the one a phone chat's "save PDF" uses) from markdown files,
 * for the maintainers' check that a template's PDF text is identical from the
 * server and the phone (documents plan D4's gate; the harness lives outside
 * this repo).
 *
 *     adb push resume-plain__resume.md /sdcard/Android/data/com.domovoi.app/files/drafts-in/
 *     adb shell am broadcast -a com.domovoi.app.debug.DRAFT_RENDER -n com.domovoi.app/.assistant.drafts.DebugDraftRender
 *     adb pull /sdcard/Android/data/com.domovoi.app/files/drafts-out/
 *
 * Each `<template>__<name>.md` becomes `drafts-out/<template>__<name>.pdf`.
 * Only a sender holding DUMP may send it (adb's shell does; an installed app
 * cannot), and a release APK does not contain it.
 */
class DebugDraftRender : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val inDir = context.getExternalFilesDir("drafts-in") ?: return
        val outDir = context.getExternalFilesDir("drafts-out") ?: return
        val pending = goAsync()
        Thread {
            try {
                val assets = DraftAssets(context.applicationContext)
                for (f in inDir.listFiles().orEmpty().filter { it.isFile && it.name.endsWith(".md") }) {
                    val template = f.name.substringBefore("__")
                    if (template !in DraftTemplates.IDS) {
                        Log.w(TAG, "skipped ${f.name}: no template $template")
                        continue
                    }
                    val bytes = DraftPdf.render(assets, f.readText(), template)
                    File(outDir, f.name.removeSuffix(".md") + ".pdf").writeBytes(bytes)
                    Log.i(TAG, "rendered ${f.name} (${bytes.size} bytes)")
                }
            } catch (e: Exception) {
                Log.e(TAG, "render failed", e)
            } finally {
                pending.finish()
            }
        }.start()
    }

    private companion object {
        const val TAG = "DraftRender"
    }
}
