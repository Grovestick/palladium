package se.palladium.tv

/**
 * The rules of the background check, with nothing of Android in them: what one look at
 * an address is worth, and where the library is asked from. [Reach] gathers the looks
 * and carries out the move; the rules are here so they can be run against a network
 * that drops connections (RouteTest) rather than tried on a phone.
 */
object Route {
    /** Whether a player that has come to the end goes on to another - a queue, a casual
     *  draw, the next episode - rather than closing: it then shows the loading look. */
    fun rollsOn(queued: Boolean, casual: Boolean, seriesNext: Boolean): Boolean =
        queued || casual || seriesNext

    /** Whether a notice from the server carries a title or a shuffle to open: the
     *  library's listener acts on those, and a player on screen leaves them to it. */
    fun opensATitle(play: String, shelf: String): Boolean = play.isNotEmpty() || shelf.isNotEmpty()

    /** Whether two machines' files are the one file: the same name, and the same length
     *  where both say it. */
    fun sameFile(name: String?, size: Long, playing: String?, playingSize: Long): Boolean =
        !playing.isNullOrEmpty() && name == playing && (size <= 0 || playingSize <= 0 || size == playingSize)

    /** A download's button: a cancel only in the seconds before it starts; started, it
     *  finishes and seeds, and the button says what it is doing (owner, 2026-11-07). */
    fun takingButton(mayCancel: Boolean, armed: Boolean, line: String): String =
        if (!mayCancel) line
        else if (armed) "Press again to cancel"
        else "\u2715 Cancel  \u00b7  " + line

    /** Which of the other machine's copies is the file playing here, by name; -1 for none. */
    fun sameCopy(names: List<String?>, playing: String?): Int =
        if (playing.isNullOrEmpty()) -1 else names.indexOfFirst { it == playing }

    /** Whether a machine's answer for a key means it holds the film: one only offered from a
     *  pack there is not, and the copy that has it plays instead. */
    fun heldThere(offered: Boolean): Boolean = !offered

    /** Whether this caller is the first to take notice `id`: true once, for the first. */
    fun claimOnce(last: java.util.concurrent.atomic.AtomicInteger, id: Int): Boolean {
        while (true) {
            val was = last.get()
            if (id <= was) return false
            if (last.compareAndSet(was, id)) return true
        }
    }

    /** How long the controls stay shrunk to the progress bar and the name before they go;
     *  Media3's own is two seconds (owner: 6 s 2026-11-03, 4 s 2026-11-04). */
    const val BAR_HELD_MS = 4000L

    /** Whether a search asks a server and waits for it: not one the reach check last saw
     *  down, when the asking has a grace (2026-11-05) - asleep at night, the main server
     *  held every search for fifteen seconds. Unknown is asked. */
    fun waitedFor(up: Boolean?, grace: Long): Boolean = !(grace > 0 && up == false)

    /** What a press brings up in the player on a television (owner, 2026-11-04): the
     *  progress bar and the name; the buttons with play/pause and the information line over
     *  them; the buttons alone. 0: the whole set, as the controls come up by themselves. */
    const val LOOK_BAR = 1
    const val LOOK_UPPER = 2
    const val LOOK_LOWER = 3

    /** The look after a press - "up", "down", "side" or "centre" - from `look`, 0 when the
     *  controls are away. Up or down brings the lower, anything else the bar; the lower,
     *  once up, is left to the buttons - but up from play/pause (`atTop`: the remote on the middle
     *  row, or on none of the controls) goes on to the upper; from the bar or the row below,
     *  up goes back to pause (owner, 2026-11-04). */
    /** What the name stands over: the bar, and the row of buttons too unless the bar is all
     *  that is asked for - Media3 shows the row for a frame first, and the name went up to
     *  it and came down again. */
    fun nameOver(barOnly: Boolean): List<String> = if (barOnly) listOf("bar") else listOf("bar", "row")

    /** Whether the picture is shaded and the controls grey: paused by somebody, not while
     *  it loads or waits for data (owner, 2026-11-05). */
    fun pausedLook(playWhenReady: Boolean, loading: Boolean): Boolean = !playWhenReady && !loading

    /** How much of the white the controls keep while paused: grey over the shade. */
    const val PAUSED_INK = 0.55f

    /** Whether the information line shows with the buttons: with no press asked for, or
     *  the upper; the lower and the bar keep it away (owner, 2026-11-04). */
    fun infoLineWith(look: Int): Boolean = look == 0 || look == LOOK_UPPER

    /** The look once the controls have shrunk to the bar on their own: the bar's, so a press
     *  from there starts where a press from nothing would. */
    fun lookWhenShrunk(look: Int, shrunk: Boolean): Int =
        if (shrunk && look != 0) LOOK_BAR else look

    fun lookAfter(key: String, look: Int, atTop: Boolean = false): Int = when {
        look == LOOK_LOWER -> if (key == "up" && atTop) LOOK_UPPER else LOOK_LOWER
        key == "down" -> LOOK_LOWER
        key == "up" -> if (look == LOOK_UPPER) LOOK_UPPER else LOOK_LOWER
        look == LOOK_UPPER -> LOOK_UPPER
        else -> LOOK_BAR
    }

    /** How many times a playing reopens a live encode the server refused at a byte (416). */
    const val ENCODE_REOPENS = 5

    /** Whether a playback error is filed as a fault: not a 416 on an encode that is
     *  reopened at the second reached - that is how an encode goes on after a cut. */
    fun filesPlaybackFault(encodeCut: Boolean, reopens: Int): Boolean =
        !(encodeCut && reopens < ENCODE_REOPENS)

    /** How wide a list of releases is drawn: most of the screen, so a release name fits
     *  on a phone held sideways; the platform's dialog stopped at a third of it. */
    const val PICKER_WIDTH = 0.94f
    val PICKER_MAX = 1000

    /** A search typed on Home that found films and programmes: shown in two parts. */
    /** Whether back on this tab first takes a scrolled Films or TV list to its top,
     *  Home being the press after. */
    fun backToTop(tab: String, index: Int, offset: Int): Boolean =
        tab in setOf("films", "tv") && (index > 0 || offset > 0)

    /** How many genres a widened More like this keeps, in the order tried: one fewer
     *  each time, down to two - or to one, for a film that had only two. */
    fun genreFewer(n: Int): List<Int> = ((n - 1) downTo (if (n > 2) 2 else 1)).toList()

    /** Every choice of `size` of the words, in their own order. */
    fun genreSets(words: List<String>, size: Int): List<List<String>> = when {
        size == 0 -> listOf(emptyList())
        words.size < size -> emptyList()
        else -> genreSets(words.drop(1), size - 1).map { listOf(words[0]) + it } +
            genreSets(words.drop(1), size)
    }

    /** Whether a resume is the activity's first - the app opening, which stays on the
     *  tab it opens on - rather than a return, which goes to Home. */
    fun isOpening(resumed: Int, openedAt: Int): Boolean = resumed <= openedAt

    /** Back on Home from a film: whether its shelves stay on screen while they are read
     *  again, rather than a spinner and the page drawn anew (owner, 2026-11-05). */
    fun quietReturn(tab: String, hasShelves: Boolean, searching: Boolean): Boolean =
        tab == "home" && hasShelves && !searching

    /** Whether a key went down after `since`: what arrives late does not move the remote
     *  from where somebody has taken it. */
    fun movedSince(keyAt: Long, since: Long): Boolean = keyAt > since

    /** The card just played first, the rest in their order: Continue watching as the
     *  server will have it once the film has said where it got to. */
    fun <T> toFront(list: List<T>, isIt: (T) -> Boolean): List<T> {
        val at = list.indexOfFirst(isIt)
        return if (at <= 0) list else listOf(list[at]) + list.filterIndexed { i, _ -> i != at }
    }

    /** Frames Continue watching is taken to its start over, back from a film: the first
     *  is before the row has drawn its new order, so one alone scrolled the old one. */
    const val DECK_FRONT_FRAMES = 4

    /** The shelves on screen by the name each is asked for under: the season's shelf is
     *  called what its cards say, so it is the one no other name claims. */
    fun <T> shelvesByOrder(names: List<String>, seasonal: String,
                           rows: List<Pair<String, T>>): Map<String, T> {
        val out = HashMap<String, T>()
        for ((title, list) in rows) {
            val n = if (title in names) title else seasonal
            if (n !in out) out[n] = list
        }
        return out
    }

    /** The band across a shuffle's card on Continue watching: a music round says so. */
    fun shuffleBand(shelf: String): String =
        if (shelf == "music" || shelf.startsWith("music:")) "MUSIC" else "SHUFFLE"

    /** Whether the picture plays from the file itself. AV1 only with a hardware decoder:
     *  the software one runs a 1080p picture at about 4 fps. */
    /** Whether a file is played as it is: one this device decodes, or one whose codecs
     *  were not in the details that came back - tried as it is, an encode only if the
     *  device then cannot decode it. */
    fun playAsIs(decodes: Boolean, hasPart: Boolean, videoCodec: String?): Boolean =
        decodes || (hasPart && videoCodec.isNullOrEmpty())

    fun videoDirect(codec: String, av1Hardware: Boolean): Boolean =
        codec in setOf("h264", "avc", "hevc", "h265", "vp9", "vp8") ||
            (codec == "av1" && av1Hardware)

    /** Films and programmes under their own headings: a search, or one person's page. */
    fun splitSearch(query: String, tab: String, films: Int, tv: Int,
                    person: Boolean = false): Boolean =
        (query.isNotBlank() || person) && films > 0 && tv > 0

    /** A split search on the TV tab: the programmes first, the films under them. */
    fun tvFirst(tab: String): Boolean = tab == "tv"

    /** "(3)" after the Version button when there are several copies, nothing for one. */
    fun copiesSaid(copies: Int): String = if (copies > 1) "  ($copies)" else ""

    /** A file worth ripping to 1080p: named a remux, or carrying a Blu-ray's bitrate (kbit/s). */
    fun isRemux(fileName: String?, bitrateKbps: Int): Boolean =
        (fileName ?: "").contains("remux", ignoreCase = true) || bitrateKbps >= 40_000

    /** The rip button's word: what pressing starts, or how far the rip of this file is. */
    fun ripWord(state: String?, progress: Double, confirming: Boolean = false,
                profile: String = ""): String = when {
        confirming -> "Cancel the rip?"
        state == null -> "Rip" + (if (profile.isNotEmpty()) " · $profile" else "")
        state == "queued" -> "Rip queued"
        state == "done" -> "Rip made"
        else -> "Ripping " + (progress * 100).toInt() + "%"
    }

    /** Whether a title's page can list other releases to download: films and episodes. */
    fun hasOtherVersions(type: String?): Boolean = type == "movie" || type == "episode"

    /** Which title "play <words>" names: the one held title spelt exactly as said,
     *  letters and digits only; null for none, or for two - that is a choice to show. */
    fun spokenMatch(words: String, titles: List<Pair<String, Boolean>>): Int? {
        val bare = { t: String -> t.lowercase().filter { it.isLetterOrDigit() } }
        val said = bare(words)
        if (said.isEmpty()) return null
        val hits = titles.withIndex().filter { (_, t) -> t.second && bare(t.first) == said }
        return hits.singleOrNull()?.index
    }

    /** Whether the search box fills what is left of the top line rather than standing
     *  at its fixed width: a phone held upright, where 190 points pushed the
     *  microphone off the screen. */
    fun searchFills(tv: Boolean, widthDp: Int): Boolean = !tv && widthDp < 600

    /** A title page still waiting on the tracker asks again this often, this many
     *  times: a minute, which is longer than a first search takes. */
    const val CHECK_EVERY_MS = 5_000L
    const val CHECK_TRIES = 12

    /** The wait before the next look: every five seconds for that minute, then every
     *  thirty while the page stays open - it says Checking until the tracker has answered. */
    fun checkGap(tries: Int): Long = if (tries < CHECK_TRIES) CHECK_EVERY_MS else 30_000L

    /** The seasons a pack carries, as a pack is named: "S02", "S01–S22", or "". */
    fun seasonSpan(seasons: List<Int>): String = when {
        seasons.isEmpty() -> ""
        seasons.size == 1 -> "S%02d".format(seasons[0])
        else -> "S%02d–S%02d".format(seasons.first(), seasons.last())
    }

    /** How soon a search is asked again while the catalogue answers behind it. */
    const val FILL_IN_MS = 1_500L

    /** How long a search waits for the tracker before it is asked again. */
    const val SEARCH_AGAIN_MS = 8_000L
    /** And how many times: a box set found for a film is read seconds after the search. */
    const val SEARCH_AGAIN_TIMES = 3

    /** The server's choice of subtitle to open with; [index] null is none. */
    class SubChoice(val index: Int?)

    /** What the server decided among these tracks, or null from a server that does not. */
    fun serverSub(text: List<SubTrack>): SubChoice? =
        if (text.none { it.decided }) null else SubChoice(text.firstOrNull { it.start }?.index)

    /**
     * The subtitle an episode that follows on by itself opens with, when one is wanted:
     * the server's choice, as pressing Next gets. Chosen by the closest file name, a
     * Swedish file sharing more of the episode's words opened for an English reader.
     * [own] is the app's rule, for a server that does not decide.
     */
    fun followOnSub(wanted: Boolean, text: List<SubTrack>, own: () -> Int?): Int? =
        if (!wanted) null else serverSub(text).let { if (it != null) it.index else own() }

    /** How many looks in a row must miss before an address is called down. */
    const val MISSES = 2

    /**
     * What one address said the last time it was asked. [misses] counts looks that
     * failed since the last answer; an address is down only at [MISSES], and until
     * then keeps what it was - up, or not known.
     */
    data class Look(val up: Boolean, val id: String, val ms: Long, val at: Long,
                    val follows: String = "", val followsId: String = "",
                    val misses: Int = 0) {
        val down get() = !up && misses >= MISSES
    }

    /** One look that got no answer. */
    fun miss(now: Long, id: String = "") = Look(false, id, 0, now, misses = 1)

    /** An address that cannot be there at all: a house address from another network. */
    fun never(now: Long, id: String = "") = Look(false, id, 0, now, misses = MISSES)

    /**
     * What is believed after a look: an answer is believed at once, a miss on the
     * second in a row. One lost connection is the network, not the server.
     */
    fun settle(had: Look?, raw: Look, now: Long, freshMs: Long): Look {
        if (raw.up || raw.down) return raw
        if (had == null || now - had.at >= freshMs) return raw
        val n = had.misses + 1
        return when {
            // was up: still up after one miss, with when it last answered kept
            had.up -> if (n >= MISSES) raw.copy(id = had.id, misses = n)
                      else had.copy(misses = n)
            else -> raw.copy(id = had.id, misses = minOf(n, MISSES))
        }
    }

    /**
     * What a request that could not connect is worth, or null for nothing.
     *
     * At most the first miss; the second is a look's to give. A phone waking sends
     * six requests into a network that is not there yet, and all six time out
     * together eight seconds later - after the check has seen the server answer.
     * Counted one each, they were two misses at once and the library moved to the
     * copy from a server that was up.
     */
    fun requestMiss(had: Look?, began: Long, now: Long, freshMs: Long): Look? {
        if (had != null && now - had.at < freshMs) {
            // answered since this request set out: its failure is older than the answer
            if (had.up && had.misses == 0 && had.at >= began) return null
            // one miss is counted, or it is down already
            if (had.misses >= 1) return null
        }
        return settle(had, miss(now, had?.id.orEmpty()), now, freshMs)
    }

    /** Up, down, or not known: null while a first miss is waiting for a second look. */
    fun state(d: Look?, now: Long, freshMs: Long): Boolean? = when {
        d == null || now - d.at >= freshMs -> null
        d.up -> true
        d.down -> false
        else -> null
    }

    /** The address of a row that answers as that machine, the near one first. */
    fun door(ways: List<String>, looks: Map<String, Look>, rowId: String, now: Long,
             freshMs: Long, near: (String) -> Boolean): String? =
        ways.filter { w ->
            val d = looks[w]
            d != null && now - d.at < freshMs && d.up &&
                (rowId.isEmpty() || d.id.isEmpty() || d.id == rowId)
        }.minByOrNull { w -> (if (near(w)) 0 else 100_000) + (looks[w]?.ms ?: 0) }

    /** A row: up on some address, down on all of them, or not known. */
    fun rowUp(ways: List<String>, looks: Map<String, Look>, rowId: String, now: Long,
              freshMs: Long, near: (String) -> Boolean): Boolean? {
        if (door(ways, looks, rowId, now, freshMs, near) != null) return true
        return if (ways.isNotEmpty() &&
                   ways.all { state(looks[it], now, freshMs) == false }) false else null
    }

    /**
     * The row that is open, from the address written down for it: the row filed
     * under it; the row that has it as its other address, when the row was filed
     * again under the first; else the first that is not a machine keeping copies.
     * An address that named no row used to mean the first row, whichever that was.
     */
    fun chosen(rows: List<Server>, want: String): Server? {
        if (rows.isEmpty()) return null
        val w = want.trimEnd('/')
        return rows.firstOrNull { it.base.trimEnd('/') == w }
            ?: rows.firstOrNull { w.isNotEmpty() && it.outside.trimEnd('/') == w }
            ?: rows.firstOrNull { it.copyOf.isEmpty() }
            ?: rows.first()
    }

    /**
     * Whether whoever holds this key is the owner, from what the server says of it:
     * not a guest, or a guest only because the key is being used away from the house -
     * which the server says by listing what the owner's key still opens from there.
     */
    fun ownersKey(guest: Boolean, ownerAway: Boolean): Boolean = !guest || ownerAway

    /**
     * Where a title starts, in seconds: past its skip rule on a fresh start from 0,
     * the place asked for otherwise. A restart inside the player is not a fresh start.
     */
    /** How long the player waits before calling a soundtrack one it cannot play. */
    const val SOUND_FIRST_MS = 2500L
    /** On screen this long first: back from Home, the HDMI sound is taken up anew. */
    const val SOUND_SETTLE_MS = 10000L
    /** And silent again this much later before the film is re-encoded. */
    const val SOUND_AGAIN_MS = 5000L

    /** Milliseconds to wait before judging the sound, the player having been back on
     *  screen for backMs. */
    fun soundJudgedAfter(backMs: Long): Long =
        maxOf(SOUND_FIRST_MS, SOUND_SETTLE_MS - maxOf(0L, backMs))

    fun leadStart(positionSec: Long, skipStart: Double, fresh: Boolean): Long =
        if (fresh && positionSec <= 0L && skipStart > 0.0) Math.round(skipStart) else positionSec

    /**
     * Whether the player asks for the receiver's raise now: a television passing sound
     * through, on screen, not already asked. Off screen never - a progress report
     * still in flight when Home is pressed must not raise it again.
     */
    fun raiseReceiver(raised: Boolean, onScreen: Boolean, tv: Boolean, passing: Boolean): Boolean =
        !raised && onScreen && tv && passing

    /** How long after the first picture the sound's route is taken as known. */
    const val SOUND_SETTLES_MS = 3000L

    /** Whether the sink has had time to say how it takes the sound: this long since
     *  the first picture of what is being read; -1 while there is none. */
    fun soundSettled(sincePictureMs: Long): Boolean = sincePictureMs > SOUND_SETTLES_MS

    /**
     * Whether the sound goes to the amplifier as it is: not cast, never through the
     * processor here, and settled. Direct play or encode alike. Counted from the
     * picture, not the film's position: a resume at 222 s that took 10 s to start
     * was past "3 s in" before any sound existed, and decoded AAC raised the receiver.
     */
    fun passesSound(onCast: Boolean, decodedHere: Boolean, sincePictureMs: Long): Boolean =
        !onCast && !decodedHere && soundSettled(sincePictureMs)

    /** What a poster says of its download: how far, and how fast while anything is
     *  arriving. The time left and the version are on the download line and the
     *  title's page. */
    fun posterDownload(state: String, progress: Double, mbit: Double = 0.0): String = when (state) {
        "downloading" -> "" + (progress * 100).toInt().coerceIn(0, 100) + "%" +
            (if (mbit >= 0.5) " · " + Math.round(mbit) + " Mbit/s" else "")
        "queued" -> "Queued"
        "unpacking" -> "Unpacking " + (progress * 100).toInt().coerceIn(0, 100) + "%"
        "done" -> "Arriving"
        else -> ""
    }

    /** What a title's page says of its download while it comes in: the version, how far,
     *  how fast and how long is left, or its place in the queue. */
    fun offerLine(state: String, version: String, progress: Double, mbit: Double,
                  eta: Long, place: Int, words: String = ""): String =
        // the server's words when it has them: they say looking for seeders, checking,
        // adding it - where a per cent alone said "Downloading 0%"
        if (words.isNotEmpty()) words
        else if (state == "queued") "Queued" + (if (place > 0) " · $place ahead" else "")
        else "Downloading " + (if (version.isNotEmpty()) "$version · " else "") +
            (progress * 100).toInt().coerceIn(0, 100) + "%" +
            (if (mbit >= 0.5) String.format(java.util.Locale.US, " · %.0f Mbit/s", mbit) else "") +
            (if (eta >= 0) " · " + when {
                eta < 60 -> "$eta s left"
                eta < 3600 -> "${eta / 60} min left"
                else -> "${eta / 3600} h ${eta % 3600 / 60} min left"
            } else "")

    /** The health page's heading from how many things need somebody. */
    fun healthTitle(problems: Int): String = when (problems) {
        0 -> "All good"
        1 -> "1 thing needs you"
        else -> "$problems things need you"
    }

    /** How long ago, said short: "just now", "12 min ago", "3 h ago", "2 days ago". */
    fun agoWords(seconds: Long): String = when {
        seconds < 60 -> "just now"
        seconds < 3600 -> "${seconds / 60} min ago"
        seconds < 172_800 -> "${seconds / 3600} h ago"
        else -> "${seconds / 86_400} days ago"
    }

    /** Whether a request that failed is worth reporting as slow: one that could not be
     *  made, or took its time - except the notice, held open on purpose, which a phone
     *  network cuts after a quiet half minute. */
    fun slowWorthSaying(path: String, ms: Long, gone: Boolean): Boolean = when {
        path.startsWith("/trace") -> false
        path.startsWith("/notice") -> gone && ms <= 2500
        else -> gone || ms > 2500
    }

    /** A short line - the strip under the tabs, the queue - in the server's words while it is
     *  not coming in yet: looking for seeders, checking, stuck. The per cent otherwise. */
    fun shortLine(stage: String, words: String, otherwise: String): String =
        if (words.isNotEmpty() && stage !in setOf("", "downloading", "queued")) words
        else otherwise

    /** A tracker download's line after "Cancel": the server's words, else the per cent. */
    fun takingLine(state: String, progress: Double, mbit: Double, words: String): String {
        if (words.isNotEmpty()) return words
        val pct = (progress * 100).toInt()
        return (if (state.startsWith("queued") || state.startsWith("stalledDL") && pct == 0)
                    "Queued" else "Downloading $pct%") +
            (if (mbit > 0) String.format(java.util.Locale.US, "  ·  %.1f Mbit/s", mbit) else "")
    }

    /** From a press of Next or an episode's end: black over the picture from here. */
    const val COVER_MS = 400L
    /** And the words on it only from here: a changeover of half a second showed them. */
    const val WORDS_MS = 2000L

    /** What the wait for the next title shows: 0 nothing, 1 black, 2 black with words. */
    fun changeoverShows(waitedMs: Long): Int =
        if (waitedMs >= WORDS_MS) 2 else if (waitedMs >= COVER_MS) 1 else 0

    /** The next title's details are read ahead this close to the end, */
    const val READ_AHEAD_BEFORE_MS = 30_000L
    /** and used at the changeover only this fresh: a subtitle may have come since. */
    const val READ_AHEAD_FRESH_MS = 90_000L

    /** Whether details read ahead stand for the title now starting: the same title
     *  from the same server, read no longer ago than READ_AHEAD_FRESH_MS. */
    fun readAheadFits(heldKey: String, heldFrom: String, wantedKey: String,
                      wantedFrom: String, ageMs: Long): Boolean =
        heldKey.isNotEmpty() && heldKey == wantedKey && heldFrom == wantedFrom &&
            ageMs in 0L..READ_AHEAD_FRESH_MS

    /** What the information line says of a receiver that was not raised: the reason
     *  where it is a fault of the receiver, nothing where the receiver is not this
     *  screen's business - a viewer outside the house, another screen in it, or no
     *  answer at all. */
    fun receiverRefusal(answered: Boolean, why: String): String =
        if (!answered || why == "off" || why == "not in the house" ||
            why == "not the device set for the receiver") "" else "receiver: $why"

    /**
     * Where the Monitor page is opened, in the order tried: the server the app is on,
     * the same by the address its name last had, then the machine keeping copies - near
     * address first. Any the background check has seen down go last rather than first.
     * The page was opened on the main server alone, and with that machine away the app
     * showed the web view's own error picture while the copy was answering.
     */
    fun monitorOrder(base: String, byAddress: String?, standby: String, standbyOut: String,
                     down: (String) -> Boolean): List<String> {
        val all = listOf(base, byAddress.orEmpty(), standby, standbyOut)
            .map { it.trimEnd('/') }.filter { it.isNotEmpty() }.distinct()
        return all.filter { !down(it) } + all.filter { down(it) }
    }

    /** Whether a title was made in any of the marked countries, comma-joined; nothing
     *  marked is every country. */
    fun madeIn(countries: List<String>, marked: String): Boolean {
        val want = marked.split(",").map { it.trim().uppercase() }.filter { it.isNotEmpty() }
        return want.isEmpty() || countries.any { it.uppercase() in want }
    }

    /** "KR" as "South Korea", in English. */
    fun countryName(code: String): String =
        java.util.Locale("", code.uppercase()).getDisplayCountry(java.util.Locale.ENGLISH)
            .ifEmpty { code }

    /** Orders that start with the highest: newest added, best picture, most popular,
     *  best rated. The rest start at the bottom - A first, oldest first. */
    val SORT_HIGH_FIRST = setOf("addedAt", "quality", "popularity", "rating")

    /** The band on a series not held here: ADD where it can be downloaded, CHECKING while
     *  the tracker is asked, REQUEST where nothing carries it; "" for a held one. */
    fun seriesBand(addable: Boolean, onTracker: Boolean, checking: Boolean): String = when {
        !addable -> ""
        checking -> "CHECKING"
        !onTracker -> "REQUEST"
        else -> "ADD"
    }

    /** The key something played from SVT Play keeps its place under: "sv" + its video id. */
    fun svtKey(id: String): String = "sv" + id

    /** Whether pressing a card plays it from SVT Play at once: a card of something played
     *  from there (Continue watching), not a catalogue film whose page offers it. */
    fun svtPlaysAtOnce(key: String, svtId: String): Boolean =
        key.startsWith("sv") && svtId.isNotEmpty()

    /** Whether a poster wears the Stream band: not held here, and SVT Play has it. */
    fun streamBand(askable: Boolean, addable: Boolean, svtId: String): Boolean =
        (askable || addable) && svtId.isNotEmpty()

    /** What an SVT Play stream that will not play says: away from Sweden SVT refuses
     *  it, which reaches the player as a bad answer to the first piece. */
    fun svtFailure(code: Int): String = when (code) {
        2004 -> "SVT Play refused the stream - it plays in Sweden only"
        in 2000..2999 -> "SVT Play could not be reached"
        else -> "SVT Play's stream would not play here"
    }

    /** The Service menu's two of this house: what is on its disks, what its packs carry. */
    const val SRC_MINE = "-1"
    const val SRC_PACKS = "-2"
    /** both marked: how Films and TV open */
    const val SRC_HOUSE = "-1,-2"

    /** What a Service menu's marks stand for: the services' ids and this house's two.
     *  Nothing marked is everything - this house and every service. */
    data class Sources(val services: List<String>, val mine: Boolean, val packs: Boolean,
                       val everything: Boolean) {
        /** a catalogue to read: a service marked, or nothing marked at all */
        val catalogue: Boolean get() = everything || services.isNotEmpty()
    }

    fun sources(marked: String): Sources {
        val all = marked.split(",").map { it.trim() }.filter { it.isNotEmpty() }
        val none = all.isEmpty()
        return Sources(all.filter { (it.toIntOrNull() ?: 0) > 0 },
                       none || SRC_MINE in all, none || SRC_PACKS in all, none)
    }

    /** Whether a title of the tab stays under these marks: on disk for My library, in a
     *  pack for In packs. Both marked keep everything, what can be asked for included. */
    fun keptByHouse(offered: Boolean, askable: Boolean, mine: Boolean, packs: Boolean): Boolean =
        if (mine && packs) true else if (offered) packs else if (askable) false else mine

    /** Whether pressing a tab opens its popular page: TV and Films do on this house's
     *  own marks; any other marks are a catalogue of their own. */
    fun tvOpensPopular(tab: String, service: String): Boolean =
        (tab == "tv" || tab == "films") && service == SRC_HOUSE

    /** A streaming service's catalogue in the popular page's orders. */
    val SERVICE_ORDERS = listOf("popularity" to "Popular", "rating" to "Rating",
                                "originallyAvailableAt" to "Released", "year" to "Year",
                                "titleSort" to "Name")

    /** A joined list with a series' catalogue card left out where a card it is held or
     *  carried under stands too - one machine's pack and the other's popular card. */
    fun <T> oneCardASeries(list: List<T>, title: (T) -> String, catalogue: (T) -> Boolean): List<T> {
        val flat = { t: String -> t.lowercase().replace(Regex("[^a-z0-9]+"), "") }
        val real = list.filter { !catalogue(it) }.mapTo(HashSet()) { flat(title(it)) }
        return list.filter { !catalogue(it) || flat(title(it)) !in real }
    }

    /** A list in one of the catalogue's orders, high first unless `asc`. Stable: what
     *  ties keeps the order it came in. */
    fun <T> inServiceOrder(list: List<T>, key: String, asc: Boolean, pop: (T) -> Double,
                           rating: (T) -> Double, released: (T) -> String, year: (T) -> Int,
                           name: (T) -> String): List<T> {
        val by: Comparator<T> = when (key) {
            "popularity" -> compareBy(pop)
            "rating" -> compareBy(rating)
            "originallyAvailableAt" -> compareBy(released)
            "year" -> compareBy(year)
            else -> compareBy { name(it).lowercase() }
        }
        return list.sortedWith(if (asc) by else by.reversed())
    }

    /** The orders a shelf opened whole offers instead of the library's own: the
     *  popular shows sort by how popular and how well rated as well; empty for the rest. */
    /** Last watched first (or last), and what was never watched in the list's own order
     *  whichever way it is turned, as the web keeps it. */
    fun <T> byLastWatched(list: List<T>, asc: Boolean, at: (T) -> Long): List<T> =
        if (asc) list.sortedBy(at) else list.sortedByDescending(at)

    /** How far ahead of the picture subtitles being written must be before they are put on. */
    const val MADE_LEAD_MS = 30_000L

    /** Whether subtitles written from the sound have got past where the film is, with a lead:
     *  `at` the share of the film written. Finished, or no length known, they have. */
    fun madeReachesHere(at: Float, on: Boolean, durationMs: Long, positionMs: Long): Boolean =
        !on || durationMs <= 0 || at * durationMs >= positionMs + MADE_LEAD_MS

    /** Where a title stands in the watchlist's order: the later of last watched and put on
     *  it, so one just added or downloaded comes first. */
    fun watchlistAt(lastViewedAt: Long, listedAt: Long): Long = maxOf(lastViewedAt, listedAt)

    /** The order the watchlist opens in, and the direction: last watched or added first. */
    val WATCHLIST_OPENS = "lastViewedAt" to false

    /** The watchlist's orders: last watched first, then the library's own. */
    val WATCHLIST_ORDERS = listOf("lastViewedAt" to "Watched or added", "addedAt" to "Added",
                                  "originallyAvailableAt" to "Released", "titleSort" to "Name",
                                  "year" to "Year", "quality" to "Quality")

    fun extraOrders(row: String?, list: Boolean = false): List<Pair<String, String>> =
        // a kept list: its own order first, the one it opens in
        if (list)
            listOf("popularity" to "List order", "originallyAvailableAt" to "Released",
                   "year" to "Year", "titleSort" to "Name", "rating" to "Rating")
        else if (popularPage(row))
            listOf("popularity" to "Popular", "originallyAvailableAt" to "Released",
                   "year" to "Year", "titleSort" to "Name", "rating" to "Rating")
        else emptyList()

    /** How far grids and shelves stop short of a television's edges, in dp: the sides
     *  and the bottom. A TV trims its edges, and a ring at the very edge was cut off.
     *  Nothing on a phone, where the screen shows all of itself. */
    fun tvSafe(tv: Boolean): Pair<Int, Int> = if (tv) 24 to 20 else 0 to 0

    /** Where Start's list moves for a focused card: its top this far under the list's
     *  top - room for the row's title - the same place every time. Null for anything
     *  smaller than a card (the picture's buttons) or taller than half the screen (the
     *  picture itself): those keep scrolling only as far as they must. */
    /** How long Start's page slides a row up or down, and a row slides along: the home
     *  screen's own, read off a recording of it at sixty frames - 468 and 318 ms. */
    const val START_SLIDE_DOWN_MS = 450
    const val START_SLIDE_ALONG_MS = 300

    /** How far a Home row scrolls for a card the remote lands on: nothing while the card
     *  and `room` either side of it are on screen - coming down from the row above is no
     *  reason to slide sideways - and otherwise just enough to give it that room, which
     *  for the first card is the row's own margin, back at its start. */
    fun calmAlong(offset: Float, size: Float, containerSize: Float, room: Float): Float {
        val start = offset - room
        val end = offset + size + room
        if (start >= 0f && end <= containerSize) return 0f
        if (start < 0f && end > containerSize) return 0f
        return if (start < 0f) start else end - containerSize
    }

    /** A film page's More like this poster width on a television: as large as the room
     *  under the film's words allows - the row's name (24), the ring's room (8), the
     *  poster at 1.5 its width and `margin` under it - never smaller than the shelf
     *  it had been, nor larger than a home shelf's. `room` is from where the words end
     *  to the foot of the screen; nought, not measured yet, keeps the small size. */
    fun likeWide(room: Float, home: Int, margin: Float = 14f): Int {
        val least = home * 5 / 8
        if (room <= 0f) return least
        return ((room - 24f - 8f - margin) / 1.5f).toInt().coerceIn(least, home)
    }

    /** How many More like this posters stand in a row `width` wide and how wide each
     *  is, so the row runs edge to edge between the page's margins: the fewest that
     *  are no wider than `most` (what the room under the words allows), at most `cap`.
     *  `sides` is the air each card carries. */
    /** The width of each of ten More like this posters filling a row `width` wide,
     *  less the air each card carries: one size for the page, whatever else is on it. */
    fun likeTen(width: Float, sides: Int): Int = maxOf(40, (width / 10f).toInt() - sides)

    fun likeFit(width: Float, most: Int, sides: Int, cap: Int = 10): Pair<Int, Int> {
        if (width <= 0f || most <= 0) return cap to most
        for (n in 1..cap) {
            val w = (width / n).toInt() - sides
            if (w <= most) return n to w
        }
        return cap to ((width / cap).toInt() - sides)
    }

    /** The order a search's finds stand in on a tab, as indexes into them: all of them,
     *  the tab's own kind first - films on Films, series and episodes on TV - each
     *  group in the order the server ranked it. */
    fun tabFirst(types: List<String>, tab: String): List<Int> {
        val own: (String) -> Boolean = when (tab) {
            "films" -> { t -> t == "movie" }
            "tv" -> { t -> t == "show" || t == "episode" }
            else -> { _ -> true }
        }
        val idx = types.indices.toList()
        return idx.filter { own(types[it]) } + idx.filter { !own(types[it]) }
    }

    /** How red a collection's card is while Play shuffle is focused: 2 left out of
     *  the shuffle whole, 1 a season it leaves part of, 0 drawn from. */
    fun shuffleOff(lit: Boolean, notShuffled: Boolean, shuffleOut: Int): Int =
        if (!lit) 0 else if (notShuffled) 2 else if (shuffleOut > 0) 1 else 0

    /** An episode's releases under their headings, in order: the pack already added,
     *  the episode on its own on the tracker, then the tracker's season packs holding
     *  it. Each row is (in a pack here, in a box); the groups are row indexes, and an
     *  empty group is left out. */
    fun releaseGroups(rows: List<Pair<Boolean, Boolean>>): List<Pair<String, List<Int>>> =
        listOf("From the pack you added" to rows.indices.filter { rows[it].first },
               "The episode on its own" to rows.indices.filter { !rows[it].first && !rows[it].second },
               "In season packs on the tracker" to rows.indices.filter { !rows[it].first && rows[it].second })
            .filter { it.second.isNotEmpty() }

    /** The groups shown: with a pack added here, that pack alone until Other versions is
     *  pressed, as a film shows its copies here first. */
    fun releaseGroupsShown(rows: List<Pair<Boolean, Boolean>>, more: Boolean): List<Pair<String, List<Int>>> =
        releaseGroups(rows).let { g -> if (more || !rows.any { it.first }) g else g.take(1) }

    /** Whether the Other versions button stands under the added pack: only while the
     *  tracker's releases are hidden behind it. */
    fun moreReleases(rows: List<Pair<Boolean, Boolean>>, more: Boolean): Boolean =
        !more && rows.any { it.first } && rows.any { !it.first }

    /** The collections popup's button for one collection: Add where this is not on
     *  it or only partly, Remove where all of it is. */
    fun shelfAct(state: String): String = if (state == "all") "Remove" else "Add"

    /** The watchlist popup's line saying where a title stands. */
    fun watchlistSaid(listed: Boolean, favorite: Boolean): String = when {
        favorite -> "A favorite: on your watchlist, and kept there when watched"
        listed -> "On your watchlist"
        else -> "Not on your watchlist"
    }

    /** The black under a picture drawn to fit a view, in pixels. The shape is the file's
     *  own when known: a decoder that pads a scope film into a 16:9 frame reports 16:9,
     *  and the subtitles were placed as if the picture filled the screen. */
    fun barUnder(viewW: Int, viewH: Int, decodedAspect: Float, sourceAspect: Float): Int {
        val aspect = if (sourceAspect > 0f) sourceAspect else decodedAspect
        if (aspect <= 0f || viewW <= 0 || viewH <= 0) return 0
        val spare = viewH - viewW / aspect
        return if (spare > 1f) Math.round(spare / 2f) else 0
    }

    /** How near a subtitle comes to the top or bottom edge of the screen, as a share of
     *  its height: a television crops a few per cent off every edge (overscan). */
    const val SUB_SAFE = 0.03f
    /** The least room under a subtitle in the band under the picture: what keeps a second
     *  row from being drawn past the screen itself. */
    const val SUB_EDGE = 0.005f

    /** The rows a height setting means: Bottom/Nearest 0, Just up/One down 1,
     *  Raised/Two down 2, High 3. Very bottom (0.9 and up) is not a number of rows. */
    fun subtitleSteps(position: Float): Int = when {
        position < 0.04f -> 0
        position < 0.12f -> 1
        position < 0.22f -> 2
        else -> 3
    }

    /**
     * Where the top of a subtitle goes, as a share of the view's height from its top.
     *
     * bar is the black under the picture and text the text size, both shares of the
     * view; a row is text * lineRatio and the gap kept from the picture's edge is the
     * leading, text * (lineRatio - 1). rows are the subtitle's lines with wraps counted.
     *
     *  - Off screen ("screen") with a band under the picture that holds a row and a gap
     *    either side: the top row hugs the picture's edge - the row's own leading is the
     *    buffer - a row lower for each step (Nearest, One down, Two down), and stays there
     *    whether the subtitle has one row or two: a second row goes down into the band.
     *    It moves up only if a row would leave the screen itself (EDGE). Very bottom puts
     *    the last row on the safe edge.
     *  - On screen ("picture"), or Off screen with no such band - a film that fills the
     *    screen: the bottom sits a gap above the picture's edge, a row higher for each
     *    step (Bottom, Just up, Raised, High), at onPicture's height for Off screen.
     *    Very bottom puts the last row on the safe edge.
     *  - On the picture and at Very bottom: no row closer than SUB_SAFE to the top or
     *    bottom of the screen.
     */
    fun subtitleTop(bar: Float, text: Float, lineRatio: Float, rows: Int, base: String,
                    position: Float, onPicture: Float, safe: Float = SUB_SAFE): Float {
        val row = text * lineRatio
        val gap = text * (lineRatio - 1f)
        val tall = rows.coerceAtLeast(1) * row
        val lowest = (1f - safe - tall).coerceAtLeast(safe)
        val edge = 1f - bar
        val under = base == "screen" && bar >= row + 2f * gap
        val height = if (base == "screen" && !under) (if (onPicture >= 0f) onPicture else 0.08f)
                     else position
        if (height >= 0.9f) return lowest
        if (under) {
            // the top row where it always is; down only as far as the screen goes
            val onEdge = (1f - SUB_EDGE - tall).coerceAtLeast(safe)
            return (edge + subtitleSteps(height) * row).coerceIn(safe, onEdge)
        }
        return (edge - gap - subtitleSteps(height) * row - tall).coerceIn(safe, lowest)
    }

    /** The rows a subtitle takes: each of its lines one row, or more where its width
     *  passes the room it is drawn in and it wraps. */
    fun cueRows(lineWidths: List<Float>, room: Float): Int =
        lineWidths.sumOf { w -> if (room <= 0f) 1 else maxOf(1, kotlin.math.ceil(w / room).toInt()) }
            .coerceAtLeast(1)

    /** The line under a poster: what the caller asks for, or for a season with nothing
     *  asked its number - its picture is often the programme's own. */
    fun posterLine(type: String?, index: Int?, asked: String?, show: String? = null): String? =
        asked ?: if (type == "season" && index != null && index > 0) seasonLine(index, "", show)
                 else null

    /** A season card's line: its programme where the card stands among other things,
     *  then its number - or its own name where it has no number. */
    fun seasonLine(index: Int?, title: String, show: String?): String {
        val own = if (index != null) "Season $index" else title
        return if (show.isNullOrBlank()) own else "$show · $own"
    }

    /** Whether a download taken from the tracker is this page's: started from it, or
     *  the same film by name and year. */
    fun takingIsFor(takeKey: String, takeTitle: String, takeYear: Int,
                    key: String, title: String, year: Int?): Boolean {
        if (takeKey.isNotEmpty()) return takeKey == key
        val flat = { t: String -> t.lowercase().filter { it.isLetterOrDigit() } }
        return flat(takeTitle).isNotEmpty() && flat(takeTitle) == flat(title) &&
            (takeYear == 0 || year == null || takeYear == year)
    }

    /** A grid's columns from its width and its cells (x, width): as many as a cell fits
     *  across - a heading, the full width, is not a cell. */
    fun columnsOf(width: Int, cells: List<Pair<Int, Int>>): Int {
        val cell = cells.map { it.second }.filter { it in 1 until width * 9 / 10 }.minOrNull()
            ?: return if (cells.isEmpty()) 0 else 1
        return maxOf(1, width / cell)
    }

    /** Up or down from card `at` of a list of `size` laid out `across` a row, in a grid
     *  of lists each under its own heading: (-1, i) for card i of the list above, (0, i)
     *  in this one, (1, i) in the one below - the same column, or the last card of a
     *  shorter row - or null where there is nothing that way. */
    fun gridStep(at: Int, across: Int, size: Int, prevSize: Int, nextSize: Int,
                 down: Boolean): Pair<Int, Int>? {
        val n = maxOf(across, 1)
        val col = at % n
        if (down) {
            if (at + n < size) return 0 to at + n
            // the short last row of this list still lies below
            if ((size - 1) / n > at / n) return 0 to size - 1
            if (nextSize > 0) return 1 to minOf(col, nextSize - 1)
            return null
        }
        if (at - n >= 0) return 0 to at - n
        if (prevSize > 0) {
            val lastRow = (prevSize - 1) / n * n
            return -1 to minOf(lastRow + col, prevSize - 1)
        }
        return null
    }

    /** The watchlist's headings in order - its films, its television, then the
     *  favorites the same two ways - each with what is under it; an empty one is left
     *  out. */
    fun <T> watchlistGroups(films: List<T>, tv: List<T>, favFilms: List<T>,
                            favTv: List<T>): List<Pair<String, List<T>>> =
        listOf("Films" to films, "TV shows" to tv, "Favorite films" to favFilms,
               "Favorite TV shows" to favTv).filter { it.second.isNotEmpty() }

    /** The word for a watchlisted film that has got a source. */
    fun watchNewsSaid(title: String, how: String): String = when (how) {
        // downloaded by this viewer, and put on their watchlist by doing so
        "fetched" -> "$title is here - it's now on your watchlist"
        "here" -> "$title is here - it was on your watchlist"
        else -> "$title can be downloaded now - it was on your watchlist"
    }

    /** The Play build's credits for the data it shows: the film catalogue, and where
     *  a film streams, which the catalogue has from JustWatch. */
    const val PLAY_CREDITS =
        "Film and series information, pictures and where they stream come from TMDB. " +
        "This product uses the TMDB API but is not endorsed or certified by TMDB. " +
        "Streaming availability data by JustWatch. Service names and logos belong to " +
        "their owners; Palladium is not connected to any streaming service."

    /** Whether a card stands in the Play Store's build: only what the server holds -
     *  nothing to download, nothing to ask for. The house's build shows everything. */
    fun forStore(play: Boolean, offered: Boolean, askable: Boolean, addable: Boolean): Boolean =
        !play || !(offered || askable || addable)

    /** What a collection's Play all plays, in the order shown: each card's key, or a
     *  season card's episodes in its place. */
    fun playAllKeys(cards: List<Pair<String, List<String>>>): List<String> =
        cards.flatMap { (key, holds) -> holds.ifEmpty { listOf(key) } }

    /** How far a press scrolls a block of text being read: down while some of it is
     *  still under the window, up while the window is not at its top; nought when the
     *  press should move on - down to the cast, or up out of it. `textEnd` is where the
     *  text ends in the scrolled column, `step` a press's worth. */
    fun readStep(down: Boolean, at: Float, window: Float, textEnd: Float, step: Float): Float =
        if (down) minOf(step, textEnd - (at + window)).coerceAtLeast(0f)
        else -minOf(step, at).coerceAtLeast(0f)

    /** The stiffness of an unbouncy spring that has covered 98% of its way after [ms]:
     *  (1 + wt)e^-wt = 0.02 at wt = 5.83, stiffness w squared. */
    fun slideStiffness(ms: Int): Float {
        val w = 5.83f / (ms / 1000f)
        return w * w
    }

    /** A Start card's width: on a television narrow enough that two whole rows, the
     *  second one's titles too, stand under the tabs - 270 left them under the screen's
     *  edge; on a phone 220, a tablet 280. */
    fun startCardW(screenWidthDp: Int, tv: Boolean): Float =
        if (tv) 256f else if (screenWidthDp < 600) 220f else 280f

    /** Where Start's page moves for a card the remote lands on; null for anything that
     *  is not a row's card. Nothing while the card, its row's name above it and the
     *  ring's room are on screen: down from the upper row to the lower one the page holds
     *  still. Below the screen: just far enough that the row stands at the foot, so the
     *  row it came from stays above it. Above, or down off the picture (`fromTop`): the
     *  row under the tabs, its name showing - decided first: asked again every frame of
     *  the slide, "on screen" stopped that slide half way. No margin under a row at the
     *  foot: 2% of the screen was more than the second row has, and it moved the page. */
    fun startRowMove(offset: Float, size: Float, containerSize: Float, fromTop: Boolean): Float? {
        val pivot = rowPivot(offset, size, containerSize) ?: return null
        if (fromTop) return pivot
        val name = containerSize * 0.085f
        val bottom = offset + size + size * 0.04f
        if (offset - name >= 0f && bottom <= containerSize) return 0f
        if (offset < name) return pivot
        return bottom - containerSize
    }

    fun rowPivot(offset: Float, size: Float, containerSize: Float): Float? {
        if (size < containerSize * 0.15f || size > containerSize * 0.5f) return null
        val titleRoom = containerSize * 0.085f
        return offset - titleRoom
    }

    /** The side margin of the Films and TV grid: on a television, with the ring's room
     *  inside the grid, the same sixteen points Start and the tabs keep. */
    fun gridSide(tv: Boolean): Int = if (tv) 12 else 0

    /** The air above a film page's shelf so that it ends `margin` above the foot of a
     *  `screen` tall screen: from where the air begins and how tall the shelf is, all in
     *  dp; never less than ten. */
    fun footGap(screen: Float, from: Float, shelf: Float, margin: Float): Float =
        (screen - from - shelf - margin).coerceAtLeast(10f)

    /** A television's home poster width in dp: two whole shelves in the `tall` dp under
     *  the tabs and any bar - per shelf its name and air (36, measured on the streamer),
     *  the poster at 1.5 its width and 4 for the focused poster's lift. */
    fun homePoster(tall: Float): Int =
        (((tall / 2f) - 36f - 4f) / 1.5f).toInt().coerceIn(96, 124)

    /** The second a still is asked for at: ten-second steps, as the server keeps them. */
    fun frameSecond(filmMs: Long): Long = (filmMs.coerceAtLeast(0) / 10_000) * 10

    /** The margin either side of a row of cards `card` wide in `width`: as many whole
     *  cards as fit, and the rest shared out, so the row stands centred and its edges
     *  are the same on both sides. A margin of its own on top kept seven 134 dp cards
     *  off a 960 dp screen they fit on with 11 dp to spare either side. */
    fun centredInset(width: Float, card: Int, least: Float = 0f): Float {
        if (card <= 0 || width <= 0f) return least
        val fit = maxOf(1, ((width - 2 * least) / card).toInt())
        return ((width - fit * card) / 2f).coerceAtLeast(0f)
    }

    /** How far in a home shelf's first poster stands: in line with the shelf's name
     *  on a television, a little in on a phone. The row itself runs edge to edge. */
    fun shelfInset(tv: Boolean): Int = if (tv) 21 else 7

    /** And the shelf's name above it: on a phone just right of the poster's edge, where
     *  21 dp of a 400 dp wide screen was a margin nobody needed. */
    fun shelfNameInset(tv: Boolean): Int = if (tv) 21 else 12

    /** The address a live channel plays from on its server, signed in as the app is:
     *  the token in the address, which the server copies onto each piece. */
    /** How long the live player waits to reach a server, and for a channel's first
     *  playlist - which arrives once the server has the channel's first stretch. */
    const val LIVE_CONNECT_MS = 5_000L
    const val LIVE_READ_MS = 30_000L

    fun liveAddress(base: String, key: String, token: String): String =
        base.trimEnd('/') + "/live/" + key + "/index.m3u8" +
            (if (token.isNotEmpty()) "?t=" + java.net.URLEncoder.encode(token, "UTF-8") else "")

    /** The display mode to show `fps` frames a second in: the current size at that
     *  rate, or at a whole multiple of it (25 on a 50 Hz mode), the plainest first. Null
     *  when the current mode already fits or none does. Modes are (id, width, height, hz). */
    fun modeFor(modes: List<Mode>, current: Mode, fps: Float): Int? {
        if (fps <= 0f) return null
        fun fits(hz: Float): Int? {
            val times = hz / fps
            val whole = Math.round(times)
            return if (whole >= 1 && kotlin.math.abs(times - whole) < 0.01f * whole) whole else null
        }
        if (fits(current.hz) != null) return null
        return modes.filter { it.width == current.width && it.height == current.height }
            .mapNotNull { m -> fits(m.hz)?.let { m to it } }
            .minByOrNull { it.second }?.first?.id
    }

    data class Mode(val id: Int, val width: Int, val height: Int, val hz: Float)

    /** A sound's channels as a person says them: Mono, Stereo, 5.1, 7.1; "" unknown. */
    fun channelsWord(n: Int): String = when {
        n >= 8 -> "7.1"
        n >= 6 -> "5.1"
        n == 2 -> "Stereo"
        n == 1 -> "Mono"
        else -> ""
    }

    /** How long the server is asked to hold the notice connection open: as asked at
     *  home; from away 20 s at most - a mobile network drops a connection idle for 45 s
     *  without a word, and the read then ran into its 57 s timeout every few minutes. */
    fun noticeHold(asked: Int, home: Boolean): Int = if (home) asked else minOf(asked, 20)

    /** How long "Analysing the film…" stays over the picture; the measuring goes on
     *  behind it and its answer is still said when it comes. */
    const val SYNC_SAY_MS = 15_000L

    /** What a subtitle measurement that failed says: a timeout is the measuring taking
     *  too long (a busy drive), not a server out of reach - which it answered 6 ms later. */
    fun syncFailure(e: Throwable): String {
        var c: Throwable? = e
        while (c != null) {
            if (c is java.net.SocketTimeoutException) return "Analysing the film took too long"
            c = c.cause
        }
        return "could not reach the server"
    }

    /** How far behind the live edge a channel plays: the cushion the server makes by
     *  starting each channel half a minute back. */
    const val LIVE_BEHIND_MS = 20_000L

    /** Where the picture is sent when the time bar stops being moved; -1 for nowhere.
     *  A move cancelled by Back never reached the player, which played on: nowhere,
     *  seamless. Back on a move that did reach it: where it was when the move began.
     *  Otherwise the controls have already gone to the spot. */
    fun afterScrub(back: Boolean, canceled: Boolean, from: Long): Long =
        if (!canceled && back && from >= 0) from else -1L

    /** The length a progress report carries: the file's own, read off the player, when
     *  it plays as it lies; else the title's. An encode's player length grows as it is
     *  made, so it is not used. */
    fun reportedLength(direct: Boolean, playerMs: Long, titleMs: Long): Long =
        if (direct && playerMs > 0 && playerMs < 86_400_000L) playerMs
        else titleMs.coerceAtLeast(0L)

    /** A country's flag from its two-letter code, as the emoji the letters make; the
     *  code itself where it is not two letters. */
    fun flagOf(code: String): String {
        val c = code.trim().uppercase()
        if (c.length != 2 || !c.all { it in 'A'..'Z' }) return c
        return c.map { String(Character.toChars(0x1F1E6 + (it - 'A'))) }.joinToString("")
    }

    /** What a live player does about an error: "edge" - fallen behind the live window,
     *  back to the live edge; "retry" - the stream or network failed, ask again; null -
     *  give up (the server refused it, or it has been picked up too often in a row). */
    fun liveRecover(errorCode: Int, httpCode: Int, tries: Int,
                    moves: Int = 0, twin: Boolean = false): String? {
        // "move" - to the house's other machine, which has the same channels: its server
        // gone (a restart, an install) after one try, or the channel refused here once
        val canMove = twin && moves < 4
        return when {
            errorCode == 1002 && tries < 6 -> "edge"
            liveRefused(httpCode) != null ->
                if (canMove && moves == 0 && httpCode != 429) "move" else null
            canMove && errorCode in 2000..2999 && tries >= 1 -> "move"
            tries >= 6 -> if (canMove) "move" else null
            errorCode in 2000..2999 || errorCode in 3000..3999 -> "retry"
            else -> null
        }
    }

    /** The house's other machine for a live channel played from `here`: the copy of
     *  this server, or the server this one is a copy of. Not a friend's server - its
     *  channels are not these. */
    fun liveTwin(rows: List<Server>, here: String): Server? {
        val at = Server("", here.trimEnd('/'), "")
        val mine = rows.firstOrNull { sameMachine(it, at) }
        fun isHere(s: Server) = sameMachine(s, at) || (mine != null && sameMachine(s, mine))
        return rows.firstOrNull { other ->
            other.base.isNotEmpty() && !isHere(other) &&
                ((other.copyOf.isNotEmpty() && isHere(Server("", other.copyOf, ""))) ||
                 (mine != null && mine.copyOf.isNotEmpty() &&
                  sameMachine(Server("", mine.copyOf, ""), other)))
        }
    }

    /** How often, and how many times, the player asks again while the server measures
     *  a film for the receiver: every 15 s for ten minutes. */
    const val RECEIVER_ASK_EVERY_MS = 15_000L
    const val RECEIVER_ASKS = 40

    /** Whether two rows are two ways of writing down one machine: the id it answers
     *  with, or an address in common. */
    fun sameMachine(a: Server, b: Server): Boolean {
        if (a.id.isNotEmpty() && a.id == b.id) return true
        val ways = { s: Server ->
            listOf(s.base, s.outside).map { it.trimEnd('/') }.filter { it.isNotEmpty() }
        }
        return ways(a).any { it in ways(b) }
    }

    /** The rows asked for a second copy of a film playing from `here`: never the
     *  machine playing it, by any address on its row or by the id it answered with.
     *  Played from its outside address, which is on no row, the main server was taken
     *  for a second machine - one film over two connections to one server. */
    fun othersThan(rows: List<Server>, here: String, hereId: String): List<Server> {
        val self = Server("", here.trimEnd('/'), "")
        val mine = rows.firstOrNull { sameMachine(it, self) || (hereId.isNotEmpty() && it.id == hereId) }
        return rows.filter {
            it.base.isNotEmpty() && !sameMachine(it, self) &&
                (mine == null || !sameMachine(it, mine)) &&
                (hereId.isEmpty() || it.id != hereId)
        }
    }

    /** The corner while a channel loads: how long it has waited, and what is in hand. */
    fun liveLoadingLine(waitedMs: Long, bufferedMs: Long): String =
        "Loading %.1f s \u00b7 %d s buffered".format(java.util.Locale.ROOT,
            waitedMs.coerceAtLeast(0) / 1000f, (bufferedMs / 1000).coerceAtLeast(0))

    /** The server's log line for a channel's start: from the press to the picture. */
    fun liveStartLine(title: String, tookMs: Long): String =
        "live start: %s ready after %.1f s".format(java.util.Locale.ROOT, title, tookMs / 1000f)

    /** And for leaving it: how long it was on, and what went wrong while it was. */
    fun liveLeftLine(title: String, onMs: Long, played: Boolean, stalls: Int, dropped: Int): String =
        if (!played) "live left: %s after %.1f s, before it played".format(
            java.util.Locale.ROOT, title, onMs / 1000f)
        else "live left: %s after %d s, %d stall%s, %d frames dropped".format(
            java.util.Locale.ROOT, title, onMs / 1000, stalls, if (stalls == 1) "" else "s", dropped)

    /** The server's log line for a stall: which channel, how long in, what was in hand. */
    fun liveStallLine(title: String, playedMs: Long, bufferedMs: Long, behindMs: Long,
                      count: Int): String =
        "live stall #$count: $title after ${playedMs / 1000} s, buffer ${bufferedMs / 1000} s" +
            (if (behindMs >= 0) ", ${behindMs / 1000} s behind live" else "")

    /** What a swipe over a live channel does: up or left the next channel, down or right
     *  the one before; with the panel open, sideways steps its group. A short one is
     *  nothing. */
    fun liveSwipe(dx: Float, dy: Float, panel: Boolean): String {
        val ax = kotlin.math.abs(dx); val ay = kotlin.math.abs(dy)
        if (maxOf(ax, ay) < 80f) return ""
        return if (ay >= ax) (if (dy < 0) "next" else "prev")
               else if (panel) (if (dx < 0) "groupNext" else "groupPrev")
               else (if (dx < 0) "next" else "prev")
    }

    /** The sound asked for when a film must be encoded rather than sent as it is:
     *  anything but passthrough makes the address an encode, held to the ceiling. */
    const val ENCODE_AUDIO = "aac"

    /** An encode's ceiling for the info line: the lower of what was chosen for this
     *  playing and the one in force here, "" for none or a direct play. */
    fun encodeCap(direct: Boolean, chosenMbit: Int, ceilingMbit: Int): String {
        if (direct) return ""
        val cap = listOf(chosenMbit, ceilingMbit).filter { it > 0 }.minOrNull() ?: return ""
        return "capped at $cap Mbit/s"
    }

    /** What a press on a film's download button does while it is coming in: the first
     *  press asks ("arm"), a second within a few seconds cancels; otherwise "" - it is
     *  the Download button and opens the versions. */
    /** A film not out yet, on its page where Play would be: "Out 15 Dec 2026". */
    fun upcomingLine(released: String): String {
        val d = Regex("""^(\d{4})-(\d{2})-(\d{2})""").find(released) ?: return "Not out yet"
        val (y, m, day) = d.destructured
        val month = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                           "Oct", "Nov", "Dec").getOrNull(m.toInt() - 1) ?: return "Not out yet"
        return "Out " + day.toInt() + " " + month + " " + y
    }

    /** The band of a card not out yet: its date, "15 DEC" - month and year when it is
     *  most of a year off - and UPCOMING when the date is not known or has passed.
     *  Both dates "yyyy-MM-dd". */
    fun upcomingBand(date: String, today: String): String {
        val day = Regex("""^(\d{4})-(\d{2})-(\d{2})""")
        val d = day.find(date)?.destructured ?: return "UPCOMING"
        val t = day.find(today)?.destructured ?: return "UPCOMING"
        val month = listOf("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
                           "OCT", "NOV", "DEC").getOrNull(d.component2().toInt() - 1)
            ?: return "UPCOMING"
        fun epochDay(y: Int, m: Int, dd: Int): Long {
            val yy = if (m <= 2) y - 1 else y
            val era = (if (yy >= 0) yy else yy - 399) / 400
            val yoe = yy - era * 400
            val doy = (153 * (if (m > 2) m - 3 else m + 9) + 2) / 5 + dd - 1
            return era * 146097L + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468
        }
        val days = epochDay(d.component1().toInt(), d.component2().toInt(), d.component3().toInt()) -
            epochDay(t.component1().toInt(), t.component2().toInt(), t.component3().toInt())
        return when {
            days <= 0 -> "UPCOMING"
            days > 330 -> month + " " + d.component1()
            else -> d.component3().toInt().toString() + " " + month
        }
    }

    /** How often a season's page looks for an episode it started downloading. */
    const val EPISODE_LANDS_EVERY_MS = 20_000L

    /** The episodes asked for and not in yet: `ready` are those that can be played now. */
    fun episodesStillComing(asked: Set<Int>, ready: Set<Int>): Set<Int> = asked - ready

    /** A series' added packs, those in use first: every one of them can be removed. */
    fun addedPacks(inUse: List<String>, rows: List<String>): List<String> =
        (inUse + rows.filter { it.isNotEmpty() }).distinct()

    /** What a series' pack row says it already is; "" for one on the tracker only. */
    fun packWord(hash: String, inUse: List<String>): String = when {
        hash.isEmpty() -> ""
        hash in inUse -> "In use"
        else -> "Added"
    }

    /** Where a version in the download list comes from; one this house holds says On disk,
     *  whether it is a file here, a pack's or the tracker's own upload. */
    fun versionTag(disk: Boolean, have: Boolean, pack: Boolean, box: Boolean,
                   seasonPack: Boolean): String = when {
        disk || have -> "On disk"
        pack -> "Pack"
        box -> if (seasonPack) "Season pack" else "Box set"
        else -> "Tracker"
    }

    /** What a plain press on Download takes: the list's recommended release, or the one
     *  pressed. Each row is its id, whether recommended, and whether it can be fetched. */
    fun freshPick(pressed: String, rows: List<Triple<String, Boolean, Boolean>>): String =
        rows.firstOrNull { it.second && it.third }?.first ?: pressed

    /** The first button on a Start slide: its word, and whether it plays (else it opens
     *  the page). Only a film here plays; one not out yet says when. */
    fun heroAct(offered: Boolean, upcoming: Boolean, askable: Boolean, type: String,
                released: String): Pair<String, Boolean> = when {
        upcoming -> upcomingLine(released) to false
        offered -> "⤓ Get it" to false
        askable -> "Request" to false
        type == "movie" -> "▶ Play" to true
        else -> "▶ Watch" to false
    }

    /** The line under a series' poster: its place, and while stepping the film on show. */
    fun sagaCaption(going: Boolean, label: String, shown: Int, size: Int, own: Boolean): String =
        if (!going) "${shown + 1} of $size"
        else "$label · ${shown + 1} of $size"

    /** The poster shown after a step through a series of `size`: within its ends. */
    fun sagaStep(shown: Int, size: Int, by: Int): Int =
        if (size <= 0) 0 else (shown + by).coerceIn(0, size - 1)

    /** The film before and after this one in its series - (key, label) or null each -
     *  from the series in order, this one marked. */
    fun sagaSteps(parts: List<Triple<String, String, Boolean>>):
            Pair<Pair<String, String>?, Pair<String, String>?> {
        val at = parts.indexOfFirst { it.third }
        if (at < 0) return null to null
        val prev = parts.getOrNull(at - 1)?.let { it.first to "‹ " + it.second }
        val next = parts.getOrNull(at + 1)?.let { it.first to it.second + " ›" }
        return prev to next
    }

    fun offerPress(state: String, armed: Boolean): String =
        if (state != "queued" && state != "downloading") ""
        else if (armed) "cancel" else "arm"

    /** The channel `step` away from `at` in a list of `size`, round the ends. */
    fun zap(at: Int, size: Int, step: Int): Int =
        if (size <= 0) 0 else ((at + step) % size + size) % size

    /** The next channel up or down among those of the same kind as the one on: a
     *  YouTube stream steps through the YouTube circle, a playlist channel through the
     *  playlist - never from one into the other. `groups` is each channel's group. */
    fun zapIn(groups: List<String>, at: Int, step: Int): Int {
        if (groups.isEmpty()) return 0
        val tube = groups.getOrNull(at) == "YouTube"
        val same = groups.indices.filter { (groups[it] == "YouTube") == tube }
        if (same.isEmpty()) return at.coerceIn(0, groups.size - 1)
        val here = same.indexOf(at).coerceAtLeast(0)
        return same[zap(here, same.size, step)]
    }

    /** What Center shows over a live channel: the stream's picture, sound and buffer,
     *  where it is in the playlist, and the group the playlist is narrowed to. */
    fun liveFacts(title: String, group: String, width: Int, height: Int, fps: Float,
                  videoCodec: String, bitrate: Int, audioCodec: String, sampleRate: Int,
                  channels: Int, bufferedMs: Long, waiting: Boolean, dropped: Int,
                  screenHz: Float, place: Int, of: Int, filter: String,
                  filterChanged: Boolean, more: List<String> = emptyList()): String {
        fun rate(x: Float) = if (x <= 0f) "" else
            (if (x == Math.round(x).toFloat()) Math.round(x).toString() else "%.2f".format(java.util.Locale.ROOT, x))
        val picture = listOf(
            if (width > 0) "${width}\u00d7$height" else "",
            rate(fps).let { if (it.isEmpty()) "" else "$it fps" },
            videoCodec,
            if (bitrate > 0) "%.1f Mbit/s".format(java.util.Locale.ROOT, bitrate / 1e6) else "")
            .filter { it.isNotEmpty() }.joinToString(" \u00b7 ")
        val sound = listOf(audioCodec,
            if (sampleRate > 0) "${sampleRate / 1000} kHz" else "",
            if (channels > 0) "$channels ch" else "")
            .filter { it.isNotEmpty() }.joinToString(" \u00b7 ")
        return (listOf(title + (if (group.isNotEmpty()) " \u00b7 $group" else "")) + more +
                listOfNotNull(
            if (picture.isNotEmpty()) "Picture: $picture" else null,
            if (sound.isNotEmpty()) "Sound: $sound" else null,
            (if (waiting) "Buffer: loading\u2026" else "Buffer: ${(bufferedMs / 1000).coerceAtLeast(0)} s") +
                " \u00b7 dropped $dropped" + rate(screenHz).let { if (it.isEmpty()) "" else " \u00b7 screen $it Hz" },
            if (of > 0) "Channel $place of $of \u00b7 Up/Down to change" else null,
            "Group: \u2039 " + filter.ifEmpty { "All" } + " \u203a" +
                (if (filterChanged) "  \u2013 Center to keep" else "  (Left/Right)"),
        )).joinToString("\n")
    }

    /** The corner of a live channel: LIVE and the whole seconds in hand, or that it is
     *  waiting for more - which is what a stall looks like from the sofa. */
    fun liveBufferLabel(bufferedMs: Long, waiting: Boolean): String =
        if (waiting) "LIVE \u00b7 loading\u2026" else "LIVE \u00b7 " + (bufferedMs / 1000).coerceAtLeast(0) + " s buffered"

    /** What a live channel the server refused says: 429 is every connection of the own
     *  playlist in use. Null for anything else, which the player shows as it is. */
    fun liveRefused(code: Int): String? = when (code) {
        429 -> "Every connection of the playlist is in use - stop another channel first"
        504, 404 -> "The channel did not start - it may be off the air, or blocked here"
        else -> null
    }

    /** Whether the Films or TV tab's own sort and filters stand above the grid: not while
     *  a shelf opened whole is on screen, which has its own - Popular opened off TV
     *  showed TV's, and its country narrowed the library instead of the page. */
    fun tabControls(moreRow: String?, tab: String): Boolean =
        moreRow == null && (tab == "films" || tab == "tv")

    /** What a tab's order menu offers after the library's own orders: on TV and
     *  Films, the popular titles, which open as a page of their own. */
    fun tabExtraOrders(tab: String): List<Pair<String, String>> =
        if (tab == "tv" || tab == "films") listOf("popular" to "Popular") else emptyList()

    /** The tab's Popular order: the list as it came, most watched first (or turned
     *  round), narrowed by every marked genre and any marked decade. */
    fun <T> narrowPopular(list: List<T>, genres: (T) -> List<String>, year: (T) -> Int,
                          rank: (T) -> Int, genre: String, decade: String,
                          asc: Boolean): List<T> {
        val want = genre.split(",").map { it.trim().lowercase() }.filter { it.isNotEmpty() }
        val eras = decade.split(",").mapNotNull { it.trim().toIntOrNull() }.filter { it > 0 }
        val kept = list.filter { x ->
            val has = genres(x).map { it.lowercase() }
            want.all { it in has } &&
                (eras.isEmpty() || eras.any { d -> year(x) in d..(d + 9) })
        }
        return if (asc) kept.sortedBy(rank) else kept.sortedByDescending(rank)
    }

    /** The name the popular page of a tab is opened under. */
    fun popularRow(tab: String): String = if (tab == "films") "Popular films" else "Popular shows"

    /** Whether a shelf opened whole is one of the popular pages. */
    fun popularPage(row: String?): Boolean = row == "Popular shows" || row == "Popular films"

    /** How many titles carry each value - a genre, a decade - for a page's filter menus. */
    fun countOf(each: List<List<String>>): List<Pair<String, Int>> =
        each.flatten().groupingBy { it }.eachCount().toList()

    /** An episode a pack offers, pressed on its season: the release list every missing
     *  episode opens, rather than its own page with one button. */
    fun episodeOpensReleases(type: String, offered: Boolean): Boolean =
        type == "episode" && offered

    private val LANG_SAME = mapOf(
        "en" to "en", "eng" to "en", "sv" to "sv", "swe" to "sv", "da" to "da", "dan" to "da",
        "no" to "no", "nor" to "no", "nob" to "no", "nb" to "no", "fi" to "fi", "fin" to "fi",
        "de" to "de", "ger" to "de", "deu" to "de", "fr" to "fr", "fre" to "fr", "fra" to "fr",
        "es" to "es", "spa" to "es", "it" to "it", "ita" to "it", "nl" to "nl", "dut" to "nl",
        "nld" to "nl", "pt" to "pt", "por" to "pt")

    /** Whether a track's language tag and a reader's language are one language: "swe"
     *  and "sv", "ger" and "de". The web's sameLang, the same table. Compared by the
     *  first two letters alone, Swedish, German and French tracks never matched. */
    fun sameLanguage(tag: String, want: String): Boolean {
        val t = tag.lowercase(); val w = want.lowercase()
        if (t.isEmpty() || w.isEmpty()) return false
        fun canon(x: String) = LANG_SAME[x] ?: LANG_SAME[x.take(3)] ?: LANG_SAME[x.take(2)] ?: x
        return t.startsWith(w.take(2)) || w.startsWith(t.take(2)) || canon(t) == canon(w)
    }

    /** Which soundtrack to pick out of a file played directly: the one chosen this
     *  time, else the one the server marks, and nothing when that is the first - the
     *  player takes the first by itself. A Korean film played its English dub because
     *  only a choice made this time was ever picked. */
    fun soundToPick(chosen: Int?, standard: Int?, first: Int?): Int? =
        chosen ?: standard?.takeIf { it != first }

    /** A skin's corner scale, held to what still draws a corner and a card: 0.2 to 2. */
    fun cornerScale(said: Float): Float =
        if (said.isNaN() || said <= 0f) 1f else said.coerceIn(0.2f, 2f)

    /** How long a name that would not resolve is asked for by number without trying. */
    const val NAME_FAILED_MS = 60_000L

    /** The same address with its name swapped for a number: "http://name:8765/x" by
     *  "1.2.3.4" is "http://1.2.3.4:8765/x". Unchanged when there is no number, or
     *  the address is a number already. */
    fun numbered(url: String, ip: String): String {
        val m = Regex("""^(https?://)([^/:]+)(.*)$""").find(url) ?: return url
        val host = m.groupValues[2]
        if (ip.isEmpty() || host.all { it.isDigit() || it == '.' }) return url
        return m.groupValues[1] + ip + m.groupValues[3]
    }

    /** Whether a name that failed to resolve at [failedAt] is still asked for by its
     *  last known number without looking it up again: every lookup that fails costs
     *  its own wait, and on a network that cannot resolve it they all fail. */
    fun nameStillFailing(failedAt: Long, now: Long): Boolean =
        failedAt > 0 && now - failedAt < NAME_FAILED_MS

    /** How long after one asking for a newer build the next may come. */
    const val UPDATE_EVERY_MS = 300_000L

    /**
     * Whether to ask for a newer build now, on coming back to the app. Not while one
     * is on offer, and not more than once in five minutes. Asked at launch only, an
     * app left open for days was never offered what had been published since.
     */
    fun updateDue(showing: Boolean, askedAt: Long, now: Long): Boolean =
        !showing && now - askedAt >= UPDATE_EVERY_MS

    /** Whether a build found is one to put on the screen: not one put off with Later. */
    fun updateOffered(code: Int, putOff: Int): Boolean = code > 0 && code != putOff

    /** What the check knows when it decides. Addresses without a trailing slash. */
    data class Seen(
        /** the row that is open is a machine keeping copies of another */
        val chosenIsCopy: Boolean,
        /** this opening of the app has not yet looked for the main server */
        val homeUnlooked: Boolean,
        /** the main server asks screens to use its copy */
        val prefersCopy: Boolean,
        /** the library is on the copy now, with the way home remembered */
        val standingBy: Boolean,
        /** where the library is asked from now */
        val base: String,
        /** [base] can be reached from this network and was not seen down */
        val baseUsable: Boolean,
        /** the main server: up, down, or not known */
        val mainUp: Boolean?,
        /** an address answering as the main server */
        val mainDoor: String?,
        /** an address of the main server worth asking while nothing is known */
        val mainTry: String?,
        /** an address answering as the machine keeping copies */
        val copyDoor: String?,
        /** the main server's own row address, the way home when nothing better is known */
        val mainBase: String,
        /** the way home remembered while standing by */
        val homeBase: String,
    )

    sealed class Move {
        /** nothing to change */
        object Stay : Move()
        /** nothing answers anywhere */
        object Nothing : Move()
        /** the library is at this address of the open row */
        data class Library(val door: String) : Move()
        /** onto the machine keeping copies, the way home remembered */
        data class Standby(val door: String, val home: String, val asked: Boolean) : Move()
        /** home from the copy, once a request there has answered */
        data class HomeAgain(val door: String) : Move()
        /** opened on the copy while the main server answers: open the main server */
        data class OpenMain(val door: String) : Move()
    }

    /** The move, and whether this opening has now looked for the main server. */
    data class Decision(val move: Move, val looked: Boolean)

    /**
     * The one decision about where the library is.
     *
     * Opened on the copy: the main server is asked first, and opened when it answers
     * and does not ask for the copy; the copy is the library once the main server is
     * known to be off, which takes two looks.
     * Opened on the main server: the copy when the main server asks for it; the main
     * server by the address that answers; the copy only when the main server is down,
     * which takes two looks; and while it is not known, the library stays where it is
     * unless that address cannot be reached from here.
     */
    fun decide(s: Seen): Decision {
        if (s.chosenIsCopy) {
            val looked = s.homeUnlooked && s.mainUp != null
            if (looked && s.mainDoor != null && !s.prefersCopy)
                return Decision(Move.OpenMain(s.mainDoor), true)
            // this opening has not heard from the main server yet: it is asked first,
            // and nothing is drawn from the copy until it is known to be off
            if (s.homeUnlooked && s.mainUp == null) {
                val move = if (!s.baseUsable && s.mainTry != null && s.mainTry != s.base)
                    Move.Library(s.mainTry) else Move.Stay
                return Decision(move, false)
            }
            val move = if (s.copyDoor != null && (s.base != s.copyDoor || s.standingBy))
                Move.Library(s.copyDoor) else Move.Stay
            return Decision(move, looked)
        }
        val looked = s.mainUp != null
        val onCopy = s.copyDoor != null && s.standingBy && s.base == s.copyDoor
        val move = when {
            s.prefersCopy && s.copyDoor != null ->
                if (onCopy) Move.Stay
                else Move.Standby(s.copyDoor, s.mainDoor ?: s.mainBase, asked = true)
            s.mainDoor != null ->
                if (s.standingBy) Move.HomeAgain(s.mainDoor)
                else if (s.base != s.mainDoor) Move.Library(s.mainDoor)
                else Move.Stay
            s.mainUp == false && s.copyDoor != null ->
                if (onCopy) Move.Stay
                else Move.Standby(s.copyDoor, s.homeBase.ifEmpty { s.mainBase }, asked = false)
            s.mainUp == false -> Move.Nothing
            // not known yet: one miss, or no look so far
            !s.standingBy && !s.baseUsable && s.mainTry != null && s.mainTry != s.base ->
                Move.Library(s.mainTry)
            else -> Move.Stay
        }
        return Decision(move, looked)
    }
}
