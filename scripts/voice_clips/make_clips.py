"""Write (or check) the built-in sample clips of the shipped voices,
domovoi/voice_clips/<voice>.ogg.

A "play a sample" button must play the voice it names (owner, 2026-10-12),
also before that voice is downloaded: then it plays this clip. One short
line per voice ("Hi, I'm Norman. This is how Domovoi sounds."), spoken by
the repo's own TTS (domovoi/phonemizer + the Piper voice on ONNX Runtime,
domovoi/clients/tts.py) with ONNX Runtime's seed fixed, then encoded as Ogg
Opus by opusenc (opus-tools; BSD-2-Clause, libopus BSD-3-Clause; only used
here, never shipped) at 24 kbit/s mono.

    bash scripts/voice_clips/make-clips-in-container.sh VOICES_DIR   # the reproducible way
    python scripts/voice_clips/make_clips.py --voices DIR [--out DIR] [--check]

VOICES_DIR holds the three voices' .onnx + .onnx.json (each checked against
its pin in domovoi/clients/tts.py before use). --check renders again into a
temporary folder and exits 1 when any clip's SHA-256 differs from
domovoi/voice_clips/SHA256SUMS. Byte-identical output needs the same
ONNX Runtime and libopus builds (the container pins both); elsewhere the
clips sound the same but their bytes may differ.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from domovoi.clients import tts  # noqa: E402
from domovoi.phonemizer import Phonemizer  # noqa: E402
from domovoi.voice_sample import CLIP_DIR, clip_line  # noqa: E402

SEED = 20261012
BITRATE_KBPS = 24
SERIAL = 1  # the Ogg stream serial number (opusenc picks a random one otherwise)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_voice(voices: Path, v) -> Path:
    onnx = voices / f"{v.model_ref}.onnx"
    cfg = voices / f"{v.model_ref}.onnx.json"
    if not (onnx.is_file() and cfg.is_file()):
        raise SystemExit(f"{v.model_ref}: {onnx.name} and {cfg.name} are needed in {voices}")
    if sha256(onnx) != v.onnx_sha256:
        raise SystemExit(f"{onnx} is not the pinned model")
    if tts._git_blob_sha1(cfg.read_bytes()) != v.json_blob_sha1:
        raise SystemExit(f"{cfg} is not the pinned config")
    return onnx


def render(voices: Path, out: Path, opusenc: str) -> dict:
    import onnxruntime

    ph = Phonemizer()   # the shipped "say it like" list: Domovoi is DOH-moe-voy
    sums = {}
    with tempfile.TemporaryDirectory() as tmp:
        for v in tts.SHIPPED_VOICES:
            onnx = check_voice(voices, v)
            onnxruntime.set_seed(SEED)       # before the session: its noise then repeats
            voice = tts.PiperVoice(onnx, threads=1)  # one thread: the same sums on any CPU count
            phonemes = ph.phonemize(clip_line(v.name), dialect=voice.dialect)
            pcm = voice.synthesize_pcm(phonemes, 1.0)
            wav = Path(tmp) / f"{v.model_ref}.wav"
            wav.write_bytes(tts._pcm_to_wav_bytes(pcm, voice.sample_rate))
            ogg = out / f"{v.model_ref}.ogg"
            subprocess.run([opusenc, "--quiet", "--bitrate", str(BITRATE_KBPS), "--vbr", "--comp", "10",
                            "--framesize", "20", "--serial", str(SERIAL), "--title", clip_line(v.name),
                            str(wav), str(ogg)], check=True)
            sums[ogg.name] = sha256(ogg)
            print(f"{ogg.name}: {ogg.stat().st_size} bytes, {len(pcm) / 2 / voice.sample_rate:.2f} s, "
                  f"{' | '.join(phonemes)}")
    return sums


def sums_text(sums: dict) -> str:
    import onnxruntime

    head = (f"# SHA-256 of the built-in voice sample clips (scripts/voice_clips/make_clips.py): "
            f"ONNX Runtime {onnxruntime.__version__}, seed {SEED}, opusenc {BITRATE_KBPS} kbit/s VBR, "
            f"serial {SERIAL}.\n")
    return head + "".join(f"{h}  {name}\n" for name, h in sorted(sums.items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--voices", required=True, type=Path, help="folder with the shipped voices' files")
    ap.add_argument("--out", type=Path, default=CLIP_DIR, help="where the clips go (default: %(default)s)")
    ap.add_argument("--check", action="store_true", help="render again and compare with SHA256SUMS")
    ap.add_argument("--opusenc", default=os.environ.get("OPUSENC", "opusenc"))
    args = ap.parse_args()
    opusenc = shutil.which(args.opusenc)
    if opusenc is None:
        raise SystemExit("opusenc (opus-tools) is needed: run make-clips-in-container.sh")
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            got = render(args.voices, Path(tmp), opusenc)
        want = (CLIP_DIR / "SHA256SUMS").read_text(encoding="utf-8")
        same = sums_text(got).split("\n", 1)[1] == want.split("\n", 1)[1]
        print("the clips are up to date" if same else "the clips differ from SHA256SUMS")
        sys.exit(0 if same else 1)
    args.out.mkdir(parents=True, exist_ok=True)
    sums = render(args.voices, args.out, opusenc)
    with open(args.out / "SHA256SUMS", "w", encoding="utf-8", newline="\n") as f:
        f.write(sums_text(sums))


if __name__ == "__main__":
    main()
