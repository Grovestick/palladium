package se.palladium.tv

import android.content.Context
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.animation.Crossfade
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.ShaderBrush
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** What the Start tab is showing: films or series, from when, the mood gone to, and Reload's
 *  round. Kept while the app runs, so coming back to the tab finds the same page. */
object StartChoice {
    val kind = mutableStateOf("movie")
    val vibe = mutableStateOf("")
    val round = mutableStateOf(0)
    /** "fresh" - this year and last - "classic" - everything before - or "" for any */
    val era = mutableStateOf("")
    /** bumped when the viewer has told it something, so the page is asked again */
    val told = mutableStateOf(0)
    /** the card being held, for More like this or Don't recommend this */
    val held = mutableStateOf<Media?>(null)
    /** bumped when the Start tab is pressed again: the page and every row back to the start */
    val top = mutableStateOf(0)
    /** the row and card last pressed: coming back to Start lands there, not at the foot */
    var cameFrom: Pair<String, String>? = null
    /** that card, still to be gone back to: set as Start is drawn, cleared once it has focus */
    val restore = mutableStateOf<Pair<String, String>?>(null)

    /** A row that never scrolls: every card of it is on the screen already. */
    @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
    val still = object : androidx.compose.foundation.gestures.BringIntoViewSpec {
        override fun calculateScrollDistance(offset: Float, size: Float,
                                             containerSize: Float): Float = 0f
    }

    /** Inside a row the remote moves and the row scrolls only when the next card is off
     *  the screen: a row sliding along at every press was a page in motion all the time. */
    @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
    val inARow = object : androidx.compose.foundation.gestures.BringIntoViewSpec {
        // when a row does move, as long as the home screen's: a spring, since the scroll
        // restarts its spec every frame and a tween restarted every frame never arrives
        override val scrollAnimationSpec: androidx.compose.animation.core.AnimationSpec<Float> =
Slide.along
        override fun calculateScrollDistance(offset: Float, size: Float,
                                             containerSize: Float): Float {
            val room = size * 0.04f
            val start = offset - room
            val end = offset + size + room
            if (start >= 0f && end <= containerSize) return 0f
            return if (start < 0f) start else end - containerSize
        }
    }
}

/**
 * The Start tab, laid out as the streaming services lay theirs: a wide picture across the
 * top that turns through this week's pick and each mood's best, a bar of chips - films or
 * series, fresh or classic, the moods - and a row for every mood, one line each, paged with
 * an arrow at either side. Each viewer's taste leans the choices; a card held says more like
 * this, or not this again.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StartScreen(onOpen: (Media) -> Unit, onMore: (Api.StartMore) -> Unit = {}) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var front by remember { mutableStateOf<Api.StartFront?>(null) }
    val kind by StartChoice.kind
    val round by StartChoice.round
    val era by StartChoice.era
    val told by StartChoice.told
    LaunchedEffect(round, told) {
        front = Api.startFront(round)
    }
    // the rows the server's plugins add - My channels - at the foot of the page
    var plugged by remember { mutableStateOf<List<Api.StartShelf>>(emptyList()) }
    LaunchedEffect(round, told) {
        val named = Api.pluginRows.ifEmpty {
            runCatching { Api.pluginRowsNow(ctx) }.getOrDefault(emptyList()) }
        plugged = named.mapNotNull { (title, path) ->
            val list = runCatching { Api.pluginRow(ctx, path) }.getOrDefault(emptyList())
            if (list.isEmpty()) null
            else Api.StartShelf("plugin-" + path, title, "", "", list,
                                Api.StartMore("", "", "", "", name = title, live = path))
        }
    }
    val list = rememberLazyListState()
    // the Start tab pressed while on it: back to the top, and nothing to go back to
    val top by StartChoice.top
    LaunchedEffect(top) {
        if (top > 0) { StartChoice.cameFrom = null; runCatching { list.scrollToItem(0) } }
    }
    LaunchedEffect(Unit) { StartChoice.restore.value = StartChoice.cameFrom }
    val play: (Media) -> Unit = { m -> startPlay(ctx, m, onOpen) }
    val rows = front?.rows.orEmpty()
    // the hero: this week's pick first, then each mood's best
    val slides = remember(front) {
        val out = ArrayList<Triple<Media, String, String>>()
        front?.pick?.let { out.add(Triple(it, "THIS WEEK'S PICK", front?.note?.ifEmpty { it.summary } ?: it.summary)) }
        // the picture across the top is the house's front: not the shuffles, not the kids'
        rows.filter { it.id != "shuffle" && it.id != "kids-movie" && it.id != "music" }.forEach { r ->
            r.list.firstOrNull()?.let { m ->
                if (out.size < 7 && out.none { it.first.ratingKey == m.ratingKey })
                    out.add(Triple(m, r.name.uppercase(), m.summary))   // what it is about, never why it was chosen
            }
        }
        out
    }
    // a card held: more like it, or not it again
    StartChoice.held.value?.let { m ->
        AlertDialog(
            onDismissRequest = { StartChoice.held.value = null },
            containerColor = Skin.Panel,
            title = { Text(m.title, color = Skin.Fg) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Pill("✓ More like this", primary = true) {
                        StartChoice.held.value = null
                        scope.launch { if (Api.startTell(m.ratingKey, "more")) StartChoice.told.value++ }
                    }
                    Pill("✗ Don't recommend this") {
                        StartChoice.held.value = null
                        scope.launch { if (Api.startTell(m.ratingKey, "not")) StartChoice.told.value++ }
                    }
                }
            },
            confirmButton = { Pill("Close") { StartChoice.held.value = null } })
    }
    // The page holds still while what has the remote is already on screen. A television
    // otherwise lifts whatever is focused to a third of the way down, so the remote
    // landing on the picture's Play scrolled the picture up under the tabs; it moves
    // now only once the remote goes down to a row that is not in view.
    @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
    val steady = remember {
        object : androidx.compose.foundation.gestures.BringIntoViewSpec {
            var traced = 0L
            var offPictureTill = 0L
            // As long as the home screen's slide, measured off a recording of it: fast from
            // the first frame and settling. The page that drifted for good was a scroll
            // rule fighting itself inside the rows, not the length of the slide.
            // A spring: the scroll restarts its spec from where it is every frame, and a
            // tween restarted every frame eased out for seconds.
            override val scrollAnimationSpec: androidx.compose.animation.core.AnimationSpec<Float> =
Slide.down
            override fun calculateScrollDistance(offset: Float, size: Float,
                                                 containerSize: Float): Float {
                // A card in a row: still while its row is on screen, one row on when
                // the remote goes below the lower one, under the tabs going up or
                // coming down off the picture
                // off the picture: while a quarter of the screen is still picture, and
                // for the rest of that slide. Any of it on screen counted the picture's
                // last few points under the first row, and down from that row slid the
                // second towards the top, then stopped it 30 points in.
                val hero = list.layoutInfo.visibleItemsInfo.firstOrNull { it.key == "hero" }
                val now = System.currentTimeMillis()
                if (hero != null && hero.offset + hero.size > containerSize * 0.25f)
                    offPictureTill = now + 500
                val fromTop = now < offPictureTill
                Route.startRowMove(offset, size, containerSize, fromTop)?.let { move ->
                    // what it measured, to the server's log, when it moves the page: the
                    // second row's last points are read from this, not from a screenshot
                    if (move != 0f && now - traced > 400) {
                        traced = now
                        val line = "start move: offset %.0f size %.0f room %.0f top %s -> %.0f"
                            .format(offset, size, containerSize, fromTop, move)
                        scope.launch { runCatching { Api.trace(line) } }
                    }
                    return move
                }
                // room for the ring a focused card grows into
                val room = size * 0.04f
                val top = offset - room
                val bottom = offset + size + room
                if (top >= 0f && bottom <= containerSize) return 0f
                // taller than the screen: its top, not its middle
                if (size + 2 * room > containerSize) return top
                // and when it has to move, a row comes up clear of the bottom edge with
                // the start of the next one under it, rather than flush against it
                val clear = containerSize * 0.14f
                return if (top < 0f) top - clear
                       else minOf(bottom - containerSize + clear, offset - room)
            }
        }
    }
    @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
    androidx.compose.runtime.CompositionLocalProvider(
        androidx.compose.foundation.gestures.LocalBringIntoViewSpec provides steady) {
    // Back on Start: the row the card was pressed in, scrolled to - the row then puts the
    // remote on the card. With none, the top: a card that took the focus while the rows
    // arrived had scrolled the page to its foot.
    val back by StartChoice.restore
    LaunchedEffect(front, plugged, back) {
        if (front == null) return@LaunchedEffect
        val keys = buildList {
            if (slides.isNotEmpty()) add("hero")
            rows.forEach { r ->
                if (r.id != "shuffle") add("row-" + r.id)
                else if (!front?.shuffles.isNullOrEmpty()) add("shuffle")
            }
            plugged.forEach { add("row-" + it.id) }
        }
        val row = back?.first
        val at = if (row == null) -1 else keys.indexOf(if (row == "shuffle") row else "row-$row")
        if (at >= 0) runCatching { list.scrollToItem(at) }
        else if (StartChoice.cameFrom == null) runCatching { list.scrollToItem(0) }
    }
    LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = PaddingValues(bottom = 40.dp),
               verticalArrangement = Arrangement.spacedBy(if (onTv()) 6.dp else 22.dp)) {
        // the remote on the picture: the page stays at its top, under the tabs; it moves
        // only once the remote goes down into the rows
        if (slides.isNotEmpty()) item(key = "hero", contentType = "hero") {
            StartCarousel(slides, onOpen, play, onFocus = { scope.launch { list.scrollToItem(0) } })
        }
        // in the order the viewer keeps them; the series collections' row starts a shuffle
        rows.forEach { r ->
            if (r.id == "shuffle") {
                front?.shuffles?.takeIf { it.isNotEmpty() }?.let { sh ->
                    item(key = "shuffle", contentType = "shuffle") { StartShuffleRow(sh, onOpen) }
                }
            } else item(key = "row-" + r.id, contentType = "row-" + r.id) {
                // a type of its own per row, as Home's shelves have: a row's slot
                // reused for another released a focused card twice and the app closed
                StartShelfRow(r, onOpen, play, onMore)
            }
        }
        // under the rest, once the rest is there: answering first, it stood alone at the top
        if (front != null) plugged.forEach { r ->
            item(key = "row-" + r.id, contentType = "row-" + r.id) {
                StartShelfRow(r, onOpen, onOpen, onMore)
            }
        }
        if (front != null && rows.isEmpty()) item(key = "none", contentType = "none") {
            Text("Nothing here in that mix yet", color = Skin.Dim, fontSize = 15.sp)
        }
    }
    }
}

/** A choice as a word: white and bold with a line under it when on, dim when not; a
 *  ring while the remote rests on it. */
@Composable
private fun StartTextTab(label: String, on: Boolean, onPick: () -> Unit) {
    var lit by remember { mutableStateOf(false) }
    val ring = lit && focusShows()
    Column(Modifier.clip(RoundedCornerShape(Skin.round(6.dp)))
               .border(2.dp, if (ring) Skin.Accent else Color.Transparent,
                       RoundedCornerShape(Skin.round(6.dp)))
               .onFocusChanged { lit = it.isFocused || it.hasFocus }
               .focusable()
               .clickable { onPick() }
               .padding(horizontal = 6.dp, vertical = 4.dp),
           horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, color = if (on || ring) Color.White else Skin.Dim, fontSize = 17.sp,
             fontWeight = if (on) FontWeight.Bold else FontWeight.Medium)
        Box(Modifier.padding(top = 3.dp).width(if (on) 22.dp else 0.dp).height(3.dp)
                .clip(CircleShape).background(Skin.Accent))
    }
}

/** The Shuffle row: the viewer's series collections, each a card that draws from its hat
 *  and plays - or, where the draw has to come from a pack, opens its page. */
@Composable
private fun StartShuffleRow(shelves: List<Triple<String, String, String>>,
                            onOpen: (Media) -> Unit) {
    val ctx = LocalContext.current
    val cardW = Route.startCardW(LocalConfiguration.current.screenWidthDp, onTv())
    Column(verticalArrangement = Arrangement.spacedBy(if (onTv()) 4.dp else 8.dp)) {
        // heading and tagline on one baseline
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Shuffle", color = Skin.Fg, fontSize = 14.sp, fontWeight = FontWeight.Bold,
                 modifier = Modifier.alignByBaseline())
            Text("Your series collections, a random episode each time", color = Skin.Dim,
                 fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                 modifier = Modifier.alignByBaseline())
        }
        @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
        androidx.compose.runtime.CompositionLocalProvider(
            androidx.compose.foundation.gestures.LocalBringIntoViewSpec provides StartChoice.inARow) {
        androidx.compose.foundation.lazy.LazyRow(
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            // on a phone the first card's edge under the row's name; a television keeps
            // room either side for the ring a focused card grows into
            contentPadding = PaddingValues(horizontal = if (onTv()) 8.dp else 0.dp,
                                           vertical = if (onTv()) 6.dp else 8.dp)) {
            items(shelves.size, key = { "sh-" + shelves[it].first }, contentType = { "sh-" + shelves[it].first }) { i ->
                val (id, name, art) = shelves[i]
                val req = remember { FocusRequester() }
                LaunchedEffect(StartChoice.restore.value) {
                    if (StartChoice.restore.value == ("shuffle" to id)) {
                        kotlinx.coroutines.delay(80)
                        runCatching { req.requestFocus() }
                        StartChoice.restore.value = null
                    }
                }
                var lit by remember { mutableStateOf(false) }
                val ring = lit && focusShows()
                val px = with(androidx.compose.ui.platform.LocalDensity.current) {
                    cardW.dp.roundToPx() }
                Column(Modifier.width(cardW.dp)
                        .focusRequester(req)
                        .onFocusChanged { lit = it.isFocused || it.hasFocus }
                        .focusable()
                        .clickable {
                            StartChoice.cameFrom = "shuffle" to id
                            startShuffle(ctx, "coll:" + id, onOpen)
                        },
                       verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f)
                            .scale(if (ring) 1.06f else 1f)
                            .clip(RoundedCornerShape(Skin.round(6.dp)))
                            .then(if (ring) Modifier.border(2.dp, Skin.Accent,
                                                            RoundedCornerShape(Skin.round(6.dp)))
                                  else Modifier)) {
                        Art(Api.pathUrl(art, px), name, Modifier.fillMaxSize(), mark = 0)
                        Text("⇄ Shuffle", color = Color.White, fontSize = 11.sp,
                             fontWeight = FontWeight.Bold,
                             modifier = Modifier.align(Alignment.TopStart).padding(6.dp)
                                 .background(Color(0xB805070A), RoundedCornerShape(10.dp))
                                 .padding(horizontal = 8.dp, vertical = 2.dp))
                    }
                    Text(name, color = if (ring) Skin.Fg else Skin.Dim, fontSize = 13.sp,
                         fontWeight = FontWeight.SemiBold, maxLines = 1,
                         overflow = TextOverflow.Ellipsis,
                         modifier = Modifier.padding(horizontal = 2.dp))
                }
            }
        }
        }
    }
}

/** A shuffle started from a page - a series' "series:<id>" - drawn, then played as a
 *  shelf's Play plays it, the shuffle named so Next draws from it again. */
fun startShuffle(ctx: Context, shelf: String, onOpen: (Media) -> Unit, at: String = "") {
    (ctx as AppCompatActivity).lifecycleScope.launch {
        val drew = runCatching { Api.shelfDraw(shelf, null, true, at = at) }.getOrNull()
        if (drew == null) {
            android.widget.Toast.makeText(ctx, "Nothing to play on that shelf",
                                          android.widget.Toast.LENGTH_SHORT).show()
            return@launch
        }
        val full = (runCatching { Api.metadata(drew.media) }.getOrNull() ?: drew.media)
        if (drew.media.offered) {
            onOpen(full)
            return@launch
        }
        val here = Api.atHome(ctx, full) ?: full
        ctx.startActivity(playIntent(ctx, here, drew.resumeAt, here.startSub(), fresh = true)
            .putExtra("casual", true)
            .putExtra("shelf", shelf))
    }
}

/** A film here plays; one in a pack, or a series, opens its page. */
private fun startPlay(ctx: Context, m: Media, onOpen: (Media) -> Unit) {
    if (m.offered || m.type != "movie") { onOpen(m); return }
    (ctx as AppCompatActivity).lifecycleScope.launch {
        val full = (runCatching { Api.metadata(m) }.getOrNull() ?: m)
            .let { Api.atHome(ctx, it) ?: it }
        ctx.startActivity(playIntent(ctx, full, full.viewOffsetMs, full.startSub(), fresh = true))
    }
}

/** The wide picture across the top, turning through its slides every eight seconds, with
 *  an arrow at each side and dots under it. */
@Composable
private fun StartCarousel(slides: List<Triple<Media, String, String>>, onOpen: (Media) -> Unit,
                          play: (Media) -> Unit, onFocus: () -> Unit = {}) {
    var at by remember(slides) { mutableStateOf(0) }
    // Held still while the remote is on it: turning swapped the slide under the focused
    // button, the focus fell to the next row down and the page scrolled to it.
    var held by remember { mutableStateOf(false) }
    LaunchedEffect(slides, at, held) {
        if (slides.size < 2 || held) return@LaunchedEffect
        delay(8000)
        at = (at + 1) % slides.size
    }
    val tall = LocalConfiguration.current.screenHeightDp
    val wide = LocalConfiguration.current.screenWidthDp >= 600
    // a remote steps with the arrows; a finger swipes the picture itself
    val tv = onTv()
    var swiped by remember { mutableStateOf(0f) }
    Box(Modifier.fillMaxWidth().height((tall * if (wide) 0.6f else 0.62f).coerceIn(300f, 620f).dp)
            .onFocusChanged {
                held = tv && it.hasFocus
                if (it.hasFocus) onFocus()
            }
            .clip(RoundedCornerShape(Skin.round(12.dp)))
            .then(if (tv || slides.size < 2) Modifier else Modifier.pointerInput(slides) {
                detectHorizontalDragGestures(
                    onDragStart = { swiped = 0f },
                    onDragEnd = {
                        if (swiped < -60f) at = (at + 1) % slides.size
                        else if (swiped > 60f) at = (at - 1 + slides.size) % slides.size
                        swiped = 0f
                    },
                    onHorizontalDrag = { change, dx -> change.consume(); swiped += dx })
            })) {
        // the slide's shape, for the picture the server cuts and shades to fit it
        val heroW = LocalConfiguration.current.screenWidthDp - 32
        val heroH = (tall * if (wide) 0.6f else 0.62f).coerceIn(300f, 620f)
        Crossfade(targetState = at, label = "hero") { n ->
            val (m, eyebrow, text) = slides[n.coerceIn(0, slides.size - 1)]
            StartSlide(m, eyebrow, text, wide, onOpen, play, aspect = heroW / heroH)
        }
        if (slides.size > 1) {
            if (tv) {
                // on an arrow, the remote's own left or right turns the picture too,
                // as pressing it does: there is nothing further that way to go to
                Box(Modifier.align(Alignment.CenterStart).padding(start = 6.dp)
                        .onPreviewKeyEvent {
                            if (it.type == androidx.compose.ui.input.key.KeyEventType.KeyDown &&
                                it.key == androidx.compose.ui.input.key.Key.DirectionLeft) {
                                at = (at - 1 + slides.size) % slides.size; true
                            } else false
                        }) {
                    Pill("‹", small = true) { at = (at - 1 + slides.size) % slides.size }
                }
                Box(Modifier.align(Alignment.CenterEnd).padding(end = 6.dp)
                        .onPreviewKeyEvent {
                            if (it.type == androidx.compose.ui.input.key.KeyEventType.KeyDown &&
                                it.key == androidx.compose.ui.input.key.Key.DirectionRight) {
                                at = (at + 1) % slides.size; true
                            } else false
                        }) {
                    Pill("›", small = true) { at = (at + 1) % slides.size }
                }
            }
            Row(Modifier.align(Alignment.BottomEnd).padding(end = 18.dp, bottom = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                slides.indices.forEach { i ->
                    Box(Modifier.width(if (i == at) 20.dp else 8.dp).height(8.dp)
                            .clip(CircleShape)
                            .background(if (i == at) Color.White else Color(0x59FFFFFF)))
                }
            }
        }
    }
}

/** One slide: the backdrop wide, what it is, why, and its buttons. */
@Composable
private fun StartSlide(m: Media, eyebrow: String, text: String, wide: Boolean,
                       onOpen: (Media) -> Unit, play: (Media) -> Unit, aspect: Float = 0f) {
    // held, More like this or Don't recommend this - as a card in a row
    Box(Modifier.fillMaxSize().pointerInput(m.ratingKey) {
            detectTapGestures(
                onLongPress = { StartChoice.held.value = m })
        }) {
        // the shade comes painted into the picture, cut to this slide's shape: one picture
        // to draw, where a picture and a shade over it cost the television's graphics
        // more than a frame's time
        Art(Api.artUrl(m, 1280, m.widePicture())?.let {
                it + (if (it.contains("?")) "&" else "?") + "shade=hero&a=" + "%.2f".format(aspect)
            }, m.title,
            Modifier.fillMaxSize(), mark = 0)

        Column(Modifier.align(Alignment.BottomStart).widthIn(max = 560.dp)
                   .padding(start = if (wide) 40.dp else 18.dp, end = 18.dp, bottom = 36.dp),
               verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Text(eyebrow, color = Skin.Accent, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                 letterSpacing = 1.6.sp)
            // a title over two lines leaves room for two of the summary, not three: with
            // three the block outgrew the picture and the buttons under it were squeezed
            var titleLines by remember(m.ratingKey) { mutableStateOf(1) }
            Text(m.title, color = Color.White, fontSize = if (wide) 46.sp else 32.sp,
                 lineHeight = if (wide) 48.sp else 34.sp, fontWeight = FontWeight.ExtraBold,
                 maxLines = 2, overflow = TextOverflow.Ellipsis,
                 onTextLayout = { titleLines = it.lineCount })
            val facts = listOfNotNull(if (m.rating > 0) "★ " + "%.1f".format(m.rating) else null,
                                      m.year?.toString(),
                                      m.genres.take(3).joinToString(" · ").ifEmpty { null })
            Text(facts.joinToString("    "), color = Color(0xFFC9D2DC), fontSize = 14.sp,
                 fontWeight = FontWeight.Medium)
            // always the room of its most lines: a summary of two stood the title a line
            // lower than one of three, so the words moved from slide to slide
            if (text.isNotEmpty()) Text(text, color = Color(0xFFD8DFE7), fontSize = 15.sp,
                                        lineHeight = 21.sp,
                                        maxLines = if (titleLines > 1) 2 else 3,
                                        minLines = if (titleLines > 1) 2 else 3,
                                        overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(2.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                val act = if (m.actWord.isNotEmpty()) m.actWord to m.actPlays
                          else Route.heroAct(m.offered, m.upcoming, m.askable || m.addable, m.type, m.released)
                Pill(act.first, primary = true) { if (act.second) play(m) else onOpen(m) }
                Pill("More info") { onOpen(m) }
            }
        }
    }
}

/** One mood's row: its name and line, then one line of wide cards moving sideways as
 *  Home's rows do - under the remote, a card at a time. */
@Composable
private fun StartShelfRow(r: Api.StartShelf, onOpen: (Media) -> Unit, play: (Media) -> Unit,
                          onMore: (Api.StartMore) -> Unit) {
    val cardW = Route.startCardW(LocalConfiguration.current.screenWidthDp, onTv())
    val ctx = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(if (onTv()) 4.dp else 8.dp)) {
        // heading and tagline on one baseline - and on a phone or tablet, pressed, the
        // row's Show more; not on a television, where the remote would stop on it
        val more = r.more
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = if (more != null && !onTv()) Modifier.clickable { onMore(more) }
                       else Modifier) {
            Text(r.name, color = Skin.Fg, fontSize = 14.sp, fontWeight = FontWeight.Bold,
                 modifier = Modifier.alignByBaseline())
            Text(r.tagline, color = Skin.Dim, fontSize = 12.sp, maxLines = 1,
                 overflow = TextOverflow.Ellipsis, modifier = Modifier.alignByBaseline())
        }
        val rowState = androidx.compose.foundation.lazy.rememberLazyListState()
        val reqs = remember { HashMap<String, FocusRequester>() }
        val back by StartChoice.restore
        val tvNow = onTv()
        LaunchedEffect(back, r.list) {
            val (row, key) = back ?: return@LaunchedEffect
            if (row != r.id) return@LaunchedEffect
            val at = r.list.indexOfFirst { it.ratingKey == key }
            if (at >= 0) {
                runCatching { rowState.scrollToItem(at) }
                kotlinx.coroutines.delay(80)
                if (Route.focusByCode(tvNow)) runCatching { reqs[key]?.requestFocus() }
            }
            StartChoice.restore.value = null
        }
        // what was pressed, and where, for coming back to it
        val mark: (Media) -> Unit = { m -> StartChoice.cameFrom = r.id to m.ratingKey }
        val top by StartChoice.top
        LaunchedEffect(top) { if (top > 0) runCatching { rowState.scrollToItem(0) } }
        @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
        androidx.compose.runtime.CompositionLocalProvider(
            androidx.compose.foundation.gestures.LocalBringIntoViewSpec provides StartChoice.inARow) {
        androidx.compose.foundation.lazy.LazyRow(
            state = rowState,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            // room for a focused card to grow without being cut at the ends
            // on a phone the first card's edge under the row's name; a television keeps
            // room either side for the ring a focused card grows into
            contentPadding = PaddingValues(horizontal = if (onTv()) 8.dp else 0.dp,
                                           vertical = if (onTv()) 6.dp else 8.dp)) {
            items(r.list.size, key = { r.list[it].ratingKey }, contentType = { r.list[it].ratingKey }) { i ->
                // a music video plays the row as a looping playlist from the one pressed
                val req = reqs.getOrPut(r.list[i].ratingKey) { FocusRequester() }
                if (r.id == "music") {
                    // a card a playlist: its round, carried on where it was, round and round
                    val go: (Media) -> Unit = { m -> mark(m); startShuffle(ctx, m.ratingKey, onOpen) }
                    StartCard(r.list[i], cardW, go, go, req)
                } else if (r.list[i].live || r.list[i].liveGroup.isNotEmpty()) {
                    // a channel or a group: round, as on Home, as tall as a wide card
                    StartLiveCard(r.list[i], cardW, { m -> mark(m); onOpen(m) }, req)
                } else StartCard(r.list[i], cardW, { m -> mark(m); onOpen(m) },
                                 { m -> mark(m); play(m) }, req)
            }
            // and the row's end: the tab it comes from, its order and category set, for
            // many more of the same
            r.more?.let { more ->
                item(key = "more:" + r.id, contentType = "more") {
                    StartMoreCard(cardW) { onMore(more) }
                }
            }
        }
        }
    }
}

/** A wide card: the backdrop, the title over its foot; focused, a little larger. Pressed it
 *  opens; held, More like this or Don't recommend this. */
/** A channel or a group on Start: round, its name under it. Focused, the whole card is
 *  scrolled into view - the circle alone was, and on the last row the name stayed under
 *  the screen's edge. */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
private fun StartLiveCard(m: Media, cardW: Float, onOpen: (Media) -> Unit,
                          req: FocusRequester = remember { FocusRequester() }) {
    val ask = remember { androidx.compose.foundation.relocation.BringIntoViewRequester() }
    val scope = rememberCoroutineScope()
    Box(Modifier.focusRequester(req).bringIntoViewRequester(ask)
            .onFocusChanged { if (it.hasFocus) scope.launch { ask.bringIntoView() } }) {
        Poster(m, width = (cardW * 9f / 16f).toInt()) { onOpen(m) }
    }
}

/** The end of a Start row: the words in a rectangle round them, standing where the
 *  next picture would, the slot as tall as a card's picture. */
@Composable
private fun StartMoreCard(cardW: Float, onClick: () -> Unit) {
    var lit by remember { mutableStateOf(false) }
    val words = RoundedCornerShape(Skin.round(8.dp))
    Box(Modifier.width((cardW * 0.6f).dp).aspectRatio(0.6f * 16f / 9f)
            .onFocusChanged { lit = it.isFocused }
            .clickable { onClick() },
        contentAlignment = Alignment.Center) {
        Text("Show more  \u203a", color = if (lit) Skin.Fg else Skin.Dim, fontSize = 14.sp,
             modifier = Modifier.clip(words).background(Skin.Panel)
                 .border(if (lit) 3.dp else 0.dp,
                         if (lit) androidx.compose.ui.graphics.Color.White
                         else androidx.compose.ui.graphics.Color.Transparent, words)
                 .padding(horizontal = 14.dp, vertical = 10.dp))
    }
}

@Composable
private fun StartCard(m: Media, cardW: Float, onOpen: (Media) -> Unit, play: (Media) -> Unit,
                      req: FocusRequester = remember { FocusRequester() }) {
    var lit by remember { mutableStateOf(false) }
    val ring = lit && focusShows()
    // the picture asked for at the card's own width, no wider
    val px = with(androidx.compose.ui.platform.LocalDensity.current) { cardW.dp.roundToPx() }
    Column(Modifier.width(cardW.dp)
            .focusRequester(req)
            .onFocusChanged { lit = it.isFocused || it.hasFocus }
            .focusable()
            .combinedClickableCompat(onClick = { onOpen(m) },
                                     onLongClick = { StartChoice.held.value = m }),
           verticalArrangement = Arrangement.spacedBy(6.dp)) {
        // The picture alone, the title under it as the home screen has it: a shade over
        // the picture for words to stand on was one more layer a card, every frame
        Art(Api.artUrl(m, px, m.widePicture()), m.title,
            Modifier.fillMaxWidth().aspectRatio(16f / 9f)
                .scale(if (ring) 1.06f else 1f)
                .clip(RoundedCornerShape(Skin.round(6.dp)))
                .then(if (ring) Modifier.border(2.dp, Skin.Accent,
                                                RoundedCornerShape(Skin.round(6.dp)))
                      else Modifier),
            mark = 0)
        Text(m.title, color = if (ring) Skin.Fg else Skin.Dim, fontSize = 13.sp,
             fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis,
             modifier = Modifier.padding(horizontal = 2.dp))
    }
}

/** Pressed and held, as a card wants both. */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
private fun Modifier.combinedClickableCompat(onClick: () -> Unit, onLongClick: () -> Unit) =
    this.combinedClickable(onClick = onClick, onLongClick = onLongClick)
