"""yt: play a YouTube video or playlist on the TV's own YouTube app, straight from YouTube's servers.

Nothing is transferred from the laptop: the laptop only tells the TV what to open (a VIEW intent for the YouTube TV app over
adb), so an 11-hour playlist costs the same as a 1-minute clip. Needs `adb` and ADB enabled on the TV (factory menu -> DEBUG ->
ADB ENABLE; see docs/tv.md). The TV's adbd accepts the laptop without a prompt.
"""
import re, shlex, subprocess, urllib.parse

PKG = 'com.google.android.youtube.tv'
ID = re.compile(r'[A-Za-z0-9_-]{11}')
LIST = re.compile(r'[A-Za-z0-9_-]{13,64}|WL|LL')                # PL…, OLAK5uy_…, UU…, RD… (mixes), FL…; Watch Later and Liked are two letters
BARE_LIST = re.compile(r'(?:PL|OLAK5uy_|UU|RD|FL)[A-Za-z0-9_-]{11,62}|WL|LL')   # a pasted bare word must look like a playlist id
HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be'}
NEW_TASK_CLEAR_TASK = '0x10008000'      # always navigate: an intent equal to the one already at the app's root only raises it


def parse(s):
    """User input -> (video id | None, playlist id | None). Accepts a bare 11-character id, a bare playlist id, and
    youtube.com / music.youtube.com / youtu.be links (watch, playlist, shorts, live, embed, v), with or without the scheme.
    A link with a good video and a bad `list=` plays the video."""
    s = s.strip()
    if ID.fullmatch(s): return s, None
    if BARE_LIST.fullmatch(s): return None, s
    u = urllib.parse.urlparse(s if '://' in s else 'https://' + s)
    host = (u.hostname or '').lower()
    if host not in HOSTS: raise ValueError('not a YouTube link: %r' % s[:60])
    q = urllib.parse.parse_qs(u.query)
    parts = [p for p in u.path.split('/') if p]
    v = (q.get('v') or [None])[0]
    if host.endswith('youtu.be'): v = parts[0] if parts else None
    elif len(parts) >= 2 and parts[0] in ('shorts', 'live', 'embed', 'v') and parts[1] != 'videoseries': v = parts[1]
    l = (q.get('list') or [None])[0]
    if l is not None and not LIST.fullmatch(l): l = None
    if v is not None and not ID.fullmatch(v):
        if l is None: raise ValueError('bad video id: %r' % v[:20])
        v = None                                                # youtu.be/playlist?list=… and the like
    if v is None and l is None: raise ValueError('no video or playlist in: %r' % s[:60])
    return v, l


def url(video, playlist):
    if video is None: return 'https://www.youtube.com/playlist?list=' + playlist
    return 'https://www.youtube.com/watch?v=' + video + ('&list=' + playlist if playlist else '')


def adb(ip, *args, timeout=6):
    return subprocess.run(['adb', '-s', ip + ':5555', *args], capture_output=True, text=True, timeout=timeout)


def play(ip, text):
    """Open the video / playlist in the TV's YouTube app. Returns the URL; raises RuntimeError (ValueError for a bad link)."""
    video, playlist = parse(text)
    link = url(video, playlist)
    out = ''
    try:
        subprocess.run(['adb', 'connect', ip + ':5555'], capture_output=True, text=True, timeout=4)    # no-op when connected
        adb(ip, 'shell', 'am force-stop ' + PKG)   # Cobalt cannot start a second time inside a live process: the new activity stays black
        for data in (link, 'vnd.youtube:' + video) if video and not playlist else (link,):   # the vnd link only if the https one is unclaimed; it has no playlist
            r = adb(ip, 'shell', 'am start -f %s -a android.intent.action.VIEW -p %s -d %s' % (NEW_TASK_CLEAR_TASK, PKG, shlex.quote(data)))
            out = r.stdout + r.stderr
            if r.returncode == 0 and not re.search(r'^(Error|error|adb:)', out, re.M): return link
    except FileNotFoundError: raise RuntimeError('adb is not installed on the laptop')
    except subprocess.TimeoutExpired: raise RuntimeError('the TV does not answer on adb :5555 (ADB ENABLE in the factory menu?)')
    raise RuntimeError('adb said: ' + (out.strip().splitlines() or ['no reply'])[-1][:120])
