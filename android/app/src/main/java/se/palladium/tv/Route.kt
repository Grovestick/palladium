package se.palladium.tv

/**
 * The rules of the background check, with nothing of Android in them: what one look at
 * an address is worth, and where the library is asked from. [Reach] gathers the looks
 * and carries out the move; the rules are here so they can be run against a network
 * that drops connections (RouteTest) rather than tried on a phone.
 */
object Route {
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
