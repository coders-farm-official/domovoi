package com.domovoi.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import com.domovoi.app.alerts.EXTRA_ROUTE
import com.domovoi.app.diagnostics.Diagnostics
import com.domovoi.app.ui.shell.AppShell
import com.domovoi.app.ui.theme.DomovoiTheme

class MainActivity : ComponentActivity() {
    private val notifPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            // Granted or not, the timer alerts re-check (and re-arm or disarm
            // the alarm mirror) — the answer arrives while the app is resumed.
            (application as DomovoiApplication).container.alerts.onAppResumed()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
        // A timer alert's tap opens Home. Only on a fresh start: a recreated
        // activity (rotation) already went where the tap asked.
        if (savedInstanceState == null) routeFrom(intent)
        setContent {
            val themeMode by container.prefs.themeMode.collectAsState()
            CompositionLocalProvider(LocalApp provides container) {
                DomovoiTheme(mode = themeMode) {
                    AppShell()
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        routeFrom(intent)
    }

    // The freeze watchdog runs only while the app is on screen.
    override fun onStart() {
        super.onStart()
        Diagnostics.watchdog.start()
    }

    override fun onStop() {
        Diagnostics.watchdog.stop()
        super.onStop()
    }

    override fun onResume() {
        super.onResume()
        (application as DomovoiApplication).container.alerts.onAppResumed()
    }

    private fun routeFrom(intent: Intent?) {
        val route = intent?.getStringExtra(EXTRA_ROUTE) ?: return
        if (route == "home") (application as DomovoiApplication).container.pendingRoute.value = route
        // Consumed: a later recreation must not navigate again.
        intent.removeExtra(EXTRA_ROUTE)
    }
}
