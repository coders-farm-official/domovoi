# Photos and videos: what Domovoi learns from them

New in 1.5.0. Domovoi can look through the photos and videos in your
libraries and work out what is in them, so you can find them by
words ("dog at the beach", "Christmas 2023"), by who is in them, and by
where they were taken. **All of it happens on your Domovoi server.** No
photo, video, description or face is sent anywhere, and nothing is ever
written into your files.

The routes behind this page are in
[API_REFERENCE.md](API_REFERENCE.md#35c1b-photos-and-videos-media-understanding-150),
how it is built is in
[ARCHITECTURE.md](ARCHITECTURE.md#media-understanding-150), and what is
kept and who sees it is in
[SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#faces-and-people).

## What it learns

| From | What Domovoi keeps |
|---|---|
| A photo | The date it was taken (from the photo's own data, else the file's time), one sentence of what it **might show**, up to 12 tags ("dog", "beach"), and the exact text in it when it has writing. |
| A video | The scene changes, what is said (the first two hours), descriptions of frames picked at scene changes, any text in those frames, a short summary and a suggested title. A search can jump to the moment that matched. |
| Faces (optional) | Who is in a photo or video, and when in a video (from when they appear to when they leave). Off unless an admin turns it on. |
| Places (optional) | The town a photo was taken in. Off unless an admin turns it on. |

Descriptions are written by the same small picture model chat uses.
Transcripts and text in pictures also work with AI features off;
descriptions and summaries need them on.

## Turning it on

An admin turns on **Understand photos and videos** in Settings → Chat & AI →
Photos and videos. A new install starts with it on; a house that upgraded
from 1.4.x starts with it off until an admin turns it on. The same card
shows progress (done, left, failed), an estimate in nights, and has
**Pause**, **Run now**, **Rescan** (all three for an admin) and a list of
excluded folders.

Descriptions need the house's own vision model. If the vision model is an
Ollama cloud model, or the model server is not on your network, Domovoi
skips that part rather than sending your photos out.

Until something has been looked at, the Photos page says so. While a
backlog is left, Home shows a small chip, "Understanding your photos: 3,210
of 20,000", that opens the same card.

**Which folders.** Pictures (photos and videos), Videos, the other media
folders (only their videos: album covers and scanned pages aren't photos),
a plugin's media folders and a USB drive while it is plugged in (photos
and videos). An admin can leave out a whole library or any folder in one
under **Excluded folders**; Domovoi asks first when that deletes what it
already worked out there.

## What runs when

- **At night**, in your quiet hours (22:00 to 07:00 unless you changed
  them), the backlog runs continuously.
- **By day**, only new photos and videos (found in the last 24 hours), and
  only after 10 minutes with nobody talking to Domovoi, resting as long as
  each item took. A photo uploaded from a phone is searchable within
  minutes; an old library is never worked through in the daytime.
- **Run now** ignores the night window until the backlog is done.
- **Voice always comes first.** Every step pauses the moment someone speaks
  to Domovoi and carries on afterwards. It waits for enough free memory
  before using the picture model, reads files slowly (at most 20 MB a
  second), and runs its helper programs at the lowest priority.
- Videos attached in a chat go first, because someone is waiting.
- A file that fails three times is marked failed and skipped until it
  changes.

On a server with no graphics card, a large library takes many nights the
first time. The estimate on the card says how many.

## How many frames of a video: Automatic or manual

A frame is one still picture Domovoi asks the picture model to describe.
More frames find more detail in a long video and cost more time.

- **Automatic** (the default). Domovoi times the picture model on your own
  server (the first time, and after you change the model) and never guesses
  from the graphics card's name. It picks frames at scene changes, so a
  short clip gets only a few and a long video gets more, up to a limit:
  120 frames on a server without a fast graphics card, and more on one
  with it (up to 600 for a two-hour video). Each video also has a time
  limit, so one long video can't use up a whole night.
- When there is a big backlog (25 or more videos waiting), each video first
  gets a quick pass with about a quarter of the frames, so everything is
  covered sooner. A later pass adds the rest.
- **Manual**: an admin sets one cap, from 3 to 1000 frames a video, which
  replaces the automatic limit.
- What is said in a video is transcribed for up to two hours whatever the
  setting.

## Finding things

- **Photos page** (Media menu): every photo and video, newest first, with a
  search box. Open one to see what it might show, its tags, the text found
  in it and its date. A video found by a moment opens at that moment.
- **Details** (the dashboard; the phone apps later): tap a photo, or a
  video's **Info** button on its tile or in the player, for everything
  Domovoi worked out about the file: its name, folder, size and date, the
  place (with locations on), the people in it (with faces on; never on a
  shared screen, and a hidden group's faces only for an admin), whether it
  has been looked at, when, with which vision model and any error code,
  and for a video what its frames might show, what is said in it (with
  times) and its summary. A time plays the video from there.
- **Videos page**: its search also looks inside videos.
- **Words, times and places** can be mixed: "dog at the beach last summer",
  "Christmas 2021", "photos from Portland", "at home". A month, season or
  holiday with no year means every year's. A file that hasn't been looked
  at yet is still found by its file and folder names.
- **By voice**: "show me photos of the beach from last summer", "find the
  video where we sang happy birthday". Domovoi says how many it found and
  that they are on the Photos page. When it recognizes your voice and a
  phone is marked as yours (Settings → Household → Phones & browsers, "This
  phone belongs to"), it asks "Do you want them on your phone?"; say yes and the
  results arrive on your phone as a chat, "Photos: …", with the same cards
  as a chat search. It never asks a guest or a voice it doesn't know, and
  sends only to the asker's own phones (a few times an hour at most).
- **In a chat** (home server): the same questions, answered with cards: a
  thumbnail, "Might show: ...", the date and, for a video, a time badge.
- **On a room's screen**: asked in a room with a screen (a phone or tablet
  in room mode, or a video satellite), the results show there for 90
  seconds. A room without a screen gets nothing new. A recognized voice's
  "photos of Sarah" shows there too, to whoever is in the room.
- With understanding off, Domovoi says photo understanding is off.

## Faces

Faces are the most sensitive thing here, so they are **off by default**
and only an admin can turn on **Recognize faces**. Turning it on downloads
two small face models once (about 39 MB), then goes back over every photo
and video already looked at.

- **Who can do what.** Guests, and any device marked as a shared screen,
  see nothing: no Faces page and no names. A paired phone or browser sees
  the Faces page, names a group, says "yes, this is Sarah" or "not this
  person", hides a stranger's group, and merges or splits groups nobody is
  named in. An admin also merges or splits a named person's faces, moves a
  group or a face that is someone's to someone else, works with hidden
  groups and owns every switch.
- **Strangers.** Faces nobody names stay in unnamed groups. Groups of one or
  two faces stay behind "show more", and Domovoi never asks who someone is.
- **A name searches people.** With faces on, "photos of Sarah" works, by
  voice only for a voice Domovoi recognizes (or a phone's chat mic), never
  for a guest and never on a shared screen.
- **Forget my face.** On your own Faces page, from the phone or browser
  that has been yours ("this is me") since before your faces were named,
  or by an admin: every saved face of that person is deleted and one
  pattern tied to nobody is kept, so new photos of them aren't grouped
  again. Chat replies that showed their photos lose those results. The
  photos, their voice and their memories stay. An admin can see the
  forgotten faces and undo one.
- **Deleting a person** makes their faces unnamed; they are grouped again
  that night.
- **The kept forget list.** Turning faces off deletes every face, group and
  face pattern, **but keeps the forget list**. It is tied to nobody, and it
  is what makes a forget last: without it, turning faces on again would
  learn a forgotten person again from the same photos. The confirmation
  says so.
- **Photos the face step skips.** HEIC, HEIF and TIFF photos (many iPhone
  photos among them), and photos over 80 MB, are not looked at for faces.
  Faces are re-grouped each night, a capped number at a time, and the work
  pauses whenever someone speaks.
- Domovoi never exports face data; it is only in a database backup.

## Places

Where a photo was taken says where you live and travel, so places are **off
by default** and an admin turns on **Use photo locations**.

- Turning it on downloads a list of towns over 1,000 people once, about
  11 MB (GeoNames, credited on the settings card). Nothing is sent to an
  online map: the town is named on your server. With internet access set to
  "No", the list is not downloaded and places stay unnamed.
- The photo's position and town are kept, and searches like "photos from
  Portland" work. Answers carry the town's name, never coordinates. Videos
  keep no location. A shared screen gets no places.
- **"At home"** means within 10 km of the Home location in Settings →
  Household. That is a town, not your address.

## What turning each switch off deletes

| Switch | What it deletes |
|---|---|
| Understand photos and videos | Nothing. Domovoi stops looking, and the Photos page, voice and chat say it is off. |
| Recognize faces | Every face, group and face pattern (it asks first). The forget list is kept. |
| Use photo locations | Every saved position and town name (it asks first). The downloaded town list stays: it is public data. |
| Exclude a library or folder | What was worked out about it: descriptions, tags, text, what is said, summaries, faces and places (it asks first). Removing the exclusion looks at it again. |

Deleting a photo or video through Files deletes what was worked out about
it at once. A file removed from a library outside Files is first marked
missing; its results, and the faces people named in it, go after three
hourly looks and a day (two weeks when a quarter or more of a library
vanishes at once). A drive that isn't plugged in, or whose folder is
empty because it isn't mounted yet, keeps everything in case it comes
back, and so does a folder Domovoi can't read. A file moved to another
library keeps its results.

## Honest about mistakes

- **"Might show" can be wrong.** A small model writes the descriptions: it
  misnames breeds and objects and sometimes sees things that aren't there.
  Tags are help for searching, not facts. Text found in a picture is read by
  a different program and is exact when it is clear.
- **What you search for isn't logged.** The words of a search, typed or
  said, never go into Domovoi's logs. A spoken request is kept in the voice
  history like every other.
- **Faces can be wrong.** A suggested face is only a suggestion until
  someone confirms it. Babies change quickly and are the hardest. Say "not
  this person" and it is never suggested for them again.
- Everything a model or the text reader makes is shown as plain text, and a
  later chat answer is told never to obey it.
