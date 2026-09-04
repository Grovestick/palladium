package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** What the player may ask for: the film at the size it was made, or smaller. */
val QUALITY_SIZES = listOf(0 to "Original — no limit", 1080 to "1080p at most",
                           720 to "720p at most")

/**
 * And how much line to use for it.
 *
 * 1080p looks like the file itself at eight megabits or more; 720p is comfortable at
 * four, and two is the figure that survives a hotel.
 */
val QUALITY_RATES = listOf(0 to "No limit", 20 to "20 Mbit at most",
                           12 to "12 Mbit at most", 8 to "8 Mbit at most",
                           5 to "5 Mbit at most", 4 to "4 Mbit at most",
                           2 to "2 Mbit at most")

/**
 * Picture size and megabits, while the film is playing.
 *
 * Both are settled while ffmpeg reads the file, so choosing either starts the film
 * again from where it had got to rather than switching mid-stream.
 *
 * The server has a ceiling of its own - one for the house, one for outside - and what
 * arrives is whichever of the two is lower. Asking for more than it allows is not an
 * error; it simply gets the ceiling.
 */
@Composable
fun QualityPanel(
    height: Int,
    mbit: Int,
    cap: String,
    plainSound: Boolean,
    /** what this panel is setting: one film, or everything this viewer plays */
    heading: String = "Quality for this film",
    note: String = "",
    /** the ceiling in force here, as (height, megabits); nought is no limit */
    capHeight: Int = 0,
    capMbit: Int = 0,
    /** the sound choice belongs to a film being played, not to a preference */
    withSound: Boolean = true,
    onPick: (Int, Int) -> Unit,
    onSound: (Boolean) -> Unit,
    onClose: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onClose,
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(0.8f),
        containerColor = Skin.Panel,
        title = { Text(heading, color = Skin.Fg,
                       fontSize = if (LocalConfiguration.current.screenWidthDp < 600)
                           17.sp else 22.sp) },
        text = {
            // Two columns side by side, each under its own heading and reading
            // straight down. Ten choices in one column is a list to be scrolled
            // through, whichever way the screen is turned.
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (note.isNotEmpty()) {
                    Text(note, color = Skin.Dim, fontSize = 14.sp,
                         modifier = Modifier.padding(bottom = 10.dp))
                }
                Row(horizontalArrangement =
                        androidx.compose.foundation.layout.Arrangement.spacedBy(10.dp)) {
                    Column(Modifier.weight(1f)) {
                        Text("Picture", color = Skin.Dim, fontSize = 15.sp,
                             modifier = Modifier.padding(bottom = 4.dp))
                        QUALITY_SIZES.forEach { (value, label) ->
                            // above the ceiling: shown, so it is clear what exists,
                            // but not offered - it would only be reduced again
                            val barred = capHeight > 0 && (value == 0 || value > capHeight)
                            QualityRow(label, on = value == height, barred = barred) {
                                if (!barred) onPick(value, mbit)
                            }
                        }
                    }
                    Column(Modifier.weight(1f)) {
                        Text("Line", color = Skin.Dim, fontSize = 15.sp,
                             modifier = Modifier.padding(bottom = 4.dp))
                        QUALITY_RATES.forEach { (value, label) ->
                            val barred = capMbit > 0 && (value == 0 || value > capMbit)
                            QualityRow(label, on = value == mbit, barred = barred) {
                                if (!barred) onPick(height, value)
                            }
                        }
                    }
                }
                // Headphones cannot be handed Dolby - nothing passes through a
                // Bluetooth pair - and a box that cannot decode it plays the picture
                // in silence. This is usually noticed automatically; here it can be
                // said outright, for the times it is not.
                if (withSound) {
                Text("Sound", color = Skin.Dim, fontSize = 15.sp,
                     modifier = Modifier.padding(top = 14.dp, bottom = 4.dp))
                Row(horizontalArrangement = androidx.compose.foundation.layout
                        .Arrangement.spacedBy(10.dp)) {
                    Column(Modifier.weight(1f)) {
                        QualityRow("As the file has it", on = !plainSound) {
                            onSound(false)
                        }
                    }
                    Column(Modifier.weight(1f)) {
                        QualityRow("Stereo, for headphones", on = plainSound) {
                            onSound(true)
                        }
                    }
                }
                }
                if (cap.isNotEmpty()) {
                    Text(cap, color = Skin.Dim, fontSize = 14.sp,
                         modifier = Modifier.padding(top = 14.dp))
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}

/** One choice. The ring says where the remote is; the accent says what is playing. */
@Composable
private fun QualityRow(label: String, on: Boolean, modifier: Modifier = Modifier,
                       barred: Boolean = false,
                       onPick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    // Two columns of these on a phone is about a hundred and sixty points a column,
    // and "20 Mbit at most" at eighteen points does not fit in it: the line wrapped
    // or was cut. A television has the room and keeps the larger type.
    val narrow = androidx.compose.ui.platform.LocalConfiguration.current
        .screenWidthDp < 600
    Row(
        modifier
            .fillMaxWidth()
            .padding(vertical = if (narrow) 2.dp else 3.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(if (on) Color(0x224A90F0) else Skin.Panel2)
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent,
                    RoundedCornerShape(8.dp))
            .onFocusChanged { focused = it.isFocused }
            .focusable()
            .clickable(onClick = onPick)
            .padding(horizontal = if (narrow) 12.dp else 20.dp,
                     vertical = if (narrow) 9.dp else 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label,
             color = if (barred) Color(0xFFF0704F) else if (on) Skin.Accent else Skin.Fg,
             fontSize = if (narrow) 13.5.sp else 18.sp,
             maxLines = 1, modifier = Modifier.weight(1f))
        if (barred) Text(if (narrow) "no" else "not allowed here",
                         color = Color(0xFFF0704F),
                         fontSize = if (narrow) 11.sp else 13.sp)
        else if (on) Text("now", color = Skin.Accent,
                          fontSize = if (narrow) 11.sp else 15.sp)
    }
}
