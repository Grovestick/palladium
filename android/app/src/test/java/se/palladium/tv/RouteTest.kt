package se.palladium.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import se.palladium.tv.Route.Look
import se.palladium.tv.Route.Move

private const val FRESH = 45_000L
private const val MAIN_LAN = "http://192.168.0.181:8765"
private const val MAIN_OUT = "http://house.example:8765"
private const val COPY_LAN = "http://192.168.0.25:8764"
private const val COPY_OUT = "http://house.example:8764"
private val MAIN = listOf(MAIN_LAN, MAIN_OUT)
private val COPY = listOf(COPY_LAN, COPY_OUT)

private fun near(a: String) = a.contains("192.168.")

/**
 * The app and a network, stepped one look at a time the way Reach does it: every
 * address asked, each answer settled, then the decision carried out.
 */
private class World(val away: Boolean, startBase: String, var chosenIsCopy: Boolean = false) {
    val looks = HashMap<String, Look>()
    var now = 1_000_000L
    var base = startBase
    var standingBy = false
    var homeBase = ""
    var homeUnlooked = true
    var prefersCopy = false
    /** the machines themselves */
    var mainOn = true
    var copyOn = true
    /** addresses whose next look is lost on the way */
    val lost = HashSet<String>()
    /** what each address said at the last look and the one before: true is a miss */
    val missedNow = HashMap<String, Boolean>()
    val missedBefore = HashMap<String, Boolean>()
    val moves = ArrayList<Move>()

    fun reachable(a: String) = !away || !near(a)
    private fun on(a: String) = if (a in MAIN) mainOn else copyOn

    fun look(advance: Long = 10_000L): Move {
        now += advance
        missedBefore.clear(); missedBefore.putAll(missedNow)
        for (a in MAIN + COPY) {
            val hit = reachable(a) && on(a) && a !in lost
            missedNow[a] = !hit
            val raw = when {
                !reachable(a) -> Route.never(now)
                hit -> Look(true, if (a in MAIN) "M" else "C", if (near(a)) 5 else 150, now,
                            followsId = if (a in COPY) "M" else "")
                else -> Route.miss(now)
            }
            looks[a] = Route.settle(looks[a], raw, now, FRESH)
        }
        lost.clear()
        return decide()
    }

    /** A request that set out at [began] and could not connect, as Reach.missed. */
    fun requestMissed(a: String, began: Long = now) {
        if (!reachable(a)) { looks[a] = Route.never(now); return }
        Route.requestMiss(looks[a], began, now, FRESH)?.let { looks[a] = it }
    }

    fun door(ways: List<String>, id: String) = Route.door(ways, looks, id, now, FRESH, ::near)
    fun up(ways: List<String>, id: String) = Route.rowUp(ways, looks, id, now, FRESH, ::near)
    fun state(a: String) = Route.state(looks[a], now, FRESH)

    fun decide(): Move {
        val d = Route.decide(Route.Seen(
            chosenIsCopy = chosenIsCopy,
            homeUnlooked = homeUnlooked,
            prefersCopy = prefersCopy,
            standingBy = standingBy,
            base = base,
            baseUsable = reachable(base) && state(base) != false,
            mainUp = up(MAIN, "M"),
            mainDoor = door(MAIN, "M"),
            mainTry = MAIN.firstOrNull { reachable(it) && state(it) != false },
            copyDoor = door(COPY, "C"),
            mainBase = MAIN_LAN,
            homeBase = homeBase))
        if (d.looked) homeUnlooked = false
        when (val m = d.move) {
            is Move.OpenMain -> { chosenIsCopy = false; base = m.door; standingBy = false; homeBase = "" }
            is Move.Library -> { base = m.door; standingBy = false; homeBase = "" }
            is Move.Standby -> { base = m.door; standingBy = true; homeBase = m.home.ifEmpty { homeBase } }
            // confirmed with a request first: it lands only on a server that is on
            is Move.HomeAgain -> if (mainOn && reachable(m.door)) {
                base = m.door; standingBy = false; homeBase = ""
            }
            Move.Nothing, Move.Stay -> {}
        }
        if (d.move != Move.Stay) moves.add(d.move)
        return d.move
    }
}

class RouteTest {

    // ---- one look

    @Test
    fun `an answer is believed at once`() {
        val had = Route.never(0)
        val now = Route.settle(had, Look(true, "M", 100, 1000), 1000, FRESH)
        assertTrue(now.up)
        assertEquals(0, now.misses)
    }

    @Test
    fun `one miss leaves an address that was up still up`() {
        val up = Look(true, "M", 100, 1000)
        val one = Route.settle(up, Route.miss(2000), 2000, FRESH)
        assertTrue(one.up)
        assertEquals(true, Route.state(one, 2000, FRESH))
        val two = Route.settle(one, Route.miss(3000), 3000, FRESH)
        assertFalse(two.up)
        assertEquals(false, Route.state(two, 3000, FRESH))
        assertEquals("the machine it was is remembered", "M", two.id)
    }

    @Test
    fun `a miss then an answer is up with nothing counted`() {
        val up = Look(true, "M", 100, 1000)
        val one = Route.settle(up, Route.miss(2000), 2000, FRESH)
        val back = Route.settle(one, Look(true, "M", 90, 3000), 3000, FRESH)
        assertEquals(0, back.misses)
        val again = Route.settle(back, Route.miss(4000), 4000, FRESH)
        assertTrue("one miss after an answer is one miss, not the second", again.up)
    }

    @Test
    fun `an address never seen is not known after one miss and down after two`() {
        val one = Route.settle(null, Route.miss(1000), 1000, FRESH)
        assertNull(Route.state(one, 1000, FRESH))
        val two = Route.settle(one, Route.miss(1700), 1700, FRESH)
        assertEquals(false, Route.state(two, 1700, FRESH))
    }

    @Test
    fun `an old answer is no answer and one miss after it is not down`() {
        val up = Look(true, "M", 100, 1000)
        assertNull(Route.state(up, 1000 + FRESH, FRESH))
        val one = Route.settle(up, Route.miss(1000 + FRESH), 1000 + FRESH, FRESH)
        assertNull(Route.state(one, 1000 + FRESH, FRESH))
    }

    @Test
    fun `a house address from another network is down at once`() {
        val d = Route.settle(Look(true, "M", 5, 1000), Route.never(2000), 2000, FRESH)
        assertEquals(false, Route.state(d, 2000, FRESH))
    }

    @Test
    fun `a miss kept as up does not make an answer look newer than it is`() {
        val up = Look(true, "M", 100, 1000)
        val one = Route.settle(up, Route.miss(40_000), 40_000, FRESH)
        assertEquals(1000L, one.at)
        assertNull(Route.state(one, 1000 + FRESH, FRESH))
    }

    // ---- away from the house

    @Test
    fun `away, opened under the house address, the library is at the outside address after one look`() {
        val w = World(away = true, startBase = MAIN_LAN)
        w.look()
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.standingBy)
    }

    @Test
    fun `away, a first look that misses the main server does not open the copy`() {
        val w = World(away = true, startBase = MAIN_LAN)
        w.lost.add(MAIN_OUT)
        w.look()
        assertFalse("one miss is not a server that is off", w.standingBy)
        assertEquals("asked at the address that may still answer", MAIN_OUT, w.base)
        w.look(700)
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.standingBy)
        assertTrue(w.moves.none { it is Move.Standby })
    }

    @Test
    fun `one lost look while browsing moves nothing`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.moves.clear()
        repeat(50) { i ->
            if (i % 3 == 0) w.lost.add(MAIN_OUT)
            w.look(if (i % 3 == 1) 700 else 10_000)
            assertEquals(MAIN_OUT, w.base)
            assertFalse(w.standingBy)
        }
        assertTrue(w.moves.toString(), w.moves.isEmpty())
    }

    @Test
    fun `one request that could not connect moves nothing`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.now += 3000
        w.requestMissed(MAIN_OUT)
        assertEquals(Move.Stay, w.decide())
        assertEquals(true, w.state(MAIN_OUT))
        w.look(700)
        assertEquals(MAIN_OUT, w.base)
        assertEquals(0, w.looks[MAIN_OUT]!!.misses)
    }

    @Test
    fun `a request that fails and a look that fails are two, and the copy opens`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.mainOn = false
        w.now += 3000
        w.requestMissed(MAIN_OUT)
        assertEquals(Move.Stay, w.decide())
        val m = w.look(700)
        assertTrue(m.toString(), m is Move.Standby)
        assertEquals(COPY_OUT, w.base)
        assertEquals("home is remembered by the row's own address", MAIN_LAN, w.homeBase)
    }

    @Test
    fun `the main server off opens the copy on the second look and home on the first answer`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.mainOn = false
        assertEquals(Move.Stay, w.look())
        assertEquals(MAIN_OUT, w.base)
        val second = w.look(700)
        assertTrue(second.toString(), second is Move.Standby && !(second as Move.Standby).asked)
        assertEquals(COPY_OUT, w.base)
        assertTrue(w.standingBy)
        repeat(5) { assertEquals(Move.Stay, w.look()) }
        w.mainOn = true
        w.look()
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.standingBy)
    }

    @Test
    fun `both lost at once is the phone's network and moves nowhere`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.moves.clear()
        repeat(6) {
            w.lost.addAll(listOf(MAIN_OUT, COPY_OUT))
            w.look(700)
            assertEquals(MAIN_OUT, w.base)
            assertFalse(w.standingBy)
        }
        w.look()
        assertEquals(MAIN_OUT, w.base)
        assertTrue(w.moves.toString(), w.moves.none { it is Move.Standby })
    }

    @Test
    fun `on the copy, one lost look of the copy does not drop the library`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.mainOn = false
        w.look(); w.look(700)
        assertEquals(COPY_OUT, w.base)
        w.lost.add(COPY_OUT)
        assertEquals(Move.Stay, w.look())
        assertEquals(COPY_OUT, w.base)
        assertTrue(w.standingBy)
    }

    @Test
    fun `home from the copy waits for a request to answer there`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.mainOn = false
        w.look(); w.look(700)
        // seen answering, gone again before the request that confirms it
        w.mainOn = true
        val raw = Look(true, "M", 150, w.now)
        w.looks[MAIN_OUT] = raw
        w.mainOn = false
        assertTrue(w.decide() is Move.HomeAgain)
        assertEquals(COPY_OUT, w.base)
        assertTrue(w.standingBy)
    }

    @Test
    fun `the main server asking for its copy is obeyed, and not twice`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.prefersCopy = true
        val m = w.look()
        assertTrue(m is Move.Standby && (m as Move.Standby).asked)
        assertEquals(COPY_OUT, w.base)
        assertEquals(Move.Stay, w.look())
        w.prefersCopy = false
        w.look()
        assertEquals(MAIN_OUT, w.base)
    }

    @Test
    fun `nothing answering anywhere is said and nothing moves`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.mainOn = false; w.copyOn = false
        w.look()
        assertEquals(Move.Nothing, w.look(700))
        assertEquals(MAIN_OUT, w.base)
        w.mainOn = true
        w.look()
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.standingBy)
    }

    @Test
    fun `six requests failing together are one miss, not six`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.now += 3000
        repeat(6) { w.requestMissed(MAIN_OUT) }
        assertEquals(1, w.looks[MAIN_OUT]!!.misses)
        assertEquals(true, w.state(MAIN_OUT))
        assertEquals(Move.Stay, w.decide())
        assertEquals(MAIN_OUT, w.base)
    }

    @Test
    fun `requests alone never open the copy, however many fail`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        repeat(20) {
            w.now += 500
            w.requestMissed(MAIN_OUT)
            assertEquals(Move.Stay, w.decide())
        }
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.standingBy)
    }

    @Test
    fun `a request that set out before the server last answered says nothing about it`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        val setOut = w.now
        w.look(5000)                               // answered since
        w.now += 3000                              // and now the old request times out
        w.requestMissed(MAIN_OUT, began = setOut)
        assertEquals(0, w.looks[MAIN_OUT]!!.misses)
    }

    @Test
    fun `a phone waking into a dead network stays on the main server`() {
        // the network was gone for five seconds after the screen came on, the requests
        // sent into it timed out at six and eight, and the library went to the copy
        // and came back three seconds later
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.now += 600_000                           // asleep: every answer is old
        val woke = w.now
        w.lost.addAll(listOf(MAIN_OUT, COPY_OUT))
        w.look(4000)                               // the first look finds nothing
        w.look(700)                                // the network is back: both answer
        assertEquals(MAIN_OUT, w.base)
        w.now = woke + 6009
        repeat(3) { w.requestMissed(MAIN_OUT, began = woke) }
        w.now = woke + 8011
        repeat(3) { w.requestMissed(MAIN_OUT, began = woke) }
        w.requestMissed(COPY_OUT, began = woke)
        assertEquals(Move.Stay, w.decide())
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.standingBy)
        assertTrue(w.moves.toString(), w.moves.none { it is Move.Standby })
    }

    @Test
    fun `waking into a dead network with nothing known yet does not open the copy`() {
        val w = World(away = true, startBase = MAIN_OUT)
        w.look()
        w.now += 600_000
        val woke = w.now
        // the requests fail before any look has finished
        w.now = woke + 100
        repeat(6) { w.requestMissed(MAIN_OUT, began = woke) }
        repeat(2) { w.requestMissed(COPY_OUT, began = woke) }
        assertEquals(Move.Stay, w.decide())
        // the network is still gone for the looks that follow, for both servers
        repeat(3) {
            w.lost.addAll(listOf(MAIN_OUT, COPY_OUT))
            w.look(700)
            assertFalse(w.standingBy)
        }
        w.look(700)                                // and back
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.standingBy)
        assertTrue(w.moves.toString(), w.moves.none { it is Move.Standby })
    }

    // ---- in the house

    @Test
    fun `at home the library is at the house address`() {
        val w = World(away = false, startBase = MAIN_OUT)
        w.look()
        assertEquals(MAIN_LAN, w.base)
    }

    @Test
    fun `at home one lost look does not send the library out through the router`() {
        val w = World(away = false, startBase = MAIN_LAN)
        w.look()
        w.moves.clear()
        w.lost.add(MAIN_LAN)
        w.look()
        assertEquals(MAIN_LAN, w.base)
        assertTrue(w.moves.isEmpty())
    }

    @Test
    fun `at home the main server off opens the copy at its house address`() {
        val w = World(away = false, startBase = MAIN_LAN)
        w.look()
        w.mainOn = false
        w.look(); w.look(700)
        assertEquals(COPY_LAN, w.base)
        assertTrue(w.standingBy)
        w.mainOn = true
        w.look()
        assertEquals(MAIN_LAN, w.base)
    }

    // ---- opened on the machine keeping copies

    @Test
    fun `opened on the copy goes home when the main server answers`() {
        val w = World(away = true, startBase = MAIN_OUT, chosenIsCopy = true)
        val m = w.look()
        assertTrue(m is Move.OpenMain)
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.chosenIsCopy)
    }

    @Test
    fun `opened on the copy, a first look that misses the main server still goes home on the second`() {
        val w = World(away = true, startBase = MAIN_OUT, chosenIsCopy = true)
        w.lost.add(MAIN_OUT)
        w.look()
        assertTrue("not decided on one miss", w.homeUnlooked)
        assertEquals("nothing drawn from the copy on one miss", MAIN_OUT, w.base)
        val m = w.look(700)
        assertTrue(m.toString(), m is Move.OpenMain)
        assertEquals(MAIN_OUT, w.base)
    }

    @Test
    fun `opened on the copy with the main server off stays, and stays when it comes back`() {
        val w = World(away = true, startBase = MAIN_OUT, chosenIsCopy = true)
        w.mainOn = false
        w.look()
        assertEquals("the main server is asked first", MAIN_OUT, w.base)
        w.look(700)
        assertFalse(w.homeUnlooked)
        assertEquals(COPY_OUT, w.base)
        w.mainOn = true
        repeat(3) { w.look() }
        assertEquals("the copy was chosen for this opening", COPY_OUT, w.base)
        assertTrue(w.chosenIsCopy)
    }

    @Test
    fun `opened on the copy while the main server asks for it stays on the copy`() {
        val w = World(away = true, startBase = MAIN_OUT, chosenIsCopy = true)
        w.prefersCopy = true
        w.look()
        assertEquals(COPY_OUT, w.base)
        assertTrue(w.chosenIsCopy)
    }

    // ---- which row is open

    private val mainRow = Server("Main", MAIN_OUT, "k", outside = MAIN_LAN)
    private val copyRow = Server("Copy", COPY_OUT, "k", copyOf = MAIN_OUT, outside = COPY_LAN)

    @Test
    fun `the open row is the one filed under the address written down`() {
        assertEquals(mainRow, Route.chosen(listOf(copyRow, mainRow), MAIN_OUT))
        assertEquals(copyRow, Route.chosen(listOf(mainRow, copyRow), COPY_OUT + "/"))
    }

    @Test
    fun `a row filed again under its other address is still the open row`() {
        assertEquals(mainRow, Route.chosen(listOf(copyRow, mainRow), MAIN_LAN))
    }

    @Test
    fun `an address that names no row opens the main server, not whichever row is first`() {
        assertEquals(mainRow, Route.chosen(listOf(copyRow, mainRow), "http://gone.example:1"))
        assertEquals(mainRow, Route.chosen(listOf(copyRow, mainRow), ""))
        assertEquals(copyRow, Route.chosen(listOf(copyRow), ""))
        assertNull(Route.chosen(emptyList(), MAIN_OUT))
    }

    @Test
    fun `opened on the copy under a house address away, the main server is asked at its outside address`() {
        val w = World(away = true, startBase = MAIN_LAN, chosenIsCopy = true)
        w.lost.add(MAIN_OUT)
        w.look()
        assertEquals(MAIN_OUT, w.base)
        assertTrue(w.chosenIsCopy)
        w.look(700)
        assertEquals(MAIN_OUT, w.base)
        assertFalse(w.chosenIsCopy)
    }

    @Test
    fun `opened on the copy, the copy is never the library while the main server answers`() {
        for (lostFirst in listOf(false, true)) {
            val w = World(away = true, startBase = MAIN_OUT, chosenIsCopy = true)
            if (lostFirst) w.lost.add(MAIN_OUT)
            repeat(4) {
                w.look(700)
                assertTrue("on ${w.base}", w.base in MAIN)
            }
            assertTrue(w.moves.toString(), w.moves.none { it is Move.Library && (it as Move.Library).door in COPY })
        }
    }

    // ---- whose key this is

    @Test
    fun `the owner's key is the owner's at home and away, and a guest's is not`() {
        assertTrue("at home", Route.ownersKey(guest = false, ownerAway = false))
        assertTrue("away: a guest to the server, which says what the key still opens",
                   Route.ownersKey(guest = true, ownerAway = true))
        assertFalse("a guest", Route.ownersKey(guest = true, ownerAway = false))
    }

    // ---- where a title starts

    @Test
    fun `a fresh start from nought begins past the skip rule and nothing else does`() {
        assertEquals("fresh, from 0", 12L, Route.leadStart(0, 12.0, fresh = true))
        assertEquals("rounded to a whole second", 13L, Route.leadStart(0, 12.5, fresh = true))
        assertEquals("a resume keeps its place", 300L, Route.leadStart(300, 12.0, fresh = true))
        assertEquals("a resume inside the lead keeps its place too", 5L,
                     Route.leadStart(5, 12.0, fresh = true))
        assertEquals("a restart in the player at 0 is a seek to 0", 0L,
                     Route.leadStart(0, 12.0, fresh = false))
        assertEquals("no rule", 0L, Route.leadStart(0, 0.0, fresh = true))
    }

    // ---- the receiver's raise

    @Test
    fun `the receiver is raised for a television passing sound through on screen and never off it`() {
        assertTrue("on screen, passing through",
                   Route.raiseReceiver(raised = false, onScreen = true, tv = true, passing = true))
        assertFalse("Home pressed: a report still in flight does not raise it again",
                    Route.raiseReceiver(raised = false, onScreen = false, tv = true, passing = true))
        assertFalse("asked once", Route.raiseReceiver(raised = true, onScreen = true, tv = true, passing = true))
        assertFalse("a phone", Route.raiseReceiver(raised = false, onScreen = true, tv = false, passing = true))
        assertFalse("decoded here", Route.raiseReceiver(raised = false, onScreen = true, tv = true, passing = false))
    }

    // ---- a name that will not resolve

    @Test
    fun `an address is asked for by number with its port, path and key kept`() {
        assertEquals("http://82.1.2.3:8765/app/version?t=k",
                     Route.numbered("http://house.example:8765/app/version?t=k", "82.1.2.3"))
        assertEquals("https://82.1.2.3/x", Route.numbered("https://house.example/x", "82.1.2.3"))
        assertEquals("http://82.1.2.3:8764", Route.numbered("http://house.example:8764", "82.1.2.3"))
    }

    @Test
    fun `an address that is a number already, or has no number kept, is left alone`() {
        assertEquals("http://192.168.0.181:8765/x", Route.numbered("http://192.168.0.181:8765/x", "82.1.2.3"))
        assertEquals("http://house.example:8765/x", Route.numbered("http://house.example:8765/x", ""))
        assertEquals("not an address", Route.numbered("not an address", "82.1.2.3"))
    }

    @Test
    fun `a name that failed is not looked up again for a minute`() {
        assertFalse("never failed", Route.nameStillFailing(0, 5_000_000))
        assertTrue(Route.nameStillFailing(5_000_000, 5_000_000 + 59_999))
        assertFalse(Route.nameStillFailing(5_000_000, 5_000_000 + 60_000))
    }

    // ---- asking for a newer build

    @Test
    fun `coming back to the app asks for a newer build, five minutes after the last asking`() {
        assertFalse(Route.updateDue(showing = false, askedAt = 1_000_000, now = 1_000_000 + 299_999))
        assertTrue(Route.updateDue(showing = false, askedAt = 1_000_000, now = 1_000_000 + 300_000))
        assertTrue("never asked", Route.updateDue(showing = false, askedAt = 0, now = 1_000_000_000))
    }

    @Test
    fun `a build on offer is not asked for again`() {
        assertFalse(Route.updateDue(showing = true, askedAt = 0, now = 1_000_000_000))
    }

    @Test
    fun `a build put off with Later is not offered again, a newer one is`() {
        assertFalse(Route.updateOffered(code = 1195, putOff = 1195))
        assertTrue(Route.updateOffered(code = 1196, putOff = 1195))
        assertTrue(Route.updateOffered(code = 1195, putOff = 0))
        assertFalse(Route.updateOffered(code = 0, putOff = 0))
    }

    // ---- a network that loses connections

    @Test
    fun `a network losing one look in three, never two running, never moves the library`() {
        for (seed in 1..40) {
            val away = seed % 2 == 0
            val rng = java.util.Random(seed.toLong())
            val home = if (away) MAIN_OUT else MAIN_LAN
            val w = World(away, home)
            w.look()
            w.moves.clear()
            val lostLast = HashSet<String>()
            repeat(3000) {
                val lose = (MAIN + COPY).filter { w.reachable(it) && it !in lostLast &&
                                                 rng.nextInt(3) == 0 }
                w.lost.addAll(lose)
                lostLast.clear(); lostLast.addAll(lose)
                w.look(if (lose.isEmpty()) 10_000 else 700)
                assertEquals("seed $seed", home, w.base)
                assertFalse("seed $seed", w.standingBy)
            }
            assertTrue("seed $seed " + w.moves, w.moves.isEmpty())
        }
    }

    @Test
    fun `with servers going off and looks being lost, the copy opens only after two misses and home is the first answer`() {
        for (seed in 1..40) {
            val away = seed % 2 == 0
            val rng = java.util.Random(1000L + seed)
            val w = World(away, if (away) MAIN_OUT else MAIN_LAN)
            w.look()
            val lostLast = HashSet<String>()
            repeat(4000) { step ->
                if (rng.nextInt(60) == 0) w.mainOn = !w.mainOn
                if (rng.nextInt(150) == 0) w.copyOn = !w.copyOn
                val lose = (MAIN + COPY).filter { w.reachable(it) && it !in lostLast &&
                                                 rng.nextInt(8) == 0 }
                w.lost.addAll(lose)
                lostLast.clear(); lostLast.addAll(lose)
                val before = w.standingBy
                val m = w.look(if (rng.nextBoolean()) 10_000 else 700)
                val mains = MAIN.filter { w.reachable(it) }
                val at = "seed $seed step $step"
                if (m is Move.Standby) {
                    assertTrue("$at: opened the copy on one miss",
                               mains.all { w.missedNow[it] == true && w.missedBefore[it] == true })
                }
                if (mains.any { w.missedNow[it] == false }) {
                    assertFalse("$at: main server answered and the library is still on the copy",
                                w.standingBy)
                    assertTrue("$at: base ${w.base}", w.base in MAIN)
                }
                if (w.standingBy) assertTrue("$at: standing by on ${w.base}", w.base in COPY)
                assertTrue("$at: at an address this network cannot reach", w.reachable(w.base))
                if (!before && w.standingBy) assertTrue(at, m is Move.Standby)
            }
        }
    }
}
