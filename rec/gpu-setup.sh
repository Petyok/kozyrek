#!/usr/bin/env bash
# One-time, idempotent setup of the GPU transcription runtime on the GPU host
# (see docs/rec.md, "Offload to a GPU host"). Run it from the laptop:
#
#   ssh gpu-box bash -s < rec/gpu-setup.sh
#
# Installs under /opt/kozyrec: a venv with numpy, NeMo-Speech.cpp built with
# CUDA at the laptop's commit, the diarizer downloaded and the ASR model
# converted at the laptop's revisions (both sha256-checked). Nothing here ever
# holds call audio or transcripts.
set -euo pipefail

ROOT=/opt/kozyrec
NEMO_REPO=https://github.com/NVIDIA/NeMo-Speech.cpp
NEMO_COMMIT=97a15afa5c
CUDA_ARCH=86  # Ampere (RTX 30xx); set to your card's compute capability
DIAR="nvidia/Nemotron-3-Diarization f667ed73aee57d40cc39428eb768b4fd87a0a29e Nemotron-3-Diarization.q8_0.gguf 08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1"
# No NeMo-Speech.cpp GGUF is published for it: converted from the .nemo.
ASR="oruk/orukeet b59c13a733fce5cf193230d0fa317ee7f145a108 orukeet-v0.1.0.q8_0.gguf f3b5569dc4a86deee279fa484bb6985572088beba4b78775218d658918e0b297"

install -d -m 0700 "$ROOT"
install -d "$ROOT/models"

[ -x "$ROOT/venv/bin/python" ] || python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q --disable-pip-version-check numpy 'cmake>=3.26' ninja
export PATH="$ROOT/venv/bin:/usr/local/cuda/bin:$PATH"

src="$ROOT/src/nemo-speech"
[ -d "$src/.git" ] || git clone -q "$NEMO_REPO" "$src"
cd "$src"
git cat-file -e "$NEMO_COMMIT^{commit}" 2>/dev/null || git fetch -q origin
git checkout -q "$NEMO_COMMIT"

read -r repo rev file sha <<<"$DIAR"
dst="$ROOT/models/$file"
if ! { [ -f "$dst" ] && echo "$sha  $dst" | sha256sum -c --status; }; then
    curl -fsSL --retry 3 -o "$dst.part" "https://huggingface.co/$repo/resolve/$rev/$file"
    echo "$sha  $dst.part" | sha256sum -c --quiet
    mv "$dst.part" "$dst"
fi

read -r repo rev file sha <<<"$ASR"
dst="$ROOT/models/$file"
if ! { [ -f "$dst" ] && echo "$sha  $dst" | sha256sum -c --status; }; then
    [ -x "$ROOT/conv-venv/bin/python" ] || python3 -m venv "$ROOT/conv-venv"
    "$ROOT/conv-venv/bin/pip" install -q --disable-pip-version-check -U pip
    "$ROOT/conv-venv/bin/pip" install -q --disable-pip-version-check -r requirements.txt
    # The GGUF's general.name is the output file's stem: convert under the
    # final name (in a scratch dir) or the sha256 differs.
    tmp=$(mktemp -d "$ROOT/models/.convert.XXXXXX")
    "$ROOT/conv-venv/bin/python" convert_model.py "$repo" --revision "$rev" --outtype q8_0 --outfile "$tmp/$file"
    echo "$sha  $tmp/$file" | sha256sum -c --quiet
    mv "$tmp/$file" "$dst"
    rmdir "$tmp"
fi

lib="$ROOT/nemo-speech/lib/libnemo_speech_asr_c.so"
stamp="$ROOT/nemo-speech/.commit"
if [ -f "$lib" ] && [ "$(cat "$stamp" 2>/dev/null)" = "$NEMO_COMMIT" ]; then
    echo "runtime up to date ($NEMO_COMMIT)"
    exit 0
fi

# configure.sh insists on llama.cpp for every CUDA preset, mic capture or not.
git submodule update --init --recursive --depth 1 ggml llama.cpp
CMAKE_POLICY_VERSION_MINIMUM=3.5 JOBS="$(nproc)" scripts/build_sentencepiece_static.sh
scripts/configure.sh cuda-asr \
    -DNEMO_SPEECH_BUILD_MIC_CAPTURE=OFF -DNEMO_SPEECH_BUILD_TTS=OFF \
    -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" -DCMAKE_INSTALL_PREFIX="$ROOT/nemo-speech"
cmake --build --preset cuda-asr -j "$(nproc)"
cmake --install build/cuda-asr --prefix "$ROOT/nemo-speech"
echo "$NEMO_COMMIT" >"$stamp"
echo "runtime built ($NEMO_COMMIT)"
