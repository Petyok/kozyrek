"""Gate decisions, the on-screen edit, the Typist, and the session loop around a fake recognizer."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import kozydict as kd

B = kd.BLOCK_S


def blocks(n, tag):
    return [(tag, i) for i in range(n)]


def run_gate(gate, seq):
    """seq: [(db, tag)] → the tags the gate fed, in order."""
    fed = []
    for db, tag in seq:
        out, _ = gate.step(tag, db)
        fed.extend(out)
    return fed


class GateTest(unittest.TestCase):
    def test_soft_onset_after_a_long_pause_arrives_with_its_preroll_in_order(self):
        g = kd.Gate()
        pause = [(-60, t) for t in blocks(round(4 / B), "pause")]
        run_gate(g, pause)
        pre = round(g.PREROLL_S / B)
        fed = run_gate(g, [(-30, ("speech", 0))])
        self.assertEqual(fed, [t for _, t in pause[-pre:]] + [("speech", 0)])

    def test_pause_after_speech_is_fed_for_hang_s_then_skipped(self):
        g = kd.Gate()
        run_gate(g, [(-60, t) for t in blocks(round(4 / B), "noise")])
        run_gate(g, [(-30, t) for t in blocks(20, "speech")])
        pause = [(-60, t) for t in blocks(round(5 / B), "pause")]
        fed = run_gate(g, pause)
        self.assertEqual(fed, [t for _, t in pause[: round(g.HANG_S / B)]])
        self.assertAlmostEqual(g.quiet_s, 5, places=6)

    def test_leading_digital_silence_does_not_pin_the_floor(self):
        # A PipeWire source hands out exact zeros while it starts up.
        g = kd.Gate()
        run_gate(g, [(-200, t) for t in blocks(10, "zeros")])
        run_gate(g, [(-60, t) for t in blocks(round(3 / B), "noise")])
        self.assertEqual(run_gate(g, [(-30, ("speech", 0))])[-1], ("speech", 0))
        self.assertEqual(g.quiet_s, 0)
        tail = run_gate(g, [(-60, t) for t in blocks(round(4 / B), "after")])
        self.assertEqual(len(tail), round(g.HANG_S / B))

    def test_talking_from_the_first_second_is_never_skipped(self):
        # No pause yet to learn the room from: the floor would sit at speech level.
        g = kd.Gate()
        speech = [(-28 + i % 3, t) for i, t in enumerate(blocks(round(4 / B), "speech"))]
        self.assertEqual(run_gate(g, speech), [t for _, t in speech])

    def test_speech_resets_the_silence_clock(self):
        g = kd.Gate()
        run_gate(g, [(-60, t) for t in blocks(round(3 / B), "noise")])
        self.assertAlmostEqual(g.quiet_s, 3, places=6)
        run_gate(g, [(-25, ("speech", 0))])
        self.assertEqual(g.quiet_s, 0)


class EditTest(unittest.TestCase):
    def test_growing_transcript_only_appends(self):
        self.assertEqual(kd.edit("Я хочу пиц", "Я хочу пиццу поесть"), (0, "цу поесть"))

    def test_revision_erases_back_to_the_first_changed_character(self):
        self.assertEqual(kd.edit("Здравствуйте. Я хоч", "Здравствуйте! Я хочу"), (7, "! Я хочу"))

    def test_unchanged_transcript_types_nothing(self):
        self.assertEqual(kd.edit("ok", "ok"), (0, ""))


class TypistTest(unittest.TestCase):
    def test_slow_window_ends_with_the_newest_transcript_typed_exactly_once(self):
        screen, calls = Screen(), []

        def slow(back, tail):
            calls.append(tail)
            time.sleep(0.02)
            screen.type_keys(back, tail)

        t = kd.Typist(slow, lambda **ev: None)
        texts = [" ".join(WORDS[: i % len(WORDS) + 1]) + "!" * (i // len(WORDS)) for i in range(60)]
        try:
            for text in texts:
                t.show(text)
            t.finish()
        finally:
            t.close()
        self.assertEqual(screen.text, texts[-1])
        self.assertLess(len(calls), len(texts))  # skipped what came and went, did not queue it

    def test_a_typing_failure_surfaces_instead_of_hanging_the_stop(self):
        def broken(back, tail):
            raise kd.Failure("wtype failed: no seat")

        t = kd.Typist(broken, lambda **ev: None)
        try:
            t.show("один")
            with self.assertRaisesRegex(kd.Failure, "no seat"):
                t.finish()
            with self.assertRaisesRegex(kd.Failure, "no seat"):
                t.show("один two")
        finally:
            t.close()


WORDS = "один two три four пять six семь eight".split()
WORD_S = 0.25


class FakeStream:
    """A word per WORD_S of loud audio, shown only once TAIL_S more audio has been
    pushed after it, like the real model's chunking; it has no finish()."""

    def __init__(self):
        self.pushed_s, self.loud_s, self.zeros_s = 0.0, 0.0, 0.0
        self.word_ends = []  # in pushed seconds

    def push(self, block):
        for part in np.array_split(block, max(1, block.size // kd.BLOCK)):
            s = part.size / kd.RATE
            self.pushed_s += s
            if not part.any():
                self.zeros_s += s
            elif kd.level_db(part) > -40:
                self.loud_s += s
                if self.loud_s >= WORD_S * (len(self.word_ends) + 1) - 1e-9:
                    self.word_ends.append(self.pushed_s)

    def heard(self):
        return " ".join(WORDS[i % len(WORDS)] for i in range(len(self.word_ends)))

    @property
    def text(self):
        n = sum(1 for e in self.word_ends if self.pushed_s - e >= kd.TAIL_S - 1e-3)
        return " ".join(WORDS[i % len(WORDS)] for i in range(n))

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeRecognizer:
    def __init__(self):
        self.streams = []

    def stream(self, language, interim=False):
        s = FakeStream()
        self.streams.append(s)
        return s

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# `loud` blocks of speech-level noise, then `quiet` blocks of room noise (forever when
# quiet < 0), then exit `code`. Neither is exact zeros: those would read as a muted mic.
CAPTURE = ("import sys, time, numpy as np\n"
           "loud, quiet, code = map(int, sys.argv[1:4])\n"
           "rng = np.random.default_rng(0); i = 0\n"
           "while quiet < 0 or i < loud + quiet:\n"
           "    gain = 0.1 if loud < 0 or i < loud else 0.0003\n"
           "    sys.stdout.buffer.write((rng.standard_normal(%d) * gain).astype(np.float32).tobytes())\n"
           "    sys.stdout.flush(); time.sleep(0.05); i += 1\n"
           "sys.exit(code)\n" % kd.BLOCK)


class Screen:
    def __init__(self):
        self.text = ""

    def type_keys(self, back, tail):
        self.text = self.text[: len(self.text) - back] + tail


class DictateTest(unittest.TestCase):
    def setUp(self):
        self.models = tempfile.TemporaryDirectory()
        Path(self.models.name, kd.ASR_MODEL["file"]).touch()
        self.rec = FakeRecognizer()
        for p in (mock.patch.object(kd, "MODEL_DIR", Path(self.models.name)),
                  mock.patch.object(kd.ns, "Lib", lambda: None),
                  mock.patch.object(kd.ns, "Recognizer", lambda *args: self.rec)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.models.cleanup)
        self.events = []
        self.screen = Screen()

    def emit(self, **ev):
        self.events.append(ev)

    def dictate(self, stop, loud, quiet=0, code=0):
        capture = [sys.executable, "-c", CAPTURE, str(loud), str(quiet), str(code)]
        return kd.dictate(stop, self.screen.type_keys, capture, self.emit)

    def stop_after(self, s):
        stop = threading.Event()
        threading.Timer(s, stop.set).start()
        return stop

    def test_stop_mid_speech_still_types_every_word_heard(self):
        self.assertEqual(self.dictate(self.stop_after(1.0), loud=-1, quiet=-1), "user")
        heard = self.rec.streams[0].heard()
        self.assertTrue(heard)
        self.assertEqual(self.screen.text, heard)
        self.assertEqual([e["text"] for e in self.events if e["ev"] == "text"][-1], heard)
        self.assertEqual([e["state"] for e in self.events if e["ev"] == "state"], ["listening", "finishing"])

    def test_stop_after_a_pause_types_every_word_without_decoding_padding(self):
        self.dictate(self.stop_after(1.0 + kd.Gate.HANG_S + 1.0), loud=20, quiet=-1)
        stream = self.rec.streams[0]
        self.assertEqual(self.screen.text, stream.heard())
        self.assertEqual(stream.zeros_s, 0)

    def test_capture_that_dies_is_an_error_not_a_hang(self):
        with self.assertRaisesRegex(kd.Failure, "capture ended"):
            self.dictate(threading.Event(), loud=10, code=3)

    def test_capture_that_never_delivers_is_an_error_not_a_hang(self):
        with mock.patch.object(kd, "NO_AUDIO_S", 0.5):
            with self.assertRaisesRegex(kd.Failure, "no audio"):
                kd.dictate(threading.Event(), self.screen.type_keys,
                           [sys.executable, "-c", "import time; time.sleep(30)"], self.emit)


if __name__ == "__main__":
    unittest.main()
