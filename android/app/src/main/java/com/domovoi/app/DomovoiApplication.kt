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

    /** Coil fetches through the app's own OkHttpClient rather than a private
     *  one, so artwork and thumbnails follow the same cleartext policy as
     *  every other request (net/CleartextPolicy.kt). */
    override fun newImageLoader(): ImageLoader =
        ImageLoader.Builder(this).okHttpClient { container.api.http }.build()
}
