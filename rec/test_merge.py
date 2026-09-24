"""Merge/echo/turn rules. Run: python3 -m unittest discover -s rec -p 'test_*.py'"""

import unittest

import numpy as np

import merge


def envelope(total_s, base_db, spans):
    """dB per 10 ms frame: base level with (start, end, dB) spans painted on top."""
    env = np.full(int(total_s / merge.FRAME_S), base_db, dtype=np.float32)
    for s, e, db in spans:
        env[int(s / merge.FRAME_S):int(e / merge.FRAME_S)] = db
    return env


def words(*items):
    return [{"w": w, "s": s, "e": e, "lang": None} for w, s, e in items]


class MicChannels(unittest.TestCase):
    # Remote talks 0–150 s. Mic channel 2 = residual echo (30–120 s, quiet);
    # channel 1 = the user, mostly alone (150.5–190 s) plus a short overlap;
    # channel 3 = a loud voice entirely inside remote speech (double talk).
    desk_segs = [(0.0, 150.0, 1)]
    mic_segs = [(30.0, 120.0, 2), (60.0, 62.0, 1), (150.5, 190.0, 1), (125.0, 140.0, 3)]
    desk_db = envelope(200, -80, [(0, 150, -20)])
    mic_db = envelope(200, -80, [(0, 150, -55), (60, 62, -15), (150.5, 190, -15), (125, 140, -12)])

    def test_quiet_channel_inside_remote_speech_is_echo_loud_one_is_me(self):
        cls = merge.classify_mic_channels(self.mic_segs, self.desk_segs, self.mic_db, self.desk_db, aec=True)
        self.assertEqual(cls["echo"], [2])
        self.assertEqual(cls["me"], [1, 3])
        self.assertAlmostEqual(cls["erl_db"], -35.0, places=1)

    def test_without_aec_overlap_alone_decides(self):
        cls = merge.classify_mic_channels(self.mic_segs, self.desk_segs, self.mic_db, self.desk_db, aec=False)
        self.assertEqual(cls["echo"], [2, 3])
        self.assertEqual(cls["me"], [1])

    def test_no_desktop_track_means_everything_is_me(self):
        cls = merge.classify_mic_channels(self.mic_segs, [], self.mic_db, None)
        self.assertEqual(cls["echo"], [])
        self.assertEqual(cls["me"], [1, 2, 3])


class EchoFilter(unittest.TestCase):
    desk_words = words(("We", 10.0, 10.2), ("ship", 10.3, 10.6), ("the", 10.7, 10.8),
                       ("release", 10.9, 11.4), ("on", 11.5, 11.6), ("Friday.", 11.7, 12.0))
    desk_segs = [(9.8, 12.2, 1)]

    def test_repeated_remote_words_are_dropped_own_words_kept(self):
        mic = words(("ship", 10.45, 10.75), ("the", 10.85, 10.95), ("release,", 11.05, 11.5),
                    ("okay", 20.0, 20.3), ("Friday", 30.0, 30.4))
        kept, dropped = merge.echo_filter(mic, self.desk_words, self.desk_segs)
        self.assertEqual([w["w"] for w in kept], ["okay", "Friday"])
        self.assertEqual({w["why"] for w in dropped}, {"echo_text"})

    def test_levels_rescue_a_loud_repeat_and_drop_quiet_mumble(self):
        mic_db = envelope(40, -80, [(9.8, 12.6, -55), (10.45, 11.5, -8)])
        desk_db = envelope(40, -80, [(9.8, 12.2, -20)])
        mic = words(("ship", 10.45, 10.75), ("the", 10.85, 10.95), ("release", 11.05, 11.5),
                    ("mm", 12.1, 12.3))
        kept, dropped = merge.echo_filter(mic, self.desk_words, self.desk_segs, mic_db, desk_db,
                                          erl_db=-35.0, tau_s=0.15)
        self.assertEqual([w["w"] for w in kept], ["ship", "the", "release"])
        self.assertEqual([(w["w"], w["why"]) for w in dropped], [("mm", "echo_level")])

    def test_single_short_common_word_is_not_text_echo(self):
        mic = words(("on", 11.6, 11.7))
        kept, _ = merge.echo_filter(mic, self.desk_words, self.desk_segs)
        self.assertEqual([w["w"] for w in kept], ["on"])


class Islands(unittest.TestCase):
    def test_short_pauses_bridge_long_pauses_split(self):
        isl = merge.build_islands([(1.0, 2.0), (2.5, 3.0), (10.0, 11.0)], 20.0)
        self.assertEqual(isl, [(0.7, 3.3), (9.7, 11.3)])

    def test_discontinuity_splits_an_island(self):
        isl = merge.build_islands([(1.0, 5.0)], 20.0, cuts=[3.0])
        self.assertEqual(isl, [(0.7, 3.0), (3.0, 5.3)])

    def test_long_island_splits_at_widest_pause_in_window(self):
        segs = [(0.0, 70.0), (70.4, 90.0), (90.9, 200.0)]
        isl = merge.build_islands(segs, 300.0, max_len=120.0)
        self.assertEqual(len(isl), 2)
        self.assertAlmostEqual(isl[0][1], 90.45, places=2)
        self.assertEqual(isl[1][1], 200.3)


class Speakers(unittest.TestCase):
    def test_word_spilling_into_next_turn_keeps_onset_speaker(self):
        segs = [(0.0, 5.0, 1), (5.0, 9.0, 2)]
        ws = words(("end.", 4.9, 5.8), ("next", 5.9, 6.2))
        self.assertEqual(merge.assign_speakers(ws, segs), [1, 2])

    def test_blip_speaker_folds_into_neighbour_and_labels_follow_first_word(self):
        ws = words(*[(f"w{i}", 2.0 + i, 2.5 + i) for i in range(6)], ("x", 3.1, 3.3))
        for w in ws[:6]:
            w["spk"] = 3
        ws[-1]["spk"] = 7
        ws.append({"w": "late", "s": 0.5, "e": 0.9, "lang": None, "spk": 5})
        ws += [{"w": f"v{i}", "s": 20.0 + i, "e": 20.5 + i, "lang": None, "spk": 5} for i in range(6)]
        merge.fold_minor_speakers(ws)
        self.assertEqual(ws[6]["spk"], 3)
        self.assertEqual(merge.speaker_labels(ws), {5: "Speaker 1", 3: "Speaker 2"})


class Turns(unittest.TestCase):
    def test_pauses_and_discontinuities_split_turns_ordered_by_start(self):
        ws = words(("Hi", 0.0, 0.3), (",", 0.3, 0.3), ("there.", 0.4, 0.8), ("Later", 5.0, 5.4),
                   ("after", 11.0, 11.3), ("gap", 11.5, 11.8))
        for w in ws:
            w["spk"] = merge.ME
        ws += [{"w": "Yes.", "s": 0.5, "e": 0.9, "lang": None, "spk": "Speaker 1"}]
        turns = merge.build_turns(ws, cuts=[11.4])
        self.assertEqual([(t["spk"], t["text"]) for t in turns],
                         [("Me", "Hi, there."), ("Speaker 1", "Yes."), ("Me", "Later"),
                          ("Me", "after"), ("Me", "gap")])

    def test_same_speaker_thinking_pause_joins_unless_someone_spoke_between(self):
        ws = words(("One.", 0.0, 0.5), ("Two.", 2.5, 3.0), ("Three.", 6.0, 6.5))
        for w in ws:
            w["spk"] = "Speaker 1"
        ws.append({"w": "Hm.", "s": 4.0, "e": 4.3, "lang": None, "spk": merge.ME})
        turns = merge.build_turns(ws)
        self.assertEqual([(t["spk"], t["text"]) for t in turns],
                         [("Speaker 1", "One. Two."), ("Me", "Hm."), ("Speaker 1", "Three.")])
        self.assertNotIn("_langs", turns[0])

    def test_long_monologue_breaks_at_sentence_end_after_soft_max(self):
        ws = [{"w": "word", "s": i * 0.5, "e": i * 0.5 + 0.4, "lang": None, "spk": "Speaker 1"}
              for i in range(160)]
        ws[125]["w"] = "end."
        turns = merge.build_turns(ws)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["words"][-1][0], "end.")


class Formatting(unittest.TestCase):
    def test_clock_and_duration_switch_to_hours_at_one_hour(self):
        self.assertEqual(merge.fmt_clock(3599), "59:59")
        self.assertEqual(merge.fmt_clock(3600, hours=True), "1:00:00")
        self.assertEqual(merge.fmt_duration(59.6), "1:00")
        self.assertEqual(merge.fmt_duration(3600), "1:00:00")

    def test_mixed_language_island_assigns_by_script(self):
        self.assertEqual(merge.word_lang("релиз", ["en-US", "ru-RU"]), "ru-RU")
        self.assertEqual(merge.word_lang("deploy", ["ru-RU", "en-US"]), "en-US")
        self.assertEqual(merge.word_lang("42", ["ru-RU", "en-US"]), "ru-RU")

    def test_wall_time_adds_clock_jumps_before_the_position(self):
        gaps = [{"at_s": 100.0, "dur_s": 20.0}]
        self.assertEqual(merge.wall_time(1000.0, 50.0, gaps), 1050.0)
        self.assertEqual(merge.wall_time(1000.0, 150.0, gaps), 1170.0)

    def test_markdown_marks_minority_language_turns(self):
        turns = [{"spk": "Me", "s": 4.0, "e": 5.0, "lang": "ru-RU", "text": "Привет, слышно?",
                  "words": [["Привет,", 4.0, 4.4], ["слышно?", 4.5, 5.0]]},
                 {"spk": "Speaker 1", "s": 6.0, "e": 6.5, "lang": "en-US", "text": "Yes.",
                  "words": [["Yes.", 6.0, 6.5]]}]
        doc = merge.build_document("/s", {"t0_unix": 0, "duration_s": 65}, turns, {}, [],
                                   {"echo_dropped_words": 0}, {})
        md = merge.render_markdown(doc)
        self.assertIn("**[00:04] Me:** Привет, слышно?", md)
        self.assertIn("**[00:06] Speaker 1:** *(en-US)* Yes.", md)
        self.assertIn("- Participants: Me, Speaker 1", md)


if __name__ == "__main__":
    unittest.main()
