package se.palladium.tv

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLongArray

/**
 * One film pulled from two machines at once.
 *
 * The house server and the machine that keeps copies hold the same file, byte for
 * byte - a copy is only accepted when its fingerprint agrees with the original. So the
 * film is asked for in lumps, and each machine takes the next lump it is free to take.
 * A link twice as quick claims twice as many, which is the whole of the balancing:
 * nothing is measured, the work simply goes where there is room.
 *
 * The lumps reach the player in order, so one that arrives early waits its turn, and
 * one running late is asked of the other machine as well: first to arrive is kept.
 *
 * A side that stops answering - a server switched off at ten - is dropped and its lump
 * asked of the other, with the picture still running. The player sees no error and
 * nothing restarts. Only when both sides are gone does this fail, and then it fails as
 * an ordinary read and the film moves house the old way.
 */
/**
 * Which lump goes to which machine, and which are in hand. No threads and no Android, so
 * it can be tested; every call is made under the reader's gate.
 */
internal class LumpLine<T>(private val lump: Long, private val ahead: Int) {
    companion object {
        const val END = -1L
        const val WAIT = -2L
    }

    /** where the next lump starts: the player's place at a lump's edge */
    var next = 0L
        private set
    private var claimed = 0L
    var end = Long.MAX_VALUE
    private val owner = HashMap<Long, Int>()
    private val done = HashMap<Long, T>()
    private var lateAt = -1L
    private var askedLate = -1L

    val inHand: Int get() = done.size
    val nextOwner: Int get() = owner[next] ?: -1
    val claimedTo: Long get() = claimed

    fun start(at: Long, until: Long) {
        next = at
        claimed = at
        end = until
        lateAt = -1L
        askedLate = -1L
        owner.clear()
        done.clear()
    }

    /** The lump for this machine: where it starts, or [END] or [WAIT]. */
    fun claim(side: Int, alive: (Int) -> Boolean): Long {
        if (lateAt >= 0) {
            val wanted = lateAt == next && !done.containsKey(next)
            if (wanted && owner[next] != side) {
                lateAt = -1L
                return next
            }
            if (!wanted) lateAt = -1L
        }
        while (claimed < end) {
            val at = claimed
            val who = owner[at]
            if (done.containsKey(at) || (who != null && who != side && alive(who))) {
                claimed += lump
                continue
            }
            // the lump the player needs next is always allowed: it is what makes room
            if (at > next && (done.size >= ahead || at - next > ahead * lump)) return WAIT
            claimed += lump
            owner[at] = side
            return at
        }
        return END
    }

    /** A lump brought. False when the player is already past it: the slower of two copies. */
    fun store(at: Long, one: T): Boolean {
        if (at < next) return false
        done[at] = one
        return true
    }

    /** The lump the player needs next, if it is here. */
    fun take(size: (T) -> Int): T? {
        val one = done.remove(next) ?: return null
        owner.remove(next)
        next += size(one)
        return one
    }

    /** Ask for the next lump from another machine too. False if already asked. */
    fun late(): Boolean {
        if (askedLate == next) return false
        askedLate = next
        lateAt = next
        return true
    }

    /** Everything this machine had taken on goes back to be fetched. */
    fun giveBack(side: Int) {
        owner.entries.removeAll { it.value == side }
        claimed = minOf(claimed, next)
    }

    /** The next lump goes back, whoever has it. */
    fun giveBackNext() {
        owner.remove(next)
        claimed = minOf(claimed, next)
    }

    fun clear() {
        owner.clear()
        done.clear()
    }
}

class TwoWays(
    private val make: () -> DataSource,
    //: The machines to read from, asked for rather than handed over once. The second
    //: one is found while the film is already playing - the lookup takes about fifteen
    //: seconds - so a list fixed when the stream opened never had it, and every film
    //: was read off one machine however many held it.
    private val where: () -> List<String>,
    //: how long the film is, in seconds: nought when not known
    private val seconds: () -> Double = { 0.0 },
) : BaseDataSource(true) {

    companion object {
        /** how much one machine is asked for at a time: each ask costs about 70 ms */
        const val LUMP = 2L * 1024 * 1024

        /** how many lumps may be in hand or asked for: a film's memory, on top of the player's */
        const val AHEAD = 6

        /** a film this heavy needs more than one connection to each machine can bring */
        const val HEAVY_MBIT = 40.0

        /** and gets these: measured from the streamer, 193 Mbit against 75 for one connection.
         *  Not every film: a whole lump is waited for at the start and at each jump. */
        const val HEAVY_LUMP = 8L * 1024 * 1024
        const val HEAVY_CONNS = 4

        /** Whether a file of this size and length is heavy. Not known: it is not. */
        fun heavy(bytes: Long, seconds: Double): Boolean =
            seconds > 0 && bytes > 0 && bytes * 8.0 / seconds >= HEAVY_MBIT * 1_000_000

        /** how long before a machine holding the next lump is put aside */
        const val PATIENCE = 8_000L

        /** the soonest the next lump is asked of another machine as well */
        const val IMPATIENCE = 750L

        /** how many machines one film may be read from at once */
        const val WAYS = 4

        /** what each side has brought, for the line along the top of the picture */
        val brought = AtomicLongArray(WAYS)

        /** how many machines are set up for the film playing now */
        @Volatile var sides = 1

        /** whether every machine carrying the film has gone: nothing left to read */
        @Volatile var stranded = false

        /** When any side last brought something in, so a stall can be told from a
         *  machine that answers about itself and sends no film. */
        @Volatile var lastBrought = 0L

        /** how many of those machines are answering at this moment */
        @Volatile var carrying = 1

        /**
         * How many machines are joined to this film and have not been dropped.
         *
         * Not `carrying`, which asks who brought something in the last ten seconds:
         * on an easy film the quick machine keeps up alone and the slow one is left
         * out of the near work on purpose, so it goes minutes without a lump while
         * being perfectly well connected.
         */
        @Volatile var joined = 1

        fun forget() {
            for (i in 0 until WAYS) brought.set(i, 0)
            lastBrought = android.os.SystemClock.elapsedRealtime()
            sides = 1
            carrying = 1
            joined = 1
            stranded = false
        }

        /**
         * "two sources 60/40" - the share each machine has actually carried.
         *
         * Shown from the moment a second machine is set up, not from the moment it
         * first carries something: it read 100/0 for the first lumps and said nothing
         * at all, so a split that was working looked exactly like no split.
         */
        /** What each machine has carried, as percentages: "99/1". */
        fun split(): String? {
            val many = sides
            // one machine left standing is one source: no share beside "1/1"
            if (many < 2 || joined < 2) return null
            val each = (0 until many).map { brought.get(it) }
            val whole = each.sum().toDouble()
            if (whole <= 0) return null
            return each.joinToString("/") { got ->
                Math.round(got.toDouble() * 100.0 / whole).toString()
            }
        }
    }

    private class Lump(val bytes: ByteArray, val size: Int)

    private val gate = java.lang.Object()
    private var spec: DataSpec? = null
    private var head = 0L                   // the next byte the player will read
    private var lump = LUMP
    private var line = LumpLine<Lump>(LUMP, AHEAD)
    //: lumps' memory used again: a new block for each freed 26 MB every 2.5 s at this rate
    private val spare = ArrayList<ByteArray>()
    private val alive = BooleanArray(WAYS) { true }
    //: seconds each side has spent fetching, against what it brought: its speed
    private val spent = DoubleArray(WAYS)
    //: when each side last brought something. A machine that has been switched off
    //: is still "alive" until the read it is holding times out, and counting it as
    //: carrying the film is how the line read two sources with one machine off.
    private val fetched = LongArray(WAYS)
    private val stop = AtomicBoolean(false)
    private var fault: IOException? = null
    //: whether the machine told us how long the film is when the stream was opened
    private var measured = false
    private var readers = listOf<Thread>()
    private var held: Lump? = null
    private var heldAt = 0

    /** when the starved line was last written, so it is one a second and not a flood */
    private var said = 0L

    override fun getUri(): Uri? = spec?.uri

    override fun open(dataSpec: DataSpec): Long {
        transferInitializing(dataSpec)
        spec = dataSpec
        forget()
        // One plain open, only to learn how much film is left from here. The bytes
        // themselves all come from the readers.
        //
        // Asked of each machine in turn rather than only of the first. This asked the
        // first and let the answer through, so a machine switched off mid-film failed
        // the open - and an open is what happens at every seek and every new stretch
        // of the film. The error went straight out to the player, which stopped and
        // started the whole thing again somewhere else: the one moment this was built
        // to carry, and it fell at the first step.
        var left = C.LENGTH_UNSET.toLong()
        var opened = false
        var trouble: IOException? = null
        for (i in alive.indices) alive[i] = true
        val ways = where()
        for (side in alive.indices) {
            val url = ways.getOrNull(side).orEmpty()
            if (side > 0 && url.isEmpty()) continue   // not known yet; it may arrive
            val one = make()
            try {
                val at = if (side == 0) dataSpec
                         else dataSpec.buildUpon().setUri(Uri.parse(url)).build()
                left = one.open(at)
                opened = true
            } catch (e: Exception) {
                alive[side] = false       // it is not answering; the other one carries
                trouble = IOException("neither machine is answering", e)
            } finally {
                runCatching { one.close() }
            }
            if (opened) break
        }
        if (!opened) throw trouble ?: IOException("neither machine is answering")
        head = dataSpec.position
        measured = left != C.LENGTH_UNSET.toLong()
        val big = measured && heavy(head + left, seconds())
        lump = if (big) HEAVY_LUMP else LUMP
        val conns = if (big) HEAVY_CONNS else 1
        line = LumpLine<Lump>(lump, AHEAD)
        line.start(head, if (!measured) Long.MAX_VALUE else head + left)
        stop.set(false)
        fault = null
        readers = alive.indices.flatMap { side ->
            (0 until conns).map { conn ->
                Thread({ fetchFor(side) }, "palladium-way-" + side + "-" + conn).also {
                    it.isDaemon = true
                    it.start()
                }
            }
        }
        transferStarted(dataSpec)
        return left
    }

    /** The next lump for this machine, or -1 when there is no more film. */
    private fun claim(side: Int): Long {
        synchronized(gate) {
            while (!stop.get() && alive[side]) {
                val at = line.claim(side) { alive[it] }
                if (at == LumpLine.END) return -1L
                if (at != LumpLine.WAIT) return at
                gate.wait(200)
            }
        }
        return -1L
    }

    /** A lump's memory, used again where there is one to spare. Held under [gate]. */
    private fun recycle(bytes: ByteArray) {
        if (bytes.size.toLong() == lump && spare.size < AHEAD + 2) spare.add(bytes)
    }

    /** One machine, taking whatever lump is next while there is room for it. */
    private fun fetchFor(side: Int) {
        val src = make()
        var wait = 5_000L               // how long before a failed side is tried again
        // Not "while this side is alive". Dropping a side is what sets that false, so
        // a thread that had just dropped its own machine ended at the next turn of the
        // loop - and asking the machine again, and letting it back in, are both things
        // only this thread does. One connection that timed out therefore put a machine
        // out of the film for good, however well it was answering a second later.
        // It runs until the film does; being dropped only means it waits and asks.
        while (!stop.get()) {
            if (!alive[side]) {
                // dropped: wait, ask, and come back in when it answers
                runCatching { Thread.sleep(wait) }
                val url0 = where().getOrNull(side).orEmpty()
                if (url0.isEmpty()) continue
                val alone = synchronized(gate) { standing() } <= 1
                wait = if (alone) minOf(wait, 5_000L) else minOf(wait * 2, 60_000L)
                if (!answers(url0)) {
                    android.util.Log.i("Palladium", "way " + side +
                        " still not answering (" + Servers.hostOf(url0) + ")")
                    continue
                }
                android.util.Log.i("Palladium", "way " + side + " asked again (" +
                    Servers.hostOf(url0) + ")")
                revive(side)
                wait = 5_000L
                continue
            }
            // Asked for every lump. The second machine is found a quarter of a minute
            // into the film, and a side that read its address once was still holding
            // an empty one long after there was something there.
            val url = where().getOrNull(side).orEmpty()
            if (url.isEmpty()) {
                if (side == 0) break              // no film to read at all
                runCatching { Thread.sleep(500) } // waiting to be told where it is
                continue
            }
            synchronized(gate) {
                if (side + 1 > sides) sides = side + 1
                counted()
            }
            val at = claim(side)
            if (at < 0) break
            val want = minOf(lump, synchronized(gate) { line.end } - at).toInt()
            if (want <= 0) break
            var open = false
            val took = android.os.SystemClock.elapsedRealtime()
            try {
                src.open(DataSpec.Builder()
                             .setUri(Uri.parse(url))
                             .setPosition(at)
                             .setLength(want.toLong())
                             .build())
                open = true
                val bytes = synchronized(gate) {
                    if (want.toLong() == lump && spare.isNotEmpty()) spare.removeAt(spare.size - 1)
                    else ByteArray(want)
                }
                var got = 0
                while (got < want && !stop.get()) {
                    val n = src.read(bytes, got, want - got)
                    if (n == C.RESULT_END_OF_INPUT) break
                    got += n
                }
                src.close()
                open = false
                if (stop.get()) break
                brought.addAndGet(side, got.toLong())
                lastBrought = android.os.SystemClock.elapsedRealtime()
                synchronized(gate) {
                    spent[side] += (android.os.SystemClock.elapsedRealtime() - took) / 1000.0
                    fetched[side] = android.os.SystemClock.elapsedRealtime()
                    counted()
                }
                wait = 5_000L                     // answering again: start over
                if (got < want && measured && at + got < synchronized(gate) { line.end }) {
                    // Short of what was asked for, with the length known from the
                    // open: the machine cut its answer short, which a busy one does.
                    // This used to be read as the film ending there - so the player
                    // was told the film had finished, dropped back to the shelf, and
                    // playing it again started from the beginning. The stretch goes
                    // back to be fetched again, from whichever machine will have it.
                    throw IOException("short answer at " + at)
                }
                synchronized(gate) {
                    // short of what was asked for means the film ends here - only
                    // when nobody said how long it was
                    if (got < want) line.end = minOf(line.end, at + got)
                    // the slower of two copies: kept, it held a place in the window for good
                    if (!line.store(at, Lump(bytes, got))) recycle(bytes)
                    gate.notifyAll()
                }
            } catch (e: Throwable) {
                // Throwable, not Exception: a lump that will not fit in memory is an
                // Error, and an Error let out of a thread is the whole app gone -
                // which is what a film reading off a machine that had been switched
                // off looked like from the sofa.
                if (open) runCatching { src.close() }
                // and said out loud. A side going quiet left no trace at all, so a
                // film coming off one machine when two were holding it could not be
                // told from one where the other machine had never been asked.
                android.util.Log.i("Palladium", "way " + side + " dropped at " + at +
                    " (" + Servers.hostOf(url) + "): " +
                    e.javaClass.simpleName + " " + (e.message ?: ""))
                drop(side, at)
                // and then it waits and tries again. A machine is switched off and
                // on again while one film plays, and a side that gave up for good
                // meant the second half of the evening came off one machine however
                // many were back. The lump it dropped is already with the other one.
                // and the loop above takes it from here: it waits, asks the
                // machine whether it is there, and lets it back in when it answers.
                // Asked before being let back in because a machine that has been
                // switched off drops the packets rather than refusing them, so
                // letting it straight back in hands it the very stretch the picture
                // is waiting on and then spends five seconds finding out.
                if (stop.get() || fault != null) break
            }
        }
        runCatching { src.close() }
    }

    /**
     * Whether that machine is there at all, asked cheaply and answered quickly.
     *
     * A second and a half: a machine on the same network answers in milliseconds, and
     * one that is off never answers at all. This is the difference between finding
     * that out on a thread nobody is waiting on and finding it out on the stretch the
     * picture needs next.
     */
    private fun answers(url: String): Boolean = runCatching {
        val at = java.net.URL(url)
        val conn = Net.open(at.protocol + "://" + at.authority + "/app/version", 1_500, 1_500)
        try {
            conn.responseCode in 200..499     // answering at all is the question
        } finally {
            conn.disconnect()
        }
    }.getOrDefault(false)

    /** Let a side back in, once it is answering again. */
    private fun revive(side: Int) {
        synchronized(gate) {
            if (stop.get() || fault != null || alive[side]) return
            alive[side] = true
            counted()
            gate.notifyAll()
        }
    }

    /**
     * How long to wait on the machine holding the stretch the player wants.
     *
     * About twice what the quickest machine standing would need to fetch it instead;
     * past that it is asked of that machine as well. Held under [gate].
     */
    private fun patienceFor(side: Int): Long {
        val best = (0 until minOf(sides, alive.size))
            .filter { alive[it] && it != side }.map { speed(it) }.maxOrNull() ?: 0.0
        if (best <= 0.0) return PATIENCE
        return (lump / best * 2000).toLong().coerceIn(IMPATIENCE, PATIENCE)
    }

    /** Bytes a second a side has managed, or nought before it has brought anything. */
    private fun speed(side: Int): Double {
        val t = spent[side]
        return if (t <= 0.0) 0.0 else brought.get(side) / t
    }

    /** How many of the machines set up for this film are answering. Held under [gate]. */
    /**
     * How many machines are standing, out of the ones there are.
     *
     * Not `alive.count` - that array has room for four whether or not four machines
     * are holding the film, and every slot starts true. Counting the empty ones made
     * a room of two look like a room of four, and a room of one look like three.
     */
    private fun standing(): Int =
        (0 until minOf(sides, alive.size)).count { alive[it] }

    private fun counted() {
        // Carrying, not merely not-yet-dropped: a side counts when it has brought
        // something in the last ten seconds.
        val now = android.os.SystemClock.elapsedRealtime()
        carrying = maxOf(1, (0 until minOf(sides, alive.size)).count {
            alive[it] && fetched[it] > 0 && now - fetched[it] < 10_000
        })
        joined = maxOf(1, standing())
    }

    /** Drop a side and hand back whatever it had taken on but not brought. */
    private fun drop(side: Int, at: Long) {
        synchronized(gate) {
            if (!alive[side]) return
            alive[side] = false
            counted()
            if (standing() == 0) {
                stranded = true
                fault = IOException("neither machine is answering")
            } else {
                // everything that side had taken on and not brought goes back
                line.giveBack(side)
            }
            gate.notifyAll()
        }
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        if (held == null) {
            if (head >= synchronized(gate) { line.end }) return C.RESULT_END_OF_INPUT
            var since = android.os.SystemClock.elapsedRealtime()
            synchronized(gate) {
                while (held == null) {
                    fault?.let { throw it }
                    val one = line.take { it.size }
                    if (one != null) {
                        held = one
                        heldAt = 0
                        break
                    }
                    if (head >= line.end) return C.RESULT_END_OF_INPUT
                    val waited = android.os.SystemClock.elapsedRealtime() - since
                    val who = line.nextOwner
                    val standing = standing()
                    // running late: the other machine fetches it too, and nobody is put aside
                    if (who >= 0 && standing > 1 && waited > patienceFor(who) && line.late())
                        gate.notifyAll()
                    if (waited > PATIENCE) {
                        if (who >= 0 && alive[who] && standing > 1) {
                            // answering about itself, sending no film: put it aside
                            alive[who] = false
                            line.giveBack(who)
                        } else if (standing > 0) {
                            line.giveBackNext()
                        } else {
                            throw IOException("nothing for " + PATIENCE + "ms")
                        }
                        gate.notifyAll()
                        since = android.os.SystemClock.elapsedRealtime()
                    }
                    // and said out loud, once a second, while it is starved. Which
                    // stretch is wanted, who was given it, how much is in hand and
                    // what each machine is thought to be doing: without these the
                    // only account of a reader going quiet is that the picture
                    // stopped, which says nothing about why.
                    val moment = android.os.SystemClock.elapsedRealtime()
                    if (moment - said > 1_000) {
                        said = moment
                        android.util.Log.i("Palladium",
                            "starved at " + head + " of " + line.end +
                            " - owner " + line.nextOwner +
                            ", " + line.inHand + " lumps in hand" +
                            ", claimed to " + line.claimedTo +
                            ", alive " + alive.joinToString(",") { if (it) "y" else "n" } +
                            ", brought " + (0 until minOf(sides, WAYS)).joinToString(",") {
                                (brought.get(it) / 1024 / 1024).toString() + "MB"
                            })
                    }
                    gate.wait(250)
                }
                gate.notifyAll()
            }
        }
        val one = held ?: return C.RESULT_END_OF_INPUT
        val take = minOf(length, one.size - heldAt)
        System.arraycopy(one.bytes, heldAt, buffer, offset, take)
        heldAt += take
        head += take
        if (heldAt >= one.size) {
            held = null
            synchronized(gate) { recycle(one.bytes) }
        }
        bytesTransferred(take)
        return take
    }

    override fun close() {
        stop.set(true)
        synchronized(gate) { gate.notifyAll() }
        readers.forEach { runCatching { it.join(300) } }
        readers = listOf()
        synchronized(gate) {
            line.clear()
            spare.clear()
        }
        held = null
        if (spec != null) {
            spec = null
            transferEnded()
        }
    }
}

/**
 * Makes a two-source reader for the film, and a plain one for everything else.
 *
 * Only the film's own address is split: a subtitle is four kilobytes and a thumbnail
 * is one request. Both machines must be serving the file itself - a transcode is made
 * as it is sent, so two of them are not the same bytes and cannot be joined.
 */
class TwoWaysFactory(
    private val inner: DataSource.Factory,
    private val listener: androidx.media3.datasource.TransferListener?,
) : DataSource.Factory {

    /** the film's address here, and the same film on every other machine that has it */
    @Volatile var main: String = ""
    @Volatile var twins: List<String> = emptyList()
    /** how long the film is, which says whether it is heavy; nought when not known */
    @Volatile var seconds: Double = 0.0

    override fun createDataSource(): DataSource {
        // Built whether or not the second machine is known yet. Deciding here meant
        // deciding before the copy had been found, which takes about fifteen seconds
        // of the film: the plain source was chosen every time, the share never
        // appeared on the screen, and a server switched off mid-film was a hand-over
        // rather than the split this exists to be.
        val src: DataSource = Split(inner, { main }, { twins }, { seconds })
        listener?.let { src.addTransferListener(it) }
        return src
    }

    /** Two ways for the film, one plain source for anything else asked for. */
    private class Split(
        private val inner: DataSource.Factory,
        private val here: () -> String,
        private val there: () -> List<String>,
        private val seconds: () -> Double,
    ) : DataSource {
        private var real: DataSource? = null
        private val listeners = mutableListOf<androidx.media3.datasource.TransferListener>()

        override fun addTransferListener(
            listener: androidx.media3.datasource.TransferListener,
        ) {
            listeners.add(listener)
            real?.addTransferListener(listener)
        }

        override fun open(dataSpec: DataSpec): Long {
            val asked = dataSpec.uri.toString()
            val film = here()
            val one = if (film.isNotEmpty() && asked == film)
                          TwoWays({ inner.createDataSource() },
                                  { (listOf(film) + there()).take(TwoWays.WAYS) }, seconds)
                      else inner.createDataSource()
            listeners.forEach { one.addTransferListener(it) }
            real = one
            return one.open(dataSpec)
        }

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int =
            real?.read(buffer, offset, length) ?: C.RESULT_END_OF_INPUT

        override fun getUri(): Uri? = real?.uri

        override fun getResponseHeaders(): Map<String, List<String>> =
            real?.responseHeaders ?: emptyMap()

        override fun close() {
            real?.close()
            real = null
        }
    }
}
