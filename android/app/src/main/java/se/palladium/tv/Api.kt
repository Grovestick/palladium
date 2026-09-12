package se.palladium.tv

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Talks to a Palladium server - ours, or a friend's.
 *
 * The server answers one nested JSON shape for its library - a container, its items,
 * and the media inside each - so one parser serves every server. Two things differ
 * and are threaded through everything here:
 *
 *   the address, because a friend's is somewhere else entirely;
 *   the token, because a friend's server has no other reason to trust us.
 *
 * Anything that belongs to a particular title takes the title, not a bare path, so the
 * URL is built against the server that title came from. Playback URLs are where the
 * real decisions live - see [playbackUrl] and [castUrl].
 */
object Api {
    @Volatile var base: String = ""          // e.g. http://192.168.1.20:8765
    @Volatile var token: String = ""         // empty on our own network

    /**
     * Which language this viewer reads subtitles in, as the server has it.
     *
     * The device's own locale is a guess about the person holding it: a tablet
     * bought in one country and a viewer who reads another language disagree, and
     * the setting is the one that was actually chosen. Read once at startup and
     * kept, because it decides which track opens before the first frame.
     */
    @Volatile var myLanguage: String = java.util.Locale.getDefault().language

    suspend fun learnLanguage() {
        val said = runCatching { subtitleLanguage() }.getOrNull()
        if (!said.isNullOrEmpty()) myLanguage = said
    }

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)

    /**
     * The other machine that holds copies of what this server has, if there is one.
     *
     * A server that follows this one keeps what the house is watching. When the
     * server itself does not answer - it is off for the night, or being replaced -
     * asking the cache is the difference between the evening carrying on and a
     * screen that says nothing is there.
     */
    @Volatile var standby: String = ""

    /**
     * The same machine from outside the main server.
     *
     * One router, two forwarded ports: the library on 8765 and the machine keeping
     * copies on 8764. A viewer at home reaches the first address, a viewer somewhere
     * else the second, and neither knows which they are.
     */
    @Volatile var standbyOut: String = ""

    /** What the machine keeping copies calls itself. Its own name, not this one's:
     *  a row reading "the cache of the main server" tells somebody nothing about which
     *  computer they are about to watch from. */
    @Volatile var standbyName: String = ""

    //: the machine this server copies from, if it copies from one. The other half of
    //: "which machine is the other machine": a copy has no copy of its own.
    @Volatile var houseWhere: String = ""
    @Volatile var houseName: String = ""
    @Volatile private var onStandby: Boolean = false

    /**
     * The server we left when it stopped answering, and when it was last tried.
     *
     * Going to the machine that keeps copies is a stop-gap: it holds what the main server
     * has been watching and nothing else. So the one with the library on it is tried
     * again about once a minute - by making the request that was going to be made
     * anyway, which is both the question and the answer.
     */
    @Volatile private var homeBase: String = ""
    @Volatile private var triedHome: Long = 0L

    fun use(s: Server) {
        base = s.base.trimEnd('/')
        token = s.token
        standby = ""
        standbyOut = ""
        standbyName = ""
        onStandby = false
        homeBase = ""
        // The machine's other address, taken from the row rather than asked for.
        // Asking means reaching the server, and the case this exists for is the one
        // where the server cannot be reached: a phone on mobile data opening a row
        // filed under a house address had nothing to fall back to but the cache.
        otherWay = s.outside.trimEnd('/')
    }

    /**
     * How the server being watched is dressed, and whether it would rather not be.
     *
     * Both are the machine's own state rather than anybody's choice: a copy after
     * dark, or a card handed to a game. Asked when a screen opens and every ten
     * minutes, because the hour turns.
     */
    @Volatile var prefersTheCopy: Boolean = false

    suspend fun learnMood() = withContext(Dispatchers.IO) {
        runCatching { Skin.wear(JSONObject(get("/mood"))) }
        Unit
    }

    /**
     * What each machine encodes with, by address. Empty means it cannot encode at
     * all - a spare box with no card and no ffmpeg - and a player reading from one
     * of those should not print somebody else's graphics card on the screen.
     */
    private val engines = java.util.concurrent.ConcurrentHashMap<String, String>()

    /**
     * The other address this same machine answers to, as it names it.
     *
     * A server learned at home is filed under its address on that network, and from
     * a train that address answers nothing at all. Every screen knew to fall back to
     * the machine that keeps copies and none of them knew to try the main server's own
     * front door - so away from home the shelves were empty while both servers
     * showed a light, because a light is a knock on whichever address answers and
     * the library was still being asked of the one that does not.
     */
    @Volatile var otherWay: String = ""

    /** Whether an address is this library's own machine that keeps copies. */
    private fun theCopy(where: String): Boolean {
        val it = Servers.hostOf(where)
        return it.isNotEmpty() &&
            (it == Servers.hostOf(standby) || it == Servers.hostOf(standbyOut))
    }

    fun engineAt(where: String): String? = engines[where.trimEnd('/')]

    suspend fun learnEngine(where: String, tok: String = "") =
        withContext(Dispatchers.IO) {
            val at = where.trimEnd('/')
            if (at.isEmpty() || engines.containsKey(at)) return@withContext
            runCatching {
                val said = JSONObject(fetch(at, "/where", tok, 6000))
                engines[at] = said.optString("engine")
                if (at == base) {
                    // whichever of its two addresses is not the one in hand
                    otherWay = listOf(said.optString("lan"), said.optString("outside"))
                        .map { it.trimEnd('/') }
                        .firstOrNull { it.isNotEmpty() && it != at } ?: otherWay
                }
            }
            Unit
        }

    /**
     * Open the server by whichever of its addresses answers from here.
     *
     * The address a machine is filed under is the one it was learned by, and that
     * says nothing about where this screen is standing now. A phone that learned the
     * copy from a train had it filed under the way in from outside, and on the main server
     * network that address goes out to the router and back - when it works at all.
     */
    /**
     * Every machine in the list, filed under whichever of its addresses answers.
     *
     * Not only the one that is open. A phone that could not reach the cache sat in
     * front of a list saying it was on this network while nothing from it would
     * load, because only the server being watched was ever checked.
     */
    suspend fun fileThemWhereTheyAnswer(ctx: Context) = withContext(Dispatchers.IO) {
        Servers.all(ctx).forEach { srv ->
            val other = outsideFor(ctx, srv)
            if (other.isEmpty()) return@forEach
            val mine = srv.base.trimEnd('/')
            val home = Servers.athome(mine)
            // the short way when it works, and the other when the filed one does not
            val swap = if (!home && Servers.athome(other)) answering(other, srv.token)
                       else !answering(mine, srv.token) && answering(other, srv.token)
            if (swap) {
                Servers.add(ctx, srv.copy(base = other, outside = srv.base,
                                          name = if (srv.name ==
                                                     Servers.hostOf(srv.base))
                                                     Servers.hostOf(other)
                                                 else srv.name))
            }
        }
        Unit
    }

    /**
     * A row's other way in: the one it learned, or failing that the one it can work out.
     *
     * Two machines behind one router share the address the internet sees, each on its
     * own port - the server assumes the same when nobody says otherwise. So a house
     * filed under its network address, with no outside address of its own, is tried on
     * the cache's outside address (remembered from home) with the main server's own port. A
     * row that never learned it otherwise kept pointing at the house network from away,
     * and posters and lists built from the row never arrived.
     */
    private fun outsideFor(ctx: Context, srv: Server): String {
        val known = srv.outside.trimEnd('/')
        if (known.isNotEmpty()) return known
        val at = srv.base.trimEnd('/')
        if (!Servers.athome(at)) return ""
        val copyOut = (prefs(ctx).getString("standbyOut:" + at, "") ?: "").trimEnd('/')
        if (copyOut.isEmpty() || Servers.athome(copyOut)) return ""
        val port = Servers.hostOf(at).substringAfter(":", "")
        val host = Servers.hostOf(copyOut).substringBefore(":")
        return if (port.isEmpty() || host.isEmpty()) "" else "http://$host:$port"
    }

    suspend fun openTheDoorThatAnswers(ctx: Context) = withContext(Dispatchers.IO) {
        val here = Servers.current(ctx) ?: return@withContext
        val other = outsideFor(ctx, here)
        val mine = here.base.trimEnd('/')
        // The one on this network, when there is one and it answers. Going out to
        // the router and back in to reach a machine three metres away works - which
        // is the trouble: it answers, so nothing ever looked for the short way, and
        // a screen that once learned a machine from away kept using the long way
        // round for good.
        if (other.isNotEmpty() && !Servers.athome(mine) && Servers.athome(other) &&
            answering(other, here.token)) {
            Servers.use(ctx, swapped(here, other))
            return@withContext
        }
        if (answering(mine, here.token)) return@withContext
        if (other.isEmpty() || !answering(other, here.token)) return@withContext
        Servers.use(ctx, swapped(here, other))
        Unit
    }

    /**
     * The same row, opened by its other address.
     *
     * The name goes with it when the name was only ever the old address written out:
     * a row calling itself by one address while reaching the machine at another is
     * how an evening gets spent looking at the wrong thing.
     */
    private fun swapped(s: Server, to: String): Server =
        s.copy(base = to, outside = s.base,
               name = if (s.name == Servers.hostOf(s.base)) Servers.hostOf(to)
                      else s.name)

    /** What the machine that keeps copies is fetching this minute, by key. */
    suspend fun copyingNow(): String = withContext(Dispatchers.IO) {
        runCatching { JSONObject(get("/copying")).optString("key") }.getOrDefault("")
    }

    /** Ask this server where else it can be reached, and write it on its row. */
    suspend fun learnTheWayIn(ctx: Context) = withContext(Dispatchers.IO) {
        val here = Servers.current(ctx)
        if (otherWay.isEmpty() && here != null && here.outside.isNotEmpty() &&
            here.outside.trimEnd('/') != base) {
            otherWay = here.outside.trimEnd('/')
        }
        // asked of every machine in the list, not only the one that is open: the
        // list is what somebody reads when nothing is loading, and a row that shows
        // one address is a row that cannot say why
        Servers.all(ctx).forEach { srv ->
            runCatching {
                val said = JSONObject(fetch(srv.base.trimEnd('/'), "/where",
                                            srv.token, 6000))
                engines[srv.base.trimEnd('/')] = said.optString("engine")
                Servers.learnDoors(ctx, srv.base, said.optString("lan"),
                                   said.optString("outside"), said.optString("name"))
                // and the machine this one follows, if it named it: adding either of
                // the two gets both, and both of each one's addresses with them
                val follows = said.optJSONObject("follows")
                val theirs = follows?.optString("lan").orEmpty().trimEnd('/')
                val theirsOut = follows?.optString("outside").orEmpty().trimEnd('/')
                if (theirs.isNotEmpty() || theirsOut.isNotEmpty()) {
                    // A row of its own, or none. It used to count a machine as
                    // known when any row so much as mentioned its address - and a row
                    // that had swallowed the other one mentioned it while being the
                    // wrong machine, so the main server could never be learned again and
                    // there was no way back to it from the cache.
                    val known = Servers.all(ctx).any {
                        it.base.trimEnd('/') == theirs ||
                        it.base.trimEnd('/') == theirsOut
                    }
                    if (!known) {
                        val at = theirs.ifEmpty { theirsOut }
                        Servers.add(ctx, Server(
                            follows?.optString("name").orEmpty()
                                .ifEmpty { Servers.hostOf(at) },
                            at, srv.token, mine = srv.mine, on = false,
                            outside = if (at == theirs) theirsOut else theirs))
                    }
                }
                if (srv.base.trimEnd('/') == base) {
                    otherWay = listOf(said.optString("lan"), said.optString("outside"))
                        .map { it.trimEnd('/') }
                        .firstOrNull { it.isNotEmpty() && it != base } ?: otherWay
                }
            }
        }
        Unit
    }

    /** Every look this server offers, and the one this viewer picked. */
    suspend fun skins(): JSONObject = withContext(Dispatchers.IO) {
        JSONObject(get("/skins"))
    }

    /** Wear this one from now on, on every screen this person uses. */
    suspend fun wearSkin(id: String) = withContext(Dispatchers.IO) {
        runCatching {
            post("/skins", JSONObject().put("skin", id))
            Skin.wear(JSONObject(get("/mood")))
        }
        Unit
    }

    /** Whether what is on screen is coming from the cache rather than the server. */
    fun standingBy(): Boolean = onStandby

    /**
     * Move the whole app to the machine that keeps copies.
     *
     * The player finds the server gone before anything else does - it is the only
     * part asking for bytes every second - and when it moves house it used to move
     * alone: the film played from the cache while the shelves behind it, the next
     * episode and everything else still asked the machine that had just gone off. So
     * leaving the film went back to a menu that could not answer.
     *
     * Nothing is written down. The way home is remembered, and the first request
     * after the server answers again goes back to it.
     */
    fun onTheCopyNow(where: String) {
        val to = where.trimEnd('/')
        if (to.isEmpty() || to == base) return
        homeBase = base
        base = to
        onStandby = true
        triedHome = System.currentTimeMillis()
    }

    /** Ask the server where its cache is, and remember it for when it is off. */
    fun learnStandby(ctx: Context) {
        val here = base
        standby = prefs(ctx).getString("standby:" + here, "") ?: ""
        standbyOut = prefs(ctx).getString("standbyOut:" + here, "") ?: ""
        try {
            val said = JSONObject(get("/standby"))
            prefersTheCopy = said.optBoolean("prefer")
            val where = said.optString("where").trimEnd('/')
            val out = said.optString("outside").trimEnd('/')
            standbyName = said.optString("name")
            if (standbyName.isNotEmpty()) {
                prefs(ctx).edit().putString("standbyName:" + here, standbyName).apply()
            } else {
                standbyName = prefs(ctx).getString("standbyName:" + here, "") ?: ""
            }
            // and the machine this one follows, which is the other half of the
            // same question. A server names the machine that copies from it in
            // "where" and the machine it copies from in "follows" - so on the copy
            // the first is empty and only the second answers. Reading one of the two
            // meant the app could see the other machine from the main server and not from
            // the cache.
            val up = said.optJSONObject("follows")
            houseWhere = (up?.optString("lan").orEmpty().trimEnd('/'))
                .ifEmpty { up?.optString("outside").orEmpty().trimEnd('/') }
            houseName = up?.optString("name").orEmpty()
            val edit = prefs(ctx).edit()
            if (houseWhere.isNotEmpty()) {
                edit.putString("house:" + here, houseWhere)
                edit.putString("houseName:" + here, houseName)
            } else {
                houseWhere = prefs(ctx).getString("house:" + here, "") ?: ""
                houseName = prefs(ctx).getString("houseName:" + here, "") ?: ""
            }
            if (where.isNotEmpty() && where != here) {
                standby = where
                edit.putString("standby:" + here, where)
            }
            if (out.isNotEmpty() && out != here) {
                standbyOut = out
                edit.putString("standbyOut:" + here, out)
            }
            edit.apply()
            // and this server's own two addresses, which it has just handed over.
            // Learned here because here is where it can be: a screen away from home
            // cannot ask a machine it cannot reach where else that machine lives.
            val mine = said.optJSONObject("mine")
            if (mine != null) {
                Servers.learnDoors(ctx, here, mine.optString("lan"),
                                   mine.optString("outside"), mine.optString("name"))
                listOf(mine.optString("lan"), mine.optString("outside"))
                    .map { it.trimEnd('/') }
                    .firstOrNull { it.isNotEmpty() && it != here }
                    ?.let { otherWay = it }
            }
            // and it goes in the list of servers like any other, tagged as what it
            // is: a viewer switches to it the same way, and a guest never has to be
            // told which port it answers on
            val home = Regex("""^https?://(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.)""")
            val pick = if (home.containsMatchIn(here)) standby.ifEmpty { standbyOut }
                       else standbyOut.ifEmpty { standby }
            if (pick.isNotEmpty()) {
                // both doors, and the key in use: the row is filed under whichever
                // address suits where we are standing now, and carries the other
                Servers.learnCopy(ctx, here, pick, said.optString("name"), token,
                                  if (pick == standbyOut) standby else standbyOut)
            }
        } catch (e: Exception) {
            // an older server, or one that is not answering: whatever was written
            // down last time is what there is
        }
    }

    /** The server this app opens with, and the list it came from. */
    fun loadServer(ctx: Context): String {
        Servers.current(ctx)?.let { use(it) }
        return base
    }

    fun saveServer(ctx: Context, url: String, tok: String = "", name: String = "") {
        val clean = url.trim().trimEnd('/')
        val s = Server(name.ifEmpty { Servers.hostOf(clean) }, clean, tok, mine = tok.isEmpty())
        Servers.add(ctx, s)
        Servers.use(ctx, s)
    }

    /** Add the token where the URL will accept it. */
    private fun auth(path: String, tok: String): String =
        if (tok.isEmpty()) path
        else path + (if (path.contains("?")) "&" else "?") + "t=" + tok

    /**
     * What this client calls itself, for the server to write down.
     *
     * The owner wants to know who is on which version - a fault reported from a
     * three-week-old build is a different conversation from one on today's - and
     * nobody is going to read it off the television for them.
     */
    fun appName(): String =
        "android " + BuildConfig.VERSION_NAME +
            (if (device == "tv") " tv" else " phone")

    private fun fetch(b: String, path: String, t: String, patience: Int,
                      reach: Int = 8000): String {
        val conn = URL(b + auth(path, t)).openConnection() as HttpURLConnection
        conn.connectTimeout = reach
        conn.readTimeout = patience
        conn.setRequestProperty("Accept", "application/json")
        if (t.isNotEmpty()) conn.setRequestProperty("X-Palladium-Token", t)
        conn.setRequestProperty("X-Palladium-App", appName())
        conn.inputStream.use { return it.readBytes().toString(Charsets.UTF_8) }
    }

    private fun get(path: String, srv: Server? = null, patience: Int = 30000): String {
        // asked for quickly means reached for quickly: eight seconds to connect is
        // eight more the screen spends showing what it had
        val reach = if (patience <= 5000) 3000 else 8000
        val b = srv?.base ?: base
        val t = srv?.token ?: token
        // Away on the cache: try the server with the library on it about once a
        // minute, with the request that was going to be made anyway. If it answers,
        // we are home; if it does not, this costs one connection refused.
        // Whenever there is a way back, not only when the cache is what we fell on.
        // Falling back to this server's own outside address is not standing by - it
        // is the same library the long way round - so that case set the flag false
        // and the way home was never tried again. One restart while a phone was on
        // the house network and it was out through the router for good.
        if (srv == null && homeBase.isNotEmpty() &&
            System.currentTimeMillis() - triedHome > 60_000L) {
            triedHome = System.currentTimeMillis()
            try {
                val said = fetch(homeBase, path, t, patience, reach)
                // and the address we were using becomes the other way in, so the
                // next fall is as quick as this one
                if (base != homeBase) otherWay = base
                base = homeBase
                homeBase = ""
                onStandby = false
                return said
            } catch (away: java.io.IOException) {
                // still off: carry on with the cache
            }
        }
        try {
            return fetch(b, path, t, patience, reach)
        } catch (e: java.io.IOException) {
            // A server that refuses or complains has answered: that is its answer,
            // and the cache would only repeat it. One that cannot be reached at
            // all has not answered, and the machine keeping copies of its films can
            // be asked instead.
            val gone = e is java.net.ConnectException ||
                       e is java.net.SocketTimeoutException ||
                       e is java.net.UnknownHostException ||
                       e is java.net.NoRouteToHostException ||
                       e is java.net.PortUnreachableException
            if (!gone) throw e
            if (srv != null || b != base) throw e
            // at home the cache is on the network, away from it behind the same
            // router on its own port. Which of the two answers is which side of the
            // door this screen is on, and asking is cheaper than knowing.
            // Its own other door before anybody else's machine: the main server answering
            // from outside is still the main server, with the whole library on it, and the
            // copy holds a fraction of it.
            // the main server by the cache's outside address and its own port, before the cache
            // itself: a phone that never learned the main server's outside address could
            // otherwise reach only the cache from away, and a copy that was down left it
            // with nothing while the main server was answering
            val houseDoor = houseBehindTheCopysDoor(b)
            for (other in listOf(otherWay, houseDoor, standby, standbyOut)) {
                if (other.isEmpty() || other == b) continue
                val said = try {
                    fetch(other, path, t, patience)
                } catch (again: java.io.IOException) {
                    continue
                }
                homeBase = b               // the one to come back to
                base = other
                // its own front door is not standing by for anything: it is the same
                // library, reached the long way round
                val theHouse = other == otherWay || other == houseDoor
                onStandby = !theHouse
                if (theHouse) otherWay = b
                triedHome = System.currentTimeMillis()
                return said
            }
            throw e
        }
    }

    /**
     * The main server's way in from outside, worked out from the cache's.
     *
     * Two machines behind one router share the address the internet sees, each on its
     * own port - which is what the server itself assumes when nobody has said
     * otherwise. Only offered for a house filed under its network address, with a copy
     * known by an outside one.
     */
    private fun houseBehindTheCopysDoor(b: String): String {
        if (standbyOut.isEmpty() || !Servers.athome(b) || Servers.athome(standbyOut)) return ""
        val port = Servers.hostOf(b).substringAfter(":", "")
        val host = Servers.hostOf(standbyOut).substringBefore(":")
        if (port.isEmpty() || host.isEmpty()) return ""
        return "http://$host:$port"
    }

    private suspend fun json(path: String, srv: Server? = null,
                            patience: Int = 30000): JSONObject =
        withContext(Dispatchers.IO) { JSONObject(get(path, srv, patience)) }

    /**
     * Does this server want a password, and have we not given it?
     *
     * Asked before anything else is blamed: a 403 from a server with a password is a
     * different thing from a 403 from somebody else's server, and telling them apart
     * is the difference between a password box and a useless error.
     */
    suspend fun wantsPassword(url: String, tok: String = ""): Boolean =
        withContext(Dispatchers.IO) {
            try {
                val conn = URL(auth(url.trimEnd('/') + "/auth/state", tok))
                    .openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 8000
                if (tok.isNotEmpty()) conn.setRequestProperty("X-Palladium-Token", tok)
                val said = JSONObject(
                    conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) })
                said.optBoolean("password") && !said.optBoolean("owner")
            } catch (e: Exception) {
                false
            }
        }

    /**
     * Give the password, and take away a token that stands for it.
     *
     * The token is kept the way an invitation is kept, because to everything else in
     * this app it is the same thing: a string that says who is asking.
     */
    suspend fun signIn(url: String, password: String): String? =
        withContext(Dispatchers.IO) {
            try {
                val conn = URL(url.trimEnd('/') + "/login")
                    .openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 5000
                conn.readTimeout = 15000
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("X-Palladium-App", appName())
                conn.outputStream.use {
                    it.write(JSONObject().put("password", password).toString()
                                 .toByteArray(Charsets.UTF_8))
                }
                val said = JSONObject(
                    conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) })
                said.optString("token").ifEmpty { null }
            } catch (e: Exception) {
                null
            }
        }

    /** The server is reachable and has a library. */
    suspend fun ping(url: String, tok: String = ""): String? = withContext(Dispatchers.IO) {
        try {
            val conn = URL(auth("$url/local/library/sections", tok))
                .openConnection() as HttpURLConnection
            conn.connectTimeout = 5000
            conn.readTimeout = 8000
            val body = conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
            val dirs = JSONObject(body).getJSONObject("MediaContainer").optJSONArray("Directory")
            if (dirs != null && dirs.length() > 0) null else "No libraries found"
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            e.message ?: "cannot reach $url"
        }
    }

    /** Parse a container, marking every title with the server it came from. */
    private fun items(container: JSONObject, srv: Server?): List<Media> {
        val out = ArrayList<Media>()
        val arr: JSONArray = container.optJSONArray("Metadata") ?: return out
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out.add(Media.from(o).also {
                it.srv = srv
                it.genres = genresOf(o)
                it.shelfView = o.optJSONObject("view")?.toString() ?: ""
                it.offered = o.optBoolean("offered", false)
                o.optJSONObject("offer")?.let { offer ->
                    it.offerState = offer.optString("state", "")
                    it.offerProgress = offer.optDouble("progress", 0.0)
                    it.offerSize = offer.optLong("size", 0L)
                    it.offerFree = if (offer.isNull("free")) -1.0
                                   else offer.optDouble("free", -1.0)
                    it.offerRefused = offer.optString("refused", "")
                    it.offerMbit = offer.optDouble("mbit", 0.0)
                    it.offerEta = if (offer.isNull("eta")) -1L else offer.optLong("eta", -1L)
                    it.offerWho = offer.optString("who", "")
                    it.offerPlace = offer.optInt("place", 0)
                    it.offerVersions = offer.optJSONArray("versions")?.let { vs ->
                        (0 until vs.length()).map { n ->
                            val v = vs.getJSONObject(n)
                            v.optString("key") to (v.optString("label", "Standard") +
                                String.format(java.util.Locale.US, "  \u00b7  %.1f GB",
                                              v.optLong("size", 0L) / 1e9))
                        }
                    } ?: emptyList()
                }
            })
        }
        return out
    }

    private fun genresOf(o: JSONObject): List<String> =
        o.optJSONArray("genres")?.let { a ->
            (0 until a.length()).map { a.optString(it) }.filter { it.isNotEmpty() }
        } ?: emptyList()

    /**
     * Ask every shown server the same question and stitch the answers together.
     *
     * A friend's machine being off must not empty the shelf, so a server that fails
     * contributes nothing and is otherwise ignored.
     */
    /**
     * Ask every shown server at once and take what has arrived.
     *
     * They were asked one after another, so a shelf took as long as all of them
     * added together and one server on a bad line held up the screen for everybody
     * else. They are asked together now, and a server that has not answered within a
     * few seconds contributes nothing to this draw - the next one will have it, and
     * the shelves fill as the answers come rather than waiting for the slowest.
     *
     * The order is still by how quickly each answered last time, so where two of
     * them hold the same film it is the near one whose card survives.
     */
    private suspend fun fromAll(ctx: Context, call: suspend (Server) -> List<Media>): List<Media> =
        withContext(Dispatchers.IO) {
            coroutineScope {
                val asked = Servers.merged(ctx).map { srv ->
                    async {
                        withTimeoutOrNull(6000L) {
                            runCatching { call(srv) }.getOrDefault(emptyList())
                        } ?: emptyList()
                    }
                }
                val out = ArrayList<Media>()
                asked.forEach { out.addAll(it.await()) }
                distinct(out)
            }
        }

    /**
     * The same thing twice is shown once.
     *
     * Identity is what the item is, not which server it came from: a film is its title
     * and year, an episode is its series and its number. Two addresses for one server
     * are meant to be folded together before this, but a shelf is not the place to
     * discover that they were not - and if two friends both hold Dune, one card that
     * plays is better than two that are the same.
     */
    private fun distinct(list: List<Media>): List<Media> {
        val seen = HashSet<String>()
        return list.filter { m ->
            val mark = when (m.type) {
                "episode" -> listOf("e", m.grandparentTitle, m.parentIndex, m.index,
                                    m.title)
                // A season card is named after its programme, so eighteen seasons
                // of one series were eighteen cards with the same name and all but
                // the first were dropped: what is on the casual shelf is the season
                // number as much as the title.
                "season" -> listOf("s", m.title.lowercase(), m.year, m.index)
                else -> listOf(m.type, m.title.lowercase(), m.year)
            }.joinToString("|")
            seen.add(mark)
        }
    }

    private suspend fun listFrom(path: String, srv: Server, patience: Int = 30000) =
        items(json(path, srv, patience).getJSONObject("MediaContainer"), srv)

    /** Titles on a shelf with these filters: the server's totalSize, the largest any server reports. */
    suspend fun shelfCount(ctx: Context, section: Int, genre: String, decade: String): Int =
        withContext(Dispatchers.IO) {
            val shelf = (if (genre.isEmpty()) ""
                         else "&genre=" + URLEncoder.encode(genre, "UTF-8")) +
                        (if (decade.isEmpty()) "" else "&decade=" + decade)
            var best = -1
            for (srv in Servers.merged(ctx)) {
                val n = withTimeoutOrNull(6000L) {
                    runCatching {
                        json("/local/library/sections/$section/all?sort=titleSort:asc" + shelf +
                             "&start=0&count=1", srv)
                            .getJSONObject("MediaContainer").optInt("totalSize", -1)
                    }.getOrDefault(-1)
                } ?: -1
                if (n > best) best = n
            }
            best
        }

    /**
     * One page of a library, merged across servers.
     *
     * Each server is asked for the top start+size of its own list; the join is sorted
     * and the window taken from it. Every title that belongs in the window is somewhere
     * in those answers, so the page is exact without any server having seen the whole
     * list - and the screen fills after 120 rows rather than after all of them.
     */
    private suspend fun page(ctx: Context, section: Int, sort: String,
                             start: Int, size: Int, genre: String = "",
                             decade: String = ""): List<Media> {
        // one genre and one decade at a time, asked of each server: the shelf is the
        // question, the order is how it is answered
        val shelf = (if (genre.isEmpty()) ""
                     else "&genre=" + URLEncoder.encode(genre, "UTF-8")) +
                    (if (decade.isEmpty()) "" else "&decade=" + decade)
        val merged = sortMerged(fromAll(ctx) { srv ->
            val got = listFrom("/local/library/sections/$section/all?sort=$sort" + shelf +
                    "&start=0&count=${start + size}", srv)
            // A shelf that comes back empty is worth a line: which machine was asked,
            // and what it said. Guessing at an empty Films tab from the outside is
            // guessing at which of four things went wrong.
            if (got.isEmpty()) {
                android.util.Log.i("Palladium",
                    "section " + section + " empty from " + srv.base +
                    (if (srv.token.isEmpty()) " (no token)" else " (token)"))
            }
            got
        }, sort)
        if (merged.isEmpty()) {
            android.util.Log.i("Palladium",
                "section " + section + " empty after asking " +
                Servers.merged(ctx).joinToString(", ") { it.base })
        }
        return if (start >= merged.size) emptyList()
               else merged.subList(start, minOf(start + size, merged.size)).toList()
    }

    suspend fun movies(ctx: Context, sort: String = "titleSort:asc",
                       start: Int = 0, size: Int = 120, genre: String = "",
                       decade: String = "") =
        page(ctx, 1, sort, start, size, genre, decade)

    suspend fun shows(ctx: Context, sort: String = "titleSort:asc",
                      start: Int = 0, size: Int = 120, genre: String = "",
                      decade: String = "") =
        page(ctx, 2, sort, start, size, genre, decade)

    /** Each server sorted its own share; the join needs one more pass. */
    private fun sortMerged(list: List<Media>, sort: String): List<Media> {
        val desc = sort.endsWith(":desc")
        val by: (Media) -> Comparable<*> = when {
            sort.startsWith("year") -> ({ m: Media -> m.year ?: 0 })
            sort.startsWith("addedAt") -> ({ m: Media -> m.addedAt })
            sort.startsWith("quality") -> ({ m: Media -> m.maxHeight })
            // dates as text sort correctly while they stay yyyy-mm-dd. A series with no
            // aired episode falls back to its year, as the server does.
            sort.startsWith("originallyAvailableAt") -> ({ m: Media ->
                m.released.ifEmpty {
                    if ((m.year ?: 0) > 0) String.format("%04d-01-01", m.year) else ""
                }
            })
            else -> ({ m: Media -> m.title.lowercase() })
        }
        // A title with no year and no air date is not the oldest thing in the library
        // and not the newest: it is unidentified, and it belongs at the bottom whichever
        // way the list runs. The server orders its own share that way; this pass put
        // them back at the top of an ascending list.
        val undated: (Media) -> Int = when {
            // a file nobody has measured is unknown, not small
            sort.startsWith("quality") ->
                ({ m: Media -> if (m.maxHeight <= 0) 1 else 0 })
            sort.startsWith("year") ->
                ({ m: Media -> if ((m.year ?: 0) <= 0) 1 else 0 })
            sort.startsWith("originallyAvailableAt") ->
                ({ m: Media -> if (m.released.isEmpty() && (m.year ?: 0) <= 0) 1 else 0 })
            else -> ({ _: Media -> 0 })
        }
        @Suppress("UNCHECKED_CAST")
        val cmp = compareBy<Media> { by(it) as Comparable<Any> }
        val ordered = if (desc) list.sortedWith(cmp.reversed()) else list.sortedWith(cmp)
        return ordered.sortedBy(undated)          // stable: the order above is kept
    }

    suspend fun recentFilms(ctx: Context) = fromAll(ctx) { srv ->
        listFrom("/local/library/sections/1/recentlyAdded?count=30", srv)
    }.sortedByDescending { it.addedAt }.take(40)

    suspend fun recentEpisodes(ctx: Context) = fromAll(ctx) { srv ->
        listFrom("/local/library/sections/2/recentlyAdded?count=30", srv)
    }.sortedByDescending { it.addedAt }.take(40)

    /** Newest first by release date: for a series that is its most recent episode. */
    suspend fun releasedFilms(ctx: Context) =
        page(ctx, 1, "originallyAvailableAt:desc", 0, 30)

    suspend fun releasedShows(ctx: Context) =
        page(ctx, 2, "originallyAvailableAt:desc", 0, 30)

    /**
     * The shelves this viewer keeps, drawn as things with posters.
     *
     * A collection belongs to the server that holds it, so a friend's shelves arrive
     * beside your own and each one is opened on the machine it came from.
     */
    suspend fun collections(ctx: Context) = fromAll(ctx) { srv ->
        listFrom("/local/library/collections", srv)
    }

    /** What one shelf holds, oldest first, from the server that holds it. */
    suspend fun collectionItems(shelf: Media): List<Media> = withContext(Dispatchers.IO) {
        runCatching {
            val id = URLEncoder.encode(shelf.ratingKey, "UTF-8")
            items(json("/local/library/collection?id=$id", shelf.srv)
                      .getJSONObject("MediaContainer"), shelf.srv)
        }.getOrDefault(emptyList())
    }

    /**
     * Every key a shelf's answer amounts to.
     *
     * Marking a programme marks its episodes, so the list itself holds no key for the
     * programme or the season. The server says what those episodes cover - whole, and
     * in part - and a screen asking "is this marked?" about a series means either.
     */
    private fun keysOf(o: JSONObject, field: String): Set<String> {
        val out = mutableSetOf<String>()
        for (name in listOf(field, "covers", "part")) {
            val arr = o.optJSONArray(name) ?: continue
            for (i in 0 until arr.length()) out.add(arr.getString(i))
        }
        return out
    }

    /**
     * Put a subtitle in step with the film, by listening to the film.
     *
     * The server decodes the sound, finds where the dialogue is and slides the
     * subtitle over it; a few seconds for a film, and instant for a second subtitle
     * of the same one. Comes back with how far it moved and whether it was sure -
     * when it is not, nothing has been written and nothing should move.
     */
    /**
     * What a measurement came to.
     *
     * `parts` is the plan: each is (from this second, rate, shift). One part means the
     * subtitle is simply out by that much all the way through; more than one means the
     * film carries something its subtitle's master did not, and everything after that
     * point is later by a different amount.
     */
    data class Sync(val offset: Float, val rate: Float, val sure: Boolean,
                    val why: String,
                    val parts: List<Triple<Float, Float, Float>> = emptyList()) {
        val steps get() = parts.size > 1

        /**
         * In words, the same five the browser uses: "static +1.2s", "drift -1.8s ->
         * -4.5s", "steps +1.2s, then +3.8s from 11:15", "by hand +1.0s", "none".
         *
         * `ends` is how long the film runs, which a drift needs: a rate is two numbers
         * to a reader - what it comes to at the start and what at the end.
         */
        fun said(ends: Float = 0f): String {
            if (parts.isEmpty()) {
                return if (offset == 0f) "none"
                       else String.format(java.util.Locale.US, "static %+.1fs", offset)
            }
            if (parts.size > 1) {
                val rest = parts.drop(1).joinToString(", ") {
                    String.format(java.util.Locale.US, "%+.1fs from %d:%02d",
                                  it.third, (it.first / 60).toInt(),
                                  (it.first % 60).toInt())
                }
                return String.format(java.util.Locale.US, "steps %+.1fs, then %s",
                                     parts[0].third, rest)
            }
            val (_, rate, shift) = parts[0]
            if (Math.abs(rate - 1f) > 1e-6f) {
                return String.format(java.util.Locale.US, "drift %+.1fs → %+.1fs",
                                     shift, (rate - 1f) * ends + shift)
            }
            return String.format(java.util.Locale.US, "static %+.1fs", shift)
        }
    }

    suspend fun syncSubtitle(m: Media, index: Int, sub: String,
                             srv: Server? = null): Sync =
        syncSubtitle(m.ratingKey, index, sub, srv ?: m.srv)

    /** The same, for a player holding a key and no title - which is most of the time. */
    suspend fun syncSubtitle(key: String, index: Int, sub: String,
                             srv: Server? = null, mi: Int = 0): Sync =
        withContext(Dispatchers.IO) {
            try {
                val q = "key=" + java.net.URLEncoder.encode(key, "UTF-8") +
                    "&mi=" + mi + "&index=" + index +
                    "&skey=" + java.net.URLEncoder.encode("l" + key, "UTF-8") +
                    "&sub=" + java.net.URLEncoder.encode(sub, "UTF-8") + "&save=1"
                // Measuring takes as long as it takes: eleven windows of audio
                // decoded across the film. 33 seconds on a 106-minute film with
                // nothing cached, which the ordinary 30-second limit reported as
                // "could not place it" while the server went on and succeeded.
                val o = JSONObject(get("/subs/sync?" + q, srv, patience = 300000))
                // a rate other than one says the server straightened the file
                // itself, because the correction is a different number at every minute
                val plan = ArrayList<Triple<Float, Float, Float>>()
                val rows = o.optJSONArray("parts")
                for (i in 0 until (rows?.length() ?: 0)) {
                    val row = rows!!.optJSONArray(i) ?: continue
                    plan.add(Triple(row.optDouble(0).toFloat(),
                                    row.optDouble(1, 1.0).toFloat(),
                                    row.optDouble(2).toFloat()))
                }
                Sync(o.optDouble("offset", 0.0).toFloat(),
                     o.optDouble("rate", 1.0).toFloat(), o.optBoolean("sure"),
                     o.optString("why", o.optString("error")), plan)
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) {
                Sync(0f, 1f, false, "could not reach the server", emptyList())
            }
        }

    /**
     * What is already known about this subtitle's timing, without measuring anything.
     *
     * A player asks on the way in, so the menu can say what is in force before anybody
     * presses a thing - and so a correction by hand is plainly on top of it.
     */
    suspend fun subtitlePlan(m: Media, index: Int, srv: Server? = null): Sync =
        subtitlePlan(m.ratingKey, index, srv ?: m.srv)

    /** The same, for a player that has a key and has not fetched the title yet. */
    suspend fun subtitlePlan(key: String, index: Int, srv: Server? = null,
                             mi: Int = 0): Sync =
        withContext(Dispatchers.IO) {
            try {
                val q = "key=" + java.net.URLEncoder.encode(key, "UTF-8") +
                    "&mi=" + mi + "&index=" + index
                val o = JSONObject(get("/subs/plan?" + q, srv))
                val plan = ArrayList<Triple<Float, Float, Float>>()
                val rows = o.optJSONArray("parts")
                for (i in 0 until (rows?.length() ?: 0)) {
                    val row = rows!!.optJSONArray(i) ?: continue
                    plan.add(Triple(row.optDouble(0).toFloat(),
                                    row.optDouble(1, 1.0).toFloat(),
                                    row.optDouble(2).toFloat()))
                }
                Sync(plan.firstOrNull()?.third ?: 0f, 1f, plan.isNotEmpty(), "", plan)
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) {
                Sync(0f, 1f, false, "", emptyList())
            }
        }

    /**
     * Put a subtitle back where its own file has it.
     *
     * The plan the server was applying goes, and so does whatever anybody nudged on
     * top of it. What the file says is always recoverable; a correction is not.
     */
    suspend fun resetSubtitle(key: String, index: Int, skey: String, sub: String,
                              srv: Server? = null, mi: Int = 0): Boolean =
        withContext(Dispatchers.IO) {
            try {
                postTo(srv, "/subs/reset", JSONObject()
                    .put("key", key).put("mi", mi).put("index", index)
                    .put("skey", skey).put("sub", sub))
                true
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) { false }
        }

    /** Whether this viewer wants subtitles placed by themselves. */
    suspend fun autoSync(): Boolean = withContext(Dispatchers.IO) {
        try { JSONObject(get("/settings?device=" + device)).optBoolean("autoSync") }
        catch (stopped: kotlinx.coroutines.CancellationException) { throw stopped }
        catch (e: Exception) { false }
    }

    /** Set it, for this viewer, everywhere they watch. */
    /**
     * Which build the server is running, and whether this screen is in its house.
     *
     * Public and cheap: asked on the way into the library, so the app can say when the
     * machine it is talking to has been replaced under it.
     */
    data class ServerNow(val version: String, val lan: Boolean)

    /** One line in the room: who said it, what they said, and when. */
    data class Said(val id: Int, val who: String, val text: String, val when_: Long)

    /**
     * What has been said since the id given.
     *
     * The server holds the answer open until somebody writes, so this returns when
     * there is something to show rather than on a clock.
     */
    suspend fun chat(since: Int = 0, hold: Int = 0, room: String = "party"):
        List<Said> = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(get("/chat?room=" + room + "&since=" + since +
                                   "&wait=" + hold,
                                   patience = (hold + 12) * 1000))
            val arr = o.optJSONArray("messages") ?: JSONArray()
            (0 until arr.length()).map {
                val m = arr.getJSONObject(it)
                Said(m.optInt("id"),
                     m.optString("from").ifEmpty { m.optString("who") },
                     m.optString("text"), m.optLong("when"))
            }
        } catch (e: Exception) { emptyList() }
    }

    /** Say something into one of the rooms. */
    suspend fun say(text: String, from: String = "", room: String = "party"): Boolean =
        withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().put("text", text).put("room", room)
            if (from.isNotEmpty()) body.put("from", from)
            JSONObject(post("/chat", body)).optBoolean("ok")
        } catch (e: Exception) { false }
    }

    /** One screen in the lobby: what it is, and what it has playing. */
    data class Present(val name: String, val watching: String)

    /**
     * The state of the room: whether a party is on, what it is watching, who is about,
     * and whether this screen has been asked to join one.
     */
    data class Party(val on: Boolean, val inIt: Boolean, val title: String,
                     val key: String, val invitedBy: String, val invitedTo: String,
                     val here: List<Present>, val off: Boolean)

    suspend fun party(): Party? = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(get("/party"))
            val asked = o.optJSONObject("invited")
            val about = o.optJSONArray("here") ?: JSONArray()
            Party(o.optBoolean("on"), o.optBoolean("in"), o.optString("title"),
                  o.optString("key"),
                  asked?.optString("from") ?: "", asked?.optString("title") ?: "",
                  (0 until about.length()).map {
                      val p = about.getJSONObject(it)
                      Present(p.optString("name"), p.optString("watching"))
                  },
                  o.optBoolean("off"))
        } catch (e: Exception) { null }
    }

    /** Join what somebody has asked this screen to, or say no to it. */
    suspend fun answerParty(join: Boolean): Boolean = withContext(Dispatchers.IO) {
        try {
            post("/party", JSONObject().put(if (join) "accept" else "decline", true))
            true
        } catch (e: Exception) { false }
    }

    /** A line the owner has sent to the screens in the house, if there is one. */
    data class Notice(val id: Int, val text: String)

    /**
     * Waits for one, rather than asking for one.
     *
     * The connection is held open by the server until somebody writes something or a
     * minute goes by, so a message lands on the television the moment it is sent
     * instead of up to half a minute later. `patience` is longer than the wait it
     * asks for, or the read times out on the answer.
     */
    suspend fun notice(since: Int = 0, hold: Int = 0): Notice? =
        withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(get("/notice?since=" + since + "&wait=" + hold,
                                   patience = (hold + 12) * 1000))
            val said = o.optString("text")
            if (said.isEmpty()) null else Notice(o.optInt("id"), said)
        } catch (e: Exception) { null }
    }

    suspend fun serverNow(): ServerNow? = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(get("/build"))
            val v = o.optString("version")
            if (v.isEmpty()) null else ServerNow(v, o.optBoolean("lan", true))
        } catch (e: Exception) { null }
    }

    /** What build the server is, what is out, and whether this screen may say so. */
    data class ServerBuild(val have: String, val latest: String, val newer: Boolean,
                           val lan: Boolean, val why: String)

    suspend fun serverBuild(fresh: Boolean = false): ServerBuild? =
        withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(get("/update" + (if (fresh) "?force=1" else ""),
                                   null, 40000))
            // an older server does not answer this at all, and refusing to offer
            // the update because it said nothing is worse than offering it and being
            // told no - which is what the server does when it means it
            ServerBuild(o.optString("have"), o.optString("latest"),
                        o.optBoolean("newer"), o.optBoolean("lan", true),
                        o.optString("why"))
        } catch (e: Exception) { null }
    }

    /**
     * The same two questions, asked of another machine.
     *
     * The machine that keeps copies is a server like any other and needs replacing
     * like any other - and switching the whole app over to it to press one button,
     * then switching back, is a silly way to spend an evening.
     */
    suspend fun serverBuildAt(where: String, tok: String = "",
                              fresh: Boolean = false): ServerBuild? =
        withContext(Dispatchers.IO) {
            try {
                // a server keeps its answer about the site for a while, which is
                // right for a page that draws itself every few seconds and wrong for
                // somebody standing there pressing Check
                val o = JSONObject(fetch(where.trimEnd('/'),
                                         "/update" + (if (fresh) "?force=1" else ""),
                                         tok, 40000))
                ServerBuild(o.optString("have"), o.optString("latest"),
                            o.optBoolean("newer"), o.optBoolean("lan", true),
                            o.optString("why"))
            } catch (e: Exception) { null }
        }

    suspend fun updateServerAt(where: String, tok: String = ""): String? =
        withContext(Dispatchers.IO) {
            try {
                val conn = URL(where.trimEnd('/') + auth("/update/install", tok))
                    .openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 8000
                conn.readTimeout = 25000
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("X-Palladium-App", appName())
                conn.outputStream.use { it.write("{}".toByteArray()) }
                val o = JSONObject(
                    conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) })
                if (o.optBoolean("ok")) null else o.optString("why", "it did not work")
            } catch (e: Exception) { e.message ?: "could not reach that machine" }
        }

    /** Take it. The server answers first and replaces itself after. */
    suspend fun updateServer(): String? = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(post("/update/install", JSONObject()))
            if (o.optBoolean("ok")) null else o.optString("why", "it did not work")
        } catch (e: Exception) { e.message ?: "could not reach the server" }
    }

    /** Whether the next episode's subtitle is fetched before it starts. */
    suspend fun fetchAhead(): Boolean = withContext(Dispatchers.IO) {
        try { JSONObject(get("/settings")).optBoolean("autoFetch", true) }
        catch (e: Exception) { true }
    }

    suspend fun setFetchAhead(on: Boolean) = withContext(Dispatchers.IO) {
        runCatching { post("/settings", JSONObject().put("autoFetch", on)) }
        Unit
    }

    suspend fun setAutoSync(on: Boolean) = withContext(Dispatchers.IO) {
        runCatching { post("/settings", JSONObject().put("autoSync", on)) }
        Unit
    }

    /** Where artwork stands behind what is on screen: on, poster, or off. The server
     *  keeps one answer per kind of screen, and takes this for the one asking. */
    suspend fun setBackdrop(where: String) = withContext(Dispatchers.IO) {
        runCatching { post("/settings", JSONObject().put("backdrop", where)) }
        Unit
    }

    /** One collection, and whether it holds all, some or none of a title. */
    data class ShelfMark(val id: String, val name: String, val state: String)

    private fun shelfMarks(text: String): List<ShelfMark> {
        val arr = JSONObject(text).optJSONArray("collections") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            ShelfMark(o.optString("id"), o.optString("name"), o.optString("state", "none"))
        }
    }

    /** Which collections hold this title. */
    suspend fun shelvesHolding(m: Media): List<ShelfMark> = withContext(Dispatchers.IO) {
        try {
            shelfMarks(postTo(m.srv, "/collections/for", JSONObject().put("key", m.ratingKey)))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptyList()
        }
    }

    /** A title on or off one collection: the collections as they now stand, or null. */
    suspend fun markShelf(m: Media, id: String, on: Boolean): List<ShelfMark>? =
        withContext(Dispatchers.IO) {
            try {
                shelfMarks(postTo(m.srv, "/collections/mark",
                                  JSONObject().put("id", id).put("key", m.ratingKey)
                                      .put("on", on)))
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) {
                null
            }
        }

    /** Make a collection of its own for this title, and put the title on it. */
    suspend fun newShelf(m: Media, name: String): List<ShelfMark>? =
        withContext(Dispatchers.IO) {
            try {
                val made = JSONObject(postTo(m.srv, "/collections",
                                             JSONObject().put("name", name).put("mode", "manual")))
                val arr = made.optJSONArray("collections") ?: return@withContext null
                val id = (0 until arr.length()).map { arr.getJSONObject(it) }
                    .firstOrNull { it.optString("name").equals(name, ignoreCase = true) }
                    ?.optString("id") ?: return@withContext null
                markShelf(m, id, true)
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) {
                null
            }
        }

    /** A draw from a shelf, and the second to start it at. */
    data class Draw(val media: Media, val resumeAt: Long)

    /**
     * Draw from one shelf, shuffled, and say where it was left.
     *
     * The round belongs to the person and the shelf, and the main server keeps it - so the
     * same evening carries on from the television, the phone or the browser rather
     * than each of them holding an evening of its own.
     */
    suspend fun shelfDraw(shelf: Media, resume: Boolean = true): Draw? =
        shelfDraw(shelf.ratingKey, shelf.srv, resume)

    /**
     * The same, by the shelf's name alone - which is all a film playing knows about
     * where it came from.
     */
    suspend fun shelfDraw(id: String, srv: Server? = null,
                          resume: Boolean = true, back: Boolean = false): Draw? =
            withContext(Dispatchers.IO) {
        try {
            // asked of the server the shelf belongs to. Shelves are gathered from
            // every server anybody has a key to, so the one on screen is frequently
            // not the one this page is otherwise talking to - and a shelf asked for
            // by name on the wrong machine is a shelf that does not exist.
            val o = JSONObject(postTo(srv, "/collections/shuffle",
                                      JSONObject().put("id",
                                          id.removePrefix("coll:"))
                                                  .put("resume", resume)
                                                  .put("back", back)))
            o.optJSONObject("item")?.let {
                Draw(Media.from(it).also { drew -> drew.srv = srv },
                     o.optLong("resumeAt", 0L))
            }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            // A draw comes back empty when the shelf is refused, when it is not on
            // that machine, or when the machine is not there - three faults the
            // player could only report as one.
            android.util.Log.i("Palladium", "shelf draw failed: " +
                e.javaClass.simpleName + " " + (e.message ?: ""))
            null
        }
    }

    /** What a shelf's round draws next, without drawing it. */
    suspend fun shelfPeek(id: String, srv: Server? = null): Media? =
            withContext(Dispatchers.IO) {
        try {
            JSONObject(postTo(srv, "/collections/shuffle",
                              JSONObject().put("id", id.removePrefix("coll:"))
                                          .put("peek", true)))
                .optJSONObject("item")?.let { Media.from(it).also { m -> m.srv = srv } }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            null
        }
    }

    /** The episode before this one, as /next gives the one after. */
    suspend fun previous(m: Media): Media? = withContext(Dispatchers.IO) {
        try {
            items(json("/local/prev?key=" + m.ratingKey, m.srv)
                      .getJSONObject("MediaContainer"), m.srv).firstOrNull()
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            null
        }
    }

    /** Everything this viewer has marked for later, newest mark first. */
    suspend fun watchlist(ctx: Context) = fromAll(ctx) { srv ->
        listFrom("/local/library/watchlist", srv)
    }

    /** The keys that are marked, for showing the star without fetching the shelf. */
    suspend fun marked(): Set<String> = withContext(Dispatchers.IO) {
        try {
            keysOf(JSONObject(post("/watchlist", JSONObject())), "watchlist")
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptySet()
        }
    }

    /** Mark something for later, or take the mark off. The new list comes back. */
    suspend fun mark(m: Media, on: Boolean): Set<String> = withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().put("key", m.ratingKey).put("on", on)
            keysOf(JSONObject(post("/watchlist", body)), "watchlist")
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptySet()
        }
    }

    /** The titles kept on both machines, for the Favorites list. */
    suspend fun favorites(ctx: Context) = fromAll(ctx) { srv ->
        listFrom("/local/library/favorites", srv)
    }

    /** Which titles are favourites, as last told: the heart on a poster reads it. */
    val favKeys = androidx.compose.runtime.mutableStateOf<Set<String>>(emptySet())

    /** Which titles are favourites. */
    suspend fun favored(): Set<String> = withContext(Dispatchers.IO) {
        try {
            keysOf(JSONObject(post("/favorites", JSONObject())), "favorites")
                .also { favKeys.value = it }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptySet()
        }
    }

    /** Make something a favourite, or take the mark off. */
    suspend fun favorite(m: Media, on: Boolean): Set<String> = withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().put("key", m.ratingKey).put("on", on)
            keysOf(JSONObject(post("/favorites", body)), "favorites")
                .also { favKeys.value = it }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptySet()
        }
    }

    /** Ask for one film from a torrent pack: whether it went, and what to say. */
    /** Stop a download: its file off in qBittorrent, the film offered again. */
    suspend fun torrentCancel(m: Media): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(postTo(m.srv, "/torrents/cancel",
                                      JSONObject().put("key", m.ratingKey)))
            if (o.optBoolean("ok")) Pair(true, "Download cancelled")
            else Pair(false, o.optString("why", "Could not cancel it"))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            Pair(false, "The server did not answer")
        }
    }

    /** Films coming in now - everyone's for the owner, a guest's own - for the line in the menu. */
    val downloading = androidx.compose.runtime.mutableStateOf<List<Media>>(emptyList())

    /** a film to open, asked for from the menu's download line */
    val openWanted = androidx.compose.runtime.mutableStateOf<Media?>(null)

    /** this title's download as the live list has it (refreshed app-wide), else as it was loaded */
    fun liveOffer(m: Media): Media = downloading.value.firstOrNull { it.ratingKey == m.ratingKey } ?: m

    /** last download summary traced, so a refresh every 3 s writes a line only on a change */
    private var downloadsSaid = ""

    suspend fun refreshDownloading(ctx: Context) {
        // Each server asked on its own, errors kept: fromAll dropped a failure into an empty
        // list, which on a television looked the same as nothing downloading.
        val rows = ArrayList<Media>()
        val trouble = ArrayList<String>()
        val servers = Servers.merged(ctx)
        for (srv in servers) {
            try {
                // Four seconds, said to the connection itself. withTimeoutOrNull
                // cannot cut a blocking read short - the coroutine only comes back
                // when the socket does - so a poll on the default thirty seconds held
                // the whole loop, and the screen kept the numbers it had until the app
                // was started again.
                val got = withTimeoutOrNull(6000L) {
                    listFrom("/torrents/active", srv, patience = 4000)
                }
                if (got == null) trouble.add(Servers.hostOf(srv.base) + " timed out")
                else rows.addAll(got)
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) {
                trouble.add(Servers.hostOf(srv.base) + " " + e.javaClass.simpleName + ": " +
                            (e.message ?: "").take(80))
            }
        }
        val list = distinct(rows)
        // Nothing from anywhere, and every server complained: that is this phone not
        // reaching the house, not a download that has finished. Writing the empty list
        // here blanked the percentage, the time left and the line under the tabs on
        // every poll that missed - which against a server busy transcoding is one a
        // minute - and each blank fell back to the shelf's snapshot, which has no time
        // left in it at all. What was last seen stays until something answers.
        val blind = list.isEmpty() && servers.isNotEmpty() && trouble.size == servers.size
        if (!blind) downloading.value = list
        // what this screen received, into the server log when it changes
        val key = list.joinToString(",") { it.ratingKey + ":" + it.offerState } + "|" +
                  trouble.joinToString() + "|" + blind
        if (key != downloadsSaid) {
            downloadsSaid = key
            val said = "downloads " + list.size + " from " + servers.size + " server(s)" +
                (if (blind) " (none answered - keeping " + downloading.value.size + ")" else "") +
                list.joinToString("") { " | " + it.title.take(30) + " " + it.offerState + " " +
                                      (it.offerProgress * 100).toInt() + "%" }+
                (if (trouble.isEmpty()) "" else " | trouble: " + trouble.joinToString("; "))
            runCatching { trace(said) }
        }
    }

    suspend fun torrentGet(m: Media): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(postTo(m.srv, "/torrents/get",
                                      JSONObject().put("key", m.ratingKey)))
            if (o.optBoolean("ok")) Pair(true, o.optString("state", "queued"))
            else Pair(false, o.optString("why", "Could not start it"))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            Pair(false, "The server did not answer")
        }
    }

    /**
     * Empty one shelf's round: the hat back to full, and nothing kept half-watched.
     *
     * Asked of the server the shelf belongs to, as a draw is. This is also what takes
     * a shuffle off Continue watching - that row stands for the shelf, so the title on
     * it is only whatever was drawn last.
     */
    suspend fun shelfReset(id: String, srv: Server? = null): Boolean =
        withContext(Dispatchers.IO) {
        try {
            postTo(srv, "/collections/shuffle/reset",
                   JSONObject().put("id", id.removePrefix("coll:")))
            true
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Put a title aside from Continue watching, or bring it back.
     *
     * Marking one episode watched hands that shelf to the next episode of the
     * programme, so a card pressed to be rid of stays there wearing a different name.
     * This is what takes it off.
     */
    suspend fun aside(m: Media, on: Boolean = true): Boolean =
        withContext(Dispatchers.IO) {
        try {
            postTo(m.srv, "/ondeck/aside",
                   JSONObject().put("key", m.ratingKey).put("on", on))
            true
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            false
        }
    }

    suspend fun onDeck(ctx: Context) = fromAll(ctx) { srv ->
        listFrom("/local/library/onDeck", srv)
    }.sortedByDescending { it.lastViewedAt }.take(40)

    suspend fun children(m: Media) =
        try {
            items(json("/local/library/metadata/${m.ratingKey}/children", m.srv)
                .getJSONObject("MediaContainer"), m.srv)
        } catch (e: java.io.FileNotFoundException) {
            android.util.Log.i("Palladium",
                "no children for " + m.ratingKey + " on " + (m.srv?.base ?: base))
            emptyList()
        }

    /**
     * One title, or nothing at all.
     *
     * A key belongs to the library that issued it, and asking the wrong machine for
     * it is a plain "not found" - which arrived here as an exception nobody caught
     * and took the whole app down with it. A title that is not there is an empty
     * screen; it is never a crash.
     */
    suspend fun metadata(m: Media): Media? =
        try {
            items(json("/local/library/metadata/${m.ratingKey}", m.srv)
                .getJSONObject("MediaContainer"), m.srv).firstOrNull()
        } catch (e: java.io.FileNotFoundException) {
            android.util.Log.i("Palladium",
                "no " + m.ratingKey + " on " + (m.srv?.base ?: base))
            null
        }

    /** One title by its key - for following an episode back to its series. */
    /**
     * The same title on another server, by what it is rather than by its number.
     *
     * Every library numbers its own titles, so the machine keeping copies of this
     * one files the same episode under a different key. A viewer handed over to it
     * mid-film knows the programme, the season and the number, which is enough.
     */
    suspend fun keyThere(srv: Server, m: Media): String? = withContext(Dispatchers.IO) {
        val path = if (m.type == "episode")
            "/local/library/find?type=episode&show=" +
                URLEncoder.encode(m.grandparentTitle ?: m.title, "UTF-8") +
                "&season=" + (m.parentIndex ?: 0) + "&episode=" + (m.index ?: 0)
        else
            "/local/library/find?type=movie&title=" +
                URLEncoder.encode(m.title, "UTF-8") + "&year=" + (m.year ?: 0)
        runCatching {
            JSONObject(get(path, srv)).getJSONObject("MediaContainer").optString("key")
        }.getOrNull()?.takeIf { it.isNotEmpty() }
    }

    /**
     * Which titles the machine keeping copies is holding, by this server's keys.
     *
     * Read once a session and refreshed when the shelves are: a dot on a poster is
     * worth having, and worth nothing if it costs a request per card.
     */
    @Volatile private var copies: Set<String> = emptySet()
    @Volatile private var copiesAt: Long = 0L

    suspend fun learnCopies() = withContext(Dispatchers.IO) {
        if (System.currentTimeMillis() - copiesAt < 120_000L) return@withContext
        copiesAt = System.currentTimeMillis()
        runCatching {
            val said = JSONObject(get("/copies")).optJSONArray("keys")
            val out = HashSet<String>()
            for (i in 0 until (said?.length() ?: 0)) out.add(said!!.optString(i))
            copies = out
        }
        Unit
    }

    /**
     * What this server says about itself when it is the machine keeping copies.
     *
     * Empty for an ordinary server. On the cache it is one sentence: which machine it
     * holds copies from, and what of this viewer's it was asked to keep - a shelf
     * with nine films on it and no explanation reads as a fault.
     */
    @Volatile var copyNote: String = ""

    suspend fun learnWhatThisIs() = withContext(Dispatchers.IO) {
        copyNote = runCatching {
            val copy = JSONObject(get("/config")).optJSONObject("copyOf")
                ?: return@runCatching ""
            val kept = listOfNotNull(
                if (copy.optBoolean("deck")) "what you were part-way through" else null,
                if (copy.optBoolean("list")) "your watchlist" else null,
                if (copy.optBoolean("casual")) "the next few of your casual shuffle"
                    else null)
            val what = when (kept.size) {
                0 -> "nothing yet - nobody has asked for anything to be kept for you"
                1 -> kept[0]
                else -> kept.dropLast(1).joinToString(", ") + " and " + kept.last()
            }
            // everything on that machine plays for anybody who may watch there
            val much = if (copy.optInt("films") + copy.optInt("episodes") > 0)
                copy.optInt("films").toString() + " films and " +
                    copy.optInt("episodes") + " episodes, all playable by anyone here"
                else "what has been copied so far"
            "Copies from " + copy.optString("name") + ": " + much +
                ". Kept for you: " + what + "."
        }.getOrDefault("")
        Unit
    }

    /**
     * Whether a server is answering, asked plainly and quickly.
     *
     * A list of servers that says nothing about which of them are up is a list of
     * guesses: the one on the shelf may be off, the cache may be awake, and the only
     * way to know is to knock.
     */
    suspend fun answering(where: String, tok: String = ""): Boolean =
        withContext(Dispatchers.IO) {
            val began = System.currentTimeMillis()
            val said = runCatching {
                val conn = URL(where.trimEnd('/') + auth("/app/version", tok))
                    .openConnection() as HttpURLConnection
                conn.connectTimeout = 2500
                conn.readTimeout = 4000
                conn.setRequestProperty("X-Palladium-App", appName())
                conn.inputStream.use { it.readBytes() }
                true
            }.getOrDefault(false)
            // how long it took to answer, kept: two servers holding the same film
            // are not the same offer, and the near one should be the one that plays
            paces[where] = if (said) System.currentTimeMillis() - began else 9_999
            said
        }

    //: how quickly each server answered last time it was asked, in milliseconds.
    //: A machine in the cupboard answers in three, one across the country in ninety.
    val paces = java.util.concurrent.ConcurrentHashMap<String, Long>()

    /** How quick a server is thought to be; unknown counts as slow but not hopeless. */
    fun paceOf(where: String): Long = paces[where] ?: 400L

    /** Whether the other machine holds this title. */
    fun copiedHere(m: Media): Boolean =
        copies.isNotEmpty() && copies.contains(m.ratingKey)

    /** That title on the machine that keeps copies, ready to play, or nothing. */
    suspend fun sameOnTheCopy(m: Media): Media? {
        val where = listOf(standby, standbyOut).firstOrNull { it.isNotEmpty() }
            ?: return null
        val srv = Server(standbyName.ifBlank { Servers.hostOf(where) }, where, token,
                         mine = false, on = false, copyOf = base)
        val key = keyThere(srv, m) ?: return null
        return runCatching { item(key, srv) }.getOrNull()
    }

    /**
     * The same film on every other machine that answers, asked all at once.
     *
     * Not only the one that keeps copies: a film may sit on any server the app knows,
     * and which of them are switched on changes while the film is playing. Asked
     * again through the evening rather than once at the start, so a machine that
     * comes on at ten joins what is already running.
     */
    suspend fun sameElsewhere(ctx: Context, m: Media,
                              // Where the film is actually coming from, which after
                              // a film has moved machines is not where the app is.
                              // Only the player moves; the shelves stay behind. So
                              // this looked for another machine while standing at the
                              // app's address, was handed the machine the film had
                              // just moved to, and the film was read off one machine
                              // with two of them holding it.
                              from: String = ""): List<Media> = coroutineScope {
        val here = from.trimEnd('/').ifEmpty { base.trimEnd('/') }
        val list = Servers.all(ctx)
        // This machine, by whichever of its addresses it is filed under. Comparing
        // the address being played from against the row's own meant a server filed
        // under its way in from outside did not match the network address in use -
        // so it found itself, called itself a second machine, and the film was moved
        // to the machine it was already playing from.
        val self = Server("", here, "")
        val mine = list.firstOrNull { Servers.sameMachine(it, self) }
        val asked = list.filter {
            it.base.isNotEmpty() && !Servers.sameMachine(it, self) &&
                (mine == null || !Servers.sameMachine(it, mine))
        }
        val got = asked
            .map { row ->
                val srv = Servers.withKey(list, row)
                async(Dispatchers.IO) {
                    runCatching {
                        withTimeoutOrNull(8_000L) { keyThere(srv, m)?.let { item(it, srv) } }
                    }.getOrNull()
                }
            }
            .awaitAll()
            .filterNotNull()
        // What was in the book and what was asked of it. Every explanation for a
        // machine not being found has been ruled out at the far end - it answers in
        // fifteen milliseconds - so what is left is this list at the moment of asking.
        trace("elsewhere: on " + here + " · " + list.size + " known [" +
              list.joinToString("; ") {
                  it.name + "@" + it.base + (if (it.id.isEmpty()) "" else " id=" + it.id)
              } + "] · asked " + asked.size + " · found " + got.size)
        got
    }

    /**
     * The same film on the main server, for a title listed while the copy answered.
     *
     * The copy holds the films but not the keys: every machine files a library under
     * keys of its own, so the one a title was listed under means nothing anywhere
     * else. Handing one machine's key to another is how a film came back as some
     * other film under the right title. So the main server is asked for its own key
     * for this title - by name and year, the way any machine is asked for a film it
     * might have - and what comes back is that machine's copy of it, or nothing.
     *
     * Only for this library's own second machine, and only once the main server is
     * answering again: somebody else's library stays theirs.
     */
    suspend fun atHome(ctx: Context, m: Media): Media? {
        val from = m.srv ?: return null
        if (onStandby || base.isEmpty() || !theCopy(from.base) || theCopy(base)) {
            return null
        }
        val house = Servers.merged(ctx).firstOrNull {
            Servers.hostOf(it.base) == Servers.hostOf(base)
        } ?: Server("", base, token)
        val key = withTimeoutOrNull(4_000L) {
            runCatching { keyThere(house, m) }.getOrNull()
        } ?: return null
        val there = withTimeoutOrNull(6_000L) {
            runCatching { item(key, house) }.getOrNull()
        } ?: return null
        // it has to be playable there: a row the main server cannot serve is worse
        // than the copy that can
        return there.takeIf { it.partKey != null || it.ratingKey.isNotEmpty() }
    }

    suspend fun item(key: String, srv: Server? = null): Media? =
        try {
            items(json("/local/library/metadata/$key", srv)
                .getJSONObject("MediaContainer"), srv).firstOrNull()
        } catch (e: java.io.FileNotFoundException) {
            android.util.Log.i("Palladium",
                "no " + key + " on " + (srv?.base ?: base))
            null
        }

    suspend fun search(ctx: Context, term: String): List<Media> {
        val q = URLEncoder.encode(term, "UTF-8")
        return fromAll(ctx) { srv ->
            val hubs = JSONObject(get("/local/hubs/search?query=$q", srv))
                .getJSONObject("MediaContainer").optJSONArray("Hub") ?: return@fromAll emptyList()
            val out = ArrayList<Media>()
            for (i in 0 until hubs.length()) out.addAll(items(hubs.getJSONObject(i), srv))
            out
        }
    }

    /**
     * Artwork, from whichever server holds the title.
     *
     * [width] is the size it will be drawn at, in pixels. The server resizes and keeps
     * the result, so a poster drawn 280 wide costs 33 kB instead of 66 and decodes in
     * a quarter of the time - which on a Chromecast is the difference between a grid
     * that scrolls and one that stutters.
     */
    fun artUrl(m: Media, width: Int = 0): String? {
        val path = m.thumb
        if (path.isNullOrEmpty()) return null
        val srv = m.srv
        val sized = if (width > 0) path + (if (path.contains("?")) "&" else "?") +
            "w=" + width else path
        return (srv?.base ?: base) + "/local" + auth(sized, srv?.token ?: token)
    }

    /**
     * Mark something watched, or not - a film, an episode, a season, or a series.
     *
     * The server keeps this as progress at the full duration, so the tick, the resume
     * point and Continue watching are one fact rather than three that can disagree.
     */
    suspend fun setWatched(m: Media, watched: Boolean) = withContext(Dispatchers.IO) {
        try {
            val verb = if (watched) "scrobble" else "unscrobble"
            get("/local/:/$verb?key=" + m.ratingKey, m.srv)
            Unit
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) { /* the mark is not worth an error on screen */ }
    }

    /** Tell the server where playback got to, so Continue watching works. */
    suspend fun progress(m: Media, positionMs: Long, durationMs: Long) =
        progressAt(m.srv?.base ?: base, m.srv?.token ?: token, m.ratingKey,
                   positionMs, durationMs)

    /**
     * The same, addressed explicitly.
     *
     * The player is handed a URL rather than a Media, and the film may belong to a
     * friend's server rather than the one on screen, so it carries the address with it.
     */
    suspend fun progressAt(host: String, tok: String, key: String,
                           positionMs: Long, durationMs: Long,
                           state: String = "playing",
                           device: String = "",
                           subtitles: String = "",
                           casual: Boolean = false,
                           /** the shelf it was drawn off, so that shelf keeps the place */
                           shelf: String = ""): Boolean = withContext(Dispatchers.IO) {
        try {
            val who = URLEncoder.encode(device.ifEmpty { "Android" }, "UTF-8")
            // whether subtitles are on matters at the end: an episode watched through
            // with them off is how a series says it does not want them fetched
            val path = auth("/local/:/timeline?ratingKey=$key&time=$positionMs" +
                    "&duration=$durationMs&state=$state&device=$who&client=app" +
                    // a casual playing keeps its place in the shuffle's own notes and
                    // leaves watched state alone: putting something on is not watching
                    (if (casual) "&casual=1" else "") +
                    (if (shelf.isEmpty()) "" else
                         "&shelf=" + URLEncoder.encode(shelf, "UTF-8")) +
                    (if (subtitles.isEmpty()) "" else "&sub=" + subtitles), tok)
            val conn = URL(host + path).openConnection() as HttpURLConnection
            conn.connectTimeout = 6000
            conn.readTimeout = 10000
            // the watch log records which build was watching, and this is the request
            // it records - without the header every line read "app: nothing"
            conn.setRequestProperty("X-Palladium-App", appName())
            val said = conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
            // The server marks a subtitle verified as this very report arrives - the
            // episode has just passed the credits - and says so in the answer. True
            // means the panel on screen has a tick to turn.
            said.contains("subsVerified")
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            false                  // progress is not worth failing playback over
        }
    }

    /**
     * What to hand a Chromecast receiver, which is fussier than the phone it was sent from.
     *
     * The default receiver understands MP4 and WebM; it does not understand Matroska, and
     * most of this library is .mkv. So "the phone can play it" is the wrong test here -
     * anything that is not already a receiver-friendly container goes through the GPU
     * engine, which emits fragmented MP4.
     */
    fun castUrl(m: Media, positionSec: Long = 0, burnIndex: Int? = null): Pair<String, Boolean> {
        val b = m.srv?.base ?: base
        val t = m.srv?.token ?: token
        val container = (m.container ?: "").lowercase()
        val v = (m.videoCodec ?: "").lowercase()
        val audio = (m.audioCodec ?: "").lowercase()
        val receiverOk = container in setOf("mp4", "m4v", "webm") &&
                v in setOf("h264", "avc", "vp8", "vp9") &&
                audio in setOf("aac", "mp3", "vorbis", "opus", "ac3", "eac3")
        if (burnIndex == null && receiverOk && m.partKey != null) {
            return Pair(b + "/local" + auth(m.partKey, t), true)
        }
        val extra = if (burnIndex != null) "&burn=" + burnIndex else ""
        // A receiver gets the playlist rather than the pipe. A Chromecast will not
        // take a fragmented MP4 arriving down one long connection - it cannot seek in
        // it, cannot recover from a stumble, and mostly refuses it outright - whereas
        // HLS is what its own player is built around.
        return Pair(b + auth("/gpu/hls?src=local&key=" + m.ratingKey +
                "&offset=" + positionSec + "&height=0&mi=" + m.mi + extra, t), false)
    }

    /**
     * What to hand ExoPlayer.
     *
     * Android TV decodes far more than a browser does - H.264, HEVC, VP9 and AV1 - so
     * most files play straight from disk with no server work at all. Only what the device
     * genuinely cannot decode (MPEG-4 ASP, DTS audio) goes through the GPU transcoder.
     */
    /**
     * Whether this device decodes HEVC, asked of the device itself and kept.
     *
     * It decides how a film too big for h264 is sent: these boxes take HEVC at 2160
     * lines and h264 at 1080, so without this the server has to halve a 4K picture
     * to reach them - which is the difference between a transcode that looks like
     * the film and one that looks like a copy of it.
     */
    @Volatile private var hevcOk: Boolean? = null

    /**
     * Whether the sound can leave here as DTS, asked of the audio chain itself.
     *
     * Not of the box: a streamer with no DTS decoder of its own still passes the
     * bitstream to a receiver that has one, and Media3 reports what the whole chain
     * can take. Where the answer is yes the track goes through untouched, which is
     * better than the AC-3 we would make out of it in every way.
     */
    @Volatile private var dtsOk: Boolean? = null

    fun takesDts(ctx: Context? = null): Boolean {
        dtsOk?.let { return it }
        val at = ctx ?: return false
        val found = runCatching {
            val caps = androidx.media3.exoplayer.audio.AudioCapabilities
                .getCapabilities(at)
            caps.supportsEncoding(androidx.media3.common.C.ENCODING_DTS) ||
                caps.supportsEncoding(androidx.media3.common.C.ENCODING_DTS_HD)
        }.getOrDefault(false)
        dtsOk = found
        return found
    }

    private fun takesHevc(): Boolean {
        hevcOk?.let { return it }
        val found = runCatching {
            val list = android.media.MediaCodecList(
                android.media.MediaCodecList.REGULAR_CODECS)
            list.codecInfos.any { info ->
                !info.isEncoder && info.supportedTypes.any {
                    it.equals("video/hevc", true)
                }
            }
        }.getOrDefault(false)
        hevcOk = found
        return found
    }

    fun playbackUrl(m: Media, positionSec: Long = 0, burnIndex: Int? = null,
                    audioIndex: Int? = null, height: Int = 0, mbit: Int = 0,
                    audio: String = "passthrough",
                    channels: Int = 0): Pair<String, Boolean> {
        // Read from the machine this title was listed from, and from no other. Two
        // servers hold the same film under different keys of their own, so sending
        // one machine's key to the other asks it for whatever that key means there -
        // which is some other film, or nothing. Coming back to the main server after
        // it has been away has to ask it for its own key first; until it does, the
        // copy answers for what was listed from the copy.
        val b = m.srv?.base ?: base
        val t = m.srv?.token ?: token
        // burning is only ever done on request: it forces an encode of a film that
        // would otherwise have streamed untouched. So does asking for a soundtrack
        // other than the first, which ffmpeg has to pick out of the file.
        // and so does asking for a smaller picture or a fixed number of megabits:
        // the file itself is whatever size it was made
        // Asking for the sound in a particular coding is asking for an encode: it
        // is the answer for a device that cannot decode what the file holds, and
        // playing the file itself is exactly what did not work there.
        // Choosing a soundtrack is not a reason to re-encode a film. Every track is
        // already in the file and the player can pick one out of it; only asking for
        // the sound in a particular coding, or burning a subtitle into the picture,
        // needs the film made again. If the chosen track turns out to be one this
        // device cannot decode, the player asks for it in AAC as it always has.
        val plain = audio == "passthrough"
        val playable = m.canDirectPlay()
        val hasPart = m.partKey != null
        if (plain && burnIndex == null && height == 0 && mbit == 0 &&
            playable && hasPart) {
            return Pair(b + "/local" + auth(m.partKey, t), true)
        }
        // and which of them refused it. A film that could have been played as it is
        // comes back re-encoded and says nothing about why, so this is the only place
        // the answer exists.
        android.util.Log.i("Palladium", "encoding " + m.ratingKey + ": " +
            (if (!plain) "audio=" + audio + " " else "") +
            (if (burnIndex != null) "burn=" + burnIndex + " " else "") +
            (if (height != 0) "height=" + height + " " else "") +
            (if (mbit != 0) "mbit=" + mbit + " " else "") +
            (if (!playable) "codecs=" + m.videoCodec + "/" + m.audioCodec + " " else "") +
            (if (!hasPart) "no part " else "") +
            "(mi=" + m.mi + ")")
        val extra = (if (takesHevc()) "&hevc=1" else "") +
                    (if (dtsOk == true) "&dts=1" else "") +
                    (if (burnIndex != null) "&burn=" + burnIndex else "") +
                    (if (audioIndex != null) "&atrack=" + audioIndex else "") +
                    (if (mbit > 0) "&mbit=" + mbit else "") +
                    (if (channels > 0) "&ch=" + channels else "")
        return Pair(b + auth("/gpu/stream?src=local&key=" + m.ratingKey +
                "&offset=" + positionSec + "&height=" + height +
                "&mi=" + m.mi + "&audio=" + audio +
                "&device=" + device + extra, t), false)
    }

    /**
     * A text subtitle track, extracted to WebVTT by the server.
     *
     * The offset matters: a live encode starts its clock at zero, so its subtitles have
     * to be extracted from the same point. A direct-played file keeps the original
     * timeline, so its cues are asked for from the beginning.
     */
    fun subsUrl(m: Media, streamIndex: Int, offsetSec: Long, shift: Float = 0f): String {
        val b = m.srv?.base ?: base
        val t = m.srv?.token ?: token
        // a correction by hand for a subtitle cut to another release; the server moves
        // every cue by it, whether the track came out of the container or from a file
        // beside it
        val moved = if (shift == 0f) ""
                    else "&shift=" + String.format(java.util.Locale.US, "%.2f", shift)
        return b + auth("/gpu/subs?src=local&key=" + m.ratingKey +
                "&index=" + streamIndex + "&offset=" + offsetSec +
                "&mi=" + m.mi + moved, t)
    }

    /** Where an encoded stream will start, in film seconds, and whether its picture
     *  goes through as it is; null when the server cannot say. Blocking: asked off the
     *  main thread, with a short patience. */
    fun encodeStart(streamUrl: String): Pair<Double, Boolean>? = try {
        val conn = java.net.URL(streamUrl.replace("/gpu/stream?", "/gpu/begins?"))
            .openConnection() as java.net.HttpURLConnection
        conn.connectTimeout = 1500
        conn.readTimeout = 1500
        conn.setRequestProperty("X-Palladium-App", appName())
        try {
            val o = JSONObject(conn.inputStream.bufferedReader().readText())
            Pair(o.optDouble("at", -1.0), o.optBoolean("copy", false))
        } finally {
            conn.disconnect()
        }
    } catch (e: Exception) {
        null
    }

    /**
     * How far this title's subtitles have been moved, in seconds.
     *
     * The server keeps it, not the device: a file cut for another release is out by
     * the same amount for everybody who plays it, so whoever notices first is the
     * last who has to correct it.
     */
    /** Who put the plain correction there: "sync" for the film, "hand" for a person. */
    suspend fun shiftBy(key: String, sub: String = ""): String =
        withContext(Dispatchers.IO) {
            try {
                JSONObject(get("/settings?key=" +
                               java.net.URLEncoder.encode(key, "UTF-8") +
                               "&sub=" + java.net.URLEncoder.encode(sub, "UTF-8") +
                               "&device=" + device)).optString("shiftBy")
            } catch (e: Exception) { "" }
        }

    suspend fun subShift(key: String, sub: String = ""): Float =
        withContext(Dispatchers.IO) {
            try {
                JSONObject(get("/settings?key=" +
                               java.net.URLEncoder.encode(key, "UTF-8") +
                               "&sub=" + java.net.URLEncoder.encode(sub, "UTF-8") +
                               "&device=" + device))
                    .optDouble("subShift", 0.0).toFloat()
            } catch (e: Exception) { 0f }
        }

    /**
     * Write it down, against this subtitle of this title.
     *
     * Correcting a subtitle is also vouching for it - somebody has watched it against
     * the picture and put it right - so a file is named here and the server writes the
     * verified mark at the same time.
     */
    suspend fun setSubShift(key: String, seconds: Float, sub: String = "",
                            name: String = "", index: Int? = null) =
        withContext(Dispatchers.IO) {
            runCatching {
                val one = JSONObject().put("key", key).put("seconds", seconds)
                    .put("sub", sub)
                if (name.isNotEmpty()) one.put("name", name)
                if (index != null) one.put("index", index)
                post("/settings", JSONObject().put("subShift", one))
            }
            Unit
        }

    /**
     * A subtitle file, parsed into cues the player can time itself.
     *
     * WebVTT is what the server always sends, whether the track came out of the
     * container or from a file beside it, so one small parser covers both. Times are
     * milliseconds from the start of the stream, which is what the player counts in.
     */
    /** Whether the last subtitle handed over was only the first part of one. */
    @Volatile var cuesPartial: Boolean = false

    suspend fun cues(url: String): List<Triple<Long, Long, String>> =
        withContext(Dispatchers.IO) {
            val out = ArrayList<Triple<Long, Long, String>>()
            try {
                val conn = URL(url).openConnection() as HttpURLConnection
                conn.connectTimeout = 8000
                // The first time a track is asked for, the server reads the whole film
                // to lift it out - minutes, on a large file being streamed off the same
                // disk. Every later request is instant, because it is kept.
                conn.readTimeout = 900000
                conn.setRequestProperty("X-Palladium-App", appName())
                val text = conn.inputStream.use {
                    it.readBytes().toString(Charsets.UTF_8)
                }
                // the server hands over the first stretch while it reads the rest out
                // of the film, and says so here
                cuesPartial = conn.getHeaderField("X-Palladium-Subs") == "partial"
                // "01:02:03.400" and "02:03.400" are both WebVTT: ffmpeg leaves the
                // hour out below an hour, which is what a track lifted out of a film
                // looks like. Demanding it matched nothing in those, so an embedded
                // subtitle came back as no cues at all and drew nothing.
                val one = "(?:(\\d+):)?(\\d{1,2}):(\\d\\d[.,]\\d+)"
                val stamp = Regex(one + "\\s*-->\\s*" + one)
                fun ms(h: String, mm: String, ss: String): Long =
                    (if (h.isEmpty()) 0L else h.toLong() * 3600_000) +
                        mm.toLong() * 60_000 +
                        (ss.replace(",", ".").toDouble() * 1000).toLong()
                for (block in text.split(Regex("\\r?\\n\\r?\\n"))) {
                    val m = stamp.find(block) ?: continue
                    val from = ms(m.groupValues[1], m.groupValues[2], m.groupValues[3])
                    val to = ms(m.groupValues[4], m.groupValues[5], m.groupValues[6])
                    val said = block.substringAfter(m.value).trim()
                        .replace(Regex("<[^>]*>"), "")
                    if (said.isNotEmpty()) out.add(Triple(from, to, said))
                }
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) { /* no subtitles is an answer */ }
            out
        }

    /**
     * How subtitles should be drawn, as the server has them.
     *
     * A title may carry its own; passing its key asks for that one, with the server's
     * default underneath whatever it does not override.
     */
    data class SubLook(val size: Float, val position: Float,
                       val colour: String, val background: String,
                       val override: Boolean = false,
                       /** "screen" counts from the panel, "picture" from the film */
                       val base: String = "picture",
                       /** false when kept on the device or the server could not be asked */
                       val answered: Boolean = true)

    /**
     * The look this title was drawn with last time, kept on the device.
     *
     * Asking the server takes a moment, and until it answers the subtitles are drawn
     * with the file's own styling and Media3's default size - which is what made them
     * appear too large and then jump.
     */
    fun rememberLook(ctx: Context, key: String, look: SubLook) {
        prefs(ctx).edit().putString("look:" + device + ":" + key,
            listOf(look.size.toString(), look.position.toString(), look.colour,
                   look.background, look.base).joinToString("|")).apply()
    }

    fun lastLook(ctx: Context, key: String): SubLook? {
        val said = prefs(ctx).getString("look:" + device + ":" + key, null) ?: return null
        val bits = said.split("|")
        if (bits.size < 5) return null
        return try {
            SubLook(bits[0].toFloat(), bits[1].toFloat(), bits[2], bits[3],
                    false, bits[4], answered = false)
        } catch (e: NumberFormatException) { null }
    }

    /** "tv" on a television, "phone" everywhere else; the server keeps a look for each. */
    @Volatile var device: String = "phone"

    fun learnDevice(ctx: Context) {
        val ui = ctx.getSystemService(Context.UI_MODE_SERVICE) as android.app.UiModeManager
        device = if (ui.currentModeType ==
                     android.content.res.Configuration.UI_MODE_TYPE_TELEVISION) "tv"
                 else "phone"
    }

    suspend fun subtitleLook(key: String = "", forDevice: String = device): SubLook =
        withContext(Dispatchers.IO) {
        try {
            val q = "?device=" + forDevice +
                (if (key.isEmpty()) "" else "&key=" + URLEncoder.encode(key, "UTF-8"))
            val all = JSONObject(get("/settings$q"))
            val o = all.getJSONObject("subtitles")
            SubLook(o.optDouble("size", 1.0).toFloat(),
                    o.optDouble("position", 0.08).toFloat(),
                    o.optString("colour", "white"),
                    o.optString("background", "shadow"),
                    all.optBoolean("override"),
                    o.optString("base", "picture"))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            SubLook(1f, 0.08f, "white", "shadow", answered = false)
        }
    }

    /** The colour the server draws with, shared by every screen that asks for it. */
    /** One colour on offer: what it is, and what to call it. */
    data class Accent(val code: String, val name: String)

    /** The colours this server offers, and which one is on. */
    suspend fun accents(): Triple<List<Accent>, String, String> =
        withContext(Dispatchers.IO) {
            try {
                val o = JSONObject(get("/settings"))
                val list = o.optJSONArray("accents")
                val out = ArrayList<Accent>()
                for (i in 0 until (list?.length() ?: 0)) {
                    val one = list!!.getJSONObject(i)
                    out.add(Accent(one.optString("code"), one.optString("name")))
                }
                Triple(out, o.optString("accent", ""), o.optString("accentMine", ""))
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) { Triple(emptyList(), "", "") }
        }

    /** Choose a colour for this viewer; an empty code gives the server's back. */
    suspend fun setAccent(code: String): String = withContext(Dispatchers.IO) {
        try {
            JSONObject(postTo(null, "/settings", JSONObject().put("accent", code)))
                .optString("accent", "")
        } catch (stopped: kotlinx.coroutines.CancellationException) { throw stopped }
        catch (e: Exception) { "" }
    }

    suspend fun accent(): String = withContext(Dispatchers.IO) {
        try { JSONObject(get("/settings")).optString("accent", "") }
        catch (stopped: kotlinx.coroutines.CancellationException) { throw stopped }
        catch (e: Exception) { "" }
    }

    /**
     * Which language this viewer wants subtitles in.
     *
     * Kept by the server alongside their size and colour, so it is the same answer on
     * the television and on the phone, and a guest's choice is their own.
     */
    /** How far the server has got writing a subtitle down from a soundtrack. */
    data class Making(val at: Float, val what: String, val on: Boolean,
                      val ok: Boolean?, val can: Boolean, val file: String = "",
                      //: what is being written at this moment, and what is waiting
                      val title: String = "", val key: String = "",
                      val queued: List<String> = emptyList())

    /** Ask the server to listen to a film and write the subtitle itself. */
    suspend fun makeSubtitles(m: Media, language: String): String =
        withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().put("key", m.ratingKey).put("mi", m.mi)
                .put("language", language)
            JSONObject(postTo(m.srv, "/subs/make", body)).optString("error", "")
        } catch (e: Exception) { "The server could not be reached" }
    }

    /** Stop what is being written down now. */
    suspend fun stopMaking(m: Media? = null): Boolean = withContext(Dispatchers.IO) {
        try {
            postTo(m?.srv, "/subs/stop", JSONObject())
            true
        } catch (e: Exception) { false }
    }

    suspend fun makingSubtitles(m: Media? = null): Making = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(get("/subs/making", m?.srv))
            val on = o.optJSONObject("on")
            val done = o.optJSONArray("done")
            val last = if (done != null && done.length() > 0)
                done.getJSONObject(done.length() - 1) else null
            Making(at = (on?.optDouble("at", 0.0) ?: 0.0).toFloat(),
                   what = on?.optString("what") ?: (last?.optString("what") ?: ""),
                   on = on != null,
                   ok = if (on != null || last == null) null else last.optBoolean("ok"),
                   can = o.optBoolean("can", true),
                   // the running job's file as soon as there is one: it grows while
                   // the film is heard, and can be watched before it is finished
                   file = on?.optString("file")?.takeIf { it.isNotEmpty() }
                          ?: (last?.optString("file") ?: ""),
                   title = on?.optString("title") ?: "",
                   key = on?.optString("key") ?: "",
                   queued = (o.optJSONArray("queued") ?: JSONArray()).let { rows ->
                       (0 until rows.length()).map {
                           rows.getJSONObject(it).optString("file")
                       }
                   })
        } catch (e: Exception) { Making(0f, "", false, null, true) }
    }

    suspend fun subtitleLanguage(): String = withContext(Dispatchers.IO) {
        try { JSONObject(get("/settings")).optString("language", "en").ifEmpty { "en" } }
        catch (e: Exception) { "en" }
    }

    suspend fun setSubtitleLanguage(code: String) = withContext(Dispatchers.IO) {
        try {
            postTo(null, "/settings", JSONObject().put("language", code))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) { "" }
        Unit
    }

    /** The decades this library holds, newest first, with how many are in each. */
    suspend fun decades(kind: String): List<Pair<String, Int>> = withContext(Dispatchers.IO) {
        try {
            val arr = JSONObject(get("/local/library/decades?type=" + kind))
                .getJSONObject("MediaContainer").optJSONArray("Directory") ?: JSONArray()
            (0 until arr.length()).map {
                val d = arr.getJSONObject(it)
                Pair(d.optInt("decade").toString(), d.optInt("count"))
            }.filter { it.first != "0" }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptyList()
        }
    }

    /** The genres this library holds, most-stocked first, with how many are in each. */
    suspend fun genres(kind: String): List<Pair<String, Int>> = withContext(Dispatchers.IO) {
        try {
            val arr = JSONObject(get("/local/library/genres?type=" + kind))
                .getJSONObject("MediaContainer").optJSONArray("Directory") ?: JSONArray()
            (0 until arr.length()).map {
                val g = arr.getJSONObject(it)
                Pair(g.optString("title"), g.optInt("count"))
            }.filter { it.first.isNotEmpty() }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptyList()
        }
    }

    /**
     * The server's own ceilings: one for the main server, one for everybody outside it.
     *
     * The owner's to set, and refused from anywhere else - which is why the app only
     * offers them when it is talking to its own server.
     */
    data class Ceilings(val homeHeight: Int, val homeMbit: Int,
                        val awayHeight: Int, val awayMbit: Int)

    private fun readCeilings(o: JSONObject): Ceilings {
        val q = o.optJSONObject("quality") ?: JSONObject()
        val home = q.optJSONObject("home") ?: JSONObject()
        val away = q.optJSONObject("away") ?: JSONObject()
        return Ceilings(home.optInt("height"), home.optInt("mbit"),
                        away.optInt("height"), away.optInt("mbit"))
    }

    suspend fun serverQuality(): Ceilings = withContext(Dispatchers.IO) {
        try { readCeilings(JSONObject(get("/settings"))) }
        catch (e: Exception) { Ceilings(0, 0, 0, 0) }
    }

    suspend fun setServerQuality(where: String, height: Int, mbit: Int): Ceilings =
        withContext(Dispatchers.IO) {
            try {
                val body = JSONObject().put("quality", JSONObject().put(where,
                    JSONObject().put("height", height).put("mbit", mbit)))
                readCeilings(JSONObject(post("/settings", body)))
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) { serverQuality() }
        }

    /** Picture size and megabits this viewer has asked for; 0 means no preference. */
    data class OwnQuality(val height: Int, val mbit: Int)

    suspend fun myQuality(): OwnQuality = withContext(Dispatchers.IO) {
        try {
            val one = JSONObject(get("/settings")).getJSONObject("mine")
            OwnQuality(one.optInt("height"), one.optInt("mbit"))
        } catch (e: Exception) { OwnQuality(0, 0) }
    }

    /**
     * Set it, for this viewer on every screen.
     *
     * Kept by the server with the rest of their settings rather than on the device,
     * so the answer is the same on the television and on the telephone.
     */
    suspend fun setMyQuality(height: Int, mbit: Int): OwnQuality =
        withContext(Dispatchers.IO) {
            try {
                val said = JSONObject(post("/settings", JSONObject().put("mine",
                    JSONObject().put("height", height).put("mbit", mbit))))
                val one = said.getJSONObject("mine")
                OwnQuality(one.optInt("height"), one.optInt("mbit"))
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) { OwnQuality(height, mbit) }
        }

    /**
     * The ceiling that actually applies here: the lower of the server's and this
     * viewer's own, as (height, megabits). Nought in either place means no limit.
     */
    suspend fun effectiveCeiling(): Pair<Int, Int> = withContext(Dispatchers.IO) {
        try {
            val s = JSONObject(get("/settings"))
            val there = s.getJSONObject("quality")
                .getJSONObject(s.optString("here", "away"))
            val mine = s.optJSONObject("mine") ?: JSONObject()
            fun lower(a: Int, b: Int) = listOf(a, b).filter { it > 0 }.minOrNull() ?: 0
            Pair(lower(there.optInt("height"), mine.optInt("height")),
                 lower(there.optInt("mbit"), mine.optInt("mbit")))
        } catch (e: Exception) { Pair(0, 0) }
    }

    /**
     * What the server allows from where this device is watching, said in words.
     *
     * Empty when there is no ceiling. The server keeps two - one for the main server, one
     * for outside - and tells each caller which side of the door it is on.
     */
    suspend fun qualityCeiling(): String = withContext(Dispatchers.IO) {
        try {
            val s = JSONObject(get("/settings"))
            val cap = s.getJSONObject("quality")
                .getJSONObject(s.optString("here", "away"))
            val h = cap.optInt("height")
            val m = cap.optInt("mbit")
            if (h == 0 && m == 0) "" else
                "Whatever you choose, this server will send you at most " +
                (if (h > 0) h.toString() + "p" else "the original size") +
                (if (m > 0) " and " + m + " Mbit/s" else "") +
                " from where you are watching."
        } catch (e: Exception) { "" }
    }

    /**
     * A line about what this client is actually doing, straight into the server's log.
     *
     * Not the noticeboard: this is for reading at the machine when something on a
     * television behaves in a way nobody at the keyboard can reproduce.
     */
    suspend fun trace(text: String) = withContext(Dispatchers.IO) {
        // To whichever machine will take it. The one thing worth knowing is what the
        // player did when a server went away - and that is the one moment its own log
        // was being posted to the server that had gone, so the whole account of it
        // was lost. Sent to the main server, and to the machine keeping copies if the main server
        // will not have it.
        val took = runCatching { post("/trace", JSONObject().put("t", text)) }.isSuccess
        if (!took) {
            val other = (standby.ifEmpty { standbyOut }).trimEnd('/')
            if (other.isNotEmpty()) {
                runCatching {
                    postTo(Server(standbyName, other, token), "/trace",
                           JSONObject().put("t", text))
                }
            }
        }
        Unit
    }

    //: Whether this viewer wants a film read off several machines, and whether it
    //: may move to another when the one serving it stops. Both on unless somebody has
    //: said otherwise; asked once a playing rather than once a second.
    @Volatile var maySplit = true
    @Volatile var mayMove = true
    @Volatile private var waysAt = 0L

    /** Ask the server how this viewer wants a film fetched. */
    suspend fun learnTheWays() = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        if (now - waysAt < 60_000) return@withContext
        waysAt = now
        runCatching {
            val said = JSONObject(get("/settings"))
            maySplit = said.optBoolean("splitPlay", true)
            mayMove = said.optBoolean("failover", true)
        }
        Unit
    }

    /** How long the next episode waits before starting itself: nought to five. */
    suspend fun nextDelay(): Int = withContext(Dispatchers.IO) {
        try { JSONObject(get("/settings")).optInt("nextDelay", 5) }
        catch (e: Exception) { 5 }
    }

    suspend fun setNextDelay(seconds: Int): Int = withContext(Dispatchers.IO) {
        try {
            JSONObject(post("/settings", JSONObject().put("nextDelay", seconds)))
                .optInt("nextDelay", seconds)
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) { seconds }
    }

    /** Whether an episode should roll into the next one. */
    suspend fun autoNext(): Boolean = withContext(Dispatchers.IO) {
        try { JSONObject(get("/settings")).optBoolean("autoNext", true) }
        catch (e: Exception) { true }
    }

    suspend fun setAutoNext(on: Boolean): Boolean = withContext(Dispatchers.IO) {
        try {
            JSONObject(post("/settings", JSONObject().put("autoNext", on)))
                .optBoolean("autoNext", on)
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) { on }
    }

    /**
     * The episode after this one: the next number in the season, or the first of the
     * season after it. Null at the end of the last season, which is where it should
     * stop rather than looping.
     */
    suspend fun nextEpisode(showKey: String, season: Int, number: Int): Media? =
        withContext(Dispatchers.IO) {
            try {
                val seasons = items(json("/local/library/metadata/$showKey/children")
                    .getJSONObject("MediaContainer"), null)
                val here = seasons.firstOrNull { it.index == season } ?: return@withContext null
                val eps = items(json("/local/library/metadata/${here.ratingKey}/children")
                    .getJSONObject("MediaContainer"), null)
                eps.firstOrNull { (it.index ?: -1) > number }
                    ?: run {
                        val after = seasons.filter { (it.index ?: -1) > season }
                            .minByOrNull { it.index ?: 0 } ?: return@withContext null
                        items(json("/local/library/metadata/${after.ratingKey}/children")
                            .getJSONObject("MediaContainer"), null).firstOrNull()
                    }
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped          // the screen closed: not a failure
            } catch (e: Exception) {
                null
            }
        }

    /**
     * Put how subtitles are drawn to another server, before anything is asked of it.
     *
     * An evening that moves to the machine keeping copies met that machine's own
     * defaults: the size, the colour and the background all changed at the moment the
     * film did, and its menus opened on values nobody had chosen. Two servers, one
     * viewer, one way of drawing.
     */
    suspend fun pushSubtitleLook(to: Server?, forDevice: String = device) {
        if (to == null) return
        withContext(Dispatchers.IO) {
            runCatching {
                val look = subtitleLook()
                val body = JSONObject()
                    .put("device", forDevice)
                    .put("subtitles", JSONObject()
                        .put("size", look.size.toDouble())
                        .put("position", look.position.toDouble())
                        .put("colour", look.colour)
                        .put("background", look.background)
                        .put("base", look.base))
                postTo(to, "/settings", body)
            }
        }
    }

    /** Store a look - against one title if a key is given, otherwise as the default. */
    suspend fun saveSubtitleLook(key: String, look: SubLook,
                                 forDevice: String = device): SubLook =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("device", forDevice)
                .put("subtitles", JSONObject()
                    .put("size", look.size.toDouble())
                    .put("position", look.position.toDouble())
                    .put("colour", look.colour)
                    .put("background", look.background)
                    .put("base", look.base))
            if (key.isNotEmpty()) body.put("key", key)
            try {
                val o = JSONObject(post("/settings", body))
                val sub = o.getJSONObject("subtitles")
                SubLook(sub.optDouble("size", 1.0).toFloat(),
                        sub.optDouble("position", 0.08).toFloat(),
                        sub.optString("colour", "white"),
                        sub.optString("background", "shadow"),
                        o.optBoolean("override"),
                        sub.optString("base", "picture"))
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped          // the screen closed: not a failure
            } catch (e: Exception) {
                look                      // the server refused; keep what was on screen
            }
        }

    /** Forget one title's exceptions, so it follows the default again. */
    suspend fun resetSubtitleLook(key: String, forDevice: String = device): SubLook =
        withContext(Dispatchers.IO) {
            try {
                post("/settings", JSONObject().put("key", key)
                    .put("device", forDevice).put("reset", true))
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped          // the screen closed: not a failure
            } catch (e: Exception) { /* nothing to undo */ }
            subtitleLook(key, forDevice)
        }

    /* ---------------- subtitles from OpenSubtitles ---------------- */

    data class SubCandidate(val id: Long, val name: String, val language: String,
                            val downloads: Int, val fromHash: Boolean,
                            // of the release an episode of this series was proved on
                            val confirmed: Boolean = false,
                            // named after the video file itself, which is the strongest
                            // evidence there is that the timing will fit
                            val sameName: Boolean = false,
                            // the hash says this file and the name says another
                            // release: shown, but not believed
                            val hashOdd: Boolean = false)

    /**
     * What is on offer for one title.
     *
     * The server does the searching: it holds the API key, and it can hash the file -
     * which is what makes the timing right, since a subtitle matched by title alone was
     * usually cut for a different release.
     */
    suspend fun findSubtitles(m: Media, language: String):
        Pair<List<SubCandidate>, String> = withContext(Dispatchers.IO) {
        try {
            val o = JSONObject(get("/subs/find?key=" + m.ratingKey + "&lang=" + language,
                                   m.srv))
            val arr = o.optJSONArray("results") ?: JSONArray()
            val out = (0 until arr.length()).map {
                val r = arr.getJSONObject(it)
                SubCandidate(r.optLong("id"), r.optString("name"),
                             r.optString("language"), r.optInt("downloads"),
                             r.optBoolean("fromHash"), r.optBoolean("confirmed"),
                             r.optBoolean("sameName"), r.optBoolean("hashOdd"))
            }
            Pair(out, o.optString("error"))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            Pair(emptyList(), e.message ?: "could not reach the server")
        }
    }

    /**
     * Vouch for a subtitle, or take the mark back.
     *
     * A file is known by the release it came from, a track inside the video by its
     * stream number - the server keeps one verified subtitle per language either way.
     * Passing neither unmarks whatever was marked for that language.
     */
    suspend fun verifySubtitle(m: Media, track: SubTrack, on: Boolean): Boolean =
        withContext(Dispatchers.IO) {
            try {
                val body = JSONObject().put("key", m.ratingKey)
                    .put("language", if (track.label.length in 2..5) track.label else "en")
                if (on && track.external) body.put("name", track.label)
                if (on && !track.external) body.put("index", track.index)
                if (!on) body.put("name", "")
                postTo(m.srv, "/subs/verify", body)
                true
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) {
                // a refusal or a server that cannot be reached: the tick did nothing,
                // and the person pressing it is owed that much
                false
            }
        }

    /**
     * Remember which file of a title was chosen, so every screen opens on it.
     *
     * Nothing is stored for the best copy: that is the default, and storing it would
     * outlive the day a better rip is added.
     */
    suspend fun pickCopy(m: Media, copy: Copy) = withContext(Dispatchers.IO) {
        try {
            postTo(m.srv, "/copy/pick",
                   JSONObject().put("key", m.ratingKey)
                       .put("file", if (copy.mi == 0) "" else copy.path))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) { "" }
        Unit
    }

    /** Remember which subtitle was chosen for a title, so it leads the list next time. */
    suspend fun pickSubtitle(m: Media, name: String) = withContext(Dispatchers.IO) {
        try {
            postTo(m.srv, "/subs/pick",
                   JSONObject().put("key", m.ratingKey).put("name", name))
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) { "" }
        Unit
    }

    /** Take one. Returns an error, or null when it worked. */
    suspend fun getSubtitle(m: Media, id: Long, language: String, release: String):
        String? = withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().put("key", m.ratingKey).put("id", id)
                .put("language", language).put("release", release)
            val o = JSONObject(postTo(m.srv, "/subs/get", body))
            if (o.optBoolean("ok")) null else o.optString("error", "it did not work")
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            e.message ?: "could not reach the server"
        }
    }

    /**
     * Fetch whatever this series has settled on, for one episode.
     *
     * Called before the episode is reached rather than as it starts: a subtitle that
     * arrives thirty seconds into the episode is a subtitle nobody wanted.
     */
    suspend fun autoSubtitle(m: Media, after: String = ""): Boolean =
        withContext(Dispatchers.IO) {
        try {
            JSONObject(postTo(m.srv, "/subs/auto",
                              JSONObject().put("key", m.ratingKey)
                                  .put("after", after))).optBoolean("ok")
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            false
        }
    }

    /** POST to a particular server rather than whichever one is open. */
    private fun postTo(srv: Server?, path: String, body: JSONObject): String {
        val b = srv?.base ?: base
        val t = srv?.token ?: token
        val conn = URL(b + auth(path, t)).openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.connectTimeout = 8000
        conn.readTimeout = 90000                 // a search and a download, in one call
        conn.setRequestProperty("Content-Type", "application/json")
        conn.setRequestProperty("X-Palladium-App", appName())
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        conn.inputStream.use { return it.readBytes().toString(Charsets.UTF_8) }
    }

    /* ---------------- invitations ----------------
       Only the owner of a server may list or create these; from anywhere else the
       server answers 403 and the People screen stays hidden. */

    data class Invite(val token: String, val name: String, val email: String,
                      val link: String, val lastSeen: Long, val expires: Long,
                      /** what their app last called itself: "android 0.11.5 phone" */
                      val app: String = "")

    suspend fun invites(): List<Invite>? = withContext(Dispatchers.IO) {
        try {
            val arr = JSONObject(get("/invites")).optJSONArray("people") ?: JSONArray()
            (0 until arr.length()).map {
                val o = arr.getJSONObject(it)
                Invite(o.optString("token"), o.optString("name"), o.optString("email"),
                       o.optString("link"), o.optLong("lastSeen"), o.optLong("expires"),
                       o.optString("app"))
            }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            null                     // not our server, or it is not answering
        }
    }

    private fun post(path: String, body: JSONObject): String {
        val conn = URL(base + auth(path, token)).openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.connectTimeout = 8000
        conn.readTimeout = 15000
        conn.setRequestProperty("Content-Type", "application/json")
        conn.setRequestProperty("X-Palladium-App", appName())
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        conn.inputStream.use { return it.readBytes().toString(Charsets.UTF_8) }
    }

    suspend fun createInvite(name: String, email: String = "", days: Int = 0): Invite? =
        withContext(Dispatchers.IO) {
            try {
                val o = JSONObject(post("/invites", JSONObject()
                    .put("name", name).put("email", email).put("days", days)))
                Invite(o.optString("token"), o.optString("name"), o.optString("email"),
                       o.optString("link"), 0, o.optLong("expires"))
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped          // the screen closed: not a failure
            } catch (e: Exception) {
                null
            }
        }

    /**
     * Send a fault or a request.
     *
     * It goes to the development machine first - that is where the app is built and
     * where anyone would look for reports - using whatever token this device holds for
     * it. Failing that it goes to the server currently open, so a report is never lost
     * merely because home is unreachable.
     */
    /** One release, and what it added, in the words a viewer would use. */
    data class Release(val version: String, val when_: String, val title: String,
                       val items: List<String>)

    /**
     * What has been added, newest first.
     *
     * The same list the web page shows, read from the server rather than built into
     * the app - so an app a version behind still says what the server can do.
     */
    suspend fun changes(): List<Release> = withContext(Dispatchers.IO) {
        try {
            val arr = JSONObject(get("/changes")).optJSONArray("changes") ?: JSONArray()
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                val lines = o.optJSONArray("items") ?: JSONArray()
                Release(o.optString("version"), o.optString("when"),
                        o.optString("title"),
                        (0 until lines.length()).map { lines.getString(it) })
            }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptyList()
        }
    }

    /** One thing somebody reported: a fault the machinery noticed, or a sentence. */
    data class Report(val id: String, val who: String, val kind: String,
                      val text: String, val when_: Long, val app: String,
                      val auto: Boolean,
                      /** ticked off: known, understood and dealt with */
                      val done: Boolean = false,
                      /** what caused it, and what was changed - both may be empty */
                      val reason: String = "", val solution: String = "",
                      /** taken back by whoever wrote it */
                      val withdrawn: Boolean = false)

    //: the name this server knows this viewer by, which is what its reports are
    //: written under: "you" at home, the name on the invitation elsewhere
    @Volatile var whoAmI: String = ""

    /**
     * What has been reported to this server, newest first.
     *
     * The same list the owner sees on the settings page, minus the rows they have
     * hidden - the server decides that, and a guest is simply not sent them.
     */
    suspend fun reports(): List<Report> = withContext(Dispatchers.IO) {
        try {
            val said = JSONObject(get("/feedback"))
            // who the server takes this caller to be, so a report of one's own can be
            // told from everybody else's
            whoAmI = if (said.optBoolean("owner")) "you"
                     else runCatching {
                         JSONObject(get("/config")).optString("name")
                     }.getOrDefault("")
            val arr = said.optJSONArray("reports") ?: JSONArray()
            (0 until arr.length()).map {
                val r = arr.getJSONObject(it)
                Report(r.optString("id"), r.optString("who", "someone"),
                       r.optString("kind", "problem"), r.optString("text"),
                       r.optLong("when"), r.optString("app"),
                       r.optString("source") == "auto",
                       r.optBoolean("done"), r.optString("reason"),
                       r.optString("solution"), r.optBoolean("withdrawn"))
            }
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            emptyList()
        }
    }

    /**
     * Tick a report off, or reopen it.
     *
     * The server allows this from the main server only; from anywhere else it answers 403
     * and the list comes back unchanged, which is the honest outcome.
     */
    /**
     * Take back something you wrote.
     *
     * Anybody may withdraw their own report and nobody else's; the server checks the
     * name against the one it wrote down. It stays on the board marked withdrawn -
     * a request somebody thought better of is still worth not asking twice.
     */
    suspend fun cancelReport(id: String): Boolean = withContext(Dispatchers.IO) {
        try {
            val said = JSONObject(post("/feedback/cancel", JSONObject().put("id", id)))
            !said.has("error")
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped
        } catch (e: Exception) {
            false
        }
    }

    suspend fun settleReport(id: String, done: Boolean): Boolean =
        withContext(Dispatchers.IO) {
            try {
                val body = JSONObject().put("id", id).put("done", done)
                post("/feedback/fix", body)
                true
            } catch (stopped: kotlinx.coroutines.CancellationException) {
                throw stopped
            } catch (e: Exception) {
                false
            }
        }

    suspend fun report(ctx: Context, kind: String, text: String): Boolean =
        withContext(Dispatchers.IO) {
            val home = BuildConfig.UPDATE_HOME
            val known = Servers.all(ctx).firstOrNull { it.base == home }
            val tries = listOfNotNull(
                if (home.isNotEmpty()) Pair(home, known?.token ?: "") else null,
                Pair(base, token),
            ).distinctBy { it.first }
            val body = JSONObject()
                .put("kind", kind).put("text", text)
                .put("app", "android " + BuildConfig.VERSION_NAME + " \u00b7 " +
                        android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL +
                        " \u00b7 " + (if (device == "tv") "television" else "phone"))
            for ((host, tok) in tries) {
                try {
                    val conn = URL(host + auth("/feedback", tok))
                        .openConnection() as HttpURLConnection
                    conn.requestMethod = "POST"
                    conn.doOutput = true
                    conn.connectTimeout = 6000
                    conn.readTimeout = 10000
                    conn.setRequestProperty("Content-Type", "application/json")
                    conn.outputStream.use { it.write(body.toString().toByteArray()) }
                    conn.inputStream.use { it.readBytes() }
                    return@withContext true
                } catch (stopped: kotlinx.coroutines.CancellationException) {
                    throw stopped          // the screen closed: not a failure
                } catch (e: Exception) {
                    continue
                }
            }
            false
        }

    suspend fun revokeInvite(tok: String): Boolean = withContext(Dispatchers.IO) {
        try {
            post("/invites/revoke", JSONObject().put("token", tok)); true
        } catch (stopped: kotlinx.coroutines.CancellationException) {
            throw stopped          // the screen closed: not a failure
        } catch (e: Exception) {
            false
        }
    }
}
