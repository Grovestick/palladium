package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/*
 * The colours this server offers, as a row of swatches.
 *
 * The choice belongs to the viewer and the server keeps it, so it is the same colour
 * on the television and on the phone - and the rest of the house is not repainted by
 * one person's taste.
 */
@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun AccentPanel(
    swatches: List<Api.Accent>,
    /** the colour in force now */
    on: String,
    /** what this viewer chose, empty when they are taking the server's */
    mine: String,
    onPick: (String) -> Unit,
    onClose: () -> Unit,
) {
    fun of(code: String): Color? {
        val hex = code.trim().removePrefix("#")
        if (hex.length != 6) return null
        return runCatching { Color(hex.toLong(16) or 0xFF000000L) }.getOrNull()
    }
    AlertDialog(
        onDismissRequest = onClose,
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        modifier = Modifier.wrapContentWidth().widthIn(min = 420.dp, max = 640.dp),
        containerColor = Skin.Panel,
        title = {
            Text("Your colour", color = Skin.Fg, fontSize = 16.sp,
                 fontWeight = FontWeight.SemiBold)
        },
        text = {
            Column {
                Text("Applies to every screen you watch on. The server sets the " +
                     "default for accounts that have not chosen.",
                     color = Skin.Dim, fontSize = 13.sp,
                     modifier = Modifier.padding(bottom = 14.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    swatches.forEach { one ->
                        val paint = of(one.code) ?: return@forEach
                        var onIt by remember { mutableStateOf(false) }
                        val here = one.code.equals(on, true)
                        Box(
                            Modifier.size(44.dp).clip(CircleShape).background(paint)
                                .border(if (onIt || here) 3.dp else 0.dp,
                                        if (onIt) Color.White
                                        else if (here) Skin.Fg else Color.Transparent,
                                        CircleShape)
                                .onFocusChanged { onIt = it.isFocused }
                                .focusable()
                                .clickable { onPick(one.code) },
                            contentAlignment = Alignment.Center,
                        ) {
                            // the one that was chosen wears the tick; the one in force
                            // without a choice behind it wears only the ring
                            if (one.code.equals(mine, true)) {
                                Text("✓", color = Color(0xFF111111), fontSize = 17.sp,
                                     fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
                if (mine.isNotEmpty()) {
                    Box(Modifier.padding(top = 16.dp)) {
                        Pill("Use the server's colour") { onPick("") }
                    }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}
