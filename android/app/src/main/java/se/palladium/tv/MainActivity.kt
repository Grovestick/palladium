package se.palladium.tv

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.widget.Toast
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.LocalIndication
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.type
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import androidx.mediarouter.app.MediaRouteButton
import coil.compose.AsyncImage
import com.google.android.gms.cast.framework.CastButtonFactory
import com.google.android.gms.cast.framework.CastContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch

/**
 * AppCompatActivity rather than ComponentActivity, and not for the theme: the cast
 * chooser is a fragment dialog, so MediaRouteButton throws
 * "The activity must be a subclass of FragmentActivity" the moment it is pressed.
 */
class MainActivity : AppCompatActivity() {
    companion object {
        /** Bumped whenever the app comes back to the front, so screens can refresh. */
        val returned = androidx.compose.runtime.mutableStateOf(0)

        /**
         * The episode a season page should open standing on.
         *
         * Set by "Go to show" on an episode's own page and cleared the moment the
         * season has used it: opening a season any other way starts at its beginning,
         * which is what somebody browsing wants.
         */
        val reveal = androidx.compose.runtime.mutableStateOf<String?>(null)

        /**
         * The last notice this app has shown, wherever it showed it.
         *
         * The library and the player both wait on the same line, and a message shown
         * over a film should not be waiting again on the shelves afterwards.
         */
        val noticeSeen = androidx.compose.runtime.mutableStateOf(0)

        /**
         * Bumped whenever a watchlist or shuffle mark is changed.
         *
         * The shelves are held while the tab is left and come back as they were, which
         * is right for a scroll position and wrong for a list whose contents have just
         * been changed on the screen in front of it. Counting the changes puts the
         * held list out of date, and it is fetched again on the way back.
         */
        val marksTouched = androidx.compose.runtime.mutableStateOf(0)

        /**
         * Whether the home screen is up with nothing on it.
         *
         * Read from dispatchKeyEvent, which runs whatever the screen is doing - so
         * leaving the app does not depend on a list arriving, a server answering, or
         * a focus request finding something to land on. A shelf that never came back
         * left back with nowhere to go and the app could not be closed at all.
         */
        @Volatile
        @JvmStatic
        var homeBare = false

        /** Whether the first focus of the run has been placed. Until it has, the
         *  server picker refuses it, so the tab is what the remote starts on. */
        //: state, not a plain field: the picker's answer to "may I be focused" is
        //: read while composing, and a field nothing follows never changes it back
        val focusLanded = androidx.compose.runtime.mutableStateOf(false)
    }

    /** When back was last pressed on a bare home screen. */
    private var lastBareBack = 0L

    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean {
        // Nothing on the screen to go back into: twice closes the app, from here
        // rather than from a handler inside the list that may not be there. This is
        // the one key that has to work when nothing else does.
        if (homeBare && event.keyCode == android.view.KeyEvent.KEYCODE_BACK &&
                event.action == android.view.KeyEvent.ACTION_DOWN &&
                event.repeatCount == 0) {
            val now = System.currentTimeMillis()
            if (now - lastBareBack < 2000) {
                finish()
            } else {
                lastBareBack = now
                Toast.makeText(this, "Press back again to exit",
                               Toast.LENGTH_SHORT).show()
            }
            return true
        }
        // Everything still belonging to the press that opened a menu by being held is
        // dropped here: its repeats, and the release at the end of it. The menu is
        // already up and has the focus, and that release is not a press of anything
        // on it - it is the end of the press that asked for it.
        // Only the release. The repeats of a held key have to go on through, because
        // they are what the press below is watching to know a hold from a press - eat
        // them and the menu never opens at all. The release is the one event with
        // nothing left to do: the menu it asked for is already up.
        if (fromTheHold(event) &&
                event.action == android.view.KeyEvent.ACTION_UP) {
            // not a cancelled one: that is sent to the window losing the focus while
            // the key is still down, and the release itself follows it
            if (!event.isCanceled) holdIsOver()
            return true
        }
        return safeKey(event) { super.dispatchKeyEvent(event) }
    }

    /** When the short way was last looked for, so coming back to the app is cheap. */
    private var lastLookedForTheShortWay = 0L

    override fun onResume() {
        super.onResume()
        // the picker steps aside again: coming back to the app is an arrival like any
        // other, and the flag being left true from the last one was why the ring still
        // appeared in the corner on anything but the very first start
        focusLanded.value = false
        returned.value = returned.value + 1
        // A phone that opened this app away from home filed the server under the
        // address the router forwards, and Android resumes a process rather than
        // making a new one - so coming home changed nothing and every poster went out
        // to the internet and back in to a machine three metres away. Looked for again
        // here, because this is the moment the network has usually changed.
        val now = System.currentTimeMillis()
        if (now - lastLookedForTheShortWay > 20_000L) {
            lastLookedForTheShortWay = now
            lifecycleScope.launch(Dispatchers.IO) {
                Api.openTheDoorThatAnswers(this@MainActivity)
                Api.fileThemWhereTheyAnswer(this@MainActivity)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Crash.install(applicationContext)      // before anything that might throw
        // themes cover the static case; this covers phones that recolour the bars when
        // the app resumes, and keeps the icons light against our dark ground
        window.statusBarColor = 0xFF0B0D10.toInt()
        window.navigationBarColor = 0xFF0B0D10.toInt()
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }
        // an episode the player started on its own: the page under it should be that
        // one, not the episode it rolled on from
        Opening.key = intent.getStringExtra("openKey") ?: ""
        Api.loadServer(this)
        Api.learnDevice(this)                          // a television wants larger text
        // which language this viewer reads subtitles in, before anything opens one
        lifecycleScope.launch { Api.learnLanguage() }
        // and where to go when this server is off: the machine that keeps copies
        lifecycleScope.launch(Dispatchers.IO) { Api.learnStandby(this@MainActivity) }
        // and the other address this same server answers to, so a screen away from
        // home reaches the main server rather than only the machine that keeps copies
        lifecycleScope.launch(Dispatchers.IO) { Api.learnTheWayIn(this@MainActivity) }
        // and if the address it is filed under does not answer from where this screen
        // is, open it by the one that does
        lifecycleScope.launch(Dispatchers.IO) {
            Api.openTheDoorThatAnswers(this@MainActivity)
            // and every other machine in the list gets the same treatment, so the
            // list says what is actually reachable from where this screen is
            Api.fileThemWhereTheyAnswer(this@MainActivity)
        }
        // how this machine is dressed: a copy after dark, or a card given to a game
        lifecycleScope.launch { Api.learnMood() }
        // and which titles that machine is holding, for the dot on their posters
        lifecycleScope.launch { Api.learnCopies() }
        // and whether this server is the machine that keeps copies, for the line at
        // the top of the shelves
        lifecycleScope.launch { Api.learnWhatThisIs() }
        Updates.skip = Updates.skippedVersion(this)    // asked once, not every launch
        Cast.warmUp(this)                      // discovery needs a nudge and a permission
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Skin.Accent,
                                                        background = Skin.Bg)) {
                CompositionLocalProvider(LocalIndication provides PressOnly) { App() }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        Opening.key = intent.getStringExtra("openKey") ?: ""
    }
}

/** A title the app has been asked to show, from outside the composition. */
object Opening {
    var key by mutableStateOf("")
    // the player already read this episode to start it; sharing it means Back works
    // the instant the episode does, rather than after a round trip
    var media: Media? = null
}

@Composable
private fun App() {
    val ctx = LocalContext.current
    var screen by remember { mutableStateOf(if (Api.base.isEmpty()) "setup" else "home") }
    //: which screen the server list was opened from, so Back leads back to it
    var cameFrom by remember { mutableStateOf("home") }
    val stack = remember { mutableStateListOf<Media>() }
    val browse = remember { Browse() }          // outlives the detail screen

    // One screen is swapped for another here, and whatever had the remote goes out of
    // the composition with it - leaving its parent in the focus tree marked as holding
    // focus with nothing inside it to hold. Every search by direction after that throws
    // "ActiveParent must have a focusedChild"; the guard on the key dispatcher keeps the
    // app alive and drops the press, so down did nothing at all on a title's page - the
    // shelf under it could not be reached by any number of presses. Cleared at the swap,
    // before the screen that follows asks for the focus it wants.
    val focus = androidx.compose.ui.platform.LocalFocusManager.current
    LaunchedEffect(screen, stack.size) { focus.clearFocus(force = true) }

    // An episode started by auto-next: its page replaces the page of the episode it
    // followed, so Back leads to what is playing and then out to the season.
    LaunchedEffect(Opening.key) {
        val key = Opening.key
        if (key.isNotEmpty()) {
            Opening.key = ""
            val handed = Opening.media?.takeIf { it.ratingKey == key }
            Opening.media = null
            (handed ?: runCatching { Api.item(key, null) }.getOrNull())?.let { m ->
                if (stack.isNotEmpty() && stack.last().type == "episode") {
                    stack[stack.lastIndex] = m
                } else {
                    stack.add(m)
                }
                screen = "home"
            }
        }
    }

    // Downloads, live on every screen: every 3 s while something is coming in, 10 s otherwise.
    // It ran inside the home screen's download line, so a film page never saw progress move.
    LaunchedEffect(Unit) {
        while (true) {
            runCatching { Api.refreshDownloading(ctx) }
            // a second while anything is arriving, so the percentage, the rate and
            // the time left move as they happen rather than in steps
            // two seconds, not one: a second poll against a server already
            // transcoding is where the connections that time out come from, and the
            // numbers move no less for being read half as often
            kotlinx.coroutines.delay(if (Api.downloading.value.isEmpty()) 10_000L else 2_000L)
        }
    }

    // Android 15 draws apps edge to edge, so without these the header sits underneath
    // the clock and status icons, and the bottom row under the navigation bar.
    Surface(color = Skin.Bg,
            modifier = Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        when {
            screen == "setup" -> {
                // Once a server is known this screen is optional, so Back has to lead
                // out of it - without a handler it fell through and quit the app.
                // And out means back where it was opened from: reached through
                // Settings, Back belongs to Settings, not to the library.
                val canLeave = Api.base.isNotEmpty()
                if (canLeave) BackHandler { screen = cameFrom }
                SetupScreen(onDone = { screen = cameFrom },
                            onCancel = if (canLeave) ({ screen = cameFrom }) else null)
            }
            screen == "people" -> {
                BackHandler { screen = "home" }
                PeopleScreen(onBack = { screen = "home" })
            }
            screen == "settings" -> {
                BackHandler { screen = "home" }
                SettingsScreen(onBack = { screen = "home" },
                               onServers = { cameFrom = "settings"; screen = "setup" },
                               onPeople = { screen = "people" })
            }
            screen == "reports" -> {
                BackHandler { screen = "home" }
                ReportsScreen(onBack = { screen = "home" })
            }
            stack.isNotEmpty() -> {
                BackHandler { if (stack.isNotEmpty()) stack.removeAt(stack.lastIndex) }
                DetailScreen(stack.last(),
                             onBack = { if (stack.isNotEmpty()) stack.removeAt(stack.lastIndex) },
                             onOpen = { stack.add(it) },
                             // the same words in the tab where they can be worked on:
                             // taken off, added to, a decade put beside them
                             onFilter = { kind, words ->
                                 browse.tab = kind
                                 browse.collectionOn = null
                                 // whatever was last typed is not part of this
                                 // question: left standing it narrowed the words
                                 // to whichever of them had that in the title
                                 browse.query = ""
                                 browse.genre = words.joinToString(",")
                                 browse.decade = ""
                                 browse.loaded = ""
                                 stack.clear()
                             },
                             // A name from the cast: everything this house holds with
                             // them in it, opened as a shelf of its own. Asked of the
                             // library, so what comes back can be watched tonight.
                             onPerson = { who ->
                                 val scope = (ctx as AppCompatActivity).lifecycleScope
                                 scope.launch {
                                     val theirs = runCatching {
                                         Api.withPerson(ctx, who.id, who.name)
                                     }.getOrDefault(emptyList())
                                     if (theirs.isEmpty()) {
                                         android.widget.Toast.makeText(
                                             ctx, "Nothing here with " + who.name + " in it",
                                             android.widget.Toast.LENGTH_SHORT).show()
                                         return@launch
                                     }
                                     browse.moreWas = Filters(
                                         browse.genre, browse.decade, browse.genres,
                                         browse.decades, browse.collSortKey,
                                         browse.collSortAsc)
                                     browse.grid = theirs
                                     browse.moreAll = theirs
                                     browse.genres = theirs.flatMap { it.genres }
                                         .groupingBy { it }.eachCount().toList()
                                         .sortedBy { it.first.lowercase() }
                                     browse.decades = theirs.mapNotNull { one ->
                                         (one.year ?: 0).takeIf { it > 0 }
                                             ?.let { (it / 10 * 10).toString() }
                                     }.groupingBy { it }.eachCount().toList()
                                         .sortedByDescending { it.first }
                                     browse.genre = ""; browse.decade = ""
                                     browse.collSortKey = "originallyAvailableAt"
                                     browse.collSortAsc = false
                                     browse.moreRow = who.name
                                     browse.focusKey = theirs.first().ratingKey
                                     // the page being read, to come back to. The
                                     // shelf needs the title's page off the stack to
                                     // be seen at all, so back had nothing left to
                                     // return to and landed on the front page.
                                     browse.personFrom = stack.lastOrNull()
                                     stack.clear()
                                 }
                             })
            }
            else -> {
                // Back from a title's page. The list underneath is the same list, so
                // an effect watching it does not run again - and the poster somebody
                // came from was never given the focus back, which left the remote on
                // the server picker in the corner.
                LaunchedEffect(Unit) { browse.cameBack = browse.cameBack + 1 }
                // Whether there is anything on this screen to go back into, kept
                // where the key dispatcher can see it. Empty means back closes the
                // app on the second press however wedged the rest of this is.
                androidx.compose.runtime.DisposableEffect(Unit) {
                    onDispose { MainActivity.homeBare = false }
                }
                // One press at the top level is too easy to hit by accident, on a remote
                // especially; the second press within a couple of seconds means it.
                var lastBack by remember { mutableStateOf(0L) }
                BackHandler {
                    val now = System.currentTimeMillis()
                    if (now - lastBack < 2000) {
                        (ctx as? android.app.Activity)?.finish()
                    } else {
                        lastBack = now
                        Toast.makeText(ctx, "Press back again to exit",
                                       Toast.LENGTH_SHORT).show()
                    }
                }
                // the menu's download line, pressed: that film's page
                LaunchedEffect(Api.openWanted.value) {
                    Api.openWanted.value?.let { Api.openWanted.value = null; stack.add(it) }
                }
                HomeScreen(browse, onOpen = { browse.backdrop = it; stack.add(it) },
                           onSettings = { cameFrom = "home"; screen = "setup" },
                           onPeople = { screen = "people" },
                           onPrefs = { screen = "settings" },
                           onReports = { screen = "reports" })
            }
        }
    }
}

/**
 * The servers this app knows: your own, and any friend who sent a link.
 *
 * One box takes both kinds of thing, because from the outside they look the same - an
 * address for a server on this network, or a whole invitation link for one that is not.
 * Which it is decides whether a token comes with it.
 */
@Composable
private fun SetupScreen(onDone: () -> Unit, onCancel: (() -> Unit)? = null) {
    val ctx = LocalContext.current as AppCompatActivity
    var text by remember { mutableStateOf("") }
    var typedToken by remember { mutableStateOf("") }
    // shown only once a server has asked for one: most have no password, and a box
    // on a screen that does not need one is a question nobody can answer
    var password by remember { mutableStateOf("") }
    var wantPassword by remember { mutableStateOf(false) }
    var checking by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var list by remember { mutableStateOf(Servers.folded(Servers.all(ctx))) }
    val first = Servers.all(ctx).isEmpty()

    fun reload() { list = Servers.folded(Servers.all(ctx)) }
    // which server is being forgotten, while the question is on screen
    var forgetting by remember { mutableStateOf<Server?>(null) }
    // which of them answered when last asked, so a row can say so
    var alive by remember { mutableStateOf<Map<String, Boolean>>(emptyMap()) }
    // and whether it lets us read anything from where we are standing. Answering and
    // letting us in are two different questions, and the dot only ever asked the
    // first: a server with no password takes the address for the proof, so from a
    // train it answers every knock and refuses every request behind it.
    var letIn by remember { mutableStateOf<Map<String, Boolean>>(emptyMap()) }
    // and whichever server is open is asked whether it keeps a copy somewhere: the
    // answer arrives while this screen is on, rather than only when the app started
    LaunchedEffect(Unit) {
        kotlinx.coroutines.withContext(Dispatchers.IO) { Api.learnStandby(ctx) }
        list = Servers.folded(Servers.all(ctx))
    }
    // every server in the list, knocked on now and then: the one on the shelf may be
    // off and the cache may be awake, and only asking says which
    LaunchedEffect(list.size) {
        while (true) {
            val said = HashMap<String, Boolean>()
            val open = HashMap<String, Boolean>()
            list.forEach { s ->
                said[s.base] = Api.answering(s.base, s.token) ||
                    (s.outside.isNotEmpty() && Api.answering(s.outside, s.token))
                // and whether it takes us for its owner from where we are standing
                if (said[s.base] == true) {
                    Servers.learnWhose(ctx, s)
                    open[s.base] = Servers.describe(s.base, s.token) != null ||
                        (s.outside.isNotEmpty() &&
                         Servers.describe(s.outside, s.token) != null)
                }
            }
            // folded, as it is drawn. The knocking loop was writing the unfolded list
            // back over it, so every machine known by both its addresses appeared
            // twice a quarter of a minute after the screen opened.
            list = Servers.folded(Servers.all(ctx))
            alive = said
            letIn = open
            kotlinx.coroutines.delay(15_000)
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())
               .padding(horizontal = 28.dp, vertical = 24.dp)) {
        Text("P", color = Skin.Accent, fontSize = 46.sp, fontFamily = FontFamily.Serif,
             fontWeight = FontWeight.SemiBold)
        Text("PALLADIUM", color = Skin.Fg, fontSize = 22.sp, letterSpacing = 4.sp,
             fontWeight = FontWeight.Light, modifier = Modifier.padding(top = 4.dp))
        Text(if (first) "Address of the server on your network, or a link a friend sent you"
             else "Servers",
             color = Skin.Dim, fontSize = 14.sp,
             modifier = Modifier.padding(top = 12.dp, bottom = 14.dp))

        // Two headings, because the two kinds of address behave differently and
        // which one a machine is filed under is exactly what goes wrong: one is
        // reached across this network, the other out through the router and back.
        var said = ""
        list.sortedWith(compareBy({ if (Servers.athome(it.base)) 0 else 1 },
                                  { it.name.ifBlank { Servers.hostOf(it.base) }
                                      .lowercase() })).forEach { srv ->
            val kind = if (Servers.athome(srv.base)) "On this network"
                       else "From outside"
            if (kind != said) {
                said = kind
                Text(kind, color = Skin.Dim, fontSize = 12.sp,
                     letterSpacing = 1.sp,
                     modifier = Modifier.padding(top = 6.dp, bottom = 6.dp))
            }
            // by address, and by either of the machine's addresses: a film that moved
            // to the cache is on the cache, whichever way in it was reached
            val open = listOf(srv.base, srv.outside).map { it.trimEnd('/') }
                .contains(Api.base.trimEnd('/'))
            // The whole row switches, not only the pill on it. Pressing the name of a
            // machine is what somebody does to choose that machine; having to find a
            // button after that reads as the press not having worked.
            // Clipped before anything is drawn into it. The panel was rounded and
            // the press-and-focus drawn over it was not, so the corners squared off
            // the moment the remote landed on a row - and which row the remote was on
            // was left to whatever the system draws, which on a television is nothing
            // much. It is ringed in white now.
            // Whether the remote is anywhere on this row, not whether it is on the
            // row itself: each row carries buttons, and on a television the remote
            // lands on one of those rather than on the panel around them - so the
            // panel's own focus was never true and the ring never appeared.
            val shape = RoundedCornerShape(10.dp)
            var onIt by remember { mutableStateOf(false) }
            Column(Modifier.fillMaxWidth().padding(bottom = 10.dp)
                       .onFocusChanged { onIt = it.hasFocus || it.isFocused }
                       .focusGroup()
                       .clip(shape)
                       .background(Skin.Panel, shape)
                       .border(if (onIt) 2.dp else 0.dp,
                               if (onIt) Color.White else Color.Transparent, shape)
                       .then(if (open) Modifier else Modifier.clickable {
                           ctx.lifecycleScope.launch {
                               val door = Servers.doorThatOpens(srv)
                               Servers.use(ctx, if (door == srv.base) srv
                                                else srv.copy(base = door,
                                                              outside = srv.base))
                               reload(); onDone()
                           }
                       })
                       .padding(horizontal = 14.dp, vertical = 12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            // answering, and how it is known: green for a machine
                            // that spoke a moment ago
                            val up = alive[srv.base]
                            val shut = up == true && letIn[srv.base] == false
                            Box(Modifier.padding(end = 8.dp).size(9.dp)
                                    .background(
                                        when {
                                            shut -> Color(0xFFF0B429)
                                            up == true -> Color(0xFF42C96A)
                                            up == false -> Color(0xFF7D2E2E)
                                            else -> Color(0xFF3A424D)
                                        }, RoundedCornerShape(5.dp)))
                            Text(srv.name.ifEmpty { Servers.hostOf(srv.base) },
                                 color = if (open) Skin.Accent else Skin.Fg,
                                 fontSize = 17.sp, fontWeight = FontWeight.Medium)
                        }
                        // Both of its addresses, each said plainly. Which one a
                        // machine is filed under is the thing that goes wrong, and
                        // it cannot be checked if only one of them is on the screen.
                        // A machine that answers and then refuses everything is
                        // the hardest thing on this screen to work out from a dot,
                        // so it says so, and says what to do about it.
                        if (alive[srv.base] == true && letIn[srv.base] == false) {
                            Text("answers, but does not know you from here — " +
                                 "set a password on that server, or open it with an " +
                                 "invitation link",
                                 color = Color(0xFFF0B429), fontSize = 11.sp,
                                 modifier = Modifier.padding(top = 3.dp))
                        }
                        // A guest who is not on that network is not shown the
                        // address on it: it is no use from where they are, and the
                        // inside of somebody else's house is not theirs to be told.
                        val showIt = { where: String ->
                            srv.mine || !Servers.athome(where) ||
                                Servers.athome(Api.base)
                        }
                        if (srv.outside.isNotEmpty() && showIt(srv.outside)) {
                            Text(Servers.whereKind(srv.outside) + "   " + srv.outside,
                                 color = Skin.Dim, fontSize = 11.sp,
                                 modifier = Modifier.padding(top = 2.dp))
                        }
                        val bits = ArrayList<String>()
                        // which address this is. One machine answers to two - the
                        // network one and the one the router forwards - and a row
                        // that did not say which read as a second machine
                        if (showIt(srv.base)) {
                            bits.add(srv.base)
                            bits.add(Servers.whereKind(srv.base))
                        }
                        bits.add("in use")
                        // a cache is a server like any other in this list; the tag
                        // says why its shelf is the shorter one
                        bits.add(if (srv.copyOf.isNotEmpty()) "cache backup"
                                 else if (srv.mine) "yours" else "guest")
                        // and whether this row holds a key at all, which is the one
                        // thing that decides whether it works from anywhere but the
                        // house - and was nowhere on the screen
                        bits.add(if (srv.token.isEmpty()) "no key" else "has a key")
                        if (srv.group.isNotEmpty()) bits.add(srv.group)
                        Text(bits.joinToString("  ·  "),
                             color = Skin.Dim, fontSize = 12.sp)
                    }
                }
                Row(Modifier.padding(top = 10.dp)) {
                    Pill(if (open) "Open" else "Switch to", active = open) {
                        // whichever of its addresses answers from where we are: a
                        // machine learned at home is filed under an address that
                        // means nothing from a train, and it has another
                        ctx.lifecycleScope.launch {
                            val door = Servers.doorThatOpens(srv)
                            Servers.use(ctx, if (door == srv.base) srv
                                             else srv.copy(base = door,
                                                           outside = srv.base))
                            reload(); onDone()
                        }
                    }
                    // a library that is shown appears in Films and TV next to the others
                    Pill(if (srv.on) "Shown" else "Hidden", active = srv.on) {
                        Servers.setShown(ctx, srv, !srv.on); reload()
                    }
                    // Forgetting a server throws away its address and its key, and
                    // a key handed out by somebody else cannot be typed back in.
                    Pill("Forget") { forgetting = srv }
                }
            }
        }

        forgetting?.let { gone ->
            AlertDialog(
                onDismissRequest = { forgetting = null },
                containerColor = Skin.Panel,
                title = { Text("Forget " + gone.name.ifEmpty { Servers.hostOf(gone.base) } +
                               "?", color = Skin.Fg, fontSize = 17.sp) },
                text = {
                    Text(if (gone.token.isEmpty())
                             "Its address goes from this list. Nothing on it is " +
                             "touched, and it can be added again by typing the " +
                             "address."
                         else
                             "Its address and the key that opens it both go. A key " +
                             "handed out by somebody else cannot be typed back in - " +
                             "they would have to send the invitation again.",
                         color = Skin.Dim, fontSize = 14.sp)
                },
                confirmButton = {
                    Pill("Forget", primary = true) {
                        Servers.remove(ctx, gone); forgetting = null; reload()
                    }
                },
                dismissButton = { Pill("Keep it") { forgetting = null } })
        }

        // The machine that keeps copies, when it is known but not in this list -
        // forgotten by hand, or never added. One press puts it back, with the key
        // this server already uses.
        // Known under either of its addresses counts as known: the row is folded, so
        // testing the one address the server happens to name made the card stand
        // there offering to add a machine that was already in the list - and the
        // press did nothing anybody could see.
        val known = Servers.all(ctx)
        val copyAt = listOf(Api.standby, Api.standbyOut).firstOrNull { where ->
            where.isNotEmpty() && known.none { s ->
                s.base.trimEnd('/') == where.trimEnd('/') ||
                s.outside.trimEnd('/') == where.trimEnd('/')
            }
        } ?: ""
        if (copyAt.isNotEmpty()) {
            Column(Modifier.fillMaxWidth().padding(bottom = 10.dp)
                       .background(Skin.Panel, RoundedCornerShape(10.dp))
                       .padding(horizontal = 14.dp, vertical = 12.dp)) {
                Text(Api.standbyName.ifBlank { Servers.hostOf(copyAt) },
                     color = Skin.Fg, fontSize = 15.sp)
                Text(copyAt + "  ·  it answers when this server is off",
                     color = Skin.Dim, fontSize = 12.sp)
                Row(Modifier.padding(top = 10.dp)) {
                    Pill("Add it back") {
                        // the other address of it, if this is the one it was not
                        // filed under, so it comes back as one machine
                        val other = listOf(Api.standby, Api.standbyOut)
                            .firstOrNull { it.isNotEmpty() && it != copyAt } ?: ""
                        // its own name, which it announced: "192.168.0.9 copy" is
                        // an address and a role, and neither is what the machine is
                        Servers.add(ctx, Server(Api.standbyName.ifBlank {
                                                    Servers.hostOf(copyAt) },
                                                copyAt, Api.token,
                                                mine = Api.token.isEmpty(),
                                                on = false, copyOf = Api.base,
                                                outside = other))
                        reload()
                    }
                }
            }
        }

        Text("Add a server", color = Skin.Dim, fontSize = 13.sp,
             modifier = Modifier.padding(top = 8.dp, bottom = 8.dp))
        TextBox(text, "192.168.1.20:8765", Modifier.fillMaxWidth()) {
            text = it; error = null
        }
        // A token is what identifies you to a server that is not on your network. It
        // is in the invitation link after /s/, and pasting the whole link fills both
        // boxes at once - this is for reading one off a screen.
        Spacer(Modifier.height(8.dp))
        TextBox(typedToken, "token or 5-letter code, for a friend's server",
                Modifier.fillMaxWidth()) {
            typedToken = it.trim(); error = null
        }
        if (wantPassword) {
            Spacer(Modifier.height(8.dp))
            TextBox(password, "the owner password for this server",
                    Modifier.fillMaxWidth(), password = true) {
                password = it; error = null
            }
        }
        if (text.isEmpty()) {
            Text("192.168.1.20:8765   \u00b7   or paste a whole invitation link",
                 color = Color(0xFF5C6675), fontSize = 12.sp,
                 modifier = Modifier.padding(top = 6.dp))
        }
        Spacer(Modifier.height(16.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill(if (checking) "Checking\u2026" else "Add", primary = true) {
                if (!checking && text.isNotBlank()) {
                    checking = true
                    ctx.lifecycleScope.launch {
                        val invite = Servers.parseInvite(text)
                        // an address as typed - no scheme needed, port assumed
                        var addr = invite?.first ?: Servers.asAddress(text)
                        // The main server may be off. The machine keeping copies is
                        // a second door to the same house - it holds every guest key
                        // and answers on its own port - but nothing pointed a new
                        // screen at it, so a guest whose server was asleep could not
                        // set up at all. Asked only when the first address is silent.
                        if (!Servers.answers(addr)) {
                            Servers.standbyOf(addr)?.let { other ->
                                if (Servers.answers(other)) addr = other
                            }
                        }
                        // a link carries its own token; otherwise take the one
                        // typed - or, if that is five characters, what it stands for
                        var tok = invite?.second ?: typedToken
                        if (invite == null && Servers.looksLikeCode(tok)) {
                            tok = Servers.redeem(addr, tok) ?: tok
                        }
                        // a server that wants a password says so, and is given one
                        // rather than a refusal nobody can act on
                        if (tok.isEmpty() && password.isNotBlank()) {
                            Api.signIn(addr, password)?.let { tok = it }
                        }
                        val problem = Api.ping(addr, tok)
                        if (problem != null && Api.wantsPassword(addr, tok)) {
                            checking = false
                            wantPassword = true
                            error = if (password.isBlank())
                                "This server has a password. Type it below."
                            else "That is not the password."
                            return@launch
                        }
                        if (problem == null) {
                            val described = Servers.describe(addr, tok)
                            val srv = Server(described?.first ?: Servers.hostOf(addr),
                                             addr, tok, mine = described?.second ?: tok.isEmpty())
                            Servers.add(ctx, srv)
                            Servers.use(ctx, srv)
                            text = ""; typedToken = ""
                            reload(); checking = false; onDone()
                        } else {
                            checking = false
                            // the usual reason a bare address fails: it is somebody
                            // else's server, and out there the link is the credential
                            error = if (tok.isEmpty() && problem.contains("403"))
                                "That server does not know you. Paste the whole " +
                                "invitation link - the part after /s/ is what " +
                                "identifies you."
                            else problem
                        }
                    }
                }
            }
            onCancel?.let { Pill("Back") { it() } }
        }
        error?.let {
            Text(it, color = Color(0xFFF0704F), fontSize = 14.sp,
                 modifier = Modifier.padding(top = 10.dp))
        }
    }
}

/**
 * Which of the five views a report belongs in.
 *
 * "Still open" is the one that matters day to day, so it is what the screen opens on;
 * a sorted report is kept and readable, but out of the way.
 */
private fun showable(r: Api.Report, view: String, tab: String): Boolean {
    // a fault the machinery noticed, or something a person asked for
    val mine = if (r.auto) "errors" else "requests"
    if (mine != tab) return false
    return if (view == "done") r.done else !r.done
}

/**
 * Settings: how subtitles look on each kind of screen, and what to do about updates.
 *
 * All three screens are here rather than only this one, because the easiest place to
 * fix the television's subtitles is usually the phone in your hand.
 */

/**
 * Reports: what is new, what has gone wrong, and what people have asked for.
 *
 * A place of its own rather than a heading inside the settings, because none of the
 * three is a setting and nobody looks for them under a gear.
 */
@Composable
private fun ReportsScreen(onBack: () -> Unit) {
    // as an activity, because the rows launch work of their own
    val ctx = LocalContext.current as AppCompatActivity
    var reports by remember { mutableStateOf<List<Api.Report>>(emptyList()) }
    var changes by remember { mutableStateOf<List<Api.Release>>(emptyList()) }
    var openRelease by remember { mutableStateOf(0) }
    // minor version open in What is new; empty = the newest
    var openGroup by remember { mutableStateOf("") }
    var reportTab by remember { mutableStateOf("new") }
    var shown by remember { mutableStateOf("open") }
    var writing by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        reports = Api.reports()
        changes = runCatching { Api.changes() }.getOrDefault(emptyList())
    }

    Column(Modifier.fillMaxSize()
               .verticalScroll(rememberScrollState())
               .padding(horizontal = 20.dp, vertical = 16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill("Back") { onBack() }
            Spacer(Modifier.width(12.dp))
            Text("Reports", color = Skin.Fg, fontSize = 20.sp)
        }
        Spacer(Modifier.height(12.dp))
        Row(Modifier.padding(bottom = 8.dp)) {
            val faults = reports.count { !it.done && it.auto }
            val asks = reports.count { !it.done && !it.auto }
            listOf("new" to "What is new",
                   "errors" to ("Errors" + if (faults > 0) " ($faults)" else ""),
                   "requests" to ("Requests" + if (asks > 0) " ($asks)" else ""))
                .forEach { (id, label) ->
                    Pill(label, active = reportTab == id) { reportTab = id }
                    Spacer(Modifier.width(6.dp))
                }
        }

        if (reportTab == "new" && changes.isNotEmpty()) {
            // grouped by minor version (0.18, 0.17), one open: hundreds of releases were one long scroll
            val groups = changes.withIndex().groupBy {
                it.value.version.split(".").take(2).joinToString(".")
            }.toList()
            groups.forEachIndexed { g, (minor, rels) ->
            val groupOpen = if (openGroup.isEmpty()) g == 0 else openGroup == minor
            Row(Modifier.fillMaxWidth().padding(bottom = 8.dp)
                    .clickable { openGroup = if (groupOpen) "-" else minor }
                    .padding(horizontal = 4.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text((if (groupOpen) "▾  " else "▸  ") + minor + "  ·  " + rels.size +
                         (if (rels.size == 1) " release" else " releases"),
                     color = Skin.Accent, fontSize = 15.sp)
            }
            if (groupOpen) rels.forEach { (n, release) ->
                val open = n == openRelease
                Column(Modifier.fillMaxWidth().padding(bottom = 8.dp)
                           .background(Skin.Panel, RoundedCornerShape(10.dp))
                           .clickable { openRelease = if (open) -1 else n }
                           .padding(horizontal = 14.dp, vertical = 10.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        // the title gives way, the date does not: with no weight of its
                        // own the title took the whole width of a phone held upright
                        // and left the date wrapping one character to a line
                        Text(release.title, color = Skin.Fg, fontSize = 15.sp,
                             maxLines = 2, overflow = TextOverflow.Ellipsis,
                             modifier = Modifier.weight(1f))
                        Text(listOf(release.version, release.when_)
                                 .filter { it.isNotEmpty() }.joinToString("  ·  "),
                             color = Skin.Accent, fontSize = 12.sp,
                             maxLines = 1, softWrap = false,
                             modifier = Modifier.padding(start = 10.dp))
                    }
                    if (open) {
                        release.items.forEach { line ->
                            Text("\u2022  " + line, color = Skin.Dim, fontSize = 13.sp,
                                 modifier = Modifier.padding(top = 6.dp))
                        }
                    }
                }
            }
            }
        }

        if (reportTab != "new") {
        // writing one belongs with the requests: that is the tab somebody is on when
        // they think of something
        if (reportTab == "requests") {
            SettingsRow("Ask for something",
                        "A film, a programme, or a thing this should do") {
                Pill("Write one") { writing = true }
            }
        } else {
            SettingsRow("Faults", "What the machinery noticed, and crashes sent home") {
                Pill("Write one") { writing = true }
            }
        }
        // within a tab, the only division worth making: still wanted, or dealt with
        Row(Modifier.padding(top = 4.dp, bottom = 6.dp)) {
            listOf("open" to "Still open", "done" to "Sorted").forEach { (id, label) ->
                val n = reports.count { showable(it, id, reportTab) }
                Pill(label + " (" + n + ")", active = shown == id) { shown = id }
                Spacer(Modifier.width(6.dp))
            }
        }
        reports.filter { showable(it, shown, reportTab) }
            .take(30)
            .forEach { r ->
                Column(Modifier.fillMaxWidth().padding(bottom = 8.dp)
                           .background(Skin.Panel, RoundedCornerShape(10.dp))
                           .padding(horizontal = 14.dp, vertical = 10.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                    if (r.done) {
                        // sorted: the same green tick the page uses
                        Text("\u2713", color = Color(0xFF5FD08A), fontSize = 13.sp,
                             modifier = Modifier.padding(end = 6.dp))
                    }
                    Text(listOf(r.who,
                                if (r.kind == "request") "request"
                                else if (r.kind == "crash") "crash"
                                else if (r.auto) "fault" else "problem",
                                java.text.SimpleDateFormat("d MMM HH:mm",
                                    java.util.Locale.getDefault())
                                    .format(java.util.Date(r.when_ * 1000)),
                                r.app).filter { it.isNotEmpty() }.joinToString("  \u00b7  "),
                         color = if (r.done) Skin.Dim
                                 else if (r.auto) Skin.Dim else Skin.Accent,
                         fontSize = 12.sp)
                    Spacer(Modifier.weight(1f))
                    // one's own, and only one's own: a request typed in haste, or one
                    // that has answered itself
                    if (!r.done && r.who == Api.whoAmI && Api.whoAmI.isNotEmpty()) {
                        Pill("Cancel") {
                            ctx.lifecycleScope.launch {
                                if (Api.cancelReport(r.id)) reports = Api.reports()
                            }
                        }
                    }
                    Pill(if (r.done) "Reopen" else "Sorted") {
                        ctx.lifecycleScope.launch {
                            if (Api.settleReport(r.id, !r.done)) reports = Api.reports()
                        }
                    }
                    }
                    Text(r.text, color = if (r.done) Skin.Dim else Skin.Fg,
                         fontSize = 13.5.sp,
                         maxLines = 6, overflow = TextOverflow.Ellipsis,
                         modifier = Modifier.padding(top = 4.dp))
                    // what was learned from it, for whoever hits the same thing next
                    if (r.reason.isNotEmpty()) {
                        Text("Why  \u00b7  " + r.reason, color = Skin.Dim,
                             fontSize = 12.5.sp, modifier = Modifier.padding(top = 3.dp))
                    }
                    if (r.solution.isNotEmpty()) {
                        Text("Fixed by  \u00b7  " + r.solution, color = Skin.Dim,
                             fontSize = 12.5.sp, modifier = Modifier.padding(top = 2.dp))
                    }
                }
            }
        if (reports.none { showable(it, shown, reportTab) }) {
            Text(if (reportTab == "errors") "Nothing has gone wrong lately."
                 else "Nothing asked for yet.",
                 color = Skin.Dim, fontSize = 13.sp,
                 modifier = Modifier.padding(bottom = 8.dp))
        }
        }
        Spacer(Modifier.height(24.dp))
    }

    if (writing) {
        ReportDialog(startAs = if (reportTab == "requests") "request" else "problem") {
            writing = false
            ctx.lifecycleScope.launch { reports = Api.reports() }
        }
    }
}

@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
private fun SettingsScreen(onBack: () -> Unit, onServers: () -> Unit,
                           onPeople: (() -> Unit)? = null) {
    val ctx = LocalContext.current as AppCompatActivity
    var editing by remember { mutableStateOf<String?>(null) }
    var looks by remember { mutableStateOf<Map<String, Api.SubLook>>(emptyMap()) }
    var checking by remember { mutableStateOf(false) }
    var found by remember { mutableStateOf<String?>(null) }
    var skipped by remember { mutableStateOf(Updates.skippedVersion(ctx)) }
    // the version the check found, waiting to be installed
    var waiting by remember { mutableStateOf<Updates.Available?>(null) }
    var rollOn by remember { mutableStateOf(true) }
    // which of the three kinds the film shelf stands for this viewer
    var films by remember { mutableStateOf(Api.FilmsShow()) }
    var reports by remember { mutableStateOf<List<Api.Report>>(emptyList()) }
    var changes by remember { mutableStateOf<List<Api.Release>>(emptyList()) }
    // what the server is running, and what it says while it is being replaced
    var server by remember { mutableStateOf<Api.ServerBuild?>(null) }
    var serverSaid by remember { mutableStateOf<String?>(null) }
    // while the server is being replaced: the press is answered at once and the
    // machine is away for ten seconds after that
    var updating by remember { mutableStateOf(false) }
    // the same three, for the machine that keeps copies
    var copyBuild by remember { mutableStateOf<Api.ServerBuild?>(null) }
    var copySaid by remember { mutableStateOf<String?>(null) }
    var copyBusy by remember { mutableStateOf(false) }
    // which release is open: the newest, because that is the one worth reading
    var openRelease by remember { mutableStateOf(0) }
    // which of the three: what is new, what is broken, what somebody would like
    var reportTab by remember { mutableStateOf("new") }
    var shown by remember { mutableStateOf("open") }     // open, or dealt with
    var writing by remember { mutableStateOf(false) }

    suspend fun reload() {
        looks = listOf("tv", "web", "phone").associateWith { Api.subtitleLook("", it) }
    }
    LaunchedEffect(Unit) {
        reload()
        rollOn = Api.autoNext()
        films = Api.filmsShow()
        server = runCatching { Api.serverBuild() }.getOrNull()
        reports = Api.reports()
        changes = runCatching { Api.changes() }.getOrDefault(emptyList())
    }

    val screens = listOf(
        Triple("tv", "Television", "Seen from across a room."),
        Triple("web", "Computer", "A monitor at desk distance."),
        Triple("phone", "Phone", "Held at arm's length; larger text for its size."),
    )

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())
               .padding(horizontal = 24.dp, vertical = 20.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill("Back") { onBack() }
            Text("Settings", color = Skin.Fg, fontSize = 22.sp,
                 fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 12.dp))
        }

        SettingsHeading("SUBTITLES")
        // Which language a subtitle is chosen in when a film has one. Everybody has
        // their own, kept by the server, so it is the same answer on the television
        // and in the browser - and a guest has it as much as the owner does.
        var speaks by remember { mutableStateOf("") }
        LaunchedEffect(Unit) { speaks = Api.subtitleLanguage() }
        SettingsRow("Subtitle language",
                    (SubLanguages.firstOrNull { it.first == speaks }?.second
                        ?: "English") + "  ·  chosen for you when a film has one") {
            var open by remember { mutableStateOf(false) }
            Pill("Change") { open = true }
            if (open) {
                AlertDialog(
                    onDismissRequest = { open = false },
                    containerColor = Skin.Panel,
                    title = { Text("Subtitle language", color = Skin.Fg,
                                   fontSize = 17.sp) },
                    text = {
                        Column(Modifier.verticalScroll(rememberScrollState())) {
                            Text("The language a subtitle is picked in when a film " +
                                 "has one. It is also the language offered first " +
                                 "when you go looking for one.",
                                 color = Skin.Dim, fontSize = 13.sp,
                                 modifier = Modifier.padding(bottom = 10.dp))
                            SubLanguages.forEach { (code, name) ->
                                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
                                    Pill(name, active = code == speaks) {
                                        speaks = code
                                        Api.myLanguage = code
                                        open = false
                                        ctx.lifecycleScope.launch {
                                            Api.setSubtitleLanguage(code)
                                        }
                                    }
                                }
                            }
                        }
                    },
                    confirmButton = { Pill("Close") { open = false } })
            }
        }

        // And what to read when the film carries nothing in the first language.
        // Without one, whatever the film does carry is used, which is how it behaved
        // before there was a second choice.
        var alsoSpeaks by remember { mutableStateOf("") }
        LaunchedEffect(Unit) { alsoSpeaks = Api.subtitleLanguage2() }
        SettingsRow("Second choice",
                    (if (alsoSpeaks.isEmpty()) "None"
                     else SubLanguages.firstOrNull { it.first == alsoSpeaks }?.second
                         ?: alsoSpeaks) +
                    "  ·  read when the film has nothing in the first") {
            var open by remember { mutableStateOf(false) }
            Pill("Change") { open = true }
            if (open) {
                AlertDialog(
                    onDismissRequest = { open = false },
                    containerColor = Skin.Panel,
                    title = { Text("Second choice", color = Skin.Fg, fontSize = 17.sp) },
                    text = {
                        Column(Modifier.verticalScroll(rememberScrollState())) {
                            Text("Read when the film carries no subtitle in the first " +
                                 "language. None means whatever the film has is used.",
                                 color = Skin.Dim, fontSize = 13.sp,
                                 modifier = Modifier.padding(bottom = 10.dp))
                            Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
                                Pill("None", active = alsoSpeaks.isEmpty()) {
                                    alsoSpeaks = ""
                                    Api.myLanguage2 = ""
                                    open = false
                                    ctx.lifecycleScope.launch { Api.setSubtitleLanguage2("") }
                                }
                            }
                            SubLanguages.filter { it.first.isNotEmpty() }
                                .forEach { (code, name) ->
                                    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
                                        Pill(name, active = code == alsoSpeaks) {
                                            alsoSpeaks = code
                                            Api.myLanguage2 = code
                                            open = false
                                            ctx.lifecycleScope.launch {
                                                Api.setSubtitleLanguage2(code)
                                            }
                                        }
                                    }
                                }
                        }
                    },
                    confirmButton = { Pill("Close") { open = false } })
            }
        }

        screens.forEach { (id, title, blurb) ->
            val l = looks[id]
            SettingsRow(
                title + (if (id == Api.device) "  (this device)" else ""),
                l?.let {
                    "%d%%  \u00b7  %s  \u00b7  %s".format(
                        (it.size * 100).toInt(),
                        it.colour.replaceFirstChar { c -> c.uppercase() },
                        SUB_BACKS.firstOrNull { b -> b.first == it.background }?.second ?: "")
                } ?: blurb,
                accent = id == Api.device,
            ) { Pill("Change") { editing = id } }
        }

        // A colour of one's own. The server picks what somebody meets on their first
        // evening; after that it belongs to the viewer, and it follows them from the
        // television to the phone because the server is keeping it.
        SettingsHeading("APPEARANCE")
        var swatches by remember { mutableStateOf<List<Api.Accent>>(emptyList()) }
        var onNow by remember { mutableStateOf("") }
        var chosen by remember { mutableStateOf("") }
        LaunchedEffect(Unit) {
            val (list, now, mine) = Api.accents()
            swatches = list; onNow = now; chosen = mine
        }
        SettingsRow(
            "Colour",
            (swatches.firstOrNull { it.code.equals(onNow, true) }?.name ?: "Accent") +
                (if (chosen.isEmpty()) "  ·  the server's" else "  ·  yours"),
            accent = true,
        ) {
            var open by remember { mutableStateOf(false) }
            Pill("Change") { open = true }
            if (open) {
                AccentPanel(
                    swatches = swatches, on = onNow, mine = chosen,
                    onPick = { code ->
                        open = false
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            val said = Api.setAccent(code)
                            if (said.isNotEmpty()) {
                                Skin.paint(said)
                                onNow = said
                                chosen = code
                            }
                        }
                    },
                    onClose = { open = false })
            }
        }

        // Where the artwork stands. The server keeps an answer for each kind of screen
        // and this sets the one it is asked from, so the phone and the television are
        // free to differ - a poster across a room is not a poster at arm's length.
        var ground by remember { mutableStateOf(Skin.Backdrop) }
        val grounds = listOf("on" to "Always", "poster" to "Title page only",
                             "off" to "Off")
        SettingsRow("Background poster",
                    grounds.firstOrNull { it.first == ground }?.second ?: "Always",
                    extra = ({
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            grounds.forEach { (id, label) ->
                                Pill(label, active = id == ground, small = true,
                                     narrow = true) {
                                    ground = id
                                    Skin.Backdrop = id      // the page behind answers now
                                    ctx.lifecycleScope.launch { Api.setBackdrop(id) }
                                }
                            }
                        }
                    }))

        SettingsHeading("FILMS")
        // Play, then download, then ask. Each kind is shown or hidden on its own:
        // what is here plays, what a pack carries can be fetched, and the rest can
        // only be asked for.
        listOf(
            Triple("disk", "On disk", "Films this house holds a file for. These play."),
            Triple("download", "Download",
                   "Films one of the packs carries. Fetching one is a button."),
            Triple("request", "Request",
                   "New on streaming and nowhere in the house. All anybody can do is ask."))
            .forEach { (name, label, note) ->
                val on = when (name) {
                    "disk" -> films.disk
                    "download" -> films.download
                    else -> films.request
                }
                SettingsRow(label, note) {
                    Pill(if (on) "Show" else "Hide", active = on) {
                        films = when (name) {
                            "disk" -> films.copy(disk = !on)
                            "download" -> films.copy(download = !on)
                            else -> films.copy(request = !on)
                        }
                        ctx.lifecycleScope.launch { films = Api.setFilmsShow(films) }
                    }
                }
            }

        SettingsHeading("SERVERS")
        SettingsRow(Servers.inUse(ctx)?.name ?: "None yet",
                    Servers.all(ctx).size.toString() + " known  \u00b7  " +
                    (Servers.inUse(ctx)?.base ?: "")) {
            Pill("Manage") { onServers() }
        }
        // Inviting somebody is done once and rarely, and it was a pill on the bar
        // above the shelves - beside the tabs, in the way, on every screen. It is a
        // setting about the server, and it lives with the rest of them.
        onPeople?.let { go ->
            SettingsRow("People", "Who may watch from away, and the links they use") {
                Pill("Invitations") { go() }
            }
        }

        // More than one server, and what to do with what they hold: one shelf, one
        // server at a time, or a shelf per group. The main server's two machines are one
        // library seen twice; a friend's is somebody else's evening.
        // What this screen wears. The server holds the palettes, so the list is
        // whatever it offers and nothing here knows a colour.
        var looks by remember { mutableStateOf<List<Triple<String, String, String>>>(
            emptyList()) }
        var wearing by remember { mutableStateOf("") }
        var putOn by remember { mutableStateOf("") }
        var putOnWhy by remember { mutableStateOf("") }
        LaunchedEffect(Unit) {
            runCatching {
                val said = Api.skins()
                val arr = said.optJSONArray("skins")
                val out = ArrayList<Triple<String, String, String>>()
                for (i in 0 until (arr?.length() ?: 0)) {
                    val one = arr!!.getJSONObject(i)
                    out.add(Triple(one.optString("id"), one.optString("name"),
                                   one.optString("why")))
                }
                looks = out
                wearing = said.optString("chosen")
                putOn = said.optString("imposed")
                putOnWhy = said.optString("why")
            }
        }
        if (looks.isNotEmpty()) {
            // The pills say which one is on, so naming it again on the line above was
            // the same word twice and a taller box for it.
            SettingsRow("Theme",
                        if (putOn.isNotEmpty())
                            "wearing " +
                            (looks.firstOrNull { it.first == putOn }?.second ?: putOn) +
                            " for now"
                        else "",
                        extra = ({
                            if (putOn.isNotEmpty()) {
                                Text("This machine put that on itself - " + putOnWhy +
                                     ". What is chosen here comes back when that " +
                                     "passes.",
                                     color = Skin.Dim, fontSize = 12.sp,
                                     modifier = Modifier.padding(bottom = 8.dp))
                            }
                            // five of them are wider than a phone held upright: they
                            // wrap, and the tighter pill keeps the wrap to two lines
                            // instead of running the box down the page
                            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp),
                                    verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                looks.forEach { (id, label, _) ->
                                    Pill(label, active = id == wearing, small = true,
                                         narrow = true) {
                                        wearing = id
                                        ctx.lifecycleScope.launch { Api.wearSkin(id) }
                                    }
                                }
                            }
                        }))
        }
        var howShelves by remember { mutableStateOf(Servers.shelves(ctx)) }
        var servers by remember { mutableStateOf(Servers.all(ctx)) }
        if (servers.size > 1) {
            val ways = listOf(
                "together" to "Together on one shelf",
                "one" to "One server at a time",
                "groups" to "A shelf for each group")
            SettingsRow("Libraries",
                        (ways.firstOrNull { it.first == howShelves }?.second
                            ?: "Together on one shelf") +
                            "  ·  " + servers.size + " servers") {
                var open by remember { mutableStateOf(false) }
                Pill("Change") { open = true }
                if (open) {
                    AlertDialog(
                        onDismissRequest = { open = false },
                        containerColor = Skin.Panel,
                        title = { Text("Libraries", color = Skin.Fg, fontSize = 17.sp) },
                        text = {
                            Column {
                                Text("What the shelves hold when more than one " +
                                     "server is known.",
                                     color = Skin.Dim, fontSize = 13.sp,
                                     modifier = Modifier.padding(bottom = 10.dp))
                                ways.forEach { (code, name) ->
                                    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
                                        Pill(name, active = code == howShelves) {
                                            howShelves = code
                                            Servers.setShelves(ctx, code)
                                            open = false
                                        }
                                    }
                                }
                            }
                        },
                        confirmButton = { Pill("Close") { open = false } })
                }
            }
            if (howShelves == "groups") {
                servers.forEach { srv ->
                    SettingsRow(
                        srv.name.ifEmpty { Servers.hostOf(srv.base) },
                        (if (srv.group.isEmpty()) "No group" else srv.group) +
                            (if (srv.copyOf.isNotEmpty()) "  ·  cache backup"
                             else ""),
                    ) {
                        var open by remember { mutableStateOf(false) }
                        var naming by remember { mutableStateOf(false) }
                        var typed by remember { mutableStateOf("") }
                        Box {
                            Pill(if (srv.group.isEmpty()) "Group" else srv.group) {
                                open = true
                            }
                            DropdownMenu(expanded = open,
                                         onDismissRequest = { open = false },
                                         modifier = Modifier.background(Skin.Panel)) {
                                DropdownMenuItem(
                                    text = { Text("No group", fontSize = 14.sp,
                                                  color = if (srv.group.isEmpty())
                                                              Skin.Accent else Skin.Fg) },
                                    onClick = {
                                        Servers.setGroup(ctx, srv, "")
                                        servers = Servers.all(ctx); open = false
                                    })
                                // the groups already named, so the second server
                                // joins the first without anybody typing twice
                                Servers.groups(ctx).forEach { name ->
                                    DropdownMenuItem(
                                        text = { Text(name, fontSize = 14.sp,
                                                      color = if (name == srv.group)
                                                                  Skin.Accent
                                                              else Skin.Fg) },
                                        onClick = {
                                            Servers.setGroup(ctx, srv, name)
                                            servers = Servers.all(ctx); open = false
                                        })
                                }
                                DropdownMenuItem(
                                    text = { Text("New group…", fontSize = 14.sp,
                                                  color = Skin.Dim) },
                                    onClick = { open = false; naming = true })
                            }
                        }
                        if (naming) {
                            AlertDialog(
                                onDismissRequest = { naming = false },
                                containerColor = Skin.Panel,
                                title = { Text("New group", color = Skin.Fg,
                                               fontSize = 17.sp) },
                                text = {
                                    TextBox(typed, "Home", Modifier.fillMaxWidth()) {
                                        typed = it
                                    }
                                },
                                confirmButton = {
                                    Pill("Save") {
                                        if (typed.isNotBlank()) {
                                            Servers.setGroup(ctx, srv, typed.trim())
                                            servers = Servers.all(ctx)
                                        }
                                        typed = ""; naming = false
                                    }
                                },
                                dismissButton = { Pill("No") { naming = false } })
                        }
                    }
                }
            }
        }

        SettingsHeading("PLAYBACK")

        // One viewer's own ceiling, kept by the server so it is the same answer on
        // every screen they use. Not the server's limit, which applies over the top
        // of it, and not the player's gear, which overrules it for one film.
        var want by remember { mutableStateOf(Api.OwnQuality(0, 0)) }
        LaunchedEffect(Unit) { want = Api.myQuality() }
        val sizeName = QUALITY_SIZES.firstOrNull { it.first == want.height }?.second
            ?: "Original"
        val rateName = QUALITY_RATES.firstOrNull { it.first == want.mbit }?.second
            ?: "No limit"
        SettingsRow("Your maximum quality",
                    sizeName + "  ·  " + rateName +
                        "  ·  yours alone, on every screen") {
            var open by remember { mutableStateOf(false) }
            Pill("Change") { open = true }
            if (open) {
                QualityPanel(
                    height = want.height, mbit = want.mbit, cap = "",
                    plainSound = false,
                    heading = "Your maximum quality",
                    note = "The most that will be sent to you, on any screen you " +
                        "watch on. Anything smaller arrives as it is. The server has " +
                        "a limit of its own for everybody, and the lower of the two " +
                        "wins.",
                    withSound = false,
                    onPick = { h, m ->
                        open = false
                        want = Api.OwnQuality(h, m)
                        ctx.lifecycleScope.launch { want = Api.setMyQuality(h, m) }
                    },
                    onSound = { },
                    onClose = { open = false })
            }
        }

        // the next-episode setting applies to everybody on the server, so it is the
        // owner's to change; a guest is not shown a switch that would be refused
        if (Servers.current(ctx)?.mine != false) {

        // The server's own ceilings, for everybody. Shown here only when this app is
        // talking to its own server - from anywhere else they are refused, and a
        // control that cannot work is worse than none.
        var caps by remember { mutableStateOf(Api.Ceilings(0, 0, 0, 0)) }
        var editing by remember { mutableStateOf("") }
        LaunchedEffect(Unit) { caps = Api.serverQuality() }
        val said = { h: Int, m: Int ->
            if (h == 0 && m == 0) "no limit"
            else listOfNotNull(if (h > 0) h.toString() + "p" else null,
                               if (m > 0) m.toString() + " Mbit" else null)
                .joinToString(", ")
        }
        SettingsRow("The server's limits — everybody",
                    "In the house: " + said(caps.homeHeight, caps.homeMbit) +
                        "  ·  From outside: " +
                        said(caps.awayHeight, caps.awayMbit),
                    extra = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Set", color = Skin.Dim, fontSize = 12.sp,
                                 modifier = Modifier.padding(end = 8.dp))
                            Pill("In the house") { editing = "home" }
                            Pill("From outside") { editing = "away" }
                        }
                    }) { }
        if (editing.isNotEmpty()) {
            val home = editing == "home"
            QualityPanel(
                height = if (home) caps.homeHeight else caps.awayHeight,
                mbit = if (home) caps.homeMbit else caps.awayMbit,
                cap = "", plainSound = false, withSound = false,
                heading = if (home) "Everyone, on this network"
                          else "Everyone, from outside",
                note = if (home)
                    "The most this server will send to anything in the house, itself " +
                        "included. There is rarely a reason to cap it."
                else
                    "The most this server will send to anybody reaching it from " +
                        "elsewhere, guests included. Bounded by what this line can " +
                        "upload, not by what they can download.",
                onPick = { h, m ->
                    val where = editing
                    editing = ""
                    ctx.lifecycleScope.launch { caps = Api.setServerQuality(where, h, m) }
                },
                onSound = { },
                onClose = { editing = "" })
        }

        var wait by remember { mutableStateOf(5) }
        LaunchedEffect(Unit) { wait = Api.nextDelay() }
        SettingsRow("Next episode",
                    if (!rollOn) "Waits to be asked"
                    else if (wait == 0) "Starts as the credits end"
                    else "Starts itself after " + wait +
                        (if (wait == 1) " second" else " seconds"),
                    // how long it waits belongs with the switch that turns it on,
                    // not in a row of its own underneath
                    extra = if (!rollOn) null else ({
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("After", color = Skin.Dim, fontSize = 12.sp,
                                 modifier = Modifier.padding(end = 8.dp))
                            (0..5).forEach { n ->
                                Pill(if (n == 0) "At once" else n.toString() + "s",
                                     active = wait == n) {
                                    wait = n
                                    ctx.lifecycleScope.launch {
                                        wait = Api.setNextDelay(n)
                                    }
                                }
                            }
                        }
                    })) {
            Pill(if (rollOn) "On" else "Off", active = rollOn) {
                rollOn = !rollOn
                ctx.lifecycleScope.launch { rollOn = Api.setAutoNext(rollOn) }
            }
        }
        }

        SettingsHeading("UPDATES")
        // The server, from the same house as the server. A screen away from home is
        // told what is running and nothing else: it cannot see what happens next, and
        // the machine it would be restarting is not in front of anybody.
        server?.let { build ->
            // by name. "The server" and "the other server" name nothing on a
            // screen that has two of them on it, and which one is being updated is
            // exactly what somebody pressing Update needs to be sure of.
            SettingsRow(Servers.inUse(ctx)?.name?.takeIf { it.isNotBlank() }
                            ?: Servers.hostOf(Api.base),
                        "Version " + build.have +
                        (if (build.newer) "  \u00b7  " + build.latest + " is out"
                         else "  \u00b7  up to date") +
                        (serverSaid?.let { "  \u00b7  " + it } ?: "")) {
                if (build.newer && build.lan) {
                    // The press stands the server down and installs: it answers in a
                    // second and is gone for ten. The button has to say so while that
                    // happens, or the press reads as having done nothing.
                    Pill(if (updating) "Updating…" else "Update",
                         primary = true, dim = updating) {
                        if (!updating) {
                            updating = true
                            serverSaid = "asking the server"
                            ctx.lifecycleScope.launch {
                                val why = Api.updateServer()
                                serverSaid = why ?: ("installing " + build.latest + " and restarting" +
                                                     " - it will come back on its own")
                                if (why != null) updating = false
                                else {
                                    // wait for it to answer again, then say what it is
                                    repeat(30) {
                                        kotlinx.coroutines.delay(2_000)
                                        val now = Api.serverBuild()
                                        if (now != null && now.have != build.have) {
                                            server = now
                                            serverSaid = "now " + now.have
                                            updating = false
                                            return@launch
                                        }
                                    }
                                    updating = false
                                }
                            }
                        }
                    }
                }
                Pill("Check") {
                    ctx.lifecycleScope.launch {
                        serverSaid = "asking the site"
                        server = Api.serverBuild(fresh = true)
                        serverSaid = null
                    }
                }
            }
        }
        // The machine that keeps copies, replaced from here rather than by switching
        // the whole app over to it and back again. It is a server, and it goes stale
        // exactly like this one.
        // The other machine, whichever way round the two are standing. This asked
        // the server for the machine that keeps copies of it - and a copy keeps no
        // copies of its own, so with the app on the copy there was no second row and
        // the house itself could not be updated from here at all.
        val elsewhere = remember(Api.base) {
            Servers.all(ctx).firstOrNull {
                it.base.isNotEmpty() &&
                    !Servers.sameMachine(it, Server("", Api.base.trimEnd('/'), ""))
            }
        }
        // A machine this app knows that is not the one it is standing on, first of
        // all. What the server says about its copy is remembered against each server
        // and can be stale - on the cache it pointed at the cache itself, so the second
        // row named the machine already in the first and the other one appeared
        // nowhere at all.
        // The server itself answers this, both ways round: it names the machine that
        // copies from it, and the machine it copies from. On the main server the first is
        // filled and on the copy the second, so between them there is always an
        // answer - which is why this no longer depends on what the app happens to
        // have written down about either machine.
        val here = Server("", Api.base.trimEnd('/'), "")
        val copyAt = listOf(Api.standby, Api.standbyOut, Api.houseWhere)
            .firstOrNull {
                it.isNotEmpty() && !Servers.sameMachine(Server("", it, ""), here)
            }
            ?: elsewhere?.base.orEmpty()
        val copyCalled = (if (copyAt == Api.houseWhere) Api.houseName else Api.standbyName)
            .ifBlank { elsewhere?.name.orEmpty() }
            .ifBlank { Servers.hostOf(copyAt) }
        // and its own key, not this server's: a key belongs to the machine that
        // issued it, and asking one machine with another's is refused
        val copyKey = elsewhere?.token?.takeIf { it.isNotEmpty() } ?: Api.token
        if (copyAt.isNotEmpty()) {
            LaunchedEffect(copyAt) {
                if (copyBuild == null) copyBuild = Api.serverBuildAt(copyAt, copyKey)
            }
            SettingsRow(copyCalled,
                        copyBuild?.let { b ->
                            "Version " + b.have +
                            (if (b.newer) "  ·  " + b.latest + " is out"
                             else "  ·  up to date") +
                            (copySaid?.let { "  ·  " + it } ?: "")
                        } ?: (Servers.hostOf(copyAt) + "  ·  asking…")) {
                val b = copyBuild
                if (b != null && b.newer && b.lan) {
                    Pill(if (copyBusy) "Updating…" else "Update",
                         primary = true, dim = copyBusy) {
                        if (!copyBusy) {
                            copyBusy = true
                            copySaid = "asking that machine"
                            ctx.lifecycleScope.launch {
                                val why = Api.updateServerAt(copyAt, copyKey)
                                copySaid = why ?: ("installing " + b.latest + " and restarting" +
                                                   " - it will come back on its own")
                                if (why != null) copyBusy = false
                                else {
                                    kotlinx.coroutines.delay(20_000)
                                    copyBuild = Api.serverBuildAt(copyAt, copyKey)
                                    copySaid = copyBuild?.let { "now " + it.have }
                                    copyBusy = false
                                }
                            }
                        }
                    }
                }
                Pill("Check") {
                    ctx.lifecycleScope.launch {
                        copySaid = "asking the site"
                        copyBuild = Api.serverBuildAt(copyAt, Api.token, fresh = true)
                        copySaid = null
                    }
                }
            }
        }
        SettingsRow("This app",
                    "Version " + BuildConfig.VERSION_NAME +
                    (if (skipped > 0) "  \u00b7  skipping " + skipped else "") +
                    (found?.let { "  \u00b7  " + it } ?: "")) {
            // Once a version has been found the same button installs it: learning
            // that one exists and then having to go and find it is two steps where
            // one will do.
            Pill(if (checking) "\u2026" else if (waiting != null) "Update" else "Check",
                 primary = waiting != null) {
                if (!checking) {
                    checking = true
                    ctx.lifecycleScope.launch {
                        val ready = waiting
                        if (ready != null) {
                            val held = Updates.waiting(ctx, ready)
                            val apk = held ?: Updates.download(ctx, ready) { p ->
                                found = "downloading " + p + "%"
                            }
                            checking = false
                            if (apk == null) {
                                found = "could not download it"
                            } else {
                                found = "installing " + ready.versionName
                                if (!Updates.installFile(ctx, apk)) {
                                    found = "no installer on this device"
                                }
                            }
                        } else {
                            val u = Updates.check(ignoreSkip = true, ctx = ctx)
                            checking = false
                            waiting = u
                            found = if (u == null) "up to date"
                                    else named(u) + " ready to install"
                        }
                    }
                }
            }
            // Skipping is about a version that is waiting: with nothing to skip the
            // button asks a question about nothing. It stays while a skip is in force,
            // because that is the only way back from one.
            if (waiting != null || skipped > 0)
            Pill(if (skipped > 0) "Unskip" else "Skip", active = skipped > 0) {
                skipped = if (skipped > 0) {
                    Updates.setSkipped(ctx, 0); Updates.skip = 0; 0
                } else {
                    val next = BuildConfig.VERSION_CODE + 1
                    Updates.setSkipped(ctx, next); Updates.skip = next; next
                }
            }
        }
        Spacer(Modifier.height(24.dp))
    }

    if (writing) {
        ReportDialog(startAs = if (reportTab == "requests") "request" else "problem") {
            writing = false
            ctx.lifecycleScope.launch { reports = Api.reports() }
        }
    }

    editing?.let { which ->
        SubtitleLookDialog("", which, ctx.lifecycleScope,
                           onChanged = { ctx.lifecycleScope.launch { reload() } },
                           onClose = { editing = null })
    }
}

/** A section heading: small, quiet, and not a headline. */
@Composable
private fun SettingsHeading(text: String) {
    Text(text, color = Skin.Dim, fontSize = 10.5.sp, letterSpacing = 1.3.sp,
         fontWeight = FontWeight.SemiBold,
         modifier = Modifier.padding(top = 18.dp, bottom = 2.dp))
}

/**
 * One setting: what it is, what it currently says, and the control that changes it.
 *
 * Deliberately compact - these were sized for a phone held at arm's length and looked
 * enormous on a television, where the same row is read from three metres away but has
 * ten times the width to do it in.
 */
@Composable
private fun SettingsRow(title: String, value: String, accent: Boolean = false,
                        // a second line inside the same box, for a setting whose
                        // choices do not fit beside its name
                        extra: (@Composable () -> Unit)? = null,
                        // nothing beside the name: a setting whose choices are all on
                        // the line below has no control to put there
                        control: @Composable RowScope.() -> Unit = {}) {
    Column(Modifier.fillMaxWidth().padding(top = 6.dp)
               .background(Skin.Panel, RoundedCornerShape(8.dp))
               .padding(start = 12.dp, end = 8.dp, top = 8.dp, bottom = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, color = if (accent) Skin.Accent else Skin.Fg, fontSize = 14.sp,
                     fontWeight = FontWeight.Medium)
                if (value.isNotEmpty()) {
                    Text(value, color = Skin.Dim, fontSize = 11.5.sp, maxLines = 1,
                         overflow = TextOverflow.Ellipsis)
                }
            }
            control()
        }
        extra?.let { Spacer(Modifier.height(8.dp)); it() }
    }
}

/**
 * Who may watch this library from outside, and the links that let them.
 *
 * Creating one and sending it are the same gesture here: on a phone the share sheet is
 * how anything reaches a person, so a new invitation goes straight into it. The row of
 * names is only a shortcut - it saves typing the same ten people every time.
 */
@Composable
private fun PeopleScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current as AppCompatActivity
    var people by remember { mutableStateOf<List<Api.Invite>>(emptyList()) }
    var busy by remember { mutableStateOf(true) }
    var typed by remember { mutableStateOf("") }

    suspend fun reload() { people = Api.invites() ?: emptyList(); busy = false }
    LaunchedEffect(Unit) { reload() }

    fun share(who: Api.Invite) {
        // One short line and the link. WhatsApp keeps them in one bubble and draws
        // the preview underneath; Messenger pulls any shared text apart to make its
        // own preview, which no wording can prevent. No subject line, which would be
        // a message of its own in both. Under 160 characters, so an SMS holds it.
        val text = who.name + ", your key to my film library:\n" + who.link
        ctx.startActivity(Intent.createChooser(
            Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, text)
            }, "Send " + who.name + " their link"))
    }

    fun invite(name: String) {
        if (name.isBlank()) return
        busy = true
        ctx.lifecycleScope.launch {
            val made = Api.createInvite(name.trim())
            reload()
            made?.let { share(it) }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())
               .padding(horizontal = 24.dp, vertical = 20.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill("Back") { onBack() }
            Text("Invite", color = Skin.Fg, fontSize = 22.sp, fontWeight = FontWeight.SemiBold,
                 modifier = Modifier.padding(start = 12.dp))
        }
        Text("Each person gets their own link, which can be revoked on its own.",
             color = Skin.Dim, fontSize = 13.sp, modifier = Modifier.padding(top = 10.dp))

        Row(Modifier.padding(top = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            TextBox(typed, "Someone else", Modifier.weight(1f)) { typed = it }
            Spacer(Modifier.width(8.dp))
            Pill("Invite", primary = true) { invite(typed); typed = "" }
        }

        Text("INVITED", color = Skin.Dim, fontSize = 11.sp, letterSpacing = 1.2.sp,
             fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 22.dp, bottom = 4.dp))
        if (busy) {
            Text("\u2026", color = Skin.Dim, fontSize = 14.sp)
        } else if (people.isEmpty()) {
            Text("Nobody yet.", color = Skin.Dim, fontSize = 14.sp)
        }
        people.forEach { who ->
            Column(Modifier.fillMaxWidth().padding(top = 8.dp)
                       .background(Skin.Panel, RoundedCornerShape(10.dp))
                       .padding(horizontal = 14.dp, vertical = 12.dp)) {
                Text(who.name, color = Skin.Fg, fontSize = 16.sp, fontWeight = FontWeight.Medium)
                Text(listOfNotNull(
                        if (who.lastSeen > 0) "last watched " +
                            java.text.SimpleDateFormat("d MMM HH:mm",
                                java.util.Locale.getDefault())
                                .format(java.util.Date(who.lastSeen * 1000))
                        else "not used yet",
                        // and which build they were on, as their app last said
                        who.app.takeIf { it.isNotEmpty() }?.let { "on " + it },
                     ).joinToString("  ·  "),
                     color = Skin.Dim, fontSize = 12.sp)
                Row(Modifier.padding(top = 10.dp)) {
                    Pill("Send again") { share(who) }
                    Pill("Revoke") {
                        busy = true
                        ctx.lifecycleScope.launch { Api.revokeInvite(who.token); reload() }
                    }
                }
            }
        }
        Spacer(Modifier.height(30.dp))
    }
}

/**
 * The servers this screen can reach, under the name of the one it is on.
 *
 * Each is knocked on as the list opens - one that is off should not be offered as
 * though it were there - and pressing one moves the whole app to it, by whichever of
 * its addresses answers from where this screen is.
 */
@Composable
private fun WhichServer(onDone: () -> Unit) {
    val ctx = LocalContext.current as AppCompatActivity
    // Read when the list opens, not once for the life of the screen. A machine gets
    // re-filed under whichever of its addresses answers, and a list remembered from
    // before that happened shows a server under the wrong heading while the settings
    // page - which reads it fresh - says the other thing.
    // Read again while the list is open, not once when it opened. A machine that
    // was switched off and came back was not there until the app was started again:
    // the list and the knocking on it were both taken once, and nothing since then
    // could put a machine back on the screen.
    var list by remember { mutableStateOf(listOf<Server>()) }
    var alive by remember { mutableStateOf<Map<String, Boolean>>(emptyMap()) }
    LaunchedEffect(Unit) {
        while (true) {
            list = Servers.folded(Servers.all(ctx))
                .sortedBy { it.name.ifBlank { Servers.hostOf(it.base) }.lowercase() }
            alive = list.associate { it.base to Api.answering(it.base, it.token) }
            kotlinx.coroutines.delay(10_000)
        }
    }
    Column(Modifier.padding(top = 6.dp, bottom = 2.dp)) {
        list.forEach { srv ->
            val on = srv.base.trimEnd('/') == Api.base
            val up = alive[srv.base]
            var lit by remember { mutableStateOf(false) }
            // Clipped before the press is drawn into it, and ringed while the remote
            // is on it. The rounded panel was painted after the press, so whatever
            // the press drew was a square laid over rounded corners - and which chip
            // the remote was on was left to the system, which on a television shows
            // almost nothing.
            val chip = RoundedCornerShape(8.dp)
            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .onFocusChanged { lit = it.hasFocus || it.isFocused }
                    .clip(chip)
                    .border(if (lit) 2.dp else 0.dp,
                            if (lit) Color.White else Color.Transparent, chip)
                    .clickable {
                        if (on) onDone() else ctx.lifecycleScope.launch {
                            val door = Servers.doorThatOpens(srv)
                            Servers.use(ctx, if (door == srv.base) srv
                                             else srv.copy(base = door,
                                                           outside = srv.base))
                            // Servers.use has already pointed the app at it; asking
                            // for "the current one" again resolved by address, and
                            // an address that changed at the door is not in the list
                            // yet - which pointed the whole app at the wrong machine
                            // and drew an empty library.
                            // the screen is about to be rebuilt from nothing, so say
                            // where it went before it goes
                            android.widget.Toast.makeText(
                                ctx, "Now on " +
                                     srv.name.ifBlank { Servers.hostOf(srv.base) } +
                                     // it is being opened, so it is not standing by
                                     "",
                                android.widget.Toast.LENGTH_SHORT).show()
                            onDone()
                            ctx.recreate()
                        }
                    }
                    .background(if (lit) Skin.Panel else Color.Transparent, chip)
                    .padding(horizontal = 8.dp, vertical = 5.dp)) {
                Box(Modifier.size(6.dp).background(
                        when (up) {
                            true -> Color(0xFF42C96A)
                            false -> Color(0xFF8A4038)
                            else -> Skin.Dim
                        }, CircleShape))
                Text(srv.name.ifBlank { Servers.hostOf(srv.base) },
                     color = if (on || lit) Skin.Fg else Skin.Dim, fontSize = 12.sp,
                     modifier = Modifier.padding(start = 8.dp))
                // Cache is what a machine is doing, not what it is: the one
                // being watched is not standing by for anything.
                if (srv.copyOf.isNotEmpty() && !on) {
                    Text("cache", color = Skin.Dim, fontSize = 10.sp,
                         modifier = Modifier.padding(start = 6.dp))
                }
                // Which way in this row is using, and a press to change it. The
                // app picks whichever answers and mostly that is right - but when it
                // is not, the person looking at the screen knows which network they
                // are on and the machine does not.
                listOf("lan", "wan").forEach { want ->
                    val using = Servers.whereKind(srv.base) == want
                    val other = srv.outside.trimEnd('/')
                    val canSwap = other.isNotEmpty() &&
                        Servers.whereKind(other) == want
                    Text(want,
                         color = if (using) Skin.Accent
                                 else if (canSwap) Skin.Dim else Skin.Line,
                         fontSize = 10.sp,
                         modifier = Modifier
                             .padding(start = 6.dp)
                             .then(if (using || !canSwap) Modifier
                                   else Modifier.clickable {
                                       ctx.lifecycleScope.launch {
                                           Servers.use(ctx, srv.copy(
                                               base = other, outside = srv.base))
                                           onDone()
                                           ctx.recreate()
                                       }
                                   }))
                }

            }
        }
    }
}

@Composable
private fun TopBar(tab: String, onTab: (String) -> Unit, onSettings: () -> Unit,
                   onChat: (() -> Unit)? = null,
                   onPeople: (() -> Unit)? = null,
                   onSetup: (() -> Unit)? = null,
                   tabFocus: Map<String, FocusRequester> = emptyMap(),
                   //: filled in as the row is laid out: where each tab sits across it
                   tabAt: MutableMap<String, Float>? = null,
                   picks: (@Composable RowScope.() -> Unit)? = null,
                   search: (@Composable RowScope.() -> Unit)? = null,
                   filters: (@Composable RowScope.() -> Unit)? = null) {
    // Everything stays where it is on every tab. Settings used to be dropped on
    // Films and TV to make room for the sort control - so a button moved and then
    // vanished depending on which shelf was open, which is worse than a crowded row:
    // the row scrolls, and a button that is off to the right is still where it was
    // last time. Nothing is hidden to make room for anything.
    val housekeeping = true
    val wide = onTv() ||
        LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE
    // Two rows, on every screen. The name and the cast button take the first; the
    // tabs and everything to do with what is being looked at take the second.
    //
    // One line was tried on the wider screens and it was always a squeeze: a phone on
    // its side lost the sort control off the end, and a television - which looks
    // roomy at 960dp until six tabs, a sort, a genre and a search box are put on the
    // same line as the name - lost the search box. A Row neither wraps nor scrolls,
    // so what does not fit is not merely off to the side; it is gone.
    Column(Modifier.fillMaxWidth()
        .background(Brush.verticalGradient(listOf(Color(0xFF12161C), Skin.Bg)))
        .padding(horizontal = 16.dp, vertical = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            // The version on a line of its own above the name, so the P and the word
            // keep the height and the spacing they had; the search box takes the width
            // that leaves - except on a short screen, where the two stand side by side
            // and smaller. On a television every point of height above the shelves is
            // a shelf that does not fit under them.
            val squat = LocalConfiguration.current.screenHeightDp < 560
            // and the name at the left with the picker after it: the word is what
            // says where you are, and it belongs where reading starts
            val holder: @Composable (@Composable () -> Unit) -> Unit =
                if (squat) { inner -> Row(verticalAlignment = Alignment.CenterVertically,
                                          content = { inner() }) }
                else { inner -> Column(content = { inner() }) }
            holder {
                Row(verticalAlignment = Alignment.CenterVertically,
                    modifier = if (squat) Modifier.padding(end = 12.dp) else Modifier) {
                    Text("P", color = Skin.Accent, fontSize = if (squat) 16.sp else 22.sp,
                         fontFamily = FontFamily.Serif, fontWeight = FontWeight.SemiBold)
                    Text("PALLADIUM", color = Skin.Fg,
                         fontSize = if (squat) 10.sp else 13.sp,
                         letterSpacing = if (squat) 2.sp else 2.5.sp,
                         modifier = Modifier.padding(start = if (squat) 6.dp else 8.dp))
                }
                // Which machine this shelf came off, beside the version. With two
                // servers in the list and a film that can be on either, the question
                // "where am I" was answered only by going back to the server list.
                val ctx = LocalContext.current
                // by address, not by what was chosen: after a film moves house the
                // app is on the cache without anybody having picked it
                // by either of a machine's addresses: a film that carried on from
                // the cache was reached by whichever way answered, and matching only
                // the address a row was filed under left the bar naming the machine
                // that had gone off
                val here = remember(Api.base) { Servers.inUse(ctx) }
                // and a press away from every other one: moving used to mean going
                // back to the server list and finding it there
                var picking by remember { mutableStateOf(false) }
                var lit by remember { mutableStateOf(false) }
                // Clipped before the press is drawn into it, as the rows below are.
                // The rounded panel was painted after the press, so the press drew a
                // square over a rounded chip - and the picker showed two grey boxes
                // with different corners, one above the other.
                val pill = RoundedCornerShape(999.dp)
                Row(verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier
                        .onFocusChanged { lit = it.hasFocus || it.isFocused }
                        // Not until the screen has given the focus to a tab. This is
                        // the first focusable thing in the layout, and the framework
                        // hands the first focus to the first thing it finds - so the
                        // ring appeared here on every start and was moved off a moment
                        // later. It cannot be first if it cannot be focused.
                        .focusProperties { canFocus = MainActivity.focusLanded.value }
                        .clip(pill)
                        .border(if (lit) 2.dp else 0.dp,
                                if (lit) Color.White else Color.Transparent, pill)
                        .clickable { picking = !picking }
                        .background(if (lit) Skin.Panel else Color.Transparent, pill)
                        // a thumb is not a remote: the words are small on purpose,
                        // so the thing being pressed has to be bigger than they are
                        .heightIn(min = 32.dp)
                        .padding(horizontal = 10.dp, vertical = 6.dp)) {
                    Text(BuildConfig.VERSION_NAME, color = Skin.Dim, fontSize = 10.sp)
                    here?.name?.takeIf { it.isNotBlank() }?.let {
                        Box(Modifier.padding(start = 7.dp).size(5.dp)
                                .background(
                                    if (here.copyOf.isNotEmpty()) Skin.Dim
                                    else Color(0xFF42C96A), CircleShape))
                        // no tag on the machine in use - this is that machine
                        Text(it,
                             color = if (lit) Skin.Fg else Skin.Dim, fontSize = 10.sp,
                             modifier = Modifier.padding(start = 5.dp))
                        Text(if (picking) "  ▴" else "  ▾", color = Skin.Dim,
                             fontSize = 10.sp)
                    }
                }
                if (picking) WhichServer { picking = false }
            }
            // Beside the name rather than at the end of the tabs: the row below is
            // full on a phone, and searching is the one thing done from any of the
            // three places a library is looked at.
            search?.let { Spacer(Modifier.width(14.dp)); it() }
            // television or phone on its side: the download line beside search, not a row of its own
            if (wide) { Spacer(Modifier.width(10.dp)); DownloadLine(inline = true) }
            Spacer(Modifier.weight(1f))
            CastButton()
        }
        run {
            val along = rememberScrollState()
            Box(Modifier.padding(top = 8.dp).fillMaxWidth()) {
                Row(Modifier.fillMaxWidth().horizontalScroll(along),
                    verticalAlignment = Alignment.CenterVertically) {
                    Tabs(tab, onTab, tabFocus, picks, where = tabAt)
                    filters?.let { Spacer(Modifier.width(8.dp)); it() }
                    if (housekeeping) {
                        Spacer(Modifier.width(10.dp))
                        onSetup?.let { Pill("Settings") { it() } }
                    }
                    // room for the arrow to sit over without covering a pill
                    if (along.maxValue > 0) Spacer(Modifier.width(22.dp))
                }
                // There is more of this row than fits, and nothing said so - which is
                // how the sort control came to be invisible rather than merely off to
                // the right. The arrow goes as soon as the row is moved.
                if (along.maxValue > 0 && along.value == 0) {
                    Box(Modifier.align(Alignment.CenterEnd)
                            .background(Brush.horizontalGradient(
                                listOf(Color(0x00000000), Skin.Bg, Skin.Bg)))
                            .padding(start = 18.dp, end = 2.dp)) {
                        Text("›", color = Skin.Accent, fontSize = 20.sp)
                    }
                }
            }
        }
        // up from the download pill goes to the current tab; with no target it had nowhere to go
        if (!wide) DownloadLine(up = tabFocus[tab] ?: tabFocus["home"])
    }
}

@Composable
private fun RowScope.Tabs(tab: String, onTab: (String) -> Unit,
                          focus: Map<String, FocusRequester> = emptyMap(),
                          picks: (@Composable RowScope.() -> Unit)? = null,
                          below: FocusRequester? = null,
                          /* where each of them ended up across the screen, so a card
                             below can go to the one standing over it rather than to
                             whatever the framework finds up and to the left */
                          where: MutableMap<String, Float>? = null) {
    /** Each tab can be asked for the focus by name - back, from a scrolled list. */
    // A phone has no room for six full-width pills and the two controls that go with
    // them; a television has the room and the remote, which wants the bigger target.
    // A narrow phone - 360 points across, which most of them are, against the 411 a
    // Pixel gives - has no room even for the narrow ones, and the genre hung off the
    // right of the screen.
    val tight = !onTv()
    val squeeze = tight && LocalConfiguration.current.screenWidthDp < 400
    @Composable
    fun tabPill(id: String, label: String) {
        val f = focus[id]
        // Down goes straight down, into whatever is under this button - the same as
        // the sort, the genre and the search box beside it. Naming one poster for the
        // whole row instead sent every tab to the first one, so two buttons on one
        // line did two different things.
        Pill(label, tab == id, narrow = tight, small = squeeze,
             modifier = (if (f == null) Modifier else Modifier.focusRequester(f))
                 .then(if (where == null) Modifier else Modifier.onGloballyPositioned {
                     where[id] = it.boundsInWindow().center.x
                 })) {
            onTab(id)
        }
    }
    tabPill("home", "Home")
    tabPill("films", "Films")
    tabPill("tv", "TV")
    // fourth and fifth along, while a list is open: the order it is in and which shelf
    // of it. At the end of the row they were off the side of a phone.
    picks?.let { it() }
    tabPill("watchlist", "Watchlist")
    tabPill("collections", "Collections")
    // Not while looking through the library: Films and TV carry a sort, a genre and a
    // search box on the same line, and nobody goes looking for the noticeboard
    // halfway down a list of films. It is on Home, where it belongs.
    if (tab != "films" && tab != "tv") tabPill("reports", "Reports")
}

/** Something downloading: what, how far, how fast, how long and who asked, on a line of
 *  its own under the tabs. At the end of them it was off the side of a phone. */
@Composable
private fun DownloadLine(up: FocusRequester? = null, inline: Boolean = false) {
    val coming = Api.downloading.value          // refreshed app-wide
    val d = coming.firstOrNull() ?: return
    val more = coming.size - 1
    // progress and ETA first, title after: the pill clips at its edge, and beside search on a
    // television the title took the width and the numbers were cut off
    val state = if (d.offerState == "queued")
                    "queued" + (if (d.offerPlace > 0) " · ${d.offerPlace} ahead" else "")
                else "${(d.offerProgress * 100).toInt()}%" +
                     (if (d.offerEta >= 0) " · " + etaShort(d.offerEta) else "") +
                     (if (d.offerMbit > 0)
                          String.format(java.util.Locale.US, " · %.1f Mbit/s", d.offerMbit)
                      else "")
    val title = if (inline) d.title.take(24) else d.title
    Row(Modifier.padding(top = if (inline) 0.dp else 6.dp)) {
        Pill("⤓ " + state + "  " + title +
                 (if (!inline && d.offerWho.isNotEmpty()) " · " + d.offerWho else "") +
                 (if (more > 0) "  +$more" else ""),
             filled = true, small = true,
             modifier = Modifier.edge(up = { moveTo(up) })) {
            Api.openWanted.value = d
        }
    }
}

/**
 * The system cast chooser.
 *
 * Defensive on purpose: this is a view from another framework that wants its own theme,
 * Play services present and the cast context already built. Any of those missing used to
 * take the whole screen down, so a failure now just means no button. It is also told to
 * stay visible - by default it hides until a receiver answers, which looks like a dead
 * control rather than an empty network.
 */
@Composable
private fun CastButton() {
    val ctx = LocalContext.current
    if (!Cast.available(ctx)) return
    AndroidView(
        modifier = Modifier.padding(end = 8.dp).size(44.dp),
        factory = { c ->
            runCatching {
                val themed = android.view.ContextThemeWrapper(
                    c, androidx.appcompat.R.style.Theme_AppCompat_NoActionBar)
                MediaRouteButton(themed).also {
                    CastButtonFactory.setUpMediaRouteButton(themed, it)
                    it.setAlwaysVisible(true)
                }
            }.getOrElse { android.view.View(c) }
        },
    )
}

/** How many titles are fetched at a time; the rest follow as the grid is scrolled. */
private const val PAGE = 120

/**
 * What the browsing screen is showing, kept outside it.
 *
 * Held by App rather than by HomeScreen so that opening a title does not destroy it:
 * the list, the tab, the sort and the scroll position are all still here when the
 * detail screen closes. The scroll position in particular has to be the same object -
 * a new LazyGridState always starts at the top, however good the data is.
 */
/** The filters and the order the shelves and the tabs share, kept while a row opened
 *  whole borrows those fields for its own. */
private data class Filters(
    val genre: String, val decade: String,
    val genres: List<Pair<String, Int>>, val decades: List<Pair<String, Int>>,
    val sortKey: String, val sortAsc: Boolean)

private class Browse {
    var tab by mutableStateOf("home")
    /** Where each card on the screen actually is, across it, by the title it shows.
     *
     * Worked out from the list and the item's offset within it, it came out short by
     * the list's own padding, which is enough to put a card nearer the tab to its left
     * than the one standing over it. Measured where it is drawn instead. Not state
     * anything is drawn from: written while the cards are laid out and read when a key
     * is pressed. */
    val cardAt = mutableMapOf<String, Float>()
    /** Where the remote was on each shelf, and how to ask for it back.
     *
     * A press of down looks for whatever lies nearest below, and shelves scroll on
     * their own - so from the left of one shelf it landed on a title well to the
     * right of the next, and the press after that came back left. Each shelf keeps
     * the card the remote was last on; up and down go to that, or to the first card
     * of a shelf nobody has been on. */
    val shelfWas = mutableMapOf<Int, String>()
    val shelfAsk = mutableMapOf<Int, FocusRequester>()
    /** Which column the remote is in, so down and up stay in it. */
    var column by mutableStateOf(0)
    /** Which shelf it is on, and where it is in the grid: what back steps back
     *  through - along the row to its first card, then out to the tabs. */
    var rowNow by mutableStateOf(0)
    /** A row opened whole, by its name: the page shows everything it holds. */
    var moreRow by mutableStateOf<String?>(null)
    /** Everything that shelf holds, before the sort and the filters are applied. */
    var moreAll by mutableStateOf<List<Media>>(emptyList())
    /** The filters and the order as they were before a row was opened whole. That
     *  row sets its own and shares these fields with the tabs, so leaving it used to
     *  carry its category onto the library and its order onto the collections. */
    var moreWas: Filters? = null
    /** The title whose cast opened this shelf. Back belongs to that page, not to the
     *  front page: somebody who pressed a name was reading about a film and wants to
     *  carry on reading about it. */
    var personFrom by mutableStateOf<Media?>(null)

    /** Leave a row opened whole, putting back the filters and the order it borrowed.
     *  This row sets its own in fields the tabs read as well, so a category picked
     *  here opened the library filtered by it with nothing on the page saying why. */
    fun leaveMoreRow() {
        moreRow = null
        grid = emptyList()
        moreWas?.let {
            genre = it.genre; decade = it.decade
            genres = it.genres; decades = it.decades
            collSortKey = it.sortKey; collSortAsc = it.sortAsc
        }
        moreWas = null
        moreAll = emptyList()
        personFrom = null
    }
    /** How many times a title's page has been left. What is under it does not change
     *  while it is open, so nothing else says the list is being looked at again. */
    var cameBack by mutableStateOf(0)
    /** Whether the shelves have been shown once already this run. */
    var opened by mutableStateOf(false)
    var gridAt by mutableStateOf(0)
    var rows by mutableStateOf<List<Pair<String, List<Media>>>>(emptyList())
    var grid by mutableStateOf<List<Media>>(emptyList())
    /** What stands behind the shelves: the last title opened, or failing that
     *  whatever is newest in Continue watching - the film somebody stopped. */
    var backdrop by mutableStateOf<Media?>(null)
    var more by mutableStateOf(false)          // another page is waiting
    /** titles matching several marked genres, for the genre button; -1 when not counted */
    var matching by mutableStateOf(-1)
    var loading by mutableStateOf(true)
    var failed by mutableStateOf<String?>(null)
    // opens on what came out last: "what is new in the world" is a better first
    // question than "what did this server notice"
    /**
     * Only what is held, on a shelf opened off recently added.
     *
     * Not a rule about the order: what a pack can fetch and what can be asked for
     * stand on the film shelf in every order, as Settings says. This is the one shelf
     * that is about arriving here, so it is the one that leaves them out - and it
     * ends when the tab is opened from the bar rather than from that shelf.
     */
    var diskOnly by mutableStateOf(false)
    var sortKey by mutableStateOf("originallyAvailableAt")
    var sortAsc by mutableStateOf(false)
    var genre by mutableStateOf("")            // "" is everything
    var genres by mutableStateOf<List<Pair<String, Int>>>(emptyList())
    var decade by mutableStateOf("")           // comma-joined; "" is every year

    /** the tab the genre and decade lists were last built for */
    var filtersFor by mutableStateOf("")
    var decades by mutableStateOf<List<Pair<String, Int>>>(emptyList())
    var query by mutableStateOf("")
    var loaded by mutableStateOf("")           // which tab and order the lists hold
    var update by mutableStateOf<Updates.Available?>(null)
    var checkedUpdate = false                  // once a launch, not once a screen
    /** the server build this app has been told about, and the one it last saw */
    var serverNow by mutableStateOf<String?>(null)
    var serverWas: String? = null
    /** a line sent from the server, and the last one that was shown */
    var notice by mutableStateOf<String?>(null)
    var noticeAsked = false                    // the first ask learns where we are
    /** the collection being looked into, or null for the shelf of shelves */
    var collectionOn by mutableStateOf<Media?>(null)

    /** whether Play on a shelf draws from its round rather than reading it in order */
    var shuffled by mutableStateOf(false)
    // a collection reads in release order, oldest first - a series is watched from
    // its beginning - and keeps that apart from the order the library is browsed in
    var collSortKey by mutableStateOf("originallyAvailableAt")
    var collSortAsc by mutableStateOf(true)
    /** the poster being held, and what is offered for it */
    var held by mutableStateOf<Media?>(null)
    /** the favorites, in a row of their own under the watchlist */
    var favs by mutableStateOf<List<Media>>(emptyList())
    /** a watchlist poster being held: favorite it or not */
    var favHeld by mutableStateOf<Media?>(null)
    /** the poster a title was opened from, so back returns the grid to it */
    var openedKey by mutableStateOf("")
    /** and where the grid was scrolled to then: first row shown, and how far into it */
    var openedAt: Pair<Int, Int>? = null
    /** the last return to the front already answered by going Home */
    var returnSeen = 0
    val gridState = androidx.compose.foundation.lazy.grid.LazyGridState()
    val rowsState = androidx.compose.foundation.lazy.LazyListState()
    /** home row a title was opened from */
    var openedRow = ""
    /** horizontal scroll per home row, kept across the title page */
    val rowStates = HashMap<String, androidx.compose.foundation.lazy.LazyListState>()
    /** what each tab was last showing, so arriving at one is not a blank page
     *
     * The grid used to be emptied on the way out, which drew the tab being opened as
     * nothing until its list arrived - and an empty list puts the scroll state back
     * to the top, which is the jump. Each tab keeps what it had; the fetch replaces
     * it in place a moment later.
     */
    val grids = HashMap<String, List<Media>>()
    /** where each tab was left, for a grid state that has not been made yet */
    val tabAt = HashMap<String, Pair<Int, Int>>()
    /** the tab whose place has already been given back, so it is not done twice */
    var tabPut = ""
    /**
     * One scroll state per tab, made already standing where that tab was left.
     *
     * Scrolling it after the list is drawn shows the top of the list for a frame and
     * then jumps - the place has to be in the state before anything is laid out, and a
     * LazyGridState can only be given one when it is made. So each tab keeps its own
     * rather than sharing one and being moved about afterwards.
     */
    val gridStates = HashMap<String, androidx.compose.foundation.lazy.grid.LazyGridState>()

    fun gridFor(tab: String): androidx.compose.foundation.lazy.grid.LazyGridState =
        gridStates.getOrPut(tab) {
            val was = tabAt[tab]
            androidx.compose.foundation.lazy.grid.LazyGridState(was?.first ?: 0,
                                                               was?.second ?: 0)
        }
    /** poster to focus once the list is drawn again after back; cleared when used */
    var focusKey by mutableStateOf("")
    /** whether the remote has been put somewhere once already.
     *
     * This screen leaves the composition while a title's page is open and comes back
     * when it closes, so an effect keyed on Unit runs again on every return - and the
     * one that places the remote at the start dragged it to Home 350 ms after back had
     * put it on the poster it came from. */
    var landed = false
    /** the build whose offer has already been given the remote once */
    var offerTaken = 0
    /** whether the search box has the cursor.
     *
     * A typed letter narrows the list, which reloads it, which changes browse.loaded -
     * and the effect that puts the remote on the tab row fired on that change and took
     * the cursor out of the box. One letter and the keyboard shut. */
    var typing by mutableStateOf(false)
}

// FocusRequester.Cancel - refusing a direction - is still marked experimental
/** What to call a build on offer: its version, and its number when that is all that
 *  separates it from the one installed. */
private fun named(u: Updates.Available): String =
    u.versionName + (if (u.versionName == BuildConfig.VERSION_NAME)
                         " (" + u.versionCode + ")" else "")

/** The row that resumes rather than opens. */
private const val DECK = "Continue watching"

/** How many of a shelf are drawn on it; the rest are behind Show more. */
private const val SHELF_SHOWS = 10

/**
 * How far in something has to be before it counts as started, in seconds.
 *
 * The same number the shelf of what to carry on with uses: below it a row is a tick
 * left by a player that reported once, not a viewing. Offering Resume at five seconds
 * while the shelf dropped the same film at thirty said two different things about one
 * film.
 */
private const val RESUME_FROM = 30

@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
private fun HomeScreen(browse: Browse, onOpen: (Media) -> Unit, onSettings: () -> Unit,
                       onPeople: () -> Unit, onPrefs: () -> Unit,
                       onReports: () -> Unit) {
    val ctx = LocalContext.current
    // Checked once per launch and remembered on the holder. It used to run every time
    // this screen was entered, so crossing between Home, Films and TV re-raised the
    // banner - and dismissing it was impossible because it came straight back.
    LaunchedEffect(Unit) {
        if (!browse.checkedUpdate) {
            browse.checkedUpdate = true
            browse.update = Updates.check(ctx = ctx)
        }
    }
    // The server can be replaced while the app is open - it takes about ten seconds -
    // and nothing said so. Asked on the way in and whenever the app comes back to the
    // front; only from the same house, because a guest has no business being told the
    // owner restarted something.
    LaunchedEffect(MainActivity.returned.value) {
        val said = Api.serverNow() ?: return@LaunchedEffect
        if (!said.lan) return@LaunchedEffect
        val was = browse.serverWas
        browse.serverWas = said.version
        // Once per version, not once per visit. What was remembered lived in memory,
        // so every return to the app was a first look and every one of them said the
        // same thing again - which on an evening of several builds is a banner every
        // time the app opens.
        val notes = ctx.getSharedPreferences("notes", 0)
        val told = notes.getString("serverTold", "") ?: ""
        if (was != null && was != said.version && told != said.version) {
            notes.edit().putString("serverTold", said.version).apply()
            // An app behind the server it is talking to wants the way to catch up,
            // not a note that something changed. Updates.check answers only when the
            // build on offer is newer than this one, so when it answers the install
            // banner stands in for the note - and when it does not, the note stands.
            val offer = Updates.check(ctx = ctx)
            if (offer != null) {
                browse.checkedUpdate = true
                browse.update = offer
            } else {
                browse.serverNow = said.version
            }
        }
    }
    // and a word from the server itself, asked for while the library is on screen:
    // the owner saying something to the room rather than to a machine
    LaunchedEffect(Unit) {
        // The first ask is a plain one, to learn which notice the server is on; after
        // that the connection is held open and the answer arrives when somebody
        // writes. A server that cannot be reached is tried again in ten seconds.
        while (true) {
            val word = Api.notice(MainActivity.noticeSeen.value,
                                  if (browse.noticeAsked) 45 else 0)
            browse.noticeAsked = true
            if (word != null && word.id != MainActivity.noticeSeen.value) {
                MainActivity.noticeSeen.value = word.id
                // and no words takes it off the screen
                browse.notice = word.text.ifEmpty { null }
                // and a title to open, sent from the server: the one thing a screen
                // in the house could not be asked to do. Opened the same way as
                // pressing it here, so everything after it - the round, the place it
                // keeps, what plays next - is the same as if somebody had.
                if (word.play.isNotEmpty()) {
                    runCatching { Api.item(word.play) }.getOrNull()?.let { m ->
                        play(ctx, m, word.at)
                    }
                }
            } else if (word == null) {
                kotlinx.coroutines.delay(10_000)
            }
        }
    }
    val update = browse.update
    var paging by remember { mutableStateOf(false) }
    // the room, opened from the bar above the shelves
    var chatting by remember { mutableStateOf(false) }
    val order = browse.sortKey + if (browse.sortAsc) ":asc" else ":desc"
    // what the held lists correspond to; coming back from a title matches, and the
    // fetch is skipped rather than throwing the scroll position away
    val want = browse.tab + "|" + order + "|" + browse.diskOnly + "|" +
        browse.genre + "|" + browse.decade + "|" +
               browse.query.trim() + "|" +
        (browse.collectionOn?.ratingKey ?: "") +
        // the two shelves are what a mark changes, so they alone are refetched when
        // one is changed; a grid of films does not move because a series was marked
        (if (browse.tab == "watchlist")
             "|" + MainActivity.marksTouched.value else "")

    // which genres exist, for the tab in hand: asked once per tab
    // and again whenever a mark changes: the numbers are counted among what is
    // already marked, so they are only true for the marks that were sent
    LaunchedEffect(browse.tab, browse.genre, browse.decade) {
        // a mark that empties a list is still the mark somebody just made; a tab that
        // has never heard of it is another matter
        val tabChanged = browse.filtersFor != browse.tab
        browse.filtersFor = browse.tab
        browse.genres = when (browse.tab) {
            "films" -> Api.genres("movie", browse.genre, browse.decade)
            // the tab is called "tv"; asking for "shows" here matched nothing, so the
            // genre control on TV has been empty since the day it was added
            "tv" -> Api.genres("show", browse.genre, browse.decade)
            // a list's own genres are worked out from the list when it loads
            "watchlist", "collections" -> browse.genres
            else -> emptyList()
        }
        browse.decades = when (browse.tab) {
            "films" -> Api.decades("movie", browse.genre)
            "tv" -> Api.decades("show", browse.genre)
            "watchlist", "collections" -> browse.decades
            else -> emptyList()
        }
        // A decade the other tab has nothing from is not a decade to carry across.
        // Only when the tab itself changed: the lists are narrowed by what is marked
        // now, so a mark missing from one of them means the filter is empty, not that
        // the mark was never any good - and dropping it there would undo the press
        // that emptied it.
        if (browse.decade.isNotEmpty() && tabChanged) {
            browse.decade = browse.decade.split(",").map { it.trim() }.filter { era ->
                era.isNotEmpty() && browse.decades.any { it.first == era }
            }.joinToString(",")
        }
        // A genre chosen on one tab is not a genre on the other: films have Western
        // and television has Talk Show, and carrying one across showed an empty
        // library with no hint as to why. It is kept when the new tab has it too.
        if (browse.genre.isNotEmpty() && tabChanged) {
            browse.genre = browse.genre.split(",").filter { g ->
                g.isNotBlank() && browse.genres.any { it.first.equals(g, ignoreCase = true) }
            }.joinToString(",")
        }
    }

    // Coming back from a film lands on the homepage, drawn again. Whatever was on
    // screen when the film started is a page from before it: Continue watching still
    // says the place it was at, a shelf still says what it said when the draw was
    // made, and a shuffle that has moved on says nothing at all.
    // Arriving, everywhere it shows: a finished download leaves the live list, and its poster
    // kept the state it loaded with until the shelf was read again by hand. Each such title is
    // read on its own every 10 s and swapped in until it has become the library film.
    val shownOffers = (browse.grid + browse.favs + browse.rows.flatMap { it.second })
        .filter { m -> m.offered && m.offerState in setOf("queued", "downloading", "done") &&
                       Api.downloading.value.none { it.ratingKey == m.ratingKey } }
        .map { it.ratingKey }.distinct()
    LaunchedEffect(shownOffers.joinToString(",")) {
        while (shownOffers.isNotEmpty()) {
            kotlinx.coroutines.delay(10_000)
            for (key in shownOffers) {
                val old = (browse.grid + browse.favs + browse.rows.flatMap { it.second })
                    .firstOrNull { it.ratingKey == key } ?: continue
                val fresh = runCatching { Api.metadata(old) }.getOrNull() ?: continue
                if (fresh.ratingKey == old.ratingKey && fresh.offered == old.offered &&
                    fresh.offerState == old.offerState) continue
                val swap = { l: List<Media> -> l.map { if (it.ratingKey == key) fresh else it } }
                browse.grid = swap(browse.grid)
                browse.favs = swap(browse.favs)
                browse.rows = browse.rows.map { (title, list) -> title to swap(list) }
            }
        }
    }
    val cameBack = MainActivity.returned.value
    LaunchedEffect(cameBack) {
        // once for each time the app came to the front: this screen is drawn again
        // after every title page, and taking that for a return sent back from the
        // middle of Films to the top of Home
        if (cameBack > 0 && cameBack != browse.returnSeen) {
            browse.returnSeen = cameBack
            browse.collectionOn = null
            browse.tab = "home"
            // after the film has said where it got to. Both happen at once when
            // somebody backs out, and this read was winning - so the shelf was drawn
            // from what the server knew a moment before the film ended.
            kotlinx.coroutines.withTimeoutOrNull(2_000) {
                PlayerActivity.settling?.join()
            }
            PlayerActivity.settling = null
            // and read again. Setting the tab is what used to force this, so it only
            // happened when the film had been started from somewhere else - and a
            // film is nearly always started from here.
            browse.loaded = ""
        }
    }
    // Keyed on what was loaded as well as on what is wanted. Coming back from a film
    // clears the first to ask for a fresh read, and on the tab the film was started
    // from the second does not change - so nothing re-ran, the shelf kept the place
    // it had before the film, and pressing the poster started it from the beginning.
    // Going to another tab and back changed it twice, which is why that worked.
    LaunchedEffect(want, browse.loaded) {
        if (browse.loaded == want) return@LaunchedEffect
        browse.loading = true; browse.failed = null
        browse.matching = -1
        val tab = browse.tab
        // a list's genres and decades become its options, and it comes back narrowed
        // to the ones chosen - the controls Films and TV have, over a list of keys
        fun narrowed(all: List<Media>): List<Media> {
            browse.genres = all.flatMap { it.genres }.groupingBy { it }.eachCount()
                .toList().sortedBy { it.first.lowercase() }
            browse.decades = all.mapNotNull { m ->
                (m.year ?: 0).takeIf { it > 0 }?.let { (it / 10 * 10).toString() }
            }.groupingBy { it }.eachCount().toList().sortedByDescending { it.first }
            if (browse.genre.isNotEmpty()) {
                browse.genre = browse.genre.split(",").filter { g ->
                    g.isNotBlank() && browse.genres.any { it.first.equals(g, ignoreCase = true) }
                }.joinToString(",")
            }
            if (browse.decade.isNotEmpty()) {
                browse.decade = browse.decade.split(",").map { it.trim() }.filter { era ->
                    era.isNotEmpty() && browse.decades.any { it.first == era }
                }.joinToString(",")
            }
            return all.filter { m ->
                (browse.genre.isEmpty() || browse.genre.split(",").filter { it.isNotBlank() }.all { want -> m.genres.any { it.equals(want, ignoreCase = true) } }) &&
                // every genre marked, but any one of the decades: a film has one year
                (browse.decade.isEmpty() || browse.decade.split(",").map { it.trim() }
                     .any { it == ((m.year ?: 0) / 10 * 10).toString() })
            }
        }
        val query = browse.query
        try {
            when {
                // A search comes first, whichever tab is open: from Home it looks
                // through everything, since Home is not a section of the library.
                query.trim().length >= 2 -> {
                    val found = Api.search(ctx, query.trim())
                    browse.grid = when (tab) {
                        "films" -> found.filter { it.type == "movie" }
                        "tv" -> found.filter { it.type == "show" || it.type == "episode" }
                        else -> found
                    }
                    browse.more = false
                }
                // A shelf that could not be fetched is not an empty shelf. Both came
                // out as no rows, the row was dropped, and the page was then marked
                // loaded - so one slow answer as the app opens left the home screen
                // without Continue watching until another tab was opened and come
                // back from, which is the only thing that asks again.
                // All at once, not one after another. Each shelf is a request of its
                // own and they were asked in order, so the screen waited for the sum
                // of seven round trips - and for every retry of a slow one - before it
                // drew anything. Asked together it waits for the slowest.
                tab == "home" -> browse.rows = coroutineScope {
                    listOf<Pair<String, suspend () -> List<Media>>>(
                        DECK to { Api.onDeck(ctx) },
                        "Recently added films" to { Api.recentFilms(ctx) },
                        "Recently released films" to { Api.releasedFilms(ctx) },
                        "Recently added TV" to { Api.recentEpisodes(ctx) },
                        "Recently released series" to { Api.releasedShows(ctx) },
                    ).map { (name, get) ->
                        name to async { askAgain(get) }
                    }.map { (name, job) ->
                        name to job.await()
                    }.filter { it.second.isNotEmpty() }
                }
                tab == "watchlist" -> {
                    // marked for later, newest mark first: the order it was thought of
                    // in, which is not an order worth re-sorting
                    // favorites have a row of their own under the watchlist
                    val kept = runCatching { Api.favorites(ctx) }.getOrDefault(emptyList())
                    val keptKeys = kept.map { it.ratingKey }.toSet()
                    runCatching { Api.favored() }
                    browse.favs = narrowed(kept)
                    browse.grid = narrowed(Api.watchlist(ctx)).filter { it.ratingKey !in keptKeys }
                    browse.more = false
                }
                tab == "collections" -> {
                    // the shelves, or the one that has been opened
                    val open = browse.collectionOn
                    browse.grid = if (open == null) Api.collections(ctx)
                                  else narrowed(Api.collectionItems(open))
                    browse.more = false
                }
                tab == "films" -> {
                    browse.grid = Api.movies(ctx, order, genre = browse.genre,
                                             decade = browse.decade,
                                             diskOnly = browse.diskOnly)
                    // a machine that said nothing holds films that are not on this
                    // shelf, and the shelf is short rather than complete
                    if (Api.silent.isNotEmpty())
                        browse.failed = Api.silent + " did not answer - this is only part of the library"
                    browse.more = browse.grid.size >= PAGE
                    // the grid is one page; the count comes from the server's totalSize
                    if (browse.genre.contains(","))
                        browse.matching = Api.shelfCount(ctx, 1, browse.genre, browse.decade)
                }
                tab == "tv" -> {
                    browse.grid = Api.shows(ctx, order, genre = browse.genre,
                                            decade = browse.decade,
                                            diskOnly = browse.diskOnly)
                    if (Api.silent.isNotEmpty())
                        browse.failed = Api.silent + " did not answer - this is only part of the library"
                    browse.more = browse.grid.size >= PAGE
                    if (browse.genre.contains(","))
                        browse.matching = Api.shelfCount(ctx, 2, browse.genre, browse.decade)
                }
            }
            // watchlists and collections are whole lists, filtered here: their count is the list
            if (browse.genre.contains(",") && (tab == "watchlist" || tab == "collections"))
                browse.matching = browse.grid.size + (if (tab == "watchlist") browse.favs.size else 0)
            browse.loaded = want
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            // another letter was typed and this fetch was dropped for the next one.
            // Not a failure, and saying so put "the coroutine scope left the
            // composition" on the screen for every keystroke.
            throw stopped
        } catch (e: Exception) {
            browse.failed = e.message ?: "could not reach the server"
        }
        browse.loading = false
    }

    // Handing out invitations is the owner's business, and the server decides who that
    // is; the button only appears where it would work.
    var owner by remember { mutableStateOf(false) }
    LaunchedEffect(Api.base) {
        owner = Api.invites() != null
        // which machine each stored address belongs to: two addresses for this server
        // must not put every film on the screen twice
        runCatching { Servers.identify(ctx) }
        // and once each machine has said which machine it is, rows that turn out to
        // be the same one are folded together and written down that way
        runCatching { Servers.tidy(ctx) }
        // and what colour this server draws with - the same one the browser and the
        // pages the server puts up use, chosen once on the settings page
        runCatching { Api.accent() }.getOrNull()?.let { Skin.paint(it) }
        // and what this screen's sound can take without being re-encoded, asked of
        // the whole chain - a streamer with no decoder of its own still passes DTS
        // to a receiver that has one
        runCatching { Api.takesDts(ctx) }
    }

    // one per tab, kept for the life of this screen: back needs to hand the focus to
    // the tab it came from, and a requester has to be the same object to work
    val tabFocus = remember {
        listOf("home", "films", "tv", "watchlist", "collections", "reports")
            .associateWith { FocusRequester() }
    }
    //: where each of those ended up across the screen. A card at the top of a list
    //: goes to the button standing over it - which is what "up" means to anybody
    //: looking at it - and the framework's own search cannot be asked for that: it
    //: takes the nearest thing up and to the left, which is the server picker.
    val tabAt = remember { mutableStateMapOf<String, Float>() }

    /** The tab standing over this point across the screen, if any of them is. */
    fun tabOver(x: Float): FocusRequester? {
        // only the ones that can be asked for by name: a button with no requester is
        // not an answer, and answering with nothing sends the press back to the
        // framework, which finds the server picker
        val near = tabAt.entries.filter { tabFocus.containsKey(it.key) }
            .minByOrNull { kotlin.math.abs(it.value - x) } ?: return null
        return tabFocus[near.key]
    }
    // and the remote starts on Home. It started wherever the framework put it, which
    // on a television is nowhere in particular: the first press of any direction went
    // hunting instead of moving.
    val remoteHere = onTv()
    LaunchedEffect(Unit) {
        if (remoteHere && !browse.landed) {
            browse.landed = true
            kotlinx.coroutines.delay(350)
            // Not over an offer to install: a build waiting to be put on is the reason
            // the bar is there, and the remote belongs on it. The bar asks for the
            // remote itself when it appears, so a slower answer is covered too.
            if (browse.update == null) {
                runCatching { tabFocus["home"]?.requestFocus() }
            }
        }
    }
    // Inside the library - focus on a poster, or the list scrolled away from the top
    // - back means "out of this list", not "out of the app". The grid refuses to let
    // focus climb out while scrolled, so without this the only way up is through every
    // poster in between; and even at the top, quitting is not what back means with a
    // film under the cursor.
    var inContent by remember { mutableStateOf(false) }
    val scrolled = if (browse.tab == "home")
        browse.rowsState.firstVisibleItemIndex > 0 ||
            browse.rowsState.firstVisibleItemScrollOffset > 0
    else
        browse.gridFor(browse.tab).firstVisibleItemIndex > 0 ||
            browse.gridFor(browse.tab).firstVisibleItemScrollOffset > 0
    // only while focus is in the list: enabled on scroll as well, a scrolled list kept Back
    // moving focus to the tab and it never reached the press-twice exit
    // Nothing holds the focus when this screen is first drawn, nor when it is come
    // back to from Settings or Reports - and the framework then takes the first
    // focusable thing in the layout, which is the server picker up in the corner.
    // The tab is where anybody would expect to be, so it is asked for by name.
    LaunchedEffect(Unit) {
        kotlinx.coroutines.delay(40)
        if (!inContent && browse.focusKey.isEmpty() && browse.openedKey.isEmpty()
                && !browse.typing) {
            // whatever happens below, the picker is a place the remote may go from
            // here on: refusing it for ever would be a server nobody can change
            MainActivity.focusLanded.value = true
            // and at the top of the shelves, the first time. A card that took the
            // focus for a moment while they were arriving scrolled the page to itself,
            // so the app opened half way down its own front page.
            if (!browse.opened) {
                browse.opened = true
                runCatching { browse.rowsState.scrollToItem(0) }
            }
            runCatching { tabFocus[browse.tab]?.requestFocus() }
        }
    }

    // Whether back has anywhere to go: the Home tab, with no shelves on it and no
    // row opened whole. Read here, where these are read during composition and the
    // effect runs again when any of them changes. In a SideEffect up the tree they
    // were not followed at all, so moving to Films never cleared it and the key
    // dispatcher went on closing the app from every screen.
    LaunchedEffect(browse.tab, browse.rows.size, browse.moreRow) {
        MainActivity.homeBare = browse.tab == "home" &&
            browse.moreRow == null && browse.rows.isEmpty()
    }

    // On a tab that is not Home, the rung above the tabs is Home itself. Only from
    // there does back start to mean leaving: quitting from Films because the cursor
    // happened to be on the tab row is not what anybody means by back.
    BackHandler(enabled = !inContent && browse.tab != "home") {
        browse.tab = "home"
        runCatching { tabFocus["home"]?.requestFocus() }
    }

    // Back steps out one landing at a time, rather than all the way at once: along
    // the row to its first card, out of the row to the tabs, and from there the two
    // presses that close the app. Somebody deep in a shelf wants the beginning of
    // that shelf far more often than they want to quit.
    val backScope = androidx.compose.runtime.rememberCoroutineScope()
    BackHandler(enabled = inContent) {
        // A row opened whole is left first, before anything else is considered. Asked
        // after the walk along a shelf, the walk answered instead - the column it reads
        // is the one from the shelf this page was opened from - and back did nothing at
        // all, which left the front page showing this list and no way off it.
        if (browse.moreRow != null) {
            // a shelf of somebody's films goes back to the film it was opened from
            val reading = browse.personFrom
            browse.leaveMoreRow()
            if (reading != null) {
                Api.openWanted.value = reading
                return@BackHandler
            }
            // and the front page as it is first met: the shelves from the top, with
            // the remote on Home. Left where it was, the page came back half way down
            // itself with the focus wherever the search happened to put it.
            backScope.launch {
                runCatching { browse.rowsState.scrollToItem(0) }
                kotlinx.coroutines.delay(60)
                runCatching { tabFocus["home"]?.requestFocus() }
            }
            return@BackHandler
        }
        // On the shelves, back walks along the row before it leaves it: a shelf is
        // read left to right and its beginning is where somebody means to return to.
        // In a list of hundreds that is not true - the tab is what they want - so
        // there back goes straight to the tab this list belongs to.
        if (browse.tab == "home" && browse.column > 0) {
            val shelf = browse.rows.getOrNull(browse.rowNow)
            val name = shelf?.first.orEmpty()
            val firstKey = shelf?.second?.firstOrNull()?.ratingKey.orEmpty()
            if (firstKey.isNotEmpty()) {
                // scrolled to before it is asked for: a card off the left-hand side of
                // the row is not drawn, and nothing that is not drawn can take focus
                backScope.launch {
                    runCatching { browse.rowStates[name]?.scrollToItem(0) }
                    browse.focusKey = firstKey
                }
                return@BackHandler
            }
        }
        // At the beginning of the row: out to the tabs, which is the way home.
        //
        // Unless there is nothing to move it to. A tab row that has not been drawn
        // yet - a screen still waiting on its shelves - swallowed every back press
        // here and the app could not be left. When the focus cannot go, this handler
        // stands down and the next press reaches the one that closes.
        val to = tabFocus[browse.tab]
        val moved = to != null && runCatching { to.requestFocus() }.isSuccess
        if (!moved) inContent = false
    }

    // A list opens with the cursor on the row of buttons over it, on the tab it is
    // showing. It opened with the focus nowhere in particular, and the first press of
    // any direction went hunting rather than moving.
    LaunchedEffect(browse.tab, browse.loaded) {
        // Not while a poster is waiting to be returned to. Back reloads the tab, which
        // is a change of browse.loaded, so this fired on the way back and took the
        // remote off the poster that had just been given it.
        if (browse.tab in setOf("films", "tv") && browse.loaded.isNotEmpty()
                && !inContent && !browse.typing
                && browse.focusKey.isEmpty() && browse.openedKey.isEmpty()) {
            kotlinx.coroutines.delay(80)
            runCatching { tabFocus[browse.tab]?.requestFocus() }
        }
    }

    val behind = browse.backdrop
        ?: browse.rows.firstOrNull { it.first == DECK }?.second?.firstOrNull()
    // Not a focusProperties { enter } here: that answers every entry into this screen,
    // not the first one, so pressing down out of the tabs was sent straight back to
    // them and the shelves could not be reached at all. Where the focus starts is
    // settled by the effect above instead.
    Box(Modifier.fillMaxSize()) {
    // The title last opened, or the one somebody stopped, kept behind the shelves.
    // Faint on purpose: every poster drawn over it has to stay legible.
    // Fitted, not cropped: filling the screen with a 2:3 poster blew it up until only
    // the middle of it was left, and what somebody stopped watching was unrecognisable.
    if (behind != null && Skin.BackdropBehind) {
        Art(Api.artUrl(behind), behind.title, Modifier.fillMaxSize().alpha(0.16f),
            mark = 0, scale = androidx.compose.ui.layout.ContentScale.Fit,
            ground = Brush.verticalGradient(listOf(Skin.Bg, Skin.Bg)))
    }
    Column(Modifier.fillMaxSize()) {
        TopBar(browse.tab,
               { chosen ->
                   // Reports is a place, not a shelf: the tab leads to it and the
                   // library stays on whichever tab it was
                   if (chosen == "reports") onReports() else {
                       // Home means the front page as it is met: the shelves at the
                       // top and every one of them back at its own beginning. Left
                       // where they were, pressing Home from halfway down the page
                       // changed nothing anybody could see.
                       if (chosen == "home") {
                           browse.column = 0
                           browse.rowNow = 0
                           backScope.launch {
                               runCatching { browse.rowsState.scrollToItem(0) }
                               browse.rowStates.values.forEach {
                                   runCatching { it.scrollToItem(0) }
                               }
                           }
                       }
                       // Where this tab was left, so pressing it again comes back
                       // to it rather than to the top. One grid state serves every
                       // tab, so it cannot hold four places at once.
                       if (browse.tab != chosen && browse.moreRow == null &&
                           browse.tab in setOf("films", "tv", "watchlist", "collections")) {
                           browse.tabAt[browse.tab] =
                               browse.gridFor(browse.tab).firstVisibleItemIndex to
                               browse.gridFor(browse.tab).firstVisibleItemScrollOffset
                       }
                       if (browse.tab != chosen) {
                           browse.tabPut = ""
                           // opened from the bar, not off a shelf: the whole library
                           browse.diskOnly = false
                           // what was on the screen belongs to the tab being left, and
                           // what the tab being opened had is what it should show
                           // again - at the place it was left, with no blank frame in
                           // between and nothing of the other tab under its heading.
                           if (browse.moreRow == null) {
                               browse.grids[browse.tab] = browse.grid
                           }
                           browse.grid = browse.grids[chosen] ?: emptyList()
                           browse.loaded = ""
                       } else if (chosen in setOf("films", "tv", "watchlist",
                                                  "collections")) {
                           // The tab it is already on, pressed again: back to the top,
                           // the way Home behaves. Arriving at a tab keeps the place
                           // it was left at; pressing the one you are standing on is
                           // how you say you want the beginning of it.
                           browse.tabAt.remove(chosen)
                           browse.tabPut = chosen   // nothing to put back any more
                           browse.column = 0
                           browse.rowNow = 0
                           backScope.launch {
                               runCatching { browse.gridFor(chosen).scrollToItem(0) }
                           }
                       }
                       browse.tab = chosen; browse.query = ""
                       // and closes a row that was opened whole, so a tab pressed
                       // while that page is up cannot leave the front page showing it
                       if (browse.moreRow != null) browse.leaveMoreRow()
                       // leaving the tab closes whatever shelf was open in it
                       if (chosen != "collections") browse.collectionOn = null
                   }
               }, onSettings,
               onChat = { chatting = true },
               onPeople = if (owner) ({ onPeople() }) else null,
               onSetup = onPrefs,
               // without these the tabs cannot be handed the focus, and Back had
               // nothing to give it to: it asked an unattached requester, the request
               // threw, and the press did nothing at all
               tabFocus = tabFocus,
               tabAt = tabAt,
               // what a press of down from the tabs reaches: the first thing on the
               // first shelf. Without it the remote asked the framework to find
               // something below, it found nothing laid out there yet, and fell back
               // to the next thing in order - the tab to the right.
               // Sorting a shuffle means nothing; putting something on is the whole
               // point of the shelf, so that is what the Watchlist tab offers instead.
               picks = if (browse.tab == "films" || browse.tab == "tv") ({
                   SortControl(
                       browse.sortKey, browse.sortAsc,
                       // choosing an order takes its natural direction with it: newest
                       // first, A first, oldest first
                       onSort = { key ->
                           browse.sortKey = key
                           browse.sortAsc = key !in setOf("addedAt",
                                                          "originallyAvailableAt",
                                                          // best picture first
                                                          "quality")
                       },
                       onFlip = { browse.sortAsc = !browse.sortAsc })
                   GenreControl(browse.genre, browse.genres, matching = browse.matching,
                                onGenre = { browse.genre = it })
                   DecadeControl(browse.decade, browse.decades,
                                 onDecade = { browse.decade = it })
               }) else if (browse.moreRow != null) ({
                   // a shelf opened whole gets the same three controls as a list
                   SortControl(
                       browse.collSortKey, browse.collSortAsc,
                       onSort = { key ->
                           browse.collSortKey = key
                           browse.collSortAsc = key !in setOf("addedAt", "quality")
                       },
                       onFlip = { browse.collSortAsc = !browse.collSortAsc })
                   GenreControl(browse.genre, browse.genres,
                                onGenre = { browse.genre = it })
                   DecadeControl(browse.decade, browse.decades,
                                 onDecade = { browse.decade = it })
               }) else if (browse.tab == "watchlist") ({
                   SortControl(
                       browse.collSortKey, browse.collSortAsc,
                       onSort = { key ->
                           browse.collSortKey = key
                           browse.collSortAsc = key !in setOf("addedAt", "quality")
                       },
                       onFlip = { browse.collSortAsc = !browse.collSortAsc })
                   GenreControl(browse.genre, browse.genres, matching = browse.matching,
                                onGenre = { browse.genre = it })
                   DecadeControl(browse.decade, browse.decades,
                                 onDecade = { browse.decade = it })
               }) else null,
               search = if (browse.tab in setOf("home", "films", "tv")) ({
                   FilterControls(browse.query, { browse.query = it },
                                  onTyping = { browse.typing = it })
               }) else null,
               filters = null)
        if (chatting) {
            ChatPanel(scope = (ctx as AppCompatActivity).lifecycleScope) {
                chatting = false
            }
        }
        // This server keeps copies of another one: say which, and what of this
        // viewer's it was asked to keep. It does not go away, because the shelves do
        // not stop being short.
        // Read once and put away, and it stays away: a state remembered inside a
        // composable comes back every time the screen is rebuilt, which on a phone is
        // every rotation and every return from a film. Kept against the words
        // themselves, so a note that changes is a note worth showing again.
        val notes = remember { ctx.getSharedPreferences("notes", 0) }
        var copySaid by remember {
            mutableStateOf(notes.getString("copyNote", "") != Api.copyNote)
        }
        if (Api.copyNote.isNotEmpty() && copySaid) {
            Row(Modifier.fillMaxWidth().background(Color(0xFF102618))
                    .padding(horizontal = 20.dp, vertical = 9.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(Api.copyNote, color = Color(0xFFCFE9D8), fontSize = 13.sp,
                     modifier = Modifier.weight(1f))
                // read once and put away, like any other notice
                Pill("Right") {
                    notes.edit().putString("copyNote", Api.copyNote).apply()
                    copySaid = false
                }
            }
        }
        browse.notice?.let { words ->
            Row(Modifier.fillMaxWidth().background(Color(0xFF12324F))
                    .padding(horizontal = 20.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(words, color = Color(0xFFDCE9F7), fontSize = 13.sp,
                     modifier = Modifier.weight(1f))
                Pill("Right") { browse.notice = null }
            }
        }
        // The same bar the app's own update uses, in the library's own colour. It takes
        // itself away after twelve seconds, unless the remote has reached it: reading a
        // line that vanishes as it is being selected is worse than one press.
        browse.serverNow?.let { version ->
            var held by remember(version) { mutableStateOf(false) }
            LaunchedEffect(version, held) {
                if (!held) {
                    kotlinx.coroutines.delay(12_000)
                    browse.serverNow = null
                }
            }
            Row(Modifier.fillMaxWidth().background(Color(0xFF12324F))
                    .padding(horizontal = 20.dp, vertical = 10.dp)
                    .onFocusChanged { held = it.hasFocus },
                verticalAlignment = Alignment.CenterVertically) {
                Text("The server is now version " + version,
                     color = Color(0xFFDCE9F7), fontSize = 13.sp,
                     modifier = Modifier.weight(1f))
                Pill("Right") { browse.serverNow = null }
            }
        }
        update?.let { u ->
            // Four states, and the button says which one it is in: nothing yet,
            // fetching (locked, counting), fetched (Install), and handed over. The
            // press that appeared to do nothing was a fetch with no sign of itself.
            var state by remember { mutableStateOf("") }
            var shown by remember { mutableStateOf(true) }
            var busy by remember { mutableStateOf(false) }
            var pct by remember { mutableStateOf(0) }
            var ready by remember { mutableStateOf(Updates.waiting(ctx, u)) }
            if (!shown) return@let
            val install: (java.io.File) -> Unit = { apk ->
                state = if (Updates.installFile(ctx, apk))
                    // The bar stays - Android's prompt can be dismissed, or missed
                    // altogether on a television, and with the bar gone the only way
                    // back to the file already downloaded was to restart the app. The
                    // button still says Install, which is instruction enough.
                    "Android is installing " + u.versionName
                else "This device has no installer for an APK"
            }
            Row(Modifier.fillMaxWidth().background(Color(0xFF7C4A08))
                    .padding(horizontal = 20.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(when {
                         state.isNotEmpty() -> state
                         busy -> "Downloading " + u.versionName + "…  " + pct + "%"
                         // Two builds of one version is an ordinary thing while a
                         // release is being worked on, and "0.17.21 is available -
                         // you have 0.17.21" reads as a fault. The build number is
                         // what Android actually compares, so it is what is shown
                         // when the names are the same.
                         ready != null -> "Version " + named(u) + " is ready to install"
                         else -> "Version " + named(u) + " is available - you have " +
                             BuildConfig.VERSION_NAME +
                             (if (u.versionName == BuildConfig.VERSION_NAME)
                                  " (" + BuildConfig.VERSION_CODE + ")" else "")
                     },
                     color = Color(0xFFFFF3DE), fontSize = 13.sp,
                     modifier = Modifier.weight(1f))
                if (!busy) Pill("Later") { shown = false; browse.update = null }
                // On a television nothing has focus until something asks for it, and
                // the offer is the reason the bar is there: the remote should be on
                // it when the app opens, not three presses away.
                val takeIt = remember { FocusRequester() }
                LaunchedEffect(u.versionCode) {
                    // Once per build on offer. This bar is drawn again every time a
                    // title's page closes, and the effect took the remote off whatever
                    // back had just put it on - which on a day of several builds is
                    // every time anybody backs out of anything.
                    if (browse.offerTaken != u.versionCode) {
                        browse.offerTaken = u.versionCode
                        runCatching { takeIt.requestFocus() }
                    }
                }
                // Downloaded and waiting: white, because it is a different act from
                // fetching it - one press and the app is replaced.
                Pill(when {
                         busy -> pct.toString() + "%"
                         ready != null -> "Install"
                         else -> "Update"
                     }, primary = true, ready = ready != null,
                     modifier = Modifier.focusRequester(takeIt)) {
                    if (busy) {
                        // locked while it is fetching: pressing again used to start
                        // the same sixteen megabytes over
                    } else if (!Updates.mayInstall(ctx)) {
                        // Android asks once, per app; this is where it asks
                        state = "Allow Palladium to install updates, then press again"
                        Updates.askForPermission(ctx)
                    } else {
                        val held = ready
                        if (held != null) {
                            install(held)
                        } else {
                            busy = true
                            pct = 0
                            state = ""
                            (ctx as AppCompatActivity).lifecycleScope.launch {
                                val apk = Updates.download(ctx, u) { p -> pct = p }
                                busy = false
                                if (apk == null) {
                                    state = "Could not download the update - is the server on?"
                                } else {
                                    // It waits here, with the white ring on: replacing
                                    // the app is its own decision, and it used to
                                    // happen the instant the last byte arrived.
                                    ready = apk
                                    state = "Downloaded - press to install"
                                }
                            }
                        }
                    }
                }
            }
        }
        // The casual shelf's own row: how it plays, how far it has got, and the
        // button that puts something on. Its own line because a television's top bar
        // has no room left after the tabs, and what fell off the end was the button
        // that actually does something.
        browse.held?.let { one ->
            // a shuffle row carries the shelf it stands for; the title on it is only
            // whatever the hat drew last
            val shelf = one.shuffleId
            val shelfName = one.shuffle.ifBlank { one.title }
            AlertDialog(
                // The menu is a window of its own, so the rest of the press that
                // opened it arrives here rather than at the activity - and the release
                // pressed the first thing in it. Dropped where it lands.
                modifier = Modifier.stillHeld(),
                onDismissRequest = { browse.held = null },
                containerColor = Skin.Panel,
                title = { Text(if (shelf.isNotEmpty()) shelfName else one.title,
                                color = Skin.Fg) },
                text = {
                    androidx.compose.foundation.layout.Column {
                        Text(when {
                                 shelf.isNotEmpty() ->
                                     "End this shuffle: everything goes back in the hat " +
                                     "and it leaves Continue watching."
                                 else -> "Seen it, or forget where you were."
                             },
                             color = Skin.Dim, fontSize = 14.sp)
                        Spacer(Modifier.height(12.dp))
                        // the title's own page; an episode has none, so its programme's
                        Pill("→ Go to title") {
                            browse.held = null
                            goToTitle(ctx, one, onOpen)
                        }
                    }
                },
                confirmButton = if (shelf.isNotEmpty()) ({
                    Pill("✕ End this shuffle", primary = true) {
                        browse.held = null
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            Api.shelfReset(shelf, one.srv)
                            browse.loaded = ""
                        }
                    }
                }) else ({
                    Pill("✓ Watched", primary = true) {
                        browse.held = null
                        MainActivity.marksTouched.value++
                        // the shelves as they stand, changed where this card is,
                        // before the server has answered: the press should land on
                        // the screen and not a moment later
                        cardMarked(browse, one, true)
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            Api.setWatched(one, true)
                            // and off the shelf with it: marking one episode watched
                            // otherwise hands the shelf to the next episode, which
                            // reads as nothing having happened
                            Api.aside(one)
                        }
                    }
                }),
                dismissButton = if (shelf.isNotEmpty()) ({
                    Pill("Cancel") { browse.held = null }
                }) else ({
                    Pill("✕ Not watched") {
                        browse.held = null
                        MainActivity.marksTouched.value++
                        cardMarked(browse, one, false)
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            Api.setWatched(one, false)
                            Api.aside(one)
                        }
                    }
                }))
        }
        // Held on the watchlist: a favorite stays on it when watched, and is kept on
        // both machines.
        browse.favHeld?.let { one ->
            val kept = one.ratingKey in Api.favKeys.value
            AlertDialog(
                modifier = Modifier.stillHeld(),
                onDismissRequest = { browse.favHeld = null },
                containerColor = Skin.Panel,
                title = { Text(one.title, color = Skin.Fg) },
                text = {
                    androidx.compose.foundation.layout.Column {
                        Text(if (kept) "A favorite stays on the watchlist when it is " +
                                       "watched, and is kept on both machines."
                             else "Make it a favorite: it stays on the watchlist when " +
                                  "it is watched, and is kept on both machines.",
                             color = Skin.Dim, fontSize = 14.sp)
                        Spacer(Modifier.height(12.dp))
                        Pill("\u2192 Go to title") {
                            browse.favHeld = null
                            goToTitle(ctx, one, onOpen)
                        }
                    }
                },
                confirmButton = {
                    Pill(if (kept) "\u2661 Remove favorite" else "\u2665 Favorite",
                         primary = true) {
                        browse.favHeld = null
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            Api.favorite(one, !kept)
                            MainActivity.marksTouched.value++
                            browse.loaded = ""
                        }
                    }
                },
                dismissButton = {
                    // Off the list from where it is shown. Taking it off was only on
                    // the title's own page, so the way off the watchlist was to open
                    // the thing you were trying to stop meaning to watch.
                    Pill("\u2715 Off the watchlist") {
                        browse.favHeld = null
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            // a favorite is a watchlist entry that stays: it goes with it
                            if (kept) Api.favorite(one, false)
                            Api.mark(one, false)
                            MainActivity.marksTouched.value++
                            browse.loaded = ""
                        }
                    }
                })
        }
        // Inside a collection: its name over the grid, and back leads out of it
        // rather than out of the app. No button for it - the one on the remote is
        // the way out of everything else here too.
        browse.collectionOn?.let { open ->
            if (browse.tab == "collections") {
                BackHandler { browse.collectionOn = null }
                val tightRow = LocalConfiguration.current.screenWidthDp < 600
                // On a phone the name takes the line and the controls take the next
                // one. Five things across 360 points put the buttons off the edge,
                // and a button off the edge cannot be pressed at all.
                if (tightRow) {
                    Row(Modifier.fillMaxWidth()
                            .padding(start = 21.dp, end = 16.dp, top = 12.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        SectionTitle(open.title)
                        Spacer(Modifier.width(10.dp))
                        Text(open.subtitle, color = Skin.Dim, fontSize = 12.sp)
                    }
                }
                Row(Modifier.fillMaxWidth()
                        .padding(start = 21.dp, end = 16.dp,
                                 top = if (tightRow) 6.dp else 12.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    if (!tightRow) {
                        SectionTitle(open.title)
                        Spacer(Modifier.width(12.dp))
                        Text(open.subtitle, color = Skin.Dim, fontSize = 13.sp)
                        Spacer(Modifier.weight(1f))
                    }
                    // in the order shown, or one drawn out of the hat
                    val start: (Media) -> Unit = { pick ->
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            // A season card on a shelf stands for the episodes under
                            // it, and those are what plays. Asking the library for the
                            // season answers with the programme, so Play opened the
                            // show and started nothing at all.
                            val first = pick.holds.firstOrNull()
                            val want = if (first != null)
                                           runCatching { Api.item(first, pick.srv) }
                                               .getOrNull() ?: pick
                                       else pick
                            val full = (runCatching { Api.metadata(want) }
                                .getOrNull() ?: want)
                                // and from the main server, if it was listed from the
                                // copy while that one was answering for it
                                .let { Api.atHome(ctx, it) ?: it }
                            if (full.isFolder) onOpen(full)      // a series: its page
                            else ctx.startActivity(
                                playIntent(ctx, full,
                                           full.viewOffsetMs,
                                           full.pickedSub ?: full.openWith(
                                               Api.myLanguage)
                                               ?.index))
                        }
                    }
                    val order = collectionOrder(browse.grid, browse.collSortKey,
                                                browse.collSortAsc)
                    if (order.isNotEmpty()) {
                        // One Play, and a switch beside it for how. Shuffled means the
                        // round the main server keeps for this shelf and this person -
                        // carrying on with whatever was left part-way, nothing twice
                        // until the hat is empty. It used to draw a title at random,
                        // which is a fresh evening every time it is pressed.
                        // Resume names the second it would start at, when the round
                        // on this shelf was left somewhere. Play on a button that
                        // carries on from eight minutes in is a promise about what
                        // pressing it does that pressing it does not keep.
                        val carryOn = browse.shuffled && open.shelfResumeAt > 30
                        Pill(if (carryOn)
                                 "▶ Resume  " + fmt(open.shelfResumeAt.toLong())
                             else "▶ Play",
                             primary = true,
                             narrow = tightRow, small = tightRow) {
                            if (browse.shuffled) {
                                (ctx as AppCompatActivity).lifecycleScope.launch {
                                    val drew = Api.shelfDraw(open, true)
                                    if (drew == null) {
                                        browse.notice = "Nothing to play on that shelf"
                                    } else if (drew.media.offered ||
                                               drew.media.ratingKey.startsWith("o")) {
                                        // Drawn from a pack rather than the library.
                                        // The server has already asked for it, and
                                        // there is nothing to open a player on until
                                        // it arrives - so its own page instead, which
                                        // is where how far along it is can be seen.
                                        val full = (runCatching {
                                            Api.metadata(drew.media)
                                        }.getOrNull() ?: drew.media)
                                        browse.notice = "Fetching " + drew.media.title
                                        onOpen(full)
                                    } else {
                                        // the shelf's copy carries no streams: the
                                        // full answer decides the soundtrack and the
                                        // subtitle, as it does everywhere else
                                        val full = (runCatching {
                                            Api.metadata(drew.media)
                                        }.getOrNull() ?: drew.media)
                                            .let { Api.atHome(ctx, it) ?: it }
                                        ctx.startActivity(
                                            playIntent(ctx, full,
                                                       drew.resumeAt,
                                                       full.pickedSub ?: full.openWith(
                                                           Api.myLanguage)?.index)
                                                // put on rather than chosen, and the
                                                // shelf it came off, so Next draws
                                                // from that shelf rather than handing
                                                // over the next episode of a series
                                                .putExtra("casual", true)
                                                .putExtra("shelf",
                                                          open.ratingKey
                                                              .removePrefix("coll:")))
                                    }
                                }
                            } else order.firstOrNull { !it.offered }?.let { start(it) }
                        }
                        Spacer(Modifier.width(if (tightRow) 4.dp else 8.dp))
                        Pill("↻ Shuffle", active = browse.shuffled,
                             narrow = tightRow, small = tightRow) {
                            browse.shuffled = !browse.shuffled
                        }
                        Spacer(Modifier.width(if (tightRow) 4.dp else 8.dp))
                    }
                    SortControl(
                        browse.collSortKey, browse.collSortAsc,
                        onSort = { key ->
                            browse.collSortKey = key
                            // released oldest first here, where the library reads
                            // newest first: a collection is watched from its start
                            browse.collSortAsc = key !in setOf("addedAt", "quality")
                        },
                        onFlip = { browse.collSortAsc = !browse.collSortAsc })
                    GenreControl(browse.genre, browse.genres, matching = browse.matching,
                                 onGenre = { browse.genre = it })
                    DecadeControl(browse.decade, browse.decades,
                                  onDecade = { browse.decade = it })
                }
            }
        }
        when {
            browse.loading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                CircularProgressIndicator(color = Skin.Accent, strokeWidth = 3.dp)
            }
            browse.failed != null -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                Text(browse.failed!!, color = Skin.Dim, fontSize = 15.sp)
            }
            browse.tab == "home" && browse.moreRow == null &&
                browse.query.trim().length < 2 -> LazyColumn(
                state = browse.rowsState,
                modifier = Modifier.onFocusChanged { inContent = it.hasFocus },
                contentPadding = PaddingValues(bottom = 28.dp)) {
                // keyed by the shelf's name. Without a key a lazy list reuses its
                // rows by position, so a shelf arriving or leaving handed one row's
                // state to another - and a card that had the focus was released twice
                // while the list was being measured, which took the app down.
                itemsIndexed(browse.rows, key = { _, row -> row.first },
                             contentType = { _, row -> row.first }) {
                        shelf, (title, list) ->
                    val topShelf = shelf == 0
                    // asked for by the shelf above and the shelf below
                    val ask = browse.shelfAsk.getOrPut(shelf) { FocusRequester() }
                    // the gap above a shelf's name, which on a short screen is the
                    // difference between two whole shelves and one and a half
                    // one line of the heading's own size above it, no more: the air
                    // over a shelf name is a shelf's worth of height down the page
                    val gap = if (LocalConfiguration.current.screenHeightDp < 560) 2 else 16
                    SectionTitle(title,
                                 Modifier.padding(start = 21.dp, top = gap.dp, bottom = 2.dp))
                    val w = shelfPoster()
                    val rowState = browse.rowStates.getOrPut(title) {
                        androidx.compose.foundation.lazy.LazyListState() }
                    // Back from a title opened in this row: scroll the row to it, then
                    // focus it. Keyed on the list as well, and the key held until the
                    // row has one: coming back reloads the shelves, so the effect ran
                    // against an empty row, found nothing, and cleared the key it would
                    // have needed a moment later - which left the remote on the tabs.
                    LaunchedEffect(title, list, browse.cameBack) {
                        if (browse.openedRow == title && browse.openedKey.isNotEmpty() &&
                            list.isNotEmpty()) {
                            val key = browse.openedKey
                            val at = list.indexOfFirst { it.ratingKey == key }
                            browse.openedRow = ""; browse.openedKey = ""
                            if (at >= 0) {
                                runCatching { rowState.scrollToItem(at) }
                                browse.focusKey = key
                            }
                        }
                    }
                    // A shelf remembers where the remote was on it. Without that,
                    // a press of down looks for whatever lies nearest below - and the
                    // shelves scroll on their own, so nearest is rarely the same
                    // column: it landed on a title further right, and the next press
                    // came back left. Each shelf is one group now, and entering it
                    // goes to where you were on it, or to its first card.
                    // Ten to a shelf. A shelf of forty is forty cards to walk past
                    // to reach the one below it; the rest are a press away on the card
                    // at the end.
                    val shown = list.take(SHELF_SHOWS)
                    LazyRow(state = rowState,
                            contentPadding = PaddingValues(horizontal = 16.dp),
                            modifier = Modifier.focusGroup()) {
                        itemsIndexed(shown, key = { _, m -> m.ratingKey },
                                        contentType = { _, m -> m.ratingKey }) { at, m ->
                            val here = remember { FocusRequester() }
                            // the one the tabs hand down to
                            val first = topShelf &&
                                m.ratingKey == shown.firstOrNull()?.ratingKey
                            // the card this shelf answers with: the one in the
                            // column the remote is in, or the last of a short shelf
                            val leads = at == browse.column.coerceAtMost(shown.lastIndex)
                            LaunchedEffect(browse.focusKey) {
                                if (browse.focusKey == m.ratingKey) {
                                    runCatching { here.requestFocus() }
                                    browse.focusKey = ""
                                }
                            }
                            Poster(m, width = w,
                                   // A card taken off a shelf leaves a gap, and the
                                   // rest of the row closes it by moving rather than
                                   // by the shelf being read again from the server -
                                   // which redrew the whole page and lost the place
                                   // the remote was on.
                                   modifier = Modifier.animateItem()
                                       .focusRequester(here)
                                       .onGloballyPositioned {
                                           browse.cardAt[m.ratingKey] =
                                               it.boundsInWindow().center.x
                                       }
                                       .then(if (leads) Modifier.focusRequester(ask)
                                             else Modifier)

                                       // where the remote is, kept for the next time
                                       // this shelf is come to
                                       .onFocusChanged {
                                           if (it.isFocused) {
                                               browse.shelfWas[shelf] = m.ratingKey
                                               browse.column = at
                                               browse.rowNow = shelf
                                           }
                                       }
                                       // up and down go shelf to shelf, to the card
                                       // each one was left on - the shelves scroll
                                       // under the remote, so nearest is rarely the
                                       // same column. Off the top shelf it is not
                                       // ours: up goes straight up, to whatever is
                                       // over that card.
                                       .edge(up = {
                                                if (shelf > 0)
                                                    moveTo(browse.shelfAsk[shelf - 1])
                                                // Off the top shelf, up is the tab
                                                // standing over this card. What the
                                                // framework finds instead is the
                                                // server picker, above and to the
                                                // left of all of them.
                                                else moveTo(tabOver(
                                                    browse.cardAt[m.ratingKey] ?: 0f))
                                            },
                                            down = { moveTo(browse.shelfAsk[shelf + 1]) },
                                            // The first card of a shelf is the end of
                                            // it. Left from there had nothing beside
                                            // it, so the search took the nearest thing
                                            // on the screen - the server picker, up in
                                            // the corner - and a held left walked
                                            // straight out of the list into it.
                                            left = { at == 0 }),
                                   // held: the menu, wherever the poster is. It
                                   // used to open the title's own page on every
                                   // shelf but Continue watching - so a hold went
                                   // straight past to the title while the button was
                                   // still down, and there was no way to say seen it
                                   // without going there first. Go to title is the
                                   // first thing in the menu.
                                   onHold = {
                                       browse.openedRow = title
                                       browse.held = m
                                   }) {
                                // Continue watching resumes where it was left. Opening
                                // the page instead put every resume one press further
                                // away, and a shuffle lost its shelf on the way: the
                                // page carries no shelf, so Next handed over the next
                                // episode of the programme rather than drawing.
                                if (title == DECK && m.shuffleId.isNotEmpty()) {
                                    // A shuffle's own row, standing for a shelf: it
                                    // asks the shelf rather than playing the card.
                                    // What the row shows is whatever the hat drew
                                    // last, so pressing it played that again from its
                                    // beginning once the round had moved on. The
                                    // shelf decides between the two - carrying on
                                    // with one left part-way, or drawing the next.
                                    //
                                    // This used to ask for a folder as well, on the
                                    // reasoning that a programme's row is one. A row
                                    // on this shelf is the episode itself, folder or
                                    // not, so the test was never true and none of it
                                    // ran.
                                    (ctx as AppCompatActivity).lifecycleScope.launch {
                                        val drew = Api.shelfDraw(m.shuffleId, m.srv,
                                                                 resume = true)
                                        if (drew == null) {
                                            browse.notice = "Nothing to play on that shelf"
                                        } else if (drew.media.offered ||
                                                   drew.media.ratingKey.startsWith("o")) {
                                            browse.notice = "Fetching " + drew.media.title
                                            onOpen(runCatching { Api.metadata(drew.media) }
                                                       .getOrNull() ?: drew.media)
                                        } else {
                                            val full = (runCatching {
                                                Api.metadata(drew.media)
                                            }.getOrNull() ?: drew.media)
                                                .let { Api.atHome(ctx, it) ?: it }
                                            full.shuffleId = m.shuffleId
                                            play(ctx, full, drew.resumeAt,
                                                 full.pickedSub
                                                     ?: full.openWith(Api.myLanguage)?.index)
                                        }
                                    }
                                } else if (title == DECK && !m.isFolder) {
                                    // asked for in full first. A row on this shelf is
                                    // brief - no codecs, no part, nothing about the
                                    // file - and a film that cannot say what it is
                                    // cannot be played as it is, so every resume from
                                    // here went through the encoder.
                                    (ctx as AppCompatActivity).lifecycleScope.launch {
                                        val full = (runCatching { Api.metadata(m) }
                                            .getOrNull() ?: m)
                                            .let { Api.atHome(ctx, it) ?: it }
                                        full.shuffleId = m.shuffleId
                                        val pick = full.pickedSub
                                            ?: full.openWith(Api.myLanguage)?.index
                                        play(ctx, full, m.viewOffsetMs / 1000, pick)
                                    }
                                } else {
                                    browse.openedRow = title; browse.openedKey = m.ratingKey
                                    onOpen(m)
                                }
                            }
                        }
                        // The end of a row, where there is more than fits on it:
                        // everything it holds, on a page. A shelf shows what the
                        // screen has room for and the rest was simply not reachable.
                        if (list.size > shown.size) {
                            // keyed, like the cards beside it: an item a lazy row
                            // cannot name is one it reuses by position
                            item(key = "more:" + title) {
                                var lit by remember { mutableStateOf(false) }
                                Box(Modifier.padding(horizontal = 5.dp, vertical = 8.dp)
                                        .width(w.dp).height((w * 1.5).dp)
                                        .clip(RoundedCornerShape(10.dp))
                                        .background(Skin.Panel)
                                        .border(if (lit) 3.dp else 0.dp,
                                                if (lit) Color.White else Color.Transparent,
                                                RoundedCornerShape(10.dp))
                                        .onFocusChanged { lit = it.isFocused }
                                        // clickable brings its own focus node. Adding
                                        // focusable() as well put two on one item, and
                                        // a lazy row reusing it released the same
                                        // pinned container twice - "Release should only
                                        // be called once", which took the app down on
                                        // any quick move along the shelves.
                                        .clickable {
                                            // Added and released are the front of a
                                            // tab rather than lists of their own:
                                            // opening one goes to that tab in that
                                            // order, where the ordinary marks are.
                                            val toTab = when (title) {
                                                "Recently added films" ->
                                                    "films" to "addedAt"
                                                "Recently released films" ->
                                                    "films" to "originallyAvailableAt"
                                                "Recently added TV" ->
                                                    "tv" to "addedAt"
                                                "Recently released series" ->
                                                    "tv" to "originallyAvailableAt"
                                                else -> null
                                            }
                                            if (toTab != null) {
                                                browse.sortKey = toTab.second
                                                browse.sortAsc = false
                                                // what arrived here, on the shelf that
                                                // is about arriving here
                                                browse.diskOnly = toTab.second == "addedAt"
                                                browse.genre = ""
                                                browse.decade = ""
                                                browse.query = ""
                                                // and nothing of the last tab left on
                                                // the screen: the grid is whatever was
                                                // fetched last, so opening Films off a
                                                // shelf showed programmes under it
                                                // until the films arrived.
                                                browse.grid = emptyList()
                                                browse.loaded = ""
                                                browse.tab = toTab.first
                                                return@clickable
                                            }
                                            // what the tabs were filtered and ordered
                                            // by, to be put back when this is left
                                            browse.moreWas = Filters(
                                                browse.genre, browse.decade,
                                                browse.genres, browse.decades,
                                                browse.collSortKey, browse.collSortAsc)
                                            browse.grid = list
                                            browse.moreAll = list
                                            // its own categories and decades, worked
                                            // out from what it holds - the same
                                            // controls a collection has
                                            browse.genres = list.flatMap { it.genres }
                                                .groupingBy { it }.eachCount().toList()
                                                .sortedBy { it.first.lowercase() }
                                            browse.decades = list.mapNotNull { m ->
                                                (m.year ?: 0).takeIf { it > 0 }
                                                    ?.let { (it / 10 * 10).toString() }
                                            }.groupingBy { it }.eachCount().toList()
                                                .sortedByDescending { it.first }
                                            browse.genre = ""; browse.decade = ""
                                            // opened in the order the shelf is in:
                                            // newest out first. The page sorts what
                                            // it is given, so without this it came up
                                            // in whatever order was last chosen
                                            // somewhere else.
                                            browse.collSortKey = "originallyAvailableAt"
                                            browse.collSortAsc = false
                                            browse.moreRow = title
                                            // and the remote on the first of them.
                                            // Nothing asking for it means the page
                                            // opens with the ring in the corner, on
                                            // the server picker.
                                            browse.focusKey =
                                                list.firstOrNull()?.ratingKey.orEmpty()
                                        },
                                    contentAlignment = Alignment.Center) {
                                    Text("Show more  \u203a", color = Skin.Dim,
                                         fontSize = 14.sp,
                                         modifier = Modifier.padding(8.dp))
                                }
                            }
                        }
                    }
                }
            }
            browse.grid.isEmpty() &&
                !(browse.tab == "watchlist" && browse.favs.isNotEmpty()) ->
                Box(Modifier.fillMaxSize(), Alignment.Center) {
                Text(if (browse.query.isBlank()) "Nothing here" else "Nothing matches that",
                     color = Skin.Dim, fontSize = 15.sp)
            }
            else -> {
                // the same state object every time, which is what keeps the scroll
                val gridState = browse.gridFor(browse.tab)
                // for moving the remote a row at a time, which may have to
                // scroll before the poster it is going to exists
                val gridScope = androidx.compose.runtime.rememberCoroutineScope()
                // fetch the next page while the end is still a screen away, so the
                // scroll never stops at a wall of nothing
                LaunchedEffect(gridState, browse.grid.size, browse.more, want) {
                    snapshotFlow {
                        gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
                    }.collect { last ->
                        if (browse.more && !paging && browse.query.isBlank() &&
                            last >= browse.grid.size - 24) {
                            paging = true
                            val next = runCatching {
                                if (browse.tab == "films")
                                    Api.movies(ctx, order, browse.grid.size, PAGE,
                                               browse.genre, browse.decade,
                                               browse.diskOnly)
                                else Api.shows(ctx, order, browse.grid.size, PAGE,
                                               browse.genre, browse.decade,
                                               browse.diskOnly)
                            }.getOrDefault(emptyList())
                            // Never the same key twice: a keyed grid throws on a
                            // repeat rather than drawing, and a page is a window
                            // into a list two servers are still agreeing about.
                            val had = browse.grid.mapTo(HashSet()) { it.ratingKey }
                            browse.grid = browse.grid + next.filter {
                                it.ratingKey !in had
                            }
                            browse.more = next.size >= PAGE
                            paging = false
                        }
                    }
                }
                // Back onto a tab: the place it was left. Not when a title was
                // opened from it - that is the effect below, which puts the grid on
                // the poster itself - and once per visit, so a page fetched while
                // scrolling does not drag the list back up.
                LaunchedEffect(browse.tab, browse.grid.isNotEmpty()) {
                    val was = browse.tabAt[browse.tab]
                    // The state was made standing in the right place, so there is
                    // nothing to do in the ordinary case. This is only for a list that
                    // came back shorter than the one left - the state would be holding
                    // an index that no longer exists.
                    if (was != null && browse.grid.isNotEmpty() &&
                        browse.openedKey.isEmpty() && browse.tabPut != browse.tab) {
                        browse.tabPut = browse.tab
                        if (gridState.firstVisibleItemIndex > browse.grid.size - 1) {
                            runCatching {
                                gridState.scrollToItem((browse.grid.size - 1)
                                                           .coerceAtLeast(0))
                            }
                        }
                    }
                }
                // Back from a title opened here: the grid is put back on its poster,
                // whatever else moved while the title's page was open. Run when the grid
                // is drawn again, not when the poster is pressed.
                LaunchedEffect(browse.grid, browse.cameBack) {
                    val key = browse.openedKey
                    val was = browse.openedAt
                    // not cleared until there is a grid to look in: back reloads the
                    // tab, and against the empty one this found nothing and threw the
                    // key away, so the remote landed on the tabs instead of the poster
                    if (key.isNotEmpty() && browse.grid.isNotEmpty()) {
                        browse.openedKey = ""
                        browse.openedAt = null
                        val order = if (browse.collectionOn != null || browse.tab == "watchlist")
                            collectionOrder(browse.grid, browse.collSortKey, browse.collSortAsc)
                        else browse.grid
                        val at = order.indexOfFirst { it.ratingKey == key }
                        when {
                            at < 0 -> Unit
                            // still where it was: nothing to do
                            was != null && gridState.firstVisibleItemIndex == was.first &&
                                gridState.firstVisibleItemScrollOffset == was.second -> Unit
                            // the same list: exactly the place it was scrolled to
                            was != null && at >= was.first && at - was.first < 60 &&
                                was.first < order.size ->
                                gridState.scrollToItem(was.first, was.second)
                            // a list drawn again differently: the poster, at least
                            else -> gridState.scrollToItem(at)
                        }
                        if (at >= 0) browse.focusKey = key
                    }
                }
                // the same size the shelves on Home use, so a film is the same film
                // whichever tab it is looked at from - and on a television that means
                // more of them on the screen at once rather than six large ones
                val cell = shelfPoster().dp
                // and on a phone standing up it is three to a row exactly, rather
                // than as many as happen to fit with a gap left over
                val standing = LocalConfiguration.current.screenWidthDp < 600 &&
                    LocalConfiguration.current.screenHeightDp >
                        LocalConfiguration.current.screenWidthDp
                // a phone lying down: five to a row, the same as its shelves
                val onItsSide = LocalConfiguration.current.screenHeightDp < 400 &&
                    LocalConfiguration.current.screenWidthDp >
                        LocalConfiguration.current.screenHeightDp
                // The page holds still while the remote moves along a row.
                //
                // A lazy grid scrolls by itself to bring whatever has the focus fully
                // into view, and it is strict about it: a poster overhanging its slot
                // by a few pixels is enough, so every step sideways nudged the whole
                // page - measured at ten pixels, and plainly visible. Stepping onto
                // something genuinely off screen still scrolls; tidying up an edge
                // does not.
                @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
                val steady = remember {
                    object : androidx.compose.foundation.gestures.BringIntoViewSpec {
                        override fun calculateScrollDistance(
                            offset: Float, size: Float, containerSize: Float
                        ): Float {
                            // The deadzone, with room for the lift.
                            //
                            // The highlight grows the picture by four per cent and
                            // the grid only knows about slots, so a row flush against
                            // the top or bottom would have its ring cut off. The room
                            // it needs is asked for here: half the growth above and
                            // below. Inside that, nothing moves - which is the
                            // deadzone, and what stops the page drifting sideways.
                            val room = size * 0.02f
                            val top = offset - room
                            val bottom = offset + size + room
                            if (top >= 0f && bottom <= containerSize) return 0f
                            // and the least that gives it that room, never centred
                            return if (top < 0f) top else bottom - containerSize
                        }
                    }
                }
                @OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
                androidx.compose.runtime.CompositionLocalProvider(
                    androidx.compose.foundation.gestures.LocalBringIntoViewSpec provides steady
                ) {
                LazyVerticalGrid(
                    state = gridState,
                    columns = when {
                        standing -> GridCells.Fixed(3)
                        onItsSide -> GridCells.Fixed(5)
                        else -> GridCells.Adaptive(cell)
                    },
                    contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
                    modifier = Modifier.fillMaxSize()
                        .onFocusChanged { inContent = it.hasFocus }
                        .focusGroup()
                        .focusProperties {
                            // Deep in the list the rows above have been recycled, so a
                            // focus search upwards finds nothing and leaps to the menu.
                            // Refuse the exit while scrolled: up then moves by rows.
                            // Where up goes is decided a press at a time, on the
                            // poster itself - see Modifier.edge. Down is the one thing
                            // left to the framework, because refusing the exit is also
                            // what makes the list scroll to the row below.
                            //
                            // The row below has not been composed yet, and the search
                            // then leaves the grid upwards - onto the Casual play
                            // button. Refuse it while there are rows left.
                            down = if (gridState.layoutInfo.visibleItemsInfo.isNotEmpty() &&
                                       gridState.layoutInfo.visibleItemsInfo.last().index <
                                           gridState.layoutInfo.totalItemsCount - 1)
                                FocusRequester.Cancel else FocusRequester.Default
                        },
                ) {
                    val shown = when {
                        // a shelf opened whole: sorted and narrowed here, because the
                        // list is in hand rather than asked for again
                        browse.moreRow != null -> collectionOrder(
                            browse.moreAll.filter { m ->
                                (browse.genre.isEmpty() ||
                                 browse.genre.split(",").filter { it.isNotBlank() }
                                     .all { want ->
                                         m.genres.any { it.equals(want, ignoreCase = true) }
                                     }) &&
                                (browse.decade.isEmpty() ||
                                 browse.decade.split(",").map { it.trim() }
                                     .any { it == ((m.year ?: 0) / 10 * 10).toString() })
                            }, browse.collSortKey, browse.collSortAsc)
                        browse.collectionOn != null || browse.tab == "watchlist" ->
                            collectionOrder(browse.grid, browse.collSortKey,
                                            browse.collSortAsc)
                        else -> browse.grid
                    }
                    itemsIndexed(shown, key = { _, m -> m.ratingKey },
                                    contentType = { _, m -> m.ratingKey }) { at, m ->
                        // Nothing under the card but its own line. Sorted by
                        // release a series is placed by its newest episode, and the
                        // card used to say "last aired" and the date to explain the
                        // order - which does not fit the width on a television, and
                        // wrapped over the title beneath it. The browser has room for
                        // it and still says it there.
                        val instead: String? = null
                        val here = remember { FocusRequester() }
                        LaunchedEffect(browse.focusKey) {
                            if (browse.focusKey == m.ratingKey) {
                                runCatching { here.requestFocus() }
                                browse.focusKey = ""
                            }
                        }
                        Poster(m, fill = true, instead = instead,
                               modifier = Modifier.focusRequester(here)
                                   .onFocusChanged {
                                       if (it.isFocused) browse.gridAt = at
                                   }
                                   .onGloballyPositioned {
                                       browse.cardAt[m.ratingKey] =
                                           it.boundsInWindow().center.x
                                   }
                                   // Out of the top row, up belongs to the row of
                                   // buttons over the posters - the tab this screen
                                   // is on. Straight up from the left-hand column
                                   // reached the server picker instead, which sits
                                   // above them and to the left. From any other row
                                   // this answers nothing and up moves by rows.
                                   // Out of the top row, to the button standing
                                   // over this card: the first is under Home, the
                                   // third under TV, and that is what up means to
                                   // anybody looking at the screen. The framework's
                                   // own search takes the nearest thing up and to the
                                   // left, which from the first cards is the server
                                   // picker - so the row is not left to find out.
                                   .edge(up = {
                                       val across = columnsNow(gridState.layoutInfo)
                                       val x = browse.cardAt[m.ratingKey]
                                       if (at < maxOf(across, 1) && x != null) {
                                           moveTo(tabOver(x))
                                       } else {
                                           // Straight up, by name.
                                           stepTo(at - maxOf(across, 1), shown,
                                                  browse, gridState, gridScope,
                                                  down = false)
                                       }
                                   }, down = {
                                       // Straight down: the one in this column, a row
                                       // on.
                                       //
                                       // Left to the framework it works until the page
                                       // has to scroll, and then the poster it is
                                       // looking for does not exist yet - so the search
                                       // settles for whatever it can see, which is the
                                       // first poster in the row. Naming the film we
                                       // want and scrolling to it cannot lose the
                                       // column.
                                       val across = maxOf(
                                           columnsNow(gridState.layoutInfo), 1)
                                       stepTo(at + across, shown, browse, gridState,
                                              gridScope, down = true)
                                   }),
                               // held: the menu. On the watchlist that is the one
                               // about the watchlist; anywhere else it is the title's,
                               // which opened its page instead - the hold went past
                               // to the title while the button was still down. Go to
                               // title is the first thing in the menu.
                               onHold = {
                                   if (m.type == "collection") openShelf(browse, m)
                                   else if (browse.tab == "watchlist") browse.favHeld = m
                                   else {
                                       browse.openedAt = gridState.firstVisibleItemIndex to
                                           gridState.firstVisibleItemScrollOffset
                                       browse.held = m
                                   }
                               }) {
                            // a shelf is not a title: it opens into what it holds
                            if (m.type == "collection") openShelf(browse, m)
                            else {
                                browse.openedKey = m.ratingKey
                                browse.openedAt = gridState.firstVisibleItemIndex to
                                    gridState.firstVisibleItemScrollOffset
                                onOpen(m)
                            }
                        }
                    }
                    // favorites: a row of their own under the watchlist
                    if (browse.tab == "watchlist" && browse.collectionOn == null &&
                        browse.favs.isNotEmpty()) {
                        item(span = {
                            androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan)
                        }) {
                            SectionTitle("Favorites",
                                         Modifier.padding(start = 7.dp, top = 18.dp,
                                                          bottom = 2.dp))
                        }
                        items(collectionOrder(browse.favs, browse.collSortKey,
                                              browse.collSortAsc)) { m ->
                            Poster(m, fill = true, onHold = { browse.favHeld = m }) {
                                onOpen(m)
                            }
                        }
                    }
                }
                }   // the steady scroll spec ends with the grid
            }
        }
    }
    }
}

/**
 * Say something went wrong, or ask for something.
 *
 * Deliberately two taps and a sentence: anything longer and nobody writes it, and a
 * sentence with a name attached is enough to find the fault.
 */
@Composable
private fun ReportDialog(startAs: String = "problem", onClose: () -> Unit) {
    val ctx = LocalContext.current as AppCompatActivity
    // opened from the Errors tab it is a fault; from Requests it is a request. The
    // tab somebody is on says which they meant.
    var kind by remember { mutableStateOf(startAs) }
    var text by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = { Text("Tell me about it", color = Skin.Fg) },
        text = {
            Column {
                Row {
                    Pill("Something is broken", kind == "problem") { kind = "problem" }
                    Pill("A request", kind == "request") { kind = "request" }
                }
                TextBox(text,
                        if (kind == "problem") "Which film, and what happened?"
                        else "What should it do?",
                        Modifier.padding(top = 12.dp).fillMaxWidth(),
                        multiline = true) { text = it }
            }
        },
        confirmButton = {
            Pill(if (sending) "Sending\u2026" else "Send", primary = true) {
                if (!sending && text.isNotBlank()) {
                    sending = true
                    ctx.lifecycleScope.launch {
                        val ok = Api.report(ctx, kind, text.trim())
                        Toast.makeText(ctx, if (ok) "Sent - thank you"
                                             else "Could not send that",
                                       Toast.LENGTH_SHORT).show()
                        onClose()
                    }
                }
            }
        },
        dismissButton = { Pill("Cancel") { onClose() } },
    )
}

/**
 * The order the titles inside a collection are shown and played in.
 *
 * Sorted here rather than asked for: a collection arrives whole, so turning it round
 * is arithmetic on a list already in hand and needs no second fetch.
 */
/** A download's time left, the way a person says it. */
private fun etaWords(seconds: Long): String = when {
    seconds < 60 -> "$seconds s left"
    seconds < 3600 -> "${seconds / 60} min left"
    else -> "${seconds / 3600} h ${seconds % 3600 / 60} min left"
}

/** A collection opened: the sort and filters it was saved with come with it. */
private fun openShelf(browse: Browse, shelf: Media) {
    runCatching {
        val saved = org.json.JSONObject(shelf.shelfView.ifEmpty { "{}" })
        saved.optString("sort").takeIf { it.isNotEmpty() }?.let {
            browse.collSortKey = it
            browse.collSortAsc = saved.optString("dir", "asc") != "desc"
        }
        browse.genre = saved.optString("genre", "")
        browse.decade = saved.optString("decade", "")
    }
    browse.collectionOn = shelf
}

/** A poster held: its own page, and for an episode its programme's, which is where an
 *  episode is found. */
/**
 * One card settled, on the shelves as they already stand.
 *
 * Marking something read every shelf again from the server, which redrew the whole
 * page: the card the remote was on moved, the scroll went back to the start, and the
 * shelf the press was made on flickered. Nothing here has changed except this one
 * title, and this changes exactly that.
 *
 * Continue watching is what is unfinished, so a card marked either way has just been
 * settled and leaves it - the row closes the gap on its own. Every other shelf keeps
 * the card and only its tick changes.
 */
private fun cardMarked(browse: Browse, one: Media, watched: Boolean) {
    val key = one.ratingKey
    fun tick(list: List<Media>) =
        list.map { if (it.ratingKey == key) it.copy(watched = watched) else it }
    // The card the remote was on is about to go, and focus goes with it - to the top
    // of the page, which is a long way back from the shelf the press was made on. It
    // is handed to the neighbour first: the card that slides into the place this one
    // is leaving, or the one before it at the end of a row.
    browse.rows.firstOrNull { it.first == DECK }?.second?.let { list ->
        val at = list.indexOfFirst { it.ratingKey == key }
        if (at >= 0) {
            val next = list.getOrNull(at + 1) ?: list.getOrNull(at - 1)
            browse.focusKey = next?.ratingKey.orEmpty()
        }
    }
    browse.rows = browse.rows.map { (title, list) ->
        title to (if (title == DECK) list.filterNot { it.ratingKey == key }
                  else tick(list))
    }
    browse.grid = tick(browse.grid)
    browse.favs = tick(browse.favs)
}


/**
 * A shelf, asked for again if it could not be had.
 *
 * The first request of a session is the one most likely to fail: the server has just
 * been reached for, or is still starting, or the wi-fi has not settled. Giving up on it
 * looked exactly like a shelf with nothing on it.
 *
 * Three tries, a second or two apart, and then it really is empty. A shelf that answers
 * with nothing is not retried - that is an answer.
 */
private suspend fun askAgain(get: suspend () -> List<Media>): List<Media> {
    repeat(3) { at ->
        try {
            return get()
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped              // the page moved on: not a failure
        } catch (e: Exception) {
            if (at == 2) {
                android.util.Log.i("Palladium", "shelf gave up: " + (e.message ?: ""))
                return emptyList()
            }
            kotlinx.coroutines.delay(1200L * (at + 1))
        }
    }
    return emptyList()
}


private fun goToTitle(ctx: android.content.Context, one: Media, onOpen: (Media) -> Unit) {
    if (one.type != "episode") {
        onOpen(one)
        return
    }
    // The season it is in, opened on the episode itself. Going to the programme put
    // the page at the top of its first season, which for something twelve seasons in
    // is a long way from what was being watched - and the row it opens on is the one
    // thing somebody going there wants to see.
    MainActivity.reveal.value = one.ratingKey
    val show = one.grandparentKey?.takeIf { it.isNotEmpty() }
    if (show == null) {
        MainActivity.reveal.value = null
        onOpen(one)
        return
    }
    (ctx as AppCompatActivity).lifecycleScope.launch {
        val programme = Api.item(show, one.srv)
        // The season is taken from the programme's own children rather than asked for
        // by its key: a season key answers with the programme it belongs to, so
        // opening it landed back on the list of seasons - which is where this started.
        val seasons = programme?.let {
            runCatching { Api.children(it) }.getOrDefault(emptyList())
        }.orEmpty()
        val season = seasons.firstOrNull { it.ratingKey == one.parentKey }
            ?: seasons.firstOrNull { it.type == "season" && it.index == one.parentIndex }
        when {
            season != null -> onOpen(season)
            programme != null -> { MainActivity.reveal.value = null; onOpen(programme) }
            else -> MainActivity.reveal.value = null
        }
    }
}

private fun collectionOrder(list: List<Media>, key: String, asc: Boolean): List<Media> {
    val by = when (key) {
        "addedAt" -> compareBy<Media> { it.addedAt }
        "titleSort" -> compareBy { it.titleSort.ifEmpty { it.title }.lowercase() }
        "year" -> compareBy { it.year ?: 0 }
        "quality" -> compareBy { it.maxHeight }
        // a date where there is one, the year otherwise
        else -> compareBy {
            it.released.ifEmpty { (it.year ?: 0).toString().padStart(4, '0') + "-01-01" }
        }
    }
    val out = list.sortedWith(by.thenBy { it.title.lowercase() })
    return if (asc) out else out.reversed()
}

/**
 * The order the library is in, as one control.
 *
 * It rides among the tabs rather than after them: five tabs, a genre and a search box
 * do not fit across a phone, and the sort was the thing that ended up off the right of
 * the row - the one control somebody reaches for on every visit to a list.
 */
@Composable
private fun RowScope.SortControl(
    sortKey: String,
    sortAsc: Boolean,
    onSort: (String) -> Unit,
    onFlip: () -> Unit,
) {
    val orders = listOf("addedAt" to "Added", "originallyAvailableAt" to "Released",
                        "titleSort" to "Name", "year" to "Year",
                        "quality" to "Quality")
    var open by remember { mutableStateOf(false) }
    val chosen = orders.firstOrNull { it.first == sortKey }?.second ?: "Sort"
    // Where the remote goes when the list closes. Without this the focus falls out of
    // a menu that is no longer there onto whatever is nearest - which from here is the
    // next tab along, so choosing an order landed somebody on TV.
    val pill = remember { FocusRequester() }
    var wasOpen by remember { mutableStateOf(false) }
    LaunchedEffect(open) {
        if (open) wasOpen = true
        else if (wasOpen) {
            kotlinx.coroutines.delay(60)
            runCatching { pill.requestFocus() }
        }
    }
    Box {
        // the order and its direction on one control: the arrows belong to the thing
        // they describe rather than sitting beside it
        Pill(chosen + " " + (if (sortAsc) "⇅" else "⇵"), narrow = !onTv(),
             small = !onTv() && LocalConfiguration.current.screenWidthDp < 400,
             modifier = Modifier.focusRequester(pill)) {
            open = true
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false },
                     modifier = Modifier.background(Skin.Panel)) {
            orders.forEachIndexed { n, (key, label) ->
                DropdownMenuItem(
                    // Out of the list without pressing Back: up off the top line, and
                    // left or right from any of them. A list that can only be left by
                    // Back is a list somebody is stuck in, and back on a remote means
                    // leave the app to most people.
                    modifier = Modifier.edge(up = { n == 0 && run { open = false; true } },
                                             left = { open = false; true },
                                             right = { open = false; true }),
                    text = {
                        Text(label + (if (key == sortKey)
                                          (if (sortAsc) "  ↑" else "  ↓") else ""),
                             color = if (key == sortKey) Skin.Accent else Skin.Fg)
                    },
                    // choosing the one already chosen turns it round
                    onClick = {
                        if (key == sortKey) onFlip() else onSort(key)
                        open = false
                    })
            }
        }
    }
}

/** Which shelf: built from what the library holds, so it never offers an empty one. */
@Composable
private fun RowScope.GenreControl(
    genre: String,
    genres: List<Pair<String, Int>>,
    /** titles carrying all marked genres, when two or more are marked; -1 while not known */
    matching: Int = -1,
    onGenre: (String) -> Unit,
) {
    if (genres.isEmpty()) return
    var genreOpen by remember { mutableStateOf(false) }
    //: where the remote goes when the list closes, so it is not left to the nearest
    //: thing on the screen - which is the next tab along
    val pill = remember { FocusRequester() }
    var wasOpen by remember { mutableStateOf(false) }
    LaunchedEffect(genreOpen) {
        if (genreOpen) wasOpen = true
        else if (wasOpen) {
            kotlinx.coroutines.delay(60)
            runCatching { pill.requestFocus() }
        }
    }
    //: the line last ticked, to put the remote back on it when the list is read again
    var touched by remember { mutableStateOf("") }
    // several can be marked: comma-joined, a title must carry all of them
    val marked = genre.split(",").map { it.trim() }.filter { it.isNotEmpty() }
    Box {
        // A tick-list reads as "any of these" to most people, and this one means
        // "all of these" - which is why the number falls as more are marked. The plus
        // says so on the button and the line over the list says so in words. A bare
        // number beside a word read as how many were marked, which it never was.
        Pill(when (marked.size) {
                 0 -> "Genre"
                 1 -> marked[0]
                 2 -> marked.joinToString(" + ") +
                      (if (matching >= 0) "  ·  " + matching else "")
                 else -> marked.size.toString() + " genres" +
                         (if (matching >= 0) "  ·  " + matching + " matches" else "")
             },
             narrow = !onTv(),
             small = !onTv() && LocalConfiguration.current.screenWidthDp < 400,
             modifier = Modifier.focusRequester(pill)) {
            genreOpen = true
        }
        DropdownMenu(
            expanded = genreOpen, onDismissRequest = { genreOpen = false },
            modifier = Modifier.background(Skin.Panel)
                .width(260.dp).heightIn(max = 360.dp)) {
            // what marking two of them does, said before anybody marks the second
            Text("Titles carrying all marked", color = Skin.Dim, fontSize = 11.sp,
                 modifier = Modifier.padding(start = 14.dp, top = 8.dp, bottom = 2.dp))
            DropdownMenuItem(
                contentPadding = MENU_PAD,
                // up off the top line is the way out, the same as it is everywhere
                // else on the screen. Back was the only way, and back on a remote
                // means leave the app to most people.
                modifier = Modifier.edge(up = { genreOpen = false; true },
                                         left = { genreOpen = false; true },
                                         right = { genreOpen = false; true }),
                text = { Text("Clear", fontSize = 14.sp,
                              color = if (marked.isEmpty()) Skin.Dim else Skin.Accent) },
                onClick = { onGenre(""); genreOpen = false })
            genres.forEach { (name, count) ->
                val on = marked.any { it.equals(name, ignoreCase = true) }
                val here = remember(name) { FocusRequester() }
                LaunchedEffect(name, touched, genres) {
                    if (touched == name) runCatching { here.requestFocus() }
                }
                DropdownMenuItem(
                    contentPadding = MENU_PAD,
                    modifier = Modifier.focusRequester(here)
                        .edge(left = { genreOpen = false; true },
                              right = { genreOpen = false; true }),
                    text = { Text((if (on) "✓  " else "     ") + name + "  (" + count + ")",
                                  fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                  color = if (on) Skin.Accent else Skin.Fg) },
                    // toggles and stays open, so several can be marked in one visit
                    onClick = {
                        touched = name
                        val next = if (on) marked.filterNot { it.equals(name, ignoreCase = true) }
                                   else marked + name
                        onGenre(next.joinToString(","))
                    })
            }
        }
    }
}

/** From when: built from what the library holds, so it never offers an empty one. */
/**
 * Move the remote to the poster at [to], bringing it on screen if it is not yet there.
 *
 * The film is named rather than searched for: a lazy grid composes what is on screen
 * and a little beyond, so the row below the fold is not there to be found when the key
 * is pressed. Asked for by name, it takes the focus as it arrives - and it arrives in
 * the column it was asked for, which is what going straight down means.
 */
private fun stepTo(to: Int, shown: List<Media>, browse: Browse,
                   grid: androidx.compose.foundation.lazy.grid.LazyGridState,
                   scope: kotlinx.coroutines.CoroutineScope,
                   down: Boolean): Boolean {
    if (to < 0 || to >= shown.size) return false
    browse.focusKey = shown[to].ratingKey
    val info = grid.layoutInfo
    val seen = info.visibleItemsInfo
    val on = seen.firstOrNull { it.index == to }
    if (on != null) {
        // On screen already. Only a poster hanging over an edge is worth moving for,
        // and then by the few pixels it hangs over - not by a screenful.
        val over = on.offset.y + on.size.height - info.viewportEndOffset
        val under = on.offset.y - info.viewportStartOffset
        val by = if (over > 0) over.toFloat() else if (under < 0) under.toFloat() else 0f
        if (by != 0f) scope.launch { runCatching { grid.animateScrollBy(by) } }
        return true
    }
    // One row, not a screenful.
    //
    // scrollToItem puts what it is given at the top of the screen, so stepping down
    // off the bottom row threw the page forward and left the remote in the top row.
    // The page moves by exactly one row instead, which leaves the poster being
    // stepped onto in the bottom row where the eye already is - and leaves the poster
    // being stepped off drawn throughout, so the ring is never orphaned mid-scroll.
    val tops = seen.asSequence().map { it.row to it.offset.y }
        .distinctBy { it.first }.sortedBy { it.first }.toList()
    val row = when {
        tops.size >= 2 -> tops[1].second - tops[0].second     // includes the gap
        else -> seen.firstOrNull()?.size?.height ?: 0
    }
    if (row <= 0) {
        scope.launch { runCatching { grid.scrollToItem(to) } }
        return true
    }
    scope.launch {
        runCatching { grid.animateScrollBy(if (down) row.toFloat() else -row.toFloat()) }
    }
    return true
}

@Composable
private fun RowScope.DecadeControl(
    decade: String,
    decades: List<Pair<String, Int>>,
    onDecade: (String) -> Unit,
) {
    if (decades.isEmpty()) return
    var open by remember { mutableStateOf(false) }
    //: where the remote goes when the list closes
    val pill = remember { FocusRequester() }
    var wasOpen by remember { mutableStateOf(false) }
    LaunchedEffect(open) {
        if (open) wasOpen = true
        else if (wasOpen) {
            kotlinx.coroutines.delay(60)
            runCatching { pill.requestFocus() }
        }
    }
    // Which line was last ticked. Marking one sends for the lists again - the numbers
    // are counted among what is marked - and the list that comes back is a new set of
    // rows, so the one under the remote stops existing and the focus falls out of the
    // menu onto whatever is nearest, which is the button beside it. It is put back on
    // the line that was ticked.
    var touched by remember { mutableStateOf("") }
    // several can be marked, comma-joined, the same way genres are. A title need only
    // be from one of them: a film has one year, so asking for all at once would answer
    // nothing at all.
    val marked = decade.split(",").map { it.trim() }.filter { it.isNotEmpty() }
    Box {
        Pill(when (marked.size) {
                 0 -> "Decade"
                 1 -> marked[0] + "s"
                 2 -> marked[0] + "s, " + marked[1] + "s"
                 else -> marked.size.toString() + " decades"
             },
             narrow = !onTv(),
             small = !onTv() && LocalConfiguration.current.screenWidthDp < 400,
             modifier = Modifier.focusRequester(pill)) {
            open = true
        }
        DropdownMenu(
            expanded = open, onDismissRequest = { open = false },
            modifier = Modifier.background(Skin.Panel)
                .width(200.dp).heightIn(max = 360.dp)) {
            // the opposite of the genres, and for a reason worth one line: a film has
            // one year, so asking for all the marked decades at once answers nothing
            Text("Titles from any marked", color = Skin.Dim, fontSize = 11.sp,
                 modifier = Modifier.padding(start = 14.dp, top = 8.dp, bottom = 2.dp))
            DropdownMenuItem(
                contentPadding = MENU_PAD,
                modifier = Modifier.edge(up = { open = false; true },
                                         left = { open = false; true },
                                         right = { open = false; true }),
                // named and coloured as the genres are: two lists that do the same
                // thing should not be worked out twice
                text = { Text("Clear", fontSize = 14.sp,
                              color = if (marked.isEmpty()) Skin.Dim else Skin.Accent) },
                onClick = { onDecade(""); open = false })
            decades.forEach { (era, count) ->
                val on = marked.contains(era)
                val here = remember(era) { FocusRequester() }
                LaunchedEffect(era, touched, decades) {
                    if (touched == era) runCatching { here.requestFocus() }
                }
                DropdownMenuItem(
                    contentPadding = MENU_PAD,
                    modifier = Modifier.focusRequester(here)
                        .edge(left = { open = false; true },
                              right = { open = false; true }),
                    text = { Text((if (on) "✓  " else "     ") + era + "s  (" + count + ")",
                                  fontSize = 14.sp,
                                  color = if (on) Skin.Accent else Skin.Fg) },
                    // toggles and stays open, so several can be marked in one visit
                    onClick = {
                        touched = era
                        val next = if (on) marked.filterNot { it == era } else marked + era
                        onDecade(next.joinToString(","))
                    })
            }
        }
    }
}

/** What is left of the row once the sort and the genre have gone up among the tabs. */
@Composable
private fun RowScope.FilterControls(
    query: String,
    onQuery: (String) -> Unit,
    /** said while the box has the cursor, so nothing else asks for it meanwhile */
    onTyping: (Boolean) -> Unit = {},
) {
    // a fixed width: the narrow bar scrolls, where "what is left" means nothing
    TextBox(query, "Search",
            Modifier.width(190.dp)
                .onFocusChanged { onTyping(it.isFocused || it.hasFocus) },
            onValue = onQuery)
    if (query.isNotEmpty()) {
        Spacer(Modifier.width(6.dp))
        Pill("Clear") { onQuery("") }
    }
}

/** The old stacked bar, kept for nothing in particular. */
@Composable
private fun FilterBar(
    query: String,
    onQuery: (String) -> Unit,
    sortKey: String,
    sortAsc: Boolean,
    onSort: (String) -> Unit,
) {
    val arrow = if (sortAsc) " \u2191" else " \u2193"
    // one line: the three orders, then the search box filling whatever is left
    Row(Modifier.padding(start = 16.dp, end = 16.dp, top = 0.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Pill("Added" + (if (sortKey == "addedAt") arrow else ""),
             sortKey == "addedAt") { onSort("addedAt") }
        Pill("Name" + (if (sortKey == "titleSort") arrow else ""),
             sortKey == "titleSort") { onSort("titleSort") }
        Pill("Year" + (if (sortKey == "year") arrow else ""),
             sortKey == "year") { onSort("year") }
        Spacer(Modifier.width(6.dp))
        TextBox(query, "Search", Modifier.weight(1f), onValue = onQuery)
        if (query.isNotEmpty()) {
            Spacer(Modifier.width(8.dp))
            Pill("Clear") { onQuery("") }
        }
    }
}

/** Menu rows are tighter than the page: a television's text size fills a screen fast. */
private val MENU_PAD = androidx.compose.foundation.layout.PaddingValues(
    horizontal = 12.dp, vertical = 2.dp)

// FocusRequester.Cancel - refusing a direction - is still marked experimental
@OptIn(ExperimentalLayoutApi::class, androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
private fun DetailScreen(m: Media, onBack: () -> Unit, onOpen: (Media) -> Unit,
                         onFilter: (String, List<String>) -> Unit = { _, _ -> },
                         /** a name pressed in the cast: everything held with them in it */
                         onPerson: (Media.Player) -> Unit = {}) {
    val ctx = LocalContext.current
    val portrait = LocalConfiguration.current.orientation == Configuration.ORIENTATION_PORTRAIT
    // A phone on its side has about three hundred and sixty points of height, and the
    // page was laid out for a television: a poster two hundred and seventy tall, a
    // title at thirty, and the facts and the subtitle line below all of it, off the
    // bottom of the screen. Everything comes up and in.
    val cramped = !portrait && LocalConfiguration.current.screenHeightDp < 500
    // A television has one screen and nothing to scroll, and what else is like this
    // film sits at the foot of it. The film's own words have to leave room for that,
    // so on a television everything above the shelf comes down a size - which is the
    // difference between a row of posters and a strip of their tops.
    // A series has no shelf under it - its page is a list of its own episodes - so
    // there is nothing there to make room for.
    val roomForShelf = !portrait && !cramped && !m.isFolder
    var full by remember(m.ratingKey) { mutableStateOf(m) }
    var children by remember(m.ratingKey) { mutableStateOf<List<Media>>(emptyList()) }
    //: which mark is waiting to be confirmed: true for watched, false for unwatched
    var marking by remember(m.ratingKey) { mutableStateOf<Boolean?>(null) }
    // index of the chosen subtitle stream, or null for none
    var sub by remember(m.ratingKey) { mutableStateOf<Int?>(null) }
    var styling by remember(m.ratingKey) { mutableStateOf(false) }
    // the cache chooser, for a title the library holds more than once
    var versions by remember(m.ratingKey) { mutableStateOf(false) }
    var downloading by remember(m.ratingKey) { mutableStateOf(false) }
    //: waiting for a subtitle being written, so the remote can be put on Play as soon
    //: as there is enough of it to start
    var watchingMade by remember(m.ratingKey) { mutableStateOf(false) }
    //: what is being written for this film, so the subtitle box can say so
    var making by remember(m.ratingKey) { mutableStateOf<Api.Making?>(null) }
    //: the subtitle box, so the remote comes back to it when the panel closes
    val subsFocus = remember { FocusRequester() }
    //: whether it should land there now, having just come out of the panel
    var backToSubs by remember(m.ratingKey) { mutableStateOf(false) }
    // the subtitle panel belongs to the page, not to the pill that opens it
    var open by remember(m.ratingKey) { mutableStateOf(false) }
    // the page's own copy of the mark, so the button answers before the server does
    var seen by remember(m.ratingKey) { mutableStateOf(m.watched) }
    // and of the watchlist mark, for the same reason
    var listed by remember(m.ratingKey) { mutableStateOf(false) }
    var picking by remember(m.ratingKey) { mutableStateOf(false) }
    var inCollection by remember(m.ratingKey) { mutableStateOf(false) }

    // More like this: the genres this film carries, asked of the library the same way
    // the genre filter asks - every one of them, so three words narrow to the films
    // that are all three. Rare corners of the library answer with nothing and the row
    // is left off rather than filled with something looser.
    var alike by remember(m.ratingKey) { mutableStateOf<List<Media>>(emptyList()) }
    var likeOn by remember(m.ratingKey) { mutableStateOf<List<String>>(emptyList()) }
    // the first of them, for a film that has just run to its end: the page it comes
    // back to is the one it started from, and what to watch next is further down it
    val firstAlike = remember(m.ratingKey) { FocusRequester() }
    //: where this film falls among them in time, so the row can be cut around it
    var alikeAt by remember(m.ratingKey) { mutableStateOf(0) }

    // and again on the way back from the player: the button says "Resume 7:19", and
    // after watching another twenty minutes it should not still say 7:19
    val cameBack = MainActivity.returned.value
    LaunchedEffect(m.ratingKey, cameBack) {
        if (m.isFolder) children = runCatching { Api.children(m) }.getOrDefault(emptyList())
        else runCatching { Api.metadata(m) }.getOrNull()?.let {
            full = it
            // The one chosen last time; failing that a file beside the video, which
            // was put there on purpose; failing that a text track inside the film,
            // which is all a fresh download usually has. Leaving that last case out
            // meant an episode with nothing but its own English track opened with no
            // subtitle at all and nothing to say why.
            sub = it.pickedSub
                ?: it.openWith(Api.myLanguage)?.index
                ?: sub
        }
        // once the film is known, what else carries its genres
        val genres = full.genres.filter { it.isNotBlank() }
        if (!full.isFolder && genres.isNotEmpty()) {
            // Films to download among the ones here, as the Films tab lists them: an
            // old corner of the library is thin, and what is on offer is what fills a
            // row of a film's own years rather than ten from the last decade.
            //
            // All of them, not the first page. A page is a hundred and twenty and it
            // comes newest first, so for a common pair of words everything older than
            // the last few years was never asked for - and a film from 1987 cannot sit
            // in the middle of a list that begins in 2015.
            // Programmes are matched against programmes: an episode's page asking the
            // film section listed films under it.
            val asShow = full.type in setOf("show", "season", "episode")
            val ownKey = full.grandparentKey?.takeIf { it.isNotEmpty() } ?: full.ratingKey
            suspend fun carrying(words: List<String>) = runCatching {
                if (asShow) Api.shows(ctx, "originallyAvailableAt:desc", size = 600,
                                      genre = words.joinToString(","))
                else Api.movies(ctx, "originallyAvailableAt:desc", size = 600,
                                genre = words.joinToString(","))
            }.getOrDefault(emptyList())
                .filter { it.ratingKey != ownKey }
            // Every word this film carries, and only films carrying all of them. The
            // row widens in time rather than in words: where there is nothing older -
            // nothing tagged action, comedy, horror and science fiction is older than
            // 1987 - it takes more from the newer side. Letting a word go instead put
            // films in the row that were not this kind of film at all.
            val words = genres
            val found = carrying(words)
            val mine = full.year ?: 0
            likeOn = words
            android.util.Log.i("Palladium", "more like " + full.title + " (" + mine +
                ") tags=" + words.joinToString("|") + " found=" + found.size)
            // In year order, around this one: as many from before it as from after,
            // so a film from 1983 is shown among its own years rather than under a row
            // of last season's. By the year rather than the day - a film is of a year,
            // and a March release is not older than a September one in any way anybody
            // means. The date only settles the order within a year; anything with no
            // year at all goes to the end of the line.
            fun yearOf(x: Media) = x.year ?: 0
            val inOrder = found.sortedWith(
                compareBy({ yearOf(it) == 0 }, { yearOf(it) }, { it.released }))
            alike = inOrder
            alikeAt = inOrder.indexOfFirst { yearOf(it) >= mine && yearOf(it) > 0 }
                .let { if (it < 0) inOrder.size else it }
        }
        seen = full.watched
        // a mark may have been changed on another screen, or on the television, since
        // this page was last looked at
        listed = runCatching { Api.marked() }.getOrDefault(emptySet())
            .contains(m.ratingKey)
        runCatching { Api.favored() }             // the heart reads what this fills in
        inCollection = Api.shelvesHolding(full).any { it.state != "none" }
    }

    val facts: @Composable () -> Unit = {
        // Six chips - year, size, codecs, rate, how it will play - are wider than a
        // phone held upright. A Row does not wrap, so the last one was squeezed to a
        // single column of letters running down the edge of the screen.
        FlowRow(Modifier.padding(top = if (cramped || roomForShelf) 4.dp else 10.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)) {
            full.year?.let { Chip(it.toString()) }
            if (full.isFolder) {
                // a show or a season has no codecs and nothing to transcode; saying
                // "Transcoded" on a folder was simply wrong
                if (full.subtitle.isNotEmpty()) Chip(full.subtitle)
            } else {
                full.height?.let { Chip(if (it >= 1700) "4K" else "${it}p") }
                full.videoCodec?.let { Chip(it.uppercase()) }
                full.audioCodec?.let { Chip(it.uppercase()) }
                // what the file runs at, which is the number a limit is set against:
                // the browser has always shown it and the app never did
                if (full.bitrate > 0) {
                    Chip(String.format(java.util.Locale.US, "%.1f Mbit/s",
                                       full.bitrate / 1000f))
                }
                Chip(when {
                         full.canDirectPlay() -> "Direct play"
                         full.videoPlaysAsIs() -> "Direct video, sound encoded"
                         else -> "Transcoded"
                     }, accent = !full.canDirectPlay())
            }
        }
    }
    /** The year for a film, the season and episode for an episode - and how long. */
    val when_: @Composable () -> Unit = {
        // beside the year, where somebody deciding what to put on is already looking,
        // rather than down among the codecs
        val line = listOfNotNull(
            if (full.type == "movie") full.year?.toString() else full.subtitle,
            runtime(full.durationMs),
        ).filter { it.isNotEmpty() }.joinToString("  ·  ")
        if (line.isNotEmpty()) {
            // straight under the title, not adrift below the buttons
            Text(line, color = Skin.Dim,
                 fontSize = if (roomForShelf) 13.sp else 15.sp,
                 modifier = Modifier.padding(top = if (roomForShelf) 2.dp else 4.dp))
        }
    }

    /**
     * The subtitle choice, as one control that says what is chosen.
     *
     * A row of pills grew with the number of tracks and pushed the rest of the page
     * sideways; a closed menu is the same information in one line.
     */
    val subtitles: @Composable () -> Unit = {
        val text = full.textSubs()
        val bitmap = full.bitmapSubs()
        // shown even with no tracks at all: a film with none is the one that most
        // needs the Download entry inside this menu
        if (!full.isFolder) {
            val chosen = (text + bitmap).firstOrNull { it.index == sub }
            // A release name is forty characters of "1080p.BluRay.x264-GROUP", which
            // on a phone lying down is the whole width of the screen for something
            // nobody reads twice. The panel behind the pill says it in full.
            fun short(name: String) =
                if (!cramped || name.length <= 22) name else name.take(21) + "…"
            // one written from the sound of this film, once there is a file
            val fromSound = text.firstOrNull {
                it.index < 0 && it.label.contains("ai-gen", true)
            }
            val label = when {
                // A subtitle being written for this film, unless somebody has since
                // chosen another: their choice is the answer, and this is only news.
                making?.on == true &&
                    (sub == PENDING_SUB ||
                     (fromSound != null && chosen === fromSound)) ->
                    if (fromSound != null)
                        "Subtitles: written from the sound  ·  ready to play  " +
                        "·  " + ((making?.at ?: 0f) * 100).toInt() + "%"
                    else
                        "Subtitles: written from the sound  ·  " +
                        ((making?.at ?: 0f) * 100).toInt() + "%, not yet enough to start"
                chosen == null -> "Subtitles: off"
                bitmap.any { it.index == chosen.index } ->
                    "Subtitles: " + short(chosen.shown().ifEmpty { "image" }) +
                        (if (cramped) "" else " (burned in)")
                // shown() leads with the language, which is the thing worth knowing
                // before pressing play: "Forced" alone never said German
                else -> "Subtitles: " + short(chosen.shown().ifEmpty { "track" })
            }
            FlowRow(Modifier.padding(top = if (cramped) 4.dp else 12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)) {
              // the pill says what is on; the panel behind it is the browser's, which
              // is the one arrangement that fits a list of long release names
              Pill(label, modifier = Modifier.focusRequester(subsFocus)) { open = true }
              LaunchedEffect(backToSubs) {
                  if (backToSubs) {
                      backToSubs = false
                      // out of the panel and back onto the box it was opened from,
                      // rather than at the top of the page
                      kotlinx.coroutines.delay(60)
                      runCatching { subsFocus.requestFocus() }
                  }
              }
              // how they look, for this title, before pressing play. The same
              // ground as the watchlist and shuffle marks beside it: two letters on
              // a dark page look like a caption, not something to press.
              Pill("Aa", filled = true) { styling = true }
              // Two files of the same film carry different subtitles and are out by
              // different amounts, so which one is playing has to be settled here.
              if (full.copies.size > 1) {
                  val copy = full.copies.firstOrNull { it.mi == full.mi }
                  Pill("Copy: " + (copy?.brief() ?: (full.mi + 1).toString()) +
                       "  (" + full.copies.size + ")") { versions = true }
              }
            }
        }
    }
    /**
     * The two marks, for whatever this page is showing.
     *
     * A series, one of its seasons, an episode or a film - all four are things
     * somebody means to watch or would put on, and only the last two used to have
     * anywhere to say so. The two halves are independent: a comfort film can be in
     * the shuffle without ever being something anybody is waiting to watch.
     */
    val marks: @Composable () -> Unit = {
        Row(Modifier.padding(top = if (cramped || roomForShelf) 6.dp else 12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            // one button stepping through three: off, on the watchlist, a favorite
            val favorite = m.ratingKey in Api.favKeys.value
            MarkControl(
                listed = listed, favorite = favorite, inCollection = inCollection,
                onList = {
                    MainActivity.marksTouched.value++
                    (ctx as AppCompatActivity).lifecycleScope.launch {
                        when {
                            favorite -> {
                                Api.favorite(full, false)
                                Api.mark(full, false)
                                listed = false
                            }
                            listed -> Api.favorite(full, true)
                            else -> {
                                Api.mark(full, true)
                                listed = true
                            }
                        }
                    }
                },
                onCollection = { picking = true })
        }
        if (picking) {
            ShelfPicker(full, emptyList(), onClose = { picking = false }) { now ->
                inCollection = now.any { it.state != "none" }
                MainActivity.marksTouched.value++
            }
        }
    }

    val buttons: @Composable () -> Unit = {
        if (!full.isFolder) {
            val resume = full.viewOffsetMs / 1000
            // On a television nothing has focus until something asks for it, and the
            // first press of the remote should start the film rather than hunt for it.
            val playFocus = remember { FocusRequester() }
            // and for a title with no file, the button that is there instead: Download
            // on something a pack carries, Request on something nobody has at all
            val actFocus = remember { FocusRequester() }
            LaunchedEffect(full.ratingKey, full.offered, full.askable) {
                runCatching {
                    if (full.offered || full.askable) actFocus.requestFocus()
                    else playFocus.requestFocus()
                }
            }
            // Wrapping, not a straight row: an episode has a fourth button and four
            // of them do not fit. Off a television the three that matter - Resume,
            // From start and Mark watched - are narrow enough to stand on one line,
            // which they were not at the full width: 997 points of pill in 974 of room.
            val narrowRow = !onTv()
            // and smaller still held upright, where the widest case - a film resumed
            // an hour and a half in, so "Resume 1:37:04" rather than "Resume 5:08" -
            // was three points over the room and put Mark watched on a line of its own
            val smallRow = narrowRow && portrait
            FlowRow(Modifier.padding(top = if (cramped) 6.dp else 14.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // Pressing Play with a subtitle that has no file yet: say so once,
                // and start without it on the second press. The player turns it on by
                // itself as soon as there is something to draw.
                var saidNotReady by remember(m.ratingKey) { mutableStateOf(false) }
                val startIt: (Long) -> Unit = { at ->
                    val waiting = sub == PENDING_SUB &&
                        full.textSubs().none {
                            it.index < 0 && it.label.contains("ai-gen", true)
                        }
                    if (waiting && !saidNotReady) {
                        saidNotReady = true
                        android.widget.Toast.makeText(
                            ctx, "The subtitle is not ready yet - press again to " +
                                 "start without it",
                            android.widget.Toast.LENGTH_LONG).show()
                    } else if (waiting) {
                        // No subtitle at all rather than whichever the language would
                        // have chosen - and the film is told what was asked for, so
                        // it comes on there as soon as there is enough of it.
                        android.widget.Toast.makeText(
                            ctx, "Starting without subtitles - the one being written " +
                                 "comes on when it is ready",
                            android.widget.Toast.LENGTH_LONG).show()
                        ctx.startActivity(
                            playIntent(ctx, full, at, NO_SUBS)
                                .putExtra("wantMade", true)
                                .also { go ->
                                    if (m.shuffleId.isNotEmpty()) {
                                        go.putExtra("casual", true)
                                        go.putExtra("shelf", m.shuffleId)
                                    }
                                })
                    } else {
                        full.shuffleId = m.shuffleId
                        // and from the main server if this page was drawn from the
                        // copy: asking that machine for its own key first, because
                        // one machine's key means nothing on another
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            val here = (Api.atHome(ctx, full) ?: full).also {
                                it.shuffleId = m.shuffleId
                            }
                            play(ctx, here, at, sub.takeIf { it != PENDING_SUB })
                        }
                    }
                }

                // A subtitle being written for this film: when there is enough of
                // it to start, say so and put the remote on Play.
                LaunchedEffect(watchingMade, m.ratingKey) {
                    if (!watchingMade) return@LaunchedEffect
                    var told = false
                    while (watchingMade) {
                        val said = Api.makingSubtitles(full)
                        making = if (said.on && (said.key.isEmpty() ||
                                                 said.key == full.ratingKey)) said
                                 else null
                        val fresh = runCatching { Api.metadata(full) }.getOrNull()
                        val made = fresh?.textSubs()?.firstOrNull {
                            it.index < 0 && it.label.contains("ai-gen", true)
                        }
                        // Somebody may have chosen another subtitle in the
                        // meantime; that choice stands, and this only says its piece.
                        // the placeholder still stands: replace it with the real
                        // track. Anything else was chosen by hand and is left alone.
                        val theirs = sub != null && sub != PENDING_SUB &&
                            sub != made?.index
                        if (made != null && !told) {
                            told = true
                            fresh?.let { full = it }
                            if (!theirs) sub = made.index
                            android.widget.Toast.makeText(
                                ctx, "Subtitles ready - the rest arrives while it plays",
                                android.widget.Toast.LENGTH_LONG).show()
                            if (!theirs) runCatching { playFocus.requestFocus() }
                        }
                        if (!said.on) {
                            making = null
                            watchingMade = false      // finished, stopped, or failed
                        }
                        kotlinx.coroutines.delay(5000)
                    }
                }
                // squeezed only beside From start, and the rest of the row with it:
                // one height for every button on the line
                // a film on offer from a torrent pack: the one thing to do is fetch it
                if (full.offered) {
                    var said by remember(m.ratingKey) { mutableStateOf("") }
                    // while it comes in: read again every few seconds, until it is done
                    // and once it has come in, the film's own page: the server answers the
                    // offer with the film, and this page becomes it
                    LaunchedEffect(full.ratingKey, full.offerState) {
                        while (full.offered &&
                               full.offerState in setOf("queued", "downloading", "done")) {
                            kotlinx.coroutines.delay(5000)
                            runCatching { Api.metadata(full) }.getOrNull()?.let { full = it }
                        }
                    }
                    // progress from the live list: the metadata re-read every 5 s carries no
                    // download progress for a pack film, so the page stayed at 0%
                    val live = Api.liveOffer(full)
                    // the live row goes when the download finishes; the metadata still
                    // says downloading, at the per cent it was read at
                    val showing = if (live === full && Api.cameIn(full.ratingKey)) "done"
                                  else live.offerState
                    val busy = full.offerRefused.isNotEmpty() ||
                        showing in setOf("queued", "downloading", "done")
                    Pill(if (full.offerRefused.isNotEmpty()) "Cannot download" else when (showing) {
                             "downloading" -> "Downloading " + (live.offerProgress * 100).toInt() + "%" +
                                 (if (live.offerMbit > 0)
                                      String.format(java.util.Locale.US, "  \u00b7  %.1f Mbit/s",
                                                    live.offerMbit) else "") +
                                 (if (live.offerEta >= 0) "  \u00b7  " + etaWords(live.offerEta) else "")
                             "queued" -> "Queued" +
                                 (if (live.offerPlace > 0) " · ${live.offerPlace} ahead" else "")
                             "done" -> "Downloaded - arriving"
                             else -> "\u2913 Download"
                         } + (if (full.offerSize > 0)
                                  String.format(java.util.Locale.US, "  %.1f GB",
                                                full.offerSize / 1e9) else "") +
                             (if (full.offerFree >= 0)
                                  String.format(java.util.Locale.US, "  ·  %.0f GB free",
                                                full.offerFree) else ""),
                         primary = !busy,
                         modifier = Modifier.focusRequester(actFocus)) {
                        // A film on a pack is fetched from here, as it always was.
                        // Asking is for a title with no file anywhere - there is
                        // nothing to fetch for one of those.
                        if (!busy) (ctx as AppCompatActivity).lifecycleScope.launch {
                            val (ok, words) = Api.torrentGet(full)
                            said = if (ok) "Downloading - it appears in Films when it has arrived"
                                   else words
                            Api.metadata(full)?.let { full = it }
                            Api.refreshDownloading(ctx)
                        }
                    }
                    if (live.offerState == "queued" || live.offerState == "downloading") {
                        Pill("Cancel download") {
                            (ctx as AppCompatActivity).lifecycleScope.launch {
                                val (ok, words) = Api.torrentCancel(full)
                                said = words
                                if (ok) Api.metadata(full)?.let { full = it }
                                Api.refreshDownloading(ctx)
                            }
                        }
                    }
                    // one poster for a film its pack carries more than once: the release is
                    // chosen here, and Download fetches the one chosen
                    if (full.offerVersions.size > 1) {
                        full.offerVersions.forEach { (key, label) ->
                            Pill(label, outline = key == full.ratingKey, small = true) {
                                if (key != full.ratingKey) (ctx as AppCompatActivity).lifecycleScope.launch {
                                    val was = full
                                    Api.metadata(was.copy(ratingKey = key).also { it.srv = was.srv })
                                        ?.takeIf { it.offered }?.let { full = it; said = "" }
                                }
                            }
                        }
                    }
                    val shown = said.ifEmpty { full.offerRefused }
                    if (shown.isNotEmpty()) {
                        Text(shown, color = Skin.Dim, fontSize = 13.sp,
                             modifier = Modifier.padding(start = 4.dp, top = 8.dp))
                    }
                }
                // New on streaming and nowhere in this house: no file, no pack, and
                // nothing to play. Asking is the whole of what this page can do.
                if (full.askable) {
                    var said by remember(m.ratingKey) { mutableStateOf("") }
                    var askedAlready by remember(m.ratingKey) { mutableStateOf(full.asked) }
                    // how many are waiting on it, beside the button: one is the
                    // person holding the remote and goes without saying
                    // one press each: the count is people, not presses, so it
                    // says at least one as soon as this viewer is one of them
                    val waiting = maxOf(full.asks, if (askedAlready) 1 else 0)
                    Pill((if (askedAlready) "Requested" else "Request") +
                         (if (waiting > 0) "  \u00b7  $waiting" else ""),
                         primary = !askedAlready,
                         modifier = Modifier.focusRequester(actFocus)) {
                        if (!askedAlready) (ctx as AppCompatActivity).lifecycleScope.launch {
                            val (ok, listed) = Api.askFor(full)
                            askedAlready = ok
                            said = when {
                                !ok -> "Could not ask for that just now"
                                listed -> "Asked for, and on your watchlist."
                                else -> "Asked for. The owner decides what comes in."
                            }
                            if (ok) Toast.makeText(
                                ctx,
                                if (listed) "Added to your watchlist"
                                else "Already on your watchlist",
                                Toast.LENGTH_SHORT).show()
                        }
                    }
                    if (said.isNotEmpty()) {
                        Text(said, color = Skin.Dim, fontSize = 13.sp,
                             modifier = Modifier.padding(start = 4.dp, top = 8.dp))
                    }
                }
                if (!full.offered && !full.askable) Pill(if (resume > RESUME_FROM) "Resume " + fmt(resume) else "Play", primary = true,
                     narrow = narrowRow && resume > RESUME_FROM, small = smallRow && resume > RESUME_FROM,
                     modifier = Modifier.focusRequester(playFocus)) {
                    startIt(resume)
                }
                if (resume > RESUME_FROM && !full.offered && !full.askable) Pill("From start", narrow = narrowRow,
                                     small = smallRow) {
                    startIt(0)
                }
                // filled in once the film has been watched; otherwise plain, with
                // the white ring under the remote like everything else
                if (!full.offered && !full.askable) Pill(if (seen) "\u2713 Watched" else "Mark watched",
                     active = seen, narrow = narrowRow, small = smallRow && resume > RESUME_FROM) {
                    seen = !seen
                    // Watched means finished, so there is nothing left to resume and
                    // the button goes back to Play. The server drops the resume point
                    // either way: marking writes progress at the full duration,
                    // unmarking deletes the row.
                    full = full.copy(viewOffsetMs = 0)
                    (ctx as AppCompatActivity).lifecycleScope.launch {
                        Api.setWatched(full, seen)
                    }
                }
                // An episode is an island otherwise. The season it is in rather
                // than the series: the list somebody wants is the one this episode is
                // in, standing on the episode itself, not a row of season posters.
                if (full.type == "episode" &&
                    !(full.parentKey ?: full.grandparentKey).isNullOrEmpty()) {
                    Pill("Go to show", narrow = narrowRow, small = smallRow && resume > RESUME_FROM) {
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            val where = full.parentKey ?: full.grandparentKey!!
                            MainActivity.reveal.value = full.ratingKey
                            Api.item(where, full.srv)?.let { onOpen(it) }
                        }
                    }
                }
            }
        }
    }
    // The whole of it, with what it is rated and who is in it - the three things the
    // page has no room for. Opened from under the plot, where a reader runs out.
    var reading by remember(m.ratingKey) { mutableStateOf(false) }
    if (reading) {
        AlertDialog(
            onDismissRequest = { reading = false },
            containerColor = Skin.Panel,
            title = { Text(full.title, color = Skin.Fg, fontSize = 18.sp) },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    val rated = listOfNotNull(
                        full.rating.takeIf { it > 0f }
                            ?.let { "★ " + String.format("%.1f", it) + " of 10" },
                        full.year?.takeIf { it > 0 }?.toString(),
                        full.genres.take(3).joinToString(", ").ifEmpty { null },
                    ).joinToString("   ·   ")
                    if (rated.isNotEmpty()) {
                        Text(rated, color = Skin.Dim, fontSize = 12.5.sp,
                             modifier = Modifier.padding(bottom = 10.dp))
                    }
                    Text(full.summary, color = Color(0xFFBFC9D4), fontSize = 14.sp,
                         lineHeight = 21.sp, modifier = Modifier.padding(bottom = 14.dp))
                    if (full.cast.isNotEmpty()) {
                        Text("With", color = Skin.Dim, fontSize = 12.sp,
                             modifier = Modifier.padding(bottom = 6.dp))
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            full.cast.forEach { who ->
                                // a face beside the name: a row of words is a list,
                                // a row of faces is a cast
                                var onIt by remember(who.id) { mutableStateOf(false) }
                                val ring = onIt && focusShows()
                                Row(verticalAlignment = Alignment.CenterVertically,
                                    modifier = Modifier
                                        .clip(RoundedCornerShape(999.dp))
                                        // Panel2, not Panel: the dialog is Panel, so a
                                        // bubble on it was the same colour as the page
                                        .background(if (ring) Skin.Accent else Skin.Panel2)
                                        .border(if (ring) 2.dp else 0.dp,
                                                if (ring) Color.White else Color.Transparent,
                                                RoundedCornerShape(999.dp))
                                        .onFocusChanged { onIt = it.isFocused || it.hasFocus }
                                        .focusable()
                                        .clickable {
                                            reading = false
                                            onPerson(who)
                                        }
                                        .padding(start = 4.dp, end = 12.dp,
                                                 top = 4.dp, bottom = 4.dp)) {
                                    val face = Api.faceUrl(who, full.srv)
                                    if (face != null) {
                                        Art(face, who.name,
                                            Modifier.size(28.dp)
                                                .clip(RoundedCornerShape(999.dp)),
                                            mark = 0)
                                    } else {
                                        Box(Modifier.size(28.dp)
                                                .clip(RoundedCornerShape(999.dp))
                                                .background(Color(0x1AFFFFFF)),
                                            contentAlignment = Alignment.Center) {
                                            Text(who.name.take(1).uppercase(),
                                                 color = Skin.Dim, fontSize = 12.sp,
                                                 fontWeight = FontWeight.Bold)
                                        }
                                    }
                                    Spacer(Modifier.width(8.dp))
                                    Text(who.name, color = if (ring) Color.Black else Skin.Fg,
                                         fontSize = 13.sp)
                                }
                            }
                        }
                    }
                }
            },
            confirmButton = { Pill("Close") { reading = false } })
    }
    val blurb: @Composable () -> Unit = {
        if (full.summary.isNotEmpty()) {
            Text(full.summary, color = Color(0xFFBFC9D4),
                 fontSize = if (cramped) 12.5.sp else if (roomForShelf) 13.sp else 14.sp,
                 lineHeight = if (cramped) 18.sp else if (roomForShelf) 17.sp else 21.sp,
                 // Three lines on a television. The rest of the plot is worth less
                 // than seeing what else is like it, which is what the room went to.
                 maxLines = if (portrait) 8 else if (cramped) 3
                            else if (roomForShelf) 3 else 6,
                 overflow = TextOverflow.Ellipsis,
                 // close under the facts it belongs to, rather than adrift below them
                 modifier = Modifier.padding(top = if (cramped) 6.dp else 6.dp))
            // and the way on: the rest of the plot, what it is rated, and who is in
            // it. Shown whenever there is something more to see - which is any film
            // with a cast, even where the plot happened to fit.
            if (full.summary.length > 180 || full.cast.isNotEmpty()) {
                Pill("Show more", small = true, narrow = true,
                     modifier = Modifier.padding(top = 4.dp)) { reading = true }
            }
        }
    }

    if (open) {
        SubtitlePanel(
            media = full,
            chosen = sub,
            onPick = { which ->
                sub = which
                open = false
                (ctx as AppCompatActivity).lifecycleScope.launch {
                    // A file beside the video is remembered by its name, a track inside
                    // the film by its number. Sending nothing for the second was read
                    // as "subtitles off", so choosing the English track inside a film
                    // never stuck.
                    val named = full.textSubs().firstOrNull { it.index == which }
                    Api.pickSubtitle(full, when {
                        which == null -> ""
                        which < 0 -> named?.label ?: ""
                        else -> "t" + which
                    })
                }
            },
            onVerify = { track, on ->
                (ctx as AppCompatActivity).lifecycleScope.launch {
                    Api.verifySubtitle(full, track, on)
                    runCatching { Api.metadata(full) }.getOrNull()?.let { full = it }
                }
            },
            onDownload = { open = false; downloading = true },
            // only while there is nothing to show for it: once the file is a track
            // of its own, offering this as well is two subtitles for one subtitle
            pending = making?.takeIf { said ->
                said.on && full.textSubs().none { t ->
                    t.index < 0 && t.label.contains("ai-gen", true)
                }
            }?.let { said ->
                "Written from the sound - " + (said.at * 100).toInt() +
                "%, not yet enough to start"
            },
            // no timing here: there is no film on screen to judge it by, and the
            // controls did nothing when pressed
            timing = false,
            onClose = { open = false; backToSubs = true })
    }

    if (downloading) {
        SubtitleDownloadDialog(
            full, "en", (ctx as AppCompatActivity).lifecycleScope,
            // a downloaded track is named for its release, which is what the list shows
            // the track that is on, or - with subtitles off - whatever file has
            // already been downloaded, so a second visit shows what was taken before
            inUse = ((full.textSubs() + full.bitmapSubs())
                .firstOrNull { it.index == sub }
                ?: full.textSubs().lastOrNull { it.index < 0 })?.label ?: "",
            proved = full.subsConfirmed,
            // asked for one to be written: the list has done its work, and the film
            // is what somebody wants to be looking at while it happens
            onStarted = {
                downloading = false
                watchingMade = true
                sub = PENDING_SUB          // chosen, and unchosen by choosing another
            },
            onTaken = { release ->
                (ctx as AppCompatActivity).lifecycleScope.launch {
                    // the file is a track now: read the title again and turn it on -
                    // downloading a subtitle is asking for it, not merely fetching it.
                    // The one that was taken: the last file beside the video is not
                    // necessarily the one just fetched.
                    runCatching { Api.metadata(full) }.getOrNull()?.let { fresh ->
                        full = fresh
                        val bare = { s: String ->
                            s.lowercase().removeSuffix(".srt")
                                .filter { c -> c.isLetterOrDigit() } }
                        val beside = fresh.textSubs().filter { t -> t.index < 0 }
                        val took = beside.firstOrNull { t ->
                            bare(t.label).isNotEmpty() &&
                                (bare(t.label).contains(bare(release)) ||
                                 bare(release).contains(bare(t.label))) }
                        (took ?: beside.lastOrNull() ?: fresh.textSubs().lastOrNull())
                            ?.let { track -> sub = track.index }
                    }
                }
            },
            onClose = { downloading = false })
    }

    if (versions) {
        VersionPanel(
            media = full,
            onPick = { which ->
                versions = false
                // another file, another set of tracks: what was chosen on the last one
                // is a number that means something else here
                val was = full
                full = full.asCopy(which)
                sub = full.pickedSub
                    ?: full.openWith(Api.myLanguage)?.index
                // and it holds: the browser and the television open on it too
                was.copies.getOrNull(which)?.let { copy ->
                    (ctx as AppCompatActivity).lifecycleScope.launch {
                        Api.pickCopy(was, copy)
                    }
                }
            },
            onClose = { versions = false })
    }

    if (styling) {
        SubtitleLookDialog("l" + full.ratingKey, Api.device,
                           (ctx as AppCompatActivity).lifecycleScope,
                           onChanged = {}, onClose = { styling = false })
    }

    Box(Modifier.fillMaxSize()) {
        // The artwork keeps to one side rather than lying under the whole page: on a
        // television the right of the screen, on a phone a band across the top. Either
        // way it fades out before it reaches the poster, so nothing is drawn twice.
        if (Skin.BackdropOnPage) {
            Box(
                if (portrait) Modifier.align(Alignment.TopCenter).fillMaxWidth().fillMaxHeight(0.34f)
                else Modifier.align(Alignment.CenterEnd).fillMaxHeight().fillMaxWidth(0.52f)
            ) {
                // the whole picture, scaled to the height it has: cropping a 2:3 poster
                // into a half-width column cut the top and bottom off it
                Art(Api.artUrl(full), full.title, Modifier.fillMaxSize().alpha(0.45f),
                    mark = 120, scale = androidx.compose.ui.layout.ContentScale.Fit)
                Box(Modifier.matchParentSize().background(
                    if (portrait)
                        Brush.verticalGradient(listOf(Skin.Bg.copy(alpha = 0.35f),
                                                      Skin.Bg.copy(alpha = 0.85f), Skin.Bg))
                    else
                        Brush.horizontalGradient(listOf(Skin.Bg, Skin.Bg.copy(alpha = 0.75f),
                                                        Skin.Bg.copy(alpha = 0.25f)))))
            }
        }

        // One column, sized to fit: with nothing to scroll, moving focus cannot drag
        // the page about. The scroller stays for a series, whose episodes go past the
        // bottom, and for a phone held upright.
        //
        // And for a shelf of what else is like this one, which is the same case: a
        // television has about a hundred points left under the film's own words and
        // the shelf wants twice that, so without this it was a strip of cropped
        // posters at the bottom edge that no amount of pressing down could bring into
        // view - and the page a finished film comes back to could not scroll down to
        // it either, because there was nowhere to scroll to.
        val scroll = rememberScrollState()
        Column(
            Modifier.fillMaxSize()
                // Always, rather than only where there is something below the fold.
                // The shelf arrives a moment after the page does, and adding the
                // scroller then rebuilt the column under whatever had the remote: the
                // tree kept a parent marked as holding focus with nothing inside it,
                // and the next press down threw inside the framework's own search.
                // The guard caught it and dropped the press, so down did nothing at
                // all on a film's page. Where everything fits there is nothing to
                // scroll and this changes nothing.
                .verticalScroll(scroll)
                .padding(horizontal = if (portrait) 20.dp else if (cramped) 18.dp else 28.dp,
                         // less air at the top and bottom where a shelf has to fit
                         // under everything else: the last line of names sat in it
                         vertical = if (portrait) 18.dp else if (cramped) 8.dp
                                    else if (roomForShelf) 12.dp else 22.dp)
        ) {
            Row { Pill("Back") { onBack() } }
            if (portrait) {
                Box(Modifier.padding(top = 14.dp).width(120.dp).height(180.dp)
                        .clip(RoundedCornerShape(10.dp))) {
                    Art(Api.artUrl(full), full.title, Modifier.fillMaxSize(), mark = 44)
                    OfferProgress(full)
                }
                Text(full.title, color = Skin.Fg, fontSize = 24.sp,
                     fontWeight = FontWeight.SemiBold,
                     modifier = Modifier.padding(top = 12.dp))
                // the year belongs to the title, so it goes with it; then the button
                when_(); buttons(); marks(); subtitles(); facts(); blurb()
            } else {
                Row(Modifier.padding(top = if (cramped || roomForShelf) 4.dp else 12.dp)) {
                    Box(Modifier
                            .width(if (cramped) 110.dp
                                   else if (roomForShelf) 150.dp else 180.dp)
                            .height(if (cramped) 165.dp
                                    else if (roomForShelf) 225.dp else 270.dp)
                            .clip(RoundedCornerShape(10.dp))) {
                        Art(Api.artUrl(full), full.title, Modifier.fillMaxSize(),
                            mark = if (cramped) 40 else 60)
                        OfferProgress(full)
                    }
                    Column(Modifier.padding(start = if (cramped) 16.dp else 24.dp)) {
                        Text(full.title, color = Skin.Fg,
                             fontSize = if (cramped) 21.sp
                                        else if (roomForShelf) 25.sp else 30.sp,
                             fontWeight = FontWeight.SemiBold)
                        when_(); buttons(); marks(); subtitles(); facts(); blurb()
                    }
                }
            }
            // A film that ran to its end comes back here with the page at the top and
            // the Play button under the remote, which is the one thing nobody wants
            // next. Down to what is like it instead, once the shelf is actually there.
            LaunchedEffect(alike, PlayerActivity.ranOut) {
                if (PlayerActivity.ranOut == full.ratingKey && alike.isNotEmpty()) {
                    PlayerActivity.ranOut = ""
                    scroll.animateScrollTo(scroll.maxValue)
                    runCatching { firstAlike.requestFocus() }
                }
            }
            if (children.isNotEmpty()) {
                FlowRow(Modifier.padding(top = 10.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    SectionTitle(
                        if (children.first().type == "season") "Seasons" else "Episodes",
                        Modifier.align(Alignment.CenterVertically)
                            .padding(start = 7.dp, end = 10.dp))
                    // The whole series, or the whole season, in one press - and one
                    // press is how a season of places got forgotten by accident, so
                    // it asks first. Plain until the remote reaches them, which is
                    // the only state worth marking.
                    Pill("Mark all watched") { marking = true }
                    Pill("Mark all unwatched") { marking = false }
                    // Up a level. A season is arrived at from an episode now, so the
                    // programme it belongs to is a page that was never opened - and
                    // Back goes where you came from, which is the film or the shelf,
                    // not the other seasons.
                    if (full.type == "season") {
                        val show = full.parentKey?.takeIf { it.isNotEmpty() }
                            ?: full.grandparentKey?.takeIf { it.isNotEmpty() }
                        if (show != null) Pill("↑ Go to show") {
                            (ctx as AppCompatActivity).lifecycleScope.launch {
                                Api.item(show, full.srv)?.let { onOpen(it) }
                            }
                        }
                    }
                }
                marking?.let { seen ->
                    val many = children.size
                    val whole = if (children.firstOrNull()?.type == "season")
                        "this programme" else "this season"
                    AlertDialog(
                        onDismissRequest = { marking = null },
                        containerColor = Skin.Panel,
                        title = { Text(if (seen) "Mark all watched?" else
                                       "Mark all unwatched?", color = Skin.Fg) },
                        text = {
                            Text(if (seen)
                                     "Every episode in " + whole + " - " + many +
                                     " - is marked as seen."
                                 else
                                     "Every episode in " + whole + " - " + many +
                                     " - is marked unseen, and where each was left " +
                                     "is forgotten.",
                                 color = Skin.Dim, fontSize = 14.sp)
                        },
                        confirmButton = {
                            Pill(if (seen) "Mark watched" else "Mark unwatched",
                                 primary = true) {
                                marking = null
                                (ctx as AppCompatActivity).lifecycleScope.launch {
                                    Api.setWatched(full, seen)
                                    children = runCatching { Api.children(full) }
                                        .getOrDefault(children)
                                }
                            }
                        },
                        dismissButton = { Pill("No") { marking = null } })
                }
                // A row of episodes, and the remote walking along it. Two things
                // were wrong on a television: a lazy row composes only what is on
                // screen, so pressing right at the last visible episode found nothing
                // to the right and focus jumped up to Mark all watched instead - and
                // once there, right and left did nothing, which is what being stuck
                // looks like. The row is a focus group so the search stays inside it,
                // and the padding at the end keeps the next episode composed and
                // reachable before it is needed.
                val episodes = rememberLazyListState()
                // Arrived from an episode's own page: stand on that episode rather
                // than at the start of the season. Cleared once used, so opening the
                // season any other way starts where it always did.
                // Scrolling moved the row and left the highlight where it was, so the
                // first press of a direction key took the row back to where it had
                // been - which is indistinguishable from not having gone there at all.
                // The episode is stood on as well as scrolled to.
                val standOn = remember { FocusRequester() }
                var standing by remember(children) {
                    mutableStateOf<String?>(null)
                }
                LaunchedEffect(children, MainActivity.reveal.value) {
                    val want = MainActivity.reveal.value
                    if (!want.isNullOrEmpty()) {
                        val at = children.indexOfFirst { it.ratingKey == want }
                        if (at >= 0) {
                            episodes.scrollToItem(at)
                            standing = want
                            MainActivity.reveal.value = null
                        }
                    }
                }
                LaunchedEffect(standing, children) {
                    if (standing != null) {
                        // composed only after the scroll has settled
                        kotlinx.coroutines.delay(120)
                        runCatching { standOn.requestFocus() }
                        standing = null
                    }
                }
                LazyRow(
                    state = episodes,
                    modifier = Modifier.focusGroup()
                        .focusProperties {
                            // At the last season or episode there is nothing to the
                            // right, and the search leaves the row - upwards, onto
                            // Mark all unwatched, where left and right then do
                            // nothing at all. That is what being stuck looks like.
                            // The end of the row is the end of the row.
                            right = FocusRequester.Cancel
                            left = FocusRequester.Cancel
                        },
                    contentPadding = PaddingValues(end = 180.dp),
                ) {
                    items(children) { c ->
                        // one programme's seasons are one picture four times over, and
                        // its episodes one still each: say which is which under them
                        val say = when {
                            c.type == "season" ->
                                c.title.ifBlank { "Season " + (c.index ?: "") }
                            c.type == "episode" ->
                                "E" + (c.index ?: "") + "  " + c.title
                            else -> ""
                        }
                        Poster(c, width = shelfPoster(), instead = say,
                               modifier = if (c.ratingKey == standing)
                                              Modifier.focusRequester(standOn)
                                          else Modifier) { onOpen(c) }
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
            // More like this: films carrying the same genres as this one. A programme
            // is left out - its page is a list of its own episodes already.
            if (!full.isFolder && alike.isNotEmpty()) {
                // The shelf sits at the foot of the screen, with the film above it.
                // Written as a gap rather than as a weight: this column scrolls on a
                // phone and for a series, and a weight in a scrolling column has
                // nothing to push against. What is left of the screen once the poster,
                // its words and the shelf itself have had their share is the gap.
                // A little smaller than a shelf on the home page: this row shares
                // its screen with a whole film's worth of words above it.
                val likeWide = if (roomForShelf) shelfPoster() * 5 / 8 else shelfPoster()
                // On a television the shelf follows the words rather than the foot of
                // the screen: measuring the gap from what was left meant that every
                // point taken off the cards was handed straight back as air above
                // them, and the row sat exactly where it had been, with its names off
                // the bottom. What is left over stays at the bottom now.
                val roomTaken = (if (cramped) 300 else 400) + likeWide * 3 / 2
                val gap = if (roomForShelf) 10
                          else (LocalConfiguration.current.screenHeightDp - roomTaken)
                              .coerceIn(if (cramped) 10 else 8, 220)
                Spacer(Modifier.height(gap.dp))
                FlowRow(Modifier.fillMaxWidth()
                            .padding(start = 7.dp, end = 14.dp, bottom = 2.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    SectionTitle("More like this",
                                 Modifier.align(Alignment.CenterVertically)
                                     .padding(end = 10.dp))
                    likeOn.forEach { word ->
                        Text("#" + word.lowercase(), color = Skin.Accent, fontSize = 12.sp,
                             fontWeight = FontWeight.SemiBold,
                             modifier = Modifier.align(Alignment.CenterVertically)
                                 .padding(end = 9.dp))
                    }
                    // These words in the tab they came from, where one can be taken
                    // off or a decade put beside them. On the heading rather than at
                    // the end of the row: a row of ten cards fills a television and
                    // the pill after them began past the edge of the screen, where it
                    // showed as a single stray letter.
                    Pill("More ›", small = true,
                         modifier = Modifier.align(Alignment.CenterVertically)) {
                        onFilter(if (full.type == "show") "tv" else "films", likeOn)
                    }
                }
                // the tail of air is for a remote, which scrolls the row under a
                // fixed point; a finger drags the row itself and the gap is waste
                LazyRow(modifier = Modifier.focusGroup()
                            .focusProperties {
                                right = FocusRequester.Cancel
                                left = FocusRequester.Cancel
                            },
                        contentPadding = PaddingValues(
                            start = 7.dp, end = if (onTv()) 180.dp else 14.dp)) {
                    // The window around this film: five from before it and five from
                    // after, and where one side is short the other makes up the ten.
                    // Newest at the left, which is the end a row is read from.
                    val from = (alikeAt - ALIKE_EACH_WAY)
                        .coerceIn(0, maxOf(0, alike.size - ROW_OF_ALIKE))
                    val shown = alike.subList(from, minOf(alike.size, from + ROW_OF_ALIKE))
                        .reversed()
                    itemsIndexed(shown, key = { _, m -> m.ratingKey },
                                    contentType = { _, m -> m.ratingKey }) { at, m ->
                        Poster(m, width = likeWide,
                               modifier = if (at == 0) Modifier.focusRequester(firstAlike)
                                          else Modifier) { onOpen(m) }
                    }
                }
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}

/** How long a film or an episode runs: "1 h 42 min", or "23 min" for a short one. */
/** Five from after this film and five from before it. Where one side runs out - the
 *  oldest thing in the library has nothing before it - the other side makes up the ten. */
private const val ALIKE_EACH_WAY = 5
private const val ROW_OF_ALIKE = ALIKE_EACH_WAY * 2

/**
 * How wide a poster on a shelf is drawn.
 *
 * Decided by the height there is, not the width. A television is 540 points tall
 * whatever its inches, and at the old size the second shelf began below the bottom of
 * the screen - Continue watching and nothing else, on a screen two metres wide. A
 * phone lying down has less height still and was showing a row cut off halfway.
 * Widths that leave room for two whole shelves under the tabs, and for one on a phone
 * on its side.
 */
@Composable
private fun shelfPoster(): Int {
    val cfg = LocalConfiguration.current
    val tall = cfg.screenHeightDp
    // A phone standing up shows three across, whatever width the phone is: the
    // shelves and the grid then agree, and a narrow phone gets three small ones
    // rather than two and a sliver of a third.
    // 32 for the shelf's own margins and 10 for the air each card carries, three
    // times over: without those a "third" card was half off the side of the screen
    val threeAcross = ((cfg.screenWidthDp - 32 - 30) / 3).coerceIn(76, 132)
    // and on its side, five: the width is there, and seven of them was a contact
    // sheet. Never taller than the room under the tabs, whatever the width allows.
    // Measured rather than guessed: on a phone lying down the shelf starts about
    // 170 points down - status bar, the one header line, the tabs, the shelf's name -
    // and the picture is half again as tall as it is wide, so this is what leaves a
    // whole row on the screen.
    val fiveAcross = ((cfg.screenWidthDp - 32 - 50) / 5)
        .coerceIn(76, 150)
        .coerceAtMost(((tall - 190) / 1.5).toInt())
    return when {
        tall < 400 -> fiveAcross     // a phone on its side: one shelf of five
        tall < 560 -> 124        // a television: two whole shelves of picture
        tall < 720 -> threeAcross    // a phone standing up
        else -> 132              // a tablet standing up
    }
}

private fun runtime(ms: Long): String? {
    val mins = Math.round(ms / 60000.0).toInt()
    if (mins < 1) return null
    return if (mins >= 60) "%d h %02d min".format(mins / 60, mins % 60)
           else "%d min".format(mins)
}

private fun fmt(sec: Long): String {
    val h = sec / 3600; val m = (sec % 3600) / 60; val s = sec % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}

private fun play(ctx: Context, m: Media, positionSec: Long, subIndex: Int? = null) {
    // A row the hat is holding stays the hat's when it is pressed. Opening it from
    // Continue watching and playing it wrote an ordinary place instead, so the shelf
    // lost track of its own evening and Next handed over the next episode of the
    // programme rather than drawing.
    val go = playIntent(ctx, m, positionSec, subIndex)
    if (m.shuffleId.isNotEmpty()) {
        go.putExtra("casual", true).putExtra("shelf", m.shuffleId)
    }
    ctx.startActivity(go)
}

/** Everything the player needs, in one place, so the next episode can start itself. */
/**
 * Whether the sound is going somewhere that cannot take Dolby.
 *
 * Bluetooth carries stereo PCM and nothing else, so an AC-3 or E-AC-3 track has to be
 * decoded in the box first - and a television box frequently cannot, having always
 * passed the bits to an amplifier over HDMI instead. Headphones on a Google TV played
 * the picture in silence. Wired and USB headsets are the same case.
 */
fun throughHeadphones(ctx: Context): Boolean {
    val am = ctx.getSystemService(Context.AUDIO_SERVICE) as? android.media.AudioManager
        ?: return false
    val ears = mutableSetOf(
        android.media.AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
        android.media.AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
        android.media.AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
        android.media.AudioDeviceInfo.TYPE_WIRED_HEADSET,
        android.media.AudioDeviceInfo.TYPE_USB_HEADSET,
        android.media.AudioDeviceInfo.TYPE_HEARING_AID)
    // LE Audio is a separate set of types, and newer headphones connect that way
    if (android.os.Build.VERSION.SDK_INT >= 31) {
        ears += setOf(android.media.AudioDeviceInfo.TYPE_BLE_HEADSET,
                      android.media.AudioDeviceInfo.TYPE_BLE_SPEAKER)
    }
    // Where the sound would actually go, rather than everything the box can list.
    //
    // A television box reports the wired and Bluetooth types whether or not anything
    // is plugged into them - Bluetooth being switched on is enough - so this was true
    // on a box whose only connected output was HDMI. Every Dolby film then had its
    // sound decoded and downmixed to stereo AAC for headphones nobody was wearing.
    if (android.os.Build.VERSION.SDK_INT >= 31) {
        val attrs = android.media.AudioAttributes.Builder()
            .setUsage(android.media.AudioAttributes.USAGE_MEDIA)
            .setContentType(android.media.AudioAttributes.CONTENT_TYPE_MOVIE)
            .build()
        val routed = runCatching { am.getAudioDevicesForAttributes(attrs) }.getOrNull()
        if (!routed.isNullOrEmpty()) return routed.any { it.type in ears }
    }
    // Older boxes have no way to ask: the list of outputs is all there is.
    return runCatching {
        am.getDevices(android.media.AudioManager.GET_DEVICES_OUTPUTS)
            .any { it.type in ears }
    }.getOrDefault(false)
}

fun playIntent(ctx: Context, m: Media, positionSec: Long, subIndex: Int? = null,
               audioIndex: Int? = null, height: Int = 0, mbit: Int = 0,
               plainSound: Boolean? = null): Intent {
    // A picture track - PGS off a disc, VobSub off a DVD - is a set of images, and
    // only something that can decode them can show it. The framework decodes the
    // first kind, so a film played straight from disk has its Blu-ray subtitle drawn
    // by the device and never touches the encoder. It has no decoder for the second:
    // a DVD's own subtitle handed to the player threw "unsupported MIME type:
    // application/vobsub" and the episode stopped. Burning is what is left - for
    // those, and for a browser, a cast receiver, or a film being encoded anyway.
    val picture = subIndex?.takeIf { idx -> m.bitmapSubs().any { it.index == idx } }
    val ears0 = plainSound ?: throughHeadphones(ctx)
    val drawable = picture != null && m.deviceCanDraw(picture) &&
                   !ears0 && audioIndex == null &&
                   height == 0 && mbit == 0 && m.canDirectPlay() && m.partKey != null
    val burn = if (drawable) null else picture
    val textSub = subIndex?.takeIf { idx -> m.textSubs().any { it.index == idx } }
    // Headphones get stereo AAC from the start rather than silence corrected a
    // second later: nothing passes through a Bluetooth pair, so the box has to decode
    // and downmix, and some of them answer 5.1 by playing nothing at all. Said
    // outright when somebody has chosen in the player, worked out otherwise.
    val ears = ears0
    val (url, direct) = Api.playbackUrl(
        m, positionSec, burn, audioIndex, height, mbit,
        audio = if (ears) "aac" else "passthrough", channels = if (ears) 2 else 0)
    val (castUrl, castDirect) = Api.castUrl(m, positionSec, picture)
    return Intent(ctx, PlayerActivity::class.java).apply {
        putExtra("url", url)
        putExtra("direct", direct)
        putExtra("castUrl", castUrl)
        putExtra("castDirect", castDirect)
        putExtra("key", m.ratingKey)
        // which file of the title this is: sync, plan and reset all act on one copy
        putExtra("mi", m.mi)
        putExtra("title", m.title)
        putExtra("subtitle", m.subtitle)
        putExtra("art", Api.artUrl(m))
        // progress belongs to whoever holds the film, which may not be the open server
        putExtra("srvBase", m.srv?.base ?: Api.base)
        putExtra("srvToken", m.srv?.token ?: Api.token)
        putExtra("positionSec", positionSec)
        // which soundtrack, so pressing CC or starting the next episode does not
        // quietly go back to the first one
        audioIndex?.let { putExtra("audioIndex", it) }
        // and the picture size and megabits chosen in the player, so the next episode
        // and every restart keep them rather than quietly going back to the original
        putExtra("height", height)
        putExtra("mbit", mbit)
        // carried so the next episode does not go back to Dolby on a device that has
        // already been shown not to manage it
        putExtra("plainSound", ears)
        // whether that was a decision or a guess: a guess is made again next time,
        // a decision is kept
        putExtra("soundChosen", plainSound != null)
        // and which subtitle was chosen. The player reads this on every restart -
        // a change of soundtrack, of quality, of coding - and it was never written,
        // so each of those quietly turned the subtitles off.
        subIndex?.let { putExtra("subIndex", it) }
        if (drawable && picture != null) {
            // which track the device is to draw, and where it sits among the text
            // tracks of the file - the player counts them in the order the container
            // holds them, which is the order the library lists them in
            putExtra("nativeSub", picture)
            putExtra("nativeSubAt",
                     m.subtitleStreams.filter { it.index >= 0 }
                         .indexOfFirst { it.index == picture }.coerceAtLeast(0))
        }
        // How far this file's sound is from the rest, for the player to make up on
        // the way out. Only where the film is played as it lies: where it is encoded
        // the encoder has already done it, and doing it twice is twice as loud.
        putExtra("gainDb", if (direct) m.gainDb else 0f)
        // and what it measured, for the line along the top. Sent whichever way it is
        // played: where the encoder evened it out, that is worth seeing too.
        m.lufs?.let { putExtra("lufs", it) }
        putExtra("gainUsed", m.gainDb)
        putExtra("durationMs", m.durationMs)
        putExtra("source", listOfNotNull(
            m.height?.let { h -> if (h >= 1700) "4K" else h.toString() + "p" },
            m.videoCodec?.uppercase(),
            m.audioLine().takeIf { it.isNotEmpty() },
        ).joinToString("  \u00b7  "))
        if (textSub != null) {
            // a direct-played file keeps the original timeline, an encode starts at zero
            putExtra("subsUrl", Api.subsUrl(m, textSub, if (direct) 0 else positionSec))
            // and its name, so the player can say which subtitle is on rather than
            // calling every one of them "und"
            putExtra("subsName",
                     m.textSubs().firstOrNull { it.index == textSub }?.label ?: "")
        }
        // what the next episode would be, if this is one
        if (m.type == "episode") {
            putExtra("showKey", m.grandparentKey ?: "")
            putExtra("season", m.parentIndex ?: 0)
            putExtra("number", m.index ?: 0)
        }
    }
}
