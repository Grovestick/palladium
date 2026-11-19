package se.palladium.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import se.palladium.tv.Route.Look
import se.palladium.tv.Route.Move

/**
 * The background check, played on a clock against servers that answer, are off or are frozen,
 * through the same Route rules Reach uses: settle, requestMiss, confirmDown, door, rowUp,
 * decide. Reach's own loop and request handling are copied here as they stand - looks every
 * 10 s, the second at once after a miss, a failed request counted and a look poked - so a rule
 * can be tried in seconds without an APK (owner, 2026-11-15: "simulate it without needing to
 * build all the time"). Each case prints its timeline.
 */
class ReachSimTest {

    /** SLOW: answers, in 2.6 s - a phone on mobile data, 2026-11-18 */
    enum class S { UP, OFF, FROZEN, SLOW }

    companion object {
        const val EVERY_MS = 10_000L
        const val AGAIN_MS = 1_700L
        const val FRESH_MS = 45_000L
        const val LOOK_MS = 9_500L
        const val REQ_EVERY_MS = 3_000L
        const val REQ_READ_MS = 8_000L
        const val MAIN_LAN = "http://192.168.0.181:8765"
        const val MAIN_OUT = "https://maverick.palladium.video:8765"
        const val COPY_LAN = "http://192.168.0.25:8764"
        const val COPY_OUT = "https://maverick.palladium.video:8764"
    }

    /** One run: the main server's state by time, whether film arrives from it, where the app starts. */
    class Sim(val main: (Long) -> S, val sending: (Long) -> Boolean = { false },
              val opening: Boolean = true, startOnMain: Boolean = true, val startAt: Long = 0L,
              /** the copy's own word on the main server: by default what it would see; false
               *  where it can still reach a main server this viewer cannot */
              val copySees: ((Long) -> S)? = null,
              /** when the screen is made again with the app still alive - opened from the
               *  launcher after a while away: start-up runs once more */
              val remadeAt: Long? = null,
              /** the app away from the screen from - to, looking at nothing */
              val awayFrom: Long? = null, val awayTo: Long? = null,
              /** start-up as the app has it: true - the route as Api.loadServer sets it */
              val startUp: (Sim) -> Unit = {
                  // as Api.loadServer: a route this run of the app already has for the server
                  // in use is kept; only a first start sets one
                  if (!Route.keepRoute(it.routedFor, it.routedFor, it.base)) {
                      it.base = MAIN_LAN; it.standby = false; it.homeBase = ""
                  }
              }) {
        val mainWays = listOf(MAIN_LAN, MAIN_OUT)
        val copyWays = listOf(COPY_LAN, COPY_OUT)
        val doors = HashMap<String, Look>()
        var base = if (startOnMain) MAIN_LAN else COPY_LAN
        var standby = false
        var homeBase = ""
        var unlooked = opening
        val said = ArrayList<String>()
        var remade = false
        /** the server this run of the app has routed for, as Api keeps it */
        var routedFor = MAIN_LAN
        val moves = ArrayList<Pair<Long, String>>()

        private fun athome(a: String) = a.startsWith("http://192.168.")
        private fun on(a: String, t: Long): S = if (a in mainWays) main(t) else S.UP

        /** what the copy's /where says of the main server at t: it asks every 10 s, and says
         *  down only when two asks in a row went unanswered */
        private fun houseWord(t: Long): Triple<Boolean, Boolean, Int> {
            val asked = (t / 10_000) * 10_000
            val seen = copySees ?: main
            // slow from a phone is quick from the copy beside it
            fun answers(s: S) = s == S.UP || s == S.SLOW
            val down = !answers(seen(asked)) && asked >= 10_000 && !answers(seen(asked - 10_000))
            return Triple(!down, answers(seen(asked)), ((t - asked) / 1000).toInt())
        }

        /** one address asked at t: the raw look and when it is known */
        private fun look(a: String, t: Long): Pair<Look, Long> {
            val id = doors[a]?.id.orEmpty()
            return when (on(a, t)) {
                S.UP -> {
                    val ms = if (athome(a)) 40L else 90L
                    if (a in copyWays) {
                        val (up, answered, ago) = houseWord(t)
                        Pair(Look(true, "C", ms, t + ms, follows = MAIN_LAN, followsId = "M",
                                  houseDown = Route.houseSaysDown(up, ago),
                                  houseUp = Route.houseSaysUp(answered, ago)), t + ms)
                    } else Pair(Look(true, "M", ms, t + ms), t + ms)
                }
                S.OFF -> Pair(Route.miss(t + 1500, id), t + 1500)
                S.FROZEN -> {
                    val patience = Route.lookPatience(unlooked, copySaidUp(t)).toLong()
                    Pair(Route.miss(t + 1500 + patience, id), t + 1500 + patience)
                }
                S.SLOW -> {
                    val patience = Route.lookPatience(unlooked, copySaidUp(t)).toLong()
                    if (patience < 2600) Pair(Route.miss(t + patience, id), t + patience)
                    else Pair(Look(true, "M", 2600, t + 2600), t + 2600)
                }
            }
        }

        /** as Reach.ask: the copy's last look said the main server answered it just now */
        private fun copySaidUp(t: Long) = doors.values.any {
            it.up && it.houseUp && t - it.at <= Route.HOUSE_WORD_MS }

        private fun upAt(a: String, now: Long) = Route.state(doors[a], now, FRESH_MS)

        /** one look, as Reach.check: each answer as it comes, stopping once it is known enough */
        private fun check(t: Long): Long {
            val all = (mainWays + copyWays).map { a -> a to look(a, t) }.sortedBy { it.second.second }
            val ways = mapOf("M" to mainWays, "C" to copyWays)
            var end = t
            for ((a, got) in all) {
                end = minOf(t + LOOK_MS, got.second)
                var raw = got.first
                if (!raw.up && a in mainWays && sending(end)) {
                    raw = doors[a]?.copy(up = true, at = end, misses = 0) ?: Look(true, "", 0, end)
                }
                doors[a] = Route.settle(doors[a], raw, end, FRESH_MS)
                Route.confirmDown(HashMap(doors), opening = unlooked, waysOf = ways, now = end)
                    .forEach { (k, d) -> doors[k] = d }
                val mainUp = Route.rowUp(mainWays, doors, "M", end, FRESH_MS) { athome(it) }
                val copyUp = Route.rowUp(copyWays, doors, "C", end, FRESH_MS) { athome(it) }
                if (Route.knownEnough(mainUp, copyUp)) break
            }
            return end
        }

        fun decide(t: Long) {
            val copyDoor = Route.door(copyWays, doors, "C", t, FRESH_MS) { athome(it) }
            val d = Route.decide(Route.Seen(
                chosenIsCopy = false, homeUnlooked = unlooked, prefersCopy = false,
                standingBy = standby, base = base, baseUsable = upAt(base, t) != false, baseHere = true,
                mainUp = Route.rowUp(mainWays, doors, "M", t, FRESH_MS) { athome(it) },
                mainDoor = Route.door(mainWays, doors, "M", t, FRESH_MS) { athome(it) },
                mainTry = mainWays.firstOrNull { upAt(it, t) != false },
                copyDoor = copyDoor, mainBase = MAIN_LAN, homeBase = homeBase))
            if (d.looked) unlooked = false
            val m = d.move
            if (remade && t < (remadeAt ?: 0) + 10_000)
                println("  decide %5.1f s base=%s standby=%s unlooked=%s mainUp=%s mainTry=%s copy=%s -> %s".format(
                    t / 1000.0, base, standby, unlooked,
                    Route.rowUp(mainWays, doors, "M", t, FRESH_MS) { athome(it) },
                    mainWays.firstOrNull { upAt(it, t) != false }, copyDoor, m))
            when (m) {
                is Move.Library -> { base = m.door; standby = false; note(t, "library at " + m.door) }
                is Move.Standby -> { base = m.door; standby = true; homeBase = m.home; note(t, "to the copy at " + m.door) }
                is Move.HomeAgain -> if (main(t) == S.UP || main(t) == S.SLOW) { base = m.door; standby = false; note(t, "home again at " + m.door) }
                Move.Nothing -> note(t, "nothing answers")
                else -> {}
            }
        }

        private fun note(t: Long, what: String) {
            if (moves.lastOrNull()?.second == what && moves.last().second.endsWith(base) && !justRemade) return
            justRemade = false
            moves.add(Pair(t, what))
        }
        var justRemade = false

        /** the app's own requests to where the library is: a failure is a miss and a look now */
        private fun request(t: Long): Long? {
            if (base !in mainWays) return null
            return when (if (base in mainWays) main(t) else S.UP) {
                S.UP, S.SLOW -> null
                S.OFF -> t + 1500
                S.FROZEN -> t + REQ_READ_MS
            }
        }

        fun run(until: Long): Sim {
            var t = startAt
            var nextLook = startAt
            var nextReq = startAt + 500L
            var failAt: Long? = null
            var failBegan = 0L
            while (t < until) {
                if (remadeAt != null && t >= remadeAt && !remade) {
                    // the screen made again: start-up runs, the opening's look is owed again
                    remade = true
                    val was = base
                    startUp(this)
                    justRemade = true
                    if (base != was) note(t, "start-up: library at " + base)
                    unlooked = true
                    decide(t)
                    nextLook = minOf(nextLook, t)
                }
                if (awayFrom != null && awayTo != null && t in awayFrom until awayTo) {
                    // away from the screen: no looks, no requests
                    nextLook = maxOf(nextLook, awayTo)
                    nextReq = maxOf(nextReq, awayTo)
                    failAt = null
                    t += 100
                    continue
                }
                if (failAt != null && t >= failAt) {
                    val k = base
                    if (!(k in mainWays && sending(t))) {
                        Route.requestMiss(doors[k], failBegan, t, FRESH_MS)?.let { doors[k] = it }
                        nextLook = minOf(nextLook, t)
                        // a request that failed asks for a decision there and then (Api.routed)
                        decide(t)
                    }
                    failAt = null
                }
                if (failAt == null && t >= nextReq) {
                    failBegan = t
                    failAt = request(t)
                    nextReq = t + REQ_EVERY_MS
                }
                if (t >= nextLook) {
                    val end = check(t)
                    decide(end)
                    val unsure = doors.values.any { it.misses > 0 && !it.down && end - it.at < FRESH_MS }
                    nextLook = end + if (unsure) AGAIN_MS else EVERY_MS
                    t = end
                    continue
                }
                t += 100
            }
            said.add(moves.joinToString("\n") { "  %5.1f s  %s".format(it.first / 1000.0, it.second) })
            return this
        }

        fun firstAt(what: String): Long? = moves.firstOrNull { it.second.startsWith(what) }?.first
        fun print(name: String) = println(name + "\n" + said.joinToString("\n"))
    }

    @Test
    fun openingWithTheMainServerFrozen() {
        // frozen long enough that the copy has seen it miss twice: its word, no timeout
        val s = Sim(main = { S.FROZEN }, startAt = 30_000).run(60_000).also { it.print("opening, main frozen a while") }
        val copy = s.firstAt("to the copy") ?: Long.MAX_VALUE
        assertTrue("on the copy within half a second of opening, was " + copy, copy <= 30_500)
        assertTrue("never the main server's outside address first",
            s.moves.none { it.second == "library at " + MAIN_OUT })
    }

    @Test
    fun openingJustAfterTheMainServerFroze() {
        // frozen at 25 s, the app opened at 28 s: the copy has not seen it miss twice yet
        val s = Sim(main = { t -> if (t >= 25_000) S.FROZEN else S.UP }, startAt = 28_000).run(60_000).also { it.print("opening 3 s after a freeze") }
        val copy = s.firstAt("to the copy") ?: Long.MAX_VALUE
        // the copy saw it answer 8 s ago: its word holds the move for a second look (2026-11-18)
        assertTrue("on the copy within 6 s of opening, was " + copy, copy <= 34_000)
        assertTrue("never the main server's outside address first",
            s.moves.none { it.second == "library at " + MAIN_OUT })
    }

    @Test
    fun openingWithTheMainServerOff() {
        val s = Sim(main = { S.OFF }).run(30_000).also { it.print("opening, main off") }
        val copy = s.firstAt("to the copy") ?: Long.MAX_VALUE
        assertTrue("on the copy within 3 s, was " + copy, copy <= 3_000)
    }

    @Test
    fun openTheMainServerFreezesThenComesBack() {
        val s = Sim(main = { t -> if (t in 30_000 until 210_000) S.FROZEN else S.UP }, opening = false)
            .run(260_000).also { it.print("open, main frozen 30-210 s") }
        val copy = s.firstAt("to the copy") ?: Long.MAX_VALUE
        assertTrue("on the copy within 25 s of the freeze, was " + copy, copy in 30_000..55_000)
        // while frozen, never back to it
        assertTrue("no move back while frozen",
            s.moves.none { it.first in (copy + 1)..210_000 && !it.second.startsWith("to the copy") })
        val home = s.moves.firstOrNull { it.first > 210_000 && it.second.startsWith("home again") }?.first
        assertTrue("home within 15 s of answering again, was " + home, home != null && home <= 225_000)
    }

    @Test
    fun openingWithTheMainServerFineStaysOnIt() {
        val s = Sim(main = { S.UP }).run(30_000).also { it.print("opening, main fine") }
        assertEquals("no move at all", 0, s.moves.size)
        assertEquals(MAIN_LAN, s.base)
    }

    @Test
    fun aServerWakingUpDoesNotThrowTheViewerBackAndForth() {
        // waking: answers for 3 s, frozen for 3 s, for a minute - then up
        val s = Sim(main = { t -> if (t < 60_000 && (t / 3_000) % 2 == 1L) S.FROZEN else S.UP }, opening = false)
            .run(120_000).also { it.print("open, main waking 0-60 s") }
        val turns = s.moves.count { it.second.startsWith("to the copy") }
        assertTrue("at most one move to the copy while it wakes, was " + turns, turns <= 1)
        assertEquals("on the main server at the end", MAIN_LAN, s.base)
    }

    @Test
    fun aBlipDoesNotMoveASettledViewer() {
        val s = Sim(main = { t -> if (t in 30_000 until 31_500) S.FROZEN else S.UP }, opening = false)
            .run(90_000).also { it.print("open, one blip at 30 s") }
        assertEquals("no move for a blip", 0, s.moves.count { it.second.startsWith("to the copy") })
    }

    @Test
    fun filmArrivingKeepsTheMainServerWhateverTheLooksSay() {
        // a choked line: looks time out, the film still comes
        val s = Sim(main = { t -> if (t >= 20_000) S.FROZEN else S.UP }, sending = { true }, opening = false)
            .run(120_000).also { it.print("open, looks fail, film arriving") }
        assertEquals("never moved", 0, s.moves.count { it.second.startsWith("to the copy") })
    }

    @Test
    fun openingWhileTheCopyCanStillReachTheMainServer() {
        // this viewer cannot reach the main server, the copy can and says it is up: the opening
        // goes to the copy once the main server is known off here, never to its outside address
        val s = Sim(main = { S.FROZEN }, copySees = { S.UP }, startAt = 30_000).run(60_000)
            .also { it.print("opening, main unreachable from here only") }
        assertTrue("never the main server's outside address, moves were " + s.moves,
            s.moves.none { it.second == "library at " + MAIN_OUT })
        // the copy says the main server answered it: a second look, at four seconds, before
        // the move - the price of not moving a viewer on a slow line (owner, 2026-11-18)
        assertTrue("on the copy within 11 s", (s.firstAt("to the copy") ?: Long.MAX_VALUE) <= 41_000)
    }

    @Test
    fun openingOnASlowLineWhileTheCopySaysTheMainServerIsUp() {
        // the phone at 10:34:12 (2026-11-18): on mobile data Maverick answered in 2.6 s, the
        // opening look gave it 2 s and the copy, saying Maverick was up, answered - it moved
        val s = Sim(main = { S.SLOW }, startAt = 30_000).run(90_000)
            .also { it.print("opening, main slow from here, the copy says it is up") }
        assertEquals("no move at all, moves were " + s.moves, 0, s.moves.size)
        assertEquals(MAIN_LAN, s.base)
    }

    @Test
    fun theScreenMadeAgainAfterAWhileAwayKeepsTheCopy() {
        // asleep all morning: on the copy, away from the screen a minute, opened again with the
        // app alive - start-up runs. It stays on the copy: no move to the main server at all.
        val s = Sim(main = { S.OFF }, startAt = 30_000, awayFrom = 50_000, awayTo = 120_000,
                    remadeAt = 120_000).run(170_000)
            .also { it.print("remade after a minute away, main asleep") }
        val after = s.moves.filter { it.first >= 120_000 }
        assertTrue("no move at all after the screen is made again, was " + after, after.isEmpty())
        assertEquals("on the copy at the end", COPY_LAN, s.base)
    }

    @Test
    fun theScreenMadeAgainWithTheMainServerUpGoesHome() {
        // the same, but the main server woke while the app was away: home, once
        val s = Sim(main = { t -> if (t >= 80_000) S.UP else S.OFF }, startAt = 30_000,
                    awayFrom = 50_000, awayTo = 120_000, remadeAt = 120_000).run(170_000)
            .also { it.print("remade after a minute away, main woke meanwhile") }
        assertEquals("on the main server at the end", MAIN_LAN, s.base)
        val after = s.moves.filter { it.first >= 120_000 }
        assertTrue("one move home, was " + after, after.size <= 1)
    }
}
