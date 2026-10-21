package com.domovoi.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import com.domovoi.app.alerts.CHAT_ROUTE
import com.domovoi.app.alerts.CONTAINERS_ROUTE
import com.domovoi.app.alerts.EXTRA_CHAT_THREAD
import com.domovoi.app.alerts.EXTRA_CHAT_TITLE
import com.domovoi.app.alerts.EXTRA_ROUTE
import com.domovoi.app.alerts.EXTRA_SERVER_KEY
import com.domovoi.app.alerts.serverKey
import com.domovoi.app.data.LocalDeveloperOptionsOn
import com.domovoi.app.diagnostics.Diagnostics
import com.domovoi.app.roommode.RoomModeActivity
import com.domovoi.app.roommode.RoomStore
import com.domovoi.app.ui.shell.AppShell
import com.domovoi.app.ui.theme.DomovoiTheme

class MainActivity : ComponentActivity() {
    private val notifPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            // Granted or not, the timer alerts re-check (and re-arm or disarm
            // the alarm mirror) — the answer arrives while the app is resumed.
            (application as DomovoiApplication).container.alerts.onAppResumed()
            (application as DomovoiApplication).container.containerAlerts.onAppResumed()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Room mode locks the normal app (roommode/): the room screen
        // instead, until the person exits there with the phone's unlock.
        if (toRoomIfLocked()) return
        enableEdgeToEdge()
        // Android 13+ hides media notifications unless the user grants
        // POST_NOTIFICATIONS — without this the playback controls never
        // appear in the tray, and no timer or reminder alert can post.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        val container = (application as DomovoiApplication).container
        // A timer alert's tap opens Home, a container incident's the
        // Containers screen. Only on a fresh start: a recreated activity
        // (rotation) already went where the tap asked.
        if (savedInstanceState == null) routeFrom(intent)
        setContent {
            val themeMode by container.prefs.themeMode.collectAsState()
            val developerOptions by container.developerOptions.on.collectAsState()
            CompositionLocalProvider(LocalApp provides container, LocalDeveloperOptionsOn provides developerOptions) {
                DomovoiTheme(mode = themeMode) {
                    AppShell()
                }
            }
        }
        // Android 8 (API 26-27) gives the first focusable view of a window
        // the focus whenever nothing holds it (at start, back from a
        // permission dialog, after a field lets go), and Compose hands that
        // on to its first text field: the keyboard opened over Music's
        // filter on every start. The content frame takes that focus instead;
        // tapping a field still focuses it. Android 9 stopped doing this for
        // touch screens.
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            findViewById<ViewGroup>(android.R.id.content).apply {
                isFocusableInTouchMode = true
                descendantFocusability = ViewGroup.FOCUS_BEFORE_DESCENDANTS
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        routeFrom(intent)
    }

    // The freeze watchdog runs only while the app is on screen — and the
    // saved server proves its identity again after any spell off it: the
    // phone may have moved to a network the watch could not see change
    // (net/IdentityGate.kt). A rotation recreates the activity and is not
    // a spell in the background.
    override fun onStart() {
        super.onStart()
        Diagnostics.watchdog.start()
        (application as DomovoiApplication).container.identity.appForegrounded()
    }

    override fun onStop() {
        Diagnostics.watchdog.stop()
        if (!isChangingConfigurations) (application as DomovoiApplication).container.identity.appBackgrounded()
        super.onStop()
    }

    override fun onResume() {
        super.onResume()
        if (toRoomIfLocked()) return
        (application as DomovoiApplication).container.alerts.onAppResumed()
        // The container guard re-reads on every return: an incident may have
        // opened or closed off screen, and its banner should say so now.
        (application as DomovoiApplication).container.containerAlerts.onAppResumed()
        (application as DomovoiApplication).container.helpAlerts.onAppResumed()
    }

    /** True (and on its way to the room screen) while room mode is on.
     *  The household task leaves Recents too: no screenshot of it stays
     *  there while the phone is a room. */
    private fun toRoomIfLocked(): Boolean {
        if (!RoomStore.isActive(this)) return false
        startActivity(Intent(this, RoomModeActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        finishAndRemoveTask()
        return true
    }

    private fun routeFrom(intent: Intent?) {
        val route = intent?.getStringExtra(EXTRA_ROUTE) ?: return
        val container = (application as DomovoiApplication).container
        if (route == CHAT_ROUTE) {
            // Help steps: their home-server thread, when the phone is still
            // on the server that sent them; Chat's list otherwise.
            val thread = intent.getLongExtra(EXTRA_CHAT_THREAD, -1L)
            val sameServer = intent.getStringExtra(EXTRA_SERVER_KEY) ==
                container.prefs.serverUrl.value.takeIf { it.isNotBlank() }?.let { serverKey(it) }
            if (thread > 0 && sameServer) {
                container.pendingChatThread.value = thread to (intent.getStringExtra(EXTRA_CHAT_TITLE) ?: "Help")
            }
            intent.removeExtra(EXTRA_CHAT_THREAD)
        }
        if (route in ROUTES_FROM_OUTSIDE) container.pendingRoute.value = route
        // Consumed: a later recreation must not navigate again.
        intent.removeExtra(EXTRA_ROUTE)
    }

    private companion object {
        /** What a notification may ask for: Home (a timer alert), the
         *  Containers screen (a container incident) and Chat (help steps).
         *  Anything else is ignored. */
        val ROUTES_FROM_OUTSIDE = setOf("home", CONTAINERS_ROUTE, CHAT_ROUTE)
    }
}
