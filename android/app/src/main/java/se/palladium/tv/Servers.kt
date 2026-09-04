package se.palladium.tv

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Every Palladium this app knows about.
 *
 * There is your own, on the home network, and there are friends' - each reached by the
 * link they sent, which is an address and a token in one string:
 *
 *     http://203.0.113.7:8765/s/<the token they were given>
 *
 * A server is either shown alongside the others in Films and TV, or kept but hidden.
 * The token is what proves us to a server that is not ours; on our own network there is
 * none, because the server recognises the address instead.
 */
data class Server(
    val name: String,
    val base: String,            // http://host:port, no trailing slash
    val token: String = "",      // empty for our own server
    val mine: Boolean = false,   // the one whose invitations we may hand out
    val on: Boolean = true,      // folded into Films and TV
    /**
     * Which machine answered here, as the machine names itself.
     *
     * Empty until this address has been reached once. Two addresses for one server -
     * the network one and the one from outside - carry the same id, which is how the
     * app knows to ask only one of them.
     */
    val id: String = "",
    /**
     * The server this one keeps copies of, if it is a cache.
     *
     * A second machine in the house holds what the house is watching and answers
     * when the first is off. It is a server like any other in this list - it is
     * opened, switched to and forgotten the same way - and this is what the row
     * says it is.
     */
    val copyOf: String = "",
    /**
     * Which group of servers this one belongs to, if the shelves are kept in groups.
     *
     * Empty is a group of its own. What a group is for: the house's two machines
     * are one library seen twice over, while a friend's is somebody else's evening
     * and does not belong on the same shelf as yours.
     */
    val group: String = "",
    /**
     * The same machine from outside the house, where it has a second address.
     *
     * A copy learned on the home network is remembered by its address there, and
     * that address means nothing from a train. One row, two doors, and whichever
     * answers is the one used.
     */
    val outside: String = "",
) {
    fun json(): JSONObject = JSONObject()
        .put("name", name).put("base", base).put("token", token)
        .put("mine", mine).put("on", on).put("id", id).put("copyOf", copyOf)
        .put("group", group).put("outside", outside)

    companion object {
        fun from(o: JSONObject) = Server(
            name = o.optString("name"),
            base = o.optString("base"),
            token = o.optString("token"),
            mine = o.optBoolean("mine"),
            on = o.optBoolean("on", true),
            id = o.optString("id"),
            copyOf = o.optString("copyOf"),
            group = o.optString("group"),
            outside = o.optString("outside"),
        )
    }
}

object Servers {
    private fun prefs(ctx: Context) =
        ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)

    fun all(ctx: Context): List<Server> {
        val raw = prefs(ctx).getString("servers", null)
        if (raw.isNullOrEmpty()) {
            // carry over the single address older versions stored
            val old = prefs(ctx).getString("server", "") ?: ""
            return if (old.isEmpty()) emptyList()
                   else listOf(Server(hostOf(old), old, "", mine = true))
        }
        val arr = JSONArray(raw)
        return (0 until arr.length()).map { Server.from(arr.getJSONObject(it)) }
    }

    /** Whether an address is one only this network can reach. */
    fun athome(where: String): Boolean {
        val host = hostOf(where).substringBefore(":")
        return host.startsWith("192.168.") || host.startsWith("10.") ||
            host.startsWith("127.") || host.endsWith(".local") ||
            host == "localhost" ||
            Regex("""^172\.(1[6-9]|2\d|3[01])\.""").containsMatchIn(host)
    }

    /** "on the network" or "from outside", for a row that shows an address. */
    fun whereKind(where: String): String =
        if (athome(where)) "lan" else "wan"

    /**
     * One row per machine, however many addresses it answers to.
     *
     * A server learned at home is filed under its network address; the same machine
     * reached from a train is filed under the one the router forwards. Both were
     * added, so the list showed the house twice under two names and switching between
     * them looked like switching machines. They carry the same id - that is what the
     * id is for - so they are folded here, the network address kept as the way in and
     * the other remembered beside it.
     */
    fun folded(list: List<Server>): List<Server> {
        val out = ArrayList<Server>()
        list.forEach { srv ->
            val same = out.indexOfFirst { had ->
                (had.id.isNotEmpty() && had.id == srv.id) ||
                had.base == srv.outside || srv.base == had.outside
            }
            if (same < 0) {
                out.add(srv)
                return@forEach
            }
            val had = out[same]
            // the address on this network is the one to open; the other is the way
            // back in from away, and is kept rather than thrown out
            val home = if (athome(had.base)) had else srv
            val away = if (athome(had.base)) srv else had
            out[same] = home.copy(
                name = home.name.ifBlank { away.name },
                outside = if (athome(away.base)) home.outside else away.base,
                on = home.on || away.on,
                copyOf = home.copyOf.ifBlank { away.copyOf },
                id = home.id.ifBlank { away.id })
        }
        return out
    }

    fun save(ctx: Context, list: List<Server>) {
        // Written as given. Folding here looked tidy and was destructive: the screens
        // show a folded list, so anything that saved what it was showing threw away
        // every row that had been folded into another - one server forgotten, several
        // gone. Folding is for drawing; the book keeps what it was told.
        val arr = JSONArray()
        list.forEach { arr.put(it.json()) }
        prefs(ctx).edit().putString("servers", arr.toString()).apply()
    }

    /** Whether two rows are two ways of writing down the same machine. */
    fun sameMachine(a: Server, b: Server): Boolean {
        if (a.id.isNotEmpty() && a.id == b.id) return true
        val ways = { s: Server ->
            listOf(s.base, s.outside).map { it.trimEnd('/') }.filter { it.isNotEmpty() }
        }
        return ways(a).any { it in ways(b) }
    }

    fun current(ctx: Context): Server? {
        val list = all(ctx)
        if (list.isEmpty()) return null
        val want = prefs(ctx).getString("current", "") ?: ""
        return list.firstOrNull { it.base == want } ?: list.first()
    }

    fun use(ctx: Context, s: Server) {
        prefs(ctx).edit().putString("current", s.base).apply()
        // Opening a server shows it. The machine that keeps copies is added hidden,
        // so its shelf does not stand beside the same films from the server it
        // copies - but switching to it and finding nothing there is not hiding, it
        // is a fault.
        //
        // And the list is written back whatever the case, because the address may
        // have changed on the way in: a machine known by its name at home is reached
        // by another from a train, and an address that is not in the list is one
        // "the current server" cannot find - which quietly resolved to the first
        // server instead and drew its library under the other one's name.
        val kept = s.copy(on = true)
        val had = all(ctx)
        val list = if (had.any { it.base == kept.base || it.base == kept.outside }) {
            had.map { if (it.base == kept.base || it.base == kept.outside) kept else it }
        } else {
            had + kept
        }
        save(ctx, list)
        Api.use(kept)
    }

    /**
     * Write down the other address a machine answers to, as it named them itself.
     *
     * A server typed in at home is filed under the address that was typed, and the
     * row knows nothing about the way in from outside - so away from home the list
     * showed one address, the one that cannot be reached. The machine knows both.
     */
    fun learnDoors(ctx: Context, forBase: String, lan: String, outside: String,
                   named: String = "") {
        val at = forBase.trimEnd('/')
        val other = listOf(lan, outside).map { it.trimEnd('/') }
            .firstOrNull { it.isNotEmpty() && it != at } ?: ""
        val list = all(ctx)
        val had = list.firstOrNull { it.base.trimEnd('/') == at } ?: return
        // and what the machine calls itself, when the row is only calling it by its
        // address. A computer has a name and it is better than four numbers.
        val callIt = if (named.isNotEmpty() && had.name == hostOf(had.base)) named
                     else had.name
        if (had.outside.trimEnd('/') == other && callIt == had.name) return
        save(ctx, list.map {
            if (it.base == had.base)
                it.copy(outside = other.ifEmpty { it.outside }, name = callIt)
            else it
        })
    }

    fun add(ctx: Context, s: Server) {
        // Any address of that machine, not just this one. Matching on the exact
        // address meant the same server learned by its other way in arrived as a
        // second row, and the list grew a duplicate every time it was reached from
        // the other side of the router.
        save(ctx, all(ctx).filterNot { sameMachine(it, s) } + s)
    }

    /**
     * Collapse rows that are the same machine, once, and write the result down.
     *
     * Deliberate, and separate from the folding the screens do to draw a list: that
     * one is a view, and saving a view is how a list loses servers. This is called
     * when the app starts, on the stored list itself, and it merges rather than
     * drops - the other address is kept on the row that survives.
     */
    fun tidy(ctx: Context) {
        val had = all(ctx)
        val one = folded(had)
        if (one.size != had.size) save(ctx, one)
    }

    /**
     * Remember the machine that keeps copies of one we already know.
     *
     * Added the way any other server is, with the same key, and left out of the
     * shelves: what is on it is a part of what is on the server it copies, and
     * folding it in would put those films on the screen twice.
     */
    fun learnCopy(ctx: Context, of: String, where: String, name: String, token: String,
                  outside: String = "") {
        if (where.isEmpty() || of.isEmpty() || where == of) return
        val list = all(ctx)
        // The same machine, filed under the other of its two addresses. A copy
        // learned from a train is filed under the way in from outside, and back on
        // the house network that address goes out to the router and back - when it
        // works at all. Whichever one suits where we are standing is the one to open.
        val elsewhere = list.firstOrNull {
            it.base != where && it.outside.trimEnd('/') == where.trimEnd('/')
        }
        if (elsewhere != null) {
            val swapped = elsewhere.copy(base = where, outside = elsewhere.base,
                                         copyOf = of,
                                         // a name that was only the old address
                                         // written out follows the address
                                         name = if (elsewhere.name ==
                                                    hostOf(elsewhere.base))
                                                    name.ifEmpty { hostOf(where) }
                                                else elsewhere.name,
                                         token = if (token.isNotEmpty()) token
                                                 else elsewhere.token)
            save(ctx, list.map { if (it.base == elsewhere.base) swapped else it })
            return
        }
        val had = list.firstOrNull { it.base == where }
        if (had != null) {
            // the key and the second address are worth keeping up to date: a row
            // learned at home holds an address that means nothing from a train, and
            // a key that was empty because at home it needed none
            val fixed = had.copy(copyOf = of, outside = outside.ifEmpty { had.outside },
                                 token = if (token.isNotEmpty()) token else had.token)
            if (fixed != had) save(ctx, list.map { if (it.base == where) fixed else it })
            return
        }
        // One cache per machine, whichever address either of them was learned by.
        // Matching on the server it copies was not enough: that server answers to
        // two addresses too, so the same copy arrived as a second row every time the
        // house was reached the other way.
        val row = Server(name = name.ifEmpty { hostOf(where) }, base = where,
                         token = token, mine = false, on = false, copyOf = of,
                         outside = outside)
        save(ctx, list.filterNot { sameMachine(it, row) } + row)
    }

    /**
     * The address of this server that answers from where we are standing.
     *
     * Tried in order: the one it is filed under, then its address from outside. A
     * machine kept in a cupboard at home is reached one way on the sofa and another
     * way from a train, and the row is the same row.
     */
    suspend fun doorThatOpens(s: Server): String {
        if (Api.answering(s.base, s.token)) return s.base
        if (s.outside.isNotEmpty() && Api.answering(s.outside, s.token)) return s.outside
        return s.base
    }

    /**
     * Ask a server whether it takes us for its owner, and write down the answer.
     *
     * Whose server it is was decided when the row was added - by whether an
     * invitation was pasted in - and never asked again. But it is the server that
     * decides, by the address a request arrives on: the same machine calls somebody
     * its owner on the sofa and a guest from a train, and a row added by link at home
     * wore "guest" for ever.
     */
    suspend fun learnWhose(ctx: Context, s: Server) {
        val said = describe(s.base, s.token)
            ?: (if (s.outside.isNotEmpty()) describe(s.outside, s.token) else null)
            ?: return
        if (said.second == s.mine) return
        save(ctx, all(ctx).map { if (it.base == s.base) it.copy(mine = said.second)
                                 else it })
    }

    /** Write down which machine an address turned out to be. */
    fun learnt(ctx: Context, base: String, id: String) {
        if (id.isEmpty()) return
        val list = all(ctx)
        if (list.none { it.base == base && it.id != id }) return
        save(ctx, list.map { if (it.base == base) it.copy(id = id) else it })
    }

    fun remove(ctx: Context, s: Server) {
        // every address of that machine, since the row stood for all of them
        save(ctx, all(ctx).filterNot { sameMachine(it, s) })
        if (current(ctx) == null) Api.use(Server("", "", ""))
        else current(ctx)?.let { Api.use(it) }
    }

    fun setShown(ctx: Context, s: Server, on: Boolean) {
        save(ctx, all(ctx).map { if (it.base == s.base) it.copy(on = on) else it })
    }

    /** The servers whose libraries appear together: the open one, plus every other shown. */
    /**
     * The servers to fill a shelf from: the one in use, then the others that are on.
     *
     * One machine appears once. The phone may hold this server twice - by its address
     * on the network and by its address from outside - because either may be the one
     * that works; asking both would put every film on the screen twice. The address
     * in use wins, being the one known to answer.
     */
    /**
     * How the libraries of several servers are shown: all on one shelf, one server
     * at a time, or a shelf per group of servers.
     *
     * "together" is what this always did. "one" is for somebody who thinks of a
     * friend's library as a place they visit rather than part of theirs. "groups"
     * is both: the house's machines on one shelf, a friend's on another.
     */
    fun shelves(ctx: Context): String =
        prefs(ctx).getString("shelves", "together") ?: "together"

    fun setShelves(ctx: Context, how: String) {
        prefs(ctx).edit().putString("shelves", how).apply()
    }

    fun setGroup(ctx: Context, s: Server, group: String) {
        save(ctx, all(ctx).map { if (it.base == s.base) it.copy(group = group) else it })
    }

    /** The groups that have been named, for a chooser that offers them. */
    fun groups(ctx: Context): List<String> =
        all(ctx).map { it.group }.filter { it.isNotEmpty() }.distinct().sorted()

    fun merged(ctx: Context): List<Server> {
        val cur = current(ctx) ?: return emptyList()
        val how = shelves(ctx)
        val others = when (how) {
            // the one that is open, and nothing else
            "one" -> emptyList()
            // the ones kept alongside it, which is what a group is
            "groups" -> all(ctx).filter {
                it.base != cur.base && it.on && it.group == cur.group
            }
            else -> all(ctx).filter { it.base != cur.base && it.on }
        }
        val out = ArrayList<Server>()
        val machines = HashSet<String>()
        // Hidden means hidden, even for the server that is open: it is still the one
        // being asked about settings and updates, but its films stay off the shelves.
        // With one server at a time there is nothing else to show, so it stays.
        val head = if (cur.on || how == "one") listOf(cur) else emptyList()
        // The same film on two machines is one card, and the card that survives is
        // the first one seen - so the order is how quickly each answered when it was
        // last asked. A copy in the cupboard beats the same film from a server two
        // hundred milliseconds away, whichever of them happens to be open.
        val order = (head + others).sortedBy { Api.paceOf(it.base) }
        order.forEach { srv ->
            // an address not yet identified counts as its own machine: better to ask
            // twice once than to hide a server that is genuinely somebody else's
            val mark = if (srv.id.isEmpty()) "?" + srv.base else srv.id
            if (machines.add(mark)) out.add(srv)
        }
        // A machine that keeps copies of one already on this shelf has nothing of its
        // own to add - everything on it came from there - and asking it costs a whole
        // round trip per shelf to a machine that is usually the slower of the two.
        // It stays in the list, it is still opened and switched to; it is simply not
        // asked twice for the same film.
        val alsoHere = out.map { it.base.trimEnd('/') }.toSet()
        val worth = out.filterNot { srv ->
            srv.copyOf.isNotEmpty() && srv.base != cur.base &&
                (srv.copyOf.trimEnd('/') in alsoHere ||
                 out.any { it.outside.trimEnd('/') == srv.copyOf.trimEnd('/') })
        }
        return if (worth.isEmpty()) out else worth
    }

    fun hostOf(url: String) = url.removePrefix("http://").removePrefix("https://")
        .substringBefore("/")

    /**
     * What somebody typed, as an address a request can be made to.
     *
     * People write "192.168.1.20:8765", or leave the port off, or paste something
     * with a stray slash on the end. All of those mean the same server.
     */
    fun asAddress(typed: String): String {
        var text = typed.trim().trimEnd('/')
        if (text.isEmpty()) return text
        if (!text.startsWith("http://") && !text.startsWith("https://")) {
            text = "http://" + text
        }
        val host = text.removePrefix("http://").removePrefix("https://")
        if (!host.contains(":")) text += ":8765"     // the port this server uses
        return text
    }

    /**
     * Exchange a short setup code for the token it stands for.
     *
     * What somebody types on a television when the alternative is thirty-two
     * characters on a directional pad. Null if the server does not know the code, in
     * which case whatever was typed is treated as a token, which it may well be.
     */
    suspend fun redeem(base: String, code: String): String? =
        withContext(Dispatchers.IO) {
            try {
                val conn = URL("$base/i/${code.uppercase()}/setup")
                    .openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 8000
                val body = conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
                JSONObject(body).optString("token").ifEmpty { null }
            } catch (e: Exception) {
                null
            }
        }

    /** Five characters, none of them easily misread: a setup code, not a token. */
    fun looksLikeCode(text: String) =
        text.length == 5 && text.all { it.isLetterOrDigit() } &&
        text.none { it in "oOiI01" }

    /** Pull an address and a token out of a pasted invitation link. */
    fun parseInvite(text: String): Pair<String, String>? {
        val m = Regex("^(https?://[^/]+)/s/([A-Za-z0-9_-]{8,})").find(text.trim()) ?: return null
        return Pair(m.groupValues[1], m.groupValues[2])
    }

    /**
     * Ask a server what it calls itself, and whether we are its owner.
     *
     * Ownership is not a claim the app can make: the server decides, by the address the
     * request came from. Being told the home settings back means we are on the home
     * network, which is exactly when handing out invitations is allowed.
     */
    suspend fun describe(base: String, token: String): Pair<String, Boolean>? =
        withContext(Dispatchers.IO) {
            val o = hello(base, token) ?: return@withContext null
            Pair(o.optString("serverName").ifEmpty { hostOf(base) }, !o.optBoolean("guest"))
        }

    /** What /config says, or null if the address did not answer. */
    private suspend fun hello(base: String, token: String): JSONObject? =
        withContext(Dispatchers.IO) {
            try {
                val sep = if (token.isEmpty()) "" else "?t=$token"
                val conn = URL("$base/config$sep").openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 8000
                val body = conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
                JSONObject(body)
            } catch (e: Exception) {
                null
            }
        }

    /**
     * Find out which machine each stored address belongs to.
     *
     * Only addresses that have never answered are asked, so this is nothing on most
     * runs. Once an address has been placed it stays placed, and the shelves stop
     * asking one server twice under two names.
     */
    suspend fun identify(ctx: Context) {
        all(ctx).filter { it.id.isEmpty() }.forEach { srv ->
            val id = hello(srv.base, srv.token)?.optString("serverId") ?: ""
            if (id.isNotEmpty()) learnt(ctx, srv.base, id)
        }
    }
}
