package com.domovoi.app.ui.screens.lists

import android.content.Context
import androidx.compose.runtime.Composable
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.ApiState
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi

/** `GET /api/lists`, or the paired-only state (no household token, or the
 *  server refused it). */
internal sealed interface ListsRead {
    data class Loaded(val lists: List<HouseholdList>) : ListsRead
    data object Refused : ListsRead
}

/** `GET /api/lists/{id}`, or that the list is gone (deleted on another
 *  phone, or by voice, while it was open here). */
internal sealed interface DetailRead {
    data class Loaded(val detail: HouseholdListDetail) : DetailRead
    data class Gone(val listId: Long) : DetailRead
}

internal class ListsData(
    val lists: ApiState<ListsRead>,
    val detail: ApiState<DetailRead?>,
    /** The list [detail] reads: [rememberListsData]'s `select` of the lists. */
    val selectedId: Long?,
) {
    /** Both reads again: after every write, on `lists.changed`, and when
     *  the screen comes back to the foreground. */
    fun refreshAll() {
        lists.refresh()
        detail.refresh()
    }
}

/**
 * The Lists screen's two reads, each re-read on `lists.changed` (the
 * household's lists change by voice in any room, and on every other phone):
 * the lists, and the open one (what [select] picks of them) with its items. [paired]
 * false reads nothing: an unpaired phone is refused both, and is shown the
 * paired-only state instead.
 */
@Composable
internal fun rememberListsData(paired: Boolean, select: (List<HouseholdList>) -> Long?): ListsData {
    val lists = rememberApi(paired, eventTypes = LISTS_EVENTS) { a ->
        if (!paired) {
            ListsRead.Refused
        } else {
            try {
                ListsRead.Loaded(a.api.get("/api/lists").decode<List<HouseholdList>>())
            } catch (e: ApiException) {
                if (listsRefused(e)) ListsRead.Refused else throw e
            }
        }
    }
    val selectedId = (lists.data as? ListsRead.Loaded)?.lists?.let(select)
    val detail = rememberApi(paired, selectedId, eventTypes = LISTS_EVENTS) { a ->
        val id = selectedId
        if (!paired || id == null) {
            null
        } else {
            try {
                DetailRead.Loaded(a.api.get("/api/lists/$id").decode<HouseholdListDetail>())
            } catch (e: ApiException) {
                if (e.status == 404) DetailRead.Gone(id) else throw e
            }
        }
    }
    return ListsData(lists, detail, selectedId)
}

/** Which list this phone last had open, per server (list ids are a
 *  server's own). A convenience: lost, it falls back to the first list. */
internal object ListsSelection {
    private const val FILE = "lists"

    private fun key(server: String) = "selected:" + ServerCredentials.normalize(server)

    fun read(context: Context, server: String): Long? = runCatching {
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getLong(key(server), -1L).takeIf { it > 0 }
    }.getOrNull()

    fun save(context: Context, server: String, id: Long?) {
        runCatching {
            val p = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            if (id == null) p.remove(key(server)) else p.putLong(key(server), id)
            p.apply()
        }
    }
}
