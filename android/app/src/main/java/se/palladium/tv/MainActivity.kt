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
import androidx.compose.foundation.background
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
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
    }

    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean =
        safeKey(event) { super.dispatchKeyEvent(event) }

    override fun onResume() {
        super.onResume()
        returned.value = returned.value + 1
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
        // home reaches the house rather than only the machine that keeps copies
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
                                                        background = Skin.Bg)) { App() }
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
                BackHandler { stack.removeAt(stack.lastIndex) }
                DetailScreen(stack.last(),
                             onBack = { stack.removeAt(stack.lastIndex) },
                             onOpen = { stack.add(it) })
            }
            else -> {
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
                HomeScreen(browse, onOpen = { stack.add(it) },
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
    // and whichever server is open is asked whether it keeps a copy somewhere: the
    // answer arrives while this screen is on, rather than only when the app started
    LaunchedEffect(Unit) {
        kotlinx.coroutines.withContext(Dispatchers.IO) { Api.learnStandby(ctx) }
        list = Servers.folded(Servers.all(ctx))
    }
    // every server in the list, knocked on now and then: the one on the shelf may be
    // off and the copy may be awake, and only asking says which
    LaunchedEffect(list.size) {
        while (true) {
            val said = HashMap<String, Boolean>()
            list.forEach { s ->
                said[s.base] = Api.answering(s.base, s.token) ||
                    (s.outside.isNotEmpty() && Api.answering(s.outside, s.token))
                // and whether it takes us for its owner from where we are standing
                if (said[s.base] == true) Servers.learnWhose(ctx, s)
            }
            list = Servers.all(ctx)
            alive = said
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
            val open = srv.base == Api.base
            Column(Modifier.fillMaxWidth().padding(bottom = 10.dp)
                       .background(Skin.Panel, RoundedCornerShape(10.dp))
                       .padding(horizontal = 14.dp, vertical = 12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            // answering, and how it is known: green for a machine
                            // that spoke a moment ago
                            val up = alive[srv.base]
                            Box(Modifier.padding(end = 8.dp).size(9.dp)
                                    .background(
                                        when (up) {
                                            true -> Color(0xFF42C96A)
                                            false -> Color(0xFF7D2E2E)
                                            else -> Color(0xFF3A424D)
                                        }, RoundedCornerShape(5.dp)))
                            Text(srv.name.ifEmpty { Servers.hostOf(srv.base) },
                                 color = if (open) Skin.Accent else Skin.Fg,
                                 fontSize = 17.sp, fontWeight = FontWeight.Medium)
                        }
                        // Both of its addresses, each said plainly. Which one a
                        // machine is filed under is the thing that goes wrong, and
                        // it cannot be checked if only one of them is on the screen.
                        if (srv.outside.isNotEmpty()) {
                            Text(Servers.whereKind(srv.outside) + "   " + srv.outside,
                                 color = Skin.Dim, fontSize = 11.sp,
                                 modifier = Modifier.padding(top = 2.dp))
                        }
                        Text(srv.base +
                             // which address this is. One machine answers to two -
                             // the network one and the one the router forwards - and
                             // a row that did not say which read as a second machine
                             "  ·  " + Servers.whereKind(srv.base) +
                             "  ·  in use" +
                             // a cache is a server like any other in this list;
                             // the tag says why its shelf is the shorter one
                             (if (srv.copyOf.isNotEmpty()) "  ·  cache backup"
                              else if (srv.mine) "  ·  yours"
                              else "  ·  guest") +
                             (if (srv.group.isNotEmpty()) "  ·  " + srv.group
                              else ""),
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
                Text("The machine that keeps copies", color = Skin.Fg, fontSize = 15.sp)
                Text(copyAt + "  ·  it answers when this server is off",
                     color = Skin.Dim, fontSize = 12.sp)
                Row(Modifier.padding(top = 10.dp)) {
                    Pill("Add it back") {
                        // the other address of it, if this is the one it was not
                        // filed under, so it comes back as one machine
                        val other = listOf(Api.standby, Api.standbyOut)
                            .firstOrNull { it.isNotEmpty() && it != copyAt } ?: ""
                        Servers.add(ctx, Server(Servers.hostOf(copyAt) + " copy",
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
                        val addr = invite?.first ?: Servers.asAddress(text)
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
            changes.forEachIndexed { n, release ->
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

        SettingsHeading("SERVERS")
        SettingsRow(Servers.current(ctx)?.name ?: "None yet",
                    Servers.all(ctx).size.toString() + " known  \u00b7  " +
                    (Servers.current(ctx)?.base ?: "")) {
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
        // server at a time, or a shelf per group. The house's two machines are one
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
            SettingsRow("Theme",
                        (looks.firstOrNull { it.first == wearing }?.second ?: "House") +
                            (if (putOn.isNotEmpty())
                                 "  ·  wearing " +
                                 (looks.firstOrNull { it.first == putOn }?.second
                                  ?: putOn)
                             else ""),
                        extra = if (putOn.isEmpty()) null else ({
                            Text("This machine put that on itself - " + putOnWhy +
                                 ". What is chosen here comes back when that passes.",
                                 color = Skin.Dim, fontSize = 12.sp)
                        })) {
                looks.forEach { (id, label, _) ->
                    Pill(label, active = id == wearing) {
                        wearing = id
                        ctx.lifecycleScope.launch { Api.wearSkin(id) }
                    }
                }
            }
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
            SettingsRow("The server",
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
                                serverSaid = why ?: ("installing " + build.latest +
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
        val copyAt = listOf(Api.standby, Api.standbyOut).firstOrNull { it.isNotEmpty() }
            ?: ""
        if (copyAt.isNotEmpty()) {
            LaunchedEffect(copyAt) {
                if (copyBuild == null) copyBuild = Api.serverBuildAt(copyAt, Api.token)
            }
            SettingsRow("The other server",
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
                                val why = Api.updateServerAt(copyAt, Api.token)
                                copySaid = why ?: ("installing " + b.latest +
                                                   " - it will come back on its own")
                                if (why != null) copyBusy = false
                                else {
                                    kotlinx.coroutines.delay(20_000)
                                    copyBuild = Api.serverBuildAt(copyAt, Api.token)
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
                        control: @Composable RowScope.() -> Unit) {
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
    val list = remember(Api.base) {
        Servers.folded(Servers.all(ctx))
            .sortedBy { it.name.ifBlank { Servers.hostOf(it.base) }.lowercase() }
    }
    var alive by remember { mutableStateOf<Map<String, Boolean>>(emptyMap()) }
    LaunchedEffect(Unit) {
        alive = list.associate { it.base to Api.answering(it.base, it.token) }
    }
    Column(Modifier.padding(top = 6.dp, bottom = 2.dp)) {
        list.forEach { srv ->
            val on = srv.base.trimEnd('/') == Api.base
            val up = alive[srv.base]
            var lit by remember { mutableStateOf(false) }
            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .onFocusChanged { lit = it.isFocused }
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
                    .background(if (lit) Skin.Panel else Color.Transparent,
                                RoundedCornerShape(8.dp))
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
                // Standby is what a machine is doing, not what it is: the one
                // being watched is not standing by for anything.
                if (srv.copyOf.isNotEmpty() && !on) {
                    Text("standby", color = Skin.Dim, fontSize = 10.sp,
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
                   /** what a press of down from the tabs should reach, if anything */
                   belowTabs: FocusRequester? = null,
                   picks: (@Composable RowScope.() -> Unit)? = null,
                   search: (@Composable RowScope.() -> Unit)? = null,
                   filters: (@Composable RowScope.() -> Unit)? = null) {
    // Everything stays where it is on every tab. Settings used to be dropped on
    // Films and TV to make room for the sort control - so a button moved and then
    // vanished depending on which shelf was open, which is worse than a crowded row:
    // the row scrolls, and a button that is off to the right is still where it was
    // last time. Nothing is hidden to make room for anything.
    val housekeeping = true
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
            // that leaves.
            Column {
                // Which machine this shelf came off, beside the version. With two
                // servers in the list and a film that can be on either, the question
                // "where am I" was answered only by going back to the server list.
                val ctx = LocalContext.current
                // by address, not by what was chosen: after a film moves house the
                // app is on the copy without anybody having picked it
                val here = remember(Api.base) {
                    Servers.all(ctx).firstOrNull { it.base.trimEnd('/') == Api.base }
                        ?: Servers.current(ctx)
                }
                // and a press away from every other one: moving used to mean going
                // back to the server list and finding it there
                var picking by remember { mutableStateOf(false) }
                var lit by remember { mutableStateOf(false) }
                Row(verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier
                        .onFocusChanged { lit = it.isFocused }
                        .clickable { picking = !picking }
                        .background(if (lit) Skin.Panel else Color.Transparent,
                                    RoundedCornerShape(999.dp))
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
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("P", color = Skin.Accent, fontSize = 22.sp,
                         fontFamily = FontFamily.Serif, fontWeight = FontWeight.SemiBold)
                    Text("PALLADIUM", color = Skin.Fg, fontSize = 13.sp,
                         letterSpacing = 2.5.sp,
                         modifier = Modifier.padding(start = 8.dp))
                }
                if (picking) WhichServer { picking = false }
            }
            // Beside the name rather than at the end of the tabs: the row below is
            // full on a phone, and searching is the one thing done from any of the
            // three places a library is looked at.
            search?.let { Spacer(Modifier.width(14.dp)); it() }
            Spacer(Modifier.weight(1f))
            CastButton()
        }
        run {
            val along = rememberScrollState()
            Box(Modifier.padding(top = 8.dp).fillMaxWidth()) {
                Row(Modifier.fillMaxWidth().horizontalScroll(along),
                    verticalAlignment = Alignment.CenterVertically) {
                    Tabs(tab, onTab, tabFocus, picks, belowTabs)
                    filters?.let { Spacer(Modifier.width(8.dp)); it() }
                    if (housekeeping) {
                        Spacer(Modifier.width(10.dp))
                        onSetup?.let { Pill("Settings") { it() } }
                    }
                    // the room, from the shelves: what is said over a film needs no
                    // press, and answering it does
                    onChat?.let {
                        Spacer(Modifier.width(8.dp)); Pill("Watch party") { it() }
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
    }
}

@Composable
private fun RowScope.Tabs(tab: String, onTab: (String) -> Unit,
                          focus: Map<String, FocusRequester> = emptyMap(),
                          picks: (@Composable RowScope.() -> Unit)? = null,
                          below: FocusRequester? = null) {
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
        Pill(label, tab == id, narrow = tight, small = squeeze,
             modifier = (if (f == null) Modifier else Modifier.focusRequester(f))
                 .then(if (below == null) Modifier
                       else Modifier.focusProperties { down = below })) {
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
    tabPill("casual", "Casual")
    // Not while looking through the library: Films and TV carry a sort, a genre and a
    // search box on the same line, and nobody goes looking for the noticeboard
    // halfway down a list of films. It is on Home, where it belongs.
    if (tab != "films" && tab != "tv") tabPill("reports", "Reports")
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
private class Browse {
    var tab by mutableStateOf("home")
    var rows by mutableStateOf<List<Pair<String, List<Media>>>>(emptyList())
    var grid by mutableStateOf<List<Media>>(emptyList())
    var more by mutableStateOf(false)          // another page is waiting
    var loading by mutableStateOf(true)
    var failed by mutableStateOf<String?>(null)
    // opens on what came out last: "what is new in the world" is a better first
    // question than "what did this server notice"
    var sortKey by mutableStateOf("originallyAvailableAt")
    var sortAsc by mutableStateOf(false)
    var genre by mutableStateOf("")            // "" is everything
    var genres by mutableStateOf<List<Pair<String, Int>>>(emptyList())
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
    var casual by mutableStateOf<Set<String>>(emptySet())   // in the shuffle
    /** the collection being looked into, or null for the shelf of shelves */
    var collectionOn by mutableStateOf<Media?>(null)
    // a collection reads in release order, oldest first - a series is watched from
    // its beginning - and keeps that apart from the order the library is browsed in
    var collSortKey by mutableStateOf("originallyAvailableAt")
    var collSortAsc by mutableStateOf(true)
    var watchTab by mutableStateOf("list")     // which of the two shelves is shown
    var askReset by mutableStateOf(false)      // Start over, waiting to be confirmed
    var casualPlayed by mutableStateOf(0)      // how far this round has got
    var casualPool by mutableStateOf(0)        // and how much there is
    val gridState = androidx.compose.foundation.lazy.grid.LazyGridState()
    val rowsState = androidx.compose.foundation.lazy.LazyListState()
}

// FocusRequester.Cancel - refusing a direction - is still marked experimental
/** What to call a build on offer: its version, and its number when that is all that
 *  separates it from the one installed. */
private fun named(u: Updates.Available): String =
    u.versionName + (if (u.versionName == BuildConfig.VERSION_NAME)
                         " (" + u.versionCode + ")" else "")

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
            browse.serverNow = said.version
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
                browse.notice = word.text
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
    val want = browse.tab + "|" + browse.watchTab + "|" + order + "|" +
        browse.genre + "|" +
               browse.query.trim() + "|" +
        (browse.collectionOn?.ratingKey ?: "") +
        // the two shelves are what a mark changes, so they alone are refetched when
        // one is changed; a grid of films does not move because a series was marked
        (if (browse.tab == "watchlist" || browse.tab == "casual")
             "|" + MainActivity.marksTouched.value else "")

    // which genres exist, for the tab in hand: asked once per tab
    LaunchedEffect(browse.tab) {
        browse.genres = when (browse.tab) {
            "films" -> Api.genres("movie")
            // the tab is called "tv"; asking for "shows" here matched nothing, so the
            // genre control on TV has been empty since the day it was added
            "tv" -> Api.genres("show")
            else -> emptyList()
        }
        // A genre chosen on one tab is not a genre on the other: films have Western
        // and television has Talk Show, and carrying one across showed an empty
        // library with no hint as to why. It is kept when the new tab has it too.
        if (browse.genre.isNotEmpty() &&
            browse.genres.none { it.first.equals(browse.genre, ignoreCase = true) }) {
            browse.genre = ""
        }
    }

    // coming back from a film: the count on the casual shelf is a round out of date
    // until something else asks for it
    val cameBack = MainActivity.returned.value
    LaunchedEffect(cameBack, browse.tab) {
        if (browse.tab == "casual") {
            val where = runCatching { Api.casualProgress() }.getOrDefault(Pair(0, 0))
            browse.casualPlayed = where.first
            browse.casualPool = where.second
        }
    }

    LaunchedEffect(want) {
        if (browse.loaded == want) return@LaunchedEffect
        browse.loading = true; browse.failed = null
        val tab = browse.tab
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
                tab == "home" -> browse.rows = listOf(
                    "Continue watching" to runCatching { Api.onDeck(ctx) }.getOrDefault(emptyList()),
                    "Recently added films" to runCatching { Api.recentFilms(ctx) }.getOrDefault(emptyList()),
                    "Recently released films" to runCatching { Api.releasedFilms(ctx) }.getOrDefault(emptyList()),
                    "Recently added TV" to runCatching { Api.recentEpisodes(ctx) }.getOrDefault(emptyList()),
                    "Recently released series" to runCatching { Api.releasedShows(ctx) }.getOrDefault(emptyList()),
                ).filter { it.second.isNotEmpty() }
                tab == "watchlist" || tab == "casual" -> {
                    browse.casual = runCatching { Api.casualMarks() }
                        .getOrDefault(emptySet())
                    // which way it plays is the server's, so the phone and the
                    // television agree about it
                    val where = runCatching { Api.casualProgress() }
                        .getOrDefault(Pair(0, 0))
                    browse.casualPlayed = where.first
                    browse.casualPool = where.second
                    // marked for later, newest mark first: the order it was thought of
                    // in, which is not an order worth re-sorting
                    // its own shelf, not a corner of the watchlist
                    browse.grid = if (tab == "casual") Api.casualShelf(ctx)
                                  else Api.watchlist(ctx)
                    browse.more = false
                }
                tab == "collections" -> {
                    // the shelves, or the one that has been opened
                    val open = browse.collectionOn
                    browse.grid = if (open == null) Api.collections(ctx)
                                  else Api.collectionItems(open)
                    browse.more = false
                }
                tab == "films" -> {
                    browse.grid = Api.movies(ctx, order, genre = browse.genre)
                    browse.more = browse.grid.size >= PAGE
                }
                tab == "tv" -> {
                    browse.grid = Api.shows(ctx, order, genre = browse.genre)
                    browse.more = browse.grid.size >= PAGE
                }
            }
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
    // the resume button on the casual shelf, so the tab above it can point down at it
    val casualPlay = remember { FocusRequester() }
    val tabFocus = remember {
        listOf("home", "films", "tv", "watchlist", "collections", "casual")
            .associateWith { FocusRequester() }
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
        browse.gridState.firstVisibleItemIndex > 0 ||
            browse.gridState.firstVisibleItemScrollOffset > 0
    BackHandler(enabled = scrolled || inContent) {
        // The place in the list is kept: coming back down should land where you were,
        // not at the beginning of the library. Only the focus moves - to the tab you
        // are on, Films from Films and TV from TV.
        runCatching { tabFocus[browse.tab]?.requestFocus() }
    }

    Column(Modifier.fillMaxSize()) {
        TopBar(browse.tab,
               { chosen ->
                   // Reports is a place, not a shelf: the tab leads to it and the
                   // library stays on whichever tab it was
                   if (chosen == "reports") onReports() else {
                       browse.tab = chosen; browse.query = ""
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
               // on the casual tab, down goes to the button that starts it
               belowTabs = if (browse.tab == "casual") casualPlay else null,
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
                   GenreControl(browse.genre, browse.genres,
                                onGenre = { browse.genre = it })
               }) else null,
               search = if (browse.tab in setOf("home", "films", "tv")) ({
                   FilterControls(browse.query, { browse.query = it })
               }) else null,
               filters = if (browse.tab == "home") null
                         // Only the two shelves up here. Everything else about casual watching
               // - how it plays, and the button that plays it - goes in a row of its
               // own below, where a television has room for it: crammed into this row
               // they ran off the edge of the screen and the play button was the one
               // that went.
               else if (browse.tab == "watchlist" || browse.tab == "casual") null
                         else null)
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
                    runCatching { takeIt.requestFocus() }
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
        if (browse.askReset) {
            AlertDialog(
                onDismissRequest = { browse.askReset = false },
                containerColor = Skin.Panel,
                title = { Text("Start the shuffle over?", color = Skin.Fg) },
                text = {
                    Text(browse.casualPlayed.toString() + " played so far will be " +
                         "forgotten, along with where any half-watched episode was left.",
                         color = Skin.Dim, fontSize = 14.sp)
                },
                confirmButton = {
                    Pill("Start over", primary = true) {
                        browse.askReset = false
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            Api.resetCasual()
                            // say so at once rather than waiting for the next visit
                            val where = runCatching { Api.casualProgress() }
                                .getOrDefault(Pair(0, browse.casualPool))
                            browse.casualPlayed = where.first
                            browse.casualPool = where.second
                            browse.loaded = ""
                        }
                    }
                },
                dismissButton = { Pill("Keep going") { browse.askReset = false } },
            )
        }
        // Inside a collection: its name over the grid, and back leads out of it
        // rather than out of the app. No button for it - the one on the remote is
        // the way out of everything else here too.
        browse.collectionOn?.let { open ->
            if (browse.tab == "collections") {
                BackHandler { browse.collectionOn = null }
                Row(Modifier.fillMaxWidth()
                        .padding(start = 21.dp, end = 16.dp, top = 12.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    SectionTitle(open.title)
                    Spacer(Modifier.width(12.dp))
                    Text(open.subtitle, color = Skin.Dim, fontSize = 13.sp)
                    Spacer(Modifier.weight(1f))
                    // in the order shown, or one drawn out of the hat
                    val start: (Media) -> Unit = { pick ->
                        (ctx as AppCompatActivity).lifecycleScope.launch {
                            val full = runCatching { Api.metadata(pick) }
                                .getOrNull() ?: pick
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
                        Pill("▶ Play", primary = true) { start(order.first()) }
                        Spacer(Modifier.width(8.dp))
                        Pill("↻ Shuffle") { start(order.random()) }
                        Spacer(Modifier.width(8.dp))
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
                }
            }
        }
        if (browse.tab == "casual") {
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically) {
                // Down from the tabs lands here rather than in the shelf: on this tab
                // the button is what somebody came for, and hunting for it past a row
                // of posters is two presses nobody should have to make.
                Pill("↻ Casual play", primary = true,
                     // Up goes back to the tabs. The name of the server is a button
                     // now and it sits above them, so the nearest thing above this
                     // one stopped being the row of tabs and became that - which is
                     // a long way from where anybody pressing up is trying to go.
                     modifier = Modifier.focusRequester(casualPlay)
                         .focusProperties {
                             up = tabFocus[browse.tab] ?: FocusRequester.Default
                         }) {
                    (ctx as AppCompatActivity).lifecycleScope.launch {
                        // the button that starts casual watching picks up where
                        // the shelf was left
                        val pick = Api.casualDraw(resume = true)
                        if (pick == null) {
                            android.widget.Toast.makeText(
                                ctx, "Nothing is marked for casual watching",
                                android.widget.Toast.LENGTH_SHORT).show()
                        } else {
                            // carry on from where it was left, if it was left part-way,
                            // and with a subtitle chosen - the shelf's copy carries no
                            // streams, so the full metadata decides
                            val full = runCatching { Api.metadata(pick.media) }
                                .getOrNull() ?: pick.media
                            ctx.startActivity(
                                playIntent(ctx, full, pick.resumeAt,
                                           full.pickedSub ?: full.openWith(
                                               Api.myLanguage)
                                               ?.index)
                                    .putExtra("casual", true))
                        }
                    }
                }
                Spacer(Modifier.width(14.dp))
                Text("Played " + browse.casualPlayed + " of " + browse.casualPool,
                     color = Skin.Dim, fontSize = 14.sp)
                Spacer(Modifier.width(10.dp))
                Pill("↺ Start over") { browse.askReset = true }
            }
        }
        when {
            browse.loading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                CircularProgressIndicator(color = Skin.Accent, strokeWidth = 3.dp)
            }
            browse.failed != null -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                Text(browse.failed!!, color = Skin.Dim, fontSize = 15.sp)
            }
            browse.tab == "home" && browse.query.trim().length < 2 -> LazyColumn(
                state = browse.rowsState,
                modifier = Modifier.onFocusChanged { inContent = it.hasFocus },
                contentPadding = PaddingValues(bottom = 28.dp)) {
                items(browse.rows) { (title, list) ->
                    SectionTitle(title, Modifier.padding(start = 21.dp, top = 16.dp, bottom = 2.dp))
                    val w = if (LocalConfiguration.current.screenWidthDp < 600) 108 else 140
                    LazyRow(contentPadding = PaddingValues(horizontal = 16.dp)) {
                        items(list) { m -> Poster(m, width = w) { onOpen(m) } }
                    }
                }
            }
            browse.grid.isEmpty() -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                Text(if (browse.query.isBlank()) "Nothing here" else "Nothing matches that",
                     color = Skin.Dim, fontSize = 15.sp)
            }
            else -> {
                // the same state object every time, which is what keeps the scroll
                val gridState = browse.gridState
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
                                               browse.genre)
                                else Api.shows(ctx, order, browse.grid.size, PAGE,
                                               browse.genre)
                            }.getOrDefault(emptyList())
                            browse.grid = browse.grid + next
                            browse.more = next.size >= PAGE
                            paging = false
                        }
                    }
                }
                // three across on a phone, six or so on a television
                val cell = if (LocalConfiguration.current.screenWidthDp < 600) 108.dp
                           else 140.dp
                LazyVerticalGrid(
                    state = gridState,
                    columns = GridCells.Adaptive(cell),
                    contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
                    modifier = Modifier.fillMaxSize()
                        .onFocusChanged { inContent = it.hasFocus }
                        .focusGroup()
                        .focusProperties {
                            // Deep in the list the rows above have been recycled, so a
                            // focus search upwards finds nothing and leaps to the menu.
                            // Refuse the exit while scrolled: up then moves by rows.
                            up = if (gridState.firstVisibleItemIndex > 0)
                                FocusRequester.Cancel else FocusRequester.Default
                            // The row below has not been composed yet either, and the
                            // search then leaves the grid upwards - onto the Casual
                            // play button. Refuse it while there are rows left.
                            down = if (gridState.layoutInfo.visibleItemsInfo.isNotEmpty() &&
                                       gridState.layoutInfo.visibleItemsInfo.last().index <
                                           gridState.layoutInfo.totalItemsCount - 1)
                                FocusRequester.Cancel else FocusRequester.Default
                        },
                ) {
                    val shown = if (browse.collectionOn != null)
                        collectionOrder(browse.grid, browse.collSortKey,
                                        browse.collSortAsc)
                        else browse.grid
                    items(shown) { m ->
                        // Sorted by release, a series is placed by its newest
                        // episode, so that is what the card says. The year a
                        // programme began, above one that began later, reads as a
                        // mistake until it says which date put it there.
                        val instead =
                            if (browse.sortKey == "originallyAvailableAt" &&
                                m.isFolder && m.released.isNotEmpty())
                                "last aired " + m.released
                            else null
                        Poster(m, fill = true, instead = instead) {
                            // a shelf is not a title: it opens into what it holds
                            if (m.type == "collection") browse.collectionOn = m
                            else onOpen(m)
                        }
                    }
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
private fun collectionOrder(list: List<Media>, key: String, asc: Boolean): List<Media> {
    val by = when (key) {
        "addedAt" -> compareBy<Media> { it.addedAt }
        "titleSort" -> compareBy { it.title.lowercase() }
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
    Box {
        // the order and its direction on one control: the arrows belong to the thing
        // they describe rather than sitting beside it
        Pill(chosen + " " + (if (sortAsc) "⇅" else "⇵"), narrow = !onTv(),
             small = !onTv() && LocalConfiguration.current.screenWidthDp < 400) {
            open = true
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false },
                     modifier = Modifier.background(Skin.Panel)) {
            orders.forEach { (key, label) ->
                DropdownMenuItem(
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
    onGenre: (String) -> Unit,
) {
    if (genres.isEmpty()) return
    var genreOpen by remember { mutableStateOf(false) }
    Box {
        Pill(if (genre.isEmpty()) "Genre" else genre, narrow = !onTv(),
             small = !onTv() && LocalConfiguration.current.screenWidthDp < 400) {
            genreOpen = true
        }
        DropdownMenu(
            expanded = genreOpen, onDismissRequest = { genreOpen = false },
            modifier = Modifier.background(Skin.Panel)
                .width(260.dp).heightIn(max = 360.dp)) {
            DropdownMenuItem(
                contentPadding = MENU_PAD,
                text = { Text("All", fontSize = 14.sp,
                              color = if (genre.isEmpty()) Skin.Accent else Skin.Fg) },
                onClick = { onGenre(""); genreOpen = false })
            genres.forEach { (name, count) ->
                DropdownMenuItem(
                    contentPadding = MENU_PAD,
                    text = { Text(name + "  (" + count + ")", fontSize = 14.sp,
                                  maxLines = 1, overflow = TextOverflow.Ellipsis,
                                  color = if (name == genre) Skin.Accent
                                          else Skin.Fg) },
                    onClick = { onGenre(name); genreOpen = false })
            }
        }
    }
}

/** What is left of the row once the sort and the genre have gone up among the tabs. */
@Composable
private fun RowScope.FilterControls(
    query: String,
    onQuery: (String) -> Unit,
) {
    // a fixed width: the narrow bar scrolls, where "what is left" means nothing
    TextBox(query, "Search", Modifier.width(190.dp), onValue = onQuery)
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
private fun DetailScreen(m: Media, onBack: () -> Unit, onOpen: (Media) -> Unit) {
    val ctx = LocalContext.current
    val portrait = LocalConfiguration.current.orientation == Configuration.ORIENTATION_PORTRAIT
    // A phone on its side has about three hundred and sixty points of height, and the
    // page was laid out for a television: a poster two hundred and seventy tall, a
    // title at thirty, and the facts and the subtitle line below all of it, off the
    // bottom of the screen. Everything comes up and in.
    val cramped = !portrait && LocalConfiguration.current.screenHeightDp < 500
    var full by remember(m.ratingKey) { mutableStateOf(m) }
    var children by remember(m.ratingKey) { mutableStateOf<List<Media>>(emptyList()) }
    //: which mark is waiting to be confirmed: true for watched, false for unwatched
    var marking by remember(m.ratingKey) { mutableStateOf<Boolean?>(null) }
    // index of the chosen subtitle stream, or null for none
    var sub by remember(m.ratingKey) { mutableStateOf<Int?>(null) }
    var styling by remember(m.ratingKey) { mutableStateOf(false) }
    // the copy chooser, for a title the library holds more than once
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
    // whether it is in the casual shuffle, which is a corner of the watchlist
    var casual by remember(m.ratingKey) { mutableStateOf(false) }

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
        seen = full.watched
        // a mark may have been changed on another screen, or on the television, since
        // this page was last looked at
        listed = runCatching { Api.marked() }.getOrDefault(emptySet())
            .contains(m.ratingKey)
        casual = runCatching { Api.casualMarks() }.getOrDefault(emptySet())
            .contains(m.ratingKey)
    }

    val facts: @Composable () -> Unit = {
        // Six chips - year, size, codecs, rate, how it will play - are wider than a
        // phone held upright. A Row does not wrap, so the last one was squeezed to a
        // single column of letters running down the edge of the screen.
        FlowRow(Modifier.padding(top = if (cramped) 4.dp else 10.dp),
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
                Chip(if (full.canDirectPlay()) "Direct play" else "Transcoded",
                     accent = !full.canDirectPlay())
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
            Text(line, color = Skin.Dim, fontSize = 15.sp,
                 modifier = Modifier.padding(top = 4.dp))
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
        Row(Modifier.padding(top = if (cramped) 6.dp else 12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            MarkControl(
                listed = listed, casual = casual,
                onList = {
                    val on = !listed
                    listed = on
                    MainActivity.marksTouched.value++
                    (ctx as AppCompatActivity).lifecycleScope.launch { Api.mark(full, on) }
                },
                onCasual = {
                    val on = !casual
                    casual = on
                    MainActivity.marksTouched.value++
                    (ctx as AppCompatActivity).lifecycleScope.launch {
                        Api.markCasual(full, on)
                    }
                })
            Text(when {
                     listed && casual -> "On your watchlist, and in the shuffle"
                     listed -> "On your watchlist"
                     casual -> "In the casual shuffle"
                     // unmarked: name the two halves rather than say "mark this",
                     // which does not say what either of them would do
                     else -> "Watchlist / Casual"
                 }, color = Skin.Dim, fontSize = 14.sp)
        }
    }

    val buttons: @Composable () -> Unit = {
        if (!full.isFolder) {
            val resume = full.viewOffsetMs / 1000
            // On a television nothing has focus until something asks for it, and the
            // first press of the remote should start the film rather than hunt for it.
            val playFocus = remember { FocusRequester() }
            LaunchedEffect(full.ratingKey) {
                runCatching { playFocus.requestFocus() }
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
                                .putExtra("wantMade", true))
                    } else {
                        play(ctx, full, at, sub.takeIf { it != PENDING_SUB })
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
                Pill(if (resume > 5) "Resume " + fmt(resume) else "Play", primary = true,
                     narrow = narrowRow, small = smallRow,
                     modifier = Modifier.focusRequester(playFocus)) {
                    startIt(resume)
                }
                if (resume > 5) Pill("From start", narrow = narrowRow,
                                     small = smallRow) {
                    startIt(0)
                }
                // filled in once the film has been watched; otherwise plain, with
                // the white ring under the remote like everything else
                Pill(if (seen) "\u2713 Watched" else "Mark watched",
                     active = seen, narrow = narrowRow, small = smallRow) {
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
                    Pill("Go to show", narrow = narrowRow, small = smallRow) {
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
    val blurb: @Composable () -> Unit = {
        if (full.summary.isNotEmpty()) {
            Text(full.summary, color = Color(0xFFBFC9D4),
                 fontSize = if (cramped) 12.5.sp else 14.sp,
                 lineHeight = if (cramped) 18.sp else 21.sp,
                 maxLines = if (portrait) 8 else if (cramped) 3 else 6,
                 overflow = TextOverflow.Ellipsis,
                 // close under the facts it belongs to, rather than adrift below them
                 modifier = Modifier.padding(top = if (cramped) 6.dp else 8.dp))
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

        // One column, sized to fit: with nothing to scroll, moving focus cannot drag
        // the page about. The scroller stays for a series, whose episodes go past the
        // bottom, and for a phone held upright.
        val scroll = rememberScrollState()
        Column(
            Modifier.fillMaxSize()
                // and it scrolls on a short screen too, whatever is on it
                .then(if (portrait || cramped || children.isNotEmpty())
                          Modifier.verticalScroll(scroll) else Modifier)
                .padding(horizontal = if (portrait) 20.dp else if (cramped) 18.dp else 28.dp,
                         vertical = if (portrait) 18.dp else if (cramped) 8.dp else 22.dp)
        ) {
            Row { Pill("Back") { onBack() } }
            if (portrait) {
                Box(Modifier.padding(top = 14.dp).width(120.dp).height(180.dp)
                        .clip(RoundedCornerShape(10.dp))) {
                    Art(Api.artUrl(full), full.title, Modifier.fillMaxSize(), mark = 44)
                }
                Text(full.title, color = Skin.Fg, fontSize = 24.sp,
                     fontWeight = FontWeight.SemiBold,
                     modifier = Modifier.padding(top = 12.dp))
                // the year belongs to the title, so it goes with it; then the button
                when_(); buttons(); marks(); subtitles(); facts(); blurb()
            } else {
                Row(Modifier.padding(top = if (cramped) 4.dp else 12.dp)) {
                    Box(Modifier
                            .width(if (cramped) 110.dp else 180.dp)
                            .height(if (cramped) 165.dp else 270.dp)
                            .clip(RoundedCornerShape(10.dp))) {
                        Art(Api.artUrl(full), full.title, Modifier.fillMaxSize(),
                            mark = if (cramped) 40 else 60)
                    }
                    Column(Modifier.padding(start = if (cramped) 16.dp else 24.dp)) {
                        Text(full.title, color = Skin.Fg,
                             fontSize = if (cramped) 21.sp else 30.sp,
                             fontWeight = FontWeight.SemiBold)
                        when_(); buttons(); marks(); subtitles(); facts(); blurb()
                    }
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
                LaunchedEffect(children, MainActivity.reveal.value) {
                    val want = MainActivity.reveal.value
                    if (!want.isNullOrEmpty()) {
                        val at = children.indexOfFirst { it.ratingKey == want }
                        if (at >= 0) {
                            episodes.scrollToItem(at)
                            MainActivity.reveal.value = null
                        }
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
                        Poster(c, width = if (portrait) 120 else 140) { onOpen(c) }
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }
}

/** How long a film or an episode runs: "1 h 42 min", or "23 min" for a short one. */
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
    ctx.startActivity(playIntent(ctx, m, positionSec, subIndex))
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
    return runCatching {
        am.getDevices(android.media.AudioManager.GET_DEVICES_OUTPUTS)
            .any { it.type in ears }
    }.getOrDefault(false)
}

fun playIntent(ctx: Context, m: Media, positionSec: Long, subIndex: Int? = null,
               audioIndex: Int? = null, height: Int = 0, mbit: Int = 0,
               plainSound: Boolean? = null): Intent {
    // A picture track - PGS off a disc, VobSub off a DVD - is a set of images, and
    // only something that can decode them can show it. Media3 can, so a film played
    // straight from disk has its subtitle drawn by the device and never touches the
    // encoder. Burning is what is left for the cases that cannot: a browser, a cast
    // receiver, or a film being encoded anyway for its sound or its size.
    val picture = subIndex?.takeIf { idx -> m.bitmapSubs().any { it.index == idx } }
    val ears0 = plainSound ?: throughHeadphones(ctx)
    val drawable = picture != null && !ears0 && audioIndex == null &&
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
