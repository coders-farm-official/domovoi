package com.domovoi.app.assistant

import java.io.File

/**
 * Where downloaded models live: `filesDir/models/<id>/`, app-private, outside
 * every backup (the app's backup rules exclude all domains) and removed on
 * uninstall.
 *
 * A file reaches its final path only after its SHA-256 matched (the
 * downloader renames the `.part` file then), so a file there at its catalog
 * size is a verified file. A model is installed when every file is, and its
 * marker names this catalog's exact files and hashes, so a catalog change in
 * a new app version reads as "not installed" rather than as stale files.
 */
class ModelStore(val root: File) {

    fun dir(model: CatalogModel): File = File(root, model.id)

    fun fileFor(model: CatalogModel, file: ModelFile): File = File(dir(model), file.path)

    /** The model's file at catalog [path]; for building engine configs. */
    fun path(model: CatalogModel, path: String): String = File(dir(model), path).absolutePath

    /** A file already verified and in place. */
    fun isComplete(model: CatalogModel, file: ModelFile): Boolean =
        fileFor(model, file).let { it.isFile && it.length() == file.size }

    fun isInstalled(model: CatalogModel): Boolean =
        model.files.all { isComplete(model, it) } &&
            marker(model).let { it.isFile && it.readText() == fingerprint(model) }

    fun markInstalled(model: CatalogModel) {
        marker(model).writeText(fingerprint(model))
    }

    /** Bytes of [model] on disk, verified or partial. */
    fun bytesOnDisk(model: CatalogModel): Long =
        dir(model).walkTopDown().filter { it.isFile }.sumOf { it.length() }

    fun delete(model: CatalogModel): Boolean = dir(model).let { !it.exists() || it.deleteRecursively() }

    /** Free space where the models go. */
    fun usableBytes(): Long = root.apply { mkdirs() }.usableSpace

    private fun marker(model: CatalogModel) = File(dir(model), ".installed")

    private fun fingerprint(model: CatalogModel): String =
        model.files.joinToString("\n") { "${it.path} ${it.size} ${it.sha256}" }
}
