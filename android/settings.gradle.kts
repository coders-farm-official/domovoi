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
    }
}

rootProject.name = "domovoi-android"
include(":app")

/*
 * Security floors for libraries the build pulls in second-hand (OpenSSF
 * Scorecard's Vulnerabilities check, OSV-Scanner over
 * gradle/verification-metadata.xml). Nearly all of these are build tools'
 * own libraries, never in the APK: the Android Gradle plugin's classpath
 * (signing, bundletool, lint: Bouncy Castle, commons-compress, httpclient,
 * jose4j, jdom2, Netty via gRPC) and its unified test platform
 * configurations (Netty, Guava, Protobuf). Guava is also in the app itself
 * (through AndroidX). Each of the versions those tools ask for has a known
 * advisory; the floor is the same release line with the fix.
 *
 * A requested version BELOW its floor is raised to it, keeping its suffix
 * (".Final", "-android", "-jre"; a bare old Guava gets "-jre"); one at or above it is left alone, so a
 * later plugin or library bump that brings something newer wins. Applied to
 * every project's own configurations and its buildscript classpath (the
 * plugins block resolves there). A bump that changes what resolves also
 * needs gradle/verification-metadata.xml rewritten (its header says how).
 */
val securityFloors: Map<String, String> = buildMap {
    for (m in listOf(
        "netty-buffer", "netty-codec", "netty-codec-http", "netty-codec-http2",
        "netty-codec-socks", "netty-common", "netty-handler", "netty-handler-proxy",
        "netty-resolver", "netty-transport", "netty-transport-native-unix-common",
    )) put("io.netty:$m", "4.1.141")
    put("com.google.guava:guava", "33.7.2")
    for (m in listOf("protobuf-java", "protobuf-java-util", "protobuf-kotlin")) {
        put("com.google.protobuf:$m", "3.25.5")
    }
    for (m in listOf("bcprov-jdk18on", "bcpkix-jdk18on", "bcutil-jdk18on")) {
        put("org.bouncycastle:$m", "1.87")
    }
    put("org.apache.commons:commons-compress", "1.28.0")
    put("org.apache.httpcomponents:httpclient", "4.5.14")
    put("org.bitbucket.b_c:jose4j", "0.9.6")
    put("org.jdom:jdom2", "2.0.6.1")
}

/** "4.1.110.Final" -> ([4, 1, 110], ".Final"); "33.3.1-android" -> ([33, 3, 1], "-android"). */
fun splitVersion(v: String): Pair<List<Int>, String> {
    val numeric = Regex("""^[0-9]+(\.[0-9]+)*""").find(v)?.value ?: return emptyList<Int>() to v
    return numeric.split('.').map { it.toInt() } to v.substring(numeric.length)
}

fun belowFloor(version: List<Int>, floor: List<Int>): Boolean {
    for (i in 0 until maxOf(version.size, floor.size)) {
        val a = version.getOrElse(i) { 0 }
        val b = floor.getOrElse(i) { 0 }
        if (a != b) return a < b
    }
    return false
}

/** The version to use instead of [asked] for group:name, or null to keep it. */
fun floorFor(group: String, name: String, asked: String?): String? {
    val floor = securityFloors["$group:$name"] ?: return null
    val (numbers, suffix) = splitVersion(asked ?: return null)
    if (numbers.isEmpty() || !belowFloor(numbers, splitVersion(floor).first)) return null
    // An old Guava asked for without a flavour ("18.0") gets the JVM one.
    return floor + if (suffix.isEmpty() && name == "guava") "-jre" else suffix
}

val raiseToFloor = Action<DependencyResolveDetails> {
    val floor = floorFor(requested.group, requested.name, requested.version) ?: return@Action
    useVersion(floor)
    because("security floor (android/settings.gradle.kts): ${requested.version} has a known advisory")
}

// Two hooks, because neither reaches everything. The resolve rule covers each
// project's configurations and buildscript classpath; the metadata rule
// rewrites what every module asks for, so it also reaches the classpaths the
// Android plugin resolves on its own (detached ones, such as lint's).
gradle.beforeProject {
    buildscript.configurations.configureEach { resolutionStrategy.eachDependency(raiseToFloor) }
    configurations.configureEach { resolutionStrategy.eachDependency(raiseToFloor) }
}
dependencyResolutionManagement {
    components {
        all {
            allVariants {
                withDependencies {
                    for (dep in this) {
                        val vc = dep.versionConstraint
                        val asked = vc.requiredVersion.ifEmpty { vc.preferredVersion }.ifEmpty { vc.strictVersion }
                        val floor = floorFor(dep.group, dep.name, asked.ifEmpty { null }) ?: continue
                        dep.version { require(floor) }
                        dep.because("security floor (android/settings.gradle.kts): $asked has a known advisory")
                    }
                }
            }
        }
    }
}
