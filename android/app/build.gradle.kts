plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.domovoi.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.domovoi.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        // The on-device assistant's native libraries: LiteRT-LM ships only
        // arm64-v8a and x86_64, so a 32-bit build could not load it.
        // arm64-v8a is every phone it targets; x86_64 is the emulator.
        // `-Pdomovoi.abis=arm64-v8a` builds a phone-only APK about half
        // the size (the libraries are stored uncompressed).
        ndk {
            abiFilters += (findProperty("domovoi.abis") as String?)?.split(',')?.map { it.trim() }
                ?: listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
    // Speech recognition and voice detection: sherpa-onnx's JNI library,
    // built from source WITHOUT its text to speech (espeak-ng, GPL-3.0) by
    // android/native/sherpa-onnx/build.sh, with the Kotlin API it binds to
    // from the same commit. Its one native dependency, libonnxruntime.so,
    // comes from the onnxruntime-android dependency below.
    sourceSets["main"].jniLibs.srcDir("../native/sherpa-onnx/jniLibs")
    sourceSets["main"].java.srcDir("../native/sherpa-onnx/kotlin")
    packaging {
        jniLibs {
            // ONNX Runtime's own Java API is not used (sherpa-onnx calls the
            // runtime from native code), so its JNI bridge stays out.
            excludes += listOf("**/libonnxruntime4j_jni.so")
        }
    }
    testOptions {
        // JVM unit tests: stub android.* so pure-Kotlin helpers that touch
        // framework classes indirectly (icons, Log) don't throw.
        unitTests.isReturnDefaultValues = true
    }
}

// Kotlin 2.4 makes the old `android { kotlinOptions { jvmTarget } }` block an
// error; this is the same setting in the compilerOptions DSL.
kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.androidx.compose.adaptive)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
    implementation(libs.coil.compose)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.session)
    implementation(libs.media3.datasource.okhttp)
    implementation(libs.media3.ui)
    implementation(libs.coil.video)
    // On-device assistant: LiteRT-LM runs the language model; sherpa-onnx
    // (android/native/sherpa-onnx, above) listens, on ONNX Runtime; the
    // phone's own TextToSpeech speaks.
    implementation(libs.litertlm.android)
    implementation(libs.onnxruntime.android)
    // The model download runs as a WorkManager job, so it survives leaving
    // the app, an app restart and a dropped Wi-Fi connection.
    implementation(libs.androidx.work.runtime.ktx)
    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
}
