package se.palladium.tv

import androidx.appcompat.app.AppCompatActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

//: a copy in one of the house's packs, told apart from the tracker's own releases
val PackBlue = Color(0xFF7FB4FF)

/**
 * Every version of one film to download, whatever there is of them - one included, so a
 * download is always a choice somebody confirmed. The tracker's releases and the packs'
 * copies side by side, each saying which it is; on disk orange, the settings' pick green,
 * a pack copy blue, one that cannot be had red.
 */
@Composable
fun DownloadPicker(full: Media, rows: List<Api.Carried>, onClose: () -> Unit,
                   onSaid: (String) -> Unit,
                   /** GB free where it would land; negative when not known */
                   free: Double = -1.0) {
    val ctx = LocalContext.current
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = {
            Text("Which one" + (if (free >= 0) "  ·  " + Math.round(free) + " GB free" else ""),
                 color = Skin.Fg, fontSize = 18.sp)
        },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                // it opens on the one the settings put forward, lit
                val opening = remember { FocusRequester() }
                val first = rows.firstOrNull { it.pick } ?: rows.firstOrNull { it.why.isEmpty() }
                // a dialog is its own window, and a request made before that window has
                // focus is dropped without a word: asked again until a row holds it
                var landed by remember { mutableStateOf(false) }
                LaunchedEffect(Unit) {
                    repeat(14) {
                        kotlinx.coroutines.delay(150)
                        if (landed) return@LaunchedEffect
                        runCatching { opening.requestFocus() }
                    }
                }
                rows.forEach { one ->
                    var lit by remember(one.id) { mutableStateOf(false) }
                    val ring = lit && focusShows()
                    Column(
                        Modifier.fillMaxWidth()
                            .then(if (one === first) Modifier.focusRequester(opening) else Modifier)
                            .padding(bottom = 8.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(if (ring) Skin.Accent else Skin.Panel2)
                            .onFocusChanged {
                                lit = it.isFocused || it.hasFocus
                                if (lit) landed = true
                            }
                            .focusable()
                            .clickable {
                                onClose()
                                when {
                                    one.have -> onSaid("That one is on disk already")
                                    one.why.isNotEmpty() -> onSaid(one.why)
                                    else -> {
                                        onSaid("Starting the download…")
                                        (ctx as AppCompatActivity).lifecycleScope.launch {
                                            val (got, word) = Api.takeFromTracker(full, one.id)
                                            onSaid(if (got) "Downloading $word" else word)
                                        }
                                    }
                                }
                            }
                            .padding(horizontal = 12.dp, vertical = 9.dp)) {
                        Text(one.name,
                             color = if (ring) Color.Black
                                     else if (one.why.isNotEmpty()) Color(0xFFFF6B6B)
                                     else if (one.have) Color(0xFFE0B341)
                                     else if (one.pick) Color(0xFF5FD08A)
                                     else if (one.pack) PackBlue
                                     else Skin.Fg,
                             fontSize = 13.sp, lineHeight = 17.sp)
                        val under = listOfNotNull(
                            when { one.disk -> "On disk"; one.pack -> "Pack"; else -> "Tracker" },
                            "${one.seeds} seeding".takeIf { !one.disk },
                            one.mbit.takeIf { it > 0 }?.let { "$it Mbit/s" },
                            one.size.takeIf { it > 0 }?.let {
                                String.format(java.util.Locale.US, "%.1f GB", it / 1073741824.0) },
                            "on disk".takeIf { one.have && !one.disk },
                            one.why.takeIf { it.isNotEmpty() },
                        ).joinToString("   ·   ")
                        Text(under, color = if (ring) Color.Black else Skin.Dim,
                             fontSize = 11.5.sp, modifier = Modifier.padding(top = 2.dp))
                    }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } })
}
