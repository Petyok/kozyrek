"""Offload: when the GPU host counts as busy, and how long the host keeps a call."""

import json
import os
import threading
import time
import unittest

import offload

IDLE = [(0, 11000)] * offload.PROBE_SAMPLES


class Busy(unittest.TestCase):
    def test_an_idle_gpu_is_free_with_or_without_comfyui(self):
        self.assertIsNone(offload.busy_reason(IDLE, None))
        self.assertIsNone(offload.busy_reason(IDLE, {"queue_running": [], "queue_pending": []}))

    def test_one_utilisation_spike_is_enough(self):
        spike = IDLE[:-1] + [(offload.BUSY_UTIL_PCT, 11000)]
        self.assertIsNotNone(offload.busy_reason(spike, None))
        below = IDLE[:-1] + [(offload.BUSY_UTIL_PCT - 1, 11000)]
        self.assertIsNone(offload.busy_reason(below, None))

    def test_vram_held_by_an_idle_model_is_busy(self):
        low = IDLE[:-1] + [(0, offload.MIN_FREE_MIB - 1)]
        self.assertIsNotNone(offload.busy_reason(low, None))
        self.assertIsNone(offload.busy_reason(IDLE[:-1] + [(0, offload.MIN_FREE_MIB)], None))

    def test_a_queued_comfyui_job_is_busy_even_between_steps(self):
        self.assertIsNotNone(offload.busy_reason(IDLE, {"queue_running": [], "queue_pending": [[1, "id"]]}))
        self.assertIsNotNone(offload.busy_reason(IDLE, {"queue_running": [[1, "id"]], "queue_pending": []}))

    def test_no_usable_gpu_is_busy(self):
        self.assertIsNotNone(offload.busy_reason([], None))


class HostInbox(unittest.TestCase):
    def open(self, timeout):
        r, w = os.pipe()
        exits, done = [], threading.Event()

        def exit(code):
            exits.append(code)
            done.set()
        inbox = offload._Inbox(os.fdopen(r, "rb"), timeout=timeout, exit=exit)
        return inbox, os.fdopen(w, "wb"), exits, done

    def test_audio_with_newlines_arrives_intact_and_eof_ends_the_process(self):
        inbox, w, exits, done = self.open(timeout=30)
        data = b"\n".join(os.urandom(4000) for _ in range(300))  # > the pipe buffer, many b"\n"
        w.write(json.dumps({"op": "audio", "track": "mic", "size": len(data)}).encode() + b"\n" + data)
        w.write(b'{"op": "ping"}\n{"op": "diarize", "track": "mic"}\n')
        w.flush()
        audio, op = inbox.get(), inbox.get()
        self.assertEqual((audio["op"], bytes(audio["data"])), ("audio", data))
        self.assertEqual(op, {"op": "diarize", "track": "mic"})  # pings never reach the job
        w.close()
        self.assertTrue(done.wait(5))
        self.assertEqual(exits, [0])

    def test_a_silent_laptop_ends_the_process_but_pings_keep_it(self):
        inbox, w, exits, done = self.open(timeout=0.4)
        until = time.monotonic() + 1.2
        while time.monotonic() < until:  # three timeouts' worth of pings
            w.write(b'{"op": "ping"}\n')
            w.flush()
            time.sleep(0.1)
        self.assertEqual(exits, [])
        t0 = time.monotonic()
        self.assertTrue(done.wait(3))
        self.assertEqual(exits, [3])
        self.assertLess(time.monotonic() - t0, 1.5)
        w.close()


if __name__ == "__main__":
    unittest.main()
