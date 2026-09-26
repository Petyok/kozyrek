"""ctypes bindings for the NeMo-Speech.cpp C ABI (include/nemo_speech/asr.h, diar.h).

Only the subset kozyrec needs: CPU offline-geometry diarization streams and
cache-aware streaming ASR with word timestamps. Structs mirror the headers of
NeMo-Speech.cpp 97a15afa5c; every struct is size-prefixed, so a header that
grows at the tail stays compatible.
"""

import ctypes as C
import os
from pathlib import Path

import numpy as np

LIB_DIR = Path(os.environ.get("KOZYREC_NEMO_LIB", Path.home() / ".local/opt/nemo-speech/lib"))
LIB_NAMES = ("libnemo_speech_asr_c.so.1", "libnemo_speech_asr_c.so")
SAMPLE_RATE = 16000

OK, INVALID_ARGUMENT, OUT_OF_MEMORY, RUNTIME, CANCELLED = range(5)


class NemoError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message or f"status {status}")
        self.status = status


class _Backend(C.Structure):
    _fields_ = [("size", C.c_size_t), ("gpu", C.c_int32)]


class _Model(C.Structure):
    _fields_ = [("size", C.c_size_t), ("path", C.c_char_p), ("name", C.c_char_p)]


class _Streaming(C.Structure):
    _fields_ = [
        ("size", C.c_size_t),
        ("chunk_size", C.c_float),
        ("ctc_left_padding", C.c_float),
        ("ctc_right_padding", C.c_float),
        ("rnnt_right_context", C.c_int32),
    ]


class _RecognizerConfig(C.Structure):
    _fields_ = [
        ("size", C.c_size_t),
        ("backend", C.POINTER(_Backend)),
        ("model", C.POINTER(_Model)),
        ("streaming", C.POINTER(_Streaming)),
        ("decoder", C.c_void_p),
        ("vad", C.c_void_p),
        ("endpointing", C.c_void_p),
        ("postproc", C.c_void_p),
        ("diar", C.c_void_p),
        ("batching", C.c_void_p),
    ]


class _Options(C.Structure):
    _fields_ = [
        ("size", C.c_size_t),
        ("request_id", C.c_char_p),
        ("language_code", C.c_char_p),
        ("interim_results", C.c_bool),
        ("enable_word_time_offsets", C.c_bool),
        ("enable_automatic_punctuation", C.c_bool),
        ("verbatim_transcripts", C.c_bool),
        ("profanity_filter", C.c_bool),
        ("stop_history_eou_ms", C.c_int32),
        ("speech_contexts", C.c_void_p),
        ("speech_context_count", C.c_size_t),
        ("max_alternatives", C.c_int32),
        ("enable_speaker_diarization", C.c_bool),
        ("max_speaker_count", C.c_int32),
    ]


class _DiarConfig(C.Structure):
    _fields_ = [
        ("size", C.c_size_t),
        ("model_path", C.c_char_p),
        ("gpu", C.c_int32),
        ("preset", C.c_char_p),
        ("chunk_frames", C.c_int32),
        ("right_context_frames", C.c_int32),
        ("left_context_frames", C.c_int32),
        ("fifo_frames", C.c_int32),
        ("spkcache_frames", C.c_int32),
        ("update_period_frames", C.c_int32),
    ]


class _DiarSegment(C.Structure):
    _fields_ = [("start_time", C.c_double), ("end_time", C.c_double), ("speaker", C.c_int32)]


_P = C.c_void_p
_F32P = C.POINTER(C.c_float)
_SIGS = {
    "nemo_speech_asr_last_error": (C.c_char_p, []),
    "nemo_speech_asr_version": (C.c_char_p, []),
    "nemo_speech_asr_recognition_options_default": (_Options, []),
    "nemo_speech_asr_create": (C.c_int, [C.POINTER(_RecognizerConfig), C.POINTER(_P)]),
    "nemo_speech_asr_destroy": (None, [_P]),
    "nemo_speech_asr_streaming_recognize": (C.c_int, [_P, C.POINTER(_Options), C.POINTER(_P)]),
    "nemo_speech_asr_stream_push_f32": (C.c_int, [_P, _F32P, C.c_size_t, C.c_int32]),
    "nemo_speech_asr_stream_finish": (C.c_int, [_P]),
    "nemo_speech_asr_stream_next": (C.c_int, [_P, C.POINTER(_P)]),
    "nemo_speech_asr_stream_close": (None, [_P]),
    "nemo_speech_asr_result_is_final": (C.c_bool, [_P]),
    "nemo_speech_asr_result_audio_processed": (C.c_float, [_P]),
    "nemo_speech_asr_result_alternative_count": (C.c_size_t, [_P]),
    "nemo_speech_asr_result_transcript": (C.c_char_p, [_P, C.c_size_t]),
    "nemo_speech_asr_result_word_count": (C.c_size_t, [_P, C.c_size_t]),
    "nemo_speech_asr_result_word_text": (C.c_char_p, [_P, C.c_size_t, C.c_size_t]),
    "nemo_speech_asr_result_word_start_time": (C.c_int32, [_P, C.c_size_t, C.c_size_t]),
    "nemo_speech_asr_result_word_end_time": (C.c_int32, [_P, C.c_size_t, C.c_size_t]),
    "nemo_speech_asr_result_language_count": (C.c_size_t, [_P, C.c_size_t]),
    "nemo_speech_asr_result_language_code": (C.c_char_p, [_P, C.c_size_t, C.c_size_t]),
    "nemo_speech_asr_result_destroy": (None, [_P]),
    "nemo_speech_diar_create": (C.c_int, [C.POINTER(_DiarConfig), C.POINTER(_P)]),
    "nemo_speech_diar_destroy": (None, [_P]),
    "nemo_speech_diar_num_speakers": (C.c_int32, [_P]),
    "nemo_speech_diar_seconds_per_frame": (C.c_double, [_P]),
    "nemo_speech_diar_stream_open": (C.c_int, [_P, C.POINTER(_P)]),
    "nemo_speech_diar_stream_push_f32": (C.c_int, [_P, _F32P, C.c_size_t, C.c_int32]),
    "nemo_speech_diar_stream_finish": (C.c_int, [_P]),
    "nemo_speech_diar_stream_close": (None, [_P]),
    "nemo_speech_diar_frame_count": (C.c_int64, [_P]),
    "nemo_speech_diar_segments": (
        C.c_int,
        [_P, C.c_void_p, C.POINTER(_DiarSegment), C.c_size_t, C.POINTER(C.c_size_t)],
    ),
}


def find_library(lib_dir=LIB_DIR):
    for name in LIB_NAMES:
        path = Path(lib_dir) / name
        if path.exists():
            return path
    return None


class Lib:
    """The loaded C ABI. Load once per process; handles hold a reference."""

    def __init__(self, path=None):
        path = path or find_library()
        if path is None:
            raise FileNotFoundError(f"libnemo_speech_asr_c.so not found in {LIB_DIR}")
        self._dll = C.CDLL(str(path))
        for name, (restype, argtypes) in _SIGS.items():
            fn = getattr(self._dll, name)
            fn.restype, fn.argtypes = restype, argtypes
            setattr(self, name.removeprefix("nemo_speech_"), fn)

    def check(self, status):
        if status != OK:
            message = (self.asr_last_error() or b"").decode("utf-8", "replace")
            raise NemoError(status, message.strip())

    def version(self):
        return self.asr_version().decode()


def _samples(block):
    block = np.ascontiguousarray(block, dtype=np.float32)
    return block, block.ctypes.data_as(_F32P), block.size


def _text(raw):
    return raw.decode("utf-8", "replace") if raw else ""


class Diarizer:
    def __init__(self, lib, model_path, preset="v3-offline"):
        self.lib = lib
        cfg = _DiarConfig(
            size=C.sizeof(_DiarConfig),
            model_path=str(model_path).encode(),
            gpu=-1,  # the ABI defaults to GPU 0 when unset
            preset=preset.encode() if preset else None,
            left_context_frames=-1,
        )
        self.handle = _P()
        lib.check(lib.diar_create(C.byref(cfg), C.byref(self.handle)))
        self.seconds_per_frame = lib.diar_seconds_per_frame(self.handle)
        self.num_speakers = lib.diar_num_speakers(self.handle)

    def stream(self):
        return DiarStream(self)

    def close(self):
        if self.handle:
            self.lib.diar_destroy(self.handle)
            self.handle = _P()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class DiarStream:
    def __init__(self, diarizer):
        self.lib = diarizer.lib
        self.seconds_per_frame = diarizer.seconds_per_frame
        self.handle = _P()
        self.lib.check(self.lib.diar_stream_open(diarizer.handle, C.byref(self.handle)))

    def push(self, block):
        """Buffer 16 kHz mono samples; runs every complete chunk before returning."""
        keep, ptr, n = _samples(block)
        self.lib.check(self.lib.diar_stream_push_f32(self.handle, ptr, n, SAMPLE_RATE))
        del keep

    def labelled_seconds(self):
        return self.lib.diar_frame_count(self.handle) * self.seconds_per_frame

    def finish(self):
        self.lib.check(self.lib.diar_stream_finish(self.handle))

    def segments(self):
        """[(start_s, end_s, speaker)] with 1-based speakers, sorted by start."""
        count = C.c_size_t(0)
        self.lib.check(self.lib.diar_segments(self.handle, None, None, 0, C.byref(count)))
        buf = (_DiarSegment * max(1, count.value))()
        self.lib.check(self.lib.diar_segments(self.handle, None, buf, count.value, C.byref(count)))
        return [(s.start_time, s.end_time, s.speaker) for s in buf[: count.value]]

    def close(self):
        if self.handle:
            self.lib.diar_stream_close(self.handle)
            self.handle = _P()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Recognizer:
    # Cache-aware right context in 80 ms frames; chunk = (1 + R) * 80 ms. 13 → 1.12 s,
    # the best published WER and the cheapest per audio second on CPU.
    def __init__(self, lib, model_path, right_context=13):
        self.lib = lib
        self._backend = _Backend(size=C.sizeof(_Backend), gpu=-1)
        self._model = _Model(size=C.sizeof(_Model), path=str(model_path).encode(), name=None)
        # Passing the streaming struct overrides every field: keep the CTC defaults.
        self._streaming = _Streaming(
            size=C.sizeof(_Streaming),
            chunk_size=0.16,
            ctc_left_padding=1.92,
            ctc_right_padding=1.92,
            rnnt_right_context=right_context,
        )
        cfg = _RecognizerConfig(
            size=C.sizeof(_RecognizerConfig),
            backend=C.pointer(self._backend),
            model=C.pointer(self._model),
            streaming=C.pointer(self._streaming),
        )
        self.handle = _P()
        lib.check(lib.asr_create(C.byref(cfg), C.byref(self.handle)))

    def stream(self, language="auto", interim=False):
        return AsrStream(self, language, interim)

    def close(self):
        if self.handle:
            self.lib.asr_destroy(self.handle)
            self.handle = _P()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _request_options(lib, language):
    opts = lib.asr_recognition_options_default()
    opts.language_code = language.encode() if language else None  # ctypes keeps the bytes alive
    opts.enable_word_time_offsets = True
    opts.enable_automatic_punctuation = True
    return opts


def _read_result(lib, result):
    """(words [(text, start_s, end_s)], languages [code]) of the top alternative."""
    if lib.asr_result_alternative_count(result) == 0:
        return [], []
    words = [
        (
            _text(lib.asr_result_word_text(result, 0, i)),
            lib.asr_result_word_start_time(result, 0, i) / 1000.0,
            lib.asr_result_word_end_time(result, 0, i) / 1000.0,
        )
        for i in range(lib.asr_result_word_count(result, 0))
    ]
    langs = [
        _text(lib.asr_result_language_code(result, 0, i))
        for i in range(lib.asr_result_language_count(result, 0))
    ]
    return [w for w in words if w[0]], langs


class AsrStream:
    def __init__(self, recognizer, language, interim=False):
        self.lib = recognizer.lib
        opts = _request_options(self.lib, language)
        if interim:
            opts.interim_results = True
        self.handle = _P()
        self.lib.check(
            self.lib.asr_streaming_recognize(recognizer.handle, C.byref(opts), C.byref(self.handle))
        )
        self.finals = []
        self._interim = interim
        self._texts = []  # punctuated transcripts of the finals, kept only with interim=True
        self._partial = ""

    def push(self, block):
        """Buffer samples, then decode every complete chunk (synchronously)."""
        keep, ptr, n = _samples(block)
        self.lib.check(self.lib.asr_stream_push_f32(self.handle, ptr, n, SAMPLE_RATE))
        del keep
        self._drain()

    def _drain(self):
        # Contract: an interim is the last result until more audio is pushed.
        while True:
            result = _P()
            self.lib.check(self.lib.asr_stream_next(self.handle, C.byref(result)))
            if not result:
                return
            try:
                final = self.lib.asr_result_is_final(result)
                if final:
                    self.finals.append(_read_result(self.lib, result))
                if self._interim:
                    self._keep_text(result, final)
            finally:
                self.lib.asr_result_destroy(result)
            if not final:
                return

    def _keep_text(self, result, final):
        text = ""
        if self.lib.asr_result_alternative_count(result):
            text = _text(self.lib.asr_result_transcript(result, 0)).strip()
        if final:
            self._texts.append(text)
            self._partial = ""
        else:
            self._partial = text

    @property
    def text(self):
        """The punctuated transcript so far: every final plus the live interim (interim=True)."""
        return " ".join(t for t in self._texts + [self._partial] if t)

    def finish(self):
        """Flush the tail; returns (words [(text, start_s, end_s)], languages [code])."""
        self.lib.check(self.lib.asr_stream_finish(self.handle))
        self._drain()
        words, languages = [], []
        for w, langs in self.finals:
            words.extend(w)
            languages.extend(code for code in langs if code not in languages)
        return words, languages

    def close(self):
        if self.handle:
            self.lib.asr_stream_close(self.handle)
            self.handle = _P()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
