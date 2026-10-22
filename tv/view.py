#!/usr/bin/env python3
"""view: the TV's screen as a live picture on the laptop.

    view.py TV_IP OUT.jpg

The TV's own `screenrecord` (Android's built-in screen recorder, H.264) is read over adb and decoded by ffmpeg into one JPEG
that is rewritten in place (atomically); the overlay shows it inside the touchpad. Runs until SIGTERM.

- `adb exec-out` keeps the stream binary-clean, except that the TV's vendor library prints two log lines into it first
  ("Init wrapper sys mutex ...", "open mma dev failed"); everything before the first start code is dropped.
- screenrecord only emits a frame when the picture changes, so the newest frame would sit unseen in ffmpeg's parser (it ends a
  frame only when the next one starts). The reader therefore forwards whole NAL units as they complete and, when the stream
  has been quiet for IDLE seconds, closes the last one with an access-unit delimiter. If a frame ever stalled mid-way for
  IDLE seconds this would corrupt that one frame until the next keyframe (screenrecord sends one every ~10 s).
- ffmpeg's stream probing swallows the first access unit, which holds the only keyframe for ~10 s; without it every later P frame
  decodes against nothing and no picture is ever output. That first access unit is therefore sent twice.
- screenrecord stops after 180 s (its maximum), so it is restarted back to back; every run begins with SPS/PPS and a keyframe.
- Cost on the TV: a software H.264 encode (no hardware encoder, 915 MB RAM), hence the small size: the picture is shown about
  300 px wide. Playing video is captured too, unlike screencap, which misses the video plane.
"""
import os, re, select, signal, subprocess, sys, time

SIZE = os.environ.get('KOZYREK_VIEW_SIZE', '640x360')
BITRATE = os.environ.get('KOZYREK_VIEW_BITRATE', '1500000')
IDLE = 0.12
SC4, AUD = b'\x00\x00\x00\x01', b'\x00\x00\x00\x01\x09\xf0'
IDR = re.compile(rb'\x00\x00\x01[\x25\x45\x65]')       # start code + NAL header of an IDR slice


def main(ip, out):
    ff = subprocess.Popen(
        ['ffmpeg', '-y', '-loglevel', 'error', '-fflags', 'nobuffer', '-flags', 'low_delay', '-probesize', '4096', '-analyzeduration', '0',
         '-threads', '1', '-f', 'h264', '-i', 'pipe:0', '-vf', 'setpts=N*48000,scale=%s,format=yuvj420p' % SIZE.replace('x', ':'), '-q:v', '4',
         '-fps_mode', 'passthrough', '-threads:v', '1', '-f', 'image2', '-update', '1', '-atomic_writing', '1', out],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    cur, stop = [None], [False]
    first = [b'', False]                                      # what was sent so far, and whether the keyframe was replayed

    def halt(*_):
        stop[0] = True
        for p in (cur[0], ff):
            if p and p.poll() is None: p.kill()

    def send(data):
        if first[1]:
            ff.stdin.write(data); ff.stdin.flush(); return
        # Until the first keyframe is complete nothing goes to ffmpeg. Then the keyframe access unit is sent twice (probing swallows
        # the first copy) and only after that whatever followed it, so the decoder never sees a P frame before its reference.
        first[0] += data
        m = IDR.search(first[0])
        if not m: return
        p = first[0].find(b'\x00\x00\x01', m.end())              # the NAL after the keyframe slice: the keyframe is complete
        if p >= 0:
            if first[0][p - 1] == 0: p -= 1
            au, rest = first[0][:p], first[0][p:]
        elif data.endswith(AUD): au, rest = first[0][:-len(AUD)], b''
        else: return
        first[0], first[1] = b'', True
        ff.stdin.write(au + AUD); ff.stdin.flush()
        time.sleep(0.3)                                           # probing is over by now
        ff.stdin.write(au + AUD + rest); ff.stdin.flush()
    signal.signal(signal.SIGTERM, halt)
    subprocess.run(['adb', 'connect', ip + ':5555'], capture_output=True, timeout=6)
    last_note, parent = 0, os.getppid()
    while not stop[0] and ff.poll() is None and os.getppid() == parent:      # tvd gone (killed): stop streaming from the TV
        t = time.time()
        cur[0] = rec = subprocess.Popen(
            ['adb', '-s', ip + ':5555', 'exec-out', 'screenrecord --output-format=h264 --size %s --bit-rate %s -' % (SIZE, BITRATE)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        fd, buf, started, held, cut, healed = rec.stdout.fileno(), b'', False, False, False, False
        try:
            while os.getppid() == parent:                     # tvd gone (killed): stop, even while frames keep flowing
                if not select.select([fd], [], [], IDLE)[0]:
                    if held and buf:                          # quiet: the last NAL is complete, close its access unit
                        send(buf + AUD); buf, held, cut = b'', False, True
                    continue
                d = os.read(fd, 65536)
                if not d: break
                if cut and not d.startswith((b'\x00\x00\x01', b'\x00\x00\x00\x01')):
                    # The quiet spell fell inside a NAL (the TV was busy): ffmpeg got a truncated frame, its tail is concealed and every
                    # later P frame drifts until the next keyframe (~10 s). Start a new run: it begins with a keyframe.
                    print('view: a frame was cut by the idle flush, restarting the capture', file=sys.stderr, flush=True)
                    healed = True
                    break
                cut = False
                buf += d
                if not started:                               # drop the vendor log lines before the first start code
                    i = buf.find(SC4)
                    if i < 0: continue
                    buf, started = buf[i:], True
                k = buf.rfind(b'\x00\x00\x01')                # everything before the last start code is complete
                if k > 0:
                    if buf[k - 1] == 0: k -= 1
                    send(buf[:k]); buf = buf[k:]
                held = True
        except (BrokenPipeError, ValueError, OSError): break
        if rec.poll() is None: rec.kill()                         # tvd gone or we are stopping; never wait out screenrecord's 180 s
        rec.wait()
        if time.time() - t < 3 and not healed:                               # failed at once (adb off, TV busy): back off instead of hammering the TV
            if time.time() - last_note > 30: print('view: screenrecord ended after %.1f s (rc %s)' % (time.time() - t, rec.returncode), file=sys.stderr, flush=True)
            last_note = time.time(); time.sleep(3)
    halt()
    # killing adb leaves the TV's screenrecord running until it next writes (a still screen: up to its 180 s limit): stop it there
    try: subprocess.run(['adb', '-s', ip + ':5555', 'shell', 'pkill -2 screenrecord'], capture_output=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired): pass


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
