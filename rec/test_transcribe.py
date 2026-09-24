"""Worker bookkeeping: progress bar monotonicity and resumable island logs."""

import os
import tempfile
import unittest

_tmp = tempfile.TemporaryDirectory()
os.environ["KOZYREC_STATE_DIR"] = _tmp.name  # before importing state

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


if __name__ == "__main__":
    unittest.main()
