package se.palladium.tv

import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type

/**
 * Where a press of the remote goes at the edge of a list, decided rather than searched
 * for.
 *
 * A browse screen is bands, one above the next: the picker and the search box, the tabs,
 * and whatever list is open. Inside a band the framework's own search moves along a row
 * and from row to row, which it does well and is left alone to do - a button goes
 * straight down into whatever is under it. Between bands it cannot: a list recycles the
 * rows that have scrolled away, so a search upwards finds nothing there and takes the
 * nearest thing on the screen instead, which is how up out of a list arrived at the
 * server picker.
 *
 * So the edges are ours, and this is the only thing in the app that decides a direction.
 * A place says what to do at its own edge and answers false everywhere else, and then
 * the press is the search's as usual. It is answered before the search runs rather than
 * after it, which is the whole difference: afterwards, the focus had already gone.
 */
fun Modifier.edge(up: (() -> Boolean)? = null, down: (() -> Boolean)? = null,
                  left: (() -> Boolean)? = null,
                  right: (() -> Boolean)? = null): Modifier =
    onPreviewKeyEvent { e ->
        if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
        when (e.key) {
            Key.DirectionUp -> up?.invoke()
            Key.DirectionDown -> down?.invoke()
            Key.DirectionLeft -> left?.invoke()
            Key.DirectionRight -> right?.invoke()
            else -> null
        } ?: false
    }

/**
 * Hand the focus to something, and say whether it was taken.
 *
 * Nothing, or a name for something not on the screen - a shelf belonging to another tab,
 * an empty list - is not an edge after all: false leaves the press to the search rather
 * than swallowing it, which is what left a remote with nothing to do.
 */
fun moveTo(wanted: FocusRequester?): Boolean =
    wanted != null && runCatching { wanted.requestFocus() }.isSuccess

/** How many posters are across one row of a grid, from what is on the screen. */
fun columnsNow(info: androidx.compose.foundation.lazy.grid.LazyGridLayoutInfo): Int {
    val seen = info.visibleItemsInfo
    val top = seen.firstOrNull()?.offset?.y ?: return 0
    return seen.count { it.offset.y == top }
}
