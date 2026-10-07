package se.palladium.tv

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Which servers answer, asked in the background before and while anything is fetched.
 *
 * Every address of every known server is asked "who are you" at once, with a short
 * wait, at start, every ten seconds while the app is open, on a network change and
 * after a request that could not connect. Requests then go to a server known to be up
 * and never wait on one known to be down: the library came from the main server's
 * address, its address again, its outside address and only then the cache - eight
 * seconds a step - while the main server was off.
 */
object Reach {
    private val doors = ConcurrentHashMap<String, Route.Look>()
    private val first = CountDownLatch(1)
    private val wake = Channel<Unit>(Channel.CONFLATED)
    @Volatile private var started = false
    @Volatile private var app: Context? = null
    /** Screens of this app on view; nothing is asked while there are none. */
    @Volatile var onView = 0

    /** Bumped when anything changes, so screens can draw again. */
    val changed = androidx.compose.runtime.mutableStateOf(0)

    private const val EVERY_MS = 10_000L
    /** an answer older than this is no answer */
    private const val FRESH_MS = 45_000L
    /** a first miss is looked at again this soon, not in ten seconds - and not
     *  sooner: a server gets this long to answer before its copy is opened */
    private const val AGAIN_MS = 1_700L
    /** one look, name lookup included, which no connect timeout bounds */
    private const val LOOK_MS = 9_500L
    /** this device had no network at the last look: nothing was learned about servers */
    @Volatile private var noNetwork = false

    private fun now() = System.currentTimeMillis()

    /** when the last look, and the decision made from it, finished */
    @Volatile private var lookedAt = 0L

    /**
     * Wait, at most this long, for a look newer than [than] ms. Coming back to the
     * app while it stands on the copy, the main server is asked before anything is
     * fetched: a shelf drawn from the copy and then again from the main server a
     * moment later is the library changing under the hand.
     */
    fun awaitFresh(ms: Long, than: Long = 15_000L) {
        if (!started || onView <= 0 || now() - lookedAt < than) return
        val had = lookedAt
        poke()
        val until = now() + ms
        while (now() < until && lookedAt == had) Thread.sleep(40)
    }

    fun start(ctx: Context) {
        app = ctx.applicationContext
        if (started) return
        started = true
        @OptIn(kotlinx.coroutines.DelicateCoroutinesApi::class)
        GlobalScope.launch(Dispatchers.IO) {
            while (true) {
                if (onView > 0 || first.count > 0L) {
                    val said = runCatching { check() }.getOrNull()
                    first.countDown()
                    runCatching { decide() }
                    lookedAt = now()
                    // the report after the answer, and beside it: sent while the
                    // library was still on a house address away from home, it held
                    // the first answer back eight seconds and every request with it
                    if (!said.isNullOrEmpty()) launch { runCatching { Api.trace(said) } }
                }
                // one miss is not believed: the second look comes at once, not in ten seconds
                val unsure = noNetwork || doors.values.any {
                    it.misses > 0 && !it.down && now() - it.at < FRESH_MS }
                withTimeoutOrNull(if (unsure && onView > 0) AGAIN_MS else EVERY_MS) {
                    wake.receive() }
            }
        }
    }

    /** Ask again now: a request could not connect, or the network changed. */
    fun poke() { wake.trySend(Unit) }

    /** Wait, at most this long, for the first round of answers. */
    fun awaitFirst(ms: Long) {
        if (!started) return
        runCatching { first.await(ms, TimeUnit.MILLISECONDS) }
    }

    private fun key(at: String) = at.trimEnd('/')

    /** What is believed of an address after this look. */
    private fun put(at: String, raw: Route.Look) {
        doors.compute(at) { _, had -> Route.settle(had, raw, now(), FRESH_MS) }
    }

    /** Known to be up, known to be down, or not known. Down takes two looks. */
    fun upAt(at: String): Boolean? = Route.state(doors[key(at)], now(), FRESH_MS)

    /** A request that set out at [began] could not connect there: at most the first
     *  miss, and the check looks again now. */
    fun missed(at: String, began: Long) {
        val k = key(at)
        if (!Api.homeHere(k)) {
            doors[k] = Route.never(now(), doors[k]?.id.orEmpty())
        } else {
            doors.compute(k) { _, had ->
                Route.requestMiss(had, began, now(), FRESH_MS) ?: had
            }
        }
        poke()
    }

    /**
     * Wait, at most this long, for the look that follows a miss: the address is
     * then up or down, and the request that missed knows where to go.
     */
    fun settled(at: String, ms: Long) {
        if (onView <= 0) return
        val k = key(at)
        val until = now() + ms
        while (now() < until) {
            val d = doors[k] ?: return
            if (d.misses == 0 || d.down) return
            Thread.sleep(50)
        }
    }

    /** The player found it gone and has moved: down until it is seen answering. */
    fun down(at: String) {
        val k = key(at)
        doors[k] = Route.never(now(), doors[k]?.id.orEmpty())
        poke()
    }

    /**
     * A row's way in from outside when it never learned one: two machines behind one
     * router share the address the internet sees, each on its own port, so a house
     * filed under its network address is tried on the copy's outside address with
     * its own port.
     */
    fun derivedDoors(s: Server): List<String> {
        val ctx = app ?: return emptyList()
        if (s.outside.isNotEmpty()) return emptyList()
        return listOf(Api.outsideFor(ctx, s)).map { key(it) }.filter { it.isNotEmpty() }
    }

    /** Every address a row may answer on. */
    private fun ways(s: Server): List<String> =
        (listOf(s.base, s.outside).map { key(it) } + derivedDoors(s))
            .filter { it.isNotEmpty() }.distinct()

    /** The address of this row that answers as this machine, the near one first. */
    fun doorOf(s: Server): String? =
        Route.door(ways(s), doors, s.id, now(), FRESH_MS) { Servers.athome(it) }

    /** Up on some address, down on all it was asked on, or not known yet. */
    fun upRow(s: Server): Boolean? =
        Route.rowUp(ways(s), doors, s.id, now(), FRESH_MS) { Servers.athome(it) }

    /** Whether this row is the machine keeping copies of that one, by the row or by
     *  what its own server said it follows. */
    fun keepsCopiesOf(r: Server, of: Server): Boolean {
        if (Servers.sameMachine(r, of)) return false
        if (r.copyOf.isNotEmpty() && Servers.sameMachine(Server("", r.copyOf, ""), of))
            return true
        val d = doorOf(r)?.let { doors[it] } ?: return false
        return (of.id.isNotEmpty() && d.followsId == of.id) ||
            (d.follows.isNotEmpty() && Servers.sameMachine(Server("", d.follows, ""), of))
    }

    /** Every address of every row, asked at once. What changed, as a line, or null. */
    @OptIn(kotlinx.coroutines.DelicateCoroutinesApi::class)
    private suspend fun check(): String? {
        val ctx = app ?: return null
        val rows = Servers.all(ctx)
        val asks = rows.flatMap { s ->
            ways(s).map { it to Servers.withKey(rows, s).token }
        }.distinctBy { it.first }
        val before = doors.keys.associateWith { upAt(it) }
        // not children of this look: a name lookup that hangs is left behind when the
        // time is up, where a child would have held every later look until it returned
        val looks = asks.map { (at, tok) ->
            at to GlobalScope.async(Dispatchers.IO) { runCatching { probe(at, tok) } }
        }
        val until = now() + LOOK_MS
        var none = false
        looks.forEach { (at, job) ->
            val got = withTimeoutOrNull(maxOf(1L, until - now())) { job.await() }
            val raw = if (got == null) Route.miss(now(), doors[at]?.id.orEmpty())
                      else got.getOrNull()
            if (raw == null) none = true else put(at, raw)
        }
        noNetwork = none
        val after = doors.keys.associateWith { upAt(it) }
        if (after == before) return null
        changed.value = changed.value + 1
        return "reach: " + doors.entries.joinToString(" ") {
            Servers.hostOf(it.key) + when (upAt(it.key)) {
                true -> " up " + it.value.ms + "ms" + (if (it.value.misses > 0) " (missed)" else "")
                false -> " down"
                else -> " missed"
            }
        }
    }

    /** Who answers at this address, read from one connection. */
    private fun ask(at: String, tok: String, asAt: String, began: Long): Route.Look {
        // a server on this network connects in milliseconds; from outside, a second,
        // and a phone's radio waking is another: Net.open gives outside four
        val conn = Net.open(at + "/where" + (if (tok.isEmpty()) "" else "?t=" + tok),
                            1500, 4000, tok)
        val said = try {
            JSONObject(conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) })
        } catch (e: java.io.FileNotFoundException) {
            // an older server without /where: it answered, so it is there
            return Route.Look(true, "", now() - began, now())
        } catch (e: java.net.SocketTimeoutException) {
            if ((e.message ?: "").contains("connect", ignoreCase = true)) throw e
            // connected and then slow: there, and as it was last known
            val had = doors[asAt]
            return had?.copy(up = true, ms = now() - began, at = now(), misses = 0)
                ?: Route.Look(true, "", now() - began, now())
        }
        val f = said.optJSONObject("follows")
        return Route.Look(true, said.optString("id"), now() - began, now(),
            f?.optString("lan").orEmpty().ifEmpty { f?.optString("outside").orEmpty() },
            f?.optString("id").orEmpty())
    }

    /** One address: who answers there, and how fast. Throws when this device has no
     *  network at all, which says nothing about the server. */
    private fun probe(at: String, tok: String): Route.Look {
        val began = now()
        val id = doors[at]?.id.orEmpty()
        // a house address from another network is not there: said at once, rather
        // than waited on for its connect time while the outside door is the answer
        if (!Api.homeHere(at)) return Route.never(began, id)
        return try {
            ask(at, tok, at, began)
        } catch (e: java.net.UnknownHostException) {
            // the name would not resolve here: its last known address. Both outside
            // addresses share one name, so a failed lookup was both servers down at once
            val byNumber = KnownHosts.byAddress(at) ?: return Route.miss(now(), id)
            try {
                ask(byNumber, tok, at, began)
            } catch (again: Exception) {
                if (unreachableHere(again)) throw again
                Route.miss(now(), id)
            }
        } catch (e: Exception) {
            if (unreachableHere(e)) throw e
            Route.miss(now(), id)
        }
    }

    /** This device has no network: not a look at the server at all. */
    fun unreachableHere(e: Throwable): Boolean =
        (e.message ?: "").contains("ENETUNREACH") ||
            (e.cause?.message ?: "").contains("ENETUNREACH")

    /** Decide now, from what is known: called by a request that found its server off. */
    fun route() { runCatching { decide() } }

    /**
     * The one decision about where the library is, made from the last look at every
     * address - every request, picture and stream follows it. The rules are
     * [Route.decide]; this reads what they need and carries out the move. Coming home
     * from the copy is confirmed with a request first, because the look can be ten
     * seconds old and the player may have just found that server gone.
     */
    @Synchronized
    private fun decide() {
        val ctx = app ?: return
        val rows = Servers.all(ctx)
        val chosen = Servers.current(ctx) ?: return
        val base = key(Api.base)
        val main = rows.firstOrNull { r -> keepsCopiesOf(chosen, r) }
        val home = main ?: chosen
        val copy = if (main != null) chosen
                   else rows.firstOrNull { r -> keepsCopiesOf(r, chosen) && doorOf(r) != null }
        val d = Route.decide(Route.Seen(
            chosenIsCopy = main != null,
            homeUnlooked = Api.homeUnlooked(),
            prefersCopy = Api.prefersTheCopy,
            standingBy = Api.standingBy(),
            base = base,
            baseUsable = Api.homeHere(base) && upAt(base) != false,
            mainUp = upRow(home),
            mainDoor = doorOf(home),
            mainTry = ways(home).firstOrNull { Api.homeHere(it) && upAt(it) != false },
            copyDoor = copy?.let { doorOf(it) },
            mainBase = key(home.base),
            homeBase = Api.homeBaseNow()))
        if (d.looked) Api.homeLookedFor()
        when (val m = d.move) {
            is Route.Move.OpenMain -> {
                say("opened on the copy; home at " + m.door)
                Servers.use(ctx, home)
                Api.swapped.value = Api.swapped.value + 1
            }
            // moved first, said after: the report goes to where the library now is,
            // and one sent to the server just left was lost with it
            is Route.Move.Library -> {
                Api.routeTo(m.door, standby = false)
                say("library at " + m.door)
            }
            is Route.Move.Standby -> {
                Api.routeTo(m.door, standby = true, home = m.home)
                say((if (m.asked) "the main server asks for the copy: "
                     else "main server off; to the copy at ") + m.door)
            }
            is Route.Move.HomeAgain ->
                if (Api.comeHomeAt(m.door)) say("home again at " + m.door)
            Route.Move.Nothing -> say("nothing answers")
            Route.Move.Stay -> {}
        }
    }

    @Volatile private var lastSaid = ""

    /** Each move, once, in the log and to the server: from a phone somewhere else,
     *  why it is on the machine it is on cannot be seen from the house. */
    @OptIn(kotlinx.coroutines.DelicateCoroutinesApi::class)
    private fun say(what: String) {
        if (what == lastSaid) return
        lastSaid = what
        android.util.Log.i("Palladium", "reach: " + what)
        GlobalScope.launch(Dispatchers.IO) { runCatching { Api.trace("route: " + what) } }
    }
}
