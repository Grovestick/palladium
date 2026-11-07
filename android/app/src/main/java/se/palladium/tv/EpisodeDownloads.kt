package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

/**
 * One season's packs on the tracker, pressed to add as a link: the season's episodes then
 * come from it, nothing downloaded. Green is the recommended one.
 */
@Composable
fun SeasonPacks(season: Media, onAdded: () -> Unit, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var got by remember { mutableStateOf<List<Api.SeasonPack>?>(null) }
    var said by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    // the packs already linked for this season, read again after each change
    var linked by remember { mutableStateOf<Pair<List<Api.LinkedPack>, Map<Int, String>>?>(null) }
    var changed by remember { mutableStateOf(0) }
    LaunchedEffect(season.ratingKey) { got = Api.seasonPacks(season) }
    LaunchedEffect(season.ratingKey, changed) { linked = Api.linkedPacks(season.title) }
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(Route.PICKER_WIDTH).widthIn(max = Route.PICKER_MAX.dp),
        title = { Text("Add pack  ·  Season " + (season.index ?: ""), color = Skin.Fg) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                   verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // what a press did, first: under the list it was off the screen
                if (said.isNotEmpty()) Text(said, color = Skin.Fg, fontSize = 14.sp,
                                            fontWeight = FontWeight.SemiBold)
                val number = season.index ?: 0
                val from = linked?.second?.get(number).orEmpty()
                val mine = linked?.first.orEmpty().filter { number in it.seasons }
                    .sortedByDescending { it.hash == from }
                Text(if (linked == null) "Reading what is added…"
                     else if (mine.isEmpty()) "No pack added for this season yet" else "Added",
                     color = Skin.Dim, fontSize = 13.sp)
                mine.forEach { p ->
                    Column(Modifier.fillMaxWidth()
                               .clip(RoundedCornerShape(Skin.round(8.dp)))
                               .background(Skin.Panel2)
                               .padding(horizontal = 12.dp, vertical = 9.dp)) {
                        Text(p.name, color = if (p.hash == from) Color(0xFF5FD08A) else Skin.Fg,
                             fontSize = 13.sp, lineHeight = 17.sp)
                        Text(listOf(if (p.hash == from) "This season comes from it" else "",
                                    if (p.seeds >= 0) "${p.seeds} seeding" else "")
                                 .filter { it.isNotEmpty() }.joinToString("  ·  "),
                             color = Skin.Dim, fontSize = 11.5.sp)
                        Pill("✕ Remove", small = true) {
                            if (!busy) {
                                busy = true
                                scope.launch {
                                    val said0 = Api.removePack(season.title, p.hash)
                                    said = said0.ifEmpty { "Pack removed - nothing deleted" }
                                    busy = false
                                    changed++
                                    onAdded()
                                }
                            }
                        }
                    }
                }
                Text("On the tracker", color = Skin.Dim, fontSize = 13.sp)
                val packs = got
                when {
                    packs == null -> Text("Asking the tracker…", color = Skin.Dim, fontSize = 14.sp)
                    packs.isEmpty() -> Text("The tracker has no pack of the whole season - " +
                                            "its episodes are downloaded one at a time",
                                            color = Skin.Dim, fontSize = 14.sp)
                    else -> packs.filter { !it.held }.forEach { p ->
                        val runs = if (p.seasons.size > 1)
                            "S%02d–S%02d".format(p.seasons.first(), p.seasons.last()) else "Season pack"
                        val facts = listOf(if (p.pick) "Recommended" else "", runs,
                                           "${p.seeds} seeding",
                                           if (p.size > 0) "%.1f GB".format(p.size / 1073741824.0) else "")
                            .filter { it.isNotEmpty() }.joinToString("  ·  ")
                        var lit by remember(p.id) { mutableStateOf(false) }
                        val ring = lit && focusShows()
                        Column(Modifier.fillMaxWidth()
                                   .clip(RoundedCornerShape(Skin.round(8.dp)))
                                   .background(if (ring) Skin.Accent else Skin.Panel2)
                                   .onFocusChanged { lit = it.isFocused || it.hasFocus }
                                   .focusable()
                                   .clickable(enabled = !busy) {
                                       busy = true
                                       scope.launch {
                                           val err = Api.addSeasonPack(season, p.id)
                                           said = err.ifEmpty { "Pack added - nothing downloaded" }
                                           busy = false
                                           changed++
                                           if (err.isEmpty()) onAdded()
                                       }
                                   }
                                   .padding(horizontal = 12.dp, vertical = 9.dp)) {
                            Text("+ Add pack  ·  " + p.name, color = if (ring) Color.Black
                                 else if (p.pick) Color(0xFF5FD08A) else Skin.Fg,
                                 fontSize = 13.sp, lineHeight = 17.sp)
                            Text(facts, color = if (ring) Color.Black else Skin.Dim,
                                 fontSize = 11.5.sp, modifier = Modifier.padding(top = 2.dp))
                        }
                    }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}

/**
 * The tracker's releases of an episode not here yet, early ones included: each with what
 * its colour means and its size, pressed to download. Green is the recommended one.
 */
@Composable
fun EpisodeDownloads(ep: Media, onPage: (() -> Unit)? = null, onTaken: () -> Unit = {},
                     onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var got by remember { mutableStateOf<Api.Carrying?>(null) }
    var said by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    // the tracker's releases, behind Other versions while a pack added here has it
    var more by remember { mutableStateOf(false) }
    LaunchedEffect(ep.ratingKey) {
        said = "Checking the tracker…"
        got = Api.trackerVersions(ep, true)
        if (said == "Checking the tracker…") said = ""
    }
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        // most of the screen, as the film's list: release names are long
        properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false),
        modifier = Modifier.fillMaxWidth(Route.PICKER_WIDTH).widthIn(max = Route.PICKER_MAX.dp),
        title = { Text("E" + (ep.index ?: "") + "  " + ep.title, color = Skin.Fg) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                   verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // what a press did, first: under the list it was off the screen
                if (said.isNotEmpty()) Text(said, color = Skin.Fg, fontSize = 14.sp,
                                            fontWeight = FontWeight.SemiBold)
                val rows = got?.rows
                when {
                    rows == null -> Text("Asking the tracker…", color = Skin.Dim, fontSize = 14.sp)
                    rows.isEmpty() -> Text("Nothing on the tracker for this one yet",
                                           color = Skin.Dim, fontSize = 14.sp)
                    else -> {
                        got?.free?.takeIf { it >= 0 }?.let {
                            Text("${it.toInt()} GB free", color = Skin.Dim, fontSize = 13.sp)
                        }
                        // the pack already added first, then the episode on its own,
                        // then the season packs on the tracker that hold it - each
                        // under a heading saying which it is
                        val kinds = rows.map { it.pack to it.box }
                        Route.releaseGroupsShown(kinds, more).forEach { (heading, at) ->
                        Text(heading, color = Skin.Fg, fontSize = 13.sp,
                             fontWeight = FontWeight.SemiBold,
                             modifier = Modifier.padding(top = 6.dp))
                        at.map { rows[it] }.forEach { v ->
                            val facts = listOf(
                                // both: "Recommended" alone hid that it was a pack's
                                if (v.pick) "Recommended" else "",
                                // the heading says pack or single; a box still says which
                                if (v.box && !v.pack)
                                    (if (v.seasonPack) "Season pack" else "Box set") else "",
                                if (v.seeds >= 0) "${v.seeds} seeding" else "",
                                if (v.size > 0) "%.1f GB".format(v.size / 1073741824.0) else "",
                                v.why,
                            ).filter { it.isNotEmpty() }.joinToString("  ·  ")
                            // the name whole, over as many lines as it takes: a one-line
                            // button cut it off where releases differ, at the end
                            var lit by remember(v.id) { mutableStateOf(false) }
                            val ring = lit && focusShows()
                            Column(Modifier.fillMaxWidth()
                                       .clip(RoundedCornerShape(Skin.round(8.dp)))
                                       .background(if (ring) Skin.Accent else Skin.Panel2)
                                       .onFocusChanged { lit = it.isFocused || it.hasFocus }
                                       .focusable()
                                       .clickable(enabled = v.why.isEmpty() && !busy) {
                                           busy = true
                                           scope.launch {
                                               val (ok, name) = Api.takeFromTracker(ep, v.id)
                                               busy = false
                                               if (ok) {
                                                   // said and closed: at the top of a scrolled
                                                   // list the word was off the screen
                                                   android.widget.Toast.makeText(ctx,
                                                       "Downloading - it can be played here as soon as it is in",
                                                       android.widget.Toast.LENGTH_LONG).show()
                                                   onTaken()
                                                   onClose()
                                               } else said = name
                                           }
                                       }
                                       .padding(horizontal = 12.dp, vertical = 9.dp)) {
                                Text(v.name.replace(Regex("""\.(mkv|mp4|avi|m4v|ts)$""",
                                                          RegexOption.IGNORE_CASE), ""),
                                     color = if (ring) Color.Black
                                             else if (v.why.isNotEmpty()) Color(0xFFFF6B6B)
                                             else if (v.pick) Color(0xFF5FD08A)
                                             else if (v.pack) PackBlue else Skin.Fg,
                                     fontSize = 13.sp, lineHeight = 17.sp)
                                Text(facts, color = if (ring) Color.Black else Skin.Dim,
                                     fontSize = 11.5.sp, modifier = Modifier.padding(top = 2.dp))
                            }
                            // a season pack: added to the series as a link, nothing
                            // downloaded - the season's episodes are then a press each
                            if (v.packId.isNotEmpty()) {
                                Pill("+ Add pack", small = true) {
                                    if (!busy) {
                                        busy = true
                                        scope.launch {
                                            val err = Api.usePack(ep.title, id = v.packId)
                                            said = err.ifEmpty { "Season pack added - nothing downloaded" }
                                            busy = false
                                        }
                                    }
                                }
                            }
                        }
                        }
                        if (Route.moreReleases(kinds, more)) Pill("Other versions", small = true) { more = true }
                    }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
        // an episode a pack offers has a page too: asking for it is done there
        dismissButton = onPage?.let { open -> { Pill("Episode page") { onClose(); open() } } },
    )
}
