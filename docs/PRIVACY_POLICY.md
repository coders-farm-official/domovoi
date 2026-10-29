# Domovoi for Android: privacy policy

Effective 21 October 2026. Published by Coders Farm, the makers of Domovoi
(github.com/coders-farm-official/domovoi). Questions: **info@codersfarm.org**.

Domovoi is a home computer system that runs on your own hardware: your
music, videos and files in every room and on your phone, with voice and AI
when you want them. This policy covers the Domovoi Android app
(`com.domovoi.app`), from Google Play, from GitHub, or from your own
Domovoi server. The short version:

- **Coders Farm collects nothing from the app.** There is no account with
  us, no analytics, no advertising, no crash reporting and no tracking of
  any kind. The app never sends us anything unless you choose to email us
  a report (below).
- **What the app shares, it shares with your own Domovoi server**, on your
  home network, which you or your household run. We cannot see it.
- **Phone chats run on the phone.** A chat with "Domovoi on this phone"
  is answered by a model on the phone, and what you ask it and what it
  answers stay on the phone, with two exceptions you choose each time: a
  lookup you allow sends the words of that lookup to the service that
  answers it (see "Lookups you allow"), and a document, a photo of a
  letter or a draft you choose to hand to your home server goes to that
  server only (see "Documents, letters and drafts").

## What stays on the phone

The app keeps these in its private storage on the phone. Other apps can't
read it, it is left out of cloud backups and phone-to-phone transfers, and
uninstalling the app deletes it.

- **Settings**: the address of your Domovoi server, the theme, this phone's
  name, the person it is "listening as", the phone bar, and the servers you
  trusted with their identity keys.
- **The household pairing token**, sealed with a key in Android's Keystore
  that never leaves the phone (but see "save to device" for videos, below).
- **Phone chats**: the models (downloaded once, see below), the
  **conversations** (one file each, until you delete them; "clear all
  conversations" deletes every one), with what a lookup found (the titles
  and addresses of its sources) and the text read from a photo or a letter photo,
  the **documents** you attach and the **drafts** written in them (deleted
  with the chat), drafts you save to the phone's "Chat documents", and
  **reply reports** you save.
- **Your own search**, if you set one for lookups: the SearXNG address or
  the Brave Search key, sealed with a key in Android's Keystore.
- **Problem reports**: crashes and "not responding" closes of the app,
  kept so you can copy one if you want help. They are never sent anywhere.
- **Timer and reminder alarms** this phone mirrors from your server, with a
  reminder's words, so it can ring when the app is closed.
- **Room mode's** room key and settings, when you make the phone a room.
- **Audiobooks you save to the phone** and your place in them.

## The microphone

The app uses the microphone only for features you start, and Android shows
its microphone indicator whenever it is on.

- **Phone chats**: the microphone opens only when you tap the mic button,
  and closes when you stop talking, when you tap again, or when you leave
  the screen. The sound is turned into text **on the phone** and then
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

The camera is asked for only when you first use it, and you can always say
no. Room mode can scan the room code your dashboard shows: the camera opens
only when you press "Scan", the picture is read on the phone and never
stored or sent, and you can paste the code instead. In a phone chat you can
take a photo to ask about (see below); the photo is held in memory, not
saved.

## What phone chats read on the phone

A phone chat can answer questions about the phone itself, read on the
phone at the moment you ask and never sent anywhere (not to your Domovoi
server, not to us): what is playing, the battery, free space, whether you
are on Wi-Fi or mobile data, your next alarm, Do Not Disturb, the phone's
model and Android version, connected headphones, and the music, videos and
saved audiobooks on the phone. What it reads goes only into that
conversation, which stays in the app's private storage.

Three things need your permission, asked the first time you ask for them,
with the reason, and always optional:

- **Approximate location**, for "where am I" (the last location Android
  already has, shown as coordinates; never looked up online, never in the
  background). **Precise location** is asked for separately and only to
  read the Wi-Fi network's name, which Android shows to no other app.
- **Contacts**, to call or text someone by name. Without it, you choose the
  person in your contacts app each time.
- **Calendar**, to read what is on this phone's calendar.

Say no and the phone chat says it can't do that, and doesn't ask again;
you can change it in Android's settings for the app. Calls, texts, emails,
directions, calendar events, timers, alarms, links and sharing open the
app that does them (timers and alarms, the Clock app), after you tap or
say yes; you finish there. The app never reads your texts, call log or
notifications.

In a phone chat you can ask about a photo, on phones fast enough to read
one. The camera is asked for the first time you tap "take a photo";
choosing a photo uses Android's photo picker and needs no permission. The
photo is shrunk and read by the model on the phone, in memory: it isn't
saved to your gallery or the app's storage, it isn't sent anywhere (except
a letter you choose to have your home server read, below), and it is
discarded from memory once Domovoi has answered. The chat keeps a "photo"
mark, not the photo. Since 2026-10-22 the words read from the photo are
kept beside its answer in the phone's own chat storage, so you can ask
about it afterwards; they are deleted with the chat.

## Lookups you allow

A phone chat can look something up online, but only when you allow it.
Before anything goes to the internet, a card shows the exact words (or the
page address) and where they would go, and you choose **Allow once**,
**Allow in this chat** or **Not now**. "Always allow internet", in the
model settings, is off until you turn it on. Nothing goes to Coders Farm.

- **At home**, with your Domovoi server connected, the phone asks your
  server. A search first looks in the server's own manual and libraries,
  which stay in your house, with no card. Only if you allowed the lookup,
  and your server's own internet setting allows it, does the server go on
  to Wikipedia, Wikidata and the web through the search your household set
  up, or read the page you named. A server set to keep everything in the
  house refuses, and the phone does not go around it.
- **Away from home**, or when your server can't be reached, the phone
  itself sends the words to **Wikipedia** (en.wikipedia.org). If you chose
  "Use your own search" in the model settings, it sends them to Wikipedia
  first and then to your own SearXNG address or to **Brave Search** with
  your own key. The key is sent only to Brave. Those services see the
  words, the phone's internet address and the app's name and version, and
  treat them under their own policies. The phone sends no account,
  household token or cookie.
- **A page you name** (such as "what does this page say"), away from home,
  is fetched by the phone from that page's own site, with no cookies, and
  only its text is read (2 MB at most). The site sees the request like any
  visit. Pages must start with https://.

What comes back is read on the phone. The conversation keeps your question,
the answer, the titles and addresses of the sources, and whether the chat
may go online. It stays in the app's private storage until you delete the
chat.

## Documents, letters and drafts

Documents you attach or write in a phone chat stay in the app's private
storage, and are deleted with the chat. Three things can go to your own
Domovoi server, each only when you tap it:

- **"Ask the home server"**, for a PDF this phone can't read itself: the
  server reads its text in a temporary folder, sends the text back and
  keeps nothing.
- **"Use home server"** on a long summary: the document is deleted on the
  server as soon as it has been read; its text and the summary are held in
  the server's memory only until the phone has taken the summary. If the
  phone never comes back for it, the server forgets it after at most 24
  hours (10 minutes once it is done and no phone is watching), and a
  restart forgets it at once.
- **"A letter for the home server"**, on a phone that can't read photos
  itself: that one photo goes to your home server, is read there in memory
  and isn't kept there; only the text read from it comes back.

When you ask what a letter says, the text read from the photo is kept
beside the answer in that phone chat, so you can ask about it again; it is
deleted when you delete the chat. The photo itself is never kept.

**Send to home** on a draft puts that one file (Word or PDF) into your home
server's Documents, in the "From Chat" folder, where it stays like any
file there. Nothing else of the chat is sent.

## Photos, music, videos and files on the phone

When no server is connected, the app can play the music and videos on the
phone and show its photos and the folders you add, with the media
permissions Android asks you for. They are read on the phone and not sent
anywhere. A file goes to your Domovoi server only when you upload it there
yourself.

Music, podcasts and videos you save from your server with "save to device"
are handed to Android's download manager and land in the Downloads/Domovoi
folder, where other apps with media access can see them; they are not in
the app's private storage, and removing the app does not remove them. For a
saved video the download manager also keeps your household token in its
own private database until you remove that download from Android's list.
Music, podcast and audiobook saves carry no token. "Save to phone" for an
audiobook stays in the app's private storage.

## What goes to your Domovoi server

When you connect the app to a Domovoi server (at an address you give it,
or one it finds on your home network by asking the addresses there whether
they are a Domovoi server), it talks to that server and to nothing else of
ours. It sends what each feature needs: your chat messages and mic audio,
what you play and control, files you upload, the documents, letters,
drafts and lookups you hand to it, timers you set or cancel, this phone's
name, and the pairing token so the server knows the phone. Documents and
spreadsheets you edit in the app open in your server's own editor page
(the page and its fonts come from the server, and what you type, and the
edits of other household devices editing the same document, go to and
from the server only). In room
mode the phone also sends the room's health to the server: battery level,
whether it is charging, Wi-Fi signal and speed, memory, temperature and
whether the microphone is working.

Your server is run by you or your household, on your own hardware; what it
keeps is described in its own documentation
(`docs/SECURITY_PRIVACY.md` in the Domovoi repository). Coders Farm has no
access to it.

Traffic to your server may be plain HTTP: the app allows that **only** to
addresses on your home network (private addresses such as 192.168.x.x,
and local names). Every address on the internet must use HTTPS. The same
rule holds for your own search address: http:// only for an address on
your home network, https:// for anything else.

## What goes to the internet

The app contacts the internet only for these, each started by you:

- **Phone chat models and phone voices**: downloaded once, when you ask,
  over HTTPS from Hugging Face (the language model, the speech
  recognition and voice detection models, and the Piper voices), each file
  pinned to one exact version and checked against its fingerprint. Hugging
  Face sees the request like any download (your IP address); nothing about
  you or your conversations is sent.
- **Lookups you allow** (see "Lookups you allow"): away from home, the
  words of the lookup go to Wikipedia and, if you set one, your own
  search, or the address of a page you named goes to that page's site.
  Never without your allow.
- **Internet radio and other streams** your server lists: when you play
  one, the phone fetches it from the station's own server, like a browser
  would.
- **Links you open**, such as this policy, open in your browser.

The app does not check for updates by itself: Google Play updates it, or
your Domovoi server offers new versions.

## Reports about Domovoi's replies

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
