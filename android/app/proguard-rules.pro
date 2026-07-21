# Keep kotlinx.serialization generated serializers
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** { kotlinx.serialization.KSerializer serializer(...); }
-keep,includedescriptorclasses class com.domovoi.app.**$$serializer { *; }
-keepclassmembers class com.domovoi.app.** { *** Companion; }
-keepclasseswithmembers class com.domovoi.app.** { kotlinx.serialization.KSerializer serializer(...); }
