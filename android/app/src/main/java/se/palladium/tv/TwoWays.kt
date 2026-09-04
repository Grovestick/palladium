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
    private val ways: List<String>,
) : BaseDataSource(true) {

    companion object {
        /** how much one machine is asked for at a time */
        const val LUMP = 2L * 1024 * 1024

        /** how many lumps may be in hand or in flight before a reader waits */
        const val AHEAD = 6

        /** a side holding up the film this long has gone, whatever it thinks */
        const val PATIENCE = 8_000L

        /** what each side has brought, for the line along the top of the picture */
        val brought = AtomicLongArray(2)

        fun forget() {
            brought.set(0, 0)
            brought.set(1, 0)
        }

        /** "two sources 60/40", or nothing while only one of them is carrying */
        fun split(): String? {
            val a = brought.get(0)
            val b = brought.get(1)
            if (a <= 0 || b <= 0) return null
            val whole = (a + b).toDouble()
            return "two sources " + Math.round(a * 100 / whole) + "/" +
                Math.round(b * 100 / whole)
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
    private val alive = booleanArrayOf(true, true)
    private val stop = AtomicBoolean(false)
    private var fault: IOException? = null
    private var readers = listOf<Thread>()
    private var held: Lump? = null
    private var heldAt = 0

    override fun getUri(): Uri? = spec?.uri

    override fun open(dataSpec: DataSpec): Long {
        transferInitializing(dataSpec)
        spec = dataSpec
        forget()
        // One plain open, only to learn how much film is left from here. The bytes
        // themselves all come from the readers.
        val first = make()
        val left = try {
            first.open(dataSpec)
        } finally {
            runCatching { first.close() }
        }
        head = dataSpec.position
        claimed = head
        end = if (left == C.LENGTH_UNSET.toLong()) Long.MAX_VALUE else head + left
        stop.set(false)
        fault = null
        alive[0] = true
        alive[1] = true
        readers = ways.indices.map { side ->
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
                // do not run ahead of the player: these are held in memory
                if (at - head > AHEAD * LUMP) {
                    claimed = at
                    gate.wait(200)
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
        val url = ways[side]
        val src = make()
        while (!stop.get() && alive[side]) {
            val at = claim(side)
            if (at < 0) break
            val want = minOf(LUMP, end - at)
            if (want <= 0) break
            var open = false
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
                synchronized(gate) {
                    // short of what was asked for means the film ends here
                    if (got < bytes.size) end = minOf(end, at + got)
                    done[at] = Lump(if (got == bytes.size) bytes else bytes.copyOf(got))
                    gate.notifyAll()
                }
            } catch (e: Exception) {
                if (open) runCatching { src.close() }
                drop(side, at)
                return
            }
        }
        runCatching { src.close() }
    }

    /** Drop a side and hand back whatever it had taken on but not brought. */
    private fun drop(side: Int, at: Long) {
        synchronized(gate) {
            if (!alive[side]) return
            alive[side] = false
            if (alive.none { it }) {
                fault = IOException("neither machine is answering")
            } else if (at in 0 until end) {
                // the survivor claims again from there; what is already in hand is
                // skipped as it goes past
                claimed = minOf(claimed, at)
            }
            gate.notifyAll()
        }
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        if (held == null) {
            if (head >= end) return C.RESULT_END_OF_INPUT
            val since = android.os.SystemClock.elapsedRealtime()
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
                    if (android.os.SystemClock.elapsedRealtime() - since > PATIENCE) {
                        // whoever took this lump on is not going to bring it
                        val slow = owner[head] ?: -1
                        if (slow >= 0 && alive.count { it } > 1) {
                            alive[slow] = false
                            claimed = minOf(claimed, head)
                            gate.notifyAll()
                        } else {
                            throw IOException("nothing for " + PATIENCE + "ms")
                        }
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

    /** the film's address here, and the same film on the other machine */
    @Volatile var main: String = ""
    @Volatile var twin: String = ""

    override fun createDataSource(): DataSource {
        val here = main
        val there = twin
        val src: DataSource = if (here.isNotEmpty() && there.isNotEmpty()) {
            Split(inner, here, there)
        } else {
            inner.createDataSource()
        }
        listener?.let { src.addTransferListener(it) }
        return src
    }

    /** Two ways for the film, one plain source for anything else asked for. */
    private class Split(
        private val inner: DataSource.Factory,
        private val here: String,
        private val there: String,
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
            val one = if (asked == here) TwoWays({ inner.createDataSource() },
                                                 listOf(here, there))
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
