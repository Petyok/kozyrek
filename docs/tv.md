# TV remote

Drives a CVTE / MAXHUB "ScreenSharePro" TV, the receiver behind the **MHShare** phone app
(Avahi service `_maxhubmobile._tcp`). Tested on firmware `RC.3.4.845.132`.

- `Tv.qml`: the overlay (`ipc call tv toggle`). A touchpad that shows the TV's screen live (see "The TV's picture in the overlay")
  (touch where you want the cursor, tap to click, mouse wheel = d-pad Up/Down which scrolls pages, right click = Back),
  d-pad, Back/Home/Menu, volume (hold to repeat), "Mirror screen" / "Mirror a window" (below), the YouTube link field, the TV's installed apps (click or wheel through the strip to launch), ✕ to close. While the panel has keyboard focus (click it):
  arrows, Enter = OK, Backspace = Back, `+`/`-` volume, `m` mute, `h` home, Esc closes.
  The text field under the pad types into the TV's focused text field (click it to focus; the TV field mirrors it, Enter presses ENTER on the TV).
  Drag the header to move it (not remembered across reloads). The bar's TV button toggles it.
- `tv/tvd.py`: child of the bar, serves a line protocol on `$XDG_RUNTIME_DIR/kozyrek-tv.sock`.
  The TV link is opened by the first command and dropped after 2 min idle (the TV shows
  "connection established" while a device is linked).
- `tv/tvctl`: `tvctl volup`, `tvctl key down down ok`, `tvctl text "hello"`, `tvctl apps`,
  `tvctl status`. Bind it or `qs -c kozyrek ipc call tv key volup` in hyprland.conf.

The TV is found over mDNS; pin it with `~/.config/kozyrek/tv.json` = `{"ip": "192.168.x.y"}`.

## The TV's picture in the overlay

While the panel is open the touchpad shows the TV's own screen live (about 5 fps while it changes, ~0.2 s behind), so the pad is a real
mini-TV: tap a tile on it and the TV clicks there, type in the text field and watch it appear. It starts when the panel opens and stops
when it closes (`tvctl view on|off|status`). Playing video shows too.

- How: `tv/view.py` runs the TV's own `screenrecord` (Android's built-in recorder, H.264, 640x360) through `adb exec-out`, decodes it with
  ffmpeg and rewrites `$XDG_RUNTIME_DIR/kozyrek-tv.jpg`; `Tv.qml` shows that file (two images take turns so a reload never blanks it).
  Needs `adb` and ADB enabled on the TV, like `tvctl yt`.
- Cost: the TV has no hardware encoder, so it runs a software encode: `screenrecord` is ~13 MB and ~10% of one core of four, and while it runs
  the TV has little free RAM (~190 MB available, of 915). The laptop side is ~9% of a core. It stops when the panel closes.
- Pitfalls found (all in `view.py`): the TV's vendor library prints two log lines into the stream (dropped); `screenrecord` only emits a frame
  when the picture changes, so the newest frame waits in ffmpeg's parser (an access-unit delimiter closes it after 120 ms of quiet);
  ffmpeg's probing swallows the first keyframe (it is sent twice, before any P frame, otherwise the picture is solid green); decoder and
  encoder frame threads add frames of delay (`-threads 1`); raw H.264 has no timestamps (`setpts=N*48000` gives the JPEG encoder strictly
  increasing ones, otherwise ffmpeg exits). `screenrecord` stops after 180 s and is restarted back to back.
- Limits of the TV's encoder: it delivers about 25 fps at 640x360 and about 20 fps at 960x540 while a video plays (it is the TV's CPU, shared with the playback), and it ignores `--bit-rate` (identical keyframes at 1.5 and 12 Mbit/s), so quality is fixed and raising the bit rate does not help. A quiet spell can fall inside a frame when the TV is busy (2 of 30 idle flushes during playback, 0 of 26 on a paused screen); that frame is then cut, its tail concealed and later frames drift (blocky strips, usually low in the picture) until the next keyframe, so `view.py` notices the cut and restarts the capture, which starts with a keyframe. The overlay reloads the picture file 25 times a second.

## Protocol (reverse-engineered from a PCAPdroid capture of the Android app)

| channel | what |
|---|---|
| TCP :7382 | frames: 64-byte header (`seq u32, len u32, 0, ms u32, 5, ~seq u32, 40 x 0`) + JSON. Handshake = the captured one, replayed from `tv/handshake.json` (the TV does not verify the signatures, and answers with a fresh key each time). |
| keys | JSON `{"content":{"message":"{\"mId\":CODE,\"mType\":1000,...}","serviceId":"event_notify.r"},"id":202}`; `mId` is the Android keycode (19/20/21/22 d-pad, 23 OK, 4 back, 3 home, 24/25 volume). |
| UDP :7387 | heartbeat, 16 bytes; echo what the TV sends and send our own every 0.5 s, otherwise the session dies in about 13 s. |
| UDP :7386 | touch: `02fd 0018 00000000 00000001 X16 Y16 STATE ...`, sent from local port 7388. X/Y are 0..65535; state 0 down, 1 move, 2 up. The TV moves its cursor by the finger's **movement** (delta 1.0 = the whole screen width / height, gain measured 1.00 within +-1 px, no acceleration seen), **not** to where it touches: a new touch continues from wherever the cursor was left. `tvd` therefore places the cursor absolutely on every press: one gesture that goes down at (1,1), moves to (0,0) (the cursor clamps in the corner from any start) and on to (X,Y) with 8 ms between packets; the cursor then lands within 1 px of the pad position (tested on 6 targets and after a drag). Sent back to back (no gap) some packets are lost and the cursor lands short. |
| HTTP :7433 | `POST /app/list type=0` (app list, icons at `/<icon>.png`), `POST /text/upload {"text": ...}` types into the focused field, `POST /app/open packname=<package>` (lowercase field name, form-encoded) launches an app. |

## Mirroring the laptop screen to the TV

"Mirror my screen" in the overlay (or `tvctl mirror on|off`) starts `tv/mirror.py`, which makes the TV show the laptop screen.
It does not use the MHShare protocol: the TV also runs a Google Cast "Chrome Mirroring" receiver (app `0F5096E8`, TLS :8009),
and `mirror.py` is a small Cast Streaming sender for it (`wf-recorder` -> H.264 -> Cast RTP over UDP).

- Chromium's own Cast button cannot be used: the TV answers Cast device authentication with SHA-1 and current Chromium rejects it
  (`chrome://media-router-internals` logs `AUTHENTICATION`), so the TV is never listed. A direct sender does not check.
- Control: CASTV2 on :8009, `LAUNCH 0F5096E8`, then `OFFER` on `urn:x-cast:com.google.cast.webrtc`. The offer **must** contain both an
  `audio_source` and a `video_source` stream and `rtpExtensions`, or the TV's receiver throws a NullPointerException. The `ANSWER` gives the UDP port (12345) and `ssrc+1`.
- Media: Cast RTP (openscreen `cast/streaming`), AES-128-CTR per frame, NACK retransmission from a 128-frame buffer, a sender report every second.
  Audio: `ffmpeg` records the monitor of the default sink (what the laptop plays; it keeps playing locally too), encodes 20 ms Opus packets and sends one per frame, payload type 127.
  Written and checked offline only (Ogg parsing, framing, NACK routing); not yet confirmed on the TV.
- The TV's decoder (`OMX.MS.AVC.Decoder`) showed a flat grey for multi-slice level 6.2 streams (what `libx264` wrote for wf-recorder).
  The stream is therefore one-slice Constrained Baseline level 4.0, 1280x800, constant quality (VAAPI `qp=23`; x264 fallback at 3 Mbit/s).
- `wf-recorder` only emits frames when the screen changes; the first IDR must land after the TV has opened its player (the sender waits 2 s).
- The capture runs as `kozyrek-mirror-rec` (a symlink in `$XDG_RUNTIME_DIR`), not `wf-recorder`, so the recording pill and the Ctrl+Shift+4 script ignore it.
- Needs `python-cryptography` and `wf-recorder`. A TV app that was just restarted answers the first Cast messages slowly (up to ~10 s).
- "Mirror a window" (`tvctl mirror window`, experimental): the compositor's own picker (xdph) chooses one window, which is captured through the
  screencast portal and PipeWire (`gst-launch-1.0 pipewiresrc`) and encoded like the screen. The first version froze when the page changed
  (the portal ran "Out of buffers"); `always-copy` plus a 30 fps pacing thread was written against that and has run offline without freezing,
  but it has not been confirmed on the TV.

## YouTube on the TV without sending video

The "YouTube link" field in the overlay (or `tvctl yt <link|id>`) opens a video or playlist in the TV's own YouTube app, which streams it
from YouTube. The laptop only sends an `adb shell am start -f 0x10008000 -a android.intent.action.VIEW -p com.google.android.youtube.tv -d <url>`
(the flags make the app start fresh on the intent, so repeating a link navigates instead of only raising the app), so an 11-hour playlist
costs nothing. Needs `adb` on the laptop and ADB enabled on the TV (factory menu -> DEBUG -> ADB ENABLE).

- Accepted: a bare video id (11 characters), a bare playlist id (`PL…`, `OLAK5uy_…`, `UU…`, `RD…`, `FL…`, `WL`, `LL`), and youtube.com /
  music.youtube.com / youtu.be links (watch, playlist, shorts, live, embed, `embed/videoseries`). A video with a bad `list=` plays the video.
  Anything else is rejected before it reaches the TV, and only the matched ids are put into the intent (`tv/yt.py`).
- `tvd` runs it off the command loop (adb can take seconds) and answers `yt ok` / `yt err <reason>`; the overlay keeps that apart from the
  TV link's status, keeps the text in the field until the TV accepted it, and shows the reason under the field.
- The app is force-stopped before every launch (`am force-stop`): its native Cobalt engine cannot start a second time inside a live process, so a second link opened over a running video left the TV black (same pid, "Looking for library libcobalt.so" again, no picture). The cost is a cold start, about 2 s.
- If the TV does not claim the https link, a plain video falls back to `vnd.youtube:<id>` (playlists do not).

## Not known

- Clicks: touch only moves the TV's cursor (and swipes scroll). The phone app clicks with ENTER (key 66) after the cursor is placed, so `tvd` sends 66 100 ms after a tap (a touch that moved under ~1.2% of the pad). The TV then injects a touchscreen DOWN/UP at the cursor (seen in `dumpsys input`), so apps that ignore touch ignore the click too.
- The cursor is a 20x20 window of `com.maxhub.screenshare.server.tv`; `adb shell dumpsys window windows` shows its `mFrame` (centre = hot spot). That is how the cursor numbers in the protocol table were measured.
- Anything else the phone app does that was not in the capture (file transfer). Screen mirroring is solved a different way, see below.
- The signature in the handshake is replayed, not computed: a firmware that starts checking it breaks the link.
  The handshake introduces the laptop as device `kozyrek` with a fixed `uuid`; the captured phone's own were replaced.
