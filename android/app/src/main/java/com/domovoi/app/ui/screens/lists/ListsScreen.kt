package com.domovoi.app.ui.screens.lists

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.DeleteOutline
import androidx.compose.material.icons.filled.DragIndicator
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Lists — the household's shared lists (to-do, shopping, any it names),
 * the same ones voice keeps ("add milk to the shopping list"). Mirrors
 * web/static/lists.jsx at phone width: a row of chips, the open list under
 * it. Household data like Calendar's: re-read on `lists.changed`
 * ([rememberListsData]) and when the screen comes back to the foreground;
 * an unpaired phone gets the paired-only state and no controls.
 */
@Composable
fun ListsScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val token by app.prefs.deviceToken.collectAsState()
    val paired = !token.isNullOrBlank()

    var savedId by remember(serverUrl) { mutableStateOf(ListsSelection.read(context, serverUrl)) }
    val data = rememberListsData(paired) { lists -> selectedList(lists, savedId)?.id }
    val listsRead = data.lists.data
    val loaded = (listsRead as? ListsRead.Loaded)?.lists
    val selected = loaded?.firstOrNull { it.id == data.selectedId }

    val select: (Long?) -> Unit = { id ->
        savedId = id
        ListsSelection.save(context, serverUrl, id)
    }

    // A list deleted elsewhere while open here: back to the first, re-read.
    val detailRead = data.detail.data
    LaunchedEffect(detailRead) {
        if (detailRead is DetailRead.Gone && detailRead.listId == selected?.id) {
            select(null)
            data.lists.refresh()
        }
    }

    // Back in the foreground: the socket may have dropped meanwhile. The
    // first resume is the screen opening, already being read.
    var resumedOnce by remember { mutableStateOf(false) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        if (resumedOnce) data.refreshAll() else resumedOnce = true
    }

    /**
     * One write. Keyed by the server it was for: an answer that lands after
     * the phone switched servers is dropped, not shown against the new one.
     * [onFail] runs before the re-read (an optimistic change undone).
     */
    fun write(
        action: String,
        onOk: (JsonElement) -> Unit = {},
        onFail: () -> Unit = {},
        call: suspend () -> JsonElement,
    ) {
        val server = app.prefs.serverUrl.value
        scope.launch {
            val result = try {
                Result.success(call())
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Result.failure(e)
            }
            if (app.prefs.serverUrl.value != server) return@launch
            result.onSuccess(onOk).onFailure { e ->
                onFail()
                listsFailure(action, e).message?.let(toast)
            }
            data.refreshAll()
        }
    }

    var deleteTarget by remember { mutableStateOf<Pair<HouseholdList, Int>?>(null) }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("Lists", loaded?.let { listsHeaderSub(it) })
        Spacer(Modifier.height(12.dp))
        when {
            !paired || listsRead is ListsRead.Refused -> EmptyState(
                "paired devices only",
                "pair this phone, or sign in, to read the household's lists",
            )
            data.lists.loading && loaded == null -> LoadingState()
            data.lists.error != null && loaded == null ->
                ErrorState(data.lists.error ?: "request failed", data.lists.refresh)
            else -> {
                val lists = loaded.orEmpty()
                ListsSwitcher(
                    lists = lists,
                    selectedId = selected?.id,
                    onSelect = { select(it) },
                    onNew = { name, done ->
                        write("make list", onOk = { res ->
                            runCatching { res.decode<HouseholdList>() }.getOrNull()?.let { select(it.id) }
                            done(true)
                        }, onFail = { done(false) }) {
                            app.api.post("/api/lists", buildJsonObject { put("name", name) })
                        }
                    },
                )
                Spacer(Modifier.height(12.dp))
                val detail = (detailRead as? DetailRead.Loaded)?.detail
                when {
                    selected == null -> EmptyState("no lists yet", "make one here, or say \"make a list called camping\"")
                    detail == null || detail.list.id != selected.id ->
                        if (data.detail.error != null && !data.detail.loading) {
                            ErrorState(data.detail.error ?: "request failed", data.detail.refresh)
                        } else {
                            LoadingState()
                        }
                    else -> ListDetail(
                        key = "$serverUrl#${detail.list.id}",
                        list = detail.list,
                        serverItems = detail.items,
                        write = ::write,
                        refresh = data::refreshAll,
                        toast = toast,
                        onAskDelete = { list, count -> deleteTarget = list to count },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }

    deleteTarget?.let { (list, count) ->
        ConfirmDialog(
            title = "Delete ${list.name}?",
            body = deleteListBody(count),
            confirmLabel = "Delete list",
            destructive = true,
            onConfirm = {
                write("delete", onOk = {
                    toast("deleted ${list.name}")
                    if (savedId == list.id) select(null)
                }) { app.api.delete("/api/lists/${list.id}") }
            },
            onDismiss = { deleteTarget = null },
        )
    }
}

private typealias Write = (
    action: String,
    onOk: (JsonElement) -> Unit,
    onFail: () -> Unit,
    call: suspend () -> JsonElement,
) -> Unit

// ---------------------------------------------------------------------------
// The chips: one per list, then "+ new list".
// ---------------------------------------------------------------------------

@Composable
private fun ListsSwitcher(
    lists: List<HouseholdList>,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
    onNew: (name: String, done: (Boolean) -> Unit) -> Unit,
) {
    var adding by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val submit = {
        val n = name.trim()
        if (n.isNotEmpty() && !busy) {
            busy = true
            onNew(n) { ok ->
                busy = false
                if (ok) {
                    name = ""
                    adding = false
                }
            }
        }
    }
    Column {
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            lists.forEach { l ->
                val on = l.id == selectedId
                Chip(
                    on = on,
                    onClick = { onSelect(l.id) },
                    modifier = Modifier.semantics {
                        role = Role.Tab
                        selected = on
                    },
                ) {
                    Text(
                        l.name,
                        style = MaterialTheme.typography.labelLarge,
                        color = if (on) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.widthIn(max = 180.dp),
                    )
                    if (l.open_count > 0) {
                        Text(
                            "${l.open_count}",
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgSubtle,
                        )
                    }
                }
            }
            if (!adding) {
                Chip(on = false, onClick = { adding = true }) {
                    Icon(Icons.Filled.Add, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp))
                    Text("new list", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fgMuted)
                }
            }
        }
        if (adding) {
            BackHandler { adding = false; name = "" }
            val focus = remember { FocusRequester() }
            LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
            Row(
                Modifier.fillMaxWidth().padding(top = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { if (it.length <= 60) name = it },
                    placeholder = { Text("list name", color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { submit() }),
                    modifier = Modifier.weight(1f).focusRequester(focus)
                        .semantics { contentDescription = "new list name" },
                )
                Button(onClick = { submit() }, enabled = name.isNotBlank() && !busy, colors = amberButton()) { Text("make") }
                TextButton(onClick = { adding = false; name = "" }) { Text("cancel", color = Domovoi.colors.fgMuted) }
            }
        }
    }
}

@Composable
private fun Chip(
    on: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    val shape = RoundedCornerShape(999.dp)
    Row(
        modifier
            .heightIn(min = 48.dp)
            .clip(shape)
            .border(1.dp, if (on) Domovoi.colors.brand else Domovoi.colors.border, shape)
            .background(if (on) Domovoi.colors.brandSoft else Color.Transparent, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) { content() }
}

@Composable
private fun amberButton() = ButtonDefaults.buttonColors(
    containerColor = Domovoi.colors.brand,
    contentColor = Domovoi.colors.brandFg,
)

// ---------------------------------------------------------------------------
// The open list.
// ---------------------------------------------------------------------------

private fun openKey(id: Long) = "o$id"

@Composable
private fun ListDetail(
    key: String,
    list: HouseholdList,
    serverItems: List<HouseholdListItem>,
    write: Write,
    refresh: () -> Unit,
    toast: (String) -> Unit,
    onAskDelete: (HouseholdList, Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val board = remember(key) { ListBoard(serverItems) }
    // A re-read lands on the board; mid-drag it is held until the drop.
    LaunchedEffect(board, serverItems) { board.serverSays(serverItems) }
    val field = remember(key) { ListAddField() }
    var renaming by remember(key) { mutableStateOf(false) }
    var editingId by remember(key) { mutableStateOf<Long?>(null) }
    var showDone by remember(key) { mutableStateOf(true) }
    val listState = rememberLazyListState()
    val base = "/api/lists/${list.id}"

    // On a failed write the board goes back to what the server last said
    // (an optimistic change undone), and the screen re-reads.
    val lastRead by rememberUpdatedState(serverItems)
    val undo = { board.serverSays(lastRead) }

    fun add() {
        val server = app.prefs.serverUrl.value
        scope.launch {
            val r = field.submit { words ->
                app.api.post("$base/items", buildJsonObject { put("text", words) }).decode<ListItemAdded>()
            } ?: return@launch
            if (app.prefs.serverUrl.value != server) return@launch
            r.onSuccess { added -> addNote(added)?.let(toast) }
                .onFailure { e -> listsFailure("add", e).message?.let(toast) }
            refresh()
        }
    }

    fun toggle(it: HouseholdListItem) {
        board.change { all -> all.map { x -> if (x.id == it.id) x.copy(done = !it.done) else x } }
        write("save", {}, undo) { app.api.patch("$base/items/${it.id}", buildJsonObject { put("done", !it.done) }) }
    }

    fun edit(it: HouseholdListItem, text: String) {
        board.change { all -> all.map { x -> if (x.id == it.id) x.copy(text = text) else x } }
        write("save", {}, undo) { app.api.patch("$base/items/${it.id}", buildJsonObject { put("text", text) }) }
    }

    fun remove(it: HouseholdListItem) {
        board.change { all -> all.filter { x -> x.id != it.id } }
        write("delete", {}, undo) { app.api.delete("$base/items/${it.id}") }
    }

    fun saveOrder(order: List<Long>) {
        board.applyOrder(order)
        write("move", {}, undo) {
            app.api.put("$base/items/order", buildJsonObject {
                put("item_ids", buildJsonArray { order.forEach { add(kotlinx.serialization.json.JsonPrimitive(it)) } })
            })
        }
    }

    fun clearDone() {
        write("clear", { res ->
            val n = runCatching { res.decode<ListsCleared>().removed }.getOrDefault(0)
            toast("removed ${listsThings(n)}")
        }, {}) { app.api.post("$base/clear-done", buildJsonObject { }) }
    }

    val endDrag = rememberUpdatedState {
        val moved = board.endDrag()
        if (moved != null) saveOrder(moved)
    }

    val open = board.open
    val done = board.done

    DomovoiCard(modifier.fillMaxWidth(), padding = 0) {
        LazyColumn(Modifier.fillMaxSize(), state = listState) {
            item(key = "head") {
                ListHead(
                    list = list,
                    open = open.size,
                    done = done.size,
                    renaming = renaming,
                    onRename = { renaming = it },
                    onSaveName = { n ->
                        if (n.isBlank() || n.trim() == list.name) {
                            renaming = false
                        } else {
                            write("rename", { renaming = false }, {}) {
                                app.api.patch("/api/lists/${list.id}", buildJsonObject { put("name", n.trim()) })
                            }
                        }
                    },
                    onDelete = { onAskDelete(list, board.items.size) },
                )
            }
            item(key = "add") {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedTextField(
                        value = field.text,
                        onValueChange = { if (it.length <= 200) field.text = it },
                        placeholder = { Text(addPlaceholder(list.name), color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { add() }),
                        modifier = Modifier.weight(1f).semantics { contentDescription = "add to ${list.name}" },
                    )
                    Button(
                        onClick = { add() },
                        enabled = field.text.isNotBlank(),
                        colors = amberButton(),
                        modifier = Modifier.heightIn(min = 48.dp),
                    ) { Text("add") }
                }
            }
            if (board.items.isEmpty()) {
                item(key = "empty") { EmptyState("nothing on this list", emptyListHint(list.name)) }
            }
            items(open, key = { openKey(it.id) }) { row ->
                val dragged = board.dragId == row.id
                ItemRow(
                    item = row,
                    editing = editingId == row.id,
                    onEditing = { on -> editingId = if (on) row.id else null },
                    onToggle = { toggle(row) },
                    onEdit = { t -> edit(row, t) },
                    onDelete = { remove(row) },
                    handle = {
                        DragHandle(
                            item = row,
                            canUp = openOrderStep(board.items, row.id, -1) != null,
                            canDown = openOrderStep(board.items, row.id, +1) != null,
                            onStep = { d -> openOrderStep(board.items, row.id, d)?.let { o -> saveOrder(o) } },
                            onStart = { board.startDrag(row.id) },
                            onDrag = { dy -> dragBy(board, listState, dy) },
                            onEnd = { endDrag.value() },
                        )
                    },
                    modifier = if (dragged) {
                        Modifier.zIndex(1f).graphicsLayer { translationY = board.dragOffset }
                            .background(Domovoi.colors.raised)
                    } else {
                        Modifier
                    },
                )
            }
            if (open.isEmpty() && done.isNotEmpty()) {
                item(key = "all-done") {
                    Text(
                        "everything here is crossed off",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
                    )
                }
            }
            if (done.isNotEmpty()) {
                item(key = "done-head") {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(
                            Modifier
                                .weight(1f)
                                .heightIn(min = 48.dp)
                                .clickable(onClickLabel = if (showDone) "fold" else "unfold") { showDone = !showDone }
                                .semantics { stateDescription = if (showDone) "expanded" else "collapsed" }
                                .padding(horizontal = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Icon(
                                if (showDone) Icons.Filled.ExpandMore else Icons.Filled.ChevronRight,
                                contentDescription = null,
                                tint = Domovoi.colors.fgMuted,
                                modifier = Modifier.size(18.dp),
                            )
                            Text("crossed off · ${done.size}", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fgMuted)
                        }
                        TextButton(onClick = { clearDone() }, modifier = Modifier.heightIn(min = 48.dp)) {
                            Text("clear crossed off", color = Domovoi.colors.fgMuted)
                        }
                    }
                }
                if (showDone) {
                    items(done, key = { "d${it.id}" }) { row ->
                        ItemRow(
                            item = row,
                            editing = editingId == row.id,
                            onEditing = { on -> editingId = if (on) row.id else null },
                            onToggle = { toggle(row) },
                            onEdit = { t -> edit(row, t) },
                            onDelete = { remove(row) },
                            handle = { Spacer(Modifier.width(48.dp)) },
                        )
                    }
                }
            }
            item(key = "foot") { Spacer(Modifier.height(12.dp)) }
        }
    }
}

/**
 * Move the dragged row by [dy]: when its middle passes a neighbouring open
 * row, the two swap ([ListBoard.dragPast]) and the offset is taken back by
 * the distance the row's slot moved, so it stays under the finger.
 */
private fun dragBy(board: ListBoard, state: LazyListState, dy: Float) {
    val id = board.dragId ?: return
    board.dragOffset += dy
    val info = state.layoutInfo.visibleItemsInfo
    val me = info.firstOrNull { it.key == openKey(id) } ?: return
    val middle = me.offset + board.dragOffset + me.size / 2f
    val target = info.firstOrNull { other ->
        other.key != me.key && (other.key as? String)?.startsWith("o") == true &&
            middle >= other.offset && middle < other.offset + other.size
    } ?: return
    val targetId = (target.key as String).removePrefix("o").toLongOrNull() ?: return
    if (!board.dragPast(targetId)) return
    val newSlot = if (target.index > me.index) me.offset + target.size else target.offset
    board.dragOffset -= (newSlot - me.offset)
}

@Composable
private fun ListHead(
    list: HouseholdList,
    open: Int,
    done: Int,
    renaming: Boolean,
    onRename: (Boolean) -> Unit,
    onSaveName: (String) -> Unit,
    onDelete: () -> Unit,
) {
    if (renaming) {
        var name by remember(list.id, list.name) { mutableStateOf(list.name) }
        BackHandler { onRename(false) }
        val focus = remember { FocusRequester() }
        LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            OutlinedTextField(
                value = name,
                onValueChange = { if (it.length <= 60) name = it },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { onSaveName(name) }),
                modifier = Modifier.weight(1f).focusRequester(focus).semantics { contentDescription = "list name" },
            )
            Button(onClick = { onSaveName(name) }, colors = amberButton()) { Text("save") }
            TextButton(onClick = { onRename(false) }) { Text("cancel", color = Domovoi.colors.fgMuted) }
        }
        return
    }
    Row(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 8.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                list.name,
                style = MaterialTheme.typography.titleMedium,
                color = Domovoi.colors.fg,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text(listCardSub(open, done), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
        IconButton(onClick = { onRename(true) }) {
            Icon(Icons.Filled.Edit, contentDescription = "rename list", tint = Domovoi.colors.fgMuted)
        }
        IconButton(onClick = onDelete) {
            Icon(Icons.Filled.DeleteOutline, contentDescription = "delete list", tint = Domovoi.colors.fgMuted)
        }
    }
}

@Composable
private fun DragHandle(
    item: HouseholdListItem,
    canUp: Boolean,
    canDown: Boolean,
    onStep: (Int) -> Unit,
    onStart: () -> Unit,
    onDrag: (Float) -> Unit,
    onEnd: () -> Unit,
) {
    val start by rememberUpdatedState(onStart)
    val drag by rememberUpdatedState(onDrag)
    val end by rememberUpdatedState(onEnd)
    Box(
        Modifier
            .size(48.dp)
            .pointerInput(item.id) {
                detectDragGesturesAfterLongPress(
                    onDragStart = { start() },
                    onDrag = { change, amount ->
                        change.consume()
                        drag(amount.y)
                    },
                    onDragEnd = { end() },
                    onDragCancel = { end() },
                )
            }
            .semantics {
                contentDescription = "move ${item.text}"
                customActions = buildList {
                    if (canUp) add(CustomAccessibilityAction("move up") { onStep(-1); true })
                    if (canDown) add(CustomAccessibilityAction("move down") { onStep(1); true })
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Icon(Icons.Filled.DragIndicator, contentDescription = null, tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(20.dp))
    }
}

@Composable
private fun ItemRow(
    item: HouseholdListItem,
    editing: Boolean,
    onEditing: (Boolean) -> Unit,
    onToggle: () -> Unit,
    onEdit: (String) -> Unit,
    onDelete: () -> Unit,
    handle: @Composable () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier.fillMaxWidth().heightIn(min = 48.dp).padding(end = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        handle()
        // The box: tap to cross off (or put back).
        Box(
            Modifier
                .size(48.dp)
                .clickable(
                    onClickLabel = if (item.done) "put back" else "cross off",
                    role = Role.Checkbox,
                    onClick = onToggle,
                )
                .semantics {
                    contentDescription = if (item.done) "put ${item.text} back on the list" else "cross off ${item.text}"
                    stateDescription = if (item.done) "crossed off" else "to go"
                },
            contentAlignment = Alignment.Center,
        ) {
            val shape = RoundedCornerShape(5.dp)
            Box(
                Modifier
                    .size(22.dp)
                    .clip(shape)
                    .background(if (item.done) Domovoi.colors.brand else Color.Transparent, shape)
                    .border(1.5.dp, if (item.done) Domovoi.colors.brand else Domovoi.colors.borderStrong, shape),
                contentAlignment = Alignment.Center,
            ) {
                if (item.done) Icon(Icons.Filled.Check, contentDescription = null, tint = Domovoi.colors.brandFg, modifier = Modifier.size(16.dp))
            }
        }
        Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
            if (editing) {
                var text by remember(item.id) { mutableStateOf(item.text) }
                val save = {
                    onEditing(false)
                    val t = text.trim()
                    if (t.isNotEmpty() && t != item.text) onEdit(t)
                }
                BackHandler { onEditing(false) }
                val focus = remember { FocusRequester() }
                LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
                OutlinedTextField(
                    value = text,
                    onValueChange = { if (it.length <= 200) text = it },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { save() }),
                    modifier = Modifier.fillMaxWidth().focusRequester(focus).semantics { contentDescription = "item text" },
                )
            } else {
                Text(
                    item.text,
                    style = MaterialTheme.typography.bodyLarge,
                    color = if (item.done) Domovoi.colors.fgMuted else Domovoi.colors.fg,
                    textDecoration = if (item.done) TextDecoration.LineThrough else null,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(onClickLabel = "change the words") { onEditing(true) }
                        .padding(vertical = 4.dp),
                )
                whoLine(item)?.let {
                    Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgSubtle)
                }
            }
        }
        IconButton(onClick = onDelete) {
            Icon(Icons.Filled.DeleteOutline, contentDescription = "remove ${item.text}", tint = Domovoi.colors.fgSubtle)
        }
    }
}
