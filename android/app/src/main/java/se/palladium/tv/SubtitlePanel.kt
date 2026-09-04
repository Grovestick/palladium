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
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import kotlinx.coroutines.launch
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/*
 * The subtitle panel, built the way the browser builds it.
 *
 * One dropdown of the tracks with Off at the top, a tick beside it to vouch for the one
 * showing, then timing. Release names run past forty characters, and a row each filled
 * the dialog with filenames. How a track reaches the screen is said in the entry: text
 * is drawn over the picture, a picture subtitle has to be burnt in, which costs an
 * encode.
 */

// Two greens meant every track looked as though it had been vouched for: the colour
// of a name says how it reaches the screen, and green is reserved for the tick that
// says somebody watched a film through with it.
private val TextKind = Color(0xFF7FA8CC)     // drawn over the picture
private val BurnKind = Color(0xFFA8794B)     // painted into it, at the cost of an encode
private val Verified = Color(0xFF5FD08A)     // watched through with, and said to fit

@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun SubtitlePanel(
    media: Media,
    chosen: Int?,
    onPick: (Int?) -> Unit,
    onVerify: (SubTrack, Boolean) -> Unit,
    onDownload: () -> Unit,
    onClose: () -> Unit,
    /** what to call the subtitle being written from the sound, if one is */
    pending: String? = null,
    /** how far the text is moved by hand, in seconds; positive is later */
    nudge: Float = 0f,
    onNudge: (Float) -> Unit = {},
    /** listening to the film to place this subtitle, and whether to do it always */
    // whether the next episode's subtitle is fetched before it starts, and the way
    // to say no: the same tick the browser has under Settings
    fetchAhead: Boolean = true,
    onFetchAhead: (Boolean) -> Unit = {},
    syncing: Boolean = false,
    autoSync: Boolean = false,
    /** what the last measurement decided, in words, or nothing yet */
    syncNote: String = "",
    /** whether the file being served is already corrected in parts */
    mended: Boolean = false,
    /** what is already in force: "static", "drift", "steps", or nothing */
    inForce: String = "",
    onSync: () -> Unit = {},
    onReset: () -> Unit = {},
    onAutoSync: (Boolean) -> Unit = {},
    /**
     * Whether the timing controls are shown.
     *
     * They belong where the film is: moving a subtitle by a tenth of a second, or
     * measuring it against the sound, is judged by watching it. Opened from a film's
     * page there is nothing to judge, so the panel is about which subtitle and
     * nothing else.
     */
    timing: Boolean = true,
    /**
     * Why the timing controls cannot be used, when they cannot.
     *
     * A picture subtitle drawn by the device is a set of images with their size and
     * their place already in them: there is nothing to move and nothing to restyle.
     * The controls stay where they are, faded, with the reason beside them - a
     * control that has quietly vanished sends somebody hunting for a setting they
     * think they have lost.
     */
    frozen: String = "",
) {
    val text = media.textSubs()
    val bitmap = media.bitmapSubs()
    // fetched files first, then this viewer's language: the container's own order is
    // whatever the encoder felt like
    val tracks = media.inOrder(text + bitmap,
                               java.util.Locale.getDefault().language)

    AlertDialog(
        onDismissRequest = onClose,
        // wide enough that the timing controls, the sync button and its tick stay on
        // one line: Material sizes a dialog for a phone standing up
        properties = androidx.compose.ui.window.DialogProperties(
            usePlatformDefaultWidth = false),
        // as wide as its widest row wants and no wider - the timing controls, the
        // sync button and its tick - with a margin either side
        modifier = Modifier.wrapContentWidth().widthIn(min = 520.dp, max = 700.dp),
        containerColor = Skin.Panel,
        title = {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    // an episode is not a film, and calling it one beside a heading
                    // that names the programme reads as the wrong panel
                    Text(if (media.type == "episode") "Subtitles for this episode"
                         else "Subtitles for this film",
                         color = Skin.Fg, fontSize = 16.sp,
                         fontWeight = FontWeight.SemiBold)
                    // The release counts the episodes differently from the season, and
                    // the scanner filed them by title rather than by number. Said here
                    // because this is the screen where the two numbers are compared:
                    // the file says one episode, the subtitle list another.
                    val shift = media.numberShift ?: 0
                    if (shift != 0) {
                        Text("File numbers " + (if (shift > 0) "+" else "") + shift +
                             " auto shifted",
                             color = Skin.Accent, fontSize = 12.5.sp,
                             maxLines = 1, softWrap = false,
                             modifier = Modifier.padding(start = 10.dp))
                    }
                    Spacer(Modifier.weight(1f))
                }
                // Where to go if the scanner got it wrong: the season can be told to
                // follow the filenames instead, and this is the screen where somebody
                // notices that it should be.
                if ((media.numberShift ?: 0) != 0) {
                    Text("The scanner put these episodes on the numbers their titles " +
                         "say. Settings → Library → Episode numbering has " +
                         "the last word.",
                         color = Skin.Dim, fontSize = 11.5.sp,
                         modifier = Modifier.padding(top = 4.dp))
                }
            }
        },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                // The release this copy is: two files of the same episode are out by
                // two different amounts, and the name is the only thing that says
                // which one a subtitle was cut for. Looking for it meant leaving the
                // player and finding the file.
                media.fileName?.let { named ->
                    Text(named, color = Skin.Dim, fontSize = 11.5.sp,
                         modifier = Modifier
                             .padding(bottom = 10.dp)
                             .background(Skin.Panel2,
                                         androidx.compose.foundation.shape
                                             .RoundedCornerShape(7.dp))
                             .padding(horizontal = 10.dp, vertical = 7.dp))
                }
                if (tracks.isEmpty()) {
                    Text("Nothing beside this film, and nothing inside it.",
                         color = Skin.Dim, fontSize = 13.5.sp)
                }
                val showing = tracks.firstOrNull { it.index == chosen }
                val nameOf: (SubTrack) -> String = { track ->
                    // language first: "Forced" alone does not say which language
                    track.shown()
                }
                // The tick is drawn separately and in its own green: the name is
                // coloured for how the track reaches the screen, and a tick that took
                // that colour said nothing. One track per language can carry it, and
                // it is earned by hand or by watching the film through.
                // negative index: a file beside the video. The rest are in the film.
                val fromTheFilm: @Composable (SubTrack) -> Unit = { track ->
                    if (track.index >= 0) {
                        Text("💿", fontSize = 12.sp,
                             modifier = Modifier.padding(end = 6.dp))
                    }
                }
                val ticked: @Composable (SubTrack) -> Unit = { track ->
                    if (track.confirmed) {
                        Text("✓", color = Verified, fontSize = 13.5.sp,
                             modifier = Modifier.padding(start = 8.dp))
                    }
                }
                // colour says how it reaches the screen; the key explains the colours
                val kindOf: (SubTrack) -> Color = { track ->
                    if (bitmap.any { it.index == track.index }) BurnKind else TextKind
                }
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text("Show", color = Skin.Dim, fontSize = 13.sp,
                         modifier = Modifier.padding(end = 8.dp))
                    var open by remember { mutableStateOf(false) }
                    var onIt by remember { mutableStateOf(false) }
                    Box(Modifier.weight(1f)) {
                        Row(
                            Modifier.fillMaxWidth()
                                .clip(RoundedCornerShape(8.dp))
                                .background(Skin.Panel2)
                                .border(if (onIt) 2.dp else 1.dp,
                                        if (onIt) Color.White else Skin.Line,
                                        RoundedCornerShape(8.dp))
                                .onFocusChanged { onIt = it.isFocused }
                                .clickable(
                                    interactionSource = remember { MutableInteractionSource() },
                                    indication = null) { open = true }
                                .padding(horizontal = 12.dp, vertical = 9.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            showing?.let { fromTheFilm(it) }
                            // The release is the whole point of the name, and it
                            // is at the end of it: one line cut short showed three
                            // subtitles all reading "Westworld.1973.1080p.Blu...".
                            // Set at the size of the file name above, which is what
                            // it is being compared against.
                            // the one being written is chosen but is no track yet:
                            // saying "Off" of it was saying the opposite of the truth
                            val saidNow = when {
                                showing != null -> nameOf(showing)
                                chosen == PENDING_SUB && pending != null -> pending
                                else -> "Off"
                            }
                            Text(saidNow,
                                 color = if (showing == null) Skin.Fg else kindOf(showing),
                                 fontSize = 11.5.sp, maxLines = 3,
                                 overflow = TextOverflow.Ellipsis,
                                 modifier = Modifier.weight(1f))
                            showing?.let { ticked(it) }
                            Text("▾", color = Skin.Dim, fontSize = 13.sp,
                                 modifier = Modifier.padding(start = 8.dp))
                        }
                        androidx.compose.material3.DropdownMenu(
                            expanded = open,
                            onDismissRequest = { open = false },
                            // Wide enough for a release name. The menu takes its width
                            // from the widest line it can fit, and the tick after the
                            // name was taking the room the end of the name needed -
                            // which is the half that says which release it is.
                            modifier = Modifier.background(Skin.Panel2)
                                .widthIn(min = 280.dp, max = 620.dp),
                        ) {
                            androidx.compose.material3.DropdownMenuItem(
                                text = { Text("Off", fontSize = 11.5.sp,
                                              color = if (chosen == null) Skin.Accent
                                                      else Skin.Fg) },
                                onClick = { open = false; onPick(null) })
                            // the one being written: choosable like any other, and
                            // unchosen by choosing something else
                            pending?.let { said ->
                                androidx.compose.material3.DropdownMenuItem(
                                    text = { Text(said, fontSize = 11.5.sp,
                                                  color = if (chosen == PENDING_SUB)
                                                      Skin.Accent else Skin.Fg) },
                                    onClick = { open = false; onPick(PENDING_SUB) })
                            }
                            tracks.forEach { track ->
                                androidx.compose.material3.DropdownMenuItem(
                                    text = {
                                        Row(Modifier.fillMaxWidth(),
                                            verticalAlignment =
                                                Alignment.CenterVertically) {
                                            fromTheFilm(track)
                                            // wraps rather than ends in dots: three
                                            // downloaded subtitles for one film differ
                                            // only at the end of the name
                                            Text(nameOf(track), fontSize = 11.5.sp,
                                                 fontWeight = if (track.index == chosen)
                                                     FontWeight.SemiBold else null,
                                                 color = kindOf(track),
                                                 modifier = Modifier.weight(1f))
                                            // after the name, not before it
                                            ticked(track)
                                        }
                                    },
                                    onClick = { open = false; onPick(track.index) })
                            }
                        }
                    }
                    // vouching for the one showing, or taking the mark back
                    if (showing != null) {
                        var ticked by remember { mutableStateOf(false) }
                        Box(
                            Modifier
                                .padding(start = 6.dp)
                                .size(34.dp)
                                .clip(androidx.compose.foundation.shape.CircleShape)
                                .border(if (ticked) 2.dp else 0.dp,
                                        if (ticked) Color.White else Color.Transparent,
                                        androidx.compose.foundation.shape.CircleShape)
                                .onFocusChanged { ticked = it.isFocused }
                                .clickable(
                                    interactionSource = remember { MutableInteractionSource() },
                                    indication = null) {
                                    onVerify(showing, !showing.confirmed)
                                },
                            contentAlignment = Alignment.Center,
                        ) {
                            Text("✓",
                                 color = if (showing.confirmed) Verified else Skin.Dim,
                                 fontSize = 16.sp)
                        }
                    }
                }
                // what those words cost: text is drawn over the picture, a picture
                // subtitle has to be burned into it by the server
                Row(Modifier.padding(top = 6.dp)) {
                    Key("text", TextKind)
                    Key("burned in", BurnKind)
                    Key("in the film", null, glyph = "💿")
                    Key("✓ verified", Verified)
                }
            }
        },
        // Timing sits with Close and Download rather than under the heading: it is
        // something done to what is on screen, not part of choosing a file.
        //
        // All of it is one wrapping row, Close included. The dialog lays its two
        // button slots out side by side and gives neither of them a line of its own,
        // so a confirm side wide enough to wrap ended up printed over the top of
        // Close. One row that wraps has nothing to collide with.
        confirmButton = {
            // Three rows, so nothing moves when the middle one appears: the timing
            // controls and the sync button together, what the measurement decided
            // under them when there is anything to say, and the two plain buttons
            // last.
            Column(Modifier.fillMaxWidth(),
                   verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (timing) androidx.compose.foundation.layout.FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    // On a file the server has already put right, this number is
                    // added on top of that - and what is already there is worth
                    // naming, because a static correction and a drift are corrected
                    // in different ways and only one of them this control can undo.
                    Text(if (mended || inForce.isNotEmpty()) "Timing offset"
                         else "Timing",
                         color = Skin.Dim, fontSize = 13.sp,
                         modifier = Modifier
                             .align(Alignment.CenterVertically)
                             .padding(end = 6.dp))
                    // Tenths, and hold to run: a subtitle two seconds out is one
                    // press held for a moment. The whole-second buttons that used to
                    // sit either side said the same thing twice.
                    HoldPill("−", dim = frozen.isNotEmpty()) { onNudge(-0.1f) }
                    Pill(String.format(java.util.Locale.US, "%+.1fs", nudge),
                         active = nudge != 0f,
                         dim = frozen.isNotEmpty()) { onNudge(-nudge) }
                    HoldPill("+", dim = frozen.isNotEmpty()) { onNudge(0.1f) }
                    Spacer(Modifier.width(4.dp))
                    // The film's own sound says where the dialogue is; the subtitle is
                    // placed against it. The button does it now, the tick does it for
                    // every subtitle nobody has corrected yet.
                    if (syncing) {
                        // the library's own mark beside the wait, as the browser does
                        Row(verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .align(Alignment.CenterVertically)
                                .padding(horizontal = 8.dp)) {
                            Text("P", color = Skin.Accent, fontSize = 16.sp,
                                 fontFamily = androidx.compose.ui.text.font.FontFamily.Serif,
                                 fontWeight = FontWeight.SemiBold)
                            Text("Analysing…", color = Skin.Dim, fontSize = 13.sp,
                                 modifier = Modifier.padding(start = 6.dp))
                        }
                        // the way to stop it, in the panel it was started from
                        Pill("Stop") { onReset() }
                    } else {
                        Pill("Sync to film", filled = true,
                             dim = frozen.isNotEmpty()) { onSync() }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier
                            .align(Alignment.CenterVertically)
                            .clickable { onAutoSync(!autoSync) }
                            .padding(start = 2.dp)) {
                        // Material gives a checkbox a 48dp touch target, which is
                        // taller than the pills beside it and lifts the whole row off
                        // the line. It is drawn at the size it looks.
                        androidx.compose.material3.Checkbox(
                            checked = autoSync, onCheckedChange = { onAutoSync(it) },
                            modifier = Modifier.size(22.dp).padding(end = 6.dp),
                            colors = androidx.compose.material3.CheckboxDefaults.colors(
                                checkedColor = Skin.Accent, uncheckedColor = Skin.Dim))
                        // never cut in half, never wrapped: it is one word and it
                        // belongs beside the tick it labels. The same word the
                        // browser uses for the same tick.
                        Text("auto", color = Skin.Dim, fontSize = 13.sp,
                             maxLines = 1, softWrap = false)
                    }
                }
                if (timing && frozen.isNotEmpty()) {
                    Text(frozen, color = Skin.Dim, fontSize = 12.5.sp,
                         modifier = Modifier.padding(top = 6.dp, bottom = 2.dp))
                }
                if (timing && frozen.isEmpty() && syncNote.isEmpty() && !syncing &&
                    (nudge != 0f || mended)) {
                    // something is being applied even though no measurement has run
                    // this sitting: a correction from another day, or a nudge by hand
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("In force: " +
                             (if (inForce.isNotEmpty()) inForce + " from the server"
                              else if (mended) "a correction from the server"
                              else String.format(java.util.Locale.US, "%+.1fs", nudge)),
                             color = Skin.Accent, fontSize = 13.sp)
                        Spacer(Modifier.width(10.dp))
                        Pill("Reset") { onReset() }
                    }
                }
                if (timing && syncNote.isNotEmpty()) {
                    // What is being applied - one number, a rate, or a number for each
                    // part of the film - with the way back to the file's own timing
                    // beside it, since that is the thing it undoes.
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("In force: " + syncNote, color = Skin.Accent,
                             fontSize = 13.sp)
                        if (syncNote != "none") {
                            Spacer(Modifier.width(10.dp))
                            Pill("Reset") { onReset() }
                        }
                    }
                }
                // A subtitle being written for this film: it is not a track yet -
                // the first lines are a minute away - but it is what was chosen, and
                // saying so is the difference between waiting and wondering.
                var writing by remember { mutableStateOf<Api.Making?>(null) }
                LaunchedEffect(media.ratingKey) {
                    while (true) {
                        val said = Api.makingSubtitles(media)
                        writing = if (said.on && (said.key.isEmpty() ||
                                                  said.key == media.ratingKey)) said
                                  else null
                        kotlinx.coroutines.delay(if (said.on) 3000 else 8000)
                    }
                }
                writing?.let { said ->
                    // the first stretch is ten minutes of film: enough to start on,
                    // and the rest arrives while it plays
                    val ready = media.textSubs().any {
                        it.index < 0 && it.label.contains("ai-gen", true)
                    }
                    Row(Modifier.fillMaxWidth().padding(bottom = 8.dp)
                            .background(Skin.Accent.copy(alpha = 0.22f),
                                        RoundedCornerShape(8.dp))
                            .padding(horizontal = 10.dp, vertical = 8.dp)) {
                        Text(if (ready)
                                 "Written from the sound  ·  ready to play  ·  " +
                                 (said.at * 100).toInt() + "% done, the rest arrives " +
                                 "while it plays"
                             else
                                 "Being written from the sound  ·  " +
                                 (said.at * 100).toInt() + "%  ·  " + said.what,
                             color = Skin.Fg, fontSize = 12.5.sp)
                    }
                }
                // Download and what it does by itself on the left, the way out at
                // the far right: Close is not one of the things done here, and sitting
                // beside them it read as one.
                Row(Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically) {
                    Pill("Download…", primary = true) { onDownload() }
                    Row(verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier
                            .clickable { onFetchAhead(!fetchAhead) }
                            .padding(start = 2.dp, end = 8.dp)) {
                        androidx.compose.material3.Checkbox(
                            checked = fetchAhead,
                            onCheckedChange = { onFetchAhead(it) },
                            modifier = Modifier.size(22.dp).padding(end = 6.dp),
                            colors = androidx.compose.material3.CheckboxDefaults.colors(
                                checkedColor = Skin.Accent, uncheckedColor = Skin.Dim))
                        Text("auto", color = Skin.Dim, fontSize = 13.sp,
                             maxLines = 1, softWrap = false)
                    }
                    Spacer(Modifier.weight(1f))
                    Pill("Close") { onClose() }
                }
            }
        },
    )
}

/**
 * A step button that keeps stepping while it is held.
 *
 * Touch and remote both: a finger held on it repeats, and so does the centre key of a
 * remote, which Android repeats for us. It starts slowly - one step, then a pause long
 * enough that a single press is a single step - and speeds up to twenty a second, so a
 * subtitle four seconds out is a press held for two.
 */
@Composable
private fun HoldPill(label: String, dim: Boolean = false, onStep: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Box(
        Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(if (focused) Skin.Panel2 else Color.Transparent)
            .border(if (focused) 2.dp else 0.dp,
                    if (focused) Color.White else Color.Transparent,
                    RoundedCornerShape(999.dp))
            .onFocusChanged { focused = it.isFocused }
            .then(if (dim) Modifier.alpha(0.45f) else Modifier)
            .focusable()
            .onKeyEvent { event ->
                if (dim) return@onKeyEvent false
                val native = event.nativeKeyEvent
                val centre = native.keyCode == android.view.KeyEvent.KEYCODE_DPAD_CENTER ||
                    native.keyCode == android.view.KeyEvent.KEYCODE_ENTER
                if (centre && native.action == android.view.KeyEvent.ACTION_DOWN) {
                    onStep()
                    true
                } else {
                    false
                }
            }
            .pointerInput(Unit) {
                detectTapGestures(onPress = {
                    onStep()
                    val runner = scope.launch {
                        var wait = 450L
                        kotlinx.coroutines.delay(wait)
                        while (true) {
                            onStep()
                            wait = (wait * 3 / 4).coerceAtLeast(50L)
                            kotlinx.coroutines.delay(wait)
                        }
                    }
                    tryAwaitRelease()
                    runner.cancel()
                })
            }
            .padding(horizontal = 18.dp, vertical = 9.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = Skin.Fg, fontSize = 13.5.sp)
    }
}

/** One entry in the key: a coloured bar and what it means. */
@Composable
private fun Key(what: String, colour: Color?, glyph: String? = null) {
    Row(verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.padding(end = 12.dp)) {
        if (glyph != null) Text(glyph, fontSize = 11.sp)
        else Box(Modifier.width(3.dp).height(11.dp).background(colour ?: Skin.Dim))
        Text(what, color = Skin.Dim, fontSize = 11.sp,
             modifier = Modifier.padding(start = 5.dp))
    }
}
