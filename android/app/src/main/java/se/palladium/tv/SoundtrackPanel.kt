package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
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
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Which soundtrack: what the file holds, and which one is playing.
 *
 * Kept apart from the subtitle panel because they are different questions - a film
 * with three soundtracks and eleven subtitles is unreadable if they share a list - and
 * because the answers behave differently: a subtitle can be turned off, a soundtrack
 * cannot.
 *
 * Choosing one starts the film again where it was, since which soundtrack is heard is
 * settled while ffmpeg reads the file.
 */
@Composable
fun SoundtrackPanel(
    tracks: List<AudioTrack>,
    playing: Int?,
    onPick: (Int) -> Unit,
    onClose: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onClose,
        // Material caps a dialog at 560dp whatever it is asked for, which is a
        // postage stamp on a television across a room
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(0.8f),
        containerColor = Skin.Panel,
        // sized as the Quality menu beside it, on both screens (owner, 2026-11-14)
        title = { Text("Soundtrack", color = Skin.Fg,
                       fontSize = if (Api.device == "tv") 18.sp else 17.sp) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (tracks.isEmpty()) {
                    Text("Nothing is known about this film's sound.",
                         color = Skin.Dim,
                         fontSize = 14.sp)
                }
                if (tracks.size == 1) {
                    // it still opens: "which soundtrack am I hearing" is a fair
                    // question when the answer is "the only one there is"
                    Text("This film has one soundtrack.",
                         color = Skin.Dim,
                         fontSize = 14.sp,
                         modifier = Modifier.padding(bottom = 10.dp))
                }
                tracks.forEach { t ->
                    SoundRow(t, on = t.index == playing) { onPick(t.index) }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}

/** One soundtrack. The ring says where the remote is; the accent says what is playing. */
@Composable
private fun SoundRow(track: AudioTrack, on: Boolean, onPick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = if (Api.device == "tv") 1.dp else 2.dp)
            .clip(RoundedCornerShape(Skin.round(8.dp)))
            .background(if (on) Color(0x224A90F0) else Skin.Panel2)
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent,
                    RoundedCornerShape(Skin.round(8.dp)))
            .onFocusChanged { focused = it.isFocused }
            .focusable()
            .clickable(onClick = onPick)
            .padding(horizontal = if (Api.device == "tv") 16.dp else 12.dp,
                     vertical = if (Api.device == "tv") 5.dp else 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(track.label, color = if (on) Skin.Accent else Skin.Fg,
             fontSize = if (Api.device == "tv") 15.sp else 13.5.sp,
             modifier = Modifier.weight(1f))
        if (on) {
            Spacer(Modifier.width(10.dp))
            Text("playing", color = Skin.Accent,
                 fontSize = if (Api.device == "tv") 13.sp else 11.sp)
        }
    }
}
