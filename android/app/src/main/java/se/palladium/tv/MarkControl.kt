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

/**
 * The two marks as one control: the watchlist on the left, the casual shelf on the right.
 *
 * They are one decision about a title taken in two steps - "I mean to watch this", and
 * "I would put this on without choosing" - so they are one control rather than two
 * buttons saying nearly the same word. The colour is the whole answer: yellow for the
 * list, green for the shuffle, grey for neither.
 *
 * The shuffle only draws from things on the list, so the right half puts a title on
 * both; taking it off the list takes it out of the shuffle with it. That rule lives in
 * the callers, which are the ones talking to the server.
 */
@Composable
fun MarkControl(
    listed: Boolean,
    casual: Boolean,
    onList: () -> Unit,
    onCasual: () -> Unit,
) {
    Row(
        Modifier
            .padding(end = 8.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(Skin.Panel2),
    ) {
        Half(mark = if (listed) "★" else "☆", on = listed,
             colour = Skin.Accent, onPress = onList)
        Box(Modifier.width(1.dp).background(Skin.Bg).padding(vertical = 2.dp)) {}
        Half(mark = "↻", on = casual, colour = Color(0xFF5FD08A),
             onPress = onCasual)
    }
}

/** One half of it. The white ring says where the remote is, as everywhere else. */
@Composable
private fun Half(mark: String, on: Boolean, colour: Color, onPress: () -> Unit) {
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
             color = if (on) Color(0xFF111111) else Skin.Dim)
    }
}
