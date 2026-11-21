"""Generate sample projects (images, tone 'voice', music, scripts) with FFmpeg for testing."""
import subprocess, sys
from pathlib import Path

SCRIPT = ("Sometimes life doesn't go as planned. We make wishes and expect everything to happen. "
          "But life has its own direction. And that is okay.")


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def image(path, i, w=1280, h=720):
    colors = ["0x2b6cb0", "0xc05621", "0x2f855a", "0x805ad5", "0xb83280", "0x975a16"]
    ff("-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:d=1,hue=h={i * 55}", "-frames:v", "1", str(path))


def tone(path, dur, freq=220):
    ff("-f", "lavfi", "-i", f"sine=f={freq}:d={dur},tremolo=f=4:d=0.6", "-ar", "44100", str(path))


def make(root, n_projects=3, images=4, dur=10.0, script=True, prefix="set-"):
    root = Path(root)
    music = root / "_music"
    music.mkdir(parents=True, exist_ok=True)
    for k, (f, d) in enumerate([(330, 40), (440, 25), (550, 15)]):
        tone(music / f"track{k + 1}.mp3", d, f)
    for p in range(1, n_projects + 1):
        d = root / f"{prefix}{p:03d}"
        d.mkdir(exist_ok=True)
        for i in range(1, images + 1):
            image(d / f"image_{i:02d}.png" if i != 10 else d / "image_10.png", i + p,
                  *((1280, 720) if i % 2 else (720, 1280)))
        tone(d / "voice.mp3", dur)
        if script:
            (d / "script.txt").write_text(SCRIPT, encoding="utf-8")
    return root


if __name__ == "__main__":
    print(make(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fx"))
