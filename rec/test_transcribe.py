"""Worker bookkeeping: progress bar, resumable island logs, decoder end-of-track."""

import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

_tmp = tempfile.TemporaryDirectory()
os.environ["KOZYREC_STATE_DIR"] = _tmp.name  # before importing state

import state  # noqa: E402
import transcribe  # noqa: E402


class ProgressBar(unittest.TestCase):
    def test_reestimated_costs_never_move_the_bar_back_and_it_ends_full(self):
        p = transcribe.Progress("/s")
        try:
            p.set_costs(prepare=3, diarize=100, asr=300, merge=2)
            seen = []
            p.begin("prepare"); p.end(); seen.append(p.frac)
            p.begin("diarize", 200)
            for a in (50, 100, 200):
                p.advance(a); seen.append(p.frac)
            p.end(); seen.append(p.frac)
            # Islands turn out far shorter than guessed: asr cost shrinks 10x.
            p.set_costs(asr=30)
            p.begin("asr", 60)
            for a in (10, 30, 60):
                p.advance(a); seen.append(p.frac)
            p.end()
            p.begin("merge", 1); p.advance(1); p.end(); seen.append(p.frac)
        finally:
            p.stop()
        self.assertEqual(seen, sorted(seen))
        self.assertAlmostEqual(seen[-1], 1.0)
        self.assertLess(seen[4], 0.3)  # diarize ≈ a quarter of the original estimate

    def test_advance_past_total_does_not_overshoot_the_stage(self):
        p = transcribe.Progress("/s")
        try:
            p.set_costs(prepare=0, diarize=10, asr=10, merge=0)
            p.begin("diarize", 100)
            p.advance(250)
            self.assertAlmostEqual(p.frac, 0.5)
        finally:
            p.stop()

    def test_concurrent_flushes_never_collide_even_with_slow_fsync(self):
        # The heartbeat thread and the worker's final flush write the same file
        # through one per-pid tmp name; unserialised, one rename found the tmp
        # gone and the FileNotFoundError failed a finished transcription.
        real_fsync = os.fsync

        def slow_fsync(fd):
            time.sleep(0.01)
            real_fsync(fd)

        p = transcribe.Progress("/s")
        errors = []

        def writer():
            try:
                for _ in range(40):
                    p.flush(force=True)
            except OSError as exc:
                errors.append(exc)

        try:
            p.begin("diarize", 100)
            with mock.patch.object(state.os, "fsync", slow_fsync):
                threads = [threading.Thread(target=writer) for _ in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
        finally:
            p.stop()
        self.assertEqual(errors, [])
        self.assertEqual(state.read_json(state.PROGRESS)["session"], "/s")


class IslandLog(unittest.TestCase):
    def test_torn_last_line_is_dropped_and_appends_stay_readable(self):
        with tempfile.TemporaryDirectory() as session:
            ck = transcribe.Checkpoints(session)
            ck.append_asr("mic", {"key": "a", "words": []})
            with open(ck.path("asr_mic.jsonl"), "a") as f:
                f.write('{"key": "b", "wor')
            ck.repair_asr("mic")
            ck.append_asr("mic", {"key": "c", "words": []})
            self.assertEqual([r["key"] for r in ck.asr_records("mic")], ["a", "c"])


class DecoderEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.wav = os.path.join(cls.dir.name, "t.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=30",
                        "-ac", "1", "-ar", "16000", cls.wav], check=True)
        cls.bad = os.path.join(cls.dir.name, "bad.opus")
        with open(cls.bad, "wb") as f:
            f.write(b"OggS" + os.urandom(4000))

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def tearDown(self):
        transcribe._cancel.clear()

    def drain(self, rd):
        while rd.read(transcribe.DIAR_BLOCK).size:
            pass

    def test_clean_end_of_track_delivers_every_sample(self):
        with transcribe.TrackReader(self.wav) as rd:
            self.drain(rd)
            self.assertEqual(rd.pos, 30 * 16000)

    def test_decoder_killed_by_cancel_is_cancel_not_end_of_track(self):
        with transcribe.TrackReader(self.wav) as rd:
            rd.read(transcribe.DIAR_BLOCK)
            os.kill(rd.proc.pid, signal.SIGTERM)  # cancel signals the whole process group
            with self.assertRaises(transcribe.Cancelled):
                self.drain(rd)

    def test_unreadable_audio_is_a_non_retryable_failure(self):
        with transcribe.TrackReader(self.bad) as rd, self.assertRaises(transcribe.Failure) as cm:
            self.drain(rd)
        self.assertEqual((cm.exception.msg, cm.exception.retry), ("audio unreadable", False))


if __name__ == "__main__":
    unittest.main()
