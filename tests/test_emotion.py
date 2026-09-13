"""Delivery tags: stripping, resolution, and the SSML they become."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import captions as cap
from core import emotion, voice

VOICE = "en-US-DavisNeural"
FLAT = "en-US-BrianMultilingualNeural"

TAGGED = "[whispering] The light was on.  [terrified] She had not been home."
CLEAN = "The light was on. She had not been home."

# --- stripping -----------------------------------------------------------

assert emotion.strip(TAGGED) == CLEAN, emotion.strip(TAGGED)
assert emotion.strip(CLEAN) == CLEAN
assert emotion.strip("[whispering]") == ""
print("tags strip cleanly")

# The reason stripping matters at all: captions compare their own word count
# against the engine's and silently emit no sentence breaks when they disagree,
# so one leaked tag costs a whole beat its line breaks with no error anywhere.
assert cap.words_per_sentence(emotion.strip(TAGGED)) == [4, 5]
assert cap.words_per_sentence(TAGGED) != [4, 5], "a leaked tag must change the count"
print("stripped text tokenises the way captions expect")

# --- resolution ----------------------------------------------------------

assert emotion.resolve("whispering", VOICE) == "whispering"
assert emotion.resolve("fearful", VOICE) == "terrified", "alias must map"
assert emotion.resolve("whisper", VOICE) == "whispering"
assert emotion.resolve("ominous", VOICE) is None
print("aliases resolve, invented tags do not")

assert emotion.styles_for(FLAT) == set(), "Brian Multilingual supports no styles"
assert emotion.resolve("whispering", FLAT) is None
print("a style-less voice accepts no tags")

assert emotion.unknown_tags("[ominous] It waited.", VOICE) == ["ominous"]
assert emotion.unknown_tags(TAGGED, VOICE) == []
print("unknown tags are reported for the validator")

# Every style offered to the model has to be one the voice can honour, or the
# script gets rejected for obeying its own instructions.
for style in emotion.HORROR_STYLES:
    assert emotion.resolve(style, VOICE), f"{style} unusable on {VOICE}"
print(f"all {len(emotion.HORROR_STYLES)} offered styles are usable on {VOICE}")

# And each must resolve to a DIFFERENT style. Offering two words for one
# delivery ("fearful" and "terrified" are the same Azure style) reads to the
# model as an extra escalation step, and it writes one the voice cannot deliver.
resolved = [emotion.resolve(s, VOICE) for s in emotion.HORROR_STYLES]
dupes = {s for s in resolved if resolved.count(s) > 1}
assert not dupes, f"offered styles collapse onto the same delivery: {dupes}"
print(f"all {len(resolved)} resolve to distinct deliveries")

# --- segmentation --------------------------------------------------------

assert emotion.segments("[whispering] One. [terrified] Two.", VOICE, "chat") == [
    ("whispering", "One."), ("terrified", "Two.")]
assert emotion.segments("One. [terrified] Two.", VOICE, "whispering") == [
    ("whispering", "One."), ("terrified", "Two.")]
print("each tag opens a run; untagged lead takes the default")

# Validation rejects a bad tag with a retry left. By synthesis time it is too
# late for that, and losing the words would cost the whole video.
assert emotion.segments("[ominous] It waited.", VOICE, "whispering") == [
    ("whispering", "It waited.")]
print("an unusable tag falls back rather than dropping speech")

# --- SSML ----------------------------------------------------------------

ssml = voice.build_ssml("[whispering] One. [terrified] Two.", VOICE, "-4%", "-4Hz",
                        default_style="chat", style_degree=1.3)
assert '<mstts:express-as style="whispering" styledegree="1.3">One.' in ssml, ssml
assert '<mstts:express-as style="terrified" styledegree="1.3">Two.' in ssml, ssml
assert 'name="en-US-DavisNeural"' in ssml
assert 'rate="-4%"' in ssml and 'pitch="-4Hz"' in ssml
assert 'xml:lang="en-US"' in ssml
print("SSML wraps each run in its own express-as span")

escaped = voice.build_ssml("Tom & Jerry <ran>", VOICE, "+0%", "+0Hz")
assert "Tom &amp; Jerry &lt;ran&gt;" in escaped, escaped
assert "<ran>" not in escaped
print("narration is XML-escaped")

plain = voice.build_ssml("Plain line.", FLAT, "+0%", "+0Hz")
assert "express-as" not in plain and "Plain line." in plain
print("no express-as for a voice that supports none")

# --- the channel's own config -------------------------------------------

from core.config import load_channel
from formats.story import allowed_styles

ch = load_channel("story_short")
vcfg = ch["voice"]
if vcfg.get("provider") == "azure":
    assert emotion.styles_for(vcfg["name"]), f"{vcfg['name']} supports no styles"
    default = vcfg.get("default_style")
    assert not default or emotion.resolve(default, vcfg["name"]), \
        f"default_style {default!r} unusable on {vcfg['name']}"
    assert allowed_styles(ch), "azure channel must offer the model some tags"
    print(f"story_short: {vcfg['name']} offers {', '.join(allowed_styles(ch))}")
else:
    assert allowed_styles(ch) == [], "edge-tts must not ask for tags"
    print("story_short is on edge-tts; delivery contract is off")

print("\nALL EMOTION TESTS PASS")
