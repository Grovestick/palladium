#!/usr/bin/env python3
"""Make a subtitle from the soundtrack, when nobody has published one.

Run as its own process, not imported: it loads a speech model and a translation
model, which together want more memory than a media server should ever hold, and
when it is finished the memory goes back to the machine with the process.

    pd_subs_make.py <video> <language> <out.srt>

It prints one line per step - `P <fraction> <what>` - so the server can say how far
along it is, and the last line is either `OK <path>` or `NO <reason>`.

The speech is read by Whisper, which can transcribe any language and translate any
language into English, but never into anything else. Swedish subtitles for English
speech therefore take two models: Whisper for the words, NLLB for the language.
"""
import glob
import os
import site
import sys
import time

# the CUDA libraries come from pip wheels, which are not on the PATH
for base in site.getsitepackages():
    for found in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
        try:
            os.add_dll_directory(found)
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = found + os.pathsep + os.environ["PATH"]

MODEL = os.environ.get("PALLADIUM_WHISPER", "large-v3")


def plain_copy(repo, mark):
    """A model as real files in a folder of our own, made once and used by every run.

    The cache a model is downloaded into is a tree of symbolic links, and a process
    started by the server cannot follow them: the speech model reported "unable to
    open model.bin" with the file plainly there, and the translation model reported a
    package missing that was installed all along - it had failed to read its own
    tokenizer and fallen back to building one. Real files answer both for good.

    `mark` is a file that proves the copy is finished.
    """
    data = os.environ.get("PALLADIUM_DATA") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "Palladium")
    mine = os.path.join(data, "models", repo.split("/")[-1])
    if os.path.exists(os.path.join(mine, mark)):
        return mine
    import shutil
    from huggingface_hub import snapshot_download
    got = snapshot_download(repo)
    os.makedirs(mine, exist_ok=True)
    for name in os.listdir(got):
        source = os.path.realpath(os.path.join(got, name))
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(mine, name))
    return mine


def model_here():
    """The speech model, as plain files."""
    return plain_copy("Systran/faster-whisper-" + MODEL, "model.bin")
#: how much of the film to decode and listen to at a time. Ten minutes: long enough
#: that the model is not started over and over, short enough that the first lines are
#: on screen inside a minute.
STRETCH = 600.0


def say(fraction, what):
    """One line the server reads. A fraction below zero leaves the number as it was."""
    print("P %.3f %s" % (fraction, what), flush=True)


def stamp(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def two_lines(text, width=42):
    """A caption is read at a glance: two lines, broken on a space."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    lines, line = [], ""
    for word in text.split(" "):
        if line and len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    lines.append(line)
    if len(lines) <= 2:
        return "\n".join(lines)
    half = len(lines) // 2
    return " ".join(lines[:half]) + "\n" + " ".join(lines[half:])


#: How long a line may stay on screen, and how much may be on it. A cue that runs to
#: the next one - twenty seconds of film with one sentence on it - reads as subtitles
#: that do not match the picture, because for most of that time they do not.
CUE_SECONDS, CUE_LETTERS = 6.0, 84


def cut_into_cues(segment):
    """One of the model's segments as cues a person can read.

    The model returns what it heard between silences, which is frequently three
    sentences and twenty seconds. Word timings say when each of those was actually
    spoken, so the line can be put on screen with the words rather than before them.
    """
    said = getattr(segment, "words", None)
    text = segment.text.strip()
    if not said:
        # No word timings for this one. The segment's own span is where the model
        # heard speech, padding and all, so a line put on screen for the whole of it
        # arrives before it is spoken and stays after. Share the time out by length
        # instead, and never hold a line longer than a line is read for.
        if not text:
            return []
        pieces, run = [], ""
        for word in text.split(" "):
            if run and len(run) + len(word) + 1 > CUE_LETTERS:
                pieces.append(run)
                run = word
            else:
                run = (run + " " + word).strip()
        if run:
            pieces.append(run)
        whole = max(0.2, float(segment.end) - float(segment.start))
        letters = sum(len(p) for p in pieces) or 1
        out, at = [], float(segment.start)
        for piece in pieces:
            span = min(CUE_SECONDS, whole * len(piece) / letters)
            out.append(((at, at + span), piece))
            at += span
        return out
    out, start, holding = [], None, []
    for word in said:
        if start is None:
            start = word.start
        holding.append(word)
        line = "".join(w.word for w in holding).strip()
        long_enough = (word.end - start) >= CUE_SECONDS or len(line) >= CUE_LETTERS
        # a full stop is where a line wants to end, if it has earned one
        ends = line.endswith((".", "!", "?", "…")) and len(line) > 24
        if long_enough or ends:
            out.append(((start, word.end), line))
            start, holding = None, []
    if holding:
        line = "".join(w.word for w in holding).strip()
        if line:
            out.append(((start, holding[-1].end), line))
    return out


def tidy_times(times):
    """No line still on screen when the next one starts, and none left hanging.

    Ninety of a film's three hundred lines ran straight into the next, which reads as
    a subtitle that will not go away.
    """
    out = []
    for i, (begins, ends) in enumerate(times):
        after = times[i + 1][0] if i + 1 < len(times) else None
        if after is not None:
            ends = min(ends, after - 0.05)
        ends = min(ends, begins + CUE_SECONDS)
        out.append((begins, max(ends, begins + 0.4)))
    return out


def write_srt(out, times, words):
    """The whole file, from the start, every time.

    Written beside itself and moved over the top, so a player reading it never sees
    half a cue - and because it is rewritten as the words arrive, a subtitle can be
    watched while the rest of it is still being made.
    """
    nl = chr(10)
    tmp = out + ".part"
    with open(tmp, "w", encoding="utf-8", newline=nl) as f:
        for n, ((start, end), text) in enumerate(zip(times, words), 1):
            f.write("%d%s%s --> %s%s%s%s%s" % (n, nl, stamp(start), stamp(end),
                                              nl, two_lines(text), nl, nl))
    os.replace(tmp, out)


def into_swedish(lines, times=None, out=None):
    """English to Swedish, a cue at a time, with NLLB."""
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    name = plain_copy("facebook/nllb-200-distilled-1.3B", "tokenizer.json")
    tok = AutoTokenizer.from_pretrained(name, src_lang="eng_Latn")
    model = AutoModelForSeq2SeqLM.from_pretrained(
        name, dtype=torch.float16).to("cuda").eval()
    swedish = tok.convert_tokens_to_ids("swe_Latn")
    done = []
    # in batches, because one cue at a time spends its life starting the model up
    for at in range(0, len(lines), 16):
        batch = lines[at:at + 16]
        got = tok(batch, return_tensors="pt", padding=True,
                  truncation=True, max_length=200).to("cuda")
        with torch.no_grad():
            said = model.generate(**got, forced_bos_token_id=swedish,
                                  max_new_tokens=200, num_beams=4)
        done += tok.batch_decode(said, skip_special_tokens=True)
        # what is finished is on disk: somebody watching now gets the first half
        # rather than nothing until the whole thing is done
        if out and times:
            write_srt(out, times[:len(done)], done)
        say(-1, "putting it into Swedish, %d of %d lines" % (len(done), len(lines)))
    return done


def how_long(video):
    """The film's length in seconds, from ffprobe, or 0 if it will not say."""
    import subprocess
    ffmpeg = os.environ.get("PALLADIUM_FFMPEG") or "ffmpeg"
    probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
    if not os.path.exists(probe):
        probe = "ffprobe"
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", video],
                             capture_output=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return float((out.stdout or b"0").decode().strip() or 0)
    except Exception:
        return 0.0


#: 16 kHz, one channel, two bytes a sample - what the model wants and what ffmpeg is
#: told to write. A second of film is 32000 bytes of it.
RATE, WIDTH, HEADER = 16000, 2, 44


def start_reading(video):
    """Begin decoding the whole soundtrack into one growing wave file.

    One pass, not a seek per stretch: the film is read off the disk once, in order,
    which is the difference between three minutes for seven and seven minutes for the
    lot. The file grows while the model works through what is already in it.
    """
    import subprocess
    import tempfile
    ffmpeg = os.environ.get("PALLADIUM_FFMPEG") or "ffmpeg"
    wav = os.path.join(tempfile.gettempdir(), "palladium-hear-%d.wav" % os.getpid())
    proc = subprocess.Popen(
        [ffmpeg, "-v", "error", "-y", "-i", video,
         # only the soundtrack: no pictures, no subtitles, no chapters to demux
         "-vn", "-sn", "-dn", "-map", "0:a:0?",
         "-ac", "1", "-ar", str(RATE), "-f", "wav", wav],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc, wav


def heard_so_far(wav):
    """How many seconds of sound are in the file at this moment."""
    try:
        return max(0.0, (os.path.getsize(wav) - HEADER) / float(RATE * WIDTH))
    except OSError:
        return 0.0


def take(wav, start, length):
    """One stretch of the growing file, as the model wants it: floats, one channel."""
    import numpy
    with open(wav, "rb") as f:
        f.seek(HEADER + int(start * RATE) * WIDTH)
        raw = f.read(int(length * RATE) * WIDTH)
    if not raw:
        return None
    return numpy.frombuffer(raw, dtype=numpy.int16).astype(numpy.float32) / 32768.0


#: The two big pieces of machinery this can use, what they are called, and the file
#: that proves a copy is finished. Neither ships with Palladium: they are fetched when
#: somebody turns them on, because together they are eight gigabytes.
ADDONS = {
    "speech": ("Systran/faster-whisper-" + MODEL, "model.bin"),
    "translate": ("facebook/nllb-200-distilled-1.3B", "tokenizer.json"),
}


def fetch(which):
    """Download one of the big pieces and put it where the work will look for it."""
    repo, mark = ADDONS[which]
    say(0.05, "fetching " + which)
    where = plain_copy(repo, mark)
    print("OK %s" % where, flush=True)
    return 0


def main():
    if sys.argv[1] == "--fetch":
        return fetch(sys.argv[2])
    video, want, out = sys.argv[1], sys.argv[2].lower()[:2], sys.argv[3]
    if not os.path.exists(video):
        print("NO the file is not there", flush=True)
        return 1
    say(0.01, "loading the model")
    from faster_whisper import WhisperModel
    model = WhisperModel(model_here(), device="cuda", compute_type="float16")

    whole = how_long(video)
    began = time.time()
    times, words = [], []
    spoken = ""
    at = 0.0
    reader, wav = start_reading(video)
    try:
        while True:
            # wait for a stretch to arrive, or for the decoder to finish what is left
            ready = heard_so_far(wav)
            while ready < at + STRETCH and reader.poll() is None:
                say(min(0.97, (ready / whole) if whole else 0.02),
                    "reading the sound, %d of %d minutes"
                    % (ready / 60, (whole or 0) / 60))
                time.sleep(3)
                ready = heard_so_far(wav)
            if ready <= at + 1:
                break                      # nothing more is coming
            sound = take(wav, at, min(STRETCH, ready - at))
            if sound is None or not len(sound):
                break
            task = ("translate" if (want == "en" and spoken not in ("", "en"))
                    else "transcribe")
            got, info = model.transcribe(
                sound, task=task, language=spoken or None, beam_size=5,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 400,
                                "speech_pad_ms": 120},
                # when each word was spoken, so a long stretch of speech becomes
                # several lines in time with the film rather than one that sits there
                word_timestamps=True,
                condition_on_previous_text=False)
            fresh_times, fresh_words = [], []
            for seg in got:
                for (begins, ends), line in cut_into_cues(seg):
                    fresh_times.append((at + begins, at + ends))
                    fresh_words.append(line)
                if whole and len(fresh_words) % 10 == 0:
                    say(min(0.97, (at + seg.end) / whole),
                        "listening, %d lines" % (len(words) + len(fresh_words)))
            fresh_times = tidy_times(fresh_times)
            if not spoken:
                spoken = (info.language or "").lower()[:2]
                if want == "en" and spoken != "en":
                    # English from another language: this stretch has to be heard
                    # again as a translation, and every one after it comes back
                    # translated
                    got, info = model.transcribe(
                        sound, task="translate", beam_size=5, vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 400,
                                "speech_pad_ms": 120},
                        word_timestamps=True,
                        condition_on_previous_text=False)
                    fresh_times, fresh_words = [], []
                    for seg in got:
                        for (begins, ends), line in cut_into_cues(seg):
                            fresh_times.append((at + begins, at + ends))
                            fresh_words.append(line)
                    fresh_times = tidy_times(fresh_times)
                    spoken = "en"
            if want != spoken and fresh_words:
                if want != "sv" or spoken != "en":
                    print("NO %s subtitles cannot be made from %s speech"
                          % (want, spoken), flush=True)
                    return 1
                fresh_words = into_swedish(fresh_words)
            times += fresh_times
            words += fresh_words
            # what is finished is on disk: the film can be watched from the start
            # while the rest of it is still being heard
            if words:
                write_srt(out, times, words)
            at += STRETCH
    finally:
        if reader.poll() is None:
            reader.kill()
        try:
            os.remove(wav)
        except OSError:
            pass

    if not words:
        print("NO nothing was said in this file", flush=True)
        return 1
    say(0.99, "writing it out")
    write_srt(out, times, words)
    print("OK %s %d lines in %.0f s" % (out, len(words), time.time() - began),
          flush=True)
    return 0



if __name__ == "__main__":
    sys.exit(main())
