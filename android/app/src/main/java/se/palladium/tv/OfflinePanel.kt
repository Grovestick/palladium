package se.palladium.tv

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import kotlinx.coroutines.launch
import androidx.compose.ui.graphics.Color
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Words for the Offline button on a title's page. */
fun offlineLabel(m: Media): String {
    val c = Offline.copies[m.ratingKey] ?: return "Offline"
    return when (c.state) {
        "ready" -> "✓ Offline"
        "failed" -> "Offline failed"
        "asked", "making", "fetching" -> if (Offline.held.value) "Offline · paused"
                                          else if (c.state == "asked") "Offline · asked"
                                          else "Offline · ${c.pct}%"
        "asked" -> "Offline · asked"
        else -> "Offline · ${c.pct}%"
    }
}

/** One copy as a question names it: title, quality and, kept, its size. */
fun offlineNamed(c: Offline.Copy): String =
    c.title + "  ·  " + Offline.qualityName(c.quality) +
        (if (c.state == "ready" && c.size > 0) "  ·  " + Offline.gb(c.size) else "")

/**
 * Asked before a copy is deleted, naming which (owner, 2026-11-12): removed, or replaced
 * by another quality - `instead` - which deletes it as well.
 */
@Composable
fun OfflineConfirm(c: Offline.Copy, instead: String?, onYes: () -> Unit, onNo: () -> Unit) {
    AlertDialog(
        onDismissRequest = onNo,
        containerColor = Skin.Panel,
        title = { Text(if (instead == null) "Remove from this device?" else "Replace?",
                       color = Skin.Fg, fontSize = 17.sp) },
        text = {
            Text(if (instead == null) offlineNamed(c)
                 else offlineNamed(c) + "\nwith " + Offline.qualityName(instead) +
                     ". The copy here is deleted.",
                 color = Skin.Fg, fontSize = 14.sp)
        },
        confirmButton = {
            Row {
                Pill(if (instead == null) "Remove" else "Replace", primary = true) { onYes() }
                Pill("Keep") { onNo() }
            }
        })
}

/**
 * Choosing a title for offline viewing: the quality the server makes the copy in, and
 * letting it go again. The copy goes to the storage chosen in Settings.
 */
@Composable
fun OfflinePanel(m: Media, onClose: () -> Unit) {
    val ctx = LocalContext.current
    val c = Offline.copies[m.ratingKey]
    // a delete waiting to be confirmed: "" removes, a quality replaces
    var asking by remember(m.ratingKey) { mutableStateOf<String?>(null) }
    val minutes = (m.durationMs / 60_000).coerceAtLeast(1)
    fun about(mbit: Double) = Offline.gb((minutes * 60 * mbit * 1e6 / 8).toLong())
    val choices = listOf(
        // the most it can be: the encoder's ceiling. Copies come out near half of it
        Triple("720", "720p", "at most " + about(4.2)),
        Triple("1080", "1080p", "at most " + about(8.2)),
        Triple("original", "Original", "an HEVC picture as it is, any other made HEVC at its size"))
        // none above the file's own
        .filter { it.first in Route.offlineQualities(m.height) }
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = { Text(m.title, color = Skin.Fg, fontSize = 17.sp) },
        text = {
            Column {
                if (c != null) {
                    Text(Offline.words(c), color = Skin.Fg, fontSize = 13.sp,
                         modifier = Modifier.padding(bottom = 10.dp))
                }
                Text("Quality", color = Skin.Dim, fontSize = 12.sp)
                Row {
                    choices.forEach { (q, label, _) ->
                        Pill(label, active = c?.quality == q) {
                            when {
                                c == null -> Offline.want(ctx, m, q)
                                c.state == "failed" && c.quality == q -> Offline.want(ctx, m, q)
                                c.quality != q -> asking = q
                            }
                        }
                    }
                }
                val picked = choices.firstOrNull { it.first == c?.quality }
                Text(if (picked == null) "Pick one: the server makes the copy and this device fetches it."
                     else picked.second + ": " + picked.third + ". HEVC; sound in stereo unless this device plays the film's own.",
                     color = Skin.Dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                Text("Saved on " + Offline.place(ctx).label, color = Skin.Dim, fontSize = 12.sp,
                     modifier = Modifier.padding(top = 4.dp))
            }
        },
        confirmButton = {
            Row {
                // paused from the notification: started again here
                if (c != null && Offline.held.value && c.state in setOf("asked", "making", "fetching"))
                    Pill("Resume", primary = true) { Offline.resume(ctx) }
                if (c != null) Pill("Remove") { asking = "" }
                Pill("Close") { onClose() }
            }
        })
    val ask = asking
    if (c != null && ask != null) {
        OfflineConfirm(c, ask.ifEmpty { null },
            onYes = {
                asking = null
                if (ask.isEmpty()) { Offline.drop(ctx, m.ratingKey); onClose() }
                else Offline.want(ctx, m, ask)
            },
            onNo = { asking = null })
    }
}


/** The quality a title is made in when `want` is asked for all of them: the best of
 *  what its file allows up to `want`. */
fun offlineQualityFor(m: Media, want: String): String {
    val may = Route.offlineQualities(m.height)
    return when {
        want in may -> want
        "720" in may -> "720"
        else -> "original"
    }
}

/** The most a copy can come to, in bytes: the encoder's ceiling over the running time,
 *  or the file's own rate for an original. 0 where the length is not known. */
fun offlineMost(m: Media, q: String): Long {
    val sec = m.durationMs / 1000.0
    val mbit = when (q) {
        "720" -> 4.2
        "1080" -> 8.2
        else -> m.bitrate / 1000.0
    }
    return (sec * mbit * 1e6 / 8).toLong()
}

/**
 * Every film and episode on the watchlist and among the favorites made offline at once
 * (owner, 2026-11-12): the quality, how many, and the most it can come to against the
 * room on the storage chosen, before anything is queued.
 */
@Composable
fun WatchlistOffline(onClose: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var titles by remember { mutableStateOf<List<Media>?>(null) }
    var want by remember { mutableStateOf("720") }
    LaunchedEffect(Unit) {
        val listed = runCatching { Api.watchlist(ctx) }.getOrDefault(emptyList()) +
            runCatching { Api.favorites(ctx) }.getOrDefault(emptyList())
        titles = listed.distinctBy { it.ratingKey }.filter {
            (it.type == "movie" || it.type == "episode") &&
                !it.offered && !it.askable && !it.upcoming && !it.live
        }
    }
    val all = titles
    val fresh = all?.filter { !Offline.copies.containsKey(it.ratingKey) } ?: emptyList()
    val most = fresh.sumOf { offlineMost(it, offlineQualityFor(it, want)) }
    val unknown = fresh.count { it.durationMs <= 0 }
    val place = Offline.place(ctx)
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = { Text("Watchlist offline", color = Skin.Fg, fontSize = 17.sp) },
        text = {
            Column {
                if (all == null) {
                    Text("Reading the watchlist…", color = Skin.Dim, fontSize = 13.sp)
                } else {
                    Text("Quality", color = Skin.Dim, fontSize = 12.sp)
                    Row {
                        Pill("720p", active = want == "720") { want = "720" }
                        Pill("1080p", active = want == "1080") { want = "1080" }
                    }
                    Text(
                        "${fresh.size} to make" +
                            (if (all.size > fresh.size) ", ${all.size - fresh.size} chosen already" else "") +
                            "  ·  at most " + Offline.gb(most) +
                            (if (unknown > 0) " and $unknown of unknown length" else "") +
                            "\nA title whose file is smaller is made at its own size." +
                            "\n${place.label}: " + Offline.gb(place.free) + " free" +
                            (if (most > place.free) "  ·  not room for all of it" else ""),
                        color = if (most > place.free) Color(0xFFE0A030) else Skin.Fg,
                        fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
                }
            }
        },
        confirmButton = {
            Row {
                if (fresh.isNotEmpty()) Pill("Start " + fresh.size, primary = true) {
                    scope.launch {
                        for (m in fresh) Offline.want(ctx, m, offlineQualityFor(m, want))
                    }
                    onClose()
                }
                Pill("Close") { onClose() }
            }
        })
}
