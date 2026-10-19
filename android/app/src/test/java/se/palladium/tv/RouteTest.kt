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

    @Test
    fun `sound is passed through only once a picture has been up three seconds`() {
        assertTrue("4 s after the first picture, not decoded here",
                   Route.passesSound(onCast = false, decodedHere = false, sincePictureMs = 4000))
        assertFalse("no picture yet - a resume far into the film that is slow to start",
                    Route.passesSound(onCast = false, decodedHere = false, sincePictureMs = -1))
        assertFalse("picture just up: the sink has not said",
                    Route.passesSound(onCast = false, decodedHere = false, sincePictureMs = 500))
        assertFalse("at the limit", Route.passesSound(onCast = false, decodedHere = false,
                                                      sincePictureMs = Route.SOUND_SETTLES_MS))
        assertFalse("decoded here",
                    Route.passesSound(onCast = false, decodedHere = true, sincePictureMs = 60_000))
        assertFalse("cast",
                    Route.passesSound(onCast = true, decodedHere = false, sincePictureMs = 60_000))
        assertFalse(Route.soundSettled(-1))
        assertTrue(Route.soundSettled(3001))
    }

    @Test
    fun `a poster says how far its download has got, and how fast`() {
        assertEquals("13%", Route.posterDownload("downloading", 0.134))
        assertEquals("0%", Route.posterDownload("downloading", 0.0))
        assertEquals("100%", Route.posterDownload("downloading", 1.2))
        assertEquals("Queued", Route.posterDownload("queued", 0.0))
        assertEquals("Arriving", Route.posterDownload("done", 1.0))
        assertEquals("", Route.posterDownload("", 0.5))
        assertEquals("42% \u00b7 46 Mbit/s", Route.posterDownload("downloading", 0.42, 46.3))
        assertEquals("a rate under half a megabit is no rate", "42%",
                     Route.posterDownload("downloading", 0.42, 0.2))
        assertEquals("queued says no rate", "Queued", Route.posterDownload("queued", 0.0, 40.0))
        assertFalse("no time left or version on it",
                    Regex("min| s$|1080p").containsMatchIn(Route.posterDownload("downloading", 0.5, 30.0)))
    }

    @Test
    fun `the wait for the next title is black from 0,4 s and worded only from 2 s`() {
        assertEquals("a changeover of 0.3 s shows nothing", 0, Route.changeoverShows(300))
        assertEquals("black at 0.4 s", 1, Route.changeoverShows(400))
        assertEquals("still no words at 1.9 s", 1, Route.changeoverShows(1900))
        assertEquals("words at 2 s", 2, Route.changeoverShows(2000))
        assertEquals(2, Route.changeoverShows(30_000))
    }

    @Test
    fun `details read ahead are used for the same title from the same server while fresh`() {
        val a = "http://192.168.0.2:8765"
        assertTrue(Route.readAheadFits("e1", a, "e1", a, 20_000))
        assertTrue("at the limit", Route.readAheadFits("e1", a, "e1", a, Route.READ_AHEAD_FRESH_MS))
        assertFalse("another title was drawn", Route.readAheadFits("e1", a, "e2", a, 20_000))
        assertFalse("drawn from the other server", Route.readAheadFits("e1", a, "e1", "http://192.168.0.3:8764", 20_000))
        assertFalse("read too long ago: a subtitle may have come since",
                    Route.readAheadFits("e1", a, "e1", a, Route.READ_AHEAD_FRESH_MS + 1))
        assertFalse("nothing read", Route.readAheadFits("", a, "", a, 0))
        assertFalse("a clock that went back", Route.readAheadFits("e1", a, "e1", a, -5))
    }

    @Test
    fun `a screen the receiver is not for says nothing of it`() {
        assertEquals("", Route.receiverRefusal(true, "off"))
        assertEquals("a viewer outside the house", "", Route.receiverRefusal(true, "not in the house"))
        assertEquals("another screen in the house", "",
                     Route.receiverRefusal(true, "not the device set for the receiver"))
        assertEquals("no answer at all", "", Route.receiverRefusal(false, ""))
        assertEquals("a fault of the receiver itself is still said",
                     "receiver: the receiver is off", Route.receiverRefusal(true, "the receiver is off"))
        assertEquals("receiver: the receiver refused", Route.receiverRefusal(true, "the receiver refused"))
    }

    @Test
    fun `the monitor is opened where something answers`() {
        val main = "http://house.example:8765"
        val copyNear = "http://192.168.0.25:8764"
        val copyOut = "http://house.example:8764"
        assertEquals("the main server first, then its number, then the copy",
                     listOf(main, "http://82.1.2.3:8765", copyNear, copyOut),
                     Route.monitorOrder(main, "http://82.1.2.3:8765", copyNear, copyOut) { false })
        assertEquals("the main server away: the copy first, the main server still last",
                     listOf(copyNear, copyOut, main),
                     Route.monitorOrder(main, null, copyNear, copyOut) { it == main })
        assertEquals("no copy known: the main server alone",
                     listOf(main), Route.monitorOrder(main, null, "", "") { false })
        assertEquals("the app already on the copy: each address once",
                     listOf(copyNear, copyOut),
                     Route.monitorOrder("$copyNear/", null, copyNear, copyOut) { false })
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

    @Test
    fun skinCornerScale() {
        assertEquals(0.4f, Route.cornerScale(0.4f), 0.0001f)
        assertEquals(1.6f, Route.cornerScale(1.6f), 0.0001f)
        // nothing said, or nonsense: the app's own corners
        assertEquals(1f, Route.cornerScale(0f), 0.0001f)
        assertEquals(1f, Route.cornerScale(-3f), 0.0001f)
        assertEquals(1f, Route.cornerScale(Float.NaN), 0.0001f)
        // held to what still draws a card
        assertEquals(0.2f, Route.cornerScale(0.01f), 0.0001f)
        assertEquals(2f, Route.cornerScale(40f), 0.0001f)
    }

    @Test
    fun directPlaySoundtrack() {
        // the server marks the Korean track, second in the file: picked out
        assertEquals(2, Route.soundToPick(null, 2, 1))
        // marked and first: the player takes it by itself
        assertNull(Route.soundToPick(null, 1, 1))
        // chosen this time wins over the mark
        assertEquals(3, Route.soundToPick(3, 2, 1))
        // nothing known: nothing picked
        assertNull(Route.soundToPick(null, null, 1))
    }

    @Test
    fun subtitleLanguages() {
        // a file's three letters and a reader's two: Swedish and German never matched
        assertTrue(Route.sameLanguage("swe", "sv"))
        assertTrue(Route.sameLanguage("ger", "de"))
        assertTrue(Route.sameLanguage("deu", "de"))
        assertTrue(Route.sameLanguage("fre", "fr"))
        assertTrue(Route.sameLanguage("eng", "en"))
        assertTrue(Route.sameLanguage("en-US", "en"))
        assertFalse(Route.sameLanguage("swe", "en"))
        assertFalse(Route.sameLanguage("", "en"))
        assertFalse(Route.sameLanguage("dan", "sv"))
    }

    @Test
    fun packEpisodeAndPopularFilters() {
        // a pack's episode on its season: the release list, as a missing one opens
        assertTrue(Route.episodeOpensReleases("episode", true))
        assertFalse(Route.episodeOpensReleases("episode", false))
        assertFalse(Route.episodeOpensReleases("show", true))
        // the popular page's menus counted from the whole list
        val got = Route.countOf(listOf(listOf("Drama", "Crime"), listOf("Drama"), emptyList())).toMap()
        assertEquals(2, got["Drama"])
        assertEquals(1, got["Crime"])
        assertEquals(2, got.size)
    }

    @Test
    fun popularOrders() {
        val orders = Route.extraOrders("Popular shows").map { it.first }
        assertEquals(listOf("popularity", "originallyAvailableAt", "year", "titleSort", "rating"), orders)
        assertTrue(Route.extraOrders("Recently added").isEmpty())
        assertTrue(Route.extraOrders(null).isEmpty())
        // the highest first for popular and rating, A first for the name
        assertTrue("popularity" in Route.SORT_HIGH_FIRST && "rating" in Route.SORT_HIGH_FIRST)
        assertFalse("titleSort" in Route.SORT_HIGH_FIRST)
    }

    @Test
    fun popularByCountry() {
        assertTrue(Route.madeIn(listOf("US"), ""))
        assertTrue(Route.madeIn(listOf("GB", "US"), "US"))
        assertTrue(Route.madeIn(listOf("KR"), "jp,kr"))
        assertFalse(Route.madeIn(listOf("US"), "KR"))
        assertFalse(Route.madeIn(emptyList(), "US"))
        assertEquals("South Korea", Route.countryName("KR"))
    }

    @Test
    fun popularOnTheTvTab() {
        assertEquals(listOf("popular"), Route.tabExtraOrders("tv").map { it.first })
        assertEquals(listOf("popular"), Route.tabExtraOrders("films").map { it.first })
        assertTrue(Route.tabExtraOrders("home").isEmpty())
        assertEquals("Popular films", Route.popularRow("films"))
        assertEquals("Popular shows", Route.popularRow("tv"))
        assertTrue(Route.popularPage("Popular films") && Route.popularPage("Popular shows"))
        assertFalse(Route.popularPage("Recently added") || Route.popularPage(null))
        assertEquals(Route.extraOrders("Popular shows"), Route.extraOrders("Popular films"))
    }

    @Test
    fun aShelfOpenedWholeHasItsOwnControls() {
        assertTrue(Route.tabControls(null, "tv"))
        assertTrue(Route.tabControls(null, "films"))
        assertFalse(Route.tabControls("Popular shows", "tv"))
        assertFalse(Route.tabControls(null, "home"))
    }

    @Test
    fun liveChannelAddress() {
        assertEquals("http://h:8765/live/lvabc/index.m3u8",
                     Route.liveAddress("http://h:8765/", "lvabc", ""))
        assertEquals("http://h:8765/live/lvabc/index.m3u8?t=a%2Bb",
                     Route.liveAddress("http://h:8765", "lvabc", "a+b"))
    }

    @Test
    fun theShelfStandsAtTheFootWithAMargin() {
        assertEquals(176f, Route.footGap(540f, 100f, 240f, 24f), 0.01f)
        assertEquals(10f, Route.footGap(540f, 400f, 240f, 24f), 0.01f)
    }

    @Test
    fun aSeriesNobodyHasSaysWhereItCanComeFrom() {
        assertEquals("ADD", Route.seriesBand(true, true, false))
        assertEquals("CHECKING", Route.seriesBand(true, false, true))
        assertEquals("REQUEST", Route.seriesBand(true, false, false))
        assertEquals("", Route.seriesBand(false, false, false))
    }

    @Test
    fun svtPlaysKeepTheirPlace() {
        assertEquals("svabc1", Route.svtKey("abc1"))
        assertEquals(true, Route.svtPlaysAtOnce("svabc1", "abc1"))
        assertEquals(false, Route.svtPlaysAtOnce("rt0123456789", "abc1"))   // a page first
        assertEquals(false, Route.svtPlaysAtOnce("svabc1", ""))
    }

    @Test
    fun streamableWearsTheStreamBand() {
        assertEquals(true, Route.streamBand(true, false, "abc1"))
        assertEquals(true, Route.streamBand(false, true, "s1"))
        assertEquals(false, Route.streamBand(true, false, ""))
        assertEquals(false, Route.streamBand(false, false, "abc1"))   // held: it plays here
    }

    @Test
    fun svtPlayFailuresSaySo() {
        assertEquals("SVT Play refused the stream - it plays in Sweden only", Route.svtFailure(2004))
        assertEquals("SVT Play could not be reached", Route.svtFailure(2001))
        assertEquals("SVT Play's stream would not play here", Route.svtFailure(4001))
    }

    @Test
    fun spokenPlayOpensOnlyAnExactHeldTitle() {
        val found = listOf("Star Courier 2049" to true, "Star Courier" to true,
                           "Star Courier" to false, "The Parcel Run" to true)
        assertEquals(1, Route.spokenMatch("star courier", found))
        assertEquals(0, Route.spokenMatch("Star Courier 2049", found))
        assertEquals(null, Route.spokenMatch("courier", found))
        assertEquals(null, Route.spokenMatch("alien", found))
        // two held with the same name: a choice, shown as the list
        assertEquals(null, Route.spokenMatch("star courier", found + ("Star Courier" to true)))
    }

    @Test
    fun searchFillsAnUprightPhone() {
        assertTrue(Route.searchFills(false, 411))
        assertTrue(Route.searchFills(false, 360))
        assertFalse(Route.searchFills(false, 800))
        assertFalse(Route.searchFills(true, 360))
    }

    @Test
    fun aPacksSeasons() {
        assertEquals("", Route.seasonSpan(emptyList()))
        assertEquals("S02", Route.seasonSpan(listOf(2)))
        assertEquals("S01–S22", Route.seasonSpan((1..22).toList()))
    }

    @Test
    fun popularIsAnOrderOfTheTab() {
        // (title, genres, year, rank)
        data class T(val t: String, val g: List<String>, val y: Int, val r: Int)
        val list = listOf(T("A", listOf("Drama", "Crime"), 2019, 5), T("B", listOf("Drama"), 2024, 9),
                          T("C", listOf("Comedy"), 1998, 7), T("D", emptyList(), 0, 0))
        val say = { g: String, d: String, asc: Boolean ->
            Route.narrowPopular(list, { it.g }, { it.y }, { it.r }, g, d, asc).joinToString("") { it.t } }
        assertEquals("BCAD", say("", "", false))
        assertEquals("DACB", say("", "", true))
        assertEquals("BA", say("drama", "", false))
        assertEquals("A", say("Drama,Crime", "", false))
        assertEquals("CA", say("", "2010,1990", false))
    }

    @Test
    fun tvOpensOnPopular() {
        assertEquals(true, Route.tvOpensPopular("tv", ""))
        assertEquals(false, Route.tvOpensPopular("tv", "1899"))
        assertEquals(true, Route.tvOpensPopular("films", ""))
        assertEquals(false, Route.tvOpensPopular("films", "8"))
        assertEquals(false, Route.tvOpensPopular("home", ""))
    }

    @Test
    fun channelsAsSaid() {
        assertEquals("Stereo", Route.channelsWord(2))
        assertEquals("5.1", Route.channelsWord(6))
        assertEquals("7.1", Route.channelsWord(8))
        assertEquals("Mono", Route.channelsWord(1))
        assertEquals("", Route.channelsWord(0))
    }

    @Test
    fun theNoticeIsHeldShortFromAway() {
        assertEquals(45, Route.noticeHold(45, home = true))
        assertEquals(20, Route.noticeHold(45, home = false))
        assertEquals(0, Route.noticeHold(0, home = false))
    }

    @Test
    fun aSubtitleMeasurementThatTimesOutSaysSo() {
        assertEquals("Analysing the film took too long",
                     Route.syncFailure(java.net.SocketTimeoutException("timeout")))
        assertEquals("Analysing the film took too long",
                     Route.syncFailure(java.io.IOException("x", java.net.SocketTimeoutException())))
        assertEquals("could not reach the server",
                     Route.syncFailure(java.net.ConnectException("refused")))
        assertEquals(15_000L, Route.SYNC_SAY_MS)
    }

    @Test
    fun backDuringAMoveLeavesThePictureWhereItWas() {
        // cancelled by Back: the player was never moved and plays on, no seek
        assertEquals(-1L, Route.afterScrub(true, true, 600_000L))
        // a move that reached the player before Back: put back
        assertEquals(600_000L, Route.afterScrub(true, false, 600_000L))
        // an ordinary move, or nothing known of where it was: the move stands
        assertEquals(-1L, Route.afterScrub(false, false, 600_000L))
        assertEquals(-1L, Route.afterScrub(true, false, -1L))
    }

    @Test
    fun progressCarriesTheLengthOfTheVersionPlaying() {
        // a 29.5 min version of an episode whose first version is 42 min
        assertEquals(1_772_438L, Route.reportedLength(true, 1_772_438L, 2_523_157L))
        // an encode: the player's length is what has been made so far
        assertEquals(2_523_157L, Route.reportedLength(false, 600_000L, 2_523_157L))
        // nothing from the player yet; ExoPlayer's unset time is a huge negative
        assertEquals(2_523_157L, Route.reportedLength(true, Long.MIN_VALUE + 1, 2_523_157L))
    }

    @Test
    fun theReceiverLineWaitsOutALongMeasurement() {
        // a film took 2.5 min to measure; the asks must outlast that by a margin
        assertTrue(Route.RECEIVER_ASKS * Route.RECEIVER_ASK_EVERY_MS >= 10 * 60_000L)
        assertTrue(Route.RECEIVER_ASK_EVERY_MS <= 15_000L)
    }

    @Test
    fun theMachinePlayingIsNoSecondSource() {
        val lan = Server("Main", "http://192.168.0.181:8765", "", id = "m1")
        val copy = Server("Copy", "http://192.168.0.25:8764", "", id = "c1")
        // played through the outside address, which is on no row: known by its id
        assertEquals(listOf(copy), Route.othersThan(listOf(lan, copy), "http://203.0.113.58:8765", "m1"))
        // by its own row's address, as before
        assertEquals(listOf(copy), Route.othersThan(listOf(lan, copy), "http://192.168.0.181:8765", ""))
        // the copy playing: the main server is the other machine
        assertEquals(listOf(lan), Route.othersThan(listOf(lan, copy), "http://192.168.0.25:8764", "c1"))
    }

    @Test
    fun aPassingBarDoesNotShrinkThePosters() {
        assertEquals(124, Route.homePoster(450f, 0f))
        assertEquals(109, Route.homePoster(380f, 0f))
        // a 50 dp update bar takes height, and is counted back in
        assertEquals(109, Route.homePoster(330f, 50f))
        assertEquals(96, Route.homePoster(100f, 0f))
    }

    @Test
    fun stillsAreAskedForInTenSecondSteps() {
        assertEquals(0L, Route.frameSecond(9_999))
        assertEquals(1230L, Route.frameSecond(1_234_567))
        assertEquals(0L, Route.frameSecond(-5))
    }

    @Test
    fun rowsOfPostersStandCentred() {
        // 960 dp wide, cards 134 dp: seven fit, 11 dp either side
        assertEquals(11f, Route.centredInset(960f, 134), 0.01f)
        // too narrow for even one with the margin: one, centred
        assertEquals(10f, Route.centredInset(150f, 130), 0.01f)
        assertEquals(0f, Route.centredInset(960f, 0), 0.01f)
    }

    @Test
    fun homeShelvesStartInLineWithTheirNames() {
        assertEquals(21, Route.shelfInset(true))
        assertEquals(8, Route.shelfInset(false))
        assertEquals(Route.shelfInset(true), Route.shelfNameInset(true))
        assertTrue(Route.shelfNameInset(false) < 21)
    }

    @Test
    fun encodeCapIsTheLowerOfChosenAndCeiling() {
        assertEquals("capped at 2 Mbit/s", Route.encodeCap(false, 2, 0))
        assertEquals("capped at 4 Mbit/s", Route.encodeCap(false, 8, 4))
        assertEquals("", Route.encodeCap(false, 0, 0))
        assertEquals("", Route.encodeCap(true, 2, 4))
    }

    @Test
    fun downloadingButtonCancelsOnTheSecondPress() {
        assertEquals("arm", Route.offerPress("downloading", false))
        assertEquals("cancel", Route.offerPress("downloading", true))
        assertEquals("arm", Route.offerPress("queued", false))
        assertEquals("", Route.offerPress("", false))
        assertEquals("", Route.offerPress("done", true))
    }

    @Test
    fun liveLoadingStartAndLeavingAreSaid() {
        assertEquals("Loading 2.4 s \u00b7 3 s buffered", Route.liveLoadingLine(2_400, 3_900))
        assertEquals("live start: SVT 1 ready after 3.2 s", Route.liveStartLine("SVT 1", 3_210))
        assertEquals("live left: SVT 1 after 95 s, 1 stall, 4 frames dropped",
                     Route.liveLeftLine("SVT 1", 95_400, true, 1, 4))
        assertEquals("live left: X after 6.0 s, before it played", Route.liveLeftLine("X", 6_000, false, 0, 0))
    }

    @Test
    fun liveStallSaysWhereAndWhatWasInHand() {
        assertEquals("live stall #2: SVT 1 after 31 s, buffer 0 s, 18 s behind live",
                     Route.liveStallLine("SVT 1", 31_400, 300, 18_200, 2))
        assertEquals("live stall #1: X after 5 s, buffer 1 s", Route.liveStallLine("X", 5_000, 1_000, -9, 1))
    }

    @Test
    fun liveSwipesChangeChannelOrGroup() {
        assertEquals("next", Route.liveSwipe(10f, -300f, false))
        assertEquals("prev", Route.liveSwipe(-10f, 300f, true))
        assertEquals("next", Route.liveSwipe(-300f, 20f, false))
        assertEquals("prev", Route.liveSwipe(300f, 20f, false))
        assertEquals("groupNext", Route.liveSwipe(-300f, 20f, true))
        assertEquals("groupPrev", Route.liveSwipe(300f, 20f, true))
        assertEquals("", Route.liveSwipe(30f, 20f, false))
    }

    @Test
    fun liveChannelStepsRoundTheEnds() {
        assertEquals(1, Route.zap(0, 5, 1))
        assertEquals(0, Route.zap(4, 5, 1))
        assertEquals(4, Route.zap(0, 5, -1))
        assertEquals(0, Route.zap(0, 0, 1))
    }

    @Test
    fun aCountrysFlagFromItsCode() {
        assertEquals("\uD83C\uDDF8\uD83C\uDDEA", Route.flagOf("SE"))
        assertEquals("\uD83C\uDDFA\uD83C\uDDF8", Route.flagOf("us"))
        assertEquals("", Route.flagOf(""))
    }

    @Test
    fun aLiveStreamThatErrsPicksItselfUp() {
        assertEquals("edge", Route.liveRecover(1002, 0, 0))       // behind the live window
        assertEquals("retry", Route.liveRecover(2001, 0, 0))      // network
        assertEquals(null, Route.liveRecover(2004, 429, 0))       // the playlist's connections: refused
        assertEquals(null, Route.liveRecover(1002, 0, 6))         // too often in a row
    }

    @Test
    fun aLiveChannelMovesToTheOtherMachineWhenItsServerGoes() {
        val main = Server("Main", "http://10.0.0.1:8765", id = "a")
        val copy = Server("Copy", "http://10.0.0.2:8764", id = "b", copyOf = "http://10.0.0.1:8765")
        val friend = Server("Friend", "http://9.9.9.9:8765", id = "c")
        assertEquals(copy, Route.liveTwin(listOf(main, friend, copy), "http://10.0.0.1:8765/"))
        assertEquals(main, Route.liveTwin(listOf(main, friend, copy), "http://10.0.0.2:8764"))
        assertEquals(null, Route.liveTwin(listOf(main, friend), "http://10.0.0.1:8765"))
        // the server gone: one try, then the other machine; with none, tries as before
        assertEquals("retry", Route.liveRecover(2001, 0, 0, 0, twin = true))
        assertEquals("move", Route.liveRecover(2001, 0, 1, 0, twin = true))
        assertEquals("retry", Route.liveRecover(2001, 0, 1, 0, twin = false))
        // refused here: once to the other, never for the playlist's connections
        assertEquals("move", Route.liveRecover(2004, 504, 0, 0, twin = true))
        assertEquals(null, Route.liveRecover(2004, 504, 0, 1, twin = true))
        assertEquals(null, Route.liveRecover(2004, 429, 0, 0, twin = true))
        // and not back and forth for ever
        assertEquals(null, Route.liveRecover(2001, 0, 6, 4, twin = true))
    }

    @Test
    fun upAndDownStayInsideTheYouTubeCircle() {
        val g = listOf("YouTube", "YouTube", "YouTube", "Sweden", "News", "Sweden")
        assertEquals(1, Route.zapIn(g, 0, 1))
        assertEquals(0, Route.zapIn(g, 2, 1))       // round, inside the circle
        assertEquals(2, Route.zapIn(g, 0, -1))
        assertEquals(4, Route.zapIn(g, 3, 1))       // a playlist channel steps the playlist
        assertEquals(3, Route.zapIn(g, 5, 1))       // and round again, past YouTube
    }

    @Test
    fun liveFactsSayPictureSoundBufferAndGroup() {
        val said = Route.liveFacts("SVT 1", "Sweden", 1920, 1080, 50f, "avc1.64002A", 6_000_000,
                                   "mp4a.40.2", 48000, 2, 12_400, false, 3, 50f, 3, 14,
                                   "Sweden", false)
        assertTrue(said.contains("SVT 1 \u00b7 Sweden"))
        assertTrue(said.contains("Picture: 1920\u00d71080 \u00b7 50 fps \u00b7 avc1.64002A \u00b7 6.0 Mbit/s"))
        assertTrue(said.contains("Sound: mp4a.40.2 \u00b7 48 kHz \u00b7 2 ch"))
        assertTrue(said.contains("Buffer: 12 s \u00b7 dropped 3 \u00b7 screen 50 Hz"))
        assertTrue(said.contains("Channel 3 of 14"))
        assertTrue(Route.liveFacts("X", "", 0, 0, 0f, "", 0, "", 0, 0, 0, true, 0, 59.94f, 1, 2,
                                   "", true).contains("Group: \u2039 All \u203a  \u2013 Center to keep"))
        // a YouTube stream's source and show, straight under its title
        val tube = Route.liveFacts("Race", "YouTube", 0, 0, 0f, "", 0, "", 0, 0, 0, true, 0, 50f,
                                   1, 9, "", false,
                                   listOf("Source: YouTube \u00b7 GTWorld", "Show: Race"))
        assertTrue(tube.startsWith("Race \u00b7 YouTube\nSource: YouTube \u00b7 GTWorld\nShow: Race"))
    }

    @Test
    fun liveSwitchesTheScreenToTheChannelsRate() {
        val modes = listOf(Route.Mode(503, 3840, 2160, 59.94f), Route.Mode(504, 3840, 2160, 60f),
                           Route.Mode(505, 3840, 2160, 50f), Route.Mode(511, 1920, 1080, 50f),
                           Route.Mode(507, 3840, 2160, 23.976f))
        val now = modes[0]
        assertEquals(505, Route.modeFor(modes, now, 50f))
        assertEquals(505, Route.modeFor(modes, now, 25f))
        assertEquals(null, Route.modeFor(modes, now, 29.97f))
        assertEquals(507, Route.modeFor(modes, now, 23.976f))
        assertEquals(null, Route.modeFor(modes, modes[2], 50f))
        assertEquals(null, Route.modeFor(modes, now, 0f))
    }

    @Test
    fun liveCornerSaysSecondsInHand() {
        assertEquals("LIVE \u00b7 12 s buffered", Route.liveBufferLabel(12_900, false))
        assertEquals("LIVE \u00b7 loading\u2026", Route.liveBufferLabel(3_000, true))
    }

    @Test
    fun liveRefusedOnlyWhenEveryConnectionIsInUse() {
        assertTrue(Route.liveRefused(429)!!.contains("in use"))
        assertTrue(Route.liveRefused(504)!!.contains("did not start"))
        assertEquals(null, Route.liveRefused(500))
        assertEquals(null, Route.liveRefused(0))
    }

    @Test
    fun searchFromHomeSplitsFilmsAndTv() {
        assertTrue(Route.splitSearch("batman", "home", 3, 2))
        assertFalse(Route.splitSearch("batman", "films", 3, 2))
        assertFalse(Route.splitSearch("batman", "home", 3, 0))
        assertFalse(Route.splitSearch("", "home", 3, 2))
    }

    @Test
    fun versionCountOnlyWhenThereAreSeveral() {
        assertEquals("", Route.copiesSaid(1))
        assertEquals("", Route.copiesSaid(0))
        assertEquals("  (3)", Route.copiesSaid(3))
    }

    @Test
    fun episodesListOtherVersionsToo() {
        assertTrue(Route.hasOtherVersions("movie") && Route.hasOtherVersions("episode"))
        assertFalse(Route.hasOtherVersions("show") || Route.hasOtherVersions(null))
    }

    @Test
    fun serverChoosesTheSubtitle() {
        val eng = SubTrack(-1, "srt", "eng", "eng", decided = true)
        val swe = SubTrack(-2, "srt", "swe", "swe", start = true, decided = true)
        assertEquals(-2, Route.serverSub(listOf(eng, swe))?.index)
        // decided, none marked: none, not the app's own guess
        assertNull(Route.serverSub(listOf(eng.copy(), swe.copy(start = false)))!!.index)
        // an older server: no answer, and the app's rule decides
        assertNull(Route.serverSub(listOf(eng.copy(decided = false), swe.copy(decided = false))))
    }

    @Test
    fun tvKeepsTheRingOffTheEdge() {
        // with the cell's own padding, a ring stays about 5% in from a TV's sides
        assertTrue(Route.tvSafe(true).first >= 20 && Route.tvSafe(true).second >= 16)
        assertEquals(0 to 0, Route.tvSafe(false))
    }
}
