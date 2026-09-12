package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
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
import androidx.compose.ui.unit.sp

/** The watchlist button - off, on the watchlist, a favorite - and the collections. */
@Composable
fun MarkControl(
    listed: Boolean,
    onList: () -> Unit,
    /** a favorite: on the watchlist, kept when watched, drawn as a red heart */
    favorite: Boolean = false,
    inCollection: Boolean = false,
    onCollection: (() -> Unit)? = null,
) {
    Row(
        Modifier
            .padding(end = 8.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(Skin.Panel2),
    ) {
        Half(mark = if (favorite) "♥" else if (listed) "★" else "☆", on = listed || favorite,
             colour = Skin.Accent, onPress = onList,
             // a favorite: the heart red, on the ground every chosen button has
             markColour = if (favorite) Color(0xFFFF4D5E) else null)
        if (onCollection != null) {
            Box(Modifier.width(1.dp).background(Skin.Bg).padding(vertical = 2.dp)) {}
            Half(mark = "↻", on = inCollection, colour = Color(0xFF8AB4F8),
                 onPress = onCollection)
        }
    }
}

/** One half of it. The white ring says where the remote is, as everywhere else. */
@Composable
private fun Half(mark: String, on: Boolean, colour: Color, onPress: () -> Unit,
                 markColour: Color? = null) {
    var focused by remember { mutableStateOf(false) }
    Box(
        Modifier
            .background(if (on) colour else Color.Transparent)
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent,
                    RoundedCornerShape(9.dp))
            .onFocusChanged { focused = it.isFocused }
            .focusable()
            .clickable(onClick = onPress)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(mark, fontSize = 17.sp,
             color = markColour ?: if (on) Color(0xFF111111) else Skin.Dim)
    }
}
