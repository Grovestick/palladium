"""Where a film's credits begin, from its picture and its sound.

Run by the system's Python, which has the GPU libraries the compiled server has not:

    python -P pd_credits_make.py VIDEO SECONDS
    python -P pd_credits_make.py --speech VIDEO SECONDS

Reads the last quarter of the file (at most 25 minutes): one keyframe every few
seconds scored by CLIP against a handful of descriptions, and the sound through a
speech detector. The last line printed is `OK {"picture": s, "speech": s}` or
`NO <reason>`.

--speech reads only the sound: a few windows spread over the file, through the same
speech detector, for measuring subtitles against. Prints `OK {"windows": [[start,
length, [[from, to], ...]], ...]}`.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

#: what CLIP is asked to tell apart; the first CREDITS are credits, the rest are not.
#: Tuned on 60 films whose chapters name the credits: against the fixed line, early by
#: more than a minute 6 times instead of 22, within a minute early or two late 40 times
#: instead of 30.
PROMPTS = ['end credits of a movie, lines of names and text on a black screen',
           'a rolling credits list of cast and crew names',
           'animated or stylised end title credits with names',
           'credits text over film footage',
           'a black screen', 'a title card with a few words of text',
           'a scene from a movie with people', 'a landscape or action scene from a film',
           'a dark scene from a film', 'a close-up of a face']
CREDITS = 2
#: a frame is credits when the credits descriptions together score above this
FRAME_SAYS = 0.4
#: and the credits have begun at the first such frame after which this share are credits
HOLDS = 0.8
#: and of the keyframes right after it, this many of the next NEXT are credits as well
NEXT, NEXT_YES = 4, 3
#: speech this long or shorter, alone after a long quiet, is a word in the credits song
STRAY_SPEECH = 2.0


def frames_from(ffmpeg, video, start, into):
    """Keyframes from `start` on, as small pictures, with their times in the film."""
    out = subprocess.run([ffmpeg, "-v", "info", "-skip_frame", "nokey", "-ss", str(start),
                          "-i", video, "-vf", "scale=224:224,showinfo", "-fps_mode", "vfr",
                          "-an", os.path.join(into, "%05d.jpg")],
                         capture_output=True, text=True, timeout=1200,
                         encoding="utf-8", errors="replace")
    pts = [start + float(x) for x in re.findall(r"pts_time:([0-9.]+)", out.stderr)]
    files = sorted(glob.glob(os.path.join(into, "*.jpg")))
    n = min(len(pts), len(files))
    return pts[:n], files[:n]


#: the picture model, kept in the local cache after its first download
CLIP = "openai/clip-vit-base-patch32"


def picture_scores(files):
    """For each frame: the share CLIP gives the credits descriptions."""
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    # A folder of plain files beside this script, written once. The hub's cache keeps
    # its files as symbolic links, and the server's own runs could not read them: every
    # run for a day failed with the weights sitting in the cache.
    kept = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models",
                        "clip-vit-base-patch32")
    try:
        model = CLIPModel.from_pretrained(kept, local_files_only=True)
        proc = CLIPProcessor.from_pretrained(kept, local_files_only=True)
    except Exception:
        # the copy already here, without asking the hub: asked once a minute for
        # hours, the hub stops answering, and a refused check reads as no weights
        try:
            model = CLIPModel.from_pretrained(CLIP, local_files_only=True)
            proc = CLIPProcessor.from_pretrained(CLIP, local_files_only=True)
        except Exception:
            model = CLIPModel.from_pretrained(CLIP)       # not here yet: fetched once
            proc = CLIPProcessor.from_pretrained(CLIP)
        try:
            model.save_pretrained(kept)
            proc.save_pretrained(kept)
        except Exception:
            pass
    model = model.to(dev).eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(files), 64):
            ims = [Image.open(f).convert("RGB") for f in files[i:i + 64]]
            px = proc(text=PROMPTS, images=ims, return_tensors="pt", padding=True).to(dev)
            out += model(**px).logits_per_image.softmax(-1).cpu().numpy().tolist()
    return out


def speech_spans(ffmpeg, video, start):
    """Where somebody is speaking, from `start` on, in seconds of the film."""
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    raw = subprocess.run([ffmpeg, "-v", "quiet", "-ss", str(start), "-i", video, "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, timeout=1200).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    spans = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=1500))
    return [(start + s["start"] / 16000, start + s["end"] / 16000) for s in spans]


def picture_end(pts, probs):
    """The first keyframe from which the picture is mostly credits to the end."""
    said = [sum(p[:CREDITS]) > FRAME_SAYS for p in probs]
    for i, yes in enumerate(said):
        rest = said[i:]
        # and straight after it too: one card that looked like credits, then 45 s of
        # film before the scroll, passed the share test and put the start 47 s early
        soon = said[i + 1:i + 1 + NEXT]
        if (yes and sum(rest) / len(rest) >= HOLDS
                and (not soon or sum(soon) >= min(NEXT_YES, len(soon)))):
            return pts[i]
    return None


def speech_end(spans):
    """The end of the last real speech: a word or two alone at the very end - a line of
    the closing song - does not count."""
    spans = list(spans)
    while len(spans) > 1 and spans[-1][1] - spans[-1][0] <= STRAY_SPEECH \
            and spans[-1][0] - spans[-2][1] > 60:
        spans.pop()
    return spans[-1][1] if spans else None


#: windows the sound is read in for --speech: enough to vote, a sixth of a film
SPEECH_WINDOWS, SPEECH_FILM, SPEECH_EPISODE = 8, 240, 150


def speech_windows(ffmpeg, video, whole):
    """Speech in evenly spread windows: [[start, length, [[from, to], ...]]]."""
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    span = SPEECH_FILM if whole > 1800 else SPEECH_EPISODE
    if whole <= span * 2:
        starts, span = [0.0], whole
    else:
        count = max(2, min(SPEECH_WINDOWS, int(whole // span)))
        step = (whole - span) / float(count - 1)
        starts = [round(i * step, 1) for i in range(count)]
    out = []
    for start in starts:
        raw = subprocess.run([ffmpeg, "-v", "quiet", "-ss", str(start), "-t", str(span),
                              "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                              "-f", "s16le", "-"], capture_output=True,
                             stdin=subprocess.DEVNULL, timeout=600).stdout
        audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        if not len(audio):
            continue
        spans = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300))
        out.append([start, round(len(audio) / 16000.0, 2),
                    [[round(x["start"] / 16000.0, 2), round(x["end"] / 16000.0, 2)]
                     for x in spans]])
    return out


def main():
    if sys.argv[1] == "--speech":
        ffmpeg = os.environ.get("PALLADIUM_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
        got = speech_windows(ffmpeg, sys.argv[2], float(sys.argv[3]))
        print("OK " + json.dumps({"windows": got}), flush=True)
        return
    video, whole = sys.argv[1], float(sys.argv[2])
    ffmpeg = os.environ.get("PALLADIUM_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
    start = max(whole * 0.75, whole - 25 * 60)
    into = tempfile.mkdtemp(prefix="pd-credits-")
    why = []
    # each reading on its own: a picture that cannot be read still leaves the sound
    try:
        pts, files = frames_from(ffmpeg, video, start, into)
        picture = picture_end(pts, picture_scores(files)) if len(files) >= 10 else None
    except Exception as e:
        picture = None
        why.append("picture: %s: %s" % (type(e).__name__, str(e)[:150]))
    finally:
        shutil.rmtree(into, ignore_errors=True)
    try:
        speech = speech_end(speech_spans(ffmpeg, video, start))
    except Exception as e:
        speech = None
        why.append("speech: %s: %s" % (type(e).__name__, str(e)[:150]))
    print("OK " + json.dumps({"picture": picture, "speech": speech,
                              "why": "; ".join(why)}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("NO %s: %s" % (type(e).__name__, str(e)[:200]), flush=True)
        sys.exit(1)
