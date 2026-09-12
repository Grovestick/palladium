package se.palladium.tv

import org.json.JSONObject

/**
 * One row from the library: a film, a show, a season or an episode.
 *
 * Codec facts come along with it because the playback decision is made on the device -
 * the Streamer decodes far more than a browser, so most files never need the server.
 */
/**
 * One subtitle track, as the server describes it.
 *
 * A file beside the film or a stream inside it; whether somebody has watched the film
 * through with it, and whether it is the one this viewer chose last time. The player
 * needs the first two to play it and the rest to say so.
 */
/** One soundtrack inside the file: which stream it is, and how to describe it. */
data class AudioTrack(
    val index: Int,              // its number in the file, as ffmpeg counts them
    val codec: String,
    val channels: Int,
    val language: String,
    val title: String,
    //: what plays when nobody has chosen - the server's answer, not the file's first
    val standard: Boolean = false,
) {
    /** "English 5.1 (AC-3)", or as much of it as the file admits to. */
    val label: String
        get() {
            val lang = when (language.lowercase().take(3)) {
                "eng" -> "English"; "swe" -> "Swedish"; "nor" -> "Norwegian"
                "dan" -> "Danish"; "fin" -> "Finnish"; "ger", "deu" -> "German"
                "fre", "fra" -> "French"; "spa" -> "Spanish"; "ita" -> "Italian"
                "por" -> "Portuguese"; "pol" -> "Polish"; "rus" -> "Russian"
                "jpn" -> "Japanese"; "kor" -> "Korean"; "chi", "zho" -> "Chinese"
                "nld", "dut" -> "Dutch"; "und", "" -> ""
                else -> language.uppercase()
            }
            val ch = when (channels) {
                6 -> "5.1"; 8 -> "7.1"; 2 -> "stereo"; 1 -> "mono"; else -> ""
            }
            val kind = codec.uppercase().replace("EAC3", "E-AC-3").replace("AC3", "AC-3")
            return listOf(title, lang, ch, if (kind.isEmpty()) "" else "($kind)")
                .filter { it.isNotEmpty() }.joinToString("  \u00b7  ")
                .ifEmpty { "Soundtrack" }
        }
}

//: The subtitle that is still being written from a film's sound. It is not a track
//: yet - there is no file for the first minute - but it can be chosen, and it becomes
//: the real track as soon as there is one.
const val PENDING_SUB = Int.MIN_VALUE

//: "No subtitle at all", as against "whichever the language would choose". It matches
//: no track, which is exactly what it means.
const val NO_SUBS = Int.MIN_VALUE + 1

data class SubTrack(
    val index: Int,              // negative for a file beside the video
    val codec: String,
    val label: String,           // the release it came from, or its language
    val language: String = "",   // "eng", "ger" - what the container says
    val forced: Boolean = false, // only the foreign lines and the signs
    val sdh: Boolean = false,    // and the door slamming
    val external: Boolean = false,
    //: its last line lands well before the film ends: still being written, or cut short
    val short: Boolean = false,
    val confirmed: Boolean = false,
    val picked: Boolean = false,
) {
    /** What to call it on screen: the language first, then what kind it is. */
    fun shown(): String {
        val name = languageName(language)
        val kind = listOfNotNull(if (forced) "forced" else null,
                                 if (sdh) "SDH" else null).joinToString(" \u00b7 ")
        // What the release called it, minus the words already said: "European
        // (Forced)" keeps European, which tells one Spanish track from another;
        // "Forced" on its own adds nothing.
        val own = label
            .replace(Regex("""(?i)\b(forced|sdh|cc|hearing impaired)\b"""), "")
            .replace(Regex("""[()\[\]]"""), "")
            .trim(' ', '-', '·', ',')
            .takeIf {
                it.isNotEmpty() && !it.equals(name, true) && !it.equals(language, true)
            }
        return listOfNotNull(name.takeIf { it.isNotEmpty() },
                             kind.takeIf { it.isNotEmpty() },
                             own).joinToString("  \u00b7  ")
            .ifEmpty { label.ifEmpty { "Track" } }
    }
}

/** "eng" as English, and the dozen others a release actually carries. */
fun languageName(tag: String): String = when (tag.lowercase()) {
    "eng", "en" -> "English"
    "swe", "sv" -> "Swedish"
    "ger", "deu", "de" -> "German"
    "fre", "fra", "fr" -> "French"
    "spa", "es" -> "Spanish"
    "ita", "it" -> "Italian"
    "dut", "nld", "nl" -> "Dutch"
    "dan", "da" -> "Danish"
    "nor", "nb", "no" -> "Norwegian"
    "fin", "fi" -> "Finnish"
    "pol", "pl" -> "Polish"
    "por", "pt" -> "Portuguese"
    "jpn", "ja" -> "Japanese"
    "chi", "zho", "zh" -> "Chinese"
    "kor", "ko" -> "Korean"
    "rus", "ru" -> "Russian"
    "ara", "ar" -> "Arabic"
    "und", "" -> ""
    else -> tag
}

/**
 * One file of a title. A film can be held twice - a 4K copy and a 1080p one, or a
 * remux and something small enough for a phone - and everything below belongs to the
 * copy rather than to the film.
 */
data class Copy(
    val mi: Int,
    val container: String?,
    val videoCodec: String?,
    val audioCodec: String?,
    val height: Int?,
    val bitrate: Int,
    val partKey: String?,
    val fileName: String?,
    val audioChannels: Int,
    val subtitleStreams: List<SubTrack>,
    val audioStreams: List<AudioTrack>,
    val pickedSub: Int?,
    /** the file on disk, which is how a choice of copy is remembered */
    val path: String = "",
    /** the one this viewer settled on, if they have settled on one */
    val picked: Boolean = false,
) {
    /** What to call it in a list: the release if the library knows it, else the facts. */
    fun shown(): String {
        val facts = listOfNotNull(
            height?.let { if (it >= 1700) "4K" else it.toString() + "p" },
            videoCodec?.uppercase(),
            (bitrate / 1000f).takeIf { it >= 0.1f }?.let { "%.1f Mbit/s".format(it) },
        ).joinToString("  ·  ")
        return fileName?.removeSuffix(".mkv")?.removeSuffix(".mp4") ?: facts
    }

    /** The same in a pill's worth of room: the facts, never the release name. */
    fun brief(): String = listOfNotNull(
        height?.let { if (it >= 1700) "4K" else it.toString() + "p" },
        videoCodec?.uppercase(),
    ).joinToString("  ·  ").ifEmpty { container?.uppercase() ?: "file" }
}

data class Media(
    val ratingKey: String,
    val type: String,
    val title: String,
    val subtitle: String,
    val year: Int?,
    val summary: String,
    val thumb: String?,
    val durationMs: Long,
    val viewOffsetMs: Long,
    val index: Int?,
    val parentIndex: Int?,
    val grandparentTitle: String?,
    val grandparentKey: String?,      // the series, so the next episode can be found
    /** the season this episode is in, so leaving it lands where it came from */
    val parentKey: String? = null,
    val container: String?,
    val videoCodec: String?,
    val audioCodec: String?,
    val height: Int?,
    /** kilobits a second, as the library measured the file */
    val bitrate: Int = 0,
    val partKey: String?,
    /** what this copy is called on disk - the release a subtitle has to match */
    val fileName: String? = null,
    /** how far the file's own episode number is from this episode's, if it says one */
    val numberShift: Int? = null,
    val subtitleStreams: List<SubTrack>,
    val audioStreams: List<AudioTrack> = emptyList(),
    val audioChannels: Int = 2,
    val addedAt: Long = 0,
    val lastViewedAt: Long = 0,
    val watched: Boolean = false,
    val watchedEpisodes: Int = 0,      // for a series or a season
    // a film's release date, or for a series the date of its newest episode
    val released: String = "",
    // this series is being watched with subtitles, so an episode of it starts with
    // whatever subtitle file is beside it turned on
    val subsWanted: Boolean = false,
    // the subtitle chosen for this title last time, if there was one
    val pickedSub: Int? = null,
    // the release an episode of this series has been watched through with
    val subsConfirmed: String = "",
    /** lines in the tallest copy held, which is what makes a title 4K */
    val maxHeight: Int = 0,
    /** every file this title is held as, and which of them the fields above describe */
    val copies: List<Copy> = emptyList(),
    val mi: Int = 0,
) {
    /** Which server this came from; null means the one currently open. */
    var srv: Server? = null

    /** The episodes a season card on a shelf stands for, in order, where it stands
     *  for any. A shelf holds episodes and shows them rolled up into seasons; playing
     *  it plays these, not the season's own key - which answers with the programme. */
    var holds: List<String> = emptyList()

    /** The shelf this stands for on Continue watching, where it stands for one. A
     *  shuffled shelf is one row rather than one row per episode, and the row has to
     *  say so or it reads as the episode it happens to be showing. */
    var shuffle: String = ""

    /** Where the round on this shelf was left, in seconds, when it was left anywhere.
     *  A button that carries on from eight minutes in should not say Play. */
    var shelfResumeAt: Int = 0

    /** Which shelf's round is holding this place, where one is. Carried into the
     *  player so the shelf keeps the place and Next draws from that shelf. */
    var shuffleId: String = ""

    /** The library's genres for this title, for narrowing a list it is on. */
    var genres: List<String> = emptyList()

    /** A collection's saved sort and filters, as the server keeps them (JSON). */
    var shelfView: String = ""

    /** A film offered from a torrent pack: not here yet, fetched when asked for. */
    var offered: Boolean = false
    /** its download as the server last said: "", queued, downloading, done or failed */
    var offerState: String = ""
    var offerProgress: Double = 0.0
    var offerSize: Long = 0L
    /** gigabytes free on the drive downloads go to; negative when not known */
    var offerFree: Double = -1.0
    /** why it cannot be downloaded at all, when qBittorrent cannot load its pack */
    var offerRefused: String = ""
    /** while it comes in: megabits a second, and seconds left (negative when not known) */
    var offerMbit: Double = 0.0
    var offerEta: Long = -1L
    /** who asked for it, and how many downloads are ahead of it while it waits */
    var offerWho: String = ""
    var offerPlace: Int = 0
    /** the pack's releases of this film when it carries more than one: key, and what to call it */
    var offerVersions: List<Pair<String, String>> = emptyList()

    val isFolder get() = type == "show" || type == "season"

    /** The same title, described by another of its files. */
    fun asCopy(which: Int): Media {
        val copy = copies.getOrNull(which) ?: return this
        return copy(
            mi = copy.mi,
            container = copy.container, videoCodec = copy.videoCodec,
            audioCodec = copy.audioCodec, height = copy.height, bitrate = copy.bitrate,
            partKey = copy.partKey, fileName = copy.fileName,
            audioChannels = copy.audioChannels,
            subtitleStreams = copy.subtitleStreams, audioStreams = copy.audioStreams,
            pickedSub = copy.pickedSub,
        ).also { it.srv = srv }
    }

    /** How many episodes this holds, when it holds any. */
    val episodeCount: Int
        // anywhere in the line, and "episode" as well as "episodes": a season
        // now says which season it is before it says how big it is
        get() = Regex("(\\d+) episodes?").find(subtitle)?.groupValues?.get(1)
            ?.toIntOrNull() ?: 0

    /**
     * The sound, in the words a person would use for it.
     *
     * The codec alone is not the interesting part - "eac3 6" means nothing on a screen,
     * while "Dolby Digital+ 5.1" is exactly what someone wants to know before they turn
     * the amplifier on.
     */
    fun audioLine(): String {
        val codec = when ((audioCodec ?: "").lowercase()) {
            "ac3" -> "Dolby Digital"
            "eac3" -> "Dolby Digital+"
            "truehd" -> "Dolby TrueHD"
            "dts" -> "DTS"
            "dca" -> "DTS"
            "dtshd", "dts_hd", "dtshd_ma" -> "DTS-HD"
            "aac" -> "AAC"
            "mp3" -> "MP3"
            "flac" -> "FLAC"
            "opus" -> "Opus"
            "pcm", "pcm_s16le" -> "PCM"
            "" -> ""
            else -> (audioCodec ?: "").uppercase()
        }
        val layout = when {
            audioChannels >= 8 -> "7.1"
            audioChannels >= 6 -> "5.1"
            audioChannels == 2 -> "Stereo"
            audioChannels == 1 -> "Mono"
            else -> ""
        }
        return listOf(codec, layout).filter { it.isNotEmpty() }.joinToString(" ")
    }

    fun progressFraction(): Float =
        if (durationMs > 0 && viewOffsetMs > 0)
            (viewOffsetMs.toFloat() / durationMs).coerceIn(0f, 1f) else 0f

    /**
     * Can this device play the file as it is?
     *
     * Android's decoders cover H.264, HEVC, VP9 and AV1, plus AAC/AC3/E-AC3/MP3/FLAC/Opus.
     * DTS has no decoder on Google's own hardware and MPEG-4 Part 2 (Xvid/DivX) is not
     * supported by ExoPlayer, so those are the two that need the server.
     */
    /** The picture plays as it is: when only the sound cannot, the server copies the
     *  picture and encodes the sound. */
    fun videoPlaysAsIs(): Boolean =
        videoCodec?.lowercase() in setOf("h264", "avc", "hevc", "h265")

    fun canDirectPlay(): Boolean {
        val v = videoCodec?.lowercase() ?: return false
        val a = audioCodec?.lowercase() ?: ""
        val videoOk = v in setOf("h264", "avc", "hevc", "h265", "vp9", "av1", "vp8")
        val audioOk = a in setOf("aac", "ac3", "eac3", "mp3", "flac", "opus", "vorbis", "pcm",
                                 "pcm_s16le", "truehd")
        return videoOk && audioOk
    }

    /**
     * The one to open with when nobody has chosen.
     *
     * A release carries twenty tracks and the first in the file is whatever the
     * encoder put first - on one of them, a German forced track with six cues in two
     * hours, which is what an English viewer was given. In order: a file somebody put
     * beside the video on purpose, then this viewer's language in full, then their
     * language with the sounds described, then their language forced, then any full
     * track at all.
     */
    fun openWith(prefer: String): SubTrack? {
        val text = textSubs()
        if (text.isEmpty()) return null
        // The first file beside the video, not the last: the server has already put
        // them in order - one proved on this series, then the one chosen last time,
        // and anything whose last line is spoken after the film ends at the bottom.
        // Taking the last undid all of that, and on an episode with two files beside
        // it picked the one cut for another release.
        text.firstOrNull { it.index < 0 }?.let { return it }
        val want = prefer.lowercase().take(3)
        val mine = text.filter {
            val tag = it.language.lowercase()
            tag.startsWith(want.take(2)) || want.startsWith(tag.take(2)) ||
                (tag == "ger" && want == "deu") || (tag == "deu" && want == "ger")
        }
        return mine.firstOrNull { !it.forced && !it.sdh }
            ?: mine.firstOrNull { !it.forced }
            ?: mine.firstOrNull()
            ?: text.firstOrNull { !it.forced && !it.sdh }
            ?: text.firstOrNull { !it.forced }
            ?: text.firstOrNull()
    }

    /** Tracks the player can draw itself, once the server has turned them into WebVTT. */
    fun textSubs(): List<SubTrack> =
        subtitleStreams.filter {
            it.codec.lowercase() in setOf("subrip", "srt", "ass", "ssa", "mov_text",
                                          "webvtt", "vtt", "text")
        }

    /**
     * The order to offer them in.
     *
     * A file somebody went and fetched comes first - it was fetched because what was
     * in the film would not do. Then this viewer's language, then the plain track
     * before the one that describes doorbells, then whatever the container's own order
     * was.
     */
    fun inOrder(tracks: List<SubTrack>, prefer: String): List<SubTrack> {
        val want = prefer.lowercase().take(2)
        fun mine(t: SubTrack) = t.language.lowercase().take(2) == want
        return tracks.sortedWith(compareBy(
            { if (it.index < 0) 0 else 1 },          // fetched, then from the film
            { if (mine(it)) 0 else 1 },              // your language first
            { if (it.forced) 1 else 0 },             // a full track before a forced one
            { if (it.sdh) 1 else 0 },                // and before one describing sounds
            { it.index },                            // otherwise as the film has them
        ))
    }

    /** Tracks that are pictures rather than words, so only burning them in will do. */
    fun bitmapSubs(): List<SubTrack> =
        subtitleStreams.filter {
            it.codec.lowercase() in setOf("pgs", "hdmv_pgs_subtitle", "vobsub",
                                          "dvd_subtitle")
        }

    /** A bitmap track has no text to hand the player, so the server burns it in. */
    fun burnSubtitleIndex(): Int? =
        subtitleStreams.firstOrNull {
            it.codec.lowercase() in setOf("pgs", "vobsub", "dvd_subtitle")
        }?.index

    companion object {
        /** One file of a title, with its own tracks. */
        private fun copyOf(media: JSONObject?, at: Int): Copy {
            val part = media?.optJSONArray("Part")?.optJSONObject(0)
            val subs = ArrayList<SubTrack>()
            val sounds = ArrayList<AudioTrack>()
            // the track this viewer settled on for this title, offered first by the
            // server and selected here without asking again
            var chosen: Int? = null
            val streams = part?.optJSONArray("Stream")
            if (streams != null) {
                for (i in 0 until streams.length()) {
                    val s = streams.getJSONObject(i)
                    if (s.optInt("streamType") == 2 && s.has("index")) {
                        sounds.add(AudioTrack(
                            index = s.optInt("index", 0),
                            codec = s.optString("codec", ""),
                            channels = s.optInt("channels", 2),
                            language = s.optString("languageTag",
                                                   s.optString("language", "")),
                            title = s.optString("title", ""),
                            standard = s.optBoolean("selected")))
                    }
                    if (s.optInt("streamType") == 3) {
                        if (s.optBoolean("picked")) chosen = s.optInt("index", -1)
                        // the track's own name where it has one - a downloaded file
                        // carries the release it came from, which is the only thing
                        // that tells two English subtitles apart
                        // optString hands back the four letters "null" for a JSON
                        // null, which is how a nameless track came to be offered as
                        // "null" in the list
                        fun said(name: String): String =
                            s.optString(name, "").let { if (it == "null") "" else it }
                        val named = said("title").ifEmpty { said("languageTag") }
                        subs.add(SubTrack(
                            index = s.optInt("index", -1),
                            codec = s.optString("codec", ""),
                            label = named,
                            language = said("language").ifEmpty { said("languageTag") },
                            forced = s.optBoolean("forced"),
                            sdh = s.optBoolean("sdh"),
                            external = s.optBoolean("external"),
                            short = s.optBoolean("short"),
                            confirmed = s.optBoolean("confirmed"),
                            picked = s.optBoolean("picked")))
                    }
                }
            }
            return Copy(
                mi = at,
                container = media?.optString("container"),
                videoCodec = media?.optString("videoCodec"),
                audioCodec = media?.optString("audioCodec"),
                height = media?.optInt("height")?.takeIf { it > 0 },
                bitrate = media?.optInt("bitrate") ?: 0,
                partKey = part?.optString("key")?.takeIf { it.isNotEmpty() },
                fileName = part?.optString("file")?.takeIf { it.isNotEmpty() }
                    ?.substringAfterLast(Char(92))?.substringAfterLast(Char(47)),
                audioChannels = media?.optInt("audioChannels")?.takeIf { it > 0 } ?: 2,
                subtitleStreams = subs,
                audioStreams = sounds,
                pickedSub = chosen,
                path = part?.optString("file") ?: "",
                picked = media?.optBoolean("picked") == true,
            )
        }

        private fun heldBy(o: JSONObject): List<String> {
            val arr = o.optJSONArray("holds") ?: return emptyList()
            return (0 until arr.length()).map { arr.optString(it) }.filter { it.isNotEmpty() }
        }

        fun from(o: JSONObject): Media {
            val all = o.optJSONArray("Media")
            val copies = (0 until (all?.length() ?: 0)).map {
                copyOf(all?.optJSONObject(it), it)
            }
            // The copies arrive best picture first, so the first is what a title
            // plays with unless somebody has settled on another one.
            val first = copies.firstOrNull { it.picked }
                ?: copies.firstOrNull() ?: copyOf(null, 0)
            val type = o.optString("type")
            val title = when (type) {
                "episode" -> o.optString("grandparentTitle", o.optString("title"))
                // a season names its programme, and says which season underneath:
                // "a series was added" is a thing said about the programme
                "season" -> o.optString("grandparentTitle").ifEmpty {
                    o.optString("parentTitle").ifEmpty { o.optString("title") }
                }
                else -> o.optString("title")
            }
            val sub = when (type) {
                "episode" -> "S%d E%d  %s".format(o.optInt("parentIndex"), o.optInt("index"),
                                                  o.optString("title"))
                "season" -> {
                    val n = o.optInt("leafCount")
                    val held = o.optInt("shelfCount")
                    // part of a season on a shelf says so: "6 of 10" is the difference
                    // between a season that is marked and one that is half marked
                    val count = when {
                        held in 1 until n -> "$held of $n episodes"
                        n == 1 -> "1 episode"
                        else -> "$n episodes"
                    }
                    val which = o.optString("title")
                    if (which.isEmpty()) count else "$which  ·  $count"
                }
                "show" -> o.optInt("leafCount").let {
                    if (it == 1) "1 episode" else "$it episodes"
                }
                // a shelf says how much it holds where a film says its year
                "collection" -> o.optInt("leafCount").let {
                    if (it == 1) "1 title" else "$it titles"
                }
                else -> o.optInt("year").takeIf { it > 0 }?.toString() ?: ""
            }
            return Media(
                ratingKey = o.optString("ratingKey"),
                type = type,
                title = title,
                subtitle = sub,
                year = o.optInt("year").takeIf { it > 0 },
                summary = o.optString("summary"),
                thumb = o.optString("thumb").takeIf { it.isNotEmpty() && it != "null" }
                    ?: o.optString("grandparentThumb").takeIf { it.isNotEmpty() && it != "null" },
                durationMs = o.optLong("duration"),
                viewOffsetMs = o.optLong("viewOffset"),
                index = o.optInt("index").takeIf { o.has("index") },
                parentIndex = o.optInt("parentIndex").takeIf { o.has("parentIndex") },
                grandparentTitle = o.optString("grandparentTitle").takeIf { it.isNotEmpty() },
                grandparentKey = o.optString("grandparentRatingKey").takeIf { it.isNotEmpty() },
                parentKey = o.optString("parentRatingKey").takeIf { it.isNotEmpty() },
                container = first.container,
                videoCodec = first.videoCodec,
                audioCodec = first.audioCodec,
                height = first.height,
                bitrate = first.bitrate,
                partKey = first.partKey,
                fileName = first.fileName,
                // nought means the two agree; absent means the name does not say
                numberShift = if (o.has("numberShift") && !o.isNull("numberShift"))
                    o.optInt("numberShift") else null,
                subtitleStreams = first.subtitleStreams,
                audioStreams = first.audioStreams,
                audioChannels = first.audioChannels,
                addedAt = o.optLong("addedAt"),
                lastViewedAt = o.optLong("lastViewedAt"),
                watched = o.optInt("viewCount") > 0,
                watchedEpisodes = o.optInt("viewedLeafCount"),
                // optString hands back the four letters "null" for a JSON null, which
                // is how a series with no aired episode came to be listed as
                // "last aired null" - and sorted as though that were a date
                released = o.optString("originallyAvailableAt", "")
                    .let { if (it == "null") "" else it },
                subsWanted = o.optBoolean("subsWanted", false),
                subsConfirmed = o.optString("subsConfirmed", ""),
                pickedSub = first.pickedSub,
                maxHeight = o.optInt("maxHeight"),
                copies = copies,
                mi = first.mi,
            ).also {
                it.holds = heldBy(o)
                it.shuffle = o.optString("shuffle", "")
                it.shelfResumeAt = o.optInt("resumeAt", 0)
                it.shuffleId = o.optString("shuffleId", "")
            }
        }
    }
}
