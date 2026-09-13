package se.palladium.tv

import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * One rule: a machine holding the file carries the film.
 *
 * Tried here rather than on a television. Each of these was a way the old rules found
 * to leave a connected machine carrying nothing.
 */
class ShareTest {

    @Test
    fun `a machine holding the file always takes work`() {
        assertFalse(Share.leaveIt(ours = 10.0, best = 10.0, near = true, idleMs = 0))
    }

    @Test
    fun `a slower machine takes work too`() {
        // it used to be kept off the stretch about to be played; a stretch that
        // arrives late is given up and fetched again instead
        assertFalse(Share.leaveIt(ours = 1.0, best = 10.0, near = true, idleMs = 0))
    }

    @Test
    fun `a much slower machine still takes work`() {
        assertFalse(Share.leaveIt(ours = 0.1, best = 100.0, near = true, idleMs = 0))
    }

    @Test
    fun `far work as well as near work`() {
        assertFalse(Share.leaveIt(ours = 1.0, best = 10.0, near = false, idleMs = 0))
    }

    @Test
    fun `a machine that has carried nothing for an hour is not written off`() {
        assertFalse(Share.leaveIt(ours = 1.0, best = 10.0, near = true,
                                  idleMs = 3_600_000))
    }

    @Test
    fun `and one that has just carried something is not written off either`() {
        assertFalse(Share.leaveIt(ours = 1.0, best = 10.0, near = true, idleMs = 1))
    }

    @Test
    fun `four machines is the most the reader can hold`() {
        org.junit.Assert.assertEquals(4, TwoWays.WAYS)
    }
}
