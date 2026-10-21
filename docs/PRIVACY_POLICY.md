# Domovoi for Android: privacy policy

Effective 16 October 2026. Published by Coders Farm, the makers of Domovoi
(github.com/coders-farm-official/domovoi). Questions: **info@codersfarm.org**.

Domovoi is a home assistant that runs on your own hardware. This policy
covers the Domovoi Android app (`com.domovoi.app`), from Google Play, from
GitHub, or from your own Domovoi server. The short version:

- **Coders Farm collects nothing from the app.** There is no account with
  us, no analytics, no advertising, no crash reporting and no tracking of
  any kind. The app never sends us anything unless you choose to email us
  a report (below).
- **What the app shares, it shares with your own Domovoi server**, on your
  home network, which you or your household run. We cannot see it.
- **The on-device assistant runs on the phone.** What you ask it and what
  it answers stay on the phone.

## What stays on the phone

The app keeps these in its private storage on the phone. Other apps can't
read it, it is left out of cloud backups and phone-to-phone transfers, and
uninstalling the app deletes it.

- **Settings**: the address of your Domovoi server, the theme, this phone's
  name, the person it is "listening as", the phone bar, and the servers you
  trusted with their identity keys.
- **The household pairing token**, sealed with a key in Android's Keystore
  that never leaves the phone.
- **The on-device assistant's models** (downloaded once, see below), its
  **conversations** (one file each, until you delete them; "clear all
  conversations" deletes every one) and **reply reports** you save.
- **Problem reports**: crashes and "not responding" closes of the app,
  kept so you can copy one if you want help. They are never sent anywhere.
- **Timer and reminder alarms** this phone mirrors from your server, with a
  reminder's words, so it can ring when the app is closed.
- **Room mode's** room key and settings, when you make the phone a room.

## The microphone

The app uses the microphone only for features you start, and Android shows
its microphone indicator whenever it is on.

- **On-device assistant**: the microphone opens only when you tap the mic
  button, and closes when you stop talking, when you tap again, or when you
  leave the screen. The sound is turned into text **on the phone** and then
  discarded: audio is never saved and never sent anywhere. Only the text of
  your question is kept, as part of the conversation.
- **Chat with your server, and the intercom ("drop in")**: while you hold
  the mic or are in a call, the sound goes to **your Domovoi server** to be
  transcribed or to reach the other room.
- **Room mode** (a spare phone or tablet as a room speaker, which you turn
  on yourself after a screen that explains it): the microphone stays on to
  hear the wake word, on the phone. Only what is said **after** the wake
  word goes to your Domovoi server, like any Domovoi room. A notification
  that cannot be hidden stays while it listens, and "Stop listening" turns
  the microphone off.

**Voice ID** (recognising who in the household is speaking) is a feature
of your Domovoi server, not of the app. If your household turned it on,
your server may compare the speech it receives from the app with the voice
samples stored on that server. Nothing about voices is stored by the app
or sent to us.

## The camera

Room mode can scan the room code your dashboard shows. The camera is asked
for only when you press "Scan", the picture is read on the phone and never
stored or sent, and you can paste the code instead.

## What the on-device assistant reads on the phone

A chat "on this phone" can answer questions about the phone itself, read
on the phone at the moment you ask and never sent anywhere (not to your
Domovoi server, not to us): what is playing, the battery, free space,
whether you are on Wi-Fi or mobile data, your next alarm, Do Not Disturb,
the phone's model and
Android version, connected headphones, and the music, videos and saved
audiobooks on the phone. What it reads goes only into that conversation,
which stays in the app's private storage.

Three things need your permission, asked the first time you ask for them,
with the reason, and always optional:

- **Approximate location**, for "where am I" (the last location Android
  already has, shown as coordinates; never looked up online, never in the
  background). **Precise location** is asked for separately and only to
  read the Wi-Fi network's name, which Android shows to no other app.
- **Contacts**, to call or text someone by name. Without it, you choose the
  person in your contacts app each time.
- **Calendar**, to read what is on this phone's calendar.

Say no and the assistant says it can't do that, and doesn't ask again;
you can change it in Android's settings for the app. Calls, texts, emails,
directions, calendar events, links and sharing open the app that does
them, after you tap; you finish there. The app never reads your texts, call
log or notifications.

## Photos, music, videos and files on the phone

When no server is connected, the app can play the music and videos on the
phone and show its photos and the folders you add, with the media
permissions Android asks you for. They are read on the phone and not sent
anywhere. A file goes to your Domovoi server only when you upload it there
yourself.

Documents you attach or write in a phone chat stay in the app's private
storage. A PDF this phone can't read itself goes to your Domovoi server
only when you tap "ask the home server", which reads its text, sends it
back and keeps nothing.

## What goes to your Domovoi server

When you connect the app to a Domovoi server (at an address you give it,
or one it finds on your home network by asking the addresses there whether
they are a Domovoi server), it talks to that server and to nothing else of
ours. It sends what each feature needs: your chat messages and mic audio,
what you play and control, files you upload, timers you set or cancel, this
phone's name, and the pairing token so the server knows the phone. In room
mode the phone also sends the room's health to the server: battery level,
whether it is charging, Wi-Fi signal and speed, memory, temperature and
whether the microphone is working.

Your server is run by you or your household, on your own hardware; what it
keeps is described in its own documentation
(`docs/SECURITY_PRIVACY.md` in the Domovoi repository). Coders Farm has no
access to it.

Traffic to your server may be plain HTTP: the app allows that **only** to
addresses on your home network (private addresses such as 192.168.x.x,
and local names). Every address on the internet must use HTTPS.

## What goes to the internet

The app contacts the internet only for these, each started by you:

- **On-device assistant models and phone voices**: downloaded once, when
  you ask, over HTTPS from Hugging Face (the language model, the speech
  recognition and voice detection models, and the Piper voices), each file
  pinned to one exact version and checked against its fingerprint. Hugging
  Face sees the request like any download (your IP address); nothing about
  you or your conversations is sent.
- **Internet radio and other streams** your server lists: when you play
  one, the phone fetches it from the station's own server, like a browser
  would.
- **Links you open**, such as this policy, open in your browser.

The app does not check for updates by itself: Google Play updates it, or
your Domovoi server offers new versions.

## Reports about the assistant's replies

If one of Domovoi's answers in the app's Chat is offensive, harmful or
wrong, whether the model on the phone or the one on your home server wrote
it, long-press it and choose **report this reply**. The report shows
exactly what it holds: your question before that reply, the reply, which
model wrote it, where (this phone or the home server) and when, the app's
version, and an optional note.

- **Save on this phone** keeps it in the app only (offered for the phone's
  own replies, listed under its model settings).
- **Email draft to Coders Farm** opens your email app with the report
  addressed to info@codersfarm.org (codersfarm@gmail.com in the first
  release, build 26101501). Nothing is sent until you press send in your
  email app. If you send it, we receive what the report shows plus what any
  email carries (your email address), and use it only to look into the
  reply and improve Domovoi. Ask us at the address you sent it to, or at
  info@codersfarm.org, to delete it.
- **Share as text** hands it to any app you choose.

## Children

Domovoi is a household app and is not directed at children. We do not
knowingly collect anything from anyone, of any age.

## Changes

If this policy changes, the new version will be published at the same
address with a new date, and the app's store listing will link to it.
