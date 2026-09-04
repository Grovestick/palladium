package se.palladium.tv

import android.app.Activity
import android.view.KeyEvent

/**
 * A press of the remote that Compose cannot answer, survived rather than crashed on.
 *
 * Reported from a Google TV: "ActiveParent must have a focusedChild", thrown inside
 * Compose's own two-dimensional focus search while a D-pad key was being dispatched.
 * It happens when whatever had focus leaves the screen - a dialog closing, a row
 * refreshing under the remote - leaving its parent marked as holding focus with
 * nothing inside it to hold. The next press up or down walks a tree that contradicts
 * itself, and throws.
 *
 * There is nothing to correct in our own layout: the state is already wrong by the
 * time the key arrives. So the press is dropped and focus let go, which sends the
 * next press looking from the top - a great deal better than the app disappearing
 * in the middle of a film.
 */
inline fun Activity.safeKey(event: KeyEvent, dispatch: () -> Boolean): Boolean =
    try {
        dispatch()
    } catch (broken: IllegalStateException) {
        if (broken.message?.contains("focusedChild") != true) throw broken
        android.util.Log.w("Palladium",
                           "focus search failed on key " + event.keyCode +
                           "; dropped the press", broken)
        window?.decorView?.findFocus()?.clearFocus()
        true
    }
