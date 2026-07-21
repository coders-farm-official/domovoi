package com.domovoi.app

import android.app.Application

class DomovoiApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.bus.start()
    }
}
