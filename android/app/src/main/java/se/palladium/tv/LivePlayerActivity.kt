package se.palladium.tv

import android.os.Bundle
import android.view.KeyEvent
import android.view.WindowManager
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * A live channel, full screen, until Back. The server copies the channel into HLS
 * pieces of its own; this plays them - no seeking, no resume, nothing kept.
 *
 * Up and Down change channel through the playlist, in its order. Center shows what the
 * stream is - picture, sound, buffer, dropped frames - and the group the channels are
 * narrowed to, changed with Left and Right and taken with Center.
 */
class LivePlayerActivity : androidx.appcompat.app.AppCompatActivity() {
    private var player: ExoPlayer? = null
    private val ticks = android.os.Handler(android.os.Looper.getMainLooper())
    private lateinit var info: android.widget.TextView
    private lateinit var name: android.widget.TextView

    private var base = ""
    private var token = ""
    /** the house's other machine, with the same channels: (address, token) */
    private var twin: Pair<String, String>? = null
    //: how many times in a row the channel has moved machines
    private var moves = 0
    /** the channels Up and Down step through: (key, title, group) */
    private var channels: List<Triple<String, String, String>> = emptyList()
    private var at = 0
    private var key = ""
    private var title = ""
    /** the groups to narrow to, "" first for all of them, and the one chosen now */
    private var groups: List<String> = listOf("")
    private var groupShown = 0
    private var groupKept = 0
    private var fpsSaid = 0f
    private var dropped = 0
    private var startedAt = 0L
    private var stalls = 0
    private var wasReady = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        base = intent.getStringExtra("base") ?: ""
        token = intent.getStringExtra("token") ?: ""
        key = intent.getStringExtra("key") ?: ""
        title = intent.getStringExtra("title") ?: ""
        Thread {
            twin = runCatching {
                Route.liveTwin(Servers.all(this), base)?.let {
                    Api.addressOf(it) to it.token.ifEmpty { Api.token }
                }
            }.getOrNull()
        }.start()
        val view = PlayerView(this)
        fun label(gravity: Int) = android.widget.TextView(this).apply {
            setTextColor(android.graphics.Color.WHITE)
            textSize = 16f
            setPadding(28, 16, 28, 16)
            setBackgroundColor(0xB0000000.toInt())
            visibility = android.view.View.GONE
        }.also { lbl ->
            lbl.tag = gravity
        }
        // nothing on the picture until it is asked for: the stream's facts on Center,
        // and the channel's name for a moment when it changes
        info = label(android.view.Gravity.TOP or android.view.Gravity.START)
        name = label(android.view.Gravity.TOP or android.view.Gravity.END)
        val box = android.widget.FrameLayout(this)
        box.addView(view)
        for (lbl in listOf(info, name)) {
            box.addView(lbl, android.widget.FrameLayout.LayoutParams(
                android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
                android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
                lbl.tag as Int).apply { setMargins(48, 40, 48, 0) })
        }
        // a phone's finger: a tap is Center, a swipe changes channel
        val touch = android.view.View(this)
        val gestures = android.view.GestureDetector(this,
            object : android.view.GestureDetector.SimpleOnGestureListener() {
                override fun onDown(e: android.view.MotionEvent) = true
                override fun onSingleTapUp(e: android.view.MotionEvent): Boolean {
                    press(); return true
                }
                override fun onFling(e1: android.view.MotionEvent?, e2: android.view.MotionEvent,
                                     vx: Float, vy: Float): Boolean {
                    val from = e1 ?: return false
                    swiped(e2.x - from.x, e2.y - from.y)
                    return true
                }
            })
        touch.setOnTouchListener { v, e -> gestures.onTouchEvent(e).also { if (e.action == android.view.MotionEvent.ACTION_UP) v.performClick() } }
        box.addView(touch, android.widget.FrameLayout.LayoutParams(
            android.view.ViewGroup.LayoutParams.MATCH_PARENT,
            android.view.ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(box)
        val made = ExoPlayer.Builder(this).build()
        player = made
        view.player = made
        view.useController = false
        made.addListener(object : Player.Listener {
            // a stall, once it was playing: into the server's log, with what was in hand
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY && !wasReady) {
                    // how long from the press to the picture, into the server's log
                    val line = Route.liveStartLine(title,
                        android.os.SystemClock.elapsedRealtime() - startedAt)
                    android.util.Log.i("PalladiumLive", line)
                    lifecycleScope.launch { runCatching { Api.trace(line) } }
                }
                if (state == Player.STATE_READY) {
                    wasReady = true
                    recoveries = 0            // playing again: a fresh allowance
                    moves = 0
                }
                if (state == Player.STATE_BUFFERING || state == Player.STATE_READY) showLoading()
                if (state == Player.STATE_BUFFERING && wasReady) {
                    stalls++
                    val p = player ?: return
                    val line = Route.liveStallLine(title,
                        android.os.SystemClock.elapsedRealtime() - startedAt,
                        p.totalBufferedDuration, p.currentLiveOffset, stalls)
                    android.util.Log.i("PalladiumLive", line)
                    lifecycleScope.launch { runCatching { Api.trace(line) } }
                }
            }

            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                val code = (error.cause as? androidx.media3.datasource.HttpDataSource
                    .InvalidResponseCodeException)?.responseCode ?: 0
                // Fallen behind the live window, or the network gone for a moment:
                // back to the live edge and on, not a frozen picture until Back
                when (Route.liveRecover(error.errorCode, code, recoveries, moves, twin != null)) {
                    "move" -> {
                        // this machine stopped answering, or will not start it: the
                        // same channel from the other one, which can be moved back from
                        val (there, theirs) = twin ?: return
                        twin = base to token
                        base = there
                        token = theirs
                        moves++
                        recoveries = 0
                        val line = "live moved: $title to $there after error ${error.errorCode}" +
                            (if (code > 0) " ($code)" else "")
                        android.util.Log.i("PalladiumLive", line)
                        lifecycleScope.launch { runCatching { Api.trace(line) } }
                        ticks.postDelayed({ if (player === made) tune(key, title) }, 500)
                        return
                    }
                    "edge" -> {
                        recoveries++
                        android.util.Log.i("PalladiumLive", "behind the live window: to the edge")
                        made.seekToDefaultPosition()
                        made.prepare()
                        return
                    }
                    "retry" -> {
                        recoveries++
                        android.util.Log.i("PalladiumLive", "stream error " + error.errorCode + ": again")
                        ticks.postDelayed({
                            if (player === made) { made.seekToDefaultPosition(); made.prepare() }
                        }, 2000)
                        return
                    }
                }
                Route.liveRefused(code)?.let {
                    android.widget.Toast.makeText(this@LivePlayerActivity, it,
                                                  android.widget.Toast.LENGTH_LONG).show()
                    // with other channels to go to, stay: Up or Down tries the next
                    if (channels.size < 2) finish()
                }
            }
        })
        made.addAnalyticsListener(object : androidx.media3.exoplayer.analytics.AnalyticsListener {
            override fun onDroppedVideoFrames(
                eventTime: androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime,
                droppedFrames: Int, elapsedMs: Long) {
                dropped += droppedFrames
            }
        })
        made.playWhenReady = true
        tune(key, title)
        // the playlist in its order, and its groups, for Up, Down and the filter
        Thread { readChannels() }.start()
    }

    private fun address(k: String) = Route.liveAddress(base, k, token)

    /** Play one channel: its frame rate asked first, the screen set to match, then the
     *  player - 50 frames on a 59.94 Hz screen hold every fifth one twice. */
    /** What the channel left did, into the server's log: how long, how many stalls. */
    private fun sayLeft() {
        if (key.isEmpty() || startedAt == 0L) return
        val line = Route.liveLeftLine(title, android.os.SystemClock.elapsedRealtime() - startedAt,
                                      wasReady, stalls, dropped)
        android.util.Log.i("PalladiumLive", line)
        lifecycleScope.launch { runCatching { Api.trace(line) } }
    }

    //: the corner while a channel loads or stalls: how long, and the seconds in hand
    private val loadingTick = object : Runnable {
        override fun run() {
            val p = player ?: return
            val waiting = !wasReady || p.playbackState == Player.STATE_BUFFERING
            if (waiting) {
                name.text = title + "\n" + Route.liveLoadingLine(
                    android.os.SystemClock.elapsedRealtime() - waitFrom, p.totalBufferedDuration)
                name.visibility = android.view.View.VISIBLE
                ticks.removeCallbacks(hideName)
                ticks.postDelayed(this, 250)
            } else {
                name.text = title
                ticks.removeCallbacks(hideName)
                ticks.postDelayed(hideName, 1500)
            }
        }
    }
    private var waitFrom = 0L
    //: how many times in a row the stream has been picked up after an error
    private var recoveries = 0

    private fun showLoading() {
        val p = player ?: return
        if (!wasReady || p.playbackState == Player.STATE_BUFFERING) {
            if (waitFrom == 0L || wasReady) waitFrom = android.os.SystemClock.elapsedRealtime()
            ticks.removeCallbacks(loadingTick)
            ticks.post(loadingTick)
        }
    }

    private fun tune(k: String, t: String) {
        val made = player ?: return
        sayLeft()
        val was = key
        key = k
        title = t
        dropped = 0
        made.stop()
        // twenty seconds behind the edge, at the speed it is sent: the server starts each
        // channel half a minute back, so that much is in hand, and catching up to the
        // edge spent it and stalled
        made.setMediaItem(MediaItem.Builder().setUri(address(k))
                              .setMimeType(MimeTypes.APPLICATION_M3U8)
                              .setLiveConfiguration(MediaItem.LiveConfiguration.Builder()
                                  .setTargetOffsetMs(Route.LIVE_BEHIND_MS)
                                  .setMinPlaybackSpeed(1f).setMaxPlaybackSpeed(1f).build())
                              .build())
        startedAt = android.os.SystemClock.elapsedRealtime()
        stalls = 0
        // a channel's first load is no stall: counted from when it has played
        wasReady = false
        // and the corner counts while it loads
        waitFrom = startedAt
        ticks.removeCallbacks(loadingTick)
        ticks.post(loadingTick)
        flash(t)
        Thread {
            // the channel left gives its connection back now, not a minute from now
            if (was.isNotEmpty() && was != k) runCatching { ask("$base/live/$was/stop") }
            val fps = runCatching {
                val c = open(address(k))
                try { c.inputStream.use { it.readBytes() }; c.getHeaderField("X-Palladium-Fps") }
                finally { c.disconnect() }
            }.getOrNull()?.toFloatOrNull() ?: 0f
            runOnUiThread {
                if (isFinishing || player !== made || key != k) return@runOnUiThread
                fpsSaid = fps
                matchRate(fps)
                made.prepare()
            }
        }.start()
    }

    private fun open(url: String): java.net.HttpURLConnection {
        val c = java.net.URL(url).openConnection() as java.net.HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = 20000
        if (token.isNotEmpty()) c.setRequestProperty("X-Palladium-Token", token)
        // the kind of screen: a television's filter is its own
        c.setRequestProperty("X-Palladium-App", Api.appName())
        return c
    }

    private fun ask(url: String): String {
        val c = open(url)
        try { return c.inputStream.use { String(it.readBytes()) } } finally { c.disconnect() }
    }

    private fun readChannels() {
        val list = runCatching {
            val rows = JSONObject(ask("$base/local/library/live/own?start=0&count=5000&order=list"))
                .getJSONObject("MediaContainer").optJSONArray("Metadata") ?: JSONArray()
            (0 until rows.length()).map { rows.getJSONObject(it) }.map { o ->
                Triple(o.optString("ratingKey"), o.optString("title"),
                       o.optJSONArray("genres")?.optString(0) ?: "")
            }
        }.getOrDefault(emptyList())
        val named = runCatching {
            val rows = JSONObject(ask("$base/local/library/live/own/groups?all=1"))
                .getJSONObject("MediaContainer").optJSONArray("Metadata") ?: JSONArray()
            (0 until rows.length()).map { rows.getJSONObject(it) }
        }.getOrDefault(emptyList())
        runOnUiThread {
            channels = list
            at = list.indexOfFirst { it.first == key }.coerceAtLeast(0)
            groups = listOf("") + named.map { it.optString("title") }
            // the group kept now: the only one marked, else all of them
            val kept = named.filter { it.optBoolean("kept") }
            groupKept = if (kept.size == 1) groups.indexOf(kept[0].optString("title")) else 0
            groupShown = groupKept
        }
    }

    /** Ask for the display mode that shows `fps` evenly; the window's own wish, which
     *  ends with the window. */
    private fun matchRate(fps: Float) {
        val d = (if (android.os.Build.VERSION.SDK_INT >= 30) display else null)
            ?: @Suppress("DEPRECATION") windowManager.defaultDisplay
        val now = d.mode
        fun mode(m: android.view.Display.Mode) =
            Route.Mode(m.modeId, m.physicalWidth, m.physicalHeight, m.refreshRate)
        val id = Route.modeFor(d.supportedModes.map { mode(it) }, mode(now), fps) ?: return
        window.attributes = window.attributes.apply { preferredDisplayModeId = id }
    }

    /** The channel's name, for a few seconds. */
    private fun flash(t: String) {
        name.text = t
        name.visibility = android.view.View.VISIBLE
        ticks.removeCallbacks(hideName)
        ticks.postDelayed(hideName, 3000)
    }
    private val hideName = Runnable { name.visibility = android.view.View.GONE }

    private val refresh = object : Runnable {
        override fun run() {
            if (info.visibility != android.view.View.VISIBLE) return
            info.text = facts()
            ticks.postDelayed(this, 1000)
        }
    }
    private val hideInfo = Runnable { info.visibility = android.view.View.GONE }

    private fun facts(): String {
        val p = player ?: return ""
        val v = p.videoFormat
        val a = p.audioFormat
        val d = (if (android.os.Build.VERSION.SDK_INT >= 30) display else null)
            ?: @Suppress("DEPRECATION") windowManager.defaultDisplay
        val group = channels.getOrNull(at)?.third ?: ""
        return Route.liveFacts(
            title = title, group = group,
            width = v?.width ?: 0, height = v?.height ?: 0,
            fps = if ((v?.frameRate ?: -1f) > 0f) v!!.frameRate else fpsSaid,
            videoCodec = v?.codecs ?: v?.sampleMimeType ?: "",
            bitrate = maxOf(v?.bitrate ?: 0, v?.averageBitrate ?: 0, v?.peakBitrate ?: 0),
            audioCodec = a?.codecs ?: a?.sampleMimeType ?: "",
            sampleRate = a?.sampleRate ?: 0, channels = a?.channelCount ?: 0,
            bufferedMs = p.totalBufferedDuration,
            waiting = p.playbackState == Player.STATE_BUFFERING,
            dropped = dropped, screenHz = d.mode.refreshRate,
            place = at + 1, of = channels.size,
            filter = groups.getOrElse(groupShown) { "" },
            filterChanged = groupShown != groupKept,
            more = moreFacts)
    }

    //: what the server says of this channel beyond picture and sound - for a YouTube
    //: stream its source, the show and how long it has been on - and for which channel
    @Volatile private var moreFacts: List<String> = emptyList()
    @Volatile private var moreFor = ""

    private fun askMoreFacts() {
        val k = key
        if (k.isEmpty() || moreFor == k) return
        moreFor = k
        Thread {
            val lines = runCatching {
                val o = JSONObject(ask("$base/local/library/live/own/facts?key=" + k))
                    .getJSONObject("MediaContainer").optJSONArray("Metadata")
                    ?.optJSONObject(0)?.optJSONArray("lines") ?: JSONArray()
                (0 until o.length()).map { o.optString(it) }.filter { it.isNotEmpty() }
            }.getOrDefault(emptyList())
            runOnUiThread {
                if (key == k) {
                    moreFacts = lines
                    if (info.visibility == android.view.View.VISIBLE) info.text = facts()
                }
            }
        }.start()
    }

    private fun showInfo() {
        // asked again for each channel: a stream's watching count changes
        if (moreFor != key) moreFacts = emptyList()
        askMoreFacts()
        info.text = facts()
        info.visibility = android.view.View.VISIBLE
        ticks.removeCallbacks(refresh)
        ticks.postDelayed(refresh, 1000)
        ticks.removeCallbacks(hideInfo)
        ticks.postDelayed(hideInfo, 12000)
    }

    /** Take the group shown in the panel: the server keeps it, the row and these
     *  channels both follow. */
    private fun keepGroup() {
        val want = groups.getOrElse(groupShown) { "" }
        Thread {
            val ok = runCatching {
                val c = open("$base/settings")
                c.requestMethod = "POST"
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use {
                    it.write(JSONObject().put("liveGroups",
                        if (want.isEmpty()) JSONArray() else JSONArray().put(want))
                        .put("liveScreen", Api.device)
                        .toString().toByteArray())
                }
                try { c.responseCode in 200..299 } finally { c.disconnect() }
            }.getOrDefault(false)
            runOnUiThread {
                if (!ok) {
                    android.widget.Toast.makeText(this, "The filter could not be kept",
                                                  android.widget.Toast.LENGTH_LONG).show()
                    return@runOnUiThread
                }
                groupKept = groupShown
                Thread { readChannels() }.start()
                if (info.visibility == android.view.View.VISIBLE) info.text = facts()
            }
        }.start()
    }

    private val infoOpen get() = info.visibility == android.view.View.VISIBLE

    /** The next channel up or down the playlist. */
    private fun zapBy(step: Int) {
        if (channels.isEmpty()) return
        at = Route.zapIn(channels.map { it.third }, at, step)
        val (k, t, _) = channels[at]
        tune(k, t)
        if (infoOpen) showInfo()
    }

    /** The next group in the open panel. */
    private fun groupBy(step: Int) {
        if (!infoOpen) return
        groupShown = Route.zap(groupShown, groups.size, step)
        showInfo()
    }

    /** Center, or a tap: the panel opened; a group changed in it kept; else closed. */
    private fun press() {
        if (infoOpen && groupShown != groupKept) keepGroup()
        else if (infoOpen) info.visibility = android.view.View.GONE
        else showInfo()
    }

    /** A phone has no Up and Down: a swipe up or to the left is the next channel, down
     *  or to the right the one before. With the panel open, sideways is its group. */
    private fun swiped(dx: Float, dy: Float) {
        when (Route.liveSwipe(dx, dy, infoOpen)) {
            "next" -> zapBy(1)
            "prev" -> zapBy(-1)
            "groupNext" -> groupBy(1)
            "groupPrev" -> groupBy(-1)
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        when (keyCode) {
            KeyEvent.KEYCODE_BACK -> {
                if (infoOpen) info.visibility = android.view.View.GONE else finish()
                return true
            }
            KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_CHANNEL_UP -> { zapBy(1); return true }
            KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_CHANNEL_DOWN -> { zapBy(-1); return true }
            KeyEvent.KEYCODE_DPAD_LEFT -> { groupBy(-1); return true }
            KeyEvent.KEYCODE_DPAD_RIGHT -> { groupBy(1); return true }
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> { press(); return true }
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onStop() {
        super.onStop()
        sayLeft()
        ticks.removeCallbacksAndMessages(null)
        val left = key
        if (left.isNotEmpty()) Thread { runCatching { ask("$base/live/$left/stop") } }.start()
        player?.release()
        player = null
        finish()
    }
}
