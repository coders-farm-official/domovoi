package com.domovoi.app.roommode

import android.app.Activity
import android.app.KeyguardManager
import android.app.admin.DevicePolicyManager
import android.provider.Settings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import androidx.core.content.ContextCompat
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.graphics.Color as AndroidColor
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.MainActivity
import com.domovoi.app.update.AppUpdate
import com.domovoi.app.update.AppUpdates
import com.domovoi.app.update.InstallFacts
import com.domovoi.app.update.noticeFor
import com.domovoi.app.update.openInBrowser
import com.domovoi.app.ui.theme.DomovoiTheme
import com.domovoi.app.ui.theme.ThemeMode
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * The room display: a separate full-screen activity in its own task, dark
 * and plain so the phone reads as a room, not as the app — the room's name,
 * a big clock, a state ring, this room's music and timers when the server
 * tells it about them, an always-visible mic-live chip, a big Mute, and
 * Exit (long press, then the phone's own unlock). What it draws is
 * [RoomDisplay]. While the phone is plugged in AND locked the screen stays
 * on; unlocked, or on battery, it sleeps as the phone's settings say, so an
 * unlocked room phone locks itself (RoomLockRules.keepScreenOn).
 *
 * Shown over the lock screen: anyone in the room may see it, mute it and
 * use its music and timer controls; leaving room mode still takes the
 * phone's PIN, pattern or fingerprint.
 * Opening it is also what (re)starts the room's service — Android lets a
 * microphone service start only from a screen the person is looking at.
 */
class RoomModeActivity : ComponentActivity() {
    private val unlock = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        if (r.resultCode == Activity.RESULT_OK) dialog = Dialog.KEEP_OR_FORGET
    }

    /** What the screen is asking, if anything. */
    private enum class Dialog { CONFIRM_NO_LOCK, SET_A_LOCK, KEEP_OR_FORGET }

    private var dialog by mutableStateOf<Dialog?>(null)

    /** A household phone without a screen lock: room mode is held (not
     *  listening) until it has one (RoomLockRules). */
    private var needsLock by mutableStateOf(false)

    /** The phone has a lock and is unlocked: the screen says how to lock it
     *  so the room stays on over the lock screen (RoomLockRules.showLockHint). */
    private var lockHint by mutableStateOf(false)

    private companion object {
        const val RECHECK_MS = 5_000L
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = RoomStore(this)
        if (!store.active || store.enrolment == null) {
            startActivity(Intent(this, RoomSetupActivity::class.java))
            finish()
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED)
        }
        checkLockAndStart()
        // Edge to edge on every Android version (enforced from Android 15
        // for API 35+ targets): black bars over the black room screen, which
        // pads itself (safeDrawingPadding).
        enableEdgeToEdge(SystemBarStyle.dark(AndroidColor.TRANSPARENT), SystemBarStyle.dark(AndroidColor.TRANSPARENT))
        setContent {
            DomovoiTheme(mode = ThemeMode.Dark) {
                val engine by RoomMode.engine.collectAsState()
                val status = engine?.status?.collectAsState()?.value
                var running by remember { mutableStateOf(store.running) }
                LaunchedEffect(engine) { running = RoomStore(this@RoomModeActivity).running }
                BackHandler { /* a room stays a room: Exit is the way out */ }
                // A newer app on the core (update/AppUpdate.kt): one faint line.
                val dismissals = remember { AppUpdates.dismissals(this@RoomModeActivity) }
                val dismissed by dismissals.dismissed.collectAsState()
                val offer = status?.appOffer
                val updateState = remember(offer) {
                    offer?.let { AppUpdate.decide(InstallFacts.read(this@RoomModeActivity), it) }
                }
                val update = updateState?.takeIf { AppUpdate.shows(it, dismissed) }?.let { st ->
                    noticeFor(st, onOpen = { openUpdate(it) }) { dismissals.dismiss(st) }
                }
                RoomDisplay(
                    label = store.enrolment?.roomLabel ?: "room",
                    status = status,
                    running = running && engine != null,
                    approvalCode = (status?.link as? RoomEngine.Link.AwaitingApproval)?.code,
                    wakeWord = store.wakeWord,
                    needsLock = needsLock,
                    lockHint = lockHint,
                    onMute = { engine?.setMuted(!(status?.muted ?: false)) },
                    onResume = {
                        RoomStore(this@RoomModeActivity).setRunning(true)
                        checkLockAndStart()
                        running = true
                    },
                    onSetLock = { openLockSettings() },
                    onCheckNow = { engine?.retryNow() },
                    onExit = { askToLeave() },
                    update = update,
                    source = engine,
                )
                when (dialog) {
                    Dialog.CONFIRM_NO_LOCK -> AlertDialog(
                        onDismissRequest = { dialog = null },
                        title = { Text("Exit room mode?") },
                        text = { Text(RoomStatusText.CONFIRM_NO_LOCK) },
                        confirmButton = { TextButton(onClick = { dialog = Dialog.KEEP_OR_FORGET }) { Text("Exit") } },
                        dismissButton = { TextButton(onClick = { dialog = null }) { Text("Stay a room") } },
                    )
                    Dialog.SET_A_LOCK -> AlertDialog(
                        onDismissRequest = { dialog = null },
                        title = { Text("Set a screen lock first") },
                        text = { Text(RoomStatusText.SET_A_LOCK_TO_EXIT) },
                        confirmButton = { TextButton(onClick = { dialog = null; openLockSettings() }) { Text("Screen lock settings") } },
                        dismissButton = { TextButton(onClick = { dialog = null }) { Text("Not now") } },
                    )
                    Dialog.KEEP_OR_FORGET -> AlertDialog(
                        onDismissRequest = { dialog = null },
                        title = { Text("Leave room mode") },
                        text = { Text(RoomStatusText.KEEP_OR_FORGET) },
                        confirmButton = { TextButton(onClick = { dialog = null; leave(forget = false) }) { Text("Keep this room") } },
                        dismissButton = { TextButton(onClick = { dialog = null; leave(forget = true) }) { Text("Forget this room") } },
                    )
                    null -> Unit
                }
            }
        }
    }

    /** On the charger and locked, the screen stays on (a room display is
     *  meant to be seen, and Home or Recents meets the lock); unlocked, or
     *  on battery, the phone's own timeout applies (RoomLockRules.keepScreenOn). */
    private var plugged = false

    private val screenEvents = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_BATTERY_CHANGED) plugged = isPlugged(intent)
            applyScreenRule()
        }
    }

    private fun isPlugged(battery: Intent?): Boolean =
        (battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0

    /** The one place the room screen holds the screen on: re-decided at
     *  every start and return, screen on and off, unlock (USER_PRESENT),
     *  focus change (the keyguard coming or going), plug and unplug, and
     *  every few seconds while shown (a lock with no broadcast of its own). */
    private fun applyScreenRule() {
        val km = getSystemService(KeyguardManager::class.java)
        val locked = km?.isKeyguardLocked == true
        val secure = km?.isDeviceSecure == true
        if (RoomLockRules.keepScreenOn(keyguardLocked = locked, plugged = plugged, deviceSecure = secure)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
        lockHint = RoomLockRules.showLockHint(keyguardLocked = locked, deviceSecure = secure)
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_BATTERY_CHANGED)
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_USER_PRESENT)
        }
        // Battery is sticky: the answer now, then every change.
        plugged = isPlugged(ContextCompat.registerReceiver(this, screenEvents, filter, ContextCompat.RECEIVER_NOT_EXPORTED))
        applyScreenRule()
        recheck?.cancel()
        recheck = lifecycleScope.launch {
            while (isActive) {
                delay(RECHECK_MS)
                applyScreenRule()
            }
        }
    }

    override fun onStop() {
        recheck?.cancel()
        recheck = null
        runCatching { unregisterReceiver(screenEvents) }
        super.onStop()
    }

    private var recheck: Job? = null

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        applyScreenRule()
    }

    override fun onResume() {
        super.onResume()
        val store = RoomStore(this)
        if (!store.active) {
            finish()
            return
        }
        applyScreenRule()
        // Re-checked at every return: the lock may have been set, or removed.
        checkLockAndStart()
    }

    /** Start (or keep) the room's service, unless a household phone has no
     *  screen lock: then stop listening and ask for one. */
    private fun checkLockAndStart() {
        val store = RoomStore(this)
        needsLock = !RoomLockRules.mayRun(RoomDevice.holdsHouseholdToken(this), RoomDevice.deviceSecure(this))
        if (needsLock) {
            if (RoomMode.engine.value != null) stopService(Intent(this, RoomService::class.java))
            return
        }
        if (store.running && RoomMode.engine.value == null) RoomMode.startService(this)
    }

    private fun openLockSettings() {
        runCatching { startActivity(Intent(DevicePolicyManager.ACTION_SET_NEW_PASSWORD)) }
            .onFailure { runCatching { startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) } }
    }

    /** The update's page in the browser, after the phone's unlock when the
     *  room screen is showing over the lock screen. */
    private fun openUpdate(link: String) {
        val km = getSystemService(KeyguardManager::class.java)
        val open = { openInBrowser(this, link); Unit }
        if (km?.isKeyguardLocked == true) {
            km.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() = open()
            })
        } else {
            open()
        }
    }

    private fun askToLeave() {
        when (RoomLockRules.exit(RoomDevice.holdsHouseholdToken(this), RoomDevice.deviceSecure(this))) {
            RoomLockRules.Exit.DEVICE_CREDENTIAL -> {
                val km = getSystemService(KeyguardManager::class.java)
                @Suppress("DEPRECATION")
                val intent = km?.createConfirmDeviceCredentialIntent("Exit room mode", "Unlock to turn this room back into your phone.")
                if (intent != null) unlock.launch(intent) else dialog = Dialog.SET_A_LOCK
            }
            RoomLockRules.Exit.CONFIRM_NO_LOCK -> dialog = Dialog.CONFIRM_NO_LOCK
            RoomLockRules.Exit.SET_A_LOCK_FIRST -> dialog = Dialog.SET_A_LOCK
        }
    }

    private fun leave(forget: Boolean) {
        if (forget) RoomMode.forget(this) else RoomMode.exit(this)
        // The household side comes back (it never started if the app was
        // started in room mode).
        (application as? DomovoiApplication)?.container?.let { c ->
            c.api.clearPairingRequired()
            c.bus.start()
            c.alerts.start()
            c.alerts.resumeAfterRoomMode()
            c.containerAlerts.start()
            c.helpAlerts.start()
            c.batteryAlerts.start()
        }
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finishAndRemoveTask()
    }
}
