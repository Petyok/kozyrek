#!/usr/bin/env python3
"""tvd: the link between kozyrek and a CVTE / MHShare TV (the protocol the MHShare phone app speaks).

Runs as a child of the bar (EOF on stdin = the bar is gone = exit). Serves a line protocol on a
Unix socket; the TV link itself is opened on the first command and dropped after IDLE seconds,
because the TV shows "connection established" and counts us as a connected device meanwhile.

  key NAME [NAME ...]    Android keycodes by name or number          -> ok
  touch d|m|u X Y        touchscreen event, X/Y 0..1 of the screen   -> (no reply, 60 Hz traffic)
  text STRING            type into the TV's focused text field       -> ok
  apps                   installed apps, JSON                        -> {"base": ..., "apps": [...]}
  launch PACKAGE         open an app (POST /app/open packname=)      -> ok
  yt LINK|ID             YouTube video / playlist on the TV's YouTube app (adb VIEW intent, see yt.py) -> yt ok | yt err REASON
  connect | ping         open the link / keep it alive               -> ok
  view on [WxH]|off|status   the TV's screen as a picture in $XDG_RUNTIME_DIR/kozyrek-tv.jpg (spawns view.py) -> view on | view off
  mirror on|window|off|status   laptop screen / one picked window on the TV (spawns mirror.py) -> mirror on | mirror off
  status                 -> {"connected": bool, "ip": str|null, "mirror": bool, "view": bool}

Wire format (captured from the MHShare Android app, TV firmware RC.3.4.845.132), all replayed, none
re-derived: TCP :7382 frames = 64-byte header + JSON; the handshake is the captured one (the TV does not
check the signatures); keys are JSON "mId" = Android keycode; touches are UDP 24-byte packets to :7386 (the TV moves its cursor by the finger's movement, so tvd homes it to a corner first, see touch_event)
from port 7388; UDP :7387 heartbeats are echoed, without them the TV drops the session in ~13 s.
"""
import asyncio, ipaddress, json, os, socket, struct, subprocess, sys, time, urllib.request
import yt

HERE = os.path.dirname(os.path.realpath(__file__))
SOCK = os.environ.get('KOZYREK_TV_SOCK') or os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'kozyrek-tv.sock')
CONF = os.path.expanduser('~/.config/kozyrek/tv.json')     # optional {"ip": "192.168.x.y"}
IDLE = 120
KEYS = dict(up=19, down=20, left=21, right=22, ok=23, enter=66, back=4, home=3, menu=82,
            volup=24, voldown=25, mute=164, power=26, play=85, pause=127, next=87, prev=88,
            ff=90, rew=89, chup=166, chdown=167)
HANDSHAKE = json.load(open(os.path.join(HERE, 'handshake.json')))
TOUCH_TAIL = {'d': b'\x00\x00\x00\x01\x00\x01\xff\xff', 'm': b'\x01\x00\x00\x01\x00\x01\xff\xff', 'u': b'\x02' + b'\0' * 7}


def ipv4(s):
    try: return ipaddress.IPv4Address(s) is not None
    except ValueError: return False


def find_ip():
    try:
        ip = json.load(open(CONF)).get('ip')
        if ip: return ip
    except (OSError, ValueError): pass
    try:
        out = subprocess.run(['avahi-browse', '-rtpk', '_maxhubmobile._tcp'], capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.TimeoutExpired): return None
    for line in out.splitlines():
        f = line.split(';')
        if f[0] == '=' and len(f) > 7 and ipv4(f[7]): return f[7]    # the TV's A record can arrive on its IPv6 row (seen 2026-10-19)
    return None


def http(ip, path, data, ctype):
    r = urllib.request.Request(f'http://{ip}:7433{path}', data, {'Content-Type': ctype})
    return urllib.request.urlopen(r, timeout=4).read().decode()


class Echo(asyncio.DatagramProtocol):
    """Heartbeat socket: whatever the TV sends on :7387 goes straight back."""
    def __init__(self, ip): self.ip = ip
    def connection_made(self, t): self.t = t
    def datagram_received(self, data, addr): self.t.sendto(data, (self.ip, 7387))


def udp(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', port)); s.setblocking(False)
    return s


class Link:
    def __init__(self):
        self.ip = None; self.up = False; self.last = time.monotonic()
        self.lock = asyncio.Lock(); self.tasks = []; self.w = None; self.seq = 0
        self.start = (0, 0); self.moved = False; self.pressed = False

    async def ensure(self):
        self.last = time.monotonic()
        if self.up: return
        async with self.lock:
            if self.up: return
            self.ip = self.ip or await asyncio.to_thread(find_ip)
            if not self.ip: raise RuntimeError('TV not found (no _maxhubmobile._tcp on the LAN; set ip in ~/.config/kozyrek/tv.json)')
            try:
                r, self.w = await asyncio.wait_for(asyncio.open_connection(self.ip, 7382), 3)
            except (OSError, asyncio.TimeoutError):
                self.ip = None; raise RuntimeError('TV is not answering (off, or the IP changed)')
            self.seq = 0
            loop = asyncio.get_running_loop()
            self.tasks = [asyncio.create_task(self._drain(r))]
            for body in HANDSHAKE:
                self.send(body); await asyncio.sleep(0.15)
            hb, self.touch_sock = udp(7386), udp(7388)
            self.hb_t, _ = await loop.create_datagram_endpoint(lambda: Echo(self.ip), sock=hb)
            self.touch_t, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, sock=self.touch_sock)
            self.tasks.append(asyncio.create_task(self._beat()))
            self.up = True
            print('tvd: connected', self.ip, file=sys.stderr, flush=True)

    async def _drain(self, r):
        try:
            while await r.read(65536): pass
        except OSError: pass
        self.close('TV closed the connection')

    async def _beat(self):
        n = 0
        while True:
            n += 1
            self.hb_t.sendto(struct.pack('>QiB', n, -n - 1, 5) + b'\0\0\0', (self.ip, 7387))
            await asyncio.sleep(0.5)

    def close(self, why='idle'):
        if not self.up and not self.tasks: return
        self.up = False
        for t in self.tasks: t.cancel()
        self.tasks = []
        for t in ('hb_t', 'touch_t'):
            if hasattr(self, t): getattr(self, t).close()
        if self.w: self.w.close()
        print('tvd: disconnected:', why, file=sys.stderr, flush=True)

    def send(self, body):
        self.seq += 1
        b = body.encode()
        self.w.write(struct.pack('>IIIIII', self.seq, len(b), 0, int(time.time() * 1000) & 0xffffffff, 5, (-self.seq - 1) & 0xffffffff) + b'\0' * 40 + b)

    async def key(self, code):
        await self.ensure()
        msg = json.dumps({"mBody": "", "mId": code, "mType": 1000, "mVersion": 0}, separators=(',', ':'))
        self.send(json.dumps({"content": {"message": msg, "serviceId": "event_notify.r"}, "id": 202}, separators=(',', ':')))

    def touch_event(self, st, x, y):
        self.last = time.monotonic()
        if st == 'd':
            self.start, self.moved, self.pressed = (x, y), False, True
            # The TV moves its cursor by how far the finger MOVES (1.0 = the whole screen width / height, measured to +-1 px),
            # not to where it touches, so a new touch would continue from wherever the cursor was left. Fix the origin: one
            # gesture that drags to the top-left corner (the cursor clamps there from any start), then on to the target.
            # Packets 8 ms apart: sent back to back the TV drops some and the cursor lands short.
            self._send_touch('d', 1, 1); time.sleep(0.008)
            self._send_touch('m', 0, 0); time.sleep(0.008)
            self._send_touch('m', x, y)
            return
        elif st == 'm' and max(abs(x - self.start[0]), abs(y - self.start[1])) > 0.012: self.moved = True
        self._send_touch(st, x, y)
        if st == 'u':
            # Touch only moves the TV's cursor (and swipes scroll); the phone app clicks with ENTER.
            # Short delay: the cursor has to be at the new place first.
            if self.pressed and not self.moved:
                asyncio.get_running_loop().call_later(0.1, lambda: asyncio.create_task(self.key(66)))
            self.pressed = False

    def _send_touch(self, st, x, y):
        if not self.up: return
        x, y = (max(0, min(65535, int(v * 65535))) for v in (x, y))
        self.touch_t.sendto(b'\x02\xfd\x00\x18\0\0\0\x00\0\0\0\x01' + struct.pack('>HH', x, y) + TOUCH_TAIL[st], (self.ip, 7386))

    async def watchdog(self):
        while True:
            await asyncio.sleep(5)
            if self.up and time.monotonic() - self.last > IDLE: self.close('idle')


link = Link()
mirror = None                                   # the mirror.py child while the screen is on the TV
viewer = None                                   # the view.py child while the TV's picture is shown in the overlay
viewer_size = None                              # the size view.py was started at
VIEW_SIZES = {'640x360': '1500000', '960x540': '2500000'}   # TV capture size -> bit rate; the larger one costs the TV's software encoder more
VIEW_JPG = os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'kozyrek-tv.jpg')


def mirroring(): return mirror is not None and mirror.poll() is None


def viewing(): return viewer is not None and viewer.poll() is None


async def stop_viewer(keep_picture=False):
    viewer.terminate()
    try: await asyncio.to_thread(viewer.wait, 5)
    except subprocess.TimeoutExpired: viewer.kill()
    if not keep_picture:
        try: os.unlink(VIEW_JPG)
        except OSError: pass


async def run(cmd, arg):
    if cmd == 'key':
        for k in arg.split():
            await link.key(KEYS[k] if k in KEYS else int(k)); await asyncio.sleep(0.08)
        return 'ok'
    if cmd == 'text':
        await link.ensure()
        await asyncio.to_thread(http, link.ip, '/text/upload', json.dumps({'text': arg}).encode(), 'application/json; charset=utf-8')
        return 'ok'
    if cmd == 'apps':
        await link.ensure()
        d = json.loads(await asyncio.to_thread(http, link.ip, '/app/list', b'type=0', 'application/x-www-form-urlencoded'))
        return json.dumps({'base': f'http://{link.ip}:7433', 'apps': json.loads(d['data']['des'])}, ensure_ascii=False)
    if cmd == 'launch':
        await link.ensure()
        await asyncio.to_thread(http, link.ip, '/app/open', b'packname=' + arg.encode(), 'application/x-www-form-urlencoded')
        return 'ok'
    if cmd in ('connect', 'ping'):
        await link.ensure(); return 'ok'
    if cmd == 'mirror':                        # the laptop screen (or one window) on the TV; mirror.py streams until stopped
        global mirror
        if arg in ('on', 'window') and not mirroring():
            ip = link.ip or await asyncio.to_thread(find_ip)
            if not ip: raise RuntimeError('TV not found')
            mirror = subprocess.Popen([sys.executable, os.path.join(HERE, 'mirror.py'), ip] + (['--window'] if arg == 'window' else []))
        elif arg == 'off' and mirroring():
            mirror.terminate()
            try: await asyncio.to_thread(mirror.wait, 8)
            except subprocess.TimeoutExpired: mirror.kill()
        return 'mirror ' + ('on' if mirroring() else 'off')
    if cmd == 'view':                          # the TV's screen as a picture in the overlay; view.py writes VIEW_JPG until stopped
        global viewer, viewer_size
        mode, _, size = arg.partition(' ')
        if mode == 'on':
            size = size.strip() or next(iter(VIEW_SIZES))
            if size not in VIEW_SIZES: raise ValueError('view size must be one of ' + ', '.join(VIEW_SIZES))
            if viewing() and size != viewer_size: await stop_viewer(keep_picture=True)    # new size: restart, the old picture stays meanwhile
            if not viewing():
                ip = link.ip or await asyncio.to_thread(find_ip)
                if not ip: raise RuntimeError('TV not found')
                viewer_size = size
                viewer = subprocess.Popen([sys.executable, os.path.join(HERE, 'view.py'), ip, VIEW_JPG],
                                          env=dict(os.environ, KOZYREK_VIEW_SIZE=size, KOZYREK_VIEW_BITRATE=VIEW_SIZES[size]))
        elif mode == 'off' and viewing():
            await stop_viewer()
        return 'view ' + ('on' if viewing() else 'off')
    if cmd == 'status':
        return json.dumps({'connected': link.up, 'ip': link.ip, 'mirror': mirroring(), 'view': viewing()})
    if cmd == 'disconnect':
        link.close('requested'); return 'ok'
    raise ValueError(f'unknown command {cmd!r}')


async def yt_play(w, arg):
    """`yt` runs off the client's command loop (adb can take seconds; the remote must keep answering) and replies `yt ok` / `yt err …`,
    which the overlay keeps apart from the TV link's own ok / err."""
    try:
        yt.parse(arg)                          # a bad link fails with its own message, before any TV lookup
        ip = link.ip or await asyncio.to_thread(find_ip)
        if not ip: raise RuntimeError('TV not found')
        await asyncio.to_thread(yt.play, ip, arg)
        reply = 'yt ok'
    except Exception as e:
        reply = 'yt err ' + ' '.join(str(e).split() or [type(e).__name__])
    try: w.write((reply + '\n').encode())
    except (OSError, RuntimeError): pass       # the client went away meanwhile


async def client(r, w):
    while True:
        try: line = await r.readline()
        except ValueError: break               # a line over the stream limit: drop the client, the overlay reconnects
        if not line: break
        cmd, _, arg = line.decode(errors='replace').rstrip('\n').partition(' ')
        try:
            if cmd == 'touch':                 # hot path: no reply, dropped while the link is coming up
                st, x, y = arg.split()
                if link.up: link.touch_event(st, float(x), float(y))
                else: asyncio.create_task(link.ensure()).add_done_callback(lambda t: t.exception())
                continue
            if cmd == 'yt':
                asyncio.create_task(yt_play(w, arg)); continue
            reply = await run(cmd, arg)
        except Exception as e:
            reply = 'err ' + (str(e) or type(e).__name__)
        w.write((reply + '\n').encode())
    w.close()


async def main():
    if os.path.exists(SOCK): os.unlink(SOCK)    # stale from a killed bar; a live older tvd loses its socket, which is fine
    server = await asyncio.start_unix_server(client, SOCK)
    os.chmod(SOCK, 0o600)
    asyncio.create_task(link.watchdog())
    loop = asyncio.get_running_loop()
    eof = loop.create_future()                  # the bar owns our stdin; its death = EOF = we go too
    r = asyncio.StreamReader(); await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(r), sys.stdin)
    async def watch():
        while await r.read(4096): pass
        eof.set_result(None)
    asyncio.create_task(watch())
    print('tvd: listening on', SOCK, file=sys.stderr, flush=True)
    await eof
    link.close('shutdown'); server.close()
    for p in (mirror, viewer):
        if p is not None and p.poll() is None: p.terminate()


if __name__ == '__main__':
    asyncio.run(main())
