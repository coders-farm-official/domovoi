import DomovoiKit
import SwiftUI

/// Lists: the household's shared lists — To-do, Shopping and any it names —
/// the same ones voice keeps ("add milk to the shopping list").
///
/// `android/.../ui/screens/lists/ListsScreen.kt` (Android 3a3b6422) as a phone
/// screen, which mirrors web/static/lists.jsx at phone width: a row of chips,
/// the open list under it. Household data like Calendar's: re-read on
/// `lists.changed` and when the app comes back to the foreground
/// (``ListsModel``); an unpaired phone gets the paired-only state and no
/// controls at all — no add box drawn as if it could add.
///
/// ### What is here
///
/// Chips with open counts and `+ new list`; the open list's card with rename,
/// delete (always confirmed), the add box (empties at once, gets its words
/// back after a failure), cross off / put back, edit in place, the who-line
/// for voice adds, the crossed-off section with `clear crossed off`, and
/// reorder by a long press and drag on the handle, with `move up` / `move
/// down` as the handle's accessibility actions. A re-read that lands
/// mid-drag is held until the drop (``ListBoard``).
///
/// ### What is not
///
/// Nothing Android lacks: no per-list settings, colours or sharing, no
/// swipe-to-delete (Android's remove is the row's bin), no offline queue.
struct ListsScreenView: View {

    let model: ShellModel

    @State private var lists: ListsModel?

    /// `onAppear`/`onDisappear`, for ``CalendarScreenView``'s reason: a
    /// pushed destination fires `onDisappear` while this view stays in the
    /// hierarchy.
    @State private var visible = false

    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                header
                if let lists {
                    content(lists)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // A drag on a handle is not a scroll of the page.
        .scrollDisabled(lists?.board.dragging == true)
        .task {
            // Built here and not in `init`: the model needs the container,
            // and a `@State` initialiser runs before the view has one.
            if lists == nil {
                lists = ListsModel(container: model.container)
            }
            lists?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            lists?.stop()
        }
        // The toast is the shell's, drawn over the whole app: `deleted
        // Camping` outlives the alert that asked for it.
        .onChange(of: lists?.toast) { _, said in
            guard said != nil, let text = lists?.takeToast() else { return }
            model.showToast(text)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            lists?.serverChanged()
        }
        .onChange(of: model.isPaired) { _, _ in
            guard visible else { return }
            lists?.refreshAll()
        }
        // Back in the foreground: the socket may have dropped meanwhile.
        .onChange(of: scenePhase) { old, phase in
            guard visible, phase == .active, old == .background else { return }
            lists?.cameBack()
        }
        .alert(
            Text(ListsStrings.deleteListTitle(lists?.deleteTarget?.list.name ?? "")),
            isPresented: Binding(
                get: { lists?.deleteTarget != nil },
                set: { shown in if !shown { lists?.cancelDelete() } }
            ),
            presenting: lists?.deleteTarget
        ) { ask in
            Button(ListsStrings.cancelDialog, role: .cancel) { lists?.cancelDelete() }
            Button(ListsStrings.deleteListConfirm, role: .destructive) { lists?.confirmDelete(ask) }
        } message: { ask in
            Text(ListsStrings.deleteListBody(ask.itemCount))
        }
    }

    // MARK: - the header

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(ListsStrings.title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            if let sub = lists?.headerSub, !pairedOnly {
                Text(sub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("lists.sub")
            }
        }
    }

    private var pairedOnly: Bool {
        !model.isPaired || lists?.refused == true
    }

    // MARK: - the states, in the Kotlin's order (`ListsScreen.kt:175-219`)

    @ViewBuilder private func content(_ lists: ListsModel) -> some View {
        if pairedOnly {
            CalendarNoteView(title: ListsStrings.pairedOnlyTitle, detail: ListsStrings.pairedOnlyBody)
        } else if lists.loadedLists == nil, lists.lists.error == nil {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
                .accessibilityLabel(ListsStrings.title)
        } else if lists.loadedLists == nil, let trouble = lists.lists.error {
            VStack(alignment: .leading, spacing: 8) {
                CalendarNoteView(title: ListsStrings.requestFailed, detail: trouble)
                HomeLink(ListsStrings.retry) { lists.lists.refresh() }
            }
        } else {
            ListsSwitcherView(lists: lists)
            if lists.selected == nil {
                CalendarNoteView(title: ListsStrings.noListsTitle, detail: ListsStrings.noListsBody)
            } else if lists.openDetail != nil {
                ListsCardView(lists: lists)
            } else if let trouble = lists.detail.error, !lists.detail.loading {
                VStack(alignment: .leading, spacing: 8) {
                    CalendarNoteView(title: ListsStrings.requestFailed, detail: trouble)
                    HomeLink(ListsStrings.retry) { lists.detail.refresh() }
                }
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 24)
                    .accessibilityLabel(ListsStrings.title)
            }
        }
    }
}

// MARK: - the chips: one per list, then "+ new list"

/// `ListsSwitcher` (`ListsScreen.kt:251-337`).
struct ListsSwitcherView: View {

    let lists: ListsModel

    @State private var adding = false
    @State private var name = ""
    @State private var busy = false
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(lists.loadedLists ?? []) { list in
                        chip(list)
                    }
                    if !adding {
                        Button {
                            adding = true
                            focused = true
                        } label: {
                            HStack(spacing: 6) {
                                Image(systemName: "plus")
                                    .font(.footnote)
                                    .accessibilityHidden(true)
                                Text(ListsStrings.newList)
                                    .font(.subheadline)
                            }
                            .foregroundStyle(Color.fgMuted)
                            .modifier(ListsChipShape(on: false))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(ListsStrings.newList)
                    }
                }
                .padding(.vertical, 1)
            }
            .scrollIndicators(.hidden)

            if adding {
                ListsFieldRow {
                    TextField(ListsStrings.listNamePlaceholder, text: $name)
                        .textFieldStyle(.roundedBorder)
                        .submitLabel(.done)
                        .focused($focused)
                        .onSubmit(submit)
                        .onChange(of: name) { _, next in if next.count > 60 { name = String(next.prefix(60)) } }
                        .accessibilityLabel(ListsStrings.newListName)
                        .accessibilityIdentifier("lists.newName")
                } buttons: {
                    Button(ListsStrings.make, action: submit)
                        .buttonStyle(BrandFillButtonStyle())
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || busy)
                    Button(ListsStrings.cancel) {
                        adding = false
                        name = ""
                    }
                    .buttonStyle(InkOutlineButtonStyle(ink: .fgMuted))
                }
            }
        }
    }

    private func submit() {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !busy else { return }
        busy = true
        lists.makeList(trimmed) { ok in
            busy = false
            if ok {
                name = ""
                adding = false
            }
        }
    }

    private func chip(_ list: HouseholdList) -> some View {
        let on = list.id == lists.selectedID
        return Button {
            lists.select(list.id)
        } label: {
            HStack(spacing: 6) {
                Text(list.name)
                    .font(.subheadline)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: 180, alignment: .leading)
                    .fixedSize(horizontal: true, vertical: false)
                    .foregroundStyle(on ? Color.primary : Color.fgMuted)
                if list.openCount > 0 {
                    Text("\(list.openCount)")
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                }
            }
            .modifier(ListsChipShape(on: on))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(list.name)
        .accessibilityValue(list.openCount > 0 ? ListsStrings.cardSub(open: list.openCount, done: 0) : "")
        .accessibilityAddTraits(on ? [.isSelected] : [])
        .accessibilityIdentifier("lists.chip")
    }
}

/// A chip's capsule: the selected one outlined in the brand's ink over a
/// pale amber wash (amber only for the selected chip, the add button and the
/// filled checkbox — hand-off §3); the others a muted 3:1 edge, which is
/// ``CalendarRangeBarView``'s measured unselected capsule.
struct ListsChipShape: ViewModifier {

    let on: Bool

    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 14)
            .frame(minHeight: 44)
            .background(on ? Color.brand.opacity(0.18) : Color.clear, in: Capsule())
            .overlay(
                Capsule().strokeBorder(
                    on ? Color.brandInk : Color.fgMuted.opacity(0.45),
                    lineWidth: on ? 1.5 : 1
                )
            )
            .contentShape(.rect)
    }
}

/// A text field with its buttons beside it, or under it at an accessibility
/// size — the three inline fields (new list, rename, add) share it.
struct ListsFieldRow<Field: View, Buttons: View>: View {

    let field: Field
    let buttons: Buttons

    @Environment(\.dynamicTypeSize) private var typeSize

    init(@ViewBuilder field: () -> Field, @ViewBuilder buttons: () -> Buttons) {
        self.field = field()
        self.buttons = buttons()
    }

    var body: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                field
                HStack(spacing: 8) { buttons }
            }
        } else {
            HStack(spacing: 8) {
                field
                buttons
            }
        }
    }
}

// MARK: - the open list

/// The open list's card — `ListDetail` (`ListsScreen.kt:372-583`).
struct ListsCardView: View {

    let lists: ListsModel

    @FocusState private var addFocused: Bool

    /// Each open row's frame in the card, for the drag (``dragBy(_:)``).
    @State private var frames: [Int64: CGRect] = [:]
    @State private var framesVersion = 0
    @State private var swappedAt = -1
    @State private var dragOffset: CGFloat = 0
    @State private var dragBase: CGFloat = 0

    static let space = "lists.card"

    var body: some View {
        if let detail = lists.openDetail {
            let list = detail.list
            let open = lists.board.open
            let done = lists.board.done
            VStack(alignment: .leading, spacing: 4) {
                ListsHeadView(lists: lists, list: list, open: open.count, done: done.count)
                addRow(list)
                if lists.board.items.isEmpty {
                    CalendarNoteView(title: ListsStrings.emptyListTitle, detail: ListsStrings.emptyListHint(list.name))
                        .padding(.horizontal, 12)
                }
                ForEach(open) { item in
                    let dragged = lists.board.dragID == item.id
                    ListsItemRowView(lists: lists, item: item) {
                        ListsDragHandle(
                            lists: lists,
                            item: item,
                            onDrag: { dy in dragBy(item.id, dy) },
                            onEnd: endDrag
                        )
                    }
                    .background(
                        GeometryReader { proxy in
                            Color.clear.preference(
                                key: ListsRowFrames.self,
                                value: [item.id: proxy.frame(in: .named(Self.space))]
                            )
                        }
                    )
                    .offset(y: dragged ? dragOffset : 0)
                    .zIndex(dragged ? 1 : 0)
                    .background(dragged ? Color(.secondarySystemBackground) : Color.clear)
                }
                if open.isEmpty, !done.isEmpty {
                    Text(ListsStrings.allCrossedOff)
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                }
                if !done.isEmpty {
                    doneHead(done.count)
                    if lists.showDone {
                        ForEach(done) { item in
                            ListsItemRowView(lists: lists, item: item) {
                                Color.clear.frame(width: 44, height: 44).accessibilityHidden(true)
                            }
                        }
                    }
                }
            }
            .padding(.vertical, 8)
            .coordinateSpace(name: Self.space)
            .onPreferenceChange(ListsRowFrames.self) { next in
                frames = next
                framesVersion += 1
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
    }

    private func addRow(_ list: HouseholdList) -> some View {
        ListsFieldRow {
            TextField(
                ListsStrings.addPlaceholder(list.name),
                text: Binding(
                    get: { lists.field.text },
                    set: { lists.field.text = String($0.prefix(200)) }
                )
            )
            .textFieldStyle(.roundedBorder)
            .submitLabel(.done)
            .focused($addFocused)
            .onSubmit {
                lists.add()
                // The keyboard stays up: the next item can be typed at once.
                addFocused = true
            }
            .accessibilityLabel(ListsStrings.addTo(list.name))
            .accessibilityIdentifier("lists.add")
        } buttons: {
            Button(ListsStrings.add) { lists.add() }
                .buttonStyle(BrandFillButtonStyle())
                .disabled(lists.field.text.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
    }

    private func doneHead(_ count: Int) -> some View {
        HStack(spacing: 8) {
            Button {
                lists.showDone.toggle()
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: lists.showDone ? "chevron.down" : "chevron.right")
                        .font(.footnote)
                        .accessibilityHidden(true)
                    Text(ListsStrings.crossedOffHeading(count))
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .foregroundStyle(Color.fgMuted)
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityValue(lists.showDone ? ListsStrings.expanded : ListsStrings.collapsed)
            .accessibilityHint(lists.showDone ? ListsStrings.fold : ListsStrings.unfold)
            Button(ListsStrings.clearCrossedOff) { lists.clearDone() }
                .buttonStyle(.plain)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .padding(.horizontal, 12)
    }

    /// Move the dragged row by the finger: when its middle passes a
    /// neighbouring open row the two swap (``ListsModel/dragPast(_:)``), and
    /// the offset is taken back by the distance the row's slot moved, so it
    /// stays under the finger (`dragBy`, `ListsScreen.kt:585-604`).
    ///
    /// One swap per layout: after a swap the frames are the old layout's
    /// until the next preference pass, and a second test against them would
    /// swap the pair straight back.
    private func dragBy(_ id: Int64, _ translation: CGFloat) {
        dragOffset = translation + dragBase
        guard framesVersion != swappedAt, let me = frames[id] else { return }
        let middle = me.midY + dragOffset
        let openIDs = Set(lists.board.open.map(\.id))
        guard let target = frames.first(where: { key, frame in
            key != id && openIDs.contains(key) && middle >= frame.minY && middle < frame.maxY
        }) else { return }
        guard lists.dragPast(target.key) else { return }
        swappedAt = framesVersion
        if target.value.minY > me.minY {
            dragBase -= target.value.height
        } else {
            dragBase += target.value.height
        }
        dragOffset = translation + dragBase
    }

    private func endDrag() {
        lists.endDrag()
        dragOffset = 0
        dragBase = 0
        swappedAt = -1
    }
}

/// Each open row's frame, keyed by its item.
struct ListsRowFrames: PreferenceKey {
    static let defaultValue: [Int64: CGRect] = [:]
    static func reduce(value: inout [Int64: CGRect], nextValue: () -> [Int64: CGRect]) {
        value.merge(nextValue()) { _, new in new }
    }
}

/// The card's title row, or the rename field — `ListHead`
/// (`ListsScreen.kt:606-660`).
struct ListsHeadView: View {

    let lists: ListsModel
    let list: HouseholdList
    let open: Int
    let done: Int

    @State private var name = ""
    @FocusState private var focused: Bool

    var body: some View {
        if lists.renaming {
            ListsFieldRow {
                TextField(ListsStrings.listName, text: $name)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.done)
                    .focused($focused)
                    .onSubmit { lists.rename(list, to: name) }
                    .onChange(of: name) { _, next in if next.count > 60 { name = String(next.prefix(60)) } }
                    .accessibilityLabel(ListsStrings.listName)
                    .accessibilityIdentifier("lists.rename")
            } buttons: {
                Button(ListsStrings.save) { lists.rename(list, to: name) }
                    .buttonStyle(BrandFillButtonStyle())
                Button(ListsStrings.cancel) { lists.renaming = false }
                    .buttonStyle(InkOutlineButtonStyle(ink: .fgMuted))
            }
            .padding(12)
            .onAppear {
                name = list.name
                focused = true
            }
        } else {
            HStack(alignment: .center, spacing: 0) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(list.name)
                        .font(.headline)
                        .lineLimit(2)
                        .truncationMode(.tail)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier("lists.title")
                    Text(ListsStrings.cardSub(open: open, done: done))
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("lists.cardSub")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                ListsIconButton(systemImage: "pencil", label: ListsStrings.renameList) {
                    lists.renaming = true
                }
                ListsIconButton(systemImage: "trash", label: ListsStrings.deleteList) {
                    lists.askToDelete(list)
                }
            }
            .padding(.leading, 16)
            .padding(.trailing, 4)
            .padding(.top, 4)
        }
    }
}

/// A 44-point glyph button with its own label.
struct ListsIconButton: View {

    let systemImage: String
    let label: String
    var ink: Color = .fgMuted
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.body)
                .foregroundStyle(ink)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// The handle: a long press and drag reorders; VoiceOver gets `move up` /
/// `move down` (`DragHandle`, `ListsScreen.kt:662-700`).
struct ListsDragHandle: View {

    let lists: ListsModel
    let item: HouseholdListItem
    let onDrag: (CGFloat) -> Void
    let onEnd: () -> Void

    @GestureState private var holding = false

    var body: some View {
        Image(systemName: "line.3.horizontal")
            .font(.body)
            .foregroundStyle(Color.fgMuted)
            .frame(width: 44, height: 44)
            .contentShape(.rect)
            .gesture(
                LongPressGesture(minimumDuration: 0.3)
                    .sequenced(before: DragGesture(minimumDistance: 0, coordinateSpace: .named(ListsCardView.space)))
                    .updating($holding) { value, state, _ in
                        if case .second(true, _) = value { state = true }
                    }
                    .onChanged { value in
                        guard case .second(true, let drag) = value else { return }
                        if !lists.board.dragging { lists.startDrag(item.id) }
                        if let drag { onDrag(drag.translation.height) }
                    }
                    .onEnded { _ in onEnd() }
            )
            // A gesture the system cancelled (a call, the app backgrounded)
            // never reaches `onEnded`: the drop happens here instead.
            .onChange(of: holding) { _, now in
                if !now, lists.board.dragID == item.id { onEnd() }
            }
            .accessibilityElement()
            .accessibilityLabel(ListsStrings.move(item.text))
            .accessibilityAction(named: Text(ListsStrings.moveUp)) {
                if lists.canStep(item, by: -1) { lists.step(item, by: -1) }
            }
            .accessibilityAction(named: Text(ListsStrings.moveDown)) {
                if lists.canStep(item, by: 1) { lists.step(item, by: 1) }
            }
    }
}

/// One item: handle · checkbox · words (tap to edit) with the who-line under
/// them · bin — `ItemRow` (`ListsScreen.kt:702-784`).
struct ListsItemRowView<Handle: View>: View {

    let lists: ListsModel
    let item: HouseholdListItem
    let handle: Handle

    @State private var text = ""
    @FocusState private var focused: Bool

    init(lists: ListsModel, item: HouseholdListItem, @ViewBuilder handle: () -> Handle) {
        self.lists = lists
        self.item = item
        self.handle = handle()
    }

    var body: some View {
        HStack(alignment: .center, spacing: 0) {
            handle
            checkbox
            words
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 6)
            ListsIconButton(systemImage: "trash", label: ListsStrings.remove(item.text)) {
                lists.remove(item)
            }
        }
        .frame(minHeight: 44)
        .padding(.trailing, 4)
    }

    private var checkbox: some View {
        Button {
            lists.toggle(item)
        } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .fill(item.done ? Color.brand : Color.clear)
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .strokeBorder(item.done ? Color.brand : Color.fgMuted, lineWidth: 1.5)
                if item.done {
                    Image(systemName: "checkmark")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(Color.brandFg)
                }
            }
            .frame(width: 22, height: 22)
            .frame(width: 44, height: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(item.done ? ListsStrings.putBack(item.text) : ListsStrings.crossOff(item.text))
        .accessibilityValue(item.done ? ListsStrings.crossedOffState : ListsStrings.toGo)
    }

    @ViewBuilder private var words: some View {
        if lists.editingID == item.id {
            TextField(ListsStrings.itemText, text: $text)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.done)
                .focused($focused)
                .onSubmit { lists.edit(item, to: text) }
                .onChange(of: text) { _, next in if next.count > 200 { text = String(next.prefix(200)) } }
                // Leaving the field without done changes nothing — Android's
                // back.
                .onChange(of: focused) { _, now in
                    if !now, lists.editingID == item.id { lists.editingID = nil }
                }
                .onAppear {
                    text = item.text
                    focused = true
                }
                .accessibilityLabel(ListsStrings.itemText)
                .accessibilityIdentifier("lists.edit")
        } else {
            VStack(alignment: .leading, spacing: 2) {
                Button {
                    lists.editingID = item.id
                } label: {
                    Text(item.text)
                        .font(.body)
                        .strikethrough(item.done)
                        .foregroundStyle(item.done ? Color.fgMuted : Color.primary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: 32, alignment: .leading)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(item.text)
                .accessibilityHint(ListsStrings.changeTheWords)
                if let who = listsWhoLine(item) {
                    Text(who)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}
