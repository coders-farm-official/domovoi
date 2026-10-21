# R8 rules for the release build (app/build.gradle.kts: isMinifyEnabled,
# isShrinkResources). R8 shrinks, optimises and renames everything not kept.
#
# Most of what must survive is kept by the libraries' own rules, which R8
# reads from their jars and AARs: kotlinx.serialization (every @Serializable
# class's serializer and Companion), WorkManager (every worker's name and
# constructor: its database stores workers by class name, so
# ModelDownloadWorker and BookDownloadWorker keep their names across
# updates), Media3, OkHttp, Gson, coroutines, DataStore, CameraX and AndroidX;
# the manifest's components (activities, services, receivers, the
# Application) are kept by the rules aapt2 writes from the manifest; native
# methods' names by proguard-android-optimize.txt. What follows is only what
# nothing else keeps, each with why; a rule goes in only when a release
# build shows it is needed (a JNI lookup, a reflective call, a crash).

# Stack traces keep their line numbers (every file named "SourceFile"), so a
# crash Play reports, or the stack in a problem report (diagnostics/), can
# be turned back into names with that build's mapping.txt (R8's retrace;
# Play does it with the mapping the App Bundle carries).
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# LiteRT-LM, the on-device assistant's engine: its AAR has no R8 rules, and
# its JNI library (liblitertlm_jni.so) finds its Kotlin classes, their
# fields and the callbacks it calls (InputData$Text, BenchmarkInfo,
# LiteRtLmJniException, the message callbacks...) by name.
-keep class com.google.ai.edge.litertlm.** { *; }

# ONNX Runtime's Java API (the phonemizer, the Piper voice, room mode's wake
# word): its AAR has no R8 rules, and libonnxruntime4j_jni.so builds
# OnnxTensor, TensorInfo, OrtException... by class name and reads their
# fields.
-keep class ai.onnxruntime.** { *; }

# sherpa-onnx (built here: native/sherpa-onnx): libsherpa-onnx-jni.so reads
# every config field by name (GetFieldID on OfflineModelConfig,
# SileroVadModelConfig...; many are never read from Kotlin, so R8 would drop
# them) and builds OfflineRecognizerResult and SpeechSegment by class name.
-keep class com.k2fsa.sherpa.onnx.** { *; }

# player/CastAwarePlayer.ownCommands: a java.lang.reflect.Proxy for
# Player.Listener that picks out onAvailableCommandsChanged by its method
# name. Renamed, the match never fires and a book's notification loses its
# next-chapter button.
-keepclassmembernames interface androidx.media3.common.Player$Listener {
    void onAvailableCommandsChanged(androidx.media3.common.Player$Commands);
}
