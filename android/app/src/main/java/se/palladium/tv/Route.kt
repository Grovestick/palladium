package se.palladium.tv

/**
 * The rules of the background check, with nothing of Android in them: what one look at
 * an address is worth, and where the library is asked from. [Reach] gathers the looks
 * and carries out the move; the rules are here so they can be run against a network
 * that drops connections (RouteTest) rather than tried on a phone.
 */
object Route {
    /** How wide a list of releases is drawn: most of the screen, so a release name fits
     *  on a phone held sideways; the platform's dialog stopped at a third of it. */
    const val PICKER_WIDTH = 0.94f
    val PICKER_MAX = 1000

    /** A search typed on Home that found films and programmes: shown in two parts. */
    fun splitSearch(query: String, tab: String, films: Int, tv: Int): Boolean =
        query.isNotBlank() && tab == "home" && films > 0 && tv > 0

    /** "(3)" after the Version button when there are several copies, nothing for one. */
    fun copiesSaid(copies: Int): String = if (copies > 1) "  ($copies)" else ""

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
        "done" -> "Arriving"
        else -> ""
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

    /** Whether pressing a tab opens its popular page: TV and Films do, unless a
     *  streaming service is marked - then it is that service's catalogue. */
    fun tvOpensPopular(tab: String, service: String): Boolean =
        (tab == "tv" || tab == "films") && service.isEmpty()

    /** A streaming service's catalogue in the popular page's orders. */
    val SERVICE_ORDERS = listOf("popularity" to "Popular", "rating" to "Rating",
                                "originallyAvailableAt" to "Released", "year" to "Year",
                                "titleSort" to "Name")

    /** The orders a shelf opened whole offers instead of the library's own: the
     *  popular shows sort by how popular and how well rated as well; empty for the rest. */
    fun extraOrders(row: String?): List<Pair<String, String>> =
        if (popularPage(row))
            listOf("popularity" to "Popular", "originallyAvailableAt" to "Released",
                   "year" to "Year", "titleSort" to "Name", "rating" to "Rating")
        else emptyList()

    /** How far grids and shelves stop short of a television's edges, in dp: the sides
     *  and the bottom. A TV trims its edges, and a ring at the very edge was cut off.
     *  Nothing on a phone, where the screen shows all of itself. */
    fun tvSafe(tv: Boolean): Pair<Int, Int> = if (tv) 24 to 20 else 0 to 0

    /** The air above a film page's shelf so that it ends `margin` above the foot of a
     *  `screen` tall screen: from where the air begins and how tall the shelf is, all in
     *  dp; never less than ten. */
    fun footGap(screen: Float, from: Float, shelf: Float, margin: Float): Float =
        (screen - from - shelf - margin).coerceAtLeast(10f)

    /** A television's home poster width in dp: two whole shelves in the `tall` dp under
     *  the tabs - per shelf its name (18), the poster at 1.5 its width and 8 of air - with
     *  the `passing` bars over the shelves counted back in, so a bar does not shrink them. */
    fun homePoster(tall: Float, passing: Float): Int =
        ((((tall + passing) / 2f) - 18f - 8f) / 1.5f).toInt().coerceIn(96, 124)

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
    fun shelfInset(tv: Boolean): Int = if (tv) 21 else 8

    /** And the shelf's name above it: on a phone just right of the poster's edge, where
     *  21 dp of a 400 dp wide screen was a margin nobody needed. */
    fun shelfNameInset(tv: Boolean): Int = if (tv) 21 else 12

    /** The address a live channel plays from on its server, signed in as the app is:
     *  the token in the address, which the server copies onto each piece. */
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
