package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/** The watchlist button - off, on the watchlist, a favorite - and the collections. */
@Composable
fun MarkControl(
    listed: Boolean,
    onList: () -> Unit,
    /** a favorite: on the watchlist, kept when watched, drawn as a red heart */
    favorite: Boolean = false,
    inCollection: Boolean = false,
    onCollection: (() -> Unit)? = null,
    /** chosen for offline viewing: the watchlist button green (owner, 2026-11-12) */
    offline: Boolean = false,
) {
    // two buttons apart: joined into one ground they read as a single button until
    // the remote reached one of them (owner, 2026-11-11)
    Row(Modifier.padding(end = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        // the tab row's marks: the bookmark, filled once on the list; a favorite's heart
        Half(mark = if (favorite) FavoriteIcon else if (listed) WatchlistIcon else WatchlistOffIcon,
             on = listed || favorite || offline,
             colour = if (offline) Color(0xFF2E9E5B) else Skin.Accent, onPress = onList,
             // a favorite: the heart red, on the ground every chosen button has
             markColour = if (favorite) Color(0xFFFF4D5E) else null)
        if (onCollection != null) {
            Half(mark = CollectionsIcon, on = inCollection, colour = Color(0xFF8AB4F8),
                 onPress = onCollection)
        }
    }
}

/** One half of it. The white ring says where the remote is, as everywhere else. */
@Composable
private fun Half(mark: androidx.compose.ui.graphics.vector.ImageVector, on: Boolean, colour: Color,
                 onPress: () -> Unit,
                 markColour: Color? = null) {
    var focused by remember { mutableStateOf(false) }
    Box(
        Modifier
            .clip(RoundedCornerShape(Skin.round(9.dp)))
            .background(if (on) colour else Skin.Panel2)
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent,
                    RoundedCornerShape(Skin.round(9.dp)))
            .onFocusChanged { focused = it.isFocused }
            .focusable()
            .clickable(onClick = onPress)
            // Play's own height beside it: the same padding over the same 24 sp line, where
            // 10 dp made these two a head taller than Play (owner, 2026-11-15)
            .padding(horizontal = 16.dp,
                     vertical = if (androidx.compose.ui.platform.LocalConfiguration.current
                                        .screenHeightDp < 560) 5.dp else 9.dp),
        contentAlignment = Alignment.Center,
    ) {
        // the mark on Play's 24 sp line, so the two stand as tall as Play
        androidx.compose.material3.Icon(mark, contentDescription = mark.name,
             tint = markColour ?: if (on) Color(0xFF111111) else Skin.Dim,
             modifier = Modifier.size(width = 20.dp, height = 24.dp))
    }
}
