"""Render the same hook in candidate voices so they can be compared by ear."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from core import voice as tts

TEXT = ("Here is a strange question. Would you rather save five thousand rupees a month "
        "for thirty years, or ten thousand a month for fifteen years? "
        "Most people say the bigger amount. Most people are wrong. "
        "And the reason is the most powerful idea in money.")

CANDIDATES = [
    ("1_indian_male",    "en-IN-PrabhatNeural",            "-8%"),
    ("2_indian_female",  "en-IN-NeerjaExpressiveNeural",   "-8%"),
    ("3_andrew_multi",   "en-US-AndrewMultilingualNeural", "-8%"),
    ("4_brian_multi",    "en-US-BrianMultilingualNeural",  "-8%"),
    ("5_ava_multi",      "en-US-AvaMultilingualNeural",    "-8%"),
]

out = pathlib.Path("out/voice_samples")
out.mkdir(parents=True, exist_ok=True)
for name, vid, rate in CANDIDATES:
    r = tts.synthesize(TEXT, out / f"{name}.mp3", voice=vid, rate=rate)
    print(f"{name:18} {vid:34} {r.duration:5.1f}s")
print(f"\nfolder: {out.resolve()}")
