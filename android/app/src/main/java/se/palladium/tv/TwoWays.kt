package se.palladium.tv

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
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
 * The lumps reach the player in order, so one that arrives early waits its turn. That
 * is why a lump is two megabytes and not twenty - the longest the quick machine can
 * ever wait on the slow one is the time the slow one needs for a single lump.
 *
 * A side that stops answering - a server switched off at ten - is dropped and its lump
 * asked of the other, with the picture still running. The player sees no error and
 * nothing restarts. Only when both sides are gone does this fail, and then it fails as
 * an ordinary read and the film moves house the old way.
 */
class TwoWays(
    private val make: () -> DataSource,
    //: The machines to read from, asked for rather than handed over once. The second
    //: one is found while the film is already playing - the lookup takes about fifteen
    //: seconds - so a list fixed when the stream opened never had it, and every film
    //: was read off one machine however many held it.
    private val where: () -> List<String>,
) : BaseDataSource(true) {

    companion object {
        /** how much one machine is asked for at a time */
        const val LUMP = 2L * 1024 * 1024

        /** how many lumps may be in hand or in flight before a reader waits */
        const val AHEAD = 6

        /** the longest anything is ever waited for, whatever the sums say */
        const val PATIENCE = 8_000L

        /** and the shortest: below this a busy moment reads as a machine gone */
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

    private class Lump(val bytes: ByteArray)

    private val gate = java.lang.Object()
    private var spec: DataSpec? = null
    private var end = 0L                    // one past the last byte wanted
    private var head = 0L                   // the next byte the player will read
    private var claimed = 0L                // the next byte nobody has taken on
    private val owner = ConcurrentHashMap<Long, Int>()
    private val done = ConcurrentHashMap<Long, Lump>()
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
        claimed = head
        measured = left != C.LENGTH_UNSET.toLong()
        end = if (!measured) Long.MAX_VALUE else head + left
        stop.set(false)
        fault = null
        readers = alive.indices.map { side ->
            Thread({ fetchFor(side) }, "palladium-way-" + side).also {
                it.isDaemon = true
                it.start()
            }
        }
        transferStarted(dataSpec)
        return left
    }

    /** The next lump nobody has taken on, or -1 when there is no more film. */
    private fun claim(side: Int): Long {
        synchronized(gate) {
            while (!stop.get() && alive[side]) {
                if (claimed >= end) return -1L
                val at = claimed
                claimed += LUMP
                // a lump already in hand, from before a side was dropped
                if (done.containsKey(at)) continue
                // Do not run ahead of the player: these are held in memory. Counted
                // as lumps in hand rather than as a distance, because a side being
                // dropped winds the distance back to where the player is - so with a
                // machine switched off and retried the range said there was room
                // while the heap filled, and the film died of it at 256 MB.
                //
                // Except the one the player is waiting on. Lumps arrive in any order
                // and are read in order, so a side that took this one on and went
                // quiet left the six behind it in hand - a full window, none of it
                // readable, and the machine still answering was not allowed to fetch
                // the one lump that would have emptied it. It is the only lump that
                // can make room, so it can never be the one refused for want of room.
                if (at > head && (done.size >= AHEAD || at - head > AHEAD * LUMP)) {
                    claimed = at
                    gate.wait(200)
                    continue
                }
                // Lumps reach the player in order, so one taken on by a slow machine
                // holds up every lump behind it while the quick one waits. A machine
                // many times slower was taking one lump in six and the film stopped
                // on each of them.
                //
                // So a slow machine takes only work the player is not about to need:
                // the far half of what is in hand, never the lump about to be read.
                // No timing arithmetic - an earlier attempt weighed a lump's fetch
                // time against how fast the quick machine could clear the queue,
                // which is not how long the player has, and starved the slow one
                // altogether. If the quick machine stops, its speed stops counting
                // and this one takes everything, which is the whole point of it.
                // and a side counts as quick only while it is still bringing
                // things in. This read the stopped machine's old speed - it keeps it,
                // nothing is ever subtracted - and went on leaving the near work to a
                // machine that had gone, for as long as it took its socket to notice.
                val warm = android.os.SystemClock.elapsedRealtime() - 5_000
                val ours = speed(side)
                val best = (0 until minOf(sides, alive.size))
                    .filter { alive[it] && (it == side || fetched[it] > warm) }
                    .map { speed(it) }.maxOrNull() ?: 0.0
                if (ours > 0.0 && best > ours * 2 && at - head < AHEAD * LUMP / 2) {
                    claimed = at                    // leave the near work to the quick one
                    gate.wait(50)
                    continue
                }
                owner[at] = side
                return at
            }
        }
        return -1L
    }

    /** One machine, taking whatever lump is next while there is room for it. */
    private fun fetchFor(side: Int) {
        val src = make()
        var wait = 5_000L               // how long before a failed side is tried again
        while (!stop.get() && alive[side]) {
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
            val want = minOf(LUMP, end - at)
            if (want <= 0) break
            var open = false
            val took = android.os.SystemClock.elapsedRealtime()
            try {
                src.open(DataSpec.Builder()
                             .setUri(Uri.parse(url))
                             .setPosition(at)
                             .setLength(want)
                             .build())
                open = true
                val bytes = ByteArray(want.toInt())
                var got = 0
                while (got < bytes.size && !stop.get()) {
                    val n = src.read(bytes, got, bytes.size - got)
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
                if (got < bytes.size && measured && at + got < end) {
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
                    if (got < bytes.size) end = minOf(end, at + got)
                    done[at] = Lump(if (got == bytes.size) bytes else bytes.copyOf(got))
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
                if (stop.get() || fault != null) break
                runCatching { Thread.sleep(wait) }
                // Backing off further and further suits a machine nobody needs. Down
                // to one machine it is the opposite: that is exactly when the second
                // one is wanted back, because the one that is left going away for a
                // moment - a restart, a busy disk - then has nothing behind it. So
                // the wait stops growing while this is the only side answering.
                val alone = synchronized(gate) { standing() } <= 1
                wait = if (alone) minOf(wait, 5_000L) else minOf(wait * 2, 60_000L)
                // Asked whether it is there before it is let back in. A machine that
                // has been switched off drops the packets rather than refusing them,
                // so letting it straight back into the rotation handed it the very
                // stretch the picture was waiting on and then spent five seconds
                // finding out - over and over, while the machine that was answering
                // sat there able to fetch it. The picture ran dry and the film moved
                // house, with a live machine holding the whole film.
                if (!answers(url)) {
                    android.util.Log.i("Palladium",
                        "way " + side + " still not answering (" +
                        Servers.hostOf(url) + ")")
                    continue
                }
                android.util.Log.i("Palladium",
                    "way " + side + " asked again (" + Servers.hostOf(url) + ")")
                revive(side)
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
        val conn = (java.net.URL(at.protocol + "://" + at.authority + "/app/version")
            .openConnection() as java.net.HttpURLConnection)
        conn.connectTimeout = 1_500
        conn.readTimeout = 1_500
        conn.requestMethod = "GET"
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
     * About twice what the quickest machine standing would need to fetch it instead.
     * Waiting longer than it would take to fetch again is time the picture spends
     * stopped for nothing - and it was a flat eight seconds, against a run of film in
     * hand that is nearer two. So a machine that went busy halfway through a stretch -
     * somebody else started watching off it - stopped the picture here for six seconds
     * before anything was done about it. Held under [gate].
     */
    private fun patienceFor(side: Int): Long {
        val best = (0 until minOf(sides, alive.size))
            .filter { alive[it] && it != side }.map { speed(it) }.maxOrNull() ?: 0.0
        if (best <= 0.0) return PATIENCE
        return (LUMP / best * 2000).toLong().coerceIn(IMPATIENCE, PATIENCE)
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
                // Everything that side had taken on and not brought goes back, not
                // only the lump it died on. Its name was left on the others, so the
                // reader waited on a machine that had gone and then gave up - with
                // the survivor sitting there able to fetch every one of them.
                owner.entries.removeAll { it.value == side }
                if (at in 0 until end) claimed = minOf(claimed, at)
                claimed = minOf(claimed, head)
            }
            gate.notifyAll()
        }
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        if (held == null) {
            if (head >= end) return C.RESULT_END_OF_INPUT
            var since = android.os.SystemClock.elapsedRealtime()
            synchronized(gate) {
                while (held == null) {
                    fault?.let { throw it }
                    val one = done.remove(head)
                    if (one != null) {
                        held = one
                        heldAt = 0
                        owner.remove(head)
                        break
                    }
                    if (head >= end) return C.RESULT_END_OF_INPUT
                    if (android.os.SystemClock.elapsedRealtime() - since >
                            patienceFor(owner[head] ?: -1)) {
                        // whoever took this lump on is not going to bring it
                        val slow = owner[head] ?: -1
                        val standing = standing()
                        if (slow >= 0 && alive[slow] && standing > 1) {
                            // it is still answering but not with this: put it aside
                            alive[slow] = false
                            owner.entries.removeAll { it.value == slow }
                            claimed = minOf(claimed, head)
                            gate.notifyAll()
                        } else if (standing > 0) {
                            // One machine left and this lump belongs to a dead one -
                            // or to nobody. It used to give up here, which is the
                            // whole of what went wrong when a server was switched off
                            // mid-film: the machine that could have carried on was
                            // sitting there, alive, with nothing asked of it. Hand
                            // the lump back and wait again.
                            owner.remove(head)
                            claimed = minOf(claimed, head)
                            gate.notifyAll()
                            since = android.os.SystemClock.elapsedRealtime()
                        } else {
                            throw IOException("nothing for " + PATIENCE + "ms")
                        }
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
                            "starved at " + head + " of " + end +
                            " - owner " + (owner[head] ?: -1) +
                            ", " + done.size + " lumps in hand" +
                            ", claimed to " + claimed +
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
        val take = minOf(length, one.bytes.size - heldAt)
        System.arraycopy(one.bytes, heldAt, buffer, offset, take)
        heldAt += take
        head += take
        if (heldAt >= one.bytes.size) held = null
        bytesTransferred(take)
        return take
    }

    override fun close() {
        stop.set(true)
        synchronized(gate) { gate.notifyAll() }
        readers.forEach { runCatching { it.join(300) } }
        readers = listOf()
        done.clear()
        owner.clear()
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

    override fun createDataSource(): DataSource {
        // Built whether or not the second machine is known yet. Deciding here meant
        // deciding before the copy had been found, which takes about fifteen seconds
        // of the film: the plain source was chosen every time, the share never
        // appeared on the screen, and a server switched off mid-film was a hand-over
        // rather than the split this exists to be.
        val src: DataSource = Split(inner, { main }, { twins })
        listener?.let { src.addTransferListener(it) }
        return src
    }

    /** Two ways for the film, one plain source for anything else asked for. */
    private class Split(
        private val inner: DataSource.Factory,
        private val here: () -> String,
        private val there: () -> List<String>,
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
                                  { (listOf(film) + there()).take(TwoWays.WAYS) })
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
