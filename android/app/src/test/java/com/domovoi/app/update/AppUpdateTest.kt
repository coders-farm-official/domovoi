package com.domovoi.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * "Domovoi 1.0.1 is available from your server" (update/AppUpdate.kt):
 * when this phone hears about a newer app, which APK it is pointed at, and
 * when it never hears about one (a Play install, a debug build, another key).
 */
class AppUpdateTest {
    private val releaseCert = "29" + "6b".repeat(31)
    private val joinUrl = "http://192.168.1.20:6370/join"

    private fun install(
        code: Long = 26101501,
        name: String = "1.0.0-rc1",
        debuggable: Boolean = false,
        installer: String? = "com.google.android.packageinstaller",
        certs: Set<String> = setOf(releaseCert),
        abis: List<String> = listOf("arm64-v8a", "armeabi-v7a", "armeabi"),
    ) = ThisInstall(code, name, debuggable, installer, certs, abis)

    private fun offer(
        code: Long = 26110101,
        name: String = "1.0.1",
        ready64: Boolean = true,
        ready32: Boolean = true,
        cert: String? = releaseCert,
        join: String? = joinUrl,
    ) = AppOffer(
        joinUrl = join,
        release = OfferedRelease(versionName = name, versionCode = code, certSha256 = cert),
        apks = listOf(
            OfferedApk("arm64-v8a", "domovoi-$name-arm64-v8a.apk", 80_000_000, ready64),
            OfferedApk("armeabi-v7a", "domovoi-$name-armeabi-v7a.apk", 48_000_000, ready32),
        ),
    )

    // ── version compare ─────────────────────────────────────────────────

    @Test fun onlyAHigherVersionCodeIsNewer() {
        assertTrue(AppUpdate.isNewer(26110101, 26101501))
        assertFalse(AppUpdate.isNewer(26101501, 26101501))
        assertFalse(AppUpdate.isNewer(26100101, 26101501))
    }

    @Test fun theSameOrAnOlderReleaseIsUpToDate() {
        val same = AppUpdate.decide(install(), offer(code = 26101501, name = "1.0.0-rc1"))
        assertEquals(UpdateState.UpToDate("1.0.0-rc1", "1.0.0-rc1"), same)
        assertEquals("Up to date: Domovoi 1.0.0-rc1, the release your server offers.", AppUpdate.aboutText(same))
        val older = AppUpdate.decide(install(code = 26120101, name = "1.0.2"), offer())
        assertEquals("Up to date: this phone has Domovoi 1.0.2; your server offers 1.0.1.", AppUpdate.aboutText(older))
        assertFalse(AppUpdate.shows(older, null))
    }

    @Test fun theRc2SchemeGoesUpByVersionCodeAlone() {
        // 1.0.0-rc2 (26101602) < 1.0.0 < 1.0.1. As text "1.0.0-rc2" sorts
        // after "1.0.0"; the notice never compares names, only versionCode.
        val rc2 = install(code = 26101602, name = "1.0.0-rc2")
        assertTrue(AppUpdate.decide(rc2, offer(code = 26101501, name = "1.0.0-rc1")) is UpdateState.UpToDate)
        assertTrue(AppUpdate.decide(rc2, offer(code = 26101602, name = "1.0.0-rc2")) is UpdateState.UpToDate)
        assertEquals(
            UpdateState.Available("1.0.0", 26102401, "arm64-v8a", "$joinUrl?abi=arm64-v8a&update=1"),
            AppUpdate.decide(rc2, offer(code = 26102401, name = "1.0.0")),
        )
        val final = install(code = 26102401, name = "1.0.0")
        assertTrue(AppUpdate.decide(final, offer(code = 26101602, name = "1.0.0-rc2")) is UpdateState.UpToDate)
        assertTrue(AppUpdate.decide(final, offer(code = 26110101, name = "1.0.1")) is UpdateState.Available)
    }

    @Test fun theRc3SchemeGoesUpByVersionCodeAlone() {
        // 1.0.0-rc2 (26101602) < 1.0.0-rc3 (26102001) < 1.0.0. An rc2 phone
        // hears about rc3; an rc3 phone hears about 1.0.0, not rc2 or rc3.
        val rc2 = install(code = 26101602, name = "1.0.0-rc2")
        assertEquals(
            UpdateState.Available("1.0.0-rc3", 26102001, "arm64-v8a", "$joinUrl?abi=arm64-v8a&update=1"),
            AppUpdate.decide(rc2, offer(code = 26102001, name = "1.0.0-rc3")),
        )
        val rc3 = install(code = 26102001, name = "1.0.0-rc3")
        assertTrue(AppUpdate.decide(rc3, offer(code = 26101602, name = "1.0.0-rc2")) is UpdateState.UpToDate)
        assertTrue(AppUpdate.decide(rc3, offer(code = 26102001, name = "1.0.0-rc3")) is UpdateState.UpToDate)
        assertEquals(
            UpdateState.Available("1.0.0", 26102401, "arm64-v8a", "$joinUrl?abi=arm64-v8a&update=1"),
            AppUpdate.decide(rc3, offer(code = 26102401, name = "1.0.0")),
        )
        val final = install(code = 26102401, name = "1.0.0")
        assertTrue(AppUpdate.decide(final, offer(code = 26102001, name = "1.0.0-rc3")) is UpdateState.UpToDate)
    }

    @Test fun aNewerReleaseOnTheServerIsAvailableWithItsJoinPage() {
        val state = AppUpdate.decide(install(), offer())
        assertEquals(
            UpdateState.Available("1.0.1", 26110101, "arm64-v8a", "$joinUrl?abi=arm64-v8a&update=1"),
            state,
        )
        assertTrue(AppUpdate.shows(state, null))
        assertEquals("Domovoi 1.0.1 is available from your server.", AppUpdate.noticeText(state))
    }

    @Test fun noReleaseOrNoAnswerSaysNothingOutsideAbout() {
        for (o in listOf(null, AppOffer(release = null), offer(code = 0), offer(name = ""))) {
            val state = AppUpdate.decide(install(), o)
            assertFalse("$o", AppUpdate.shows(state, null))
            assertNull(AppUpdate.noticeText(state))
        }
        assertEquals(UpdateState.NoRelease, AppUpdate.decide(install(), AppOffer(release = null)))
        assertEquals(UpdateState.Unknown, AppUpdate.decide(install(), null))
    }

    // ── never for Play, a debug build, or another key ───────────────────

    @Test fun aPlayInstallNeverHearsOfIt() {
        val state = AppUpdate.decide(install(installer = "com.android.vending"), offer())
        assertEquals(UpdateState.PlayStore, state)
        assertFalse(AppUpdate.shows(state, null))
        assertEquals("Installed from Google Play, which keeps it up to date.", AppUpdate.aboutText(state))
    }

    @Test fun anyOtherInstallerOrNoneStillDoes() {
        for (who in listOf(null, "com.google.android.packageinstaller", "com.android.packageinstaller", "com.android.shell")) {
            assertFalse(who, AppUpdate.isPlayInstall(who))
            assertTrue(who, AppUpdate.decide(install(installer = who), offer()) is UpdateState.Available)
        }
    }

    @Test fun aDebugBuildSaysSoAndIsNeverComparedWithAReleaseBuild() {
        val state = AppUpdate.decide(install(debuggable = true, certs = setOf("de".repeat(32))), offer())
        assertEquals(UpdateState.DebugBuild, state)
        assertFalse(AppUpdate.shows(state, null))
        assertTrue(AppUpdate.aboutText(state).startsWith("Debug build"))
        // Even a debug build that somehow carries the release key.
        assertEquals(UpdateState.DebugBuild, AppUpdate.decide(install(debuggable = true), offer()))
    }

    @Test fun aCopySignedByAnotherKeyIsNotPointedAtARelease() {
        val state = AppUpdate.decide(install(certs = setOf("ab".repeat(32))), offer())
        assertEquals(UpdateState.OtherKey("1.0.1"), state)
        assertFalse(AppUpdate.shows(state, null))
        assertTrue(AppUpdate.aboutText(state).contains("can't install over it"))
        // A rotated key: the release cert anywhere in this install's lineage counts.
        val rotated = install(certs = setOf("ab".repeat(32), releaseCert))
        assertTrue(AppUpdate.decide(rotated, offer()) is UpdateState.Available)
        // Upper-case hex from a server is the same certificate.
        assertTrue(AppUpdate.decide(install(), offer(cert = releaseCert.uppercase())) is UpdateState.Available)
    }

    @Test fun unknownCertificatesDoNotHideTheNotice() {
        assertTrue(AppUpdate.decide(install(), offer(cert = null)) is UpdateState.Available)
        assertTrue(AppUpdate.decide(install(certs = emptySet()), offer()) is UpdateState.Available)
    }

    // ── which APK ───────────────────────────────────────────────────────

    @Test fun aSixtyFourBitPhoneGetsTheSixtyFourBitApp() {
        val state = AppUpdate.decide(install(), offer()) as UpdateState.Available
        assertEquals("arm64-v8a", state.abi)
    }

    @Test fun aThirtyTwoBitPhoneGetsTheThirtyTwoBitApp() {
        val state = AppUpdate.decide(install(abis = listOf("armeabi-v7a", "armeabi")), offer()) as UpdateState.Available
        assertEquals("armeabi-v7a", state.abi)
        assertEquals("$joinUrl?abi=armeabi-v7a&update=1", state.link)
    }

    @Test fun anX86ChromebookThatRunsArmAppsGetsTheArmOne() {
        val state = AppUpdate.decide(install(abis = listOf("x86_64", "x86", "arm64-v8a", "armeabi-v7a")), offer())
        assertEquals("arm64-v8a", (state as UpdateState.Available).abi)
    }

    @Test fun aPhoneTheReleaseHasNothingForHearsNothing() {
        val state = AppUpdate.decide(install(abis = listOf("x86_64", "x86")), offer())
        assertEquals(UpdateState.NoApkForThisPhone("1.0.1"), state)
        assertFalse(AppUpdate.shows(state, null))
    }

    @Test fun aSixtyFourBitPhoneIsNotMovedToTheThirtyTwoBitApp() {
        val state = AppUpdate.decide(install(), offer(ready64 = false, ready32 = true))
        assertEquals(UpdateState.NotOnServer("1.0.1", 26110101), state)
    }

    @Test fun pickApkFollowsThePhonesOwnOrder() {
        val apks = offer().apks
        assertEquals("armeabi-v7a", AppUpdate.pickApk(listOf("armeabi-v7a", "arm64-v8a"), apks)?.abi)
        assertNull(AppUpdate.pickApk(emptyList(), apks))
        assertNull(AppUpdate.pickApk(listOf("arm64-v8a"), emptyList()))
        // An ABI a release never carries is never picked, whatever the server lists.
        assertNull(AppUpdate.pickApk(listOf("x86_64"), listOf(OfferedApk("x86_64", "x.apk", 1, true))))
    }

    // ── not on the server yet ───────────────────────────────────────────

    @Test fun aReleaseTheServerDoesNotHoldAsksAnAdminWithNoButton() {
        val state = AppUpdate.decide(install(), offer(ready64 = false, ready32 = false))
        assertEquals(UpdateState.NotOnServer("1.0.1", 26110101), state)
        assertTrue(AppUpdate.shows(state, null))
        val text = AppUpdate.noticeText(state)!!
        assertTrue(text, text.contains("An admin can press “Download the app to this server”"))
        val notice = noticeFor(state, onOpen = {}, onDismiss = {})!!
        assertNull("no button the phone can't act on", notice.onGet)
    }

    @Test fun aServerWithNoAddressForPhonesGetsWordsNotADeadButton() {
        val state = AppUpdate.decide(install(), offer(join = null)) as UpdateState.Available
        assertNull(state.link)
        assertNull(noticeFor(state, onOpen = {}, onDismiss = {})!!.onGet)
        assertTrue(AppUpdate.noticeText(state)!!.contains("Settings → Phones & browsers"))
    }

    // ── the join page link ──────────────────────────────────────────────

    @Test fun theLinkIsTheServersJoinPageForThatAbi() {
        assertEquals("$joinUrl?abi=arm64-v8a&update=1", AppUpdate.joinLink(joinUrl, "arm64-v8a"))
        assertEquals("$joinUrl?abi=arm64-v8a&update=1", AppUpdate.joinLink("$joinUrl/", "arm64-v8a"))
        assertEquals("$joinUrl?abi=arm64-v8a&update=1", AppUpdate.joinLink("$joinUrl?app=missing#x", "arm64-v8a"))
        assertEquals(
            "https://domovoi.example:6370/join?abi=armeabi-v7a&update=1",
            AppUpdate.joinLink("https://domovoi.example:6370/join", "armeabi-v7a"),
        )
    }

    @Test fun anythingButAJoinPageIsNoLink() {
        for (bad in listOf(null, "", "not a url", "ftp://192.168.1.20/join", "http://192.168.1.20:6370/",
            "http://192.168.1.20:6370/joined", "http://192.168.1.20:6370/v1/android-app/files/x.apk",
            "javascript:alert(1)")) {
            assertNull(bad, AppUpdate.joinLink(bad, "arm64-v8a"))
        }
        assertNull(AppUpdate.joinLink(joinUrl, "x86_64"))
    }

    // ── dismissal ───────────────────────────────────────────────────────

    private class MemoryStore : DismissalStore {
        var saved: Dismissal? = null
        override fun load() = saved
        override fun save(dismissal: Dismissal) { saved = dismissal }
    }

    @Test fun aDismissedNoticeStaysHiddenUntilTheNextVersion() {
        val store = MemoryStore()
        val available = AppUpdate.decide(install(), offer())
        UpdateDismissals(store).dismiss(available)
        // A new process reads it back.
        val again = UpdateDismissals(store)
        assertEquals(Dismissal(26110101, ready = true), again.dismissed.value)
        assertFalse(again.shows(available))
        assertFalse(again.shows(AppUpdate.decide(install(), offer(ready64 = false))))
        assertTrue("the next version shows", again.shows(AppUpdate.decide(install(), offer(code = 26120101, name = "1.0.2"))))
    }

    @Test fun aDismissedNotYetOnTheServerNoticeComesBackOnceItCanBeInstalled() {
        val store = MemoryStore()
        val dismissals = UpdateDismissals(store)
        dismissals.dismiss(AppUpdate.decide(install(), offer(ready64 = false, ready32 = false)))
        assertFalse(dismissals.shows(AppUpdate.decide(install(), offer(ready64 = false, ready32 = false))))
        val nowReady = AppUpdate.decide(install(), offer())
        assertTrue(UpdateDismissals(store).shows(nowReady))
        dismissals.dismiss(nowReady)
        assertFalse(UpdateDismissals(store).shows(nowReady))
    }

    @Test fun dismissingAStateWithNoNoticeRemembersNothing() {
        val store = MemoryStore()
        UpdateDismissals(store).dismiss(UpdateState.DebugBuild)
        UpdateDismissals(store).dismiss(UpdateState.UpToDate("1.0.1", "1.0.1"))
        assertNull(store.saved)
    }

    @Test fun anUnreadableStoreStillShowsTheNotice() {
        val broken = object : DismissalStore {
            override fun load(): Dismissal? = error("disk")
            override fun save(dismissal: Dismissal) = error("disk")
        }
        val dismissals = UpdateDismissals(broken)
        val state = AppUpdate.decide(install(), offer())
        assertTrue(dismissals.shows(state))
        dismissals.dismiss(state)
        assertFalse("hidden for this run at least", dismissals.shows(state))
    }

    // ── this install's certificate ──────────────────────────────────────

    @Test fun signerDigestsAreLowerCaseSha256Hex() {
        assertEquals(
            setOf("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
            InstallFacts.signerDigests(listOf("abc".toByteArray())),
        )
        assertEquals(emptySet<String>(), InstallFacts.signerDigests(emptyList()))
    }
}
