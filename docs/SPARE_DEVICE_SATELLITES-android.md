# Spare-device satellites: an Android phone or tablet as a room

> The phone section of `docs/SPARE_DEVICE_SATELLITES.md` (the Linux laptop
> guide lives there too). Written on `wf/room-mode` while that file was not
> on main yet; the integrator folds it in.

An old phone or tablet on its charger can be a **room**: it listens for the
wake word, sends what you say after it to your Domovoi server, speaks the
answer, plays the room's music and takes drop-ins and announcements, like a
Raspberry Pi satellite. It runs inside the normal Domovoi Android app as
**room mode**, with its own home-screen icon, **Domovoi Room**.

## What you need

| | |
|---|---|
| A phone or tablet | Android 8.0 or later, 32-bit or 64-bit (use the armeabi-v7a APK on a 32-bit phone: [ANDROID_COMPATIBILITY.md](ANDROID_COMPATIBILITY.md)). About 1-2 GB of RAM is enough; room mode loads none of the on-device assistant's libraries. |
| Its charger | A room listens all day. Turn on the phone's battery-protection (charge limit) setting if it has one, and never use a phone whose battery has swollen. |
| Your home Wi-Fi | Room mode uses Wi-Fi (or a wired dock) only, never mobile data: with no Wi-Fi it is offline. |
| The server's wake-word models | The phone runs the same openWakeWord models as a Pi, fetched from your server. On the dashboard, **Settings → Satellites → prepare satellite media → Refresh caches** once (it downloads them; it needs the internet that one time). A Pi you prepared already did this. |
| A room code | On the dashboard, **Settings → Satellites → add a spare device**: a QR code and a line of text that start `domovoi-room:1?…`. It names your server, the server's key and the room. It holds no secret. |

## Setting it up (about ten minutes)

1. Install the Domovoi app on the spare phone (factory-reset it first if
   you can: it becomes a shared device).
2. Open **Domovoi Room** from the home screen. (On a phone that already
   uses the app with your household: Settings → Satellites → **Use this
   device as a room**.)
3. Read the explainer to the end and press **Start room mode**. It says
   what room mode means: always listening, a shared device, power and
   heat, Android's rules, what stops working, how to leave.
4. Allow the **microphone** (and, on Android 13+, notifications).
5. **Scan the room code** (the camera is asked for only now), or paste the
   text. The phone shows the server's address and the start of its key:
   compare the key with the dashboard's Settings → About. Change the room
   name if you like, then press **Become this room**.
6. The room screen shows **waiting for approval** and a six-digit code. On
   the dashboard, Settings → Satellites shows an approval card for the
   room: type the code the phone shows and approve. Within about half a
   minute the phone says **Say "Hey Jarvis"** (press **check now** to skip
   the wait).
7. Say "Hey Jarvis, what time is it?".

The wake word is the server's default, `hey_jarvis`; a wake word you train
and push from the dashboard (Settings → Wake words) reaches the phone the
same way it reaches a Pi.

## The room screen

A dark full-screen display, deliberately unlike the app: the room's name,
a clock, a ring that says what the room is doing — **idle**, **listening**,
**thinking**, **speaking**, **muted**, **offline**, **mic blocked** — the
words under it, and:

- **the mic chip** (top right), always visible: red **mic live** while what
  the room hears is going to the server (a command, or a drop-in), plain
  **mic** while it only listens for the wake word, struck-through **mic
  off** when muted;
- **mute**: closes the microphone (Android's green dot goes off). Anyone in
  the room can mute it, from the lock screen too;
- **exit room mode**: hold it, then unlock with the phone's PIN, pattern or
  fingerprint. On a phone with no screen lock it asks first and says so.

The screen moves its contents a few pixels every minute so a display left
on does not burn in. It is shown over the lock screen.

## What a phone room does

| | |
|---|---|
| Wake word, commands, answers, follow-up questions | yes (wake word on the phone, speech-to-text and everything after it on the server, as for a Pi) |
| The wake acknowledgement | a greeting clip from the server, or a chime when music was playing; what the mic hears under it is dropped |
| Early commit ("pause the music" answered at once) | yes |
| Timer, reminder and intercom announcements | yes |
| The room's music | yes, in its own player (not the app's); paused for an answer, resumed by the server after |
| Volume ("turn it up") | yes: the phone's media volume |
| Drop-ins | on phones with an echo canceller only (most phones; the app checks). The room says so to the server, which refuses a drop-in otherwise |
| Chat mode | no (not in this version) |
| Restart, upgrade, Settings tab, live log, wake-word clip recording on the dashboard | no: the app updates from its store or a new APK; the dashboard greys these out for an "Android room" |

The dashboard shows the room with an **Android room** badge, and its
health block shows the phone's battery and **mic blocked** when Android is
handing the room silence.

## Android's rules

- **A notification stays** while the room listens ("Domovoi Room ·
  kitchen"), with **Stop listening** (behind the phone's unlock on Android
  12+). The **green microphone dot** stays on. Both are expected.
- **After a restart** Android lets no app start listening by itself: the
  phone shows **"…is not listening — tap to resume"**. One tap.
- **Battery optimisation**: turn it off for Domovoi (the explainer has a
  button for the settings page), or Android may put the room to sleep.
- **The microphone privacy toggle** (Android 12+, quick settings) makes
  the room deaf: the ring says **mic blocked**, and so does the dashboard.
  A phone call, or another app using the microphone, does the same until
  it ends.
- **Chromebooks**: keep the lid open, plugged in, with sleep off.

## A phone that is also your household phone

You can turn your own phone into a room for a while. Then:

- the normal app is **locked** (no files, browsing, chat, assistant)
  until you exit room mode with the phone's unlock;
- the app's **household pairing stays on the phone but is not used**: no
  request carries it while room mode is on, the app's player stops and its
  live connection to the server closes (timer alerts for the household
  stop reaching this phone until you exit);
- the room has its **own key**, made on the phone, and the server knows
  the room by it alone. Removing the room on the dashboard and forgetting
  the phone as a household device are two separate things.

## When something is wrong

| The room screen says | What to do |
|---|---|
| waiting for approval | Approve it on the dashboard with the code on the phone. |
| Another device is this room on the server | That room is paired with another device. On the dashboard, open the room and reset its pairing, or exit and choose another name. |
| Another device is already waiting for approval as this room | Reject the other request on the dashboard, or wait three minutes. |
| …this is a different server… / …would not sign for… | The address in the room code answered with a different key, or something is relaying it. Make a new room code on the right server. A server reached through a name or a forwarded port needs that name in its `TRUSTED_HOSTS`. |
| The server has no wake-word models yet | Dashboard: Settings → Satellites → prepare satellite media → Refresh caches. |
| The server does not hand wake-word models to phones yet | Update the Domovoi server. |
| waiting for Wi-Fi | The phone has no Wi-Fi. Room mode never uses mobile data. |
| (on the room code screen) a room's server must be on the home network | The room code names a server outside your home network. A room only talks to a server at a local address or a `.local` / `.home.arpa` / `.lan` name. |
| mic blocked | Turn the microphone back on in quick settings, or end the call. |
| Not listening. Tap Resume | Someone pressed Stop listening, or the phone restarted. |

## For developers

The code is `android/app/src/main/java/com/domovoi/app/roommode/`; how it
is built and tested is in [android/README.md](../android/README.md#room-mode),
and what the phone holds in [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#a-room-phone-android-room-mode).
