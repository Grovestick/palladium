package se.palladium.tv

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import androidx.media3.cast.CastPlayer
import androidx.media3.cast.SessionAvailabilityListener
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import com.google.android.gms.cast.framework.CastContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Playback, locally or on the television.
 *
 * Two shapes of stream arrive here:
 *   direct    the file itself, seekable by byte range - seek locally, nothing else to do
 *   engine    a live ffmpeg encode that begins at the requested offset, so its own clock
 *             starts at zero and the offset has to be added back for progress reporting
 *
 * Casting swaps the player underneath the same controls: the receiver fetches the very
 * same URL from the server, so the phone is not in the video path at all.
 */
/**
 * The film itself.
 *
 * An AppCompatActivity rather than a plain one for a single reason: MediaRouteButton
 * opens its chooser through a fragment manager and throws without one, which took the
 * whole player down when the cast button was pressed. Compose does not mind either way.
 */
//: The ink behind the strip along the top. One value, and painted once: the
//: information line carries it for the whole width, and the buttons sitting on that
//: line have no ground of their own - two translucent blacks over each other is a
//: darker patch where they overlap, which is exactly what it looks like.
private const val TOP_BAR_INK = 0x99000000.toInt()

/**
 * What 100% means: the text is this much of the height of the screen it is drawn on,
 * whatever the resolution of the film or of the television.
 *
 * The same number is in pd-server.py as SUB_BASE, in the web client as SUB_BASE_VH and
 * in the burn-in filter, because each of them draws its own text. It is 5% because that
 * is where both standards sit - WebVTT's own default, and a hair under CEA-608's 5.33%,
 * which is what Media3 uses and what made a size chosen on a laptop come out nearly
 * twice as large here.
 */
private const val SUB_BASE = 0.05f

/**
 * Daylight under the last line, as a share of the height.
 *
 * A cue on the bottom row is laid out flush with the bottom of the view, which on a
 * television is the edge of the panel: the tails of y, j and g land on the last row of
 * pixels, and anything the set overscans takes them off altogether. One per cent is
 * enough to clear the edge and too little to read as a gap. The browser leaves the
 * same room - CUE_AIR in static/app.js.
 */
private const val SUB_AIR = 0.01f

//: How tall a row of subtitle is as a multiple of the text size - what the player does
//: with the leading, not a choice. The browser measured the same 1.32.
private const val SUB_LEADING = 1.32f

class PlayerActivity : androidx.appcompat.app.AppCompatActivity() {

    companion object {
        /**
         * The closing report, while it is still in the air.
         *
         * The screen behind a film reads Continue watching again the moment it comes
         * back, and that read was racing this - and usually winning, so the shelf was
         * drawn from the position the server held a moment before the film ended.
         */
        @Volatile
        @JvmStatic
        var settling: kotlinx.coroutines.Job? = null

        /**
         * The film that just ran to its end, for the page it came from.
         *
         * A film that finishes has nowhere to go - no next episode, no draw from a
         * hat - so it closes and leaves somebody looking at the top of the page they
         * started from. What they want next is on the same page, further down.
         */
        @Volatile
        @JvmStatic
        var ranOut: String = ""
    }

    private var local: ExoPlayer? = null
    private var cast: CastPlayer? = null
    private var view: PlayerView? = null
    private var direct = true
    private var baseOffsetSec = 0L
    /** where a stream with its picture copied really starts, in ms; -1 when not copied */
    private var copyStartMs = -1L
    private var ratingKey = ""
    /** which copy of the title is playing, when the library holds more than one */
    private var mi = 0
    private var durationMs = 0L
    private lateinit var item: MediaItem
    private lateinit var castItem: MediaItem
    private var castDirect = true
    private var infoBar: android.widget.TextView? = null
    private var streamUrl = ""
    private var wrapper: OffsetPlayer? = null
    private var sourceFacts = ""
    private var srvBase = ""
    private var srvToken = ""
    private var subLook = Api.SubLook(1f, 0.08f, "white", "shadow")
    private val stylingOpen = androidx.compose.runtime.mutableStateOf(false)
    // the subtitle panel over the picture, and the download list behind it
    private val tracksOpen = androidx.compose.runtime.mutableStateOf(false)
    private val soundOpen = androidx.compose.runtime.mutableStateOf(false)
    private val qualityOpen = androidx.compose.runtime.mutableStateOf(false)

    /** One panel at a time: opening a second used to leave the first behind it. */
    private fun onlyOne(wanted: androidx.compose.runtime.MutableState<Boolean>) {
        for (one in listOf(tracksOpen, soundOpen, qualityOpen, stylingOpen)) {
            if (one !== wanted) one.value = false
        }
        wanted.value = true
    }
    //: on a phone, the three controls that live in the top corner rather than the
    //: transport row; they hide and show with the rest of the controls
    private var cornerRow: android.view.View? = null
    //: whether the sound has already been asked for a second time, in AAC, because
    //: this device could not decode what it was first given
    private var triedPlainAudio = false
    //: whether this playing has already described itself to the server
    private var told = false
    //: subtitles moved by hand, in seconds; positive is later
    private val subNudge = androidx.compose.runtime.mutableStateOf(0f)

    /** Analysing the film to place the subtitle, and whether to do it every time. */
    private val syncing = androidx.compose.runtime.mutableStateOf(false)
    private val autoSync = androidx.compose.runtime.mutableStateOf(false)
    /** whether the measurement running now was asked for, or is the app's own doing */
    private val syncAsked = androidx.compose.runtime.mutableStateOf(false)
    /** what came of it, shown where the waiting was shown and then let go */
    private val syncSaid = androidx.compose.runtime.mutableStateOf("")
    /**
     * What the party has said lately, and when each line should go.
     *
     * A conversation is lines stacking up rather than one sentence replacing the
     * last, so they are held as a list in the corner and each takes itself away when
     * its time is up. Ten seconds is long enough to read a line and short enough that
     * the picture is not a chat window.
     */
    private val roomSaid = androidx.compose.runtime.mutableStateListOf<Room>()

    data class Room(val id: Int, val who: String, val text: String, val until: Long)
    /** the measurement in flight, so Reset can call it off */
    private var syncJob: kotlinx.coroutines.Job? = null

    /** What the last measurement decided, in words, for the CC menu to show. */
    private val syncNote = androidx.compose.runtime.mutableStateOf("")

    /** Whether the file being served is already mended in parts, rather than plain. */
    private val mended = androidx.compose.runtime.mutableStateOf(false)
    /** and which kind it is: a reader is owed the word, not only the fact */
    private val inForce = androidx.compose.runtime.mutableStateOf("")
    private var nudgeSoon: kotlinx.coroutines.Job? = null
    //: paused because the headphones went, and waiting for them to come back
    private var waitingForEars = false
    //: a silent stream that keeps a Bluetooth link open while nothing is playing
    private var hum: android.media.AudioTrack? = null
    private var humming = false

    /**
     * Keep the headphones connected while the film is paused.
     *
     * A Bluetooth link with nothing going down it is closed by the box within
     * seconds, and the headphones then go looking for something else to attach
     * themselves to - which on a pause of half a minute means hunting for them again
     * afterwards. So while the film is paused and the sound is going to a pair of
     * headphones, a silent stream is written instead: no sound, but a live link.
     *
     * Only while this screen is up. A paused film in the background has no business
     * holding anybody's headphones.
     */
    /**
     * A read that breaks is asked again, quietly, while the buffer plays on.
     *
     * A server being replaced is back in a few seconds and the player is holding
     * more than that, so nothing needs to reach the viewer at all. Media3 gives up
     * after three tries across three seconds, which is shorter than a restart - so
     * the picture stopped and the film was started again at the same second, which
     * is a black moment for something the viewer never had to know about. What is
     * still failing a minute later is a server that has gone, and that falls through
     * to the player as before: one restart, then the machine that keeps copies.
     */
    private fun patientLoading() =
        object : androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy() {
            override fun getRetryDelayMsFor(
                info: androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy.LoadErrorInfo,
            ): Long {
                val why = info.exception
                val status = why as?
                    androidx.media3.datasource.HttpDataSource.InvalidResponseCodeException
                // a server on its way up answers 502 or 503 for a moment; a 404 is an
                // answer and asking again will not change it
                val worthAsking = status == null || status.responseCode in 500..504
                return if (worthAsking && why is java.io.IOException && info.errorCount <= 45)
                    1_000L
                else super.getRetryDelayMsFor(info)
            }

            override fun getMinimumLoadableRetryCount(dataType: Int): Int = 46
        }

    private fun keepLinkWarm(wanted: Boolean) {
        if (wanted && !humming && throughHeadphones() && !onCastNow()) {
            val rate = 48000
            val least = android.media.AudioTrack.getMinBufferSize(
                rate, android.media.AudioFormat.CHANNEL_OUT_STEREO,
                android.media.AudioFormat.ENCODING_PCM_16BIT)
            val track = runCatching {
                android.media.AudioTrack.Builder()
                    .setAudioAttributes(android.media.AudioAttributes.Builder()
                        .setUsage(android.media.AudioAttributes.USAGE_MEDIA)
                        .setContentType(
                            android.media.AudioAttributes.CONTENT_TYPE_MOVIE)
                        .build())
                    .setAudioFormat(android.media.AudioFormat.Builder()
                        .setEncoding(android.media.AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(rate)
                        .setChannelMask(android.media.AudioFormat.CHANNEL_OUT_STEREO)
                        .build())
                    .setBufferSizeInBytes(maxOf(least, rate))
                    .build()
            }.getOrNull() ?: return
            hum = track
            humming = true
            runCatching {
                track.setVolume(0f)
                track.play()
            }
            Thread {
                val quiet = ShortArray(1024)
                // Long enough to cover a pause, not long enough to squat on somebody's
                // headphones: a pair connected to a phone as well will go back to the
                // phone if that is what it wants, and a film left paused for an hour
                // should not be holding them.
                val until = android.os.SystemClock.elapsedRealtime() + 90_000
                while (humming && android.os.SystemClock.elapsedRealtime() < until) {
                    val wrote = runCatching { track.write(quiet, 0, quiet.size) }
                        .getOrDefault(-1)
                    if (wrote < 0) break
                }
                humming = false
                runCatching { track.stop() }
                runCatching { track.release() }
            }.also { it.isDaemon = true }.start()
        } else if (!wanted && humming) {
            humming = false            // the thread stops and lets the track go
            hum = null
        }
    }

    /**
     * The headphones have gone.
     *
     * Android calls it "audio becoming noisy" - the sound is about to come out of a
     * speaker instead - and sends it whether a plug was pulled or a Bluetooth pair
     * walked out of range. A film should not carry on playing to an empty room, and
     * whoever put the headphones back on should not have to hunt for their place.
     */
    private val earsWentAway = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: android.content.Context?,
                               intent: android.content.Intent?) {
            val p = current() ?: return
            if (!p.isPlaying) return
            waitingForEars = true
            p.pause()
            view?.showController()
            android.widget.Toast.makeText(
                this@PlayerActivity, "Headphones gone - waiting for them",
                android.widget.Toast.LENGTH_SHORT).show()
        }
    }
    //: what the outside world talks to: headphone buttons, a car, a watch
    private var session: androidx.media3.session.MediaSession? = null
    //: what the server allows from where this device is watching, in words
    private val qualityCap = androidx.compose.runtime.mutableStateOf("")
    //: and the same thing as numbers, for marking what cannot be chosen
    private val capNow = androidx.compose.runtime.mutableStateOf(Pair(0, 0))
    private val gettingSubs = androidx.compose.runtime.mutableStateOf(false)
    //: whether the next episode's subtitle is fetched before it starts: the server's
    //: setting, shown beside Download because that is where somebody thinks about it
    private val fetchAhead = androidx.compose.runtime.mutableStateOf(true)
    // never equal: what changes about a title while it plays - its download, its
    // place - lives in Media's body, which its equals() does not look at
    private val title = androidx.compose.runtime.mutableStateOf<Media?>(
        null, androidx.compose.runtime.neverEqualPolicy())
    // the episode after this one, and the seconds left before it starts itself
    private val nextUp = androidx.compose.runtime.mutableStateOf<Media?>(null)
    //: a draw asked for and not yet answered: the picture is held over until it is
    private val drawing = androidx.compose.runtime.mutableStateOf(false)
    private val countdown = androidx.compose.runtime.mutableIntStateOf(0)
    private var autoNext = true
    //: how long the card waits before starting the next episode itself
    private var nextWait = 5
    private var showKey = ""
    private var prefetched = false          // the next episode's subtitles, once
    private var retried = 0                 // recoveries from a stream that dropped
    // moved to the machine keeping copies: once per film, or a server that is off
    // would send the viewer round in circles
    private var handedOver = false
    //: when the film last moved machines. Moving is once-only for a minute, not for
    //: the rest of the film: a picture that stopped on the machine it had moved to
    //: sat frozen, with the machine it came from answering all the while.
    private var movedAt = 0L
    //: whether the server is being asked, this moment, whether it is still there
    @Volatile private var asking = false
    /**
     * The same film on the machine that keeps copies, found before it is needed.
     *
     * Looking it up at the moment the stream dies costs a request and two failed
     * retries with the picture frozen. Looked up while everything is well, the switch
     * is a seek to the same second on another address.
     */
    private var ready: Media? = null
    //: every other machine that has this film, as of the last look round
    private var readies: List<Media> = emptyList()
    private var switching = false
    /** the thing that reads the film, off one machine or off two */
    private var ways: TwoWaysFactory? = null
    /** seconds in a row with almost nothing buffered, and seconds frozen */
    private var thin = 0
    private var stopped = 0
    private var looking = false
    private var askedAt = 0L
    /** when this screen last said how much film it had in hand */
    private var saidBuffer = 0L

    /** whether this playing has already said how its sound is being handled */
    private var saidHowSound = false

    /** error codes already reported for this playing, so a retry loop says it once */
    private val saidFaults = mutableSetOf<String>()

    /** How much the sound of this file is lifted or held back, in decibels. */
    private val gain = Gain()

    /** Set from what the server measured, each time a film is opened. */
    private fun evenTheVolume() {
        // last film's numbers are not this one's
        Api.gainSaid = null
        Api.lufsSaid = null
        gain.decibels = intent.getFloatExtra("gainDb", 0f)
        if (gain.decibels != 0f) {
            log("evening the sound by " + gain.decibels + " dB")
        }
    }
    /**
     * Whether the copy already held this film when it started playing.
     *
     * Only a copy made while somebody is watching is worth saying out loud: one that
     * was already there is not news, and a notice for it is a notice nobody asked
     * for at the start of every film.
     */
    private var hadItAtStart: Boolean? = null
    private var toldTaking = false
    private var toldTaken = false
    private var lookedAt = 0L
    /**
     * The furthest into the film this playing has actually reached.
     *
     * A player whose stream is pulled from under it can report nought: the timeline
     * it was holding is gone. Asking it where it was then gives the beginning, and
     * starting again there means watching the first three minutes over and over,
     * which is exactly what a restarted server did to somebody all one evening.
     */
    private var lastGood = 0L
    /** where the last fault happened, so a long clean run earns its retries back */
    private var faultAt = 0L
    private var subsAttached = false        // a subtitle file riding with the video
    private var nameBar: android.widget.TextView? = null
    private var season = 0
    private var number = 0
    private var decoderName = ""
    private var decodedSize = ""
    private var fps = ""                    // what the stream says, when it says anything
    private var framesShown = 0L            // and what is actually reaching the screen
    private var frameMark = Pair(0L, 0L)    // when we last looked, and the count then
    private var audioDecoder = ""
    private var droppedFrames = 0
    private var bytesLoaded = 0L
    // (wall clock, bytes) samples, trimmed to ten seconds: throughput now, not on average
    private val transfer = ArrayDeque<Pair<Long, Long>>()
    private val startedAt = android.os.SystemClock.elapsedRealtime()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        goImmersive()
        // The player can be started without the library screen ever having run - a
        // fresh process, a casual draw, the next episode - and the subtitle look is
        // kept per screen. A television that thinks it is a phone reads and writes
        // the wrong one, which is why a height chosen here did not stick.
        Api.learnDevice(this)
        // a word from the house reaches whoever is watching, not only whoever is
        // looking at the shelves
        listenForNotices()
        listenToTheRoom()
        wantsTheMade.value = intent.getBooleanExtra("wantMade", false)
        // Something may already be writing a subtitle for this film - started from the
        // page rather than from here. Follow it, and put it on as soon as there is a
        // line in it, whatever else is showing.
        lifecycleScope.launch {
            // The title is fetched when a panel is opened and not before, so this
            // asks for it rather than waiting for something else to.
            val key = intent.getStringExtra("key") ?: return@launch
            val film = title.value?.takeIf { it.ratingKey == key }
                ?: runCatching { Api.item(key, null) }.getOrNull() ?: return@launch
            // and it keeps asking: a subtitle can be started from a phone while the
            // film is already playing here
            while (true) {
                val said = runCatching { Api.makingSubtitles(film) }.getOrNull()
                if (said?.on == true) {
                    followTheMaking(film)
                    return@launch
                }
                kotlinx.coroutines.delay(30_000)
            }
        }

        val url = intent.getStringExtra("url") ?: return finish()
        // Headphones cannot be handed Dolby: nothing passes through a Bluetooth pair,
        // so the box has to decode it, and a television box frequently cannot. Ask
        // for AAC from the start rather than starting in silence and correcting it.
        streamUrl = if (throughHeadphones()) plainSound(url) else url
        // asked for in plain stereo already: do not go round again if it is still
        // silent, which would be a restart per attempt for ever
        triedPlainAudio = soundIsPlain()
        direct = intent.getBooleanExtra("direct", true)
        // Where to go if this server stops answering. Learned by the shelves at
        // startup, but a player opened from a notification or a resumed task may be
        // the first thing running, and a film that cannot name the other machine
        // cannot move to it.
        lifecycleScope.launch {
            if (Api.standby.isEmpty() && Api.standbyOut.isEmpty()) {
                kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    runCatching { Api.learnStandby(this@PlayerActivity) }
                }
            }
            log("standby is " + Api.standby + " / " + Api.standbyOut)
            Api.learnEngine(srvBase.ifEmpty { Api.base }, srvToken)
            whatIsPlaying()
        }
        baseOffsetSec = intent.getLongExtra("positionSec", 0)
        ratingKey = intent.getStringExtra("key") ?: ""
        mi = intent.getIntExtra("mi", 0)
        durationMs = intent.getLongExtra("durationMs", 0)
        sourceFacts = intent.getStringExtra("source") ?: ""
        srvBase = intent.getStringExtra("srvBase") ?: Api.base
        srvToken = intent.getStringExtra("srvToken") ?: Api.token
        showKey = intent.getStringExtra("showKey") ?: ""
        season = intent.getIntExtra("season", 0)
        number = intent.getIntExtra("number", 0)

        // a side-loaded WebVTT track: the server extracted it, the player draws it, and
        // the video stream itself is left alone
        subsAttached = intent.getStringExtra("subsUrl") != null
        val subsName = intent.getStringExtra("subsName") ?: ""
        item = MediaItem.Builder()
            // the file is not handed to Media3: we hold it and time it ourselves, so
            // that correcting the timing does not mean a new media item and a film
            // that starts again
            .setSubtitleConfigurations(emptyList())
            .setUri(url)
            .setMimeType(MimeTypes.VIDEO_MP4)          // the receiver wants to be told
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(intent.getStringExtra("title"))
                    .setSubtitle(intent.getStringExtra("subtitle"))
                    .setArtworkUri(intent.getStringExtra("art")?.let { android.net.Uri.parse(it) })
                    .build())
            .build()

        // a picture copied as it is starts on the keyframe before the second asked for
        if (askWhereItStarts()) item = item.buildUpon().setUri(streamUrl).build()

        // the receiver gets its own URL: it cannot read Matroska, whatever this phone can
        castDirect = intent.getBooleanExtra("castDirect", true)
        castItem = intent.getStringExtra("castUrl")?.let {
            // a playlist has to be announced as one, or the receiver reads the first
            // few bytes, decides it is not a film and gives up
            val kind = if (it.contains("/gpu/hls")) MimeTypes.APPLICATION_M3U8
                       else MimeTypes.VIDEO_MP4
            item.buildUpon().setUri(it).setMimeType(kind).build()
        } ?: item

        // something put on casually always has another draw, and an episode has the
        // ones either side; a film watched on its own has neither
        val steppable = intent.getBooleanExtra("casual", false) ||
            intent.getStringExtra("key").orEmpty().startsWith("e")
        val player = PlayerView(this).also {
            it.keepScreenOn = true
            it.setShowSubtitleButton(true)      // turn them off again without leaving
            // an episode has the ones either side of it, and something put on
            // casually always has another draw; a film on its own has neither, and
            // shows neither rather than showing two dead controls
            // ours are in the row below; Media3's would be a second pair that works
            // from a playlist it has not got
            it.setShowNextButton(false)
            it.setShowPreviousButton(false)
        }
        view = player
        // Media3 keeps its subtitle view inside the picture, which is where a subtitle
        // belongs - until somebody asks for it in the black below the film, and there
        // is no black inside the picture to put it in. Ours covers the whole player,
        // so both answers are reachable; the player's own stays empty, because the
        // text track is turned off and the cues are drawn by us.
        subs = androidx.media3.ui.SubtitleView(this).also {
            it.setApplyEmbeddedStyles(false)
            it.setBottomPaddingFraction(0f)
            player.addView(it, android.widget.FrameLayout.LayoutParams(-1, -1))
        }
        // Draw them our way from the first cue: the look the server keeps takes a
        // request to fetch, and until it arrives the file's own styling and Media3's
        // default size apply - subtitles came up too large and then jumped.
        // this title's own look if it has been drawn before, otherwise the general
        // one for this kind of screen - anything but the built-in default, which is
        // not what most people are watching with
        (Api.lastLook(this, "l" + ratingKey) ?: Api.lastLook(this, ""))
            ?.let { subLook = it }
        applySubtitleLook(subLook)

        // Picture size, the same pair the web player offers. A scope film letterboxes on
        // a 16:9 panel; zoom crops the sides to fill it. Stretch is not on the menu.
        val fitButton = android.widget.TextView(this).apply {
            textSize = 15f                         // the same as "Aa" beside it
            setTextColor(0xFFE8ECF1.toInt())
            setPadding(26, 12, 26, 12)
            isClickable = true
            isFocusable = true                     // the remote has to reach it too
            background = pillBackground(false)
            // a white ring says where the remote is; nothing else on this screen is white
            background = pillBackground(false)
            setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 1f else 0.85f
            }
            setOnClickListener { cycleFit(this) }
        }
        applyFit(fitButton)

        // What is really happening, along the top: source facts, the decoder Android
        // picked, and whether the server is re-encoding. A decoder name starting c2.qti
        // or OMX.qcom is hardware; c2.android is the software fallback.
        infoBar = android.widget.TextView(this).apply {
            // small, because the three buttons share this line with it
            textSize = 10f
            maxLines = 2
            setTextColor(0xFFD3DAE2.toInt())
            setBackgroundColor(TOP_BAR_INK)
            setPadding(16, 6, 16, 6)
            // the buttons in the corner are taller than a line of this: the text sits
            // in the middle of the strip so the two read as one bar
            gravity = android.view.Gravity.CENTER_VERTICAL
            text = buildInfo()
        }

        // The show and the episode, or the film and its year, just above the bar
        nameBar = android.widget.TextView(this).apply {
            textSize = 16f
            maxLines = 2
            setTextColor(0xFFF2F5F8.toInt())
            setShadowLayer(8f, 0f, 2f, 0xE6000000.toInt())
            setPadding(30, 6, 30, 6)
            text = listOf(intent.getStringExtra("title") ?: "",
                          intent.getStringExtra("subtitle") ?: "")
                .filter { it.isNotEmpty() }.joinToString("   \u00b7   ")
        }

        val frame = android.widget.FrameLayout(this)
        // the padded edges show this, and black under a camera is the one thing that
        // looks deliberate rather than missing
        frame.setBackgroundColor(0xFF000000.toInt())
        frame.addView(player, android.widget.FrameLayout.LayoutParams(-1, -1))
        keepClearOfTheHardware(frame, player)
        // Beside the subtitles button, inside Media3's own row, so it lines up with
        // the rest and the remote reaches it in the natural order. If that row cannot
        // be found - a future version renaming its parts - it falls back to floating
        // above the bar rather than disappearing.
        // "Aa", next to CC: how the subtitles look, for this film on this screen
        val lookButton = android.widget.TextView(this).apply {
            text = "Aa"
            textSize = 15f
            setTextColor(0xFFE8ECF1.toInt())
            gravity = android.view.Gravity.CENTER
            isClickable = true
            isFocusable = true
            background = pillBackground(false)
            setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 1f else 0.85f
            }
            setOnClickListener {
                // A picture subtitle carries its size and its place inside the image,
                // so there is nothing here to change. The button stays where it is -
                // faded, and it says why when pressed - rather than vanishing, which
                // would send somebody hunting for a setting they have not lost.
                if (pictureSub() != null) {
                    android.widget.Toast.makeText(
                        this@PlayerActivity,
                        "Bitmap track - size and position come from the file",
                        android.widget.Toast.LENGTH_LONG).show()
                } else {
                    onlyOne(stylingOpen)
                }
            }
            // and a long press opens the subtitles themselves - what is there, what
            // fits, and what else could be fetched
            setOnLongClickListener {
                openSubtitlePanel()
                true
            }
        }

        // and it has to look unusable before it is pressed, not after
        if (pictureSub() != null) {
            lookButton.alpha = 0.45f
            lookButton.setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 0.6f else 0.45f
            }
        }

        // Next and previous, ours rather than Media3's. Its own two buttons work
        // from a playlist it does not have, and finding its views by id is a bet on
        // a layout that changes between versions - when the bet fails the press
        // simply does nothing, which is what was reported.
        val prevButton = android.widget.TextView(this).apply {
            text = "\u23EE"
            textSize = 16f
            setTextColor(0xFFE8ECF1.toInt())
            gravity = android.view.Gravity.CENTER
            isClickable = true
            isFocusable = true
            background = pillBackground(false)
            setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 1f else 0.85f
            }
            setOnClickListener {
                // The first press starts this one again, as on any disc player ever
                // made. Only in the opening ten seconds does it mean the one before,
                // which is when somebody has plainly just arrived here by mistake.
                if (position() > 10_000) restartAt(0) else step(forward = false)
            }
        }
        val nextButton = android.widget.TextView(this).apply {
            text = "\u23ED"
            textSize = 16f
            setTextColor(0xFFE8ECF1.toInt())
            gravity = android.view.Gravity.CENTER
            isClickable = true
            isFocusable = true
            background = pillBackground(false)
            setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 1f else 0.85f
            }
            setOnClickListener { step(forward = true) }
        }

        // Media3 hides its own subtitle button when the stream has no text tracks,
        // which is exactly when somebody wants to open the menu and fetch one. Ours
        // is always here.
        val ccButton = android.widget.TextView(this).apply {
            text = "CC"
            textSize = 15f
            setTextColor(0xFFE8ECF1.toInt())
            gravity = android.view.Gravity.CENTER
            isClickable = true
            isFocusable = true
            background = pillBackground(false)
            setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 1f else 0.85f
            }
            setOnClickListener { openSubtitlePanel() }
        }

        // Which soundtrack: a film with an English track and a French one, or with a
        // commentary, was previously stuck with whichever came first in the file.
        val soundButton = android.widget.TextView(this).apply {
            text = "\u266A"
            textSize = 21f
            setTextColor(0xFFE8ECF1.toInt())
            gravity = android.view.Gravity.CENTER
            isClickable = true
            isFocusable = true
            background = pillBackground(false)
            setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 1f else 0.85f
            }
            setOnClickListener { openSoundPanel() }
        }

        // Picture size and megabits, for a line that cannot carry the film as it is.
        val gearButton = android.widget.TextView(this).apply {
            text = "⚙"
            textSize = 21f
            setTextColor(0xFFE8ECF1.toInt())
            gravity = android.view.Gravity.CENTER
            isClickable = true
            isFocusable = true
            background = pillBackground(false)
            setOnFocusChangeListener { v, has ->
                v.background = pillBackground(has)
                v.alpha = if (has) 1f else 0.85f
            }
            setOnClickListener { openQualityPanel() }
        }

        // as tall as the row they sit in, which is a thumb; the width is the row's
        // business, settled once the row knows how wide it is
        val thumb = (44 * resources.displayMetrics.density).toInt()
        for (b in listOf(gearButton, soundButton, ccButton, lookButton,
                         prevButton, nextButton)) {
            b.minHeight = thumb
        }

        // Media3 draws next and previous but works them from its own playlist, which
        // holds one film - so they would sit there greyed out. The views are ours to
        // point somewhere useful: the episodes either side, or the next draw.
        val ccRow = player.findViewById<android.view.View>(
            androidx.media3.ui.R.id.exo_subtitle)?.parent as? android.view.ViewGroup
        if (ccRow != null) {
            // a ring on a child of this row must not be trimmed by the row
            ccRow.clipChildren = false
            ccRow.clipToPadding = false
            (ccRow.parent as? android.view.ViewGroup)?.let {
                it.clipChildren = false
                it.clipToPadding = false
            }
            val cc = player.findViewById<android.view.View>(androidx.media3.ui.R.id.exo_subtitle)
            // Media3's own subtitle button, wearing the same ring as its two
            // neighbours - three controls in a row should not answer to the remote
            // in three different ways
            // CC is ours: the built-in button lists the tracks already in the file
            // and can neither fetch another nor say which one has been proved to fit
            // Media3's own button steps aside for ours, which does not disappear
            // when a stream happens to carry no text
            cc?.visibility = android.view.View.GONE
            // Media3 draws a gear of its own - playback speed and its own idea of
            // track selection - which next to ours is two settings buttons saying
            // different things. Ours is the one that knows about the transcoder.
            player.findViewById<android.view.View>(androidx.media3.ui.R.id.exo_settings)
                ?.visibility = android.view.View.GONE
            val ccPlain = cc?.background
            cc?.setOnFocusChangeListener { v, has ->
                v.background = if (has) pillBackground(true) else ccPlain
                v.alpha = if (has) 1f else 0.8f
            }
            // The icon comes at the full height of its button, which next to two
            // letters reads as enormous. It is drawn to the size the letters are
            // drawn at - one fixed measure, not something recomputed per layout.
            (cc as? android.widget.ImageView)?.let { icon ->
                icon.scaleType = android.widget.ImageView.ScaleType.FIT_CENTER
                val letters = (15f * resources.displayMetrics.scaledDensity).toInt()
                icon.maxHeight = letters
                icon.maxWidth = letters * 2
                icon.adjustViewBounds = false
                icon.addOnLayoutChangeListener { v, _, _, _, _, _, _, _, _ ->
                    val pad = ((v.height - letters) / 2).coerceAtLeast(0)
                    if (v.height > 0 && v.paddingTop != pad) {
                        v.setPadding(v.paddingLeft, pad, v.paddingRight, pad)
                    }
                }
            }
            fitButton.background = pillBackground(false)
            fitButton.setPadding(0, 0, 0, 0)
            fitButton.gravity = android.view.Gravity.CENTER
            // the same box the subtitle button occupies, so the row stays even
            val like = cc.layoutParams as? android.widget.LinearLayout.LayoutParams
            val lp = android.widget.LinearLayout.LayoutParams(
                like?.width ?: -2, like?.height ?: -1)
            if (like != null) {
                lp.setMargins(like.leftMargin, like.topMargin,
                              like.rightMargin, like.bottomMargin)
                lp.gravity = like.gravity
            }
            if (lp.width < 0) fitButton.minWidth = cc.width.coerceAtLeast(96)
            // to the left of CC, so the two picture controls sit together
            ccRow.addView(fitButton, ccRow.indexOfChild(cc), lp)
            val lp2 = android.widget.LinearLayout.LayoutParams(
                like?.width ?: -2, like?.height ?: -1)
            if (like != null) {
                lp2.setMargins(like.leftMargin, like.topMargin,
                               like.rightMargin, like.bottomMargin)
                lp2.gravity = like.gravity
            }
            ccRow.addView(lookButton, ccRow.indexOfChild(cc) + 1, lp2)
            val lpSound = android.widget.LinearLayout.LayoutParams(
                like?.width ?: -2, like?.height ?: -1)
            if (like != null) {
                lpSound.setMargins(like.leftMargin, like.topMargin,
                                   like.rightMargin, like.bottomMargin)
                lpSound.gravity = like.gravity
            }
            ccRow.addView(soundButton, ccRow.indexOfChild(lookButton) + 1, lpSound)
            val lpGear = android.widget.LinearLayout.LayoutParams(
                like?.width ?: -2, like?.height ?: -1)
            if (like != null) {
                lpGear.setMargins(like.leftMargin, like.topMargin,
                                  like.rightMargin, like.bottomMargin)
                lpGear.gravity = like.gravity
            }
            ccRow.addView(gearButton, ccRow.indexOfChild(soundButton) + 1, lpGear)
            val lpCc = android.widget.LinearLayout.LayoutParams(
                like?.width ?: -2, like?.height ?: -1)
            if (like != null) {
                lpCc.setMargins(like.leftMargin, like.topMargin,
                                like.rightMargin, like.bottomMargin)
                lpCc.gravity = like.gravity
            }
            ccRow.addView(ccButton, ccRow.indexOfChild(fitButton) + 1, lpCc)
            if (steppable) {
                // at the front of the row: stepping is about the film, not about how
                // it is drawn
                for (b in listOf(nextButton, prevButton)) {
                    val lpStep = android.widget.LinearLayout.LayoutParams(
                        like?.width ?: -2, like?.height ?: -1)
                    if (like != null) {
                        lpStep.setMargins(like.leftMargin, like.topMargin,
                                          like.rightMargin, like.bottomMargin)
                        lpStep.gravity = like.gravity
                    }
                    ccRow.addView(b, 0, lpStep)
                }
                // A remote walking down out of the picture lands on the first thing
                // in this row, which is the way back to the one before. Down twice
                // means "get on with it", so it is sent to the step forward instead.
                nextButton.id = android.view.View.generateViewId()
                for (above in listOf(androidx.media3.ui.R.id.exo_play_pause,
                                     androidx.media3.ui.R.id.exo_progress)) {
                    player.findViewById<android.view.View>(above)?.nextFocusDownId =
                        nextButton.id
                }
            }
            // On a television all of it stays in one row: there is width to spare
            // and one line is the order a remote walks. On a phone the three that
            // are not about the picture - casting, subtitles and quality - go to the
            // top corner instead, which leaves the transport row uncrowded and puts
            // them where a thumb reaches.
            val toTheCorner = !onTelevision()
            if (!toTheCorner) {
                castChooser()?.let { button ->
                    val lp3 = android.widget.LinearLayout.LayoutParams(
                        like?.width ?: -2, like?.height ?: -1)
                    if (like != null) {
                        lp3.setMargins(like.leftMargin, like.topMargin,
                                       like.rightMargin, like.bottomMargin)
                        lp3.gravity = like.gravity
                    }
                    ccRow.addView(button, ccRow.indexOfChild(lookButton) + 1, lp3)
                }
            } else {
                // On a phone it is CC that stays in the row and the picture size that
                // goes to the corner: which subtitle is asked for many times in a film
                // and the crop once, if ever, and the row is where the thumb already
                // is. CC lands beside Aa, which is the other half of the same question.
                ccRow.removeView(fitButton)
                ccRow.removeView(gearButton)
            }
            // One row, boxes of the same width, in the order they are read. Forty
            // is deliberately modest: eight buttons at a full fingertip come to more
            // than the width of a phone held upright, and this row draws what fits
            // and drops the rest - which is how the gear became unreachable.
            ccRow.post {
                val d = resources.displayMetrics.density
                val standing = (0 until ccRow.childCount).count {
                    ccRow.getChildAt(it).visibility != android.view.View.GONE
                }
                val room = ccRow.width - ccRow.paddingLeft - ccRow.paddingRight
                // as wide as the row can afford, between a cramped 32 and a
                // comfortable 48 - a television has room for the second, a phone
                // held upright with eight buttons in the row has not
                val box = if (standing > 0 && room > 0)
                    (room / standing).coerceIn((32 * d).toInt(), (48 * d).toInt())
                    else (40 * d).toInt()
                for (b in listOf<android.view.View>(fitButton, ccButton, lookButton,
                                                    soundButton, gearButton,
                                                    prevButton, nextButton, cc)) {
                    b.minimumWidth = box
                }
                // the three in the top corner are the same buttons in a different
                // place: the same box and the same gaps, so the two groups read as
                // one set of controls rather than two
                (cornerRow as? android.widget.LinearLayout)?.let { row ->
                    for (i in 0 until row.childCount) {
                        val child = row.getChildAt(i)
                        val lp = child.layoutParams
                            as? android.widget.LinearLayout.LayoutParams ?: continue
                        lp.width = box
                        lp.setMargins(like?.leftMargin ?: 0, lp.topMargin,
                                      like?.rightMargin ?: 0, lp.bottomMargin)
                        child.layoutParams = lp
                        child.minimumWidth = box
                    }
                }
                ccRow.requestLayout()
            }
        } else {
            frame.addView(fitButton, android.widget.FrameLayout.LayoutParams(-2, -2).apply {
                gravity = android.view.Gravity.BOTTOM or android.view.Gravity.END
                bottomMargin = 190
                marginEnd = 28
            })
        }
        // across the whole width: there is enough to say that a corner cannot hold it
        frame.addView(infoBar, android.widget.FrameLayout.LayoutParams(-1, -2).apply {
            gravity = android.view.Gravity.TOP
        })

        // On a phone: casting, picture size and quality in the top right corner, under
        // the information line. They are the settings rather than the watching, and
        // eight buttons in one row along the bottom was more than a phone held upright
        // can draw.
        if (!onTelevision()) {
            val corner = android.widget.LinearLayout(this).apply {
                orientation = android.widget.LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
                // none of its own: it sits on the information line, which is drawn
                // the full width and made as tall as this row
                setBackgroundColor(android.graphics.Color.TRANSPARENT)
                val pad = (2 * resources.displayMetrics.density).toInt()
                setPadding(pad, pad, pad, pad)
                clipChildren = false
                clipToPadding = false
            }
            val box = (36 * resources.displayMetrics.density).toInt()
            // from the left: the television, then the picture size, then the quality
            listOfNotNull(castChooser(), fitButton, gearButton).forEach { button ->
                (button.parent as? android.view.ViewGroup)?.removeView(button)
                button.minimumWidth = box
                button.minimumHeight = box
                corner.addView(button, android.widget.LinearLayout.LayoutParams(box, box))
            }
            cornerRow = corner
            // hard against the top edge, sharing the line with the information bar
            frame.addView(corner, android.widget.FrameLayout.LayoutParams(-2, -2).apply {
                gravity = android.view.Gravity.TOP or android.view.Gravity.END
                marginEnd = (4 * resources.displayMetrics.density).toInt()
            })
            // and the line stops where the buttons begin, rather than running under
            // them: the width is whatever the three of them come to
            corner.addOnLayoutChangeListener { v, _, _, _, _, _, _, _, _ ->
                val bar = infoBar ?: return@addOnLayoutChangeListener
                val want = v.width + (10 * resources.displayMetrics.density).toInt()
                if (bar.paddingRight != want) {
                    bar.setPadding(bar.paddingLeft, bar.paddingTop, want,
                                   bar.paddingBottom)
                }
                // and the same height, so the join between them does not show
                if (v.height > 0 && bar.minimumHeight != v.height) {
                    bar.minimumHeight = v.height
                }
            }
        }
        // sat on top of the transport bar, clear of it by the bar's own height so it
        // stays put whatever a future Media3 does with the layout
        frame.addView(nameBar, android.widget.FrameLayout.LayoutParams(-1, -2).apply {
            gravity = android.view.Gravity.BOTTOM
            bottomMargin = 260
        })
        player.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            // clear of whichever part of the transport reaches highest - in one layout
            // that is the progress bar, in another the row of buttons below it
            val mine = IntArray(2).also { player.getLocationInWindow(it) }
            var highest = player.height
            for (id in intArrayOf(androidx.media3.ui.R.id.exo_progress,
                                  androidx.media3.ui.R.id.exo_bottom_bar)) {
                val v = player.findViewById<android.view.View>(id) ?: continue
                if (v.height <= 0) continue
                val at = IntArray(2).also { v.getLocationInWindow(it) }
                highest = minOf(highest, at[1] - mine[1])
            }
            val want = (player.height - highest + 14).coerceAtLeast(0)
            val lp = nameBar?.layoutParams as? android.widget.FrameLayout.LayoutParams
            if (lp != null && want > 14 && lp.bottomMargin != want) {
                lp.bottomMargin = want
                nameBar?.layoutParams = lp
            }
        }
        // the appearance panel is Compose; this activity is not, so it gets a layer
        val overlay = androidx.compose.ui.platform.ComposeView(this).apply {
            setContent {
                androidx.compose.material3.MaterialTheme(
                    colorScheme = androidx.compose.material3.darkColorScheme(
                        primary = Skin.Accent, background = Skin.Bg)) {
                  androidx.compose.runtime.CompositionLocalProvider(
                      androidx.compose.foundation.LocalIndication provides PressOnly) {
                    nextUp.value?.let { next ->
                        NextEpisodeCard(next, countdown.intValue,
                                        onPlay = { startNext(next) },
                                        onStop = { nextUp.value = null; finish() })
                    }
                    // Over the picture from the moment the button is pressed. The
                    // shelf is asked twice before anything can start, and the film
                    // went on playing through both - so pressing shuffle at the end
                    // of one thing showed the thing just watched for another second
                    // or two, which reads as the wrong answer rather than a wait.
                    if (drawing.value) {
                        Box(Modifier.fillMaxSize()
                                .background(androidx.compose.ui.graphics.Color.Black),
                            contentAlignment = Alignment.Center) {
                            androidx.compose.material3.Text(
                                "Drawing from the shelf",
                                color = androidx.compose.ui.graphics.Color(0xFF9AA3AE),
                                fontSize = 15.sp)
                        }
                    }
                    // the same arrangement the browser uses: the tracks, a tick to
                    // vouch for one, and a way to fetch another
                    // While a subtitle is being placed: the library's own mark in
                    // its own gold, over the picture. A system toast cannot be
                    // coloured, and this is the app speaking rather than Android.
                    // The wait and the answer are the same sentence finished, so
                    // they are drawn in the same pill in the same place. A system
                    // toast could not be: it cannot be coloured and it appears
                    // wherever Android decides.
                    // The party, bottom right: newest at the bottom, five at most,
                    // each gone when its ten seconds are up. Away from the subtitles,
                    // which live along the bottom edge, and away from the controls.
                    if (roomSaid.isNotEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomEnd) {
                            androidx.compose.foundation.layout.Column(
                                horizontalAlignment = Alignment.End,
                                verticalArrangement = androidx.compose.foundation.layout
                                    .Arrangement.spacedBy(6.dp),
                                modifier = Modifier.padding(end = 28.dp, bottom = 96.dp)
                                    .widthIn(max = 380.dp)) {
                                roomSaid.takeLast(5).forEach { line ->
                                    Row(verticalAlignment = Alignment.CenterVertically,
                                        modifier = Modifier
                                            .clip(RoundedCornerShape(10.dp))
                                            .background(androidx.compose.ui.graphics
                                                            .Color(0xCC0B0D10))
                                            .padding(horizontal = 12.dp, vertical = 7.dp)) {
                                        androidx.compose.material3.Text(
                                            line.who,
                                            color = Skin.Accent, fontSize = 12.5.sp,
                                            fontWeight = androidx.compose.ui.text.font
                                                .FontWeight.SemiBold,
                                            modifier = Modifier.padding(end = 8.dp))
                                        androidx.compose.material3.Text(
                                            line.text, color = Skin.Fg, fontSize = 13.sp,
                                            maxLines = 3)
                                    }
                                }
                            }
                        }
                    }
                    val waiting = syncing.value && syncAsked.value
                    if (waiting || syncSaid.value.isNotEmpty()) {
                        Box(Modifier.fillMaxSize(),
                            contentAlignment = Alignment.BottomCenter) {
                            Row(verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier
                                    .padding(bottom = 64.dp)
                                    .clip(RoundedCornerShape(999.dp))
                                    .background(androidx.compose.ui.graphics
                                                    .Color(0xCC0B0D10))
                                    .padding(horizontal = 16.dp, vertical = 8.dp)) {
                                androidx.compose.material3.Text(
                                    "P", color = Skin.Accent, fontSize = 18.sp,
                                    fontFamily = androidx.compose.ui.text.font
                                        .FontFamily.Serif,
                                    fontWeight = androidx.compose.ui.text.font
                                        .FontWeight.SemiBold)
                                androidx.compose.material3.Text(
                                    if (waiting) "Analysing the film…"
                                    else syncSaid.value,
                                    color = Skin.Fg,
                                    fontSize = 14.sp,
                                    modifier = Modifier.padding(start = 10.dp))
                            }
                        }
                    }
                    if (tracksOpen.value) {
                        title.value?.let { film ->
                            SubtitlePanel(
                                media = film,
                                chosen = if (wantsTheMade.value) PENDING_SUB
                                         else subsIndex(),
                                // The one being written can be chosen before it
                                // exists: a film playing with no subtitles then has
                                // them the moment there are enough to read.
                                pending = madeSaid.value,
                                onPick = { which ->
                                    if (which == PENDING_SUB) {
                                        wantsTheMade.value = true
                                        tracksOpen.value = false
                                        sayForAMoment(
                                            "It will come on when there is enough of " +
                                            "it to read", 5)
                                        if (subsIndex() != null) {
                                            // what is showing is not what was asked
                                            // for: nothing, until there is something
                                            playWith(film, null)
                                        } else {
                                            title.value?.let { followTheMaking(it) }
                                        }
                                    } else {
                                        wantsTheMade.value = false
                                        playWith(film, which)
                                    }
                                },
                                onVerify = { track, on ->
                                    lifecycleScope.launch {
                                        val done = Api.verifySubtitle(film, track, on)
                                        if (done) {
                                            title.value =
                                                runCatching { Api.metadata(film) }
                                                    .getOrNull() ?: film
                                        } else {
                                            // silence looked like a button that does
                                            // not work, which is what it was
                                            android.widget.Toast.makeText(
                                                this@PlayerActivity,
                                                "The server would not take that",
                                                android.widget.Toast.LENGTH_SHORT).show()
                                        }
                                    }
                                },
                                onDownload = {
                                    tracksOpen.value = false
                                    gettingSubs.value = true
                                },
                                onClose = { tracksOpen.value = false },
                                nudge = subNudge.value,
                                onNudge = { by -> nudgeSubtitles(by) },
                                // a picture drawn by the device carries its own size
                                // and place; there is nothing here to move
                                frozen = if (pictureSub() != null)
                                    "Bitmap track decoded on this device. Size, " +
                                    "position and timing come from the file."
                                else "",
                                syncing = syncing.value,
                                fetchAhead = fetchAhead.value,
                                onFetchAhead = { on ->
                                    fetchAhead.value = on
                                    lifecycleScope.launch { Api.setFetchAhead(on) }
                                },
                                autoSync = autoSync.value,
                                syncNote = syncNote.value,
                                mended = mended.value,
                                inForce = inForce.value,
                                // out of the way while it listens: the answer is
                                // about what is on screen, and the menu covers it
                                onSync = {
                                    tracksOpen.value = false
                                    syncSubtitles(true)
                                },
                                onReset = { resetSubtitle() },
                                onAutoSync = { on ->
                                    autoSync.value = on
                                    lifecycleScope.launch { Api.setAutoSync(on) }
                                    if (on && subNudge.value == 0f) {
                                        tracksOpen.value = false
                                        syncSubtitles(true)
                                    }
                                })
                        }
                    }
                    if (qualityOpen.value) {
                        title.value?.let { film ->
                            QualityPanel(
                                height = chosenHeight(), mbit = chosenRate(),
                                cap = qualityCap.value,
                                plainSound = soundIsPlain(),
                                heading = "Quality for this film",
                                capHeight = capNow.value.first,
                                capMbit = capNow.value.second,
                                note = "For what is playing now. Your own maximum, in "
                                    + "Settings, applies to everything else.",
                                onPick = { h, m -> playQuality(film, h, m) },
                                onSound = { plain -> playSoundAs(film, plain) },
                                onClose = { qualityOpen.value = false })
                        }
                    }
                    if (soundOpen.value) {
                        title.value?.let { film ->
                            SoundtrackPanel(
                                tracks = film.audioStreams,
                                playing = chosenAudio()
                                    ?: film.audioStreams.firstOrNull { it.standard }
                                        ?.index
                                    ?: film.audioStreams.firstOrNull()?.index,
                                onPick = { which -> playSound(film, which) },
                                onClose = { soundOpen.value = false })
                        }
                    }
                    if (gettingSubs.value) {
                        title.value?.let { film ->
                            SubtitleDownloadDialog(
                                media = film,
                                language = "en",
                                scope = lifecycleScope,
                                inUse = "",
                                proved = film.subsConfirmed,
                                onTaken = { release ->
                                    // it is beside the film now: play it from here.
                                    // The one that was taken, not merely the last file
                                    // beside the video - three subtitles beside a film
                                    // and it played whichever came back last.
                                    lifecycleScope.launch {
                                        val fresh = runCatching { Api.metadata(film) }
                                            .getOrNull() ?: film
                                        title.value = fresh
                                        val beside = fresh.textSubs()
                                            .filter { it.index < 0 }
                                        (beside.firstOrNull {
                                            sameRelease(it.label, release) }
                                            ?: beside.lastOrNull())
                                            ?.let { playWith(fresh, it.index) }
                                    }
                                },
                                onClose = { gettingSubs.value = false },
                                onStarted = {
                                    gettingSubs.value = false
                                    sayForAMoment("Making subtitles from the sound", 4)
                                    followTheMaking(film)
                                })
                        }
                    }
                    if (stylingOpen.value) {
                        SubtitleLookDialog(
                            "l" + ratingKey, Api.device, lifecycleScope,
                            onChanged = { look ->
                                subLook = look
                                applySubtitleLook(look)
                                Api.rememberLook(this@PlayerActivity,
                                                 "l" + ratingKey, look)
                                // a burned track lives in the picture: only a new
                                // stream can show the difference
                                if (streamUrl.contains("&burn=")) restartAt(position())
                            },
                            onClose = { stylingOpen.value = false },
                            // no way through to the tracks from here: Aa is how
                            // they look, CC is which one, and CC is next to it
                            onTracks = null)
                    }
                  }
                }
            }
        }
        frame.addView(overlay, android.widget.FrameLayout.LayoutParams(-1, -1))

        setContentView(frame)

        // The icon and the info line belong to the controls, not to the film: when the
        // transport bar times out they go with it, and a tap brings all three back.
        player.setControllerVisibilityListener(
            PlayerView.ControllerVisibilityListener { visibility ->
                // a child of the bar hides with it; a floating fallback needs telling
                if (fitButton.parent === frame) fitButton.visibility = visibility
                infoBar?.visibility = visibility
                nameBar?.visibility = visibility
                cornerRow?.visibility = visibility
            })

        // and when they go: pause, rather than play on to the room
        runCatching {
            registerReceiver(earsWentAway, android.content.IntentFilter(
                android.media.AudioManager.ACTION_AUDIO_BECOMING_NOISY))
        }

        // headphones put on halfway through: the same swap, from wherever it is
        runCatching {
            val am = getSystemService(AUDIO_SERVICE) as android.media.AudioManager
            am.registerAudioDeviceCallback(object : android.media.AudioDeviceCallback() {
                override fun onAudioDevicesAdded(
                    added: Array<out android.media.AudioDeviceInfo>?
                ) {
                    if (!throughHeadphones() || onCastNow()) return
                    // back on: carry on from where they left off
                    if (waitingForEars) {
                        waitingForEars = false
                        current()?.play()
                        android.widget.Toast.makeText(
                            this@PlayerActivity, "Headphones back",
                            android.widget.Toast.LENGTH_SHORT).show()
                    }
                    if (streamUrl.contains("/gpu/") &&
                        !streamUrl.contains("audio=passthrough")) return
                    val film = title.value ?: return
                    startActivity(
                        playIntent(this@PlayerActivity, film, position() / 1000,
                                   chosenSub(),
                                   chosenAudio(), chosenHeight(), chosenRate(),
                                   plainSound = true)
                            .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
                    finish()
                }
            }, null)
        }

        // the subtitle chosen for this playing, drawn from here on - after the
        // correction somebody has already worked out for this release, if there is one
        lifecycleScope.launch {
            subNudge.value = runCatching { Api.subShift(shiftKey(), shiftSub()) }
                .getOrDefault(0f)
            subsUrlFor(baseOffsetSec)?.let { drawOwnSubtitles(it) }
            autoSync.value = runCatching { Api.autoSync() }.getOrDefault(false)
            fetchAhead.value = runCatching { Api.fetchAhead() }.getOrDefault(true)
            // what is already in force for this subtitle, so the menu says it before
            // anybody presses anything - and so a nudge by hand reads as being on top
            // by the key, not by the title: the title is fetched only when a panel
            // asks for it, so at this point there usually is not one yet - which is
            // why a stored plan went unmentioned until something else loaded it
            val which = subsIndex()
            var settled = false
            if (which != null) {
                val plan = runCatching {
                    Api.subtitlePlan(ratingKey, which, mi = mi)
                }.getOrNull()
                val ends = durationMs / 1000f
                if (plan != null && plan.parts.isNotEmpty()) {
                    mended.value = plan.steps
                    inForce.value = kindOf(plan)
                    syncNote.value = plan.said(ends)
                    // somebody has already worked this subtitle out: keep it rather
                    // than listening to the film again for the same answer
                    settled = true
                } else if (subNudge.value != 0f) {
                    // the same number reads differently depending on who set it
                    val by = runCatching { Api.shiftBy(shiftKey(), shiftSub()) }
                        .getOrDefault("")
                    syncNote.value = String.format(
                        java.util.Locale.US,
                        if (by == "sync") "static %+.1fs" else "by hand %+.1fs",
                        subNudge.value)
                } else {
                    syncNote.value = "none"
                }
            }
            // Nobody has placed this one and this viewer asked for it to be done for
            // them: listen to the film while it plays, and move the text if it is sure.
            // Quietly - an automatic thing that cannot be done should say nothing and
            // leave the subtitle where it was, not interrupt with an apology.
            // Fetched files only. A track inside the film came with the release
            // and is in step; measuring one costs minutes to be told nought.
            if (autoSync.value && !settled && subNudge.value == 0f
                && (subsIndex() ?: 0) < 0) {
                syncSubtitles(false)
            }
        }

        // a fixed ten seconds per press, rather than a fraction of the running time
        timeBar()?.setKeyTimeIncrement(10_000L)

        // how the server wants subtitles drawn - this film's own look if it has one
        lifecycleScope.launch {
            // "l" + key: the server keys per-title settings that way. Without it
            // the film was drawn with the general settings.
            val said = Api.subtitleLook("l" + ratingKey)
            // the server's answer wins; the device's own look only when it could not
            // be asked - kept looks overrode a changed setting for good
            val mine = Api.lastLook(this@PlayerActivity, "l" + ratingKey)
                       ?: Api.lastLook(this@PlayerActivity, "")
            subLook = if (said.answered || mine == null) said else mine
            applySubtitleLook(subLook)
            Api.rememberLook(this@PlayerActivity, "l" + ratingKey, subLook)
            // with no exception of its own this is the general look, and it is the
            // best guess for the next title that has never been opened
            if (said.answered && !said.override)
                Api.rememberLook(this@PlayerActivity, "", said)
            autoNext = Api.autoNext()
            nextWait = Api.nextDelay()
        }

        // Every byte that arrives, counted here. The bandwidth meter only reports
        // estimates for adaptive playback, so a plain file or a single fragmented MP4
        // left the rate and the total reading zero for the whole film.
        val http = androidx.media3.datasource.DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
            // Two different waits. Getting a connection is quick or it is never: a
            // server that has been switched off drops the packets rather than
            // refusing them, so the attempt hangs for the whole timeout - twenty
            // seconds of frozen picture, twice over, before anything else was tried.
            // Reading is the slow one, because a transcode has to be started before
            // it can answer and a card with something else on it takes a while.
            .setConnectTimeoutMs(5_000)
            .setReadTimeoutMs(30_000)
            // and it says what it is. The player's own requests carried no name at
            // all, so a film the app was reading went down as a browser - one
            // viewing under two names in Now playing, and two evenings of looking
            // for a program on the television that was never there.
            .setDefaultRequestProperties(mapOf("X-Palladium-App" to Api.appName()))
        val counter = object : androidx.media3.datasource.TransferListener {
            override fun onTransferInitializing(
                source: androidx.media3.datasource.DataSource,
                spec: androidx.media3.datasource.DataSpec,
                isNetwork: Boolean,
            ) = Unit

            override fun onTransferStart(
                source: androidx.media3.datasource.DataSource,
                spec: androidx.media3.datasource.DataSpec,
                isNetwork: Boolean,
            ) = Unit

            override fun onBytesTransferred(
                source: androidx.media3.datasource.DataSource,
                spec: androidx.media3.datasource.DataSpec,
                isNetwork: Boolean,
                bytes: Int,
            ) {
                if (!isNetwork) return
                bytesLoaded += bytes
                val now = android.os.SystemClock.elapsedRealtime()
                // one mark a second is enough to work a rate out of
                if (transfer.isEmpty() || now - transfer.last().first > 1000) {
                    transfer.addLast(Pair(now, bytesLoaded))
                }
            }

            override fun onTransferEnd(
                source: androidx.media3.datasource.DataSource,
                spec: androidx.media3.datasource.DataSpec,
                isNetwork: Boolean,
            ) = Unit
        }
        // and the film itself may come off two machines at once: the wrapper splits
        // it in lumps when a twin is known, and is a plain http source otherwise
        ways = TwoWaysFactory(http, counter)
        offerTwo()
        // Renderers of our own: sound that can be evened out between one release and
        // the next, and a subtitle renderer that will draw a DVD's bitmaps - the
        // framework refuses them by default and the film stops where one is chosen.
        evenTheVolume()
        local = ExoPlayer.Builder(this, Renderers(this, gain))
            .setMediaSourceFactory(
                androidx.media3.exoplayer.source.DefaultMediaSourceFactory(ways!!)
                    .setLoadErrorHandlingPolicy(patientLoading()))
            .setSeekForwardIncrementMs(15_000)
            .setSeekBackIncrementMs(10_000)
            .build()
        // Audio focus. YouTube asks for it and we never did, and the focus stack was
        // empty while a film was playing - which is Android's way of saying nobody
        // owns the sound. Focus is what tells the system, the Bluetooth stack and a
        // headset with two sources paired which device is the one playing; it is also
        // what makes a telephone call duck the film rather than talk over it.
        runCatching {
            local?.setAudioAttributes(
                androidx.media3.common.AudioAttributes.Builder()
                    .setUsage(androidx.media3.common.C.USAGE_MEDIA)
                    .setContentType(androidx.media3.common.C.AUDIO_CONTENT_TYPE_MOVIE)
                    .build(),
                /* handleAudioFocus= */ true)
        }

        // A file played straight from disk gets whichever soundtrack the file calls
        // its first, which on a release with a dub in front is not the one anybody
        // wanted. English is what plays unless the viewer picks otherwise - the same
        // answer the server gives the encoder - and it costs nothing when the file
        // has one track.
        lifecycleScope.launch {
            val want = title.value?.audioStreams?.firstOrNull { it.standard }
                ?.language?.take(2)?.ifEmpty { null } ?: "en"
            local?.let { p ->
                p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
                    .setPreferredAudioLanguage(want)
                    .build()
            }
        }
        // The button on a pair of headphones, and the one on a remote that is not
        // ours: both arrive as media keys, and nothing answers them unless there is a
        // session to answer with. Play, pause, next and previous all come this way.
        session = runCatching {
            androidx.media3.session.MediaSession.Builder(this, local!!)
                .setId("palladium-" + System.currentTimeMillis())
                .build()
        }.getOrNull()

        cast = runCatching { CastPlayer(CastContext.getSharedInstance(this)) }.getOrNull()
        // Whatever the receiver makes of what it is given. Without this a refused
        // cast is silent at both ends: the television shows its own screen, the phone
        // shows the player it already had, and nothing is written down.
        cast?.addListener(object : Player.Listener {
            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                val said = listOf(
                    "casting failed on the receiver",
                    "code=" + error.errorCodeName + " (" + error.errorCode + ")",
                    error.message ?: "",
                    "url=" + (intent.getStringExtra("castUrl") ?: "?"),
                ).filter { it.isNotEmpty() }.joinToString("  ")
                lifecycleScope.launch { Api.report(this@PlayerActivity, "error", said) }
                android.widget.Toast.makeText(
                    this@PlayerActivity, "The television refused it: " +
                        error.errorCodeName, android.widget.Toast.LENGTH_LONG).show()
            }
        })
        cast?.setSessionAvailabilityListener(object : SessionAvailabilityListener {
            // hand off to the television at the position reached here, not at the
            // position this playing started from
            override fun onCastSessionAvailable() = handOver()
            override fun onCastSessionUnavailable() {
                // the receiver went away - keep playing here from the same second
                android.widget.Toast.makeText(this@PlayerActivity,
                    "Cast ended - playing here", android.widget.Toast.LENGTH_SHORT).show()
                use(local!!)
            }
        })

        use(if (cast?.isCastSessionAvailable == true) cast!! else local!!)

        // Back with the controls up puts them away; Back again leaves the film. On a
        // remote the two are the same button, and losing your place because the
        // transport bar happened to be showing is not what anybody meant by it.
        onBackPressedDispatcher.addCallback(this,
            object : androidx.activity.OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    val v = view
                    if (v != null && v.isControllerFullyVisible) {
                        v.hideController()
                    } else {
                        isEnabled = false
                        onBackPressedDispatcher.onBackPressed()
                    }
                }
            })

        lifecycleScope.launch {
            // the panel polls every 2.5s and treats a client as gone after 20s, so this
            // has to be brisker than the old ten-second progress ping
            while (true) {
                delay(5_000)
                report()
            }
        }

        // the buffer and the rate move every second; the rest only when it learns
        // something, but rebuilding the whole line is one string concatenation
        lifecycleScope.launch {
            while (true) {
                delay(1_000)
                if (infoBar?.visibility == android.view.View.VISIBLE) refreshInfo()
                watchTheBuffer()
            }
        }
    }

    /**
     * Once a second: is there anything left in hand, and is there anywhere else to go.
     *
     * A stream that is about to stop says so first - the buffer stops filling and
     * drains at the rate the film is played. Six seconds of that, or five seconds
     * frozen with the picture stopped, is a server that has gone, and the machine that
     * keeps copies is asked for the same second rather than for a fault report.
     *
     * The first fifteen seconds are not counted: a transcode starts with an empty
     * buffer whatever the network is doing.
     */
    private fun watchTheBuffer() {
        val p = current() ?: return
        if (onCastNow() || switching) return
        // A move is once-only for a minute, not for the rest of the film. This gave
        // up watching for good after one, so a picture that stopped on the machine it
        // had moved to sat frozen with the other machine answering all the while.
        if (handedOver &&
            android.os.SystemClock.elapsedRealtime() - movedAt < 60_000) return
        // Looked for before the settling wait and whether or not one has been found
        // already. It ran only while nothing had been found, so the second machine
        // was the last one ever looked for - a third holding the same film, or one
        // switched on halfway through, was never picked up. It throttles itself to
        // once a minute, and waiting fifteen seconds before even starting to ask put
        // the first answer half a minute into the film.
        lifecycleScope.launch { Api.learnTheWays() }
        findTheOtherOne()
        if (android.os.SystemClock.elapsedRealtime() - startedAt < 15_000) return
        if (ready == null) return
        watchTheCopying()
        val left = p.duration
        if (left > 0 && p.currentPosition > left - 20_000) return   // it is ending anyway
        if (!p.playWhenReady) { thin = 0; stopped = 0; return }
        val ahead = (p.bufferedPosition - p.currentPosition) / 1000
        // told to the machine the film is coming off, every five seconds: it holds
        // copying back while anybody is low, and it cannot see this from its end -
        // a film read in bursts and one made by the encoder look the same from there
        val tick = android.os.SystemClock.elapsedRealtime()
        if (tick - saidBuffer > 5_000) {
            saidBuffer = tick
            // and whether the picture has stopped for want of anything to show. A
            // transcode arrives as it is made and its buffer is thin whatever the
            // network is doing, so how deep it is says little; having run out says
            // everything, and says it the same way for both ways of watching.
            val stalled = p.playbackState == Player.STATE_BUFFERING && p.playWhenReady
            lifecycleScope.launch { Api.sayBuffer(ahead, playingOn(), stalled) }
        }
        stopped = if (p.playbackState == Player.STATE_BUFFERING) stopped + 1 else 0
        thin = if (ahead in 0..7) thin + 1 else 0
        // While the reader still has a machine, it is dealing with this and nothing
        // here fires over the top of it. Being paired used to only make this wait
        // longer, so a film reading fine off the slower machine ran its buffer thin,
        // waited out the longer count and then moved house - to the machine it was
        // already reading from, restarting the film to arrive where it was.
        // Being read off several machines is not help if the picture has stopped
        // anyway: one of them may be alive and slow, or reached by a bad road. The
        // reader is left to get on with it while the film is running, and not once it
        // has actually stalled for long enough to have emptied what was in hand.
        // While the reader still has a machine and film is still arriving, the film
        // does not move. Moving restarts it on the other machine, which throws away
        // everything in hand and fills from nothing - so the buffer emptying is
        // caused by the move rather than cured by it. The reader hands a dropped
        // machine's work to whichever is left and takes the whole film off it; that
        // is what should be allowed to happen, and it was being interrupted after
        // fourteen seconds of a picture that had already stopped.
        //
        // The exception is a reader that has stopped bringing anything at all for
        // long enough that it is not going to: then there is nothing to protect.
        val paired = !(ways?.twins).isNullOrEmpty()
        val quiet = android.os.SystemClock.elapsedRealtime() - TwoWays.lastBrought
        if (paired && !TwoWays.stranded && quiet < 25_000) return
        // The picture actually stopping is a reason to move. A thin buffer is a reason
        // only when the film is being sent as it stands: an encode is made as it goes
        // and keeps a few seconds in front of itself however well it is going, so
        // "running out" was true for the whole of every encoded film - and somebody
        // watching one was moved to another machine with nothing whatever wrong.
        // A picture that has stopped is not proof the server is at fault. The
        // viewer's own line stalls too - and then the trouble travels with them, so
        // moving cannot help and lands them on a machine that is usually the slower
        // of the two. The server is asked whether it is still there: if it answers,
        // this is not its doing and the film stays where it is until the stall is
        // long enough that nothing else explains it.
        val stall = if (paired) 14 else 5
        val lull = if (paired) 16 else 6
        if (stopped >= stall || (direct && thin >= lull)) {
            val why = if (stopped >= stall) "Moving to " + theirName()
                      else "Running out of buffer - moving over"
            if (stopped >= stall * 4 || thin >= lull * 4) {
                leanOnTheCopy(why)          // long past explaining away
            } else if (!asking) {
                asking = true
                lifecycleScope.launch {
                    val here = Servers.current(this@PlayerActivity)
                    val where = srvBase.trimEnd('/').ifEmpty { Api.base }
                    // Answering about itself is not the same as sending a film. A
                    // server on its way out - one that has just been replaced and is
                    // shutting down - says its version cheerfully while the picture
                    // stands still, and staying put on that word is how a stall that
                    // had a live machine behind it lasted the best part of a minute.
                    val quiet = android.os.SystemClock.elapsedRealtime() -
                        TwoWays.lastBrought
                    val still = Api.answering(where, here?.token ?: Api.token) &&
                        quiet < 12_000
                    if (!still) leanOnTheCopy(why)
                    else log("stalled but " + Servers.hostOf(where) +
                             " is still answering - staying")
                    asking = false
                }
            }
        }
    }

    /** The line along the top; rebuilt whenever the player learns something new. */
    private fun buildInfo(): String {
        // What this machine actually encodes with, asked of it rather than assumed.
        // A spare box with no card and no ffmpeg was being labelled "transcode
        // (NVENC)" by whatever played from it, which is a sentence about a different
        // computer - and when it cannot encode at all, what arrived is the file.
        val engine = Api.engineAt(srvBase.ifEmpty { Api.base })
        val how = if (onCastNow()) "casting"
                  else if (direct) "direct play"
                  else if (copyStartMs >= 0) "direct video, sound encoded"
                  else if (engine.isNullOrEmpty()) "sent as it is"
                  else "transcode (" + engine + ")"
        val p = current()
        val buffered = p?.let {
            val ahead = (it.bufferedPosition - it.currentPosition) / 1000
            if (ahead > 0) "buffer " + ahead + "s" else null
        }
        // a rate is a line speed, so megabits; only the amount that has gone by is
        // in bytes
        val rate = throughput()?.let {
            String.format(java.util.Locale.US, "%.1f Mbit/s", it * 8)
        }
        val used = if (bytesLoaded > 0) {
            val mb = bytesLoaded / 1_048_576.0
            if (mb >= 1024) String.format(java.util.Locale.US, "%.2f GB used", mb / 1024)
            else String.format(java.util.Locale.US, "%.0f MB used", mb)
        } else null
        val dropped = if (droppedFrames > 0) "dropped " + droppedFrames else null
        // How loud the file is and what is being done about it. A file nobody has
        // measured yet says nothing at all rather than a nought that means "unknown".
        // What the server has said most recently wins over what it said when the film
        // was opened: a file measured while it was playing had its number on the
        // server and nothing on the screen.
        val measured = Api.lufsSaid
            ?: intent.getFloatExtra("lufs", Float.NaN).takeIf { !it.isNaN() }
        val loud = measured?.let {
            // on a direct play the level in force is the one the processor holds,
            // part way through an ease included; an encode carries what was baked in
            val put = if (direct) gain.nowDb() else intent.getFloatExtra("gainUsed", 0f)
            // Wanted and applied are two different things. Sound passed through to an
            // amplifier never reaches the processor, so a correction can be held and
            // do nothing: saying "+6.7 dB" there is a lie the screen tells itself.
            val wanted = Api.gainSaid ?: 0f
            String.format(java.util.Locale.US, "%.1f LUFS", it) + when {
                direct && !gain.working && wanted != 0f ->
                    String.format(java.util.Locale.US,
                                  "  %+.1f dB not applied - passed through", wanted)
                put == 0f -> ""
                direct -> String.format(java.util.Locale.US, "  %+.1f dB", put)
                else -> String.format(java.util.Locale.US, "  %+.1f dB encoded", put)
            }
        }
        // Grouped left to right, nearest thing first: where the film is coming from,
        // then what it is, then how it is being decoded, then how it is flowing, then
        // anything wrong with it. The order used to be the order each was added, so
        // whether the film was being encoded sat behind two decoder names and how
        // many frames had been dropped sat between the buffer and the subtitle.
        return listOf(whichMachine(), sourceLine(), how,
                      sourceFacts, decodedSize, frameRate(),
                      decoderName, audioDecoder,
                      rate, used, buffered,
                      loud, dropped, subtitleName())
            .filter { !it.isNullOrEmpty() }
            .joinToString("   \u00b7   ")
    }

    /**
     * Frames a second, as counted off the screen.
     *
     * The declared rate is used until enough frames have gone by to measure one, so the
     * figure appears immediately on a file that says what it is and within a couple of
     * seconds on one that does not.
     */
    private fun frameRate(): String {
        val now = android.os.SystemClock.elapsedRealtime()
        val span = (now - frameMark.first) / 1000.0
        if (frameMark.first > 0 && span >= 2.0) {
            val rate = (framesShown - frameMark.second) / span
            frameMark = Pair(now, framesShown)
            if (rate > 0.5) measured = String.format(java.util.Locale.US, "%.1f fps", rate)
        } else if (frameMark.first == 0L) {
            frameMark = Pair(now, framesShown)
        }
        return measured.ifEmpty { fps }
    }

    private var measured = ""

    /**
     * Megabytes a second, over twenty seconds - or the average since playback started
     * when those twenty seconds happen to be quiet.
     *
     * A direct play fills its buffer in bursts and then asks for nothing, so a short
     * window reads zero for most of a film while the film is plainly still playing.
     */
    private fun throughput(): Double? {
        val now = android.os.SystemClock.elapsedRealtime()
        while (transfer.size > 1 && now - transfer.first().first > 20_000) {
            transfer.removeFirst()
        }
        val first = transfer.firstOrNull() ?: return null
        val span = (now - first.first) / 1000.0
        val recent = if (span >= 1.5) (bytesLoaded - first.second) / span / 1_048_576.0
                     else 0.0
        if (recent > 0.01) return recent
        val lived = (now - startedAt) / 1000.0
        if (lived < 2 || bytesLoaded <= 0) return null
        return bytesLoaded / lived / 1_048_576.0
    }

    private fun onCastNow(): Boolean = view?.player != null && view?.player === cast

    private fun refreshInfo() {
        infoBar?.text = buildInfo()
    }

    /** A rounded dark chip, ringed in white when the remote is on it. */
    private fun pillBackground(focused: Boolean): android.graphics.drawable.Drawable {
        val chip = android.graphics.drawable.GradientDrawable().apply {
            shape = android.graphics.drawable.GradientDrawable.RECTANGLE
            cornerRadius = 999f
            setColor(0x99000000.toInt())
            if (focused) setStroke(4, 0xFFFFFFFF.toInt())
        }
        // Inset from the view's own edges: these buttons sit in Media3's row at
        // exactly its height, so a ring drawn on the boundary loses its bottom.
        // It reports no padding of its own - a background's padding becomes the
        // view's, which would resize the icon the moment the remote arrived.
        return object : android.graphics.drawable.InsetDrawable(chip, 0, 6, 0, 6) {
            override fun getPadding(padding: android.graphics.Rect): Boolean {
                padding.set(0, 0, 0, 0)
                return false
            }
        }
    }

    /** Two names for one release, however either of them was punctuated. */
    private fun sameRelease(one: String, two: String): Boolean {
        fun bare(s: String) = s.lowercase().removeSuffix(".srt").removeSuffix(".vtt")
            .filter { it.isLetterOrDigit() }
        val a = bare(one)
        val b = bare(two)
        return a.isNotEmpty() && b.isNotEmpty() &&
            (a == b || a.contains(b) || b.contains(a))
    }

    private fun fitPref() =
        getSharedPreferences("palladium", MODE_PRIVATE).getInt("fit", 0)

    private fun applyFit(button: android.widget.TextView) {
        val zoom = fitPref() == 1
        view?.resizeMode = if (zoom) AspectRatioFrameLayout.RESIZE_MODE_ZOOM
                           else AspectRatioFrameLayout.RESIZE_MODE_FIT
        // the letter of the mode it is in, so it can be read rather than pressed
        // to find out. Fit is standard; zoom crops to fill the panel.
        button.text = if (zoom) "Z" else "F"
        button.contentDescription = if (zoom) "Zoom to fill" else "Fit"
        button.alpha = if (zoom) 1f else 0.75f
    }

    private fun cycleFit(button: android.widget.TextView) {
        val next = if (fitPref() == 1) 0 else 1
        getSharedPreferences("palladium", MODE_PRIVATE).edit().putInt("fit", next).apply()
        applyFit(button)
        android.widget.Toast.makeText(
            this, if (next == 1) "Zoom to fill" else "Fit", android.widget.Toast.LENGTH_SHORT).show()
        // Zoom takes the black away and the picture's bottom edge goes down to the
        // frame with it: the padding the cues are measured against is worked out from
        // that black, so it has to be worked out again here. Waiting for the view to
        // be laid out again does not happen - the subtitle view keeps its size - and
        // the text sat where the old edge was until the next subtitle arrived.
        (subs ?: view?.subtitleView)?.let { fitSubtitles(it, subLook) }
        // burned subtitles are part of the picture, so zoom crops them with it: ask
        // for the stream again with them raised out of the way
        if (streamUrl.contains("&burn=")) restartAt(position())
    }

    /**
     * How much of the picture's height zoom is throwing away, as a fraction.
     *
     * Zoom scales until both dimensions cover the view and crops the rest; for a film
     * with black bars baked in that is the bottom, where its subtitles are.
     */
    private fun croppedFraction(): Float {
        if (fitPref() != 1) return 0f
        val size = local?.videoSize ?: return 0f
        val vw = size.width.toFloat()
        val vh = size.height.toFloat() *
            (if (size.pixelWidthHeightRatio > 0) 1f else 1f)
        val v = view ?: return 0f
        val pw = v.width.toFloat()
        val ph = v.height.toFloat()
        if (vw <= 0f || vh <= 0f || pw <= 0f || ph <= 0f) return 0f
        val scale = maxOf(pw / vw, ph / vh)
        val visible = ph / (vh * scale)              // share of the height still shown
        return ((1f - visible) / 2f).coerceIn(0f, 0.35f)
    }

    /**
     * How far one press of left or right moves the playhead.
     *
     * Media3 divides the film into a fixed number of steps, so on a two-hour film every
     * press jumped about six minutes - too coarse to land on anything. Ten seconds is
     * the right first step, and holding the key should cover ground: the increment is
     * raised as the key repeats and put back the moment it is released, which is what a
     * television remote is expected to do.
     */
    private fun keyStepMs(repeat: Int): Long = when {
        repeat < 3 -> 10_000L
        repeat < 8 -> 30_000L
        repeat < 15 -> 60_000L
        repeat < 25 -> 120_000L
        else -> 300_000L
    }

    /**
     * Draw subtitles as the server says.
     *
     * setApplyEmbeddedStyles(false) is the important one: without it a track carrying
     * its own colours overrules everything chosen here.
     */
    /**
     * How much black there is under a wide film, in pixels.
     *
     * The player fills the screen; the picture sits inside it at its own shape. What
     * is left over, halved, is the bar under the picture. Nothing for a film that
     * fills the height, which is most of them on a television.
     */
    private fun letterbox(): Int {
        // Zoom scales the picture until it covers the panel, so there is no black under
        // it to measure - the bottom of the film is the bottom of the television.
        if (fitPref() == 1) return 0
        val sv = subs ?: view?.subtitleView ?: return 0
        val size = local?.videoSize ?: return 0
        val w = size.width.toFloat()
        val h = size.height.toFloat() * (if (size.pixelWidthHeightRatio > 0f)
            1f / size.pixelWidthHeightRatio else 1f)
        if (w <= 0f || h <= 0f || sv.width <= 0 || sv.height <= 0) return 0
        val shown = sv.width * (h / w)               // the picture, drawn to this width
        val spare = sv.height - shown
        return if (spare > 1f) Math.round(spare / 2f) else 0
    }

    private fun applySubtitleLook(look: Api.SubLook) {
        val sv = subs ?: view?.subtitleView ?: return
        val fg = when (look.colour) {
            "yellow" -> 0xFFFFE94D.toInt()
            "cyan" -> 0xFF6FD3FF.toInt()
            "green" -> 0xFF8DEA6A.toInt()
            "grey" -> 0xFFC9D3DC.toInt()
            else -> 0xFFFFFFFF.toInt()
        }
        val bg = when (look.background) {
            "dark" -> 0x8C000000.toInt()
            "black" -> 0xFF000000.toInt()
            else -> 0x00000000                      // none and shadow draw no box
        }
        val edge = if (look.background == "shadow")
            androidx.media3.ui.CaptionStyleCompat.EDGE_TYPE_DROP_SHADOW
        else androidx.media3.ui.CaptionStyleCompat.EDGE_TYPE_NONE
        sv.setApplyEmbeddedStyles(false)
        sv.setStyle(androidx.media3.ui.CaptionStyleCompat(
            fg, bg, 0x00000000, edge, 0xFF000000.toInt(), null))
        // Media3's own bottom padding applies only to cues that state no line of their
        // own, and a converted SRT frequently states one. Padding the view lifts them
        // all, so the setting means the same thing whatever the subtitle file says.
        sv.setBottomPaddingFraction(0f)
        // The height comes from the cue and from nothing else: the default padding of
        // 0.08 lifted the text a second time, and "Bottom" - then 0.93 of the height -
        // was drawn at 0.85. What is left here is the one per cent that keeps the tails
        // of y and j off the edge of the panel; every row moves up by it together, so
        // "one line up" still means one line.
        fitSubtitles(sv, look)
        if (!liftWatched) {
            liftWatched = true
            // The bar is measured from the laid-out view, so it is only right for the
            // window it was measured in: a rotation, a split screen or a new picture
            // shape needs it worked out again.
            sv.addOnLayoutChangeListener { v, _, _, _, _, _, _, _, _ ->
                fitSubtitles(v as androidx.media3.ui.SubtitleView, subLook)
            }
        }
    }

    /**
     * Size the text and lift it clear of the bottom, both measured from the picture.
     *
     * 100% is 5% of the height of the picture - SUB_BASE, the same number the server
     * and the browser use. Media3 measures its fraction against the view less its
     * padding, so the size moved whenever the padding did: a phone held upright drew
     * the first cues at 5% of the whole screen, which is several times the height of
     * the picture, and they shrank as soon as the bar was padded out. The fraction
     * ignores the padding now and the picture is measured directly.
     *
     * The lift is padding rather than the cue's own line, so the row spacing is left
     * alone. Both are only written when they differ, because writing either asks for
     * another layout.
     */
    private fun fitSubtitles(sv: androidx.media3.ui.SubtitleView, look: Api.SubLook) {
        val tall = if (sv.height > 0) sv.height else resources.displayMetrics.heightPixels
        val wide = if (sv.width > 0) sv.width else resources.displayMetrics.widthPixels
        // 100% is 5% of the height of a 16:9 picture as wide as the view - not of the
        // view, which is the picture itself and is a band across the middle of a phone
        // held upright, and not of the screen, which is three times taller than that
        // band. A scope film and a 16:9 one then draw the same size of text, and on a
        // television the reference is the screen, as it always was.
        val reference = Math.min(resources.displayMetrics.heightPixels.toFloat(),
                                 wide * 9f / 16f)
        val size = SUB_BASE * look.size * reference / tall
        if (size != sizedAt) {
            sizedAt = size
            sv.setFractionalTextSize(size, true)
        }
        // Nothing is padded. Padding the black away was meant to make nought the
        // picture's bottom edge, and on a phone it did not: the cue is drawn by a child
        // view that keeps the size it was measured at, so the fractions went on being
        // counted off the whole window and the text sat under the picture. The bar is
        // part of the number instead, where it can be seen.
        if (sv.paddingBottom != 0) sv.setPadding(0, 0, 0, 0)
        // Media3 paints into a child of the subtitle view, and that child can be left
        // with no height at all - measured before the picture had a shape, and never
        // measured again. Nothing is then drawn, however good the cues are. Give it the
        // room the view has, outside the layout pass this is called from.
        val kid = if (sv.childCount > 0) sv.getChildAt(0) else null
        val room = sv.height - sv.paddingTop - sv.paddingBottom
        if (kid != null && room > 0 && kid.height != room) {
            sv.post { sv.requestLayout() }
        }
    }

    private var sizedAt = -1f

    //: the subtitle view we draw into: the whole player, not the picture inside it
    private var subs: androidx.media3.ui.SubtitleView? = null

    private var liftWatched = false

    //: said once per playing: the server only announces the mark once, and this covers
    //: the case of it being made again after a restart in the middle of an episode
    private var saidVerified = false

    private fun timeBar(): androidx.media3.ui.DefaultTimeBar? =
        view?.findViewById(androidx.media3.ui.R.id.exo_progress)

    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean {
        // The button on a pair of headphones, and the play key on any remote. The
        // media session answers these when the app is in the background; in the
        // foreground they arrive here first, and something has to act on them.
        val media = event.keyCode in setOf(
            android.view.KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
            android.view.KeyEvent.KEYCODE_HEADSETHOOK,
            android.view.KeyEvent.KEYCODE_MEDIA_PLAY,
            android.view.KeyEvent.KEYCODE_MEDIA_PAUSE)
        if (media && event.action == android.view.KeyEvent.ACTION_DOWN) {
            current()?.let { p ->
                val wanted = when (event.keyCode) {
                    android.view.KeyEvent.KEYCODE_MEDIA_PLAY -> true
                    android.view.KeyEvent.KEYCODE_MEDIA_PAUSE -> false
                    else -> !p.isPlaying
                }
                if (wanted) p.play() else p.pause()
                view?.showController()
            }
            return true
        }
        if (media) return true              // the matching key-up, already dealt with
        val sideways = event.keyCode == android.view.KeyEvent.KEYCODE_DPAD_LEFT ||
                       event.keyCode == android.view.KeyEvent.KEYCODE_DPAD_RIGHT
        if (sideways) {
            // the bar does the scrubbing; this only decides how big its next step is
            timeBar()?.setKeyTimeIncrement(
                if (event.action == android.view.KeyEvent.ACTION_DOWN)
                    keyStepMs(event.repeatCount) else 10_000L)
        }
        return safeKey(event) { super.dispatchKeyEvent(event) }
    }

    /**
     * Upright, keep the picture out from under the camera and the navigation pill.
     *
     * Sideways nothing is done: the film is the whole screen, which is the point of
     * turning the phone. Upright there is black above and below the picture already,
     * so the room is free - the top band grows to cover the punch-hole and the
     * transport bar rises above the pill.
     *
     * The sizes come from the phone. In immersive mode the system bars report nothing,
     * being hidden, but the cutout and the gesture area are still there and still
     * answer, which is what makes this work on a screen this app has never seen.
     */
    private fun keepClearOfTheHardware(frame: android.view.View, player: PlayerView) {
        androidx.core.view.ViewCompat.setOnApplyWindowInsetsListener(frame) { _, insets ->
            val upright = resources.configuration.orientation ==
                android.content.res.Configuration.ORIENTATION_PORTRAIT
            val cutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val gestures = insets.getInsets(WindowInsetsCompat.Type.systemGestures())
            val top = if (upright) maxOf(cutout.top, bars.top) else 0
            val bottom = if (upright)
                maxOf(cutout.bottom, bars.bottom, gestures.bottom) else 0
            if (frame.paddingTop != top) frame.setPadding(0, top, 0, 0)
            // the transport rises, not the picture: the film stays where it is and the
            // buttons come up out of the pill's way
            val controls = player.findViewById<android.view.View>(
                androidx.media3.ui.R.id.exo_controller)
            if (controls != null && controls.paddingBottom != bottom) {
                controls.setPadding(controls.paddingLeft, controls.paddingTop,
                                    controls.paddingRight, bottom)
            }
            insets
        }
    }

    /**
     * The system's cast button, or nothing at all.
     *
     * Nothing where Play services cannot answer: the button would list no televisions
     * and do nothing when pressed, which is worse than not offering it. It is told to
     * stay visible rather than hiding until a receiver replies, so it does not appear
     * and disappear while somebody is reaching for it.
     */
    private fun castChooser(): android.view.View? {
        if (!Cast.available(this)) return null
        return runCatching {
            val themed = android.view.ContextThemeWrapper(
                this, androidx.appcompat.R.style.Theme_AppCompat_NoActionBar)
            androidx.mediarouter.app.MediaRouteButton(themed).also {
                com.google.android.gms.cast.framework.CastButtonFactory
                    .setUpMediaRouteButton(themed, it)
                it.setAlwaysVisible(true)
            }
        }.getOrNull()
    }

    /**
     * Nothing but the film: status bar and the phone's navigation bar both go away, and
     * a swipe from the edge brings them back briefly rather than permanently.
     */
    private fun goImmersive() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) goImmersive()          // it creeps back after dialogs and pauses
    }

    /**
     * Give the film to the television, from where the viewer has got to.
     *
     * The address the receiver is handed was built when this playing started, so it
     * begins at that offset; a direct play can simply be seeked, and a transcode has
     * to be asked for again from the new position. Either way the player stays open
     * and follows what the television is doing.
     */
    private fun handOver() {
        val player = cast ?: return
        val at = position()                       // absolute, whichever stream this is
        if (castDirect) {
            use(player)
            if (at > 0) runCatching { player.seekTo(at) }
            return
        }
        // a transcode starts where it was asked to start: ask again, at here
        val film = title.value
        if (film == null) {
            use(player)
            return
        }
        // only a picture track can be burned into a cast stream; a text one rides
        // along, and a file beside the film is always text
        val (url, direct) = Api.castUrl(film, at / 1000,
                                        chosenSub()?.takeIf { it >= 0 })
        castDirect = direct
        castItem = item.buildUpon().setUri(url)
            .setMimeType(if (url.contains("/gpu/hls"))
                             androidx.media3.common.MimeTypes.APPLICATION_M3U8
                         else androidx.media3.common.MimeTypes.VIDEO_MP4).build()
        use(player)
    }

    /** Point the view and the stream at whichever player is now in charge. */
    private fun use(player: Player) {
        val other = if (player === local) cast else local
        val resumeAt = other?.currentPosition?.takeIf { it > 0 && other.isCommandAvailable(
            Player.COMMAND_GET_CURRENT_MEDIA_ITEM) } ?: 0L
        other?.stop()
        val onCastPlayer = player === cast
        // a live encode is not seekable, so the controls get a wrapper that turns a seek
        // into a fresh encode from that point; a real file needs no such help
        view?.player = if (!onCastPlayer && !direct) {
            OffsetPlayer(player, streamStartMs(), durationMs) { target ->
                restartAt(target)
            }.also { wrapper = it }
        } else {
            wrapper = null
            player
        }
        // The session speaks for whatever the screen is showing, wrapper and all.
        // Given the bare player it reported a live encode's own clock - which starts
        // at zero however far into the film it is - and answered a seek by asking a
        // stream that cannot seek. Headphones ask both of those questions.
        runCatching { session?.player = view?.player ?: player }
        player.setMediaItem(if (onCastPlayer) castItem else item)
        player.prepare()
        // an engine stream already starts at the offset; a plain file has to be seeked
        val startsAtOffset = if (onCastPlayer) castDirect else direct
        val target = if (resumeAt > 0) resumeAt else if (startsAtOffset) streamStartMs() else 0L
        if (target > 5000) player.seekTo(target)
        player.playWhenReady = true
        player.addListener(object : Player.Listener {
            /**
             * No sound, and no error to say why.
             *
             * A television that cannot decode Dolby - no licence in the box, or a set
             * that will not take it over HDMI - is handed a stream it can see and
             * cannot hear. Media3 does not treat that as a failure: the track is
             * there, marked unsupported, and simply never selected. So the film plays
             * in silence and nothing anywhere says so.
             *
             * When that happens the stream is asked for again with the sound in AAC,
             * which every device decodes. Once per playing, so a device that cannot
             * manage AAC either does not sit here restarting.
             */
            override fun onTracksChanged(tracks: androidx.media3.common.Tracks) {
                drawPictureSubtitle()
                pickTheChosenSound(tracks)
                if (triedPlainAudio || onCastNow()) return
                var heard = false
                var offered = false
                for (group in tracks.groups) {
                    if (group.type != androidx.media3.common.C.TRACK_TYPE_AUDIO) continue
                    offered = true
                    for (i in 0 until group.length) {
                        if (group.isTrackSupported(i) && group.isTrackSelected(i)) {
                            heard = true
                        }
                    }
                }
                if (!offered || heard) return
                triedPlainAudio = true
                val film = title.value ?: return
                android.widget.Toast.makeText(
                    this@PlayerActivity,
                    "This device cannot decode that soundtrack - re-encoding it",
                    android.widget.Toast.LENGTH_SHORT).show()
                // Started again rather than re-pointed. A film played from disk has a
                // timeline of its own and an encode has one that starts where it was
                // asked to start; swapping the address underneath left the player
                // holding the wrong one, and the progress bar stopped moving.
                startActivity(
                    playIntent(this@PlayerActivity, film, position() / 1000,
                               chosenSub(),
                               chosenAudio(), chosenHeight(), chosenRate(),
                               plainSound = true)
                        .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
                finish()
            }

            override fun onPlaybackStateChanged(state: Int) {
                // Once, when it first has something to say: what the player is
                // holding and what it makes of it. A television across the room
                // cannot be watched from here any other way.
                if (state == Player.STATE_READY && !told) {
                    told = true
                    val p = current()
                    val inner = local
                    lifecycleScope.launch {
                        Api.trace(listOf(
                            "player=" + (p?.javaClass?.simpleName ?: "none"),
                            // the subtitle's state, which was the one thing a
                            // television across the room could not be asked about
                            "sub=" + (subsIndex()?.toString() ?: "none"),
                            "cues=" + ownCues.size,
                            "nudge=" + subNudge.value,
                            "subPos=" + subLook.position,
                            "autoSync=" + autoSync.value,
                            "screen=" + Api.device,
                            "direct=" + direct,
                            "plain=" + soundIsPlain(),
                            "base=" + baseOffsetSec,
                            "durationIntent=" + durationMs,
                            "durationPlayer=" + (p?.duration ?: -1),
                            "durationInner=" + (inner?.duration ?: -1),
                            "position=" + (p?.currentPosition ?: -1),
                            "positionInner=" + (inner?.currentPosition ?: -1),
                            "seekable=" + (p?.isCurrentMediaItemSeekable ?: false),
                            "canSeek=" + (p?.isCommandAvailable(
                                Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM) ?: false),
                            "session=" + (session != null),
                            "url=" + streamUrl.substringAfter("//").take(160),
                        ).joinToString("  "))
                    }
                }
                if (state == Player.STATE_ENDED) {
                    // something put on casually leads to the next draw; a series rolls
                    // on; a film on its own has nowhere to go
                    if (casually()) step(forward = true)
                    else if (autoNext && showKey.isNotEmpty()) offerNext()
                    else {
                        ranOut = title.value?.ratingKey.orEmpty()
                        finish()
                    }
                } else {
                    report()                       // buffering and ready, as they happen
                }
            }

            override fun onIsPlayingChanged(isPlaying: Boolean) {
                report()                           // pause and resume reach the panel at once
                // paused: hold the headphones open with a silent stream, or they
                // drop the link and wander off to whatever else is paired
                keepLinkWarm(!isPlaying)
                holdTheScreen(isPlaying)
            }

            /**
             * Something stopped. Tell the person in one line, and the server in detail.
             *
             * A dropped cast session is the common one and it is recoverable: the
             * receiver goes away, the film carries on here, from the same second.
             */
            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                // Said to the server before anything is done about it. The player
                // handled its own errors and told nobody, so a film that would not
                // start left the server with a stream cut after two megabytes and no
                // reason for it - which is all anybody had to work from afterwards.
                // Once per code per playing: the retries below would otherwise send
                // the same line six times in twenty seconds.
                if (saidFaults.add(error.errorCodeName)) {
                    val note = listOf(
                        "playback failed",
                        "code=" + error.errorCodeName + " (" + error.errorCode + ")",
                        error.message ?: "",
                        "at=" + position() + "s",
                        "on=" + srvBase,
                        "file=" + (title.value?.fileName ?: "?"),
                    ).filter { it.isNotEmpty() }.joinToString("  ")
                    log(note)
                    lifecycleScope.launch { Api.report(this@PlayerActivity, "error", note) }
                }
                // A stream that stops arriving mid-block looks to the extractor like a
                // broken file: the server was restarted, or the network blinked. The
                // film is fine, so ask for it again from the same second rather than
                // dropping the viewer back to the poster.
                // the subtitle is part of the source to Media3, so one it cannot read
                // stops the film before it starts. The film is not the problem.
                // ...but only when the source itself is fine. A server that has
                // gone off produces the same "could not read it" from the extractor,
                // and blaming the subtitle for that dropped them, said so, and
                // restarted - once per attempt, and again in the player it moved to.
                val networkGone = error.errorCode in setOf(
                    androidx.media3.common.PlaybackException
                        .ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
                    androidx.media3.common.PlaybackException
                        .ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
                    androidx.media3.common.PlaybackException
                        .ERROR_CODE_IO_BAD_HTTP_STATUS,
                    androidx.media3.common.PlaybackException
                        .ERROR_CODE_IO_UNSPECIFIED)
                if (!onCastNow() && subsAttached && retried < 2 && !networkGone) {
                    retried++
                    subsAttached = false
                    faultAt = maxOf(position(), lastGood)
                    item = item.buildUpon()
                        .setSubtitleConfigurations(emptyList()).build()
                    android.widget.Toast.makeText(
                        this@PlayerActivity,
                        "Those subtitles would not load - playing without them",
                        android.widget.Toast.LENGTH_LONG).show()
                    restartAt(maxOf(position(), lastGood))
                    return
                }
                // The device took the video decoder away, or would not give one
                // out. Restarting straight away asks the same device for a decoder it
                // has not released yet, which fails at once - hence seven attempts in
                // a minute. Let go of this player, wait a moment, and come back as a
                // transcode, which is the format most likely to find a free decoder.
                val codecTrouble = !onCastNow() && error.errorCode in setOf(
                    androidx.media3.common.PlaybackException
                        .ERROR_CODE_DECODING_RESOURCES_RECLAIMED,
                    androidx.media3.common.PlaybackException
                        .ERROR_CODE_DECODER_INIT_FAILED,
                    androidx.media3.common.PlaybackException
                        .ERROR_CODE_DECODER_QUERY_FAILED)
                if (codecTrouble && retried < 2) {
                    retried++
                    val at = maxOf(position(), lastGood) / 1000
                    android.widget.Toast.makeText(
                        this@PlayerActivity,
                        "The device took the decoder back - starting again",
                        android.widget.Toast.LENGTH_SHORT).show()
                    runCatching { local?.release() }
                    local = null
                    view?.player = null
                    val film = title.value
                    lifecycleScope.launch {
                        kotlinx.coroutines.delay(1200)
                        val whole = film ?: runCatching { Api.item(ratingKey, playingOn()) }
                            .getOrNull()
                        if (whole == null) {
                            finish()
                            return@launch
                        }
                        // burn nothing, choose nothing: a plain transcode, which the
                        // device has the best chance of decoding
                        val (url, _) = Api.playbackUrl(whole, at, burnIndex = null)
                        startActivity(playIntent(this@PlayerActivity, whole, at)
                                          .putExtra("url", url)
                                          .putExtra("direct", false)
                                          .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
                        finish()
                    }
                    return
                }
                // where it was, or where it last got to: a dropped stream often
                // leaves the player reporting nought, and that is not the same as
                // somebody being at the beginning
                val at = maxOf(position(), lastGood)
                // one retry rather than two when there is a copy standing by: each
                // one is a connection attempt to a machine that has gone, and the
                // viewer watches every second of it
                val patience = if (Api.standby.isNotEmpty() ||
                                   Api.standbyOut.isNotEmpty()) 1 else 2
                val recoverable = !onCastNow() && retried < patience && at > 0 &&
                    error.errorCode in setOf(
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_PARSING_CONTAINER_MALFORMED,
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_IO_UNSPECIFIED)
                if (recoverable) {
                    retried++
                    faultAt = at
                    restartAt(at)
                    return
                }
                // This server has stopped answering and asking it again has not
                // helped. The machine that keeps copies of it holds this film too -
                // it numbers its own library, so the film is asked for by what it
                // is - and the evening carries on there.
                val lost = !onCastNow() && !handedOver && at > 0 &&
                    error.errorCode in setOf(
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_IO_UNSPECIFIED,
                        androidx.media3.common.PlaybackException
                            .ERROR_CODE_PARSING_CONTAINER_MALFORMED)
                if (lost && (Api.standby.isNotEmpty() || Api.standbyOut.isNotEmpty())) {
                    handedOver = true
                    lifecycleScope.launch {
                        val film = whatIsPlaying()
                        val other = ready
                                    ?: if (film == null) null
                                       else runCatching { Api.sameOnTheCopy(film) }
                                           .getOrNull()
                        if (other == null) {
                            log("nothing to hand over to: standby=" + Api.standby +
                                " out=" + Api.standbyOut)
                            android.widget.Toast.makeText(
                                this@PlayerActivity,
                                theirName() + " does not hold this one",
                                android.widget.Toast.LENGTH_SHORT).show()
                            return@launch
                        }
                        other.srv?.base?.let { Api.onTheCopyNow(it) }
                        log("handing over to " + other.srv?.base + " (key " +
                            other.ratingKey + ") at " + (at / 1000) + "s")
                        android.widget.Toast.makeText(
                            this@PlayerActivity,
                            "Carrying on from " + theirName(),
                            android.widget.Toast.LENGTH_SHORT).show()
                        // Everything that was chosen goes with it. A track
                        // number belongs to the machine that numbered it, so the
                        // subtitle and the soundtrack are looked for again in the
                        // other machine's lists by their language and their name.
                        // Handing over with none of this - which is what it did -
                        // takes the subtitles off at the moment the film moves, and
                        // that is the moment nobody can work out why.
                        // the subtitle on the screen, not the one this playing
                        // was started with. A subtitle chosen while the film ran, or
                        // picked by the player itself, is not in the intent - so the
                        // film moved house with subtitles on and arrived without any.
                        val showing = subsIndex() ?: chosenSub()
                        val sub = sameSub(showing, title.value?.textSubs(),
                                          other.textSubs())
                        val ear = sameAudio(chosenAudio(), title.value?.audioStreams,
                                            other.audioStreams)
                        // and how they are drawn, put to the other machine before it
                        // is asked for anything, so its own menus agree with what is
                        // on the screen
                        runCatching { Api.pushSubtitleLook(other.srv) }
                        startActivity(playIntent(this@PlayerActivity, other, at / 1000,
                                                 sub, ear, chosenHeight(), chosenRate(),
                                                 plainSound = soundToKeep())
                                          .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
                        finish()
                    }
                    return
                }
                val where = if (onCastNow()) "casting" else "on the device"
                val note = error.errorCodeName + " " + where + ": " +
                        (error.message ?: "no detail") +
                        "  [" + intent.getStringExtra("title") + "]  " +
                        streamUrl.substringBefore("?") +
                        "  at " + (position() / 1000) + "s"
                android.widget.Toast.makeText(
                    this@PlayerActivity,
                    if (onCastNow()) "Lost the television - playing here instead"
                    else "Playback stopped: " + error.errorCodeName,
                    android.widget.Toast.LENGTH_LONG).show()
                lifecycleScope.launch {
                    Api.report(this@PlayerActivity, "crash", note)
                }
                if (onCastNow()) {
                    // fall back to this screen rather than sitting on a dead session
                    local?.let { use(it) }
                }
            }
            override fun onVideoSizeChanged(size: androidx.media3.common.VideoSize) {
                decodedSize = if (size.width > 0) size.width.toString() + "x" + size.height else ""
                refreshInfo()
                // the black bar can only be measured once the picture exists
                applySubtitleLook(subLook)
            }
        })
        // every frame handed to the surface, which is what the rate is counted from
        (player as? ExoPlayer)?.setVideoFrameMetadataListener { _, _, _, _ -> framesShown++ }
        (player as? ExoPlayer)?.addAnalyticsListener(
            object : androidx.media3.exoplayer.analytics.AnalyticsListener {
                override fun onVideoInputFormatChanged(
                    eventTime: androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime,
                    format: androidx.media3.common.Format,
                    evaluation: androidx.media3.exoplayer.DecoderReuseEvaluation?,
                ) {
                    // the frame rate the stream declares, trimmed of trailing zeros
                    val r = format.frameRate
                    fps = if (r > 0) {
                        val text = String.format(java.util.Locale.US, "%.3f", r)
                            .trimEnd('0').trimEnd('.')
                        text + " fps"
                    } else ""
                    refreshInfo()
                }

                override fun onDroppedVideoFrames(
                    eventTime: androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime,
                    dropped: Int,
                    elapsedMs: Long,
                ) {
                    droppedFrames += dropped
                }

                // the bytes are counted by the data source now; adding them here
                // as well would count every one of them twice

                override fun onAudioDecoderInitialized(
                    eventTime: androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime,
                    decoder: String,
                    initializedTimestampMs: Long,
                    initializationDurationMs: Long,
                ) {
                    audioDecoder = decoder.substringAfterLast('.')
                    refreshInfo()
                }

                override fun onVideoDecoderInitialized(
                    eventTime: androidx.media3.exoplayer.analytics.AnalyticsListener.EventTime,
                    decoder: String,
                    initializedTimestampMs: Long,
                    initializationDurationMs: Long,
                ) {
                    decoderName = decoder
                    refreshInfo()
                }
            })
        refreshInfo()
    }

    /**
     * Find the next episode and start it after five seconds.
     *
     * The countdown matters: continuing when somebody wanted to stop is worse than not
     * continuing, so there is a visible count and a way out of it.
     */
    private fun offerNext() {
        report("stopped")
        lifecycleScope.launch {
            val next = Api.nextEpisode(showKey, season, number)
            if (next == null) {
                finish()
                return@launch
            }
            nextUp.value = next
            countdown.intValue = nextWait
            while (countdown.intValue > 0 && nextUp.value != null) {
                delay(1_000)
                countdown.intValue -= 1
            }
            if (nextUp.value != null) startNext(next)
        }
    }

    private fun startNext(next: Media) {
        nextUp.value = null
        // The next one is not here yet: a pack has it and this machine does not.
        // Its page is opened instead of the player, where how far along it is can be
        // seen and it can be asked for - the same as a shuffle that draws one. It is
        // deliberately not fetched by pressing Next: a download is somebody's week's
        // allowance, and Next is pressed on the way out of the room.
        if (next.offered) {
            Opening.media = next          // already in hand: no second trip for it
            startActivity(android.content.Intent(this@PlayerActivity,
                                                 MainActivity::class.java).apply {
                addFlags(android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP or
                         android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP)
                putExtra("openKey", next.ratingKey)
            })
            finish()
            return
        }
        // subtitles carry over: the next episode's file was fetched while this one was
        // still playing, so it is there waiting - but the copy of the episode we hold
        // was read before that, and has to be read again to see it
        val showing = subtitlesShowing()
        lifecycleScope.launch {
            var go = next
            runCatching { Api.metadata(next) }.getOrNull()?.let { go = it }
            // either this episode was being watched with subtitles, or the series has
            // settled on a variant, or a subtitle file is simply sitting beside the
            // video - which only happens because somebody put it there
            val wanted = showing || go.subsWanted ||
                go.textSubs().any { it.index < 0 }
            val track = if (wanted) matchingSubtitle(go) else null
            // put this episode's page behind the player before the player opens, so
            // Back leaves the film and arrives where the film came from
            Opening.media = go            // already in hand: no second trip for it
            startActivity(android.content.Intent(this@PlayerActivity,
                                                 MainActivity::class.java).apply {
                addFlags(android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP or
                         android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP)
                putExtra("openKey", go.ratingKey)
            })
            startActivity(playIntent(this@PlayerActivity, go, 0, track)
                              .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
            finish()
        }
    }

    /** Restart the engine stream from a new point and carry on as if it were a seek. */
    /**
     * Find the same film on the machine that keeps copies, before anything goes wrong.
     *
     * One request, once, while the picture is fine. Then a switch is a seek rather
     * than a search: no lookup, no two failed retries with the frame frozen.
     */
    /**
     * Tell the reader where the second copy is, when there is one worth having.
     *
     * Only a film being served as itself can be split: a transcode is made as it is
     * sent, so two of them are not the same bytes. The copy is the same file when it
     * carries the same name - it was only accepted after its fingerprint agreed with
     * the original - and that is the whole of the test.
     */
    //: why the film is coming off one machine rather than several, when it is and
    //: there is nothing to be done about it - casting, or a film being encoded
    private var whyNotSplit: String? = null

    /**
     * How many machines are carrying the film, of how many are known to hold it.
     *
     * A count rather than a sentence. It said "looking for a second machine" while it
     * was asking and named a machine when it found nothing, which is a paragraph
     * about a thing that wants a number: one of one, one of two, two of two.
     */
    private fun sourceLine(): String {
        // What is connected to this film, which is the only question the line is
        // being asked. It used to count machines that hold the file against machines
        // that had brought something in the last ten seconds - two different
        // questions, and on an easy film it read 1/2 while nothing was wrong.
        val on = if ((ways?.twins).isNullOrEmpty()) 1 else TwoWays.joined
        val head = "sources " + on + "/" + on
        // a share only for a film coming off two machines now: the counts are kept
        // across films, and a stream with no second source showed the last one's 50/50
        val share = if (on >= 2) TwoWays.split() else null
        // and which machine is answering, when it is not the one this screen calls
        // home. A film served by the copy looked exactly like one served by the main
        // server, so there was no way to tell from the screen that anything had
        // fallen over - or that it had not come back.
        val standing = if (Api.standingBy())
            "  ·  on " + Api.standbyName.ifBlank { "the other machine" } else ""
        return when {
            share != null -> head + "  " + share + standing
            whyNotSplit != null -> head + "  " + whyNotSplit + standing
            else -> head + standing
        }
    }

    private fun offerTwo() {
        val reader = ways ?: return
        // Somebody who does not want a film read off two machines gets it off one -
        // and so does a film being encoded, which cannot be read in stretches at all.
        //
        // The reader asks for two megabytes at a time. Against a file that is exactly
        // right; against an encode being made as it is sent, every one of those asks
        // starts the encoder again from that point, and starting it again stops the
        // one before. The picture then waits for an encode that is replaced every two
        // seconds, for ever. Two hundred and thirty encodes were begun for one
        // episode nobody was watching.
        if (!Api.maySplit || !direct) {
            reader.main = ""
            reader.twins = emptyList()
            whyNotSplit = if (!direct) "being encoded" else null
            // said out loud. This was the quietest of the ways out of here, and a
            // film that could have come off two machines and did not looked exactly
            // like one where nothing had been asked.
            log("not splitting - " + (if (!Api.maySplit) "turned off in settings"
                                      else "being encoded"))
            return
        }
        reader.main = streamUrl
        val other = ready
        val film = title.value
        // The same file, on a different machine. Both halves matter: the same name
        // is what says the bytes are identical, and a different machine is the whole
        // point - a film split between two addresses of one computer is one computer
        // doing twice the work, and it read as two sources on the line at the top.
        val elsewhere = (other?.srv?.base ?: "").trimEnd('/')
        val here = srvBase.trimEnd('/').ifEmpty { Api.base }
        val same = other != null && film != null &&
            !other.fileName.isNullOrEmpty() && other.fileName == film.fileName &&
            elsewhere.isNotEmpty() && elsewhere != here
        if (!direct || !same || onCastNow()) {
            // Why not, in words, on the line along the top of the picture. It said
            // nothing at all unless somebody was reading the system log, so a film
            // that could have come off both machines and did not looked exactly like
            // one that could not.
            // by name: "the copy" names nothing on a screen that has both machines
            // on it, and the line is there to say which one is not being read from.
            val copy = (other?.srv?.name.orEmpty()
                .ifBlank { Servers.all(this).firstOrNull {
                    it.copyOf.trimEnd('/') == here }?.name.orEmpty() }
                .ifBlank { Api.standbyName }
                .ifBlank { otherKnownName() })
                .ifBlank { "the other server" }
            whyNotSplit = when {
                onCastNow() -> "casting"
                !direct -> "being encoded"
                // Not asked yet is not the same as asked and refused. The lookup
                // takes a moment and this line was drawn before it answered, so a
                // film the copy did hold was announced as one it did not - and the
                // words stayed on the screen after the split had started.
                other == null && hadItAtStart == null -> null
                other == null -> null
                other.fileName.isNullOrEmpty() -> copy + " has not got this one"
                other.fileName != film?.fileName -> copy + " holds a different file"
                elsewhere == here -> null
                else -> null
            }
            // and the whole of it in the log. Three of the branches above give no
            // words at all - rightly, there is nothing a viewer can do about any of
            // them - and the log printed the same sentence for all three, so a film
            // that had found its second machine and then discarded it read exactly
            // like one that had found nothing.
            log("not splitting - " + (whyNotSplit ?: "no reason given") +
                " · direct=" + direct + " casting=" + onCastNow() +
                " other=" + (other?.let { (it.srv?.name ?: "?") + "/" + it.ratingKey }
                             ?: "none") +
                " here=" + here + " elsewhere=" + elsewhere +
                " mine=" + (film?.fileName ?: "none") +
                " theirs=" + (other?.fileName ?: "none"))
            reader.twins = emptyList()
            return
        }
        whyNotSplit = null
        lifecycleScope.launch {
            // every machine holding the same file, so the film is read off all of
            // them at once and survives any of them going off
            val urls = readies.filter { it.fileName == film?.fileName }
                .mapNotNull { one ->
                    // by the address that opens on this network, not whichever one
                    // the row is filed under: reading a machine in the same house
                    // through the router costs the film
                    val had = one.srv
                    val near = had?.let {
                        val door = Servers.doorThatOpens(it)
                        if (door == it.base) it
                        else it.copy(base = door, outside = it.base)
                    }
                    val (url, straight) = Api.playbackUrl(one, 0)
                    // the same answer, off the near door: only the address changes
                    if (!straight) null
                    else if (near == null || had == null || near.base == had.base) url
                    else url.replace(had.base.trimEnd('/'), near.base.trimEnd('/'))
                }
            if (urls == reader.twins) return@launch      // said once, not once a minute
            reader.twins = urls
            log(if (urls.isEmpty())
                    // and why there is nothing: every machine that was found, and
                    // what became of its address. An empty list said nothing at all.
                    "nothing to split with - " + readies.size + " found [" +
                        readies.joinToString("; ") {
                            (it.srv?.name ?: "?") + " file=" + (it.fileName ?: "none") +
                                " part=" + (it.partKey ?: "none") +
                                " direct=" + it.canDirectPlay()
                        } + "] mine=" + (film?.fileName ?: "none")
                else "splitting with " + urls.joinToString(", "))
        }
    }

    private fun findTheOtherOne() {
        // Asked again all through the film, not only until one machine was found.
        // Servers are switched on and off while something is playing, and the set
        // that has this film at ten past is not the set that had it at ten.
        if (looking || onCastNow()) return
        val now = android.os.SystemClock.elapsedRealtime()
        // Every twenty seconds, not every minute. One small question to each machine
        // known - has it this film - and a minute is a long time to carry a film off
        // one machine while another that has it sits there, which is what happens
        // every time the main server is restarted under a film.
        if (now - askedAt < 20_000) return
        askedAt = now
        looking = true
        lifecycleScope.launch {
            val film = whatIsPlaying()
            if (film == null) {
                looking = false
                log("cannot say what this is; nothing to look for on the copy")
                return@launch
            }
            // every machine that answers and holds it, this one aside
            readies = runCatching {
                Api.sameElsewhere(this@PlayerActivity, film,
                                  srvBase.trimEnd('/').ifEmpty { Api.base }) }
                .getOrNull().orEmpty()
            ready = readies.firstOrNull()
            if (hadItAtStart == null) hadItAtStart = ready != null
            else if (hadItAtStart == false && ready != null && !toldTaken) {
                // Noted, not announced. Finding a second machine is the ordinary case
                // now and the line along the top already says so; a message across the
                // picture belongs to the moment something goes wrong, not to a film
                // that is playing perfectly well.
                toldTaken = true
            }
            looking = false
            log(if (readies.isEmpty()) "no other machine has this one"
                else "also on " + readies.joinToString(", ") {
                    (it.srv?.name ?: "?") + " as " + it.ratingKey })
            // The main server is back, and this asked every machine twenty seconds
            // ago - so it is already known, and coming home need not wait for the
            // episode to end and need not cost a pause when it does. The picture
            // stays where it is: a live encode cannot be moved, and a direct play is
            // already being read off both. Only what this screen calls home changes,
            // so the next draw is home from the first moment.
            if (Api.standingBy() && readies.any { r ->
                    (r.srv?.base ?: "").trimEnd('/') == Api.homeBaseNow() }) {
                if (Api.comeHomeIfUp()) log("home again: " + Api.base)
            }
            offerTwo()
        }
    }

    /**
     * Whether the machine that keeps copies has started on this very film.
     *
     * Asked twice a minute while something is playing, and said once. A copy that
     * was already there before this started is not mentioned at all.
     */
    private fun watchTheCopying() {
        if (toldTaking || hadItAtStart != false) return
        val now = android.os.SystemClock.elapsedRealtime()
        if (now - lookedAt < 30_000) return
        lookedAt = now
        lifecycleScope.launch {
            if (Api.copyingNow() != ratingKey) return@launch
            toldTaking = true
            log("the copy has started on this one")
            android.widget.Toast.makeText(
                this@PlayerActivity,
                theirName() + " is taking a copy of this one",
                android.widget.Toast.LENGTH_LONG).show()
        }
    }

    /**
     * What is on screen, as a catalogue entry rather than a URL.
     *
     * The details were only ever fetched when somebody opened the sound or subtitle
     * panel, because until now nothing else needed them. Moving house does: the other
     * machine numbers its own library, so the film has to be asked for by what it is,
     * and a player that could not say what it was playing had nothing to ask with.
     * One request, once, held for the rest of the film.
     */
    private suspend fun whatIsPlaying(): Media? {
        val had = title.value?.takeIf { it.ratingKey == ratingKey }
        // A row off a shelf carries what draws a poster, not what finds the same film
        // on another machine. A film is looked for there by title and year, and the
        // year is not on the row - so the question went out with year nought, which
        // matches nothing, and every film read as held by no other machine. An
        // episode is looked for by programme and numbering, which the row does carry,
        // which is why episodes found their second machine and films never did.
        // and the name of the file itself, which is how the same film on two machines
        // is known to be the same copy of it. A row that had the title and the year
        // but no file name was taken as enough, so the pair was matched on a name the
        // player did not have - nothing matched, and a film held on both machines was
        // read off one of them all evening.
        val enough = had != null && !had.fileName.isNullOrEmpty() && (
            if (had.type == "episode")
                !had.grandparentTitle.isNullOrEmpty() &&
                    had.index != null && had.parentIndex != null
            else had.title.isNotEmpty() && (had.year ?: 0) > 0)
        if (enough) return had
        val got = runCatching { Api.item(ratingKey, playingOn()) }.getOrNull()
        // A number that names a film names it in one library only. The two machines
        // number their own, so the film that moved to the other one and came back as
        // key 43 was a different film here - and somebody watching one thing was put
        // on to another, mid-evening, by the server. If what comes back is not what
        // is on the screen, it is not this film and it is not used.
        if (got != null && had != null && had.title.isNotEmpty() &&
            got.title.isNotEmpty() && !got.title.equals(had.title, true)) {
            log("key " + ratingKey + " is \"" + got.title + "\" on " +
                (playingOn()?.base ?: Api.base) + " but this is \"" + had.title +
                "\" - not following it")
            return had
        }
        if (got != null) title.value = got
        return got ?: had
    }

    /**
     * Move to the other machine while there is still something in the buffer.
     *
     * Waiting for the stream to fail means the viewer watches it fail. A film that
     * has less than eight seconds left in hand and is not filling is one that is
     * about to stop, and the copy is a few milliseconds away on the same network.
     */
    /** What to call the machine a film would move to. Its name, never a description. */
    private fun theirName(): String =
        (ready?.srv?.name.orEmpty()
            .ifBlank { Api.standbyName }
            .ifBlank { Servers.hostOf(ready?.srv?.base.orEmpty()) }
            // and failing all that, the machine this app knows that is not this one.
            // The other three are empty until a copy has been found and the server has
            // said where its copy is, and a message that arrives before either was
            // calling a machine with a name "the other server".
            .ifBlank { otherKnownName() })
            .ifBlank { "the other server" }

    /** The name of a machine this app knows that is not the one playing. */
    private fun otherKnownName(): String {
        val here = srvBase.trimEnd('/').ifEmpty { Api.base }
        val them = Servers.all(this)
            .firstOrNull { !Servers.sameMachine(it, Server("", here, "")) }
            ?: return ""
        return them.name.ifBlank { Servers.hostOf(them.base) }
    }

    private fun leanOnTheCopy(why: String) {
        if (switching || onCastNow()) return
        // and somebody who would rather a film stopped than moved machines
        if (!Api.mayMove) {
            log("the film would move, but this viewer has asked it not to")
            return
        }
        if (handedOver &&
            android.os.SystemClock.elapsedRealtime() - movedAt < 60_000) return
        val other = ready
        if (other == null) {
            // nothing found in advance: look now rather than wait for the stream to
            // fail. It costs one request and the buffer is what pays for it.
            log("buffer low, no copy in hand - looking now")
            findTheOtherOne()
            return
        }
        if (other.srv?.base.isNullOrEmpty()) {
            log("the copy has no address of its own; not moving")
            return
        }
        // and it has to be this film. Splitting a film checks that both machines hold
        // the same file and moving one did not, so whatever had been found stood as
        // "the same film over there" without anything testing the claim.
        val playing = title.value
        val sameFilm = playing == null ||
            (!other.fileName.isNullOrEmpty() && other.fileName == playing.fileName) ||
            (playing.title.isNotEmpty() && other.title.equals(playing.title, true))
        if (!sameFilm) {
            log("what was found on " + (other.srv?.name ?: "the other machine") +
                " is \"" + other.title + "\", not \"" + playing?.title +
                "\" - not moving")
            ready = null
            readies = emptyList()
            return
        }
        // and never to the machine this is already playing from: moving there
        // restarts the film to arrive exactly where it is
        val to = other.srv!!.base.trimEnd('/')
        val on = srvBase.trimEnd('/').ifEmpty { Api.base.trimEnd('/') }
        if (to == on || Servers.sameMachine(other.srv!!, Server("", on, ""))) {
            log("the only other copy is this machine; not moving")
            return
        }
        switching = true
        handedOver = true
        movedAt = android.os.SystemClock.elapsedRealtime()
        // What was found from where we were standing is not what is true from where
        // we are going: the machine being moved to was the other machine a moment
        // ago, and holding on to that made the app find itself as its own second
        // source - so it read the film off one machine with two of them holding it.
        ready = null
        readies = emptyList()
        askedAt = 0L
        val at = maxOf(position(), lastGood) / 1000
        lifecycleScope.launch {
            // asked for the way this film was asked for here. It went with nothing:
            // whatever picture size or megabits the viewer had settled on was
            // dropped at the moment of moving, so somebody who had turned the
            // quality down - usually because their line could not carry it - was
            // handed the whole file by the machine that took over.
            fun asked(name: String): Int =
                Regex("[?&]" + name + "=([0-9]+)").find(streamUrl)
                    ?.groupValues?.getOrNull(1)?.toIntOrNull() ?: 0
            // and the soundtrack as it was asked for. Only the picture was carried
            // over, so a phone that had asked for stereo AAC - because nothing
            // passes through a Bluetooth pair, and the film's own track is Dolby
            // Digital Plus - was handed the original by the machine that took over,
            // and got sound it could not play.
            val ear = Regex("[?&]audio=([a-z0-9]+)").find(streamUrl)
                ?.groupValues?.getOrNull(1) ?: "passthrough"
            val (url, direct) = Api.playbackUrl(other, at,
                                                height = asked("height"),
                                                mbit = asked("mbit"),
                                                audio = ear,
                                                channels = asked("ch"))
            log("moving to " + other.srv?.base + " at " + at + "s: " +
                url.substringBefore("?"))
            // and the rest of the app goes with it: the shelves, what plays next, and
            // whatever is asked for after this film ends
            other.srv?.base?.let { Api.onTheCopyNow(it) }
            android.widget.Toast.makeText(
                this@PlayerActivity, why, android.widget.Toast.LENGTH_SHORT).show()
            startActivity(playIntent(this@PlayerActivity, other, at)
                              .putExtra("url", url)
                              .putExtra("direct", direct)
                              .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
            finish()
        }
    }

    private fun restartAt(positionMs: Long) {
        val seconds = (positionMs / 1000).coerceAtLeast(0)
        baseOffsetSec = seconds
        streamUrl = streamUrl.replace(Regex("offset=\\d+"), "offset=" + seconds)
        // tell the server how much of the bottom this screen is cropping
        val shift = croppedFraction()
        streamUrl = streamUrl.replace(Regex("&subshift=[0-9.]*"), "")
        if (shift > 0.005f) {
            streamUrl += "&subshift=" + String.format(java.util.Locale.US, "%.3f", shift)
        }
        // The subtitles come out of the server cut to the same point as the picture,
        // with their cue times counted from there. Left pointing at the old extract
        // after a seek, the track was minutes out of step with the film - which on
        // screen is one line sitting there while the scene moves on.
        askWhereItStarts()
        item = item.buildUpon().setUri(streamUrl).build()
        // the film is opened again, so its tracks are new objects and the choice made
        // against the old ones no longer names anything
        pickedSound = false
        offerTwo()
        wrapper?.baseChanged(streamStartMs())
        local?.let {
            it.setMediaItem(item)
            it.prepare()
            it.playWhenReady = true
        }
        subsUrlFor(seconds)?.let { drawOwnSubtitles(it) }
        refreshInfo()
    }

    /**
     * Move the subtitles by a tenth of a second, and fetch them again shortly.
     *
     * Each press only changes the number. The track is asked for again once the
     * pressing stops, because fetching it means starting the stream from the same
     * second, and doing that ten times in a row would be absurd.
     */
    /**
     * What the correction is filed under: this episode, or this film.
     *
     * Not the series. Subtitles in a season are not out by one amount - one episode
     * is half a second late and the next is two seconds early, because each was cut
     * from whatever release it came from. Only the ones somebody has corrected are
     * written down, which is a handful.
     */
    private fun shiftKey(): String = "l" + ratingKey

    /**
     * Which subtitle the correction belongs to.
     *
     * Two files for one episode are out by two different amounts - that is usually
     * why there are two - so a track inside the film is known by its stream number
     * and a file beside it by its name.
     */
    private fun shiftSub(): String {
        val n = subsIndex() ?: return ""
        if (n >= 0) return "t" + n
        return intent.getStringExtra("subsName").orEmpty().ifEmpty { "file" + n }
    }

    /**
     * Place this subtitle against the film's own sound.
     *
     * The server does the listening - it has the file - and answers with how far out
     * the subtitle is and whether it is sure. Quiet mode says nothing when it is not,
     * which is what "do it automatically" should feel like: a subtitle that cannot be
     * placed is left exactly where it was.
     */
    /** Say something over the picture for a few seconds, where the waiting was. */
    /**
     * Say how far the machine has got, over the picture, until there is text on screen.
     *
     * The file grows as the film is heard, so the track is turned on as soon as there
     * is a first line in it and the player asks for more as it goes. Until then the
     * only thing to look at is the film, and a number in the corner.
     */
    private fun followTheMaking(film: Media) {
        madeWatch?.cancel()
        madeWatch = lifecycleScope.launch {
            // It comes on for somebody who asked for it and for nobody else: a film
            // showing English, or showing nothing because Off was chosen, is showing
            // what was decided. The watching goes on either way, because the subtitle
            // list needs to know how far it has got and cannot ask for itself.
            var turnedOn = !wantsTheMade.value
            while (true) {
                val said = Api.makingSubtitles(film)
                val mine = said.key.isEmpty() || said.key == film.ratingKey
                // The title as it stands now: the track appears in it the moment the
                // file does, and this player's own copy was read before that.
                val fresh = runCatching { Api.metadata(film) }.getOrNull()
                    ?: title.value ?: film
                if (fresh !== title.value) title.value = fresh
                val made = fresh.textSubs().firstOrNull {
                    it.index < 0 && it.label.contains("ai-gen", true)
                }
                // offered only while there is nothing to show for it: once the file is
                // a track of its own, the track is the entry
                madeSaid.value = if (said.on && mine && made == null)
                    "Written from the sound - " + (said.at * 100).toInt() +
                    "%, not yet enough to start"
                    else null

                if (!turnedOn && mine && made != null) {
                    if (position() <= 0) {
                        // the picture has not started: switching now would carry a
                        // position of nothing, and the film would begin again
                        kotlinx.coroutines.delay(3000)
                        continue
                    }
                    turnedOn = true
                    val already = subsIndex() == made.index ||
                        (intent.getStringExtra("subsName") ?: "")
                            .contains("ai-gen", true) ||
                        (intent.getStringExtra("subsUrl") ?: "")
                            .contains("ai-gen", true)
                    if (!already) playWith(fresh, made.index)
                }

                if (!said.on) {
                    madeSaid.value = null          // nothing left to offer
                    if (said.ok == false && said.what.isNotEmpty()) {
                        sayForAMoment(said.what, 6)
                    }
                    return@launch
                }
                // said over the picture only for whoever asked for it
                if (!turnedOn && mine && wantsTheMade.value) {
                    sayForAMoment("Subtitles " + (said.at * 100).toInt() + "%  " +
                                  said.what, 5)
                }
                kotlinx.coroutines.delay(if (turnedOn) 15_000 else 5_000)
            }
        }
    }

    private var madeWatch: kotlinx.coroutines.Job? = null

    //: whether somebody chose the subtitle being written before there was a file, and
    //: what to call it in the list while there is not
    private val wantsTheMade = androidx.compose.runtime.mutableStateOf(false)
    private val madeSaid = androidx.compose.runtime.mutableStateOf<String?>(null)

    private fun sayForAMoment(what: String, seconds: Long = 5) {
        syncSaid.value = what
        lifecycleScope.launch {
            kotlinx.coroutines.delay(seconds * 1000)
            if (syncSaid.value == what) syncSaid.value = ""
        }
    }

    /**
     * A word from the house, said over the film.
     *
     * The same overlay a finished measurement uses: somebody watching should hear
     * that dinner is ready without leaving the picture, and a notice that only ever
     * appeared on the shelves would reach whoever was not watching anything.
     */
    /**
     * The room, read over the film.
     *
     * A watch party is people saying things while something plays, so what is said
     * belongs on the picture rather than behind it. Shown in the overlay a finished
     * measurement uses, named for whoever said it - nothing to press, and nothing
     * that stops the film.
     */
    /** How long a line from the party stands on the picture. */
    private val CHAT_HOLD = 10L

    private fun listenToTheRoom() {
        lifecycleScope.launch {
            var since = -1
            while (true) {
                if (since < 0) {
                    // what is already there is not news: start from the last line
                    since = runCatching { Api.chat(0, 0).lastOrNull()?.id ?: 0 }
                        .getOrDefault(0)
                    continue
                }
                val said = runCatching { Api.chat(since, 45) }.getOrDefault(emptyList())
                if (said.isEmpty()) {
                    kotlinx.coroutines.delay(3_000)
                    continue
                }
                since = said.last().id
                val goes = System.currentTimeMillis() + CHAT_HOLD * 1000
                said.forEach { roomSaid.add(Room(it.id, it.who, it.text, goes)) }
                // and each takes itself away
                lifecycleScope.launch {
                    kotlinx.coroutines.delay(CHAT_HOLD * 1000)
                    val now = System.currentTimeMillis()
                    roomSaid.removeAll { it.until <= now }
                }
            }
        }
    }

    private fun listenForNotices() {
        lifecycleScope.launch {
            var asked = false
            while (true) {
                val word = Api.notice(MainActivity.noticeSeen.value, if (asked) 45 else 0)
                asked = true
                if (word != null && word.id != MainActivity.noticeSeen.value) {
                    MainActivity.noticeSeen.value = word.id
                    sayForAMoment(word.text, 10)
                } else if (word == null) {
                    kotlinx.coroutines.delay(10_000)
                }
            }
        }
    }

    private fun syncSubtitles(loud: Boolean) {
        // by the key, not the title: the title is fetched only when a panel wants one,
        // so at the start of a playing there is not one - and the automatic run,
        // which happens exactly then, returned here without a word.
        val n = subsIndex() ?: return
        if (syncing.value) return
        syncing.value = true
        // Whether anybody asked. An automatic run - at the start of an episode, or
        // because a file was just fetched - draws nothing over the picture and says
        // nothing when it lands. Only what somebody pressed for reports back.
        syncAsked.value = loud
        // the overlay says it now, in the library's own colours
        syncJob = lifecycleScope.launch {
            val said = Api.syncSubtitle(ratingKey, n, shiftSub(), mi = mi)
            syncing.value = false
            val ends = (durationMs / 1000f).takeIf { it > 1f } ?: 0f
            if (said.sure) {
                // the server has written it down already; this only moves what is on
                // screen, so the cues shift without the film touching a frame
                subNudge.value = Math.round(said.offset * 10f) / 10f
                mended.value = said.steps
                inForce.value = kindOf(said)
                if (said.steps) {
                    // The film carries something the subtitle's master did not - a
                    // recap, a title card, an advert break - so everything after that
                    // point is later by a different amount. Say both numbers.
                    syncNote.value = said.said(ends)
                    // the file itself was rewritten, so the cues in hand are the old
                    // ones and have to be fetched again
                    subsUrlFor(baseOffsetSec)?.let { drawOwnSubtitles(it) }
                    if (loud) sayForAMoment("Subtitles: " + said.said(ends))
                } else {
                    syncNote.value = said.said(ends)
                    if (loud) sayForAMoment(
                        if (said.offset == 0f && said.parts.isEmpty())
                            "Subtitles are already in step"
                        else "Subtitles: " + said.said(ends))
                }
            } else {
                syncNote.value = "could not place it"
                if (loud) sayForAMoment(said.why.ifEmpty {
                    "Could not place this subtitle against the film" })
            }
        }
    }

    /**
     * Which row of text the subtitles sit on, counting up from the bottom.
     *
     * One row per choice: Bottom is the last row, Just up is the one above it, and so
     * on. Counted from the list rather than worked out from the fraction, so the steps
     * stay one row apart whatever the numbers behind them are.
     */
    /** Keeping the screen awake, and letting it go when nobody is watching. */
    private val screenHold = android.os.Handler(android.os.Looper.getMainLooper())

    /**
     * A film keeps the television awake; a paused film does not, for long.
     *
     * The flag is what stops a screensaver arriving in the middle of a scene, and it is
     * also what would hold a still frame and a row of controls on an OLED panel all
     * night. Five minutes after a pause it is let go, and the television is allowed to
     * do whatever it does about that.
     */
    private fun holdTheScreen(playing: Boolean) {
        screenHold.removeCallbacksAndMessages(null)
        if (playing) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            return
        }
        screenHold.postDelayed({
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }, 5 * 60 * 1000L)
    }

    /**
     * How far up from the bottom of the picture the text sits, as a share of it.
     *
     * One step of the Position setting is one row of text, whatever size the text is -
     * the same rule as the browser. A row is the text size times the leading the player
     * uses, both shares of the same height, so the two cancel out into one number.
     */
    /** Whether the text belongs in the black under the picture rather than on it. */
    //: A film that fills the set has no black under it. The text does not jump back
    //: onto the picture for that - it sits on the lowest row the panel has, which is
    //: what off the picture means when there is no room below it.
    private fun offPicture(): Boolean =
        subLook.base == "screen" && subLook.position < 0.9f

    /**
     * How far down the panel the top of the text goes, as a share of it.
     *
     * Measured from the top because that is the edge somebody is placing: the first
     * row starts where the picture ends, and a second line - or a line long enough to
     * wrap - grows downwards into the black rather than back over the film.
     */
    private fun subtitleDown(lines: Int = 1): Float {
        val text = if (sizedAt > 0f) sizedAt else SUB_BASE * subLook.size
        val row = text * SUB_LEADING
        // off the picture the list stops two rows down; a deeper number stored from
        // the picture's own list is drawn at that last step
        val steps = (subtitleRow(subLook.position) - 1).coerceIn(0, 2)
        // The top of the text is what is placed, so the rest of it has to be counted
        // or a two-line subtitle hangs off the bottom of the panel - which is what a
        // film with no black under it did: the top landed on the last row and the
        // second line was drawn past the edge of the screen.
        val tall = lines.coerceAtLeast(1) * row
        val floor = (1f - tall - SUB_AIR).coerceAtLeast(0f)
        return (1f - barShare() + SUB_AIR + steps * row).coerceIn(0f, floor)
    }

    private fun subtitleUp(lines: Int = 1): Float {
        // one step means the panel itself: as low as the screen goes, whatever shape
        // the film is
        if (subLook.position >= 0.9f) return SUB_AIR
        val steps = if (subLook.base == "screen")
            (subtitleRow(subLook.position) - 1).coerceIn(0, 2)
            else (subtitleRow(subLook.position) - 1).coerceAtLeast(0)
        // the size as the view has it, so a row is a row of the text actually drawn
        val text = if (sizedAt > 0f) sizedAt else SUB_BASE * subLook.size
        val row = text * SUB_LEADING
        if (subLook.base == "screen") {
            // Off the picture: the whole line below its bottom edge, then a row at a
            // time further down. The line's own height comes off first, or the text
            // would straddle the edge of the film rather than clear it.
            val bar = barShare()
            return (bar - lines * row - steps * row - SUB_AIR).coerceIn(SUB_AIR, 0.6f)
        }
        // On the picture means in it: the black under a scope film is not part of the
        // picture, so the bar is added and nought becomes the film's own bottom edge.
        // Zoom reports no bar - it fills the panel - so the same setting then puts the
        // text on the frame, which is where the picture now ends.
        return (barShare() + steps * row + SUB_AIR).coerceIn(0f, 0.6f)
    }

    /** The black under the picture, as a share of the view the cues are drawn in. */
    private fun barShare(): Float {
        val sv = subs ?: view?.subtitleView ?: return 0f
        val tall = if (sv.height > 0) sv.height else resources.displayMetrics.heightPixels
        return if (tall > 0) letterbox() / tall.toFloat() else 0f
    }

    //: a line break, named rather than escaped into the middle of an expression
    private val chr10 = Char(10)

    private fun subtitleRow(position: Float): Int {
        val at = SUB_POSITIONS.indexOfFirst { Math.abs(it.first - position) < 0.005f }
        return 1 + (if (at >= 0) at else Math.round(position / 0.08f))
    }

    /**
     * Put this subtitle back where its own file has it - or call off the measurement.
     *
     * While the film is being listened to, Reset is the way out: it is the only button
     * on that panel that means "undo what is happening", and waiting five minutes for
     * an answer nobody wants any more is not a thing to make somebody do.
     */
    private fun resetSubtitle() {
        if (syncing.value) {
            syncJob?.cancel()
            syncing.value = false
            sayForAMoment("Stopped")
            return
        }
        val n = subsIndex() ?: return
        lifecycleScope.launch {
            val done = Api.resetSubtitle(ratingKey, n, shiftKey(), shiftSub(),
                                        mi = mi)
            if (!done) {
                android.widget.Toast.makeText(
                    this@PlayerActivity, "The server would not take that",
                    android.widget.Toast.LENGTH_SHORT).show()
                return@launch
            }
            subNudge.value = 0f
            mended.value = false
            inForce.value = ""
            syncNote.value = "none"
            // the served file changes when a plan goes, so the cues in hand are stale
            subsUrlFor(baseOffsetSec)?.let { drawOwnSubtitles(it) }
            android.widget.Toast.makeText(
                this@PlayerActivity, "Subtitles put back as the file has them",
                android.widget.Toast.LENGTH_SHORT).show()
        }
    }

    /** What a plan amounts to, in the one word somebody would use for it. */
    private fun kindOf(plan: Api.Sync): String = when {
        plan.parts.size > 1 -> "steps"
        plan.parts.size == 1 && Math.abs(plan.parts[0].second - 1f) > 1e-6f -> "a drift"
        plan.parts.size == 1 -> "a static offset"
        else -> ""
    }

    private fun nudgeSubtitles(by: Float) {
        val to = (subNudge.value + by).coerceIn(-1800f, 1800f)
        subNudge.value = Math.round(to * 10f) / 10f
        // the server keeps it, for everybody: this subtitle is out by this much
        // whoever plays it. Naming a file also vouches for it - putting the timing
        // right is what the verified mark means.
        val n = subsIndex()
        lifecycleScope.launch {
            Api.setSubShift(shiftKey(), subNudge.value, shiftSub(),
                            name = if (n != null && n < 0)
                                intent.getStringExtra("subsName").orEmpty() else "",
                            index = if (n != null && n >= 0) n else null)
        }
        // nothing else to do: the text is ours to time, so the picture is not touched
    }

    //: the subtitle file this playing is using, held whole so its timing can be
    //: corrected without asking for it again - which would mean restarting the film
    private var ownCues: List<Triple<Long, Long, String>> = emptyList()
    private var cueTicker: kotlinx.coroutines.Job? = null

    /**
     * Draw the subtitles ourselves, from the file, at a time we choose.
     *
     * Media3 will happily side-load a subtitle file, but its timing is fixed to the
     * file: correcting it meant fetching a shifted copy, which meant a new media item,
     * which meant starting the film again. Holding the cues here costs one fetch and
     * makes a correction instant - and it is the same thing the browser does with the
     * cues it already has.
     */
    private fun drawOwnSubtitles(url: String) {
        cueTicker?.cancel()
        ownCues = emptyList()
        (subs ?: view?.subtitleView)?.setCues(emptyList())
        // the height comes from the cue itself now, so the view must not lift them a
        // second time
        (subs ?: view?.subtitleView)?.setPadding(0, 0, 0, 0)
        // Media3 must not draw any text of its own while we are drawing ours. A file
        // played straight from disk carries its own subtitle track, and the player
        // picks one up by itself - which is two copies of every line, one over the
        // other, in slightly different places.
        local?.let { p ->
            p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
                .setTrackTypeDisabled(androidx.media3.common.C.TRACK_TYPE_TEXT, true)
                .build()
        }
        cueTicker = lifecycleScope.launch {
            // A track inside the film has to be lifted out before it can be drawn, and
            // on a large file that is minutes the first time. Say so: an empty screen
            // with no explanation reads as broken subtitles.
            // For a moment, like anything else said over a film: it used to stay
            // up until the lift finished, which on a large file is minutes.
            val slow = launch {
                kotlinx.coroutines.delay(2500)
                sayForAMoment("Lifting the subtitles out of the film…")
            }
            ownCues = try {
                runCatching { Api.cues(url) }.getOrDefault(emptyList())
            } finally {
                slow.cancel()
                if (syncSaid.value.startsWith("Lifting")) syncSaid.value = ""
            }
            if (ownCues.isEmpty()) {
                // Nothing in it yet. A subtitle being written from the sound is empty
                // for a minute and then is not, and giving up here left the film with
                // no subtitles for the rest of its length. Ask again for a while.
                var tries = 0
                while (ownCues.isEmpty() && tries < 40) {
                    kotlinx.coroutines.delay(10_000)
                    tries++
                    ownCues = runCatching { Api.cues(url) }.getOrDefault(emptyList())
                }
                if (ownCues.isEmpty()) return@launch
            }
            // The server may have handed over the first stretch while it reads the
            // rest out of the film. Ask again as the film approaches the end of what
            // is in hand, and go on asking until the whole of it arrives.
            var partial = Api.cuesPartial
            if (partial) {
                launch {
                    while (partial) {
                        val covers = ownCues.lastOrNull()?.second ?: 0L
                        val left = covers - (local?.currentPosition ?: 0L)
                        kotlinx.coroutines.delay(
                            (left - 120_000L).coerceIn(5_000L, 60_000L))
                        val more = runCatching { Api.cues(url) }.getOrDefault(emptyList())
                        if (more.size > ownCues.size) ownCues = more
                        partial = Api.cuesPartial && more.isNotEmpty()
                    }
                }
            }
            var shown = emptyList<androidx.media3.common.text.Cue>()
            //: the shape the cues on screen were placed for. A cue is built once and
            //: left alone while its text stands, so a line placed for a letterboxed
            //: picture stayed where it was when zoom filled the panel. When the shape
            //: changes the same text is placed again.
            var placedFor = ""
            while (true) {
                // The subtitle view is the size of the picture, and until the first
                // frame arrives it is the size of the whole window - cues drawn in that
                // moment are the wrong size and in the wrong place, and then jump.
                if ((local?.videoSize?.height ?: 0) <= 0) {
                    kotlinx.coroutines.delay(120)
                    continue
                }
                val at = (local?.currentPosition ?: 0) - (subNudge.value * 1000).toLong()
                val live = ownCues.filter { at >= it.first && at < it.second }
                // A file usually starts the next line on the frame the last one ends,
                // and sometimes a little before; a subtitle for the deaf adds a line
                // for a sign or a song over the top of the dialogue. Two cues anchored
                // at the same height are drawn one on top of the other, which is two
                // different subtitles at once. The newest line is the one being read:
                // anything that began appreciably earlier has had its turn, and what
                // is left is drawn as one cue of at most two lines, as the browser
                // has always done it.
                val newest = live.maxOfOrNull { it.first } ?: 0L
                val said = live.filter { newest - it.first <= 250 }
                    .map { it.third }.takeLast(2)
                val now = (if (said.isEmpty()) emptyList()
                           else listOf(said.joinToString("\n")))
                    .map {
                        // Say where the line sits, rather than leaving it to the
                        // view's own bottom edge: a cue with no position of its own
                        // is drawn from that edge downwards, so a two-line subtitle
                        // lost its second line off the bottom of the screen. The
                        // bottom of the box is anchored at the height the Aa setting
                        // asks for, and the text grows upwards from there.
                        androidx.media3.common.text.Cue.Builder()
                            .setText(it)
                            .setTextAlignment(android.text.Layout.Alignment.ALIGN_CENTER)
                            // A share of the height, with the bottom of the box
                            // anchored at it. Line numbers - -1 for the bottom row,
                            // -2 for one row up - drew nothing at all in a subtitle
                            // view shorter than the screen, which is what a phone held
                            // upright gives: the picture is a band across the middle
                            // and the rows were counted off a box that is not there.
                            // The step is still one row of text, worked out from the
                            // size rather than counted by the player.
                            // On the picture the bottom of the text is placed and
                            // it grows upwards; off it the top is placed and it grows
                            // downwards into the black, so a second line never reaches
                            // back over the film and a line that wraps cannot either.
                            // both are told how tall the cue is: one places its
                            // top and the other its bottom, and either can be pushed
                            // off the panel by the line it does not know about
                            .setLine(if (offPicture()) subtitleDown(it.count { c -> c == chr10 } + 1)
                                     else 1f - subtitleUp(it.count { c -> c == chr10 } + 1),
                                     androidx.media3.common.text.Cue.LINE_TYPE_FRACTION)
                            .setLineAnchor(
                                if (offPicture())
                                    androidx.media3.common.text.Cue.ANCHOR_TYPE_START
                                else androidx.media3.common.text.Cue.ANCHOR_TYPE_END)
                            .setPosition(0.5f)
                            .setPositionAnchor(
                                androidx.media3.common.text.Cue.ANCHOR_TYPE_MIDDLE)
                            .setSize(0.9f)
                            .build()
                    }
                val shape = "%d/%.4f/%.4f".format(fitPref(), barShare(), sizedAt)
                if (now.size != shown.size ||
                    now.map { it.text } != shown.map { it.text } ||
                    shape != placedFor) {
                    shown = now
                    placedFor = shape
                    (subs ?: view?.subtitleView)?.setCues(now)
                }
                kotlinx.coroutines.delay(120)
            }
        }
    }

    /**
     * The subtitle track as it should be asked for from this point in the film.
     *
     * An encode starts its clock at zero wherever it began, so the text has to be
     * extracted from the same second. A direct play keeps the file's own timeline and
     * wants the whole thing, unshifted.
     */
    private fun subsUrlFor(seconds: Long): String? {
        val asked = intent.getStringExtra("subsUrl") ?: return null
        // no shift is asked of the server any more: the timing is applied here, to
        // cues already in hand, so it costs nothing and interrupts nothing
        return if (direct) asked
               else asked.replace(Regex("offset=[0-9.]+"), "offset=" +
                   (if (copyStartMs >= 0 && seconds == baseOffsetSec)
                        String.format(java.util.Locale.US, "%.3f", copyStartMs / 1000.0)
                    else seconds.toString()))
    }

    /**
     * Ask where an encoded stream will start and whether its picture goes through as
     * it is. A copied picture starts on the keyframe before the second asked for, and
     * the timeline and subtitles have to count from there. True when the address
     * changed.
     */
    private fun askWhereItStarts(): Boolean {
        copyStartMs = -1L
        if (direct || !streamUrl.contains("/gpu/stream")) return false
        val was = streamUrl
        streamUrl = streamUrl.replace("&copyv=1", "")
        val said = kotlinx.coroutines.runBlocking(kotlinx.coroutines.Dispatchers.IO) {
            kotlinx.coroutines.withTimeoutOrNull(1800) { Api.encodeStart(streamUrl) }
        }
        if (said != null && said.second && said.first >= 0) {
            copyStartMs = Math.round(said.first * 1000)
            streamUrl += "&copyv=1"
        }
        return streamUrl != was
    }

    /** Where the stream in hand starts, in film time. */
    private fun streamStartMs(): Long =
        if (copyStartMs >= 0) copyStartMs else baseOffsetSec * 1000

    /** Whether the sound is going somewhere that cannot take Dolby. */
    private fun throughHeadphones(): Boolean = throughHeadphones(this)

    /** The same stream, in stereo AAC - the one thing everything decodes. */
    private fun plainSound(url: String): String {
        if (!url.contains("/gpu/")) return url        // the file itself: nothing to ask
        var out = if (url.contains("audio=passthrough"))
            url.replace("audio=passthrough", "audio=aac") else url
        if (!out.contains("audio=")) out += "&audio=aac"
        if (!out.contains("&ch=")) out += "&ch=2"
        return out
    }

    /** The track number of a picture subtitle the device is drawing itself. */
    private fun pictureSub(): Int? =
        intent.getIntExtra("nativeSub", Int.MIN_VALUE).takeIf { it != Int.MIN_VALUE }

    /**
     * Hand a picture subtitle to the player rather than burning it into the film.
     *
     * PGS and VobSub are images, and Media3 decodes both - so a film played straight
     * from disk keeps every bit of its picture and the device draws the subtitle over
     * it. The alternative was an encode of the whole film to paint the words on,
     * which on a 4K disc runs at half real time and looks like a copy of itself.
     *
     * The track is found by counting: the container holds its subtitle streams in an
     * order, the library lists them in that order, and the player offers them in it.
     */
    private fun drawPictureSubtitle() {
        val want = pictureSub() ?: return
        val at = intent.getIntExtra("nativeSubAt", 0)
        val p = local ?: return
        var seen = 0
        for (group in p.currentTracks.groups) {
            if (group.type != androidx.media3.common.C.TRACK_TYPE_TEXT) continue
            for (i in 0 until group.length) {
                if (seen == at) {
                    if (group.isTrackSelected(i)) return       // already on
                    runCatching {
                        p.trackSelectionParameters = p.trackSelectionParameters
                            .buildUpon()
                            .setTrackTypeDisabled(
                                androidx.media3.common.C.TRACK_TYPE_TEXT, false)
                            .setOverrideForType(
                                androidx.media3.common.TrackSelectionOverride(
                                    group.mediaTrackGroup, i))
                            .build()
                    }
                    return
                }
                seen++
            }
        }
    }

    private fun current(): Player? = view?.player

    /**
     * Which machine these bytes are coming from, by its name.
     *
     * First on the line, because after a film has moved house it is the one thing
     * about the picture that has changed - and while two machines are carrying it
     * between them, the share beside it says how much each is doing.
     */
    private fun whichMachine(): String? {
        val where = srvBase.trimEnd('/').ifEmpty { return null }
        val srv = Servers.all(this).firstOrNull { it.base.trimEnd('/') == where }
        val name = srv?.name?.takeIf { it.isNotBlank() } ?: Servers.hostOf(where)
        return name
    }

    /**
     * The machine this film is coming from, as something Api can be handed.
     *
     * The key in the intent is that machine's number for the film, and no other
     * machine knows it. Looking it up on whichever server the app happens to be
     * pointed at is how a film played from the copy answered "cannot read this title
     * just now" the moment anybody opened the subtitle menu.
     */
    /**
     * Draw the next thing off a shelf, from the machine playing it or from the other.
     *
     * A draw was asked of one machine and given up on. The bytes fail over and the
     * shuffle did not: with the main server off, the film carried on off the copy and
     * the next episode came back "Could not draw from the shelf" - while the copy,
     * which holds the round and answers for itself when the main server cannot be
     * reached, was never asked.
     */
    private suspend fun drawFrom(shelf: String, back: Boolean): Api.Draw? {
        val here = playingOn()
        Api.shelfDraw(shelf, here, resume = false, back = back)?.let { return it }
        val other = standbyServer() ?: return null
        if (other.base.trimEnd('/') == (here?.base ?: "").trimEnd('/')) return null
        log("shelf draw fell back to " + other.base)
        return Api.shelfDraw(shelf, other, resume = false, back = back)
    }

    /** The machine to fall back on, as a server: the copy this house keeps. */
    private fun standbyServer(): Server? {
        val where = (Api.standby.ifEmpty { Api.standbyOut }).trimEnd('/')
        if (where.isEmpty()) return null
        return Servers.all(this).firstOrNull { it.base.trimEnd('/') == where }
            ?: Server(Servers.hostOf(where), where, srvToken)
    }

    private fun playingOn(): Server? {
        val where = srvBase.trimEnd('/')
        if (where.isEmpty() || where == Api.base) return null
        return Servers.all(this).firstOrNull { it.base.trimEnd('/') == where }
            ?: Server(Servers.hostOf(where), where, srvToken)
    }

    /** Written where adb can read it: what the player did, when a server went. */
    private fun log(what: String) {
        android.util.Log.i("Palladium", what)
        // and to the server, where somebody can read it. A television has no console
        // and no way to be asked; everything this player knew about why a film came
        // off one machine, or how it moved to another, was written to a log on the
        // device that nobody was ever going to see.
        val now = System.currentTimeMillis()
        if (now - lastTold < 400 || saidAlready.contains(what)) return
        lastTold = now
        saidAlready.add(what)
        if (saidAlready.size > 60) saidAlready.clear()
        runCatching {
            lifecycleScope.launch { Api.trace("player: " + what.take(300)) }
        }
    }

    //: what has been told to the server already, so a line repeated every second is
    //: said once
    private val saidAlready =
        java.util.Collections.synchronizedSet(mutableSetOf<String>())
    private var lastTold = 0L

    /** Which subtitle is on screen, by name, so it can be told from the others. */
    private fun subtitleName(): String? {
        if (streamUrl.contains("&burn=")) return "subtitles burned in"
        val p = current() ?: return null
        for (group in p.currentTracks.groups) {
            if (group.type != androidx.media3.common.C.TRACK_TYPE_TEXT) continue
            for (i in 0 until group.length) {
                if (!group.isTrackSelected(i)) continue
                val f = group.getTrackFormat(i)
                // "und" is what a side-loaded file says about itself; the name the
                // library gave the track is the one worth showing
                val name = f.label
                    ?: intent.getStringExtra("subsName")?.takeIf { it.isNotEmpty() }
                    ?: f.language?.takeIf { it != "und" }
                    ?: "on"
                return "sub: " + name.removeSuffix(".srt").take(40)
            }
        }
        return null
    }

    /**
     * Which of the next episode's subtitles to start with.
     *
     * Two things say a subtitle belongs here. It should be for this episode - its own
     * title, or the season and number written the way subtitle files write them - and
     * it should be of a piece with the one being watched, because a show is usually
     * subtitled by the same person week after week and the names run alike:
     * "A Series - 3x17 - The Episode", then 3x18, then 3x19.
     *
     * Being the right episode counts for more than being the right family. Failing
     * both, a file sitting beside the video beats a track buried inside it.
     */
    private fun matchingSubtitle(next: Media): Int? {
        val tracks = next.textSubs()
        if (tracks.isEmpty()) return null
        val fallback = (tracks.lastOrNull { it.index < 0 } ?: tracks.last()).index
        if (tracks.size == 1) return fallback

        val previous = intent.getStringExtra("subsName") ?: ""
        // "S3 E19  <the episode's name>" - its own title is the tail of the line
        val episodeTitle = next.subtitle.substringAfter("  ", "").trim()
        val season = next.parentIndex ?: 0
        val number = next.index ?: 0
        val tags = listOf("s%02de%02d".format(season, number),
                          "%dx%02d".format(season, number))

        fun score(name: String): Int {
            val low = name.lowercase()
            return likeness(previous, name) +
                2 * likeness(episodeTitle, name) +
                (if (number > 0 && tags.any { low.contains(it) }) 3 else 0)
        }
        val best = tracks.maxByOrNull { score(it.label) }
        return if (best != null && score(best.label) > 0) best.index else fallback
    }

    /** Words two names have in common, ignoring numbers and punctuation. */
    private fun likeness(a: String, b: String): Int {
        fun words(t: String) = t.lowercase()
            .split(Regex("[^a-z0-9]+"))
            .filter { it.length > 2 && !it.all(Char::isDigit) }
            .toSet()
        return words(a).intersect(words(b)).size
    }

    /** Is anything being drawn - a text track, or a subtitle burnt into the picture? */
    private fun subtitlesShowing(): Boolean {
        if (streamUrl.contains("&burn=")) return true
        // the ones we draw ourselves are not tracks of the player's
        if (ownCues.isNotEmpty()) return true
        val p = current() ?: return false
        return p.currentTracks.groups.any {
            it.type == androidx.media3.common.C.TRACK_TYPE_TEXT && it.isSelected
        }
    }

    /**
     * Five minutes from the end, fetch the next episode's subtitles.
     *
     * Early enough that the file is in place before auto-next fires, and late enough
     * that it is not fetched for an episode nobody finishes.
     */
    private fun maybePrefetchNext(pos: Long) {
        // a shuffle always has a next thing, even when this is a film and there is no
        // series behind it
        if (prefetched || durationMs <= 0) return
        if (!casually() && showKey.isEmpty()) return
        if (durationMs - pos > 5 * 60_000) return
        prefetched = true
        lifecycleScope.launch {
            // What comes next: the shuffle's own draw when something is being put on
            // casually - already settled by the server, and already in hand from
            // lookAhead - and the next episode otherwise. Casual watching is exactly
            // where nobody wants to be asked about subtitles.
            val next = if (casually()) (upNext ?: shelfPeek())
                       else Api.nextEpisode(showKey, season, number)
            if (next == null) return@launch
            Api.autoSubtitle(next, ratingKey)
        }
    }

    /**
     * Open the subtitle panel, reading the title again first.
     *
     * The copy the player was started with is a snapshot; a subtitle fetched five
     * minutes ago by the television downstairs would not be in it.
     */
    /**
     * Which soundtrack, with the film's details fetched first if they are not to hand.
     *
     * Setting the flag alone was not enough: the panel behind it only draws when the
     * details are loaded, so the button did nothing at all until something else had
     * fetched them.
     */
    private fun openSoundPanel() {
        lifecycleScope.launch {
            if (title.value == null || title.value?.ratingKey != ratingKey) {
                title.value = runCatching { Api.item(ratingKey, playingOn()) }.getOrNull()
            }
            // Opened either way. The tracks inside the file are what this panel is
            // mostly for and the player already knows them; the catalogue is only
            // needed for what could be fetched. A menu that refuses to open because
            // a lookup failed is a menu that cannot be used at all.
            onlyOne(soundOpen)
        }
    }

    private fun openSubtitlePanel() {
        lifecycleScope.launch {
            if (title.value == null || title.value?.ratingKey != ratingKey) {
                title.value = runCatching { Api.item(ratingKey, playingOn()) }.getOrNull()
            } else {
                title.value = runCatching { Api.metadata(title.value!!) }.getOrNull()
                    ?: title.value
            }
            run {
                onlyOne(tracksOpen)
            }
        }
    }

    /** Which subtitle stream this player was started with, if any. */
    /**
     * Which subtitle was chosen, or nothing.
     *
     * A file beside the film is numbered -1, -2 and so on, so "-1 means none" quietly
     * threw away a downloaded subtitle on every restart - a change of soundtrack, of
     * quality, or a pair of headphones connecting. The sentinel has to be a number no
     * subtitle can have.
     */
    /**
     * The same subtitle on the other machine, by what it is rather than by its number.
     *
     * Two libraries number their streams for themselves, so the number chosen here
     * means something else there - or nothing at all. The language and the name
     * first, then the language alone, then the same place in the list, which is the
     * best that is left.
     */
    private fun sameSub(want: Int?, here: List<SubTrack>?, there: List<SubTrack>?): Int? {
        if (want == null || here.isNullOrEmpty() || there.isNullOrEmpty()) return null
        val mine = here.firstOrNull { it.index == want } ?: return null
        return (there.firstOrNull { it.language == mine.language && it.label == mine.label }
            ?: there.firstOrNull { it.language.isNotEmpty() && it.language == mine.language }
            ?: here.indexOf(mine).let { at -> there.getOrNull(at) })?.index
    }

    /** The same soundtrack on the other machine, read the same way. */
    private fun sameAudio(want: Int?, here: List<AudioTrack>?,
                          there: List<AudioTrack>?): Int? {
        if (want == null || here.isNullOrEmpty() || there.isNullOrEmpty()) return null
        val mine = here.firstOrNull { it.index == want } ?: return null
        return (there.firstOrNull { it.language == mine.language && it.label == mine.label }
            ?: there.firstOrNull { it.language.isNotEmpty() && it.language == mine.language }
            ?: here.indexOf(mine).let { at -> there.getOrNull(at) })?.index
    }

    private fun chosenSub(): Int? =
        intent.getIntExtra("subIndex", Int.MIN_VALUE).takeIf { it != Int.MIN_VALUE }

    private fun subsIndex(): Int? =
        // a picture subtitle the device is drawing has no fetched file behind it, so
        // there is no address to read the number off - it is the number we asked the
        // player to draw. Without this the menu said "off" with one on the screen.
        pictureSub()
            ?: intent.getStringExtra("subsUrl")
                ?.substringAfter("index=", "")?.substringBefore("&")?.toIntOrNull()

    /**
     * Start the film again at this second, with a different subtitle.
     *
     * A subtitle is part of how the stream is built - burned into the picture, lifted
     * out of the container, or hung beside it - so changing one means asking for the
     * stream again. It comes back where it was left.
     */
    private fun playWith(film: Media, track: Int?) {
        tracksOpen.value = false
        val at = position() / 1000
        // Casual travels with the playing. Choosing a subtitle restarts the stream in
        // a new activity, and without this that activity reported as an ordinary
        // watching - which wrote a progress row and put the title in Continue
        // watching, from the shelf that exists to keep it out.
        startActivity(playIntent(this, film, at, track, chosenAudio(),
                                 chosenHeight(), chosenRate(),
                                 plainSound = soundToKeep())
                          .putExtra("casual", casually()).putExtra("shelf", offTheShelf())
                          // carried across: choosing the one being written turns the
                          // current subtitle off, which starts the film again, and
                          // the wish must not be lost with it
                          .putExtra("wantMade", wantsTheMade.value))
        finish()
    }

    /** Whether this was put on from the casual shelf rather than chosen. */
    private fun casually(): Boolean = intent.getBooleanExtra("casual", false)

    /** Which shelf this was drawn off, where it was drawn off one. The shelf keeps
     *  the place, and Next draws from it rather than handing over the next episode
     *  of whatever series this happens to belong to. */
    private fun offTheShelf(): String = intent.getStringExtra("shelf") ?: ""

    /** What the shelf this came off draws next; null when it did not come off one. */
    private suspend fun shelfPeek(): Media? =
        offTheShelf().takeIf { it.isNotEmpty() }?.let { Api.shelfPeek(it, playingOn()) }

    /** What the shuffle will play next, fetched while this one is starting. */
    @Volatile private var upNext: Media? = null

    /**
     * Ask now, so pressing Next is not a decision away.
     *
     * The server settles what comes next when something starts playing, so this is
     * the same film the button will play - and it can be named on screen.
     */
    private fun lookAhead() {
        if (!casually()) return
        lifecycleScope.launch {
            upNext = runCatching { shelfPeek() }.getOrNull()
            upNext?.let {
                nameBar?.append("        next: " + (it.grandparentTitle ?: it.title))
            }
        }
    }

    /**
     * The next thing, and the one before.
     *
     * In a series, the episodes either side. Watching casually, the next draw and the
     * previous one - the shuffle's own order, not another roll of the dice, because a
     * person pressing "previous" means what they were just watching.
     */
    private fun step(forward: Boolean) {
        if (drawing.value) return            // one press, one draw
        // The picture and the sound stop with the press, not when the answer comes.
        drawing.value = true
        runCatching { current()?.pause() }
        lifecycleScope.launch {
            val here = title.value
                ?: runCatching { Api.item(ratingKey, playingOn()) }.getOrNull()
            var startAt = 0L
            val to = if (casually()) {
                // forward is already known: it was settled when this one started
                // From the shelf this playing came off, where it came off one.
                // Asking the old casual shelf instead drew a title from somebody's
                // marks and left the shelf's own round exactly where it was, so Next
                // played something plausible and the hat never moved.
                val shelf = offTheShelf()
                val drawn = if (shelf.isNotEmpty())
                                drawFrom(shelf, back = !forward)
                            else null
                startAt = drawn?.resumeAt ?: 0L
                drawn?.media
            } else if (here == null) {
                null
            } else if (forward) {
                Api.nextEpisode(here.grandparentKey ?: "",
                                here.parentIndex ?: 0, here.index ?: 0)
            } else {
                Api.previous(here)
            }
            if (to == null) {
                // A shuffle does not run out - it starts the shelf again - so saying
                // "nothing after this one" there would be untrue. The only reason a
                // draw comes back empty is that the server could not be asked.
                val why = if (casually()) "Could not draw from the shelf"
                          else if (forward) "Nothing after this one"
                          else "Nothing before this one"
                android.widget.Toast.makeText(this@PlayerActivity, why,
                                              android.widget.Toast.LENGTH_SHORT).show()
                // nothing is coming: the film that was stopped for it carries on
                drawing.value = false
                runCatching { current()?.play() }
                return@launch
            }
            // the next thing needs its own subtitle chosen: passing none turned them
            // off, verified track or not. The list comes with the full metadata only.
            val full = runCatching { Api.metadata(to) }.getOrNull() ?: to
            val pick = full.pickedSub
                ?: full.openWith(Api.myLanguage)?.index
            startActivity(playIntent(this@PlayerActivity, full, startAt, pick,
                                     height = chosenHeight(), mbit = chosenRate(),
                                     plainSound = soundToKeep())
                              .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
            finish()
        }
    }

    //: whether the chosen soundtrack has been picked out of the file already
    private var pickedSound = false

    /**
     * Play the soundtrack somebody chose, out of the film itself.
     *
     * The film used to be re-encoded to hand over one of its own tracks, which costs
     * the card a whole film's work to do what the player does for nothing - and turns
     * a film that would have played untouched into one that cannot be split, cannot
     * be seeked cheaply, and is sized by an encoder's guess.
     *
     * Matched by where the track sits among the film's soundtracks rather than by the
     * number the server gave it: those are the numbers ffmpeg uses for every stream in
     * the file, pictures and subtitles included, and the player only ever sees the
     * sound.
     */
    private fun pickTheChosenSound(tracks: androidx.media3.common.Tracks) {
        if (pickedSound || !direct || onCastNow()) return
        val want = chosenAudio() ?: return
        val film = title.value ?: return
        val nth = film.audioStreams.indexOfFirst { it.index == want }
        if (nth < 0) return
        var seen = 0
        for (group in tracks.groups) {
            if (group.type != androidx.media3.common.C.TRACK_TYPE_AUDIO) continue
            for (i in 0 until group.length) {
                if (seen == nth && group.isTrackSupported(i)) {
                    local?.let { p ->
                        p.trackSelectionParameters = p.trackSelectionParameters
                            .buildUpon()
                            .setOverrideForType(
                                androidx.media3.common.TrackSelectionOverride(
                                    group.mediaTrackGroup, i))
                            .build()
                    }
                    pickedSound = true
                    log("playing soundtrack " + want + " out of the file")
                    return
                }
                seen += 1
            }
        }
    }

    /** Which soundtrack this playing was started with, if one was chosen. */
    private fun chosenAudio(): Int? =
        intent.getIntExtra("audioIndex", -1).takeIf { it >= 0 }

    /** The picture size this playing was asked for at; 0 is the film as it is. */
    private fun chosenHeight(): Int = intent.getIntExtra("height", 0)

    /** And how many megabits, or 0 for whatever the encoder decides. */
    private fun chosenRate(): Int = intent.getIntExtra("mbit", 0)

    /** Whether this playing was already asked for in plain stereo AAC. */
    private fun soundIsPlain(): Boolean = intent.getBooleanExtra("plainSound", false)

    /** And whether somebody said so, as against the app working it out. */
    private fun soundWasChosen(): Boolean = intent.getBooleanExtra("soundChosen", false)

    /** What to hand the next playing: a decision if there was one, otherwise nothing. */
    private fun soundToKeep(): Boolean? =
        if (soundWasChosen()) soundIsPlain() else null

    /**
     * Ask for the sound one way or the other, by hand.
     *
     * Headphones are meant to be noticed, and usually are. When they are not - a box
     * that reports its television's capabilities whatever it is actually playing
     * through - this is how somebody says it themselves.
     */
    private fun playSoundAs(film: Media, plain: Boolean) {
        qualityOpen.value = false
        if (plain == soundIsPlain()) return
        val at = position() / 1000
        val sub = chosenSub()
        startActivity(playIntent(this, film, at, sub, chosenAudio(),
                                 chosenHeight(), chosenRate(), plainSound = plain)
                          .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
        finish()
    }

    /**
     * Open the quality panel, and ask the server what it allows from here.
     *
     * A ceiling is not an error and does not stop anything being chosen - what
     * arrives is whichever is lower - but saying so is better than a viewer choosing
     * 1080p, being sent 720p and drawing their own conclusions about the software.
     */
    private fun openQualityPanel() {
        onlyOne(qualityOpen)
        lifecycleScope.launch {
            // The panel is drawn from the film itself - it has to hand it back to be
            // started again at another size - and nothing has necessarily fetched it
            // yet. Without this the first press of the gear opened nothing at all,
            // and it worked only once some other panel had loaded the title.
            if (title.value == null || title.value?.ratingKey != ratingKey) {
                title.value = runCatching { Api.item(ratingKey, playingOn()) }.getOrNull()
            }
            if (title.value == null) {
                qualityOpen.value = false
                android.widget.Toast.makeText(
                    this@PlayerActivity, "Cannot read this title just now",
                    android.widget.Toast.LENGTH_SHORT).show()
                return@launch
            }
            qualityCap.value = runCatching { Api.qualityCeiling() }.getOrDefault("")
            capNow.value = runCatching { Api.effectiveCeiling() }.getOrDefault(Pair(0, 0))
        }
    }

    /**
     * Start again at another size, from where this one has got to.
     *
     * The size and the bitrate are settled while ffmpeg reads the file, so this is a
     * new stream rather than a switch - the same as choosing a soundtrack.
     */
    private fun playQuality(film: Media, height: Int, mbit: Int) {
        qualityOpen.value = false
        if (height == chosenHeight() && mbit == chosenRate()) return
        val at = position() / 1000
        val sub = chosenSub()
        startActivity(playIntent(this, film, at, sub, chosenAudio(), height, mbit,
                                 plainSound = soundToKeep())
                          .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
        finish()
    }

    /**
     * Start again on another soundtrack, from where this one has got to.
     *
     * Which soundtrack is heard is settled while ffmpeg reads the file, so this is a
     * new stream rather than a switch - the same as choosing a subtitle to burn in.
     */
    private fun playSound(film: Media, audio: Int) {
        soundOpen.value = false
        val at = position() / 1000
        val sub = chosenSub()
        startActivity(playIntent(this, film, at, sub, audio,
                                 chosenHeight(), chosenRate(),
                                 plainSound = soundToKeep())
                          .putExtra("casual", casually()).putExtra("shelf", offTheShelf()))
        finish()
    }

    /** Absolute position in the film, whichever stream and whichever player. */
    private fun position(): Long {
        val p = current() ?: return 0
        val at = p.currentPosition.coerceAtLeast(0)
        // OffsetPlayer already reports film time; anything else reports stream time
        return if (p is OffsetPlayer || direct) at else streamStartMs() + at
    }

    /**
     * What this device is called when it reports in.
     *
     * The AV panel shows one thing at a time and picks between clients, so the name has
     * to distinguish them - and "Streamer" is what the television is known as here.
     */
    /** Whether this is a television, asked of the system rather than remembered. */
    private fun onTelevision(): Boolean {
        val ui = getSystemService(UI_MODE_SERVICE) as android.app.UiModeManager
        return ui.currentModeType == android.content.res.Configuration.UI_MODE_TYPE_TELEVISION
    }

    private fun deviceName(): String {
        val ui = getSystemService(UI_MODE_SERVICE) as android.app.UiModeManager
        return if (ui.currentModeType == android.content.res.Configuration.UI_MODE_TYPE_TELEVISION)
            "Streamer" else android.os.Build.MODEL
    }

    /**
     * For the one report that must not be lost.
     *
     * Everything said while a film plays belongs to the activity and stops with it.
     * The closing word does not: it is sent as the player is being torn down, and on
     * the activity's own scope it was cancelled before it reached the server - so the
     * last and most accurate position was the one least likely to arrive.
     */
    private val lastWord = kotlinx.coroutines.CoroutineScope(
        kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.IO)

    private fun report(state: String = "") {
        val pos = position()
        if (ratingKey.isEmpty()) return
        if (pos > 0) {
            lastGood = maxOf(lastGood, pos)
            // half a minute of playing since the last trouble is a working stream,
            // and it should not be spending a budget the last hour used up
            if (pos > faultAt + 30_000) { retried = 0; faultAt = 0 }
        }
        val say = state.ifEmpty {
            val p = current()
            when {
                p == null -> "stopped"
                p.isPlaying -> "playing"
                p.playbackState == Player.STATE_BUFFERING -> "buffering"
                else -> "paused"
            }
        }
        // a stop is worth sending even at position zero; anything else needs a position
        if (pos <= 0 && say != "stopped") return
        // "off" is a statement: an episode watched through without subtitles tells the
        // series to stop fetching them. With one on, the report names it - a number for
        // a track inside the film, the release name for a file beside it - because
        // "on" said nothing about which, and only a subtitle the server had fetched
        // itself could then be verified by watching.
        val subs = if (!subtitlesShowing()) "off" else subsIndex()?.let { i ->
            if (i >= 0) "t" + i
            else intent.getStringExtra("subsName")?.takeIf { it.isNotEmpty() } ?: "on"
        } ?: "on"
        // a closing report outlives the player; everything else stops with it
        val dur = if (durationMs > 0) durationMs else 0
        val who = deviceName()
        val mine = casually()
        val shelf = offTheShelf()
        if (state == "stopped") {
            // The closing word, on a scope that outlives the player: it is sent as the
            // activity is being torn down, and on the activity's own scope it was
            // cancelled before it reached the server - so the last and most accurate
            // position was the one least likely to arrive. Nothing is drawn from here:
            // there is no screen left to draw on.
            settling = lastWord.launch {
                runCatching {
                    Api.progressAt(srvBase, srvToken, ratingKey, pos, dur, say, who,
                                   subs, casual = mine, shelf = shelf)
                }
            }
        } else lifecycleScope.launch {
            val verified = Api.progressAt(
                srvBase, srvToken, ratingKey, pos, dur, say, who, subs,
                // putting something on is not watching it
                casual = mine, shelf = shelf,
                info = runCatching { buildInfo() }.getOrDefault(""))
            // The level this file needs, as the server has it now. A file measured
            // after it was opened is corrected where it stands, eased in over half a
            // minute rather than arriving between two buffers.
            Api.gainSaid?.let { said ->
                if (said != gain.decibels) {
                    log("easing the sound to " + said + " dB")
                    gain.easeTo(said, 30f)
                }
            }
            // Said once per film, and only once the sink has had a chance to settle:
            // whether the sound reaches the processor at all. Without it, "it does not
            // sound any louder" could not be told from "it is being passed through",
            // and the only way to find out was to read the television's own screen.
            if (!saidHowSound && pos > 10) {
                saidHowSound = true
                val want = Api.gainSaid ?: gain.decibels
                log("sound is " + (if (gain.working) "decoded here" else "passed through") +
                    ", correction wanted " + want + " dB, " +
                    (if (gain.working) "applied" else "not applied"))
            }
            // the subtitle just earned its mark: read the title again so an open panel
            // turns its tick, and say so on screen for a moment either way
            if (verified && !saidVerified) {
                saidVerified = true
                val again = title.value?.let { Api.metadata(it) }
                    ?: Api.item(ratingKey, playingOn())
                runCatching { again }.getOrNull()?.let { title.value = it }
                sayForAMoment("Subtitle verified")
            }
        }
        maybePrefetchNext(pos)
    }

    override fun onStop() {
        super.onStop()
        report()
        // a film out of sight has no business holding anybody's headphones open
        keepLinkWarm(false)
        // playback continues on the television when the phone screen goes off
        if (current() !== cast) local?.playWhenReady = false
    }

    override fun finish() {
        // one last word, so nothing is left frozen on the panel
        report("stopped")
        super.finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        cueTicker?.cancel()
        keepLinkWarm(false)
        runCatching { unregisterReceiver(earsWentAway) }
        // the session goes before the player it speaks for
        session?.release()
        session = null
        local?.release()
        cast?.setSessionAvailabilityListener(null)
        cast?.release()
        local = null
        cast = null
    }
}
