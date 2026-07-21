package com.domovoi.app

import android.Manifest
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
import com.domovoi.app.ui.shell.AppShell
import com.domovoi.app.ui.theme.DomovoiTheme

class MainActivity : ComponentActivity() {
    private val notifPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Android 13+ hides media notifications unless the user grants
        // POST_NOTIFICATIONS — without this the playback controls never
        // appear in the tray.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        val container = (application as DomovoiApplication).container
        setContent {
            val themeMode by container.prefs.themeMode.collectAsState()
            CompositionLocalProvider(LocalApp provides container) {
                DomovoiTheme(mode = themeMode) {
                    AppShell()
                }
            }
        }
    }
}
