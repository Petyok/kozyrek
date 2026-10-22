#!/usr/bin/env python3
"""mirror: show this laptop's screen on a CVTE ScreenSharePro TV (Google Cast "Chrome Mirroring" receiver).

    mirror.py TV_IP

Runs until SIGTERM, until the TV closes the Cast app, or until the capture dies. Started and stopped by tvd.

Why this exists instead of Chromium's own Cast button: the TV answers Cast device authentication with SHA-1
and current Chromium rejects that, so Chromium never lists the TV. Spoken directly, the TV accepts everything.

Control: CASTV2 (TLS :8009, protobuf framing, JSON payloads) launches app 0F5096E8 and swaps OFFER/ANSWER.
The offer MUST carry an audio_source and a video_source stream (the TV's receiver dereferences both and
throws a NullPointerException otherwise).
Media:   Cast RTP over UDP to the ANSWER's udpPort, RTCP on the same socket. Formats follow openscreen
         cast/streaming (rtp_packetizer.cc, frame_crypto.cc, sender_report_builder.cc, compound_rtcp_parser.cc).
Capture: wf-recorder (as `kozyrek-mirror-rec`) -> H.264 Annex B on a pipe, one access unit per AUD; ffmpeg records the default
         sink's monitor -> 20 ms Opus packets (Ogg pages parsed), one per Cast frame, every frame a key frame.
Needs python-cryptography (AES-128-CTR) and wf-recorder (h264_vaapi, or libx264 as the fallback).
"""
import json, os, shutil, signal, socket, ssl, struct, subprocess, sys, threading, time
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

APP = '0F5096E8'
NS_CONN, NS_HB, NS_RECV, NS_RTC = ('urn:x-cast:com.google.cast.' + n for n in ('tp.connection', 'tp.heartbeat', 'receiver', 'webrtc'))
VSSRC, ASSRC = 1001, 2001
MAX_PACKET = 1200
HDR = 12 + 7                            # RTP header + Cast header
PAY = MAX_PACKET - HDR
NTP_EPOCH = 2208988800
KEEP = 128                              # frames of packets kept for NACK retransmission
RES = (1280, 800)                       # sent size; the TV letterboxes it
DRI = '/dev/dri/renderD128'


def log(*a): print('mirror:', *a, file=sys.stderr, flush=True)


# ---- CASTV2 control channel -------------------------------------------------------------------------

def varint(n):
    out = b''
    while True:
        b = n & 0x7F; n >>= 7
        if n: out += bytes([b | 0x80])
        else: return out + bytes([b])


def field(n, s):
    s = s.encode() if isinstance(s, str) else s
    return varint(n << 3 | 2) + varint(len(s)) + s


def frame(src, dst, ns, payload):
    m = b'\x08\x00' + field(2, src) + field(3, dst) + field(4, ns) + b'\x28\x00' + field(6, json.dumps(payload))
    return struct.pack('>I', len(m)) + m


def parse(m):
    out, i = {}, 0
    while i < len(m):
        key, i = rd_varint(m, i); n, wt = key >> 3, key & 7
        if wt == 0: v, i = rd_varint(m, i)
        elif wt == 2:
            ln, i = rd_varint(m, i); v = m[i:i + ln]; i += ln
        else: raise ValueError('wire type %d' % wt)
        out[n] = v
    return out


def rd_varint(b, i):
    n = s = 0
    while True:
        c = b[i]; i += 1; n |= (c & 0x7F) << s; s += 7
        if not c & 0x80: return n, i


class Cast:
    def __init__(self, ip):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        self.s = ctx.wrap_socket(socket.create_connection((ip, 8009), 5))
        self.s.settimeout(None)             # the connect timeout would otherwise end the reader after 5 quiet seconds
        self.lock = threading.Lock(); self.rid = 0
        self.q = []; self.cv = threading.Condition(); self.closed = False
        threading.Thread(target=self._rx, daemon=True).start()
        threading.Thread(target=self._hb, daemon=True).start()

    def send(self, dst, ns, payload):
        with self.lock: self.s.sendall(frame('sender-0', dst, ns, payload))

    def _rx(self):
        try:
            while True:
                n = struct.unpack('>I', self._read(4))[0]
                m = parse(self._read(n))
                ns, body = m.get(4, b'').decode(), m.get(6, b'').decode()
                try: p = json.loads(body) if body else {}
                except ValueError: continue
                if ns == NS_HB and p.get('type') == 'PING': self.send(m[2].decode(), NS_HB, {'type': 'PONG'}); continue
                with self.cv: self.q.append((ns, p)); self.cv.notify_all()
        except Exception as e:
            log('control channel closed:', repr(e))
        with self.cv: self.closed = True; self.cv.notify_all()

    def _read(self, n):
        b = b''
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c: raise OSError('closed')
            b += c
        return b

    def _hb(self):
        while not self.closed:
            time.sleep(5)
            try: self.send('receiver-0', NS_HB, {'type': 'PING'})
            except OSError: return

    def wait(self, pred, timeout):
        end = time.time() + timeout
        with self.cv:
            while True:
                for i, (ns, p) in enumerate(self.q):
                    if pred(ns, p): del self.q[i]; return p
                if self.closed or time.time() > end: return None
                self.cv.wait(0.2)


def session(ip, vkey, vivm, akey, aivm):
    c = Cast(ip)
    c.send('receiver-0', NS_CONN, {'type': 'CONNECT'})
    c.send('receiver-0', NS_RECV, {'type': 'GET_STATUS', 'requestId': 1})
    c.wait(lambda ns, p: p.get('type') == 'RECEIVER_STATUS', 10)     # a freshly restarted TV app drops a LAUNCH that follows CONNECT too closely
    st = None
    for rid in (2, 3, 4):
        c.send('receiver-0', NS_RECV, {'type': 'LAUNCH', 'appId': APP, 'requestId': rid})
        st = c.wait(lambda ns, p: p.get('type') == 'RECEIVER_STATUS' and any(a.get('appId') == APP for a in p['status'].get('applications', [])), 5)
        if st: break
    if not st: raise RuntimeError('TV did not start the mirroring app')
    app = next(a for a in st['status']['applications'] if a['appId'] == APP)
    tid = app['transportId']
    c.send(tid, NS_CONN, {'type': 'CONNECT'})
    streams = [
        {'index': 0, 'type': 'video_source', 'codecName': 'h264', 'rtpProfile': 'cast', 'rtpPayloadType': 96, 'ssrc': VSSRC,
         'targetDelay': 400, 'aesKey': vkey.hex(), 'aesIvMask': vivm.hex(), 'timeBase': '1/90000', 'maxFrameRate': '30000/1000',
         'maxBitRate': 8000000, 'resolutions': [{'width': RES[0], 'height': RES[1]}], 'profile': 'main', 'level': '4.0',
         'protection': 'false', 'errorRecoveryMode': 'castv2', 'receiverRtcpEventLog': True, 'rtpExtensions': [],
         'codecParameter': '', 'channels': 1},
        {'index': 1, 'type': 'audio_source', 'codecName': 'opus', 'rtpProfile': 'cast', 'rtpPayloadType': 127, 'ssrc': ASSRC,
         'targetDelay': 400, 'aesKey': akey.hex(), 'aesIvMask': aivm.hex(), 'timeBase': '1/48000', 'bitRate': 128000,
         'channels': 2, 'receiverRtcpEventLog': True, 'rtpExtensions': [], 'codecParameter': ''}]
    c.send(tid, NS_RTC, {'type': 'OFFER', 'seqNum': 1, 'offer': {'castMode': 'mirroring', 'receiverGetStatus': True, 'supportedStreams': streams}})
    ans = c.wait(lambda ns, p: p.get('type') == 'ANSWER', 10)
    if not ans or ans.get('result') != 'ok': raise RuntimeError('TV refused the offer: %s' % ans)
    return c, tid, app['sessionId'], ans['answer']['udpPort']


# ---- Cast RTP ----------------------------------------------------------------------------------------

def ntp(t):
    t += NTP_EPOCH
    return int(t) << 32 | int(t % 1 * (1 << 32))


class Stream:
    """One Cast RTP stream: H.264 video (payload type 96) or Opus audio (127). Every audio frame is a key frame."""
    def __init__(self, sock, addr, ssrc, pt, key, ivm):
        self.sock, self.addr, self.ssrc, self.pt, self.key, self.ivm = sock, addr, ssrc, pt, key, ivm
        self.seq = int.from_bytes(os.urandom(2), 'big')
        self.fid = 0; self.ref = 0; self.pkts = self.octets = 0
        self.sent = {}                  # frame id (8 bit) -> [packet bytes]
        self.lock = threading.Lock()

    def encrypt(self, fid, data):
        nonce = bytearray(16); nonce[8:12] = struct.pack('>I', fid & 0xFFFFFFFF)
        e = Cipher(algorithms.AES(self.key), modes.CTR(bytes(a ^ b for a, b in zip(nonce, self.ivm)))).encryptor()
        return e.update(data) + e.finalize()

    def send_frame(self, data, key, rtp):
        fid = self.fid; ref = fid if key else self.ref
        enc = self.encrypt(fid, data)
        n = max(1, -(-len(enc) // PAY))
        pk = []
        with self.lock:
            for i in range(n):
                p = struct.pack('>BBHII', 0x80, (0x80 if i == n - 1 else 0) | self.pt, self.seq & 0xFFFF, rtp & 0xFFFFFFFF, self.ssrc)
                p += struct.pack('>BBHHB', (0x80 if key else 0) | 0x40, fid & 0xFF, i, n - 1, ref & 0xFF) + enc[i * PAY:(i + 1) * PAY]
                self.seq += 1; self.pkts += 1; self.octets += len(p) - HDR; pk.append(p)
            self.sent[fid & 0xFF] = pk
            self.sent.pop((fid - KEEP) & 0xFF, None)
            self.ref = fid; self.fid += 1
        for i, p in enumerate(pk):
            self.sock.sendto(p, self.addr)
            if i % 8 == 7: time.sleep(0.002)    # a keyframe is a burst of ~40 datagrams; the TV drops them unpaced

    def report(self, rtp):
        self.sock.sendto(struct.pack('>BBHIQIII', 0x80, 200, 6, self.ssrc, ntp(time.time()), rtp & 0xFFFFFFFF, self.pkts, self.octets), self.addr)

    def resend(self, f, pid, mask):
        with self.lock: pk = self.sent.get(f)
        if not pk: return
        for w in (range(len(pk)) if pid == 0xFFFF else [pid] + [pid + 1 + b for b in range(8) if mask >> b & 1]):
            if w < len(pk): self.sock.sendto(pk[w], self.addr)


def route_feedback(d, streams):
    """Receiver RTCP: Cast feedback (PT 206, FMT 15, 'CAST') names the stream by its SSRC and carries NACKs as (frame, packet, bitmask)."""
    i = 0
    while i + 4 <= len(d):
        ln = (struct.unpack('>H', d[i + 2:i + 4])[0] + 1) * 4
        pkt = d[i:i + ln]; i += ln
        if len(pkt) >= 20 and pkt[1] == 206 and pkt[0] & 0x1F == 15 and pkt[12:16] == b'CAST':
            s = streams.get(struct.unpack('>I', pkt[8:12])[0])
            for o in range(20, 20 + 4 * pkt[17], 4):
                if s: s.resend(pkt[o], struct.unpack('>H', pkt[o + 1:o + 3])[0], pkt[o + 3])


def access_units(f):
    """Yield (is_key, annexb) per access unit of an Annex B stream in which every unit starts with an AUD."""
    buf, cur, key = b'', [], False
    while True:
        d = f.read1(65536)
        if not d: return
        buf += d
        starts = []; i = buf.find(b'\x00\x00\x01')
        while i >= 0: starts.append(i); i = buf.find(b'\x00\x00\x01', i + 3)
        for a, b in zip(starts, starts[1:]):
            nal = buf[a + 3:b]; nal = nal[:-1] if nal.endswith(b'\x00') else nal
            t = nal[0] & 0x1F
            if t == 9:
                if cur: yield key, b''.join(b'\x00\x00\x00\x01' + x for x in cur)
                cur, key = [], False
            else:
                cur.append(nal); key |= t == 5
        if starts: buf = buf[starts[-1]:]


def encoder():
    """(vaapi, ffmpeg-style codec options): the GPU's H.264 encoder when there is one (the laptop CPU is weak), x264 otherwise.
    Both produce one single-slice Constrained Baseline level 4.0 stream with an AUD per frame, which is what this TV's decoder shows.
    (Multi-slice and level 6.2 streams decoded to a flat grey.)"""
    if os.path.exists(DRI) and os.environ.get('KOZYREK_MIRROR_ENC') != 'x264':
        return True, {'profile': 'constrained_baseline', 'level': '40',
                      'qp': '23',               # constant quality: text stays sharp, a still screen costs nothing
                      'g': '60', 'bf': '0', 'aud': '1', 'async_depth': '1'}
    return False, {'preset': 'ultrafast', 'tune': 'zerolatency', 'profile': 'baseline', 'level': '4.0', 'b': '3000000',
                   'maxrate': '4000000', 'bufsize': '1500000',
                   'x264-params': 'repeat-headers=1:aud=1:bframes=0:keyint=60:slices=1:sliced-threads=0:fps=30'}


def screen_cmd(exe):
    """The whole screen: wf-recorder captures and encodes (frames only when the screen changes)."""
    vaapi, o = encoder()
    a = ['-c', 'h264_vaapi', '-d', DRI, '-F', 'scale_vaapi=w=%d:h=%d:format=nv12' % RES] if vaapi else \
        ['-c', 'libx264', '-x', 'yuv420p', '-F', 'scale=%d:%d' % RES]
    return [exe] + a + [x for k, v in o.items() for x in ('-p', k + '=' + v)] + ['-m', 'h264', '-f', '/dev/stdout']


def pick_window():
    """Ask the screencast portal for one window (xdph shows its picker). Returns (pipewire fd, node id, close function).
    The session lives as long as this process keeps its D-Bus connection."""
    import gi
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    me = bus.get_unique_name()[1:].replace('.', '_')
    D, P = 'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop'
    ctx, n = GLib.MainContext.default(), [0]

    def request(method, sig, *args):
        n[0] += 1; tok = 'kozyrek%d_%d' % (os.getpid(), n[0]); got = []
        sub = bus.signal_subscribe(D, 'org.freedesktop.portal.Request', 'Response', '%s/request/%s/%s' % (P, me, tok), None,
                                   Gio.DBusSignalFlags.NONE, lambda *a: got.append(a[5].unpack()))
        opts = dict(args[-1], handle_token=GLib.Variant('s', tok))
        bus.call_sync(D, P, 'org.freedesktop.portal.ScreenCast', method, GLib.Variant(sig, args[:-1] + (opts,)), None, Gio.DBusCallFlags.NONE, -1, None)
        while not got: ctx.iteration(False); time.sleep(0.05)    # the picker blocks on the user; SIGTERM must still be able to end us
        bus.signal_unsubscribe(sub)
        if got[0][0] != 0: raise RuntimeError('window picker cancelled')
        return got[0][1]

    V = GLib.Variant
    sess = request('CreateSession', '(a{sv})', {'session_handle_token': V('s', 'kozyrek%d' % os.getpid())})['session_handle']
    request('SelectSources', '(oa{sv})', sess, {'types': V('u', 2), 'multiple': V('b', False), 'cursor_mode': V('u', 2)})
    streams = request('Start', '(osa{sv})', sess, '', {})['streams']
    node = streams[0][0]
    res, fds = bus.call_with_unix_fd_list_sync(D, P, 'org.freedesktop.portal.ScreenCast', 'OpenPipeWireRemote', V('(oa{sv})', (sess, {})),
                                               GLib.VariantType('(h)'), Gio.DBusCallFlags.NONE, -1, None, None)
    fd = fds.get(res.get_child_value(0).get_handle())
    close = lambda: bus.call_sync(D, sess, 'org.freedesktop.portal.Session', 'Close', None, None, Gio.DBusCallFlags.NONE, -1, None)
    return fd, node, close


def window_capture(fd, node):
    """PipeWire window frames -> NV12 1280x800 (letterboxed) -> ffmpeg encoder. Returns (the ffmpeg Popen whose stdout is the H.264
    stream, the gst Popen).
    The portal only sends a frame when the window changes and has a handful of buffers: `always-copy` hands each one back at once
    (a first version held them, the portal ran out and the picture froze the moment the page changed). The fixed 30 fps the encoder
    needs comes from a pacing thread that repeats the latest frame, which also keeps keyframes coming for a static window."""
    gst = subprocess.Popen(
        ['gst-launch-1.0', '-q', 'pipewiresrc', 'fd=%d' % fd, 'path=%d' % node, 'always-copy=true', '!',
         'videoconvert', '!', 'videoscale', 'add-borders=true', '!', 'video/x-raw,format=NV12,width=%d,height=%d,pixel-aspect-ratio=1/1' % RES, '!',
         'fdsink', 'fd=1', 'sync=false'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, pass_fds=[fd])
    os.close(fd)
    vaapi, o = encoder()
    enc = ['-vaapi_device', DRI, '-vf', 'format=nv12,hwupload', '-c:v', 'h264_vaapi'] if vaapi else ['-c:v', 'libx264']
    ff = subprocess.Popen(
        ['ffmpeg', '-loglevel', 'error', '-fflags', 'nobuffer', '-f', 'rawvideo', '-pix_fmt', 'nv12', '-s', '%dx%d' % RES,
         '-framerate', '30', '-i', 'pipe:0'] + enc + [x for k, v in o.items() for x in ('-' + k, v)] + ['-f', 'h264', 'pipe:1'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, latest = RES[0] * RES[1] * 3 // 2, [None]

    def read():
        while len(b := gst.stdout.read(size)) == size: latest[0] = b

    def feed():
        t = time.monotonic()
        while ff.poll() is None:
            t += 1 / 30; d = t - time.monotonic()
            if d > 0: time.sleep(d)
            else: t = time.monotonic()                  # behind: do not burst
            if latest[0] is not None:
                try: ff.stdin.write(latest[0]); ff.stdin.flush()
                except (OSError, ValueError): return
    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=feed, daemon=True).start()
    return ff, gst


def ogg_packets(f):
    """Yield the Opus packets of an Ogg stream (the OpusHead / OpusTags header packets are skipped)."""
    cur = b''
    while True:
        h = f.read(27)
        if len(h) < 27: return
        for lace in f.read(h[26]):
            cur += f.read(lace)
            if lace < 255:                          # a packet ends at the first lacing value below 255
                if not cur.startswith((b'OpusHead', b'OpusTags')): yield cur
                cur = b''


def audio_cmd():
    """ffmpeg: what the laptop plays (monitor of the default sink) -> 20 ms Opus packets, one per Ogg page."""
    try: sink = subprocess.run(['pactl', 'get-default-sink'], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError): sink = ''
    return ['ffmpeg', '-loglevel', 'error', '-fflags', 'nobuffer', '-f', 'pulse', '-fragment_size', '3840',
            '-i', sink + '.monitor' if sink else '@DEFAULT_MONITOR@', '-ac', '2', '-ar', '48000',
            '-c:a', 'libopus', '-b:a', '128k', '-application', 'lowdelay', '-frame_duration', '20',
            '-flush_packets', '1', '-page_duration', '20000', '-f', 'ogg', 'pipe:1']


def main(ip, window=False):
    if window: fd, node, close_portal = pick_window()    # first: a cancelled picker must not start the TV app
    vkey, vivm, akey, aivm = (os.urandom(16) for _ in range(4))
    c, tid, sid, port = session(ip, vkey, vivm, akey, aivm)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.bind(('0.0.0.0', 0))
    v = Stream(sock, (ip, port), VSSRC, 96, vkey, vivm)
    a = Stream(sock, (ip, port), ASSRC, 127, akey, aivm)
    streams = {VSSRC: v, ASSRC: a}
    time.sleep(2)                       # the TV opens its player after the ANSWER; an IDR that lands earlier is dropped and a static
                                        # screen never produces another (wf-recorder only emits on damage)
    if window:
        rec, extra = window_capture(fd, node)
    else:
        exe = os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'kozyrek-mirror-rec')
        try: os.symlink(shutil.which('wf-recorder'), exe)   # own process name: the recording pill and the Ctrl+Shift+4 script act on every "wf-recorder"
        except FileExistsError: pass
        rec, extra = subprocess.Popen(screen_cmd(exe), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL), None
    try: aud = subprocess.Popen(audio_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as e: aud = None; log('no audio:', e)
    t0 = time.time(); stop = threading.Event()
    ts = lambda hz: int((time.time() - t0) * hz)

    def halt(*_):
        stop.set(); rec.kill()
        if extra: extra.kill()
        if aud: aud.kill()

    def rx():
        while not stop.is_set():
            try: route_feedback(sock.recv(2000), streams)
            except OSError: return
    threading.Thread(target=rx, daemon=True).start()

    def watch():                                    # the TV closed the app, or the link died
        while not stop.is_set():
            if c.closed or c.wait(lambda ns, p: ns == NS_RECV and p.get('type') == 'RECEIVER_STATUS' and not any(
                    a_.get('appId') == APP for a_ in p['status'].get('applications', [])), 1) is not None: break
        halt()
    threading.Thread(target=watch, daemon=True).start()

    def sr():
        while not stop.wait(1):
            v.report(ts(90000))
            if aud: a.report(ts(48000))
    threading.Thread(target=sr, daemon=True).start()

    def audio():
        for pkt in ogg_packets(aud.stdout):
            if stop.is_set(): return
            a.send_frame(pkt, True, ts(48000))
    if aud: threading.Thread(target=audio, daemon=True).start()
    signal.signal(signal.SIGTERM, halt)
    log('streaming to', ip, port, '(with audio)' if aud else '(video only)')
    n = 0
    for key, au in access_units(rec.stdout):
        if n == 0: v.report(ts(90000))
        v.send_frame(au, key, ts(90000)); n += 1
    halt()
    try: c.send('receiver-0', NS_RECV, {'type': 'STOP', 'sessionId': sid, 'requestId': 99})
    except OSError: pass
    if window:
        try: close_portal()
        except Exception: pass
    log('stopped after', n, 'frames')


if __name__ == '__main__':
    main(sys.argv[1], '--window' in sys.argv[2:])
