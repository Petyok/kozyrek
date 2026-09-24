"""Pure two-track merge logic: echo-channel classification, ASR islands,
speaker attribution, echo word filtering, turns and transcript rendering.

Times are seconds on the call timeline (audio time from t0). Envelopes are
numpy float arrays of level in dB per 10 ms frame (None when unavailable).
Nothing here touches files, processes or the speech runtime.
"""

import bisect
import difflib
import time
import unicodedata

import numpy as np

FRAME_S = 0.01

# Desktop speech reaches the mic through speakers + air + buffering: echo starts
# slightly before a desktop segment edge (segment padding) and lingers after it.
ECHO_DILATE = (0.05, 0.40)
ECHO_CHANNEL_OVERLAP = 0.85  # share of a mic channel inside desktop speech
ECHO_CHANNEL_MARGIN_DB = 10.0
ECHO_WORD_MARGIN_DB = 8.0
NEAR_FIELD_MARGIN_DB = 20.0  # far above residual echo: the user really said it
DEFAULT_ERL_DB = -30.0
DEFAULT_TAU_S = 0.15
MIN_LEVEL_FRAMES = 3000  # 30 s of frames before trusting a measured ERL / delay

# Speaker of a word = diarization speaker over its first 120 ms (transducer
# punctuation can stretch a word's end into the next turn).
ONSET_WINDOW_S = 0.12
TURN_GAP_S = 1.2
TURN_SOFT_MAX_S = 60.0
TURN_JOIN_GAP_S = 5.0
SENTENCE_END = (".", "?", "!", "…")
ATTACHING = set(".,!?;:…)]}»”%")

ME = "Me"


# ---- intervals ---------------------------------------------------------------


def union(intervals, join=0.0):
    """Sorted, merged copy; intervals closer than `join` are bridged."""
    out = []
    for s, e in sorted((float(s), float(e)) for s, e in intervals if e > s):
        if out and s - out[-1][1] <= join:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def dilate(intervals, before, after):
    return [(max(0.0, s - before), e + after) for s, e in intervals]


def covered(merged, s, e):
    """Seconds of [s, e] inside `merged` (a union() result)."""
    if e <= s or not merged:
        return 0.0
    i = max(0, bisect.bisect_right(merged, (s, float("inf"))) - 1)
    total = 0.0
    while i < len(merged) and merged[i][0] < e:
        total += max(0.0, min(e, merged[i][1]) - max(s, merged[i][0]))
        i += 1
    return total


def _frame_mask(intervals, n):
    mask = np.zeros(n, dtype=bool)
    for s, e in intervals:
        a, b = max(0, int(s / FRAME_S)), min(n, int(np.ceil(e / FRAME_S)))
        if b > a:
            mask[a:b] = True
    return mask


# ---- levels ------------------------------------------------------------------


def _shifted(desk_db, n, lag):
    """desk_db delayed by `lag` frames, aligned to n mic frames (NaN-padded)."""
    out = np.full(n, np.nan)
    src = desk_db[: max(0, n - lag)]
    out[lag : lag + src.size] = src
    return out


def level_db(db, s, e):
    """Power-mean level of [s, e] in dB, or None when no frames."""
    if db is None:
        return None
    a, b = max(0, int(s / FRAME_S)), min(db.size, int(np.ceil(e / FRAME_S)))
    if b <= a:
        return None
    frames = db[a:b].astype(np.float64)
    frames = frames[np.isfinite(frames)]
    if frames.size == 0:
        return None
    return float(10.0 * np.log10(np.mean(10.0 ** (frames / 10.0))))


def estimate_tau(mic_db, desk_db, mask, max_lag_s=0.5):
    """Echo delay: lag (0..max_lag_s) maximising envelope correlation on mask."""
    n = min(mic_db.size, desk_db.size, mask.size)
    if int(mask[:n].sum()) < MIN_LEVEL_FRAMES:
        return DEFAULT_TAU_S
    best, best_lag = -np.inf, None
    for lag in range(int(max_lag_s / FRAME_S) + 1):
        d = _shifted(desk_db, n, lag)
        m = mask[:n] & np.isfinite(d)
        if m.sum() < MIN_LEVEL_FRAMES:
            continue
        x, y = mic_db[:n][m], d[m]
        x, y = x - x.mean(), y - y.mean()
        denom = np.sqrt((x * x).sum() * (y * y).sum())
        if denom <= 0:
            continue
        c = float((x * y).sum() / denom)
        if c > best:
            best, best_lag = c, lag
    return DEFAULT_TAU_S if best_lag is None else best_lag * FRAME_S


def _median_delta(mic_db, desk_db, mask, tau):
    n = min(mic_db.size, mask.size)
    d = _shifted(desk_db, n, int(round(tau / FRAME_S)))
    m = mask[:n] & np.isfinite(d)
    if not m.any():
        return None, 0
    return float(np.median(mic_db[:n][m] - d[m])), int(m.sum())


# ---- mic channels ------------------------------------------------------------


def classify_mic_channels(mic_segs, desk_segs, mic_db=None, desk_db=None, aec=True):
    """Split mic diarization channels into the user ("me") and residual echo.

    Residual echo is a different voice from the user, so the diarizer gives it
    its own channel, active only while the desktop plays. A channel is echo when
    >= 85 % of it lies inside (dilated) desktop speech and it is not much louder
    than the measured echo return level (the level test is skipped without AEC).
    """
    desk = union(dilate([(s, e) for s, e, _ in desk_segs], *ECHO_DILATE))
    channels = {}
    for s, e, k in mic_segs:
        ch = channels.setdefault(k, {"segs": [], "active_s": 0.0, "inside_s": 0.0})
        ch["segs"].append((s, e))
        ch["active_s"] += e - s
        ch["inside_s"] += covered(desk, s, e)
    for ch in channels.values():
        ch["r"] = ch["inside_s"] / ch["active_s"] if ch["active_s"] > 0 else 0.0

    levels = mic_db is not None and desk_db is not None and mic_db.size and desk_db.size
    erl, tau = DEFAULT_ERL_DB, DEFAULT_TAU_S
    if levels:
        n = mic_db.size
        in_desk = _frame_mask(desk, n)
        any_mic = _frame_mask([(s, e) for s, e, _ in mic_segs], n)
        candidates = [ch["segs"] for ch in channels.values() if ch["r"] >= ECHO_CHANNEL_OVERLAP]
        echo_like = _frame_mask([iv for segs in candidates for iv in segs], n) & in_desk
        if echo_like.sum() < MIN_LEVEL_FRAMES:
            echo_like = in_desk & ~any_mic
        tau = estimate_tau(mic_db, desk_db, echo_like)
        measured, count = _median_delta(mic_db, desk_db, in_desk & ~any_mic, tau)
        if measured is not None and count >= MIN_LEVEL_FRAMES:
            erl = min(0.0, max(-60.0, measured))
        for ch in channels.values():
            ch["delta_db"], _ = _median_delta(mic_db, desk_db, _frame_mask(ch["segs"], n) & in_desk, tau)

    me, echo = [], []
    for k in sorted(channels):
        ch = channels[k]
        quiet = (not aec) or (not levels) or ch.get("delta_db") is None \
            or ch["delta_db"] < erl + ECHO_CHANNEL_MARGIN_DB
        (echo if ch["r"] >= ECHO_CHANNEL_OVERLAP and quiet else me).append(k)
    stats = {
        str(k): {
            "active_s": round(ch["active_s"], 2),
            "overlap": round(ch["r"], 3),
            "delta_db": None if ch.get("delta_db") is None else round(ch["delta_db"], 1),
        }
        for k, ch in sorted(channels.items())
    }
    return {"me": me, "echo": echo, "erl_db": erl, "tau_s": tau, "channels": stats}


# ---- islands -----------------------------------------------------------------


def build_islands(segs, total_s, cuts=(), pad=0.3, join=1.0, max_len=120.0, min_gap=0.3):
    """ASR regions: padded speech segments, bridged across short pauses, split at
    recording discontinuities and (when longer than max_len) at the widest pause."""
    speech = union(segs)
    padded = union([(max(0.0, s - pad), min(total_s, e + pad)) for s, e in speech], join=join)
    out = []
    for s, e in padded:
        pieces, start = [], s
        for c in sorted(c for c in cuts if s < c < e):
            pieces.append((start, c))
            start = c
        pieces.append((start, e))
        for a, b in pieces:
            out.extend(_split_long(a, b, speech, max_len, min_gap))
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a > 0.05]


def _split_long(s, e, speech, max_len, min_gap):
    out = []
    while e - s > max_len:
        lo, hi = s + max_len / 2, s + max_len
        best = None
        for (_, a_end), (b_start, _) in zip(speech, speech[1:]):
            if lo <= a_end and b_start <= hi and b_start - a_end >= min_gap:
                if best is None or b_start - a_end > best[1] - best[0]:
                    best = (a_end, b_start)
        if best is None:
            break
        cut = (best[0] + best[1]) / 2
        out.append((s, cut))
        s = cut
    out.append((s, e))
    return out


# ---- words ---------------------------------------------------------------------

_CYRILLIC_LANGS = ("ru", "uk", "bg")


def word_lang(text, langs):
    """Language of one word. The runtime reports languages per result, not per
    word: when an island mixes languages, pick by script."""
    if not langs:
        return None
    if len(langs) == 1:
        return langs[0]
    cyr = any("CYRILLIC" in unicodedata.name(ch, "") for ch in text)
    lat = any("LATIN" in unicodedata.name(ch, "") for ch in text)
    for code in langs:
        is_cyr = code.split("-")[0] in _CYRILLIC_LANGS
        if (cyr and is_cyr) or (lat and not cyr and not is_cyr):
            return code
    return langs[0]


def island_words(record, offset=0.0):
    """Call-timeline words of one ASR checkpoint record."""
    base = record["s"] + offset
    langs = record.get("lang") or []
    return [
        {"w": w, "s": round(base + s, 3), "e": round(base + max(e, s), 3), "lang": word_lang(w, langs)}
        for w, s, e in record["words"]
    ]


def assign_speakers(words, segs):
    """Diarization speaker per word, onset-anchored (punctuation can stretch a
    transducer word into the next turn). Fallbacks: nearest segment within 1 s,
    then the previous word's speaker."""
    by_spk = {}
    for s, e, k in segs:
        by_spk.setdefault(k, []).append((s, e))
    merged = {k: union(v) for k, v in by_spk.items()}
    starts = sorted((s, e, k) for s, e, k in segs)
    out, prev = [], None
    for w in words:
        a = w["s"]
        b = a + max(0.05, min(ONSET_WINDOW_S, w["e"] - w["s"]))
        best, best_cov = None, 0.0
        for k, m in merged.items():
            c = covered(m, a, b)
            if c > best_cov:
                best, best_cov = k, c
        if best is None:
            dist = [(min(abs(a - s), abs(a - e)) if not s <= a <= e else 0.0, k) for s, e, k in starts]
            near = min(dist, default=None)
            if near is not None and near[0] <= 1.0:
                best = near[1]
        if best is None:
            best = prev if prev is not None else (starts[0][2] if starts else 1)
        out.append(best)
        prev = best
    return out


def fold_minor_speakers(words, key="spk", min_s=3.0, min_words=5):
    """Reassign speakers with < min_s of speech AND < min_words words to the
    temporally nearest word of a real speaker (diarizer blips, notification
    sounds). Mutates and returns words."""
    stats = {}
    for w in words:
        st = stats.setdefault(w[key], [0, 0.0])
        st[0] += 1
        st[1] += w["e"] - w["s"]
    minor = {k for k, (n, dur) in stats.items() if n < min_words and dur < min_s}
    if not minor or len(minor) == len(stats):
        return words
    major = sorted((w["s"], w[key]) for w in words if w[key] not in minor)
    times = [t for t, _ in major]
    for w in words:
        if w[key] in minor:
            i = bisect.bisect_left(times, w["s"])
            cands = [major[j] for j in (i - 1, i) if 0 <= j < len(major)]
            w[key] = min(cands, key=lambda c: abs(c[0] - w["s"]))[1]
    return words


def speaker_labels(words, key="spk"):
    """Remote diar ids → "Speaker N" numbered by first word."""
    order = []
    for w in sorted(words, key=lambda w: w["s"]):
        if w[key] not in order:
            order.append(w[key])
    return {k: f"Speaker {i + 1}" for i, k in enumerate(order)}


# ---- echo filter -----------------------------------------------------------------


def norm_token(text):
    t = "".join(ch for ch in text.lower() if not unicodedata.category(ch).startswith("P"))
    return t.replace("ё", "е").strip()


def echo_filter(mic_words, desk_words, desk_segs, mic_db=None, desk_db=None,
                erl_db=DEFAULT_ERL_DB, tau_s=DEFAULT_TAU_S):
    """Drop mic words that are the remote side leaking through the speakers.

    Only words overlapping (dilated) desktop speech are candidates. A word is
    echo when it repeats desktop words from the preceding 1.5 s (aligned text),
    or when it is barely louder than the echo return level. A text match that is
    far louder than any echo (near-field) is kept: the user repeated a phrase.
    Returns (kept, dropped); dropped words carry "why".
    """
    desk = union(dilate([(s, e) for s, e, _ in desk_segs], *ECHO_DILATE))
    dstarts = [w["s"] for w in desk_words]
    dtoks = [norm_token(w["w"]) for w in desk_words]
    levels = mic_db is not None and desk_db is not None
    lag = int(round(tau_s / FRAME_S))

    def delta(w):
        if not levels:
            return None
        m = level_db(mic_db, w["s"], w["e"])
        d = level_db(desk_db, max(0.0, w["s"] - lag * FRAME_S), max(0.0, w["e"] - lag * FRAME_S))
        return None if m is None or d is None else m - d

    def desk_range(a, b):
        return range(bisect.bisect_left(dstarts, a), bisect.bisect_right(dstarts, b))

    cand = [i for i, w in enumerate(mic_words) if covered(desk, w["s"], w["e"]) > 0]
    runs, cur = [], []
    for i in cand:
        if cur and (i != cur[-1] + 1 or mic_words[i]["s"] - mic_words[cur[-1]]["e"] > 1.0):
            runs.append(cur)
            cur = []
        cur.append(i)
    if cur:
        runs.append(cur)

    why = {}
    for run in runs:
        rs, re_ = mic_words[run[0]]["s"], mic_words[run[-1]]["e"]
        di = [j for j in desk_range(rs - 1.5, re_ + 0.5) if dtoks[j]]
        mi = [i for i in run if norm_token(mic_words[i]["w"])]
        a = [norm_token(mic_words[i]["w"]) for i in mi]
        b = [dtoks[j] for j in di]
        if a and b:
            sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
            for block in sm.get_matching_blocks():
                if block.size >= 2 or (block.size == 1 and len(a[block.a]) >= 5):
                    for x in range(block.a, block.a + block.size):
                        why[mi[x]] = "echo_text"
        for x, i in enumerate(mi):
            if i in why or len(a[x]) < 4:
                continue
            w = mic_words[i]
            for j in desk_range(w["s"] - 1.5, w["s"] + 1.5):
                if dtoks[j] and difflib.SequenceMatcher(None, a[x], dtoks[j]).ratio() >= 0.8:
                    why[i] = "echo_text"
                    break
        for i in run:
            d = delta(mic_words[i])
            if d is None:
                continue
            if why.get(i) == "echo_text" and d >= erl_db + NEAR_FIELD_MARGIN_DB:
                del why[i]
            elif i not in why and d < erl_db + ECHO_WORD_MARGIN_DB:
                why[i] = "echo_level"

    kept = [w for i, w in enumerate(mic_words) if i not in why]
    dropped = [dict(w, why=why[i]) for i, w in enumerate(mic_words) if i in why]
    return kept, dropped


# ---- turns ---------------------------------------------------------------------


def join_words(tokens):
    out = ""
    for t in tokens:
        if out and t[:1] not in ATTACHING:
            out += " "
        out += t
    return out


def build_turns(words, cuts=(), max_gap=TURN_GAP_S, soft_max=TURN_SOFT_MAX_S,
                join_gap=TURN_JOIN_GAP_S):
    """Per speaker, maximal runs of words with pauses <= max_gap, never across a
    recording discontinuity; runs longer than soft_max end at the next sentence
    end. Turns are ordered by start (ties: earlier end first); then consecutive
    turns of one speaker (nobody else in between) are joined across ordinary
    thinking pauses up to join_gap."""
    cuts = sorted(cuts)
    by_spk = {}
    for w in words:
        by_spk.setdefault(w["spk"], []).append(w)
    turns = []
    for spk, ws in by_spk.items():
        ws.sort(key=lambda w: (w["s"], w["e"]))
        cur = []
        for w in ws:
            if cur:
                prev = cur[-1]
                cut = bisect.bisect_right(cuts, prev["s"]) < bisect.bisect_right(cuts, w["s"])
                long_done = (prev["e"] - cur[0]["s"] > soft_max
                             and prev["w"].rstrip("\"'»”)").endswith(SENTENCE_END))
                if w["s"] - prev["e"] > max_gap or cut or long_done:
                    turns.append(_turn(spk, cur))
                    cur = []
            cur.append(w)
        if cur:
            turns.append(_turn(spk, cur))
    turns.sort(key=lambda t: (t["s"], t["e"]))
    out = []
    for t in turns:
        p = out[-1] if out else None
        if (p and p["spk"] == t["spk"] and t["s"] - p["e"] <= join_gap
                and t["e"] - p["s"] <= soft_max
                and bisect.bisect_right(cuts, p["e"]) == bisect.bisect_right(cuts, t["s"])):
            out[-1] = _turn(t["spk"], [{"w": w, "s": s, "e": e, "lang": lang}
                                       for x in (p, t) for (w, s, e), lang in
                                       zip(x["words"], x["_langs"])])
        else:
            out.append(t)
    for t in out:
        del t["_langs"]
    return out


def _turn(spk, ws):
    langs = {}
    for w in ws:
        if w.get("lang"):
            langs[w["lang"]] = langs.get(w["lang"], 0) + 1
    return {
        "spk": spk,
        "s": ws[0]["s"],
        "e": max(w["e"] for w in ws),
        "lang": max(langs, key=langs.get) if langs else None,
        "text": join_words([w["w"] for w in ws]),
        "words": [[w["w"], w["s"], w["e"]] for w in ws],
        "_langs": [w.get("lang") for w in ws],  # dropped by build_turns
    }


# ---- document ----------------------------------------------------------------------


def fmt_clock(seconds, hours=False):
    """Turn timestamp: MM:SS, or H:MM:SS for calls of an hour or more."""
    t = max(0, int(seconds))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if hours else f"{h * 60 + m:02d}:{s:02d}"


def fmt_duration(seconds):
    t = max(0, int(round(seconds)))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def wall_time(t0_unix, audio_t, gaps):
    """Unix time of an audio position; gaps are points where the clock jumped."""
    return t0_unix + audio_t + sum(g["dur_s"] for g in gaps if g["at_s"] <= audio_t)


def build_document(session, meta, turns, labels, dropped, stats, engine):
    """transcript.json (schema 1). `labels` maps turn spk → display label; the
    user is ME. meta = session.json (t0_unix, duration_s, gaps, complete)."""
    t0 = float(meta.get("t0_unix") or 0.0)
    gaps = [g for g in meta.get("gaps") or [] if "at_s" in g and "dur_s" in g]
    words_by_lang, speech = {}, {}
    for t in turns:
        n = len(t["words"])
        if t["lang"]:
            words_by_lang[t["lang"]] = words_by_lang.get(t["lang"], 0) + n
        speech[t["spk"]] = speech.get(t["spk"], 0.0) + sum(e - s for _, s, e in t["words"])
    total = sum(words_by_lang.values())
    languages = {k: round(v / total, 3) for k, v in sorted(words_by_lang.items(), key=lambda kv: -kv[1])}
    order = [ME] if ME in speech else []
    order += [k for k in sorted(speech, key=lambda k: min(t["s"] for t in turns if t["spk"] == k))
              if k != ME]
    return {
        "schema": 1,
        "session": str(session),
        "t0_unix": t0,
        "duration_s": round(float(meta.get("duration_s") or 0.0), 2),
        "complete_recording": bool(meta.get("complete", True)),
        "language_majority": next(iter(languages), None),
        "languages": languages,
        "engine": engine,
        "speakers": [{"id": k, "label": labels.get(k, k), "speech_s": round(speech[k], 1)} for k in order],
        "turns": [
            dict(t, label=labels.get(t["spk"], t["spk"]), wall=round(wall_time(t0, t["s"], gaps), 2))
            for t in turns
        ],
        "dropped": [{"w": w["w"], "s": w["s"], "e": w["e"], "why": w["why"]} for w in dropped],
        "stats": stats,
    }


def render_markdown(doc):
    t0, dur = doc["t0_unix"], doc["duration_s"]
    lt = time.localtime(t0)
    tz = time.strftime("%z", lt)
    tz = f"{tz[:3]}:{tz[3:]}" if len(tz) == 5 else tz
    hours = dur >= 3600
    majority = doc["language_majority"]
    lines = [
        f"# Call {time.strftime('%Y-%m-%d %H:%M', lt)} · {fmt_duration(dur)}",
        "",
        f"- Started: {time.strftime('%Y-%m-%d %H:%M:%S', lt)} {tz} · Duration: {fmt_duration(dur)}",
        f"- Participants: {', '.join(s['label'] for s in doc['speakers']) or 'nobody spoke'}",
    ]
    if doc["languages"]:
        lines.append("- Languages: " + ", ".join(
            f"{k} {round(v * 100)}%" for k, v in doc["languages"].items()))
    notes = []
    removed = doc["stats"].get("echo_dropped_words", 0)
    if removed:
        notes.append(f"{removed} echo word{'s' if removed != 1 else ''} removed")
    if not doc["complete_recording"]:
        notes.append("recording was interrupted")
    if notes:
        lines.append("- Notes: " + " · ".join(notes))
    lines += ["", "---", ""]
    if not doc["turns"]:
        lines += ["*No speech was recognised.*", ""]
    for t in doc["turns"]:
        marker = f"*({t['lang']})* " if t["lang"] and majority and t["lang"] != majority else ""
        lines += [f"**[{fmt_clock(t['s'], hours)}] {t['label']}:** {marker}{t['text']}", ""]
    return "\n".join(lines)
