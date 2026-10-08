package com.domovoi.app

import android.app.Application
import android.content.pm.ApplicationInfo
import android.os.StrictMode
import coil.ImageLoader
import coil.ImageLoaderFactory
import com.domovoi.app.alerts.TimerNotifier
import com.domovoi.app.diagnostics.Diagnostics
import com.domovoi.app.net.NetworkWatch

class DomovoiApplication : Application(), ImageLoaderFactory {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        // First, so a crash anywhere below is recorded too.
        Diagnostics.install(this)
        if ((applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0) watchMainThreadIo()
        TimerNotifier.createChannel(this)
        container = AppContainer(this)
        // A new network — a different default, the Wi-Fi under a VPN, new
        // addresses on the same one — means the saved server proves itself
        // again before the household token goes to it (net/IdentityGate.kt,
        // A6-03). The watch's fingerprint is what every verdict is keyed to.
        NetworkWatch.start(this, container.network)
        container.bus.start()
        container.alerts.start()
        // Earlier crashes and Android's exit history, read off the main thread.
        Diagnostics.onLaunch(this)
    }

    /**
     * Debug builds only: log (never crash on) network and disk work on the
     * main thread, so a blocking call that sneaks back onto it shows up in
     * logcat as a StrictMode line while it is being developed. Release
     * builds never install this.
     */
    private fun watchMainThreadIo() {
        StrictMode.setThreadPolicy(
            StrictMode.ThreadPolicy.Builder()
                .detectNetwork()
                .detectDiskReads()
                .detectDiskWrites()
                .penaltyLog()
                .build(),
        )
    }

    /**
     * Cover art and thumbnails come from the same Domovoi as everything
     * else, so Coil fetches through the app's own OkHttpClient rather than a
     * private one: its requests carry the household device token
     * (DeviceAuthInterceptor) and follow the same cleartext policy as every
     * other request (net/CleartextPolicy.kt). Without this Coil would build a
     * client of its own and its requests would be the only unauthenticated —
     * and unpoliced — ones the app makes.
     */
    override fun newImageLoader(): ImageLoader =
        ImageLoader.Builder(this).okHttpClient { container.api.http }.build()
}
