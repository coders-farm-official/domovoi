pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // sherpa-onnx (on-device speech) publishes its Android AAR only as a
        // GitHub release asset, not to any Maven repository. Gradle fetches
        // it from the release like any other dependency, caches it, and
        // checks its SHA-256 against gradle/verification-metadata.xml. Only
        // that one group may come from here.
        exclusiveContent {
            forRepository {
                ivy("https://github.com/k2-fsa/sherpa-onnx/releases/download/") {
                    name = "sherpaOnnxReleases"
                    patternLayout { artifact("v[revision]/[artifact]-[revision].[ext]") }
                    metadataSources { artifact() }
                }
            }
            filter { includeGroup("com.k2fsa.sherpa.onnx") }
        }
    }
}

rootProject.name = "domovoi-android"
include(":app")
