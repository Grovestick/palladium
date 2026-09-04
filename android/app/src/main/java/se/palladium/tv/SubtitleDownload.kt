package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** Proved on this series: green, the one colour not otherwise spoken for. */
private val Proved = Color(0xFF5FD08A)

/** The languages worth offering, in the order anyone here would want them. */
val SubLanguages = listOf(
    "en" to "English", "sv" to "Svenska", "no" to "Norsk", "da" to "Dansk",
    "fi" to "Suomi", "de" to "Deutsch", "fr" to "Français", "es" to "Español")

/** The language last chosen, so it does not have to be chosen again every time. */
private fun lastLanguage(ctx: android.content.Context) =
    ctx.getSharedPreferences("palladium", android.content.Context.MODE_PRIVATE)
        .getString("subLang", "en") ?: "en"

private fun rememberLanguage(ctx: android.content.Context, code: String) {
    ctx.getSharedPreferences("palladium", android.content.Context.MODE_PRIVATE)
        .edit().putString("subLang", code).apply()
}

/**
 * Choosing a subtitle to download for one title.
 *
 * The list is ordered as the server ranks it: an exact match to this very file first -
 * OpenSubtitles compares a hash of the video, so those are cut to the same release and
 * their timing is right - then whatever the most people have taken.
 *
 * Picking one here is also how a series learns. Nothing is remembered at this point,
 * because finding a subtitle that actually fits often takes two or three tries; the
 * one that carries an episode to the end is the one the series keeps.
 */
@Composable
fun SubtitleDownloadDialog(
    media: Media,
    language: String,
    scope: CoroutineScope,
    inUse: String,                       // the release the current track came from
    proved: String,                      // and the one this series has been proved on
    onTaken: (String) -> Unit,           // the release that was taken
    onClose: () -> Unit,
    //: a subtitle is being written from the sound: the film is what the viewer wants
    //: to be looking at while it happens
    onStarted: () -> Unit = {},
) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var results by remember { mutableStateOf<List<Api.SubCandidate>?>(null) }
    // the device's last choice to begin with, then the server's, which is the one
    // that follows this viewer between screens
    var speaking by remember { mutableStateOf(lastLanguage(ctx)) }
    LaunchedEffect(Unit) { speaking = Api.subtitleLanguage() }
    val first = remember { FocusRequester() }
    var problem by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf("") }

    LaunchedEffect(media.ratingKey, speaking) {
        results = null
        val (found, err) = Api.findSubtitles(media, speaking)
        results = found
        problem = err
        // start on the first candidate rather than on Close
        if (found.isNotEmpty()) runCatching { first.requestFocus() }
    }

    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = {
            Text("Subtitles to download", color = Skin.Fg, fontSize = 17.sp,
                 fontWeight = FontWeight.SemiBold)
        },
        text = {
            Column {
                // The file's own name, above the list and out of the scroll. Which
                // release a subtitle is cut to is the whole question here, and it
                // cannot be answered against a name that has scrolled away.
                media.fileName?.let { name ->
                    Text(name, color = Skin.Accent, fontSize = 11.5.sp,
                         maxLines = 2, overflow = TextOverflow.Ellipsis,
                         modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))
                }
                Column(Modifier.verticalScroll(rememberScrollState())) {
                // a subtitle is kept per language, so this is a choice rather than a
                // setting hidden somewhere else
                Row(Modifier.fillMaxWidth().padding(bottom = 8.dp)
                        .horizontalScroll(rememberScrollState())) {
                    SubLanguages.forEach { (code, name) ->
                        // Which language this list is showing, and which one would
                        // be written from the sound. It is a choice about this film,
                        // not about every film: looking for a Swedish subtitle once
                        // used to make Swedish the answer everywhere, for good.
                        // Settings, Subtitles is where that is decided.
                        Pill(name, active = code == speaking) {
                            speaking = code
                            rememberLanguage(ctx, code)
                        }
                        Spacer(Modifier.width(6.dp))
                    }
                }
                var making by remember { mutableStateOf<Api.Making?>(null) }
                var asked by remember(media.ratingKey) { mutableStateOf(false) }
                LaunchedEffect(media.ratingKey) {
                    while (true) {
                        val said = Api.makingSubtitles(media)
                        // The one this dialog asked for has something to draw: turn
                        // it on now rather than at the end. The file grows as the film
                        // is heard, and the player asks again as it nears the end of
                        // what it holds.
                        if (asked && said.file.isNotEmpty() &&
                            (said.on || said.ok == true)) {
                            asked = false
                            onTaken(said.file)
                        }
                        making = said
                        // a job started before this list was opened, or from another
                        // screen, should show itself without anybody pressing anything
                        kotlinx.coroutines.delay(if (said.on) 3000 else 8000)
                    }
                }
                val list = results
                when {
                    list == null -> Text("Searching…", color = Skin.Dim, fontSize = 14.sp)
                    problem.isNotEmpty() && list.isEmpty() ->
                        Text(problem, color = Color(0xFFF0704F), fontSize = 13.sp)
                    list.isEmpty() ->
                        Text("Nothing found for this one.", color = Skin.Dim, fontSize = 14.sp)
                    // all of them: which release matches is exactly what one is
                    // scrolling for, and cutting the list hides the right answer
                    else -> list.forEachIndexed { at, candidate ->
                        var focused by remember(candidate.id) { mutableStateOf(false) }
                        Row(
                            Modifier.fillMaxWidth().padding(vertical = 3.dp)
                                // white is what focus looks like everywhere in this
                                // app, so the one in use is amber instead - a tinted
                                // row rather than a word in a different colour
                                .background(
                                    if (inUse.isNotEmpty() && inUse.startsWith(
                                            candidate.name.removeSuffix(".srt")))
                                        Skin.Accent.copy(alpha = 0.22f) else Skin.Panel2,
                                    RoundedCornerShape(8.dp))
                                // the same white ring as everywhere else: this is
                                // where the remote is standing
                                .border(2.dp,
                                        if (focused) Color.White else Color.Transparent,
                                        RoundedCornerShape(8.dp))
                                .onFocusChanged { focused = it.isFocused }
                                .then(if (at == 0) Modifier.focusRequester(first)
                                      else Modifier)
                                .focusable()
                                .clickable {
                                    if (busy.isEmpty()) {
                                        busy = candidate.name
                                        scope.launch {
                                            val err = Api.getSubtitle(
                                                media, candidate.id, speaking,
                                                candidate.name)
                                            busy = ""
                                            if (err == null) {
                                                onTaken(candidate.name); onClose()
                                            }
                                            else problem = err
                                        }
                                    }
                                }
                                .padding(horizontal = 12.dp, vertical = 9.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            // named for the release it came from, which is exactly
                            // what these candidates are called
                            val bare = candidate.name.removeSuffix(".srt")
                            val using = inUse.isNotEmpty() && inUse.startsWith(bare)
                            // the server decides this: the same release as the one
                            // proved on this series, which is named differently on
                            // every episode
                            // Only what has been proved carries the tick. A name
                            // that matches the file says where the subtitle came from,
                            // not that anybody has watched the film with it.
                            val sure = candidate.confirmed
                            Column(Modifier.weight(1f)) {
                                Text(candidate.name,
                                     color = if (using) Skin.Accent else Skin.Fg,
                                     fontSize = 13.5.sp,
                                     maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(
                                    listOfNotNull(
                                        if (sure) "confirmed" else null,
                                        if (using) "in use" else null,
                                        if (candidate.sameName) "matches this file by name"
                                        else if (candidate.hashOdd)
                                            "hash says this file, name says another"
                                        else if (candidate.fromHash) "matches this file"
                                        else null,
                                        candidate.downloads.toString() + " downloads",
                                    ).joinToString("  ·  "),
                                    color = if (sure || candidate.sameName ||
                                                (candidate.fromHash &&
                                                 !candidate.hashOdd)) Proved
                                            else if (using) Skin.Accent
                                            else Skin.Dim,
                                    fontSize = 11.5.sp)
                            }
                            // green for proved on this series, amber for playing now
                            if (busy.isEmpty() && (sure || using)) {
                                Text("\u2713",
                                     color = if (sure) Proved else Skin.Accent,
                                     fontSize = 16.sp)
                            }
                            if (busy == candidate.name) {
                                Text("…", color = Skin.Accent, fontSize = 16.sp)
                            }
                        }
                    }
                }
                // The last row of the list, in the language the list is showing:
                // when nobody has published one cut to this release, the soundtrack
                // is still there to be listened to. "ai-gen" is in the file's name
                // as well, so it stays obvious afterwards.
                var wanted by remember(media.ratingKey) { mutableStateOf(false) }
                val called = SubLanguages.firstOrNull { it.first == speaking }
                    ?.second ?: speaking.uppercase()
                val busyNow = making?.on == true
                // this film waiting its turn behind another: one at a time, and the
                // queue says how long the wait is
                val stem = (media.fileName ?: "").substringBeforeLast(".")
                val waiting = stem.isNotEmpty() &&
                    making?.queued?.any { it.startsWith(stem) } == true
                // Only the two the machine can actually write: it hears any language,
                // but it can only translate into English, and Swedish comes from
                // English through a second model. Offering the rest offers a refusal.
                var madeFocus by remember { mutableStateOf(false) }
                if (speaking == "en" || speaking == "sv")
                Row(Modifier.fillMaxWidth().padding(top = 6.dp)
                        .background(Skin.Panel2, RoundedCornerShape(8.dp))
                        // the same white ring the rows above carry: this is where
                        // the remote is standing
                        .border(2.dp,
                                if (madeFocus) Color.White else Color.Transparent,
                                RoundedCornerShape(8.dp))
                        .onFocusChanged { madeFocus = it.isFocused }
                        .focusable()
                        // while one is being written, the same row stops it: a film
                        // put on by mistake holds the card for half an hour
                        .clickable {
                            if (busyNow) {
                                scope.launch {
                                    Api.stopMaking(media)
                                    making = Api.makingSubtitles(media)
                                }
                            } else {
                                wanted = true
                                asked = true
                                scope.launch {
                                    val why = Api.makeSubtitles(media, speaking)
                                    if (why.isNotEmpty()) {
                                        problem = why
                                    } else {
                                        // back to the film: it says how far along it
                                        // is over the picture until the words arrive
                                        onStarted()
                                    }
                                    making = Api.makingSubtitles(media)
                                }
                            }
                        }
                        .padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        // one already written for this file in this language: say so
                        // rather than offering to write the same thing again
                        val made = media.subtitleStreams.firstOrNull {
                            it.index < 0 && it.label.contains("ai-gen", true) &&
                            it.language.startsWith(speaking.take(2), true)
                        }
                        val there = made != null || (making?.ok == true && wanted)
                        // a file whose last line lands well before the end is one
                        // that is still being written, or was stopped part way
                        val part = made?.short == true
                        Text(when {
                                 waiting -> "Queued"
                                 busyNow -> "Stop generation"
                                 there && part -> called + " ai-gen, part of it"
                                 there -> called + " ai-gen completed"
                                 else -> "Make " + called + " (ai-gen)"
                             },
                             // stopping throws away what has been written: red, as
                             // everything else that takes something away is
                             color = if (busyNow) Color(0xFFF0704F) else Skin.Fg,
                             fontSize = 13.sp)
                        val said = making
                        Text(when {
                                 waiting && said != null ->
                                     "waiting for " + said.title + " - " +
                                     (said.at * 100).toInt() + "%"
                                 said?.on == true ->
                                     (said.at * 100).toInt().toString() + "%  ·  " +
                                     said.what
                                 said?.can == false -> "no speech model on this server"
                                 // a failure is worth reporting only when it is
                                 // about this file and nothing newer is running
                                 said?.ok == false && wanted &&
                                     said.file.startsWith(
                                         (media.fileName ?: "").substringBeforeLast("."))
                                     -> said.what
                                 there && part -> "unfinished - press to write it again"
                                 there -> "press to write it again"
                                 else -> "written from the sound of this file"
                             },
                             color = Skin.Dim, fontSize = 11.5.sp)
                    }
                }
                if (problem.isNotEmpty() && !results.isNullOrEmpty()) {
                    Text(problem, color = Color(0xFFF0704F), fontSize = 12.sp,
                         modifier = Modifier.padding(top = 8.dp))
                }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )
}
