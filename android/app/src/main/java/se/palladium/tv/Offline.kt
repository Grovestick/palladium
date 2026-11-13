package se.palladium.tv

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Titles kept on this device, to play where no server answers.
 *
 * A setting, off unless turned on. On, a film or an episode can be chosen for offline
 * viewing: the server makes a copy in the quality asked for, this device fetches it into
 * its own folder on the storage chosen (internal or a card) and the server deletes its
 * copy once it is here. The shelves answer from what they last said while no server
 * answers, and there - or with the test on - what is kept is drawn in colour and the
 * rest grey.
 */
object Offline {
    data class Copy(
        val key: String, val mi: Int, val title: String, val quality: String,
        /** the series and the season an episode belongs to, so their posters count it */
        val show: String, val season: String,
        /** the server's row as JSON; "" for the library's own */
        val srv: String,
        val job: String = "",
        /** asked, making, fetching, ready, failed */
        val state: String = "asked",
        val pct: Int = 0, val size: Long = 0, val file: String = "",
        /** the subtitle tracks in the copy, by their number in the original */
        val subs: List<Int> = emptyList(),
        val why: String = "")

    /** the setting itself: nothing here runs while it is off */
    val on = mutableStateOf(false)
    /** the library's server did not answer, and the shelves are its last answers */
    val cut = mutableStateOf(false)
    /** The Offline filter, set in Settings: "" off, "only" the titles chosen for offline
     *  viewing, kept or coming in, "mix" everything with the kept ones in colour */
    val filter = mutableStateOf("")
    val copies = mutableStateMapOf<String, Copy>()
    /** Titles chosen in Versions to play from the copy on this device while a server answers */
    val playHere = mutableStateMapOf<String, Boolean>()

    @Volatile private var app: Context? = null
    @Volatile private var working = false
    private val hashes = java.util.concurrent.ConcurrentHashMap<String, Int>()

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)

    fun load(ctx: Context) {
        val c = ctx.applicationContext
        app = c
        on.value = prefs(c).getBoolean("offlineOn", false)
        filter.value = prefs(c).getString("offlineFilter", "") ?: ""
        sync.value = prefs(c).getString("offlineSync", "wifi") ?: "wifi"
        pausedNow = prefs(c).getLong("offlinePaused", 0L) != 0L
        held.value = pausedNow
        prefs(c).getStringSet("offlinePlayHere", emptySet())?.forEach { playHere[it] = true }
        val f = File(c.filesDir, "offline.json")
        if (f.exists()) runCatching {
            val arr = JSONArray(f.readText())
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val subs = o.optJSONArray("subs") ?: JSONArray()
                copies[o.getString("key")] = Copy(
                    o.getString("key"), o.optInt("mi"), o.optString("title"),
                    o.optString("quality"), o.optString("show"), o.optString("season"),
                    o.optString("srv"), o.optString("job"), o.optString("state", "asked"),
                    o.optInt("pct"), o.optLong("size"), o.optString("file"),
                    (0 until subs.length()).map { subs.getInt(it) }, o.optString("why"))
            }
        }
        if (on.value && !pausedNow) kick(c)
    }

    private fun save() {
        val c = app ?: return
        val arr = JSONArray()
        for (it in copies.values) arr.put(JSONObject().put("key", it.key).put("mi", it.mi)
            .put("title", it.title).put("quality", it.quality).put("show", it.show)
            .put("season", it.season).put("srv", it.srv).put("job", it.job)
            .put("state", it.state).put("pct", it.pct).put("size", it.size)
            .put("file", it.file).put("subs", JSONArray(it.subs)).put("why", it.why))
        val f = File(c.filesDir, "offline.json")
        val tmp = File(c.filesDir, "offline.json.tmp")
        tmp.writeText(arr.toString())
        tmp.renameTo(f)
    }

    fun setOn(ctx: Context, yes: Boolean) {
        on.value = yes
        prefs(ctx).edit().putBoolean("offlineOn", yes).apply()
        if (!yes) cut.value = false
        forgetImageLoader()
        if (yes) kick(ctx)
    }

    /** Whether posters are drawn as they would be with no server: kept ones in colour. */
    fun view(): Boolean = on.value && (cut.value || filter.value == "mix")

    /** Whether copies come to this device: "on" over any network, "wifi" over Wi-Fi or a
     *  cable only, "off" not at all. Asking the server to make one is a few bytes and goes
     *  on any network; the copy itself waits. */
    val sync = mutableStateOf("wifi")

    fun setSync(ctx: Context, s: String) {
        sync.value = s
        prefs(ctx).edit().putString("offlineSync", s).apply()
        onLine?.invoke(line())
        if (s != "off") kick(ctx)
    }

    /** On Wi-Fi or a cable now. */
    fun onWifi(ctx: Context): Boolean = runCatching {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return@runCatching false
        caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_ETHERNET)
    }.getOrDefault(false)

    /** Why a copy waits to come here now, or "". */
    fun fetchWaits(ctx: Context): String = when {
        sync.value == "off" -> "sync off"
        sync.value == "wifi" && !onWifi(ctx) -> "waiting for Wi-Fi"
        else -> ""
    }

    fun setFilter(ctx: Context, f: String) {
        filter.value = f
        prefs(ctx).edit().putString("offlineFilter", f).apply()
    }

    /** Whether the filter's "only" keeps it: chosen for offline viewing, kept or coming
     *  in, or a series, season or shelf holding something that is. */
    fun chosen(m: Media): Boolean = copies.containsKey(m.ratingKey) ||
        copies.values.any { it.show == m.ratingKey || it.season == m.ratingKey } ||
        m.holds.any { copies.containsKey(it) }

    /** A list as the filter shows it: a collection's card stays, to be opened onto the
     *  titles in it that are. */
    fun narrow(list: List<Media>): List<Media> =
        if (on.value && filter.value == "only")
            list.filter { it.type == "collection" || chosen(it) } else list

    /** A copy here and whole, for a title or for the series or season it is in. */
    fun has(m: Media): Boolean = copies.values.any {
        it.state == "ready" && (it.key == m.ratingKey || it.show == m.ratingKey ||
                                it.season == m.ratingKey)
    }

    /** The file to play for this title, where it is to be played from here. */
    fun playable(m: Media): Copy? {
        if (!Route.playsHere(on.value, view(), playHere[m.ratingKey] == true)) return null
        val c = copies[m.ratingKey] ?: return null
        return c.takeIf { it.state == "ready" && File(it.file).exists() }
    }

    /** Whether a title can be kept: one of the library's, with a file behind it. */
    fun keepable(m: Media): Boolean =
        on.value && (m.type == "movie" || m.type == "episode") && m.partKey != null &&
            !m.offered && !m.askable && !m.upcoming && !m.live

    /* ---------------- where ---------------- */

    data class Place(val label: String, val dir: File, val free: Long)

    /** The app's own folder on each storage this device has: no permission is asked. */
    fun places(ctx: Context): List<Place> =
        ctx.getExternalFilesDirs("offline").filterNotNull().mapIndexed { i, d ->
            val removable = runCatching {
                android.os.Environment.isExternalStorageRemovable(d) }.getOrDefault(i > 0)
            Place(if (removable) "SD card" else "Internal", d,
                  runCatching { d.usableSpace }.getOrDefault(0L))
        }.ifEmpty { listOf(Place("Internal", File(ctx.filesDir, "offline"),
                                 ctx.filesDir.usableSpace)) }

    fun place(ctx: Context): Place {
        val all = places(ctx)
        val chosen = prefs(ctx).getString("offlineDir", "") ?: ""
        return all.firstOrNull { it.dir.absolutePath == chosen } ?: all.first()
    }

    fun setPlace(ctx: Context, p: Place) {
        prefs(ctx).edit().putString("offlineDir", p.dir.absolutePath).apply()
    }

    /* ---------------- what this device plays ---------------- */

    /** What the copy may keep as it is: HEVC, HDR10, and which sound this device decodes. */
    fun can(ctx: Context): JSONObject {
        val infos = runCatching {
            android.media.MediaCodecList(android.media.MediaCodecList.REGULAR_CODECS)
                .codecInfos.filter { !it.isEncoder }
        }.getOrDefault(emptyList())
        fun decodes(mime: String) = infos.any { i -> i.supportedTypes.any { it.equals(mime, true) } }
        val main10 = infos.any { i ->
            i.supportedTypes.any { it.equals("video/hevc", true) } && runCatching {
                i.getCapabilitiesForType("video/hevc").profileLevels.any {
                    it.profile == android.media.MediaCodecInfo.CodecProfileLevel.HEVCProfileMain10HDR10
                }
            }.getOrDefault(false)
        }
        val screen = runCatching {
            (ctx.getSystemService(Context.DISPLAY_SERVICE) as android.hardware.display.DisplayManager)
                .getDisplay(android.view.Display.DEFAULT_DISPLAY)
                .hdrCapabilities.supportedHdrTypes
                .contains(android.view.Display.HdrCapabilities.HDR_TYPE_HDR10)
        }.getOrDefault(false)
        val sound = JSONArray()
        for ((codec, mime) in listOf("ac3" to "audio/ac3", "eac3" to "audio/eac3",
                                     "dts" to "audio/vnd.dts", "truehd" to "audio/true-hd"))
            if (decodes(mime)) sound.put(codec)
        return JSONObject().put("hevc", decodes("video/hevc"))
            .put("hdr", main10 && screen).put("sound", sound)
    }

    /* ---------------- choosing ---------------- */

    fun want(ctx: Context, m: Media, quality: String) {
        val had = copies[m.ratingKey]
        if (had != null) drop(ctx, m.ratingKey)
        copies[m.ratingKey] = Copy(
            m.ratingKey, m.mi, listOfNotNull(m.grandparentTitle, m.title).joinToString(" - "),
            quality, m.grandparentKey ?: "", m.parentKey ?: "",
            m.srv?.json()?.toString() ?: "")
        save()
        // its card, and its series', asked once now so the Offline filter has them
        // with no server answering
        @OptIn(kotlinx.coroutines.DelicateCoroutinesApi::class)
        kotlinx.coroutines.GlobalScope.launch(Dispatchers.IO) {
            runCatching { Api.item(m.ratingKey, m.srv) }
            m.grandparentKey?.let { runCatching { Api.item(it, m.srv) } }
        }
        kick(ctx)
    }

    /** Not kept any more: the file here and the server's copy deleted. */
    /** Versions: play this title from the copy here (true), or from a server again. */
    fun setPlayHere(ctx: Context, key: String, yes: Boolean) {
        if (yes) playHere[key] = true else playHere.remove(key)
        prefs(ctx).edit().putStringSet("offlinePlayHere", playHere.keys.toSet()).apply()
    }

    fun drop(ctx: Context, key: String) {
        val c = copies.remove(key) ?: return
        setPlayHere(ctx, key, false)
        save()
        if (c.file.isNotEmpty()) { File(c.file).delete(); File(c.file + ".part").delete() }
        if (c.job.isNotEmpty() && c.state != "ready") {
            @OptIn(kotlinx.coroutines.DelicateCoroutinesApi::class)
            kotlinx.coroutines.GlobalScope.launch(Dispatchers.IO) {
                runCatching { Api.offlineLet(server(c), c.job, taken = false) }
            }
        }
    }

    private fun server(c: Copy): Server? =
        if (c.srv.isEmpty()) null else runCatching { Server.from(JSONObject(c.srv)) }.getOrNull()

    fun serverOf(c: Copy): Server? = server(c)

    private fun put(c: Copy) {
        val was = copies[c.key]
        copies[c.key] = c
        if (was?.pct != c.pct || was.state != c.state) onLine?.invoke(line())
    }

    /** The notification's line, told to the service as it changes. */
    @Volatile var onLine: ((String) -> Unit)? = null

    /** Paused from the notification, until Resume there or on the title's Offline
     *  button (owner, 2026-11-12): nothing comes in meanwhile, the part kept. */
    @Volatile private var pausedNow = false
    val held = mutableStateOf(false)

    fun paused(): Boolean = pausedNow

    fun pause(ctx: Context) {
        pausedNow = true
        held.value = true
        prefs(ctx).edit().putLong("offlinePaused", System.currentTimeMillis()).apply()
    }

    fun resume(ctx: Context) {
        pausedNow = false
        held.value = false
        prefs(ctx).edit().putLong("offlinePaused", 0L).apply()
        kick(ctx)
    }

    /** A quality as the notification says it. */
    fun qualityName(q: String): String = when (q) {
        "720" -> "720p"
        "1080" -> "1080p"
        else -> "Original"
    }

    /** What the notification says: the title coming in, its quality, how far, and how
     *  many more. */
    fun line(): String {
        val open = copies.values.filter { it.state in setOf("asked", "making", "fetching") }
        val waits = app?.let { fetchWaits(it) } ?: ""
        val now = open.firstOrNull { it.state == "fetching" }
            ?: open.firstOrNull { it.state == "making" } ?: open.firstOrNull()
            ?: return "Nothing left to fetch"
        val which = now.title + "  ·  " + qualityName(now.quality)
        val more = if (open.size > 1) "  ·  ${open.size - 1} more" else ""
        if (paused()) return "Paused  ·  $which$more"
        if (waits.isNotEmpty() && now.state == "fetching")
            return waits.replaceFirstChar { it.uppercase() } + "  ·  $which$more"
        val doing = when (now.state) {
            "fetching" -> "fetching ${now.pct}%"
            "making" -> "server making it ${now.pct}%"
            else -> "asked for"
        }
        return "$which  ·  $doing$more"
    }

    /** Words for a copy: how far it has got. */
    fun words(c: Copy): String = when (c.state) {
        "asked" -> "Asked for" + if (c.why.isNotEmpty()) "  ·  " + c.why else ""
        "making" -> "Server making it  ·  ${c.pct}%" +
            if (c.why.isNotEmpty()) "  ·  " + c.why else ""
        "fetching" -> "Coming to this device  ·  ${c.pct}%" +
            if (c.why.isNotEmpty()) "  ·  " + c.why else ""
        "ready" -> "On this device  ·  " + gb(c.size)
        else -> "Failed  ·  " + c.why
    }

    fun gb(bytes: Long): String = String.format(java.util.Locale.US, "%.1f GB", bytes / 1e9)

    /** Settings' line for what is kept: how many, the gigabytes here, what is still
     *  coming where the server has said its size, and the room left on the storage. */
    fun total(kept: Collection<Copy>, place: Place): String {
        val ready = kept.filter { it.state == "ready" }
        val coming = kept.filter { it.state != "ready" && it.state != "failed" }
        val sized = coming.filter { it.size > 0 }
        return ready.size.toString() + " of " + kept.size + " here  ·  " + gb(ready.sumOf { it.size }) +
            (if (sized.isNotEmpty()) "  ·  " + gb(sized.sumOf { it.size }) + " more coming" else "") +
            (if (coming.size > sized.size) "  ·  ${coming.size - sized.size} not made yet" else "") +
            "  ·  " + gb(place.free) + " free on " + place.label
    }

    /* ---------------- the shelves' last answers ---------------- */

    /** Whether this answer is kept for when no server answers. Polls are not. */
    fun keeps(path: String): Boolean = on.value && !path.startsWith("/torrents/active") &&
        listOf("/trace", "/notice", "/chat", "/offline", "/gpu", "/applog", "/stream",
               "/monitor", "/party").none { path.startsWith(it) }

    private fun answerFile(srv: Server?, path: String): File? {
        val c = app ?: return null
        val name = (srv?.id?.ifEmpty { srv.name } ?: "main") + " " + path
        val digest = java.security.MessageDigest.getInstance("SHA-1")
            .digest(name.toByteArray()).joinToString("") { "%02x".format(it) }
        return File(File(c.filesDir, "answers").apply { mkdirs() }, digest)
    }

    fun answered(srv: Server?, path: String, said: String) {
        if (srv == null) cut.value = false
        val f = answerFile(srv, path) ?: return
        val h = said.hashCode()
        if (hashes[f.name] == h && f.exists()) return
        hashes[f.name] = h
        runCatching {
            val tmp = File(f.path + ".tmp")
            tmp.writeText(said)
            tmp.renameTo(f)
        }
    }

    fun answerKept(srv: Server?, path: String): String? {
        val f = answerFile(srv, path) ?: return null
        if (!f.exists()) return null
        if (srv == null) cut.value = true
        return runCatching { f.readText() }.getOrNull()
    }

    /* ---------------- where playing got to, with no server to tell ---------------- */

    fun keepProgress(host: String, tok: String, key: String, pos: Long, dur: Long) {
        val c = app ?: return
        if (!on.value) return
        val kept = JSONObject(prefs(c).getString("offlineProgress", "{}") ?: "{}")
        kept.put(key, JSONObject().put("host", host).put("tok", tok).put("pos", pos)
                 .put("dur", dur))
        prefs(c).edit().putString("offlineProgress", kept.toString()).apply()
    }

    fun progressReached(key: String) {
        val c = app ?: return
        val raw = prefs(c).getString("offlineProgress", "{}") ?: "{}"
        if (!raw.contains("\"$key\"")) return
        val kept = JSONObject(raw)
        kept.remove(key)
        prefs(c).edit().putString("offlineProgress", kept.toString()).apply()
    }

    private suspend fun sendProgress(c: Context) {
        val kept = JSONObject(prefs(c).getString("offlineProgress", "{}") ?: "{}")
        for (key in kept.keys().asSequence().toList()) {
            val o = kept.getJSONObject(key)
            runCatching {
                Api.progressAt(o.getString("host"), o.getString("tok"), key,
                               o.getLong("pos"), o.getLong("dur"), "stopped")
            }
        }
    }

    /* ---------------- the work ---------------- */

    /** The work started, kept alive by a service while it runs. */
    fun kick(ctx: Context) {
        if (!on.value || paused()) return
        runCatching {
            androidx.core.content.ContextCompat.startForegroundService(
                ctx, Intent(ctx, OfflineService::class.java))
        }
    }

    /** Called by the service: the copies followed until none is left to fetch. */
    suspend fun work(ctx: Context) {
        if (working) return
        working = true
        try {
            while (on.value && sync.value != "off" && !paused()) {
                sendProgress(ctx)
                val open = copies.values.filter { it.state in setOf("asked", "making", "fetching") }
                if (open.isEmpty()) break
                for (c in open) {
                    // the copy itself only over the network the setting allows
                    val waits = if (c.state == "fetching") fetchWaits(ctx) else ""
                    if (waits.isEmpty()) step(ctx, c)
                    else if (c.why != waits) put(c.copy(why = waits))
                }
                onLine?.invoke(line())
                delay(10_000)
            }
        } finally {
            working = false
        }
    }

    private suspend fun step(ctx: Context, c: Copy) = withContext(Dispatchers.IO) {
        val srv = server(c)
        when (c.state) {
            "asked" -> runCatching { Api.offlineMake(srv, c.key, c.mi, c.quality, can(ctx)) }
                .onSuccess { said ->
                    val job = said.optJSONObject("job")
                    if (copies[c.key] == null) return@onSuccess
                    put(if (job != null) c.copy(job = job.optString("id"), state = "making", why = "")
                        else c.copy(state = "failed", why = said.optString("why", "refused")))
                    save()
                }
                .onFailure { put(c.copy(why = "server not answering")) }
            "making" -> runCatching { Api.offlineJobs(srv, listOf(c.job)) }
                .onSuccess { jobs ->
                    if (copies[c.key] == null) return@onSuccess
                    val j = jobs.firstOrNull()
                    val subs = j?.optJSONArray("subs")?.let { a -> (0 until a.length()).map { a.getInt(it) } }
                    put(when (j?.optString("state")) {
                        null -> c.copy(state = "asked", job = "", pct = 0)    // the server lost it
                        "ready" -> c.copy(state = "fetching", pct = 0, size = j.optLong("size"),
                                          subs = subs ?: emptyList(), why = "")
                        "failed" -> c.copy(state = "failed", why = j.optString("why"))
                        else -> c.copy(pct = j.optInt("pct"), why = j.optString("why"))
                    })
                    save()
                }
                .onFailure { put(c.copy(why = "server not answering")) }
            "fetching" -> fetch(ctx, c, srv)
        }
    }

    /** The finished copy brought here, from where a cut-off one stopped. */
    private suspend fun fetch(ctx: Context, c0: Copy, srv: Server?) {
        var c = c0
        if (c.file.isEmpty()) {
            val p = place(ctx)
            p.dir.mkdirs()
            c = c.copy(file = File(p.dir, c.key + "-" + c.quality + ".mkv").absolutePath)
            put(c); save()
        }
        val part = File(c.file + ".part")
        val have = if (part.exists()) part.length() else 0L
        if (c.size > 0 && part.parentFile?.usableSpace ?: 0L < c.size - have + 200_000_000L) {
            put(c.copy(why = "not enough room on this storage")); return
        }
        try {
            val conn = Net.open(Api.offlineFileUrl(srv, c.job), 8000, 60_000,
                                srv?.token ?: Api.token)
            if (have > 0) conn.setRequestProperty("Range", "bytes=$have-")
            val code = conn.responseCode
            if (code == 404) {
                // made again: the server no longer has it
                part.delete()
                put(c.copy(state = "asked", job = "", pct = 0, why = "")); save(); return
            }
            val from = if (code == 206) have else 0L
            val total = if (c.size > 0) c.size else from + conn.contentLengthLong
            java.io.FileOutputStream(part, code == 206).use { out ->
                conn.inputStream.use { inp ->
                    val buf = ByteArray(256 * 1024)
                    var got = from
                    var said = -1
                    while (true) {
                        if (copies[c.key] == null) return          // let go of meanwhile
                        if (paused()) return                        // the part kept for later
                        val n = inp.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        got += n
                        val pct = if (total > 0) (got * 100 / total).toInt() else 0
                        if (pct != said) {
                            said = pct
                            put(c.copy(pct = pct, why = ""))
                            // off Wi-Fi with Wi-Fi only: stopped, the part kept for later
                            if (fetchWaits(ctx).isNotEmpty()) return
                        }
                    }
                }
            }
            if (total > 0 && part.length() < total) return     // cut off: the next look goes on
            part.renameTo(File(c.file))
            put(c.copy(state = "ready", pct = 100, size = File(c.file).length(), why = ""))
            save()
            runCatching { Api.offlineLet(srv, c.job, taken = true) }
        } catch (e: java.io.IOException) {
            copies[c.key]?.let { put(it.copy(why = "server not answering")) }
        }
    }
}

/** Keeps the app running while copies come in, with a line in the notifications. */
class OfflineService : android.app.Service() {
    private val scope = kotlinx.coroutines.CoroutineScope(
        kotlinx.coroutines.SupervisorJob() + Dispatchers.Default)

    override fun onBind(intent: Intent?): android.os.IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(android.app.NotificationManager::class.java)
        fun press(action: String) = Intent(this, OfflineService::class.java).setAction(action).let {
            val flags = android.app.PendingIntent.FLAG_IMMUTABLE or
                android.app.PendingIntent.FLAG_UPDATE_CURRENT
            // Resume starts the work again from a press on the notification, which
            // Android lets start a foreground service
            if (action == "resume" && android.os.Build.VERSION.SDK_INT >= 26)
                android.app.PendingIntent.getForegroundService(this, action.hashCode(), it, flags)
            else android.app.PendingIntent.getService(this, action.hashCode(), it, flags)
        }
        if (android.os.Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(
            android.app.NotificationChannel("offline", "Offline copies",
                                            android.app.NotificationManager.IMPORTANCE_LOW))
        fun note(text: String) = androidx.core.app.NotificationCompat.Builder(this, "offline")
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("Offline viewing")
            .setContentText(text)
            // paused it can be swept away: Resume is on the title's Offline button too
            .setOngoing(!Offline.paused())
            .setOnlyAlertOnce(true)
            .addAction(0, if (Offline.paused()) "Resume" else "Pause",
                       press(if (Offline.paused()) "resume" else "pause"))
            .build()
        if (intent?.action == "pause") {
            Offline.pause(applicationContext)
            Offline.onLine = null
            androidx.core.app.ServiceCompat.stopForeground(
                this, androidx.core.app.ServiceCompat.STOP_FOREGROUND_DETACH)
            runCatching { nm.notify(7, note(Offline.line())) }
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == "resume") Offline.resume(applicationContext)
        val n = note(Offline.line())
        // the title and how far, as it moves
        Offline.onLine = { text -> runCatching { nm.notify(7, note(text)) } }
        runCatching {
            androidx.core.app.ServiceCompat.startForeground(
                this, 7, n,
                if (android.os.Build.VERSION.SDK_INT >= 29)
                    android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0)
        }
        scope.launch {
            runCatching { Offline.work(applicationContext) }
            stopSelf()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        Offline.onLine = null
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()
        super.onDestroy()
    }
}
