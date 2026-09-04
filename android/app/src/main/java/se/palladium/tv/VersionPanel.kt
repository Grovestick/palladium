package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/*
 * Which file of a title to play, when the library holds more than one.
 *
 * A 4K copy and a 1080p one, or a remux beside something cut down for a phone: they
 * carry different subtitles and are out by different amounts, so the choice has to be
 * made before pressing play rather than inside the player. The browser has had this
 * as a dropdown since the beginning; the app played the first file and nothing else.
 */
@Composable
fun VersionPanel(
    media: Media,
    onPick: (Int) -> Unit,
    onClose: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onClose,
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        modifier = Modifier.wrapContentWidth().widthIn(min = 480.dp, max = 700.dp),
        containerColor = Skin.Panel,
        title = {
            Text("Which copy to play", color = Skin.Fg, fontSize = 16.sp,
                 fontWeight = FontWeight.SemiBold)
        },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                media.copies.forEachIndexed { at, copy ->
                    val here = copy.mi == media.mi
                    var onIt by remember { mutableStateOf(false) }
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 4.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(if (here) Skin.Panel2 else Color.Transparent)
                            .border(if (onIt) 2.dp else 1.dp,
                                    if (onIt) Color.White else Skin.Line,
                                    RoundedCornerShape(8.dp))
                            .onFocusChanged { onIt = it.isFocused }
                            .clickable(
                                interactionSource = remember { MutableInteractionSource() },
                                indication = null) { onPick(at) }
                            .padding(horizontal = 12.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            // the release name first: it is what tells two copies of
                            // the same film apart, and what a subtitle has to match
                            Text(copy.shown(), color = Skin.Fg, fontSize = 13.5.sp,
                                 fontFamily = if (copy.fileName != null)
                                     FontFamily.Monospace else FontFamily.Default,
                                 maxLines = 2, overflow = TextOverflow.Ellipsis)
                            val facts = listOfNotNull(
                                copy.brief(),
                                copy.audioCodec?.uppercase(),
                                (copy.bitrate / 1000f).takeIf { it >= 0.1f }
                                    ?.let { String.format(java.util.Locale.US,
                                                          "%.1f Mbit/s", it) },
                                copy.subtitleStreams.size
                                    .takeIf { it > 0 }?.let { it.toString() + " subtitles" },
                            ).joinToString("  ·  ")
                            Text(facts, color = Skin.Dim, fontSize = 12.sp,
                                 modifier = Modifier.padding(top = 3.dp))
                        }
                        if (here) {
                            Text("✓", color = Skin.Accent, fontSize = 15.sp,
                                 modifier = Modifier.padding(start = 10.dp))
                        }
                    }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}
