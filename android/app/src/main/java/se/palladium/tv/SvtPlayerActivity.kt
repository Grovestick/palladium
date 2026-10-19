package se.palladium.tv

import android.os.Bundle
import android.view.WindowManager
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.trackselection.DefaultTrackSelector
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.launch

/**
 * A film from SVT Play, full screen, until Back: the stream is SVT's own HLS, asked of
 * the server when the film is opened and played straight from SVT - from Sweden only.
 * Seeking with the bar, SVT's Swedish subtitles on. Nothing is encoded here.
 */
class SvtPlayerActivity : androidx.appcompat.app.AppCompatActivity() {
    private var player: ExoPlayer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val id = intent.getStringExtra("svt") ?: ""
        val title = intent.getStringExtra("title") ?: ""
        key = Route.svtKey(id)
        val view = PlayerView(this)
        view.useController = true
        view.setShowSubtitleButton(true)
        setContentView(view)
        // SVT's subtitles are Swedish and come inside the stream: chosen unless turned off
        val tracks = DefaultTrackSelector(this).apply {
            setParameters(buildUponParameters().setPreferredTextLanguage("sv")
                .setSelectUndeterminedTextLanguage(true))
        }
        val made = ExoPlayer.Builder(this).setTrackSelector(tracks).build()
        player = made
        view.player = made
        made.addListener(object : Player.Listener {
            // the place told as soon as the picture is up, not ten seconds later
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY && !toldOnce) tell("playing")
            }
            override fun onPlayerError(error: PlaybackException) {
                android.widget.Toast.makeText(this@SvtPlayerActivity,
                    Route.svtFailure(error.errorCode), android.widget.Toast.LENGTH_LONG).show()
            }
        })
        lifecycleScope.launch {
            val said = runCatching { Api.svtStream(id) }.getOrNull()
            if (said == null || said.first.isEmpty()) {
                android.widget.Toast.makeText(this@SvtPlayerActivity,
                    said?.second?.ifEmpty { null } ?: "SVT Play has no stream of this to play here",
                    android.widget.Toast.LENGTH_LONG).show()
                finish()
                return@launch
            }
            if (player !== made) return@launch
            // where this viewer left it, carried on from
            if (said.third > 0) made.seekTo(said.third)
            made.setMediaItem(MediaItem.Builder().setUri(said.first)
                                  .setMimeType(MimeTypes.APPLICATION_M3U8)
                                  .setMediaMetadata(androidx.media3.common.MediaMetadata.Builder()
                                                        .setTitle(title).build())
                                  .build(), said.third.coerceAtLeast(0L))
            ticks.postDelayed(report, 10_000)
            made.playWhenReady = true
            made.prepare()
            lifecycleScope.launch { runCatching { Api.trace("svt play: $title ($id)") } }
        }
    }

    //: where it is, told to the server every ten seconds while it plays: Continue
    //: watching and the next opening carry on from there
    private var key = ""
    private val ticks = android.os.Handler(android.os.Looper.getMainLooper())
    private val report = object : Runnable {
        override fun run() {
            tell("playing")
            ticks.postDelayed(this, 10_000)
        }
    }

    //: whether a report has gone out yet; the first one's outcome goes in the server's log
    private var toldOnce = false

    private fun tell(state: String) {
        val p = player ?: return
        val at = p.currentPosition
        val length = p.duration.takeIf { it > 0 } ?: return
        if (key.isEmpty()) return
        val s = if (state == "playing" && !p.isPlaying) "paused" else state
        val first = !toldOnce
        toldOnce = true
        lifecycleScope.launch {
            val went = runCatching { Api.progressAt(Api.base, Api.token, key, at, length, s) }
            if (first) runCatching {
                Api.trace("svt progress: $key at ${at / 1000} s of ${length / 1000} s to " +
                          Api.base + " - " + (went.exceptionOrNull()?.toString() ?: "sent"))
            }
        }
    }

    override fun onStop() {
        super.onStop()
        ticks.removeCallbacks(report)
        tell("stopped")
        player?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        player?.release()
        player = null
    }
}
