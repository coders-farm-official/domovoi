package com.domovoi.app.ui.shell

/** The numbers beside nav items. Since 2026-11-20 no page shows a total
 *  (people, calendar events, favourite stations): a number beside a nav
 *  item reads as "something new here", and a total never is (the web's
 *  CORE_NAV_ITEMS, components.jsx). What stays is something that really
 *  is new: Friend houses' unread messages. Settings carries no badge (the
 *  tab's own first line counts the rooms), as on the web. */
data class SidebarCounts(
    /** Unread messages in the house inbox (Friend houses' badge); null
     *  while it is hidden or there are none. */
    val friendHouses: Int? = null,
) {
    fun forRoute(r: Route): Int? = when (r) {
        Route.FriendHouses -> friendHouses
        else -> null
    }
}
