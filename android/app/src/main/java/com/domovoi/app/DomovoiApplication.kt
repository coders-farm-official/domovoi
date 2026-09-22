package com.domovoi.app

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory

class DomovoiApplication : Application(), ImageLoaderFactory {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.bus.start()
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
