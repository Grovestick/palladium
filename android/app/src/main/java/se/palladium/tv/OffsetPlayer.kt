package se.palladium.tv

import androidx.media3.common.C
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.Timeline

/**
 * Makes a live encode behave like a file.
 *
 * The GPU engine hands over one fragmented MP4 that starts at the requested offset and
 * has no length and no index: ExoPlayer cannot scrub it, so the progress bar sat at zero
 * and the skip buttons did nothing. This wrapper puts the film's real timeline back in
 * front of the controls -
 *
 *   position  = where the encode started + how far into it we are
 *   duration  = the length the library knows about
 *   seek      = ask the server to start again from there
 *
 * Direct-played files need none of this; they are seekable already and are handed to the
 * controls unwrapped.
 */
//: the one period and window of the timeline this player presents
private val WHOLE_FILM = Any()

class OffsetPlayer(
    inner: Player,
    private var baseMs: Long,
    private val totalMs: Long,
    private val restartAt: (Long) -> Unit,
) : ForwardingPlayer(inner) {

    fun baseChanged(newBaseMs: Long) {
        baseMs = newBaseMs
    }

    override fun getDuration(): Long = if (totalMs > 0) totalMs else super.getDuration()

    /**
     * A timeline with the film's length in it.
     *
     * This is the one that matters, and it took a photograph of the screen to see it:
     * the controls do not ask the player how long the film is. They read the duration
     * out of the current timeline window - and a live encode's window has no length,
     * so the bar was handed a duration of zero. It drew "00:00" at the right-hand end
     * and had no scale to place anything on, which is a bar that cannot be dragged
     * however seekable the player says it is.
     */
    override fun getCurrentTimeline(): Timeline =
        if (totalMs > 0) timeline else super.getCurrentTimeline()

    private val timeline = object : Timeline() {
        override fun getWindowCount(): Int = 1

        override fun getWindow(windowIndex: Int, window: Window,
                               defaultPositionProjectionUs: Long): Window {
            window.set(
                /* uid= */ WHOLE_FILM,
                /* mediaItem= */ MediaItem.EMPTY,
                /* manifest= */ null,
                /* presentationStartTimeMs= */ C.TIME_UNSET,
                /* windowStartTimeMs= */ C.TIME_UNSET,
                /* elapsedRealtimeEpochOffsetMs= */ C.TIME_UNSET,
                /* isSeekable= */ true,
                /* isDynamic= */ false,
                /* liveConfiguration= */ null,
                /* defaultPositionUs= */ 0,
                /* durationUs= */ totalMs * 1000,
                /* firstPeriodIndex= */ 0,
                /* lastPeriodIndex= */ 0,
                /* positionInFirstPeriodUs= */ 0)
            return window
        }

        override fun getPeriodCount(): Int = 1

        override fun getPeriod(periodIndex: Int, period: Period, setIds: Boolean): Period =
            period.set(if (setIds) WHOLE_FILM else null, if (setIds) WHOLE_FILM else null,
                       0, totalMs * 1000, 0)

        override fun getIndexOfPeriod(uid: Any): Int =
            if (uid == WHOLE_FILM) 0 else C.INDEX_UNSET

        override fun getUidOfPeriod(periodIndex: Int): Any = WHOLE_FILM
    }

    override fun getContentDuration(): Long = duration

    override fun getCurrentPosition(): Long = baseMs + super.getCurrentPosition().coerceAtLeast(0)

    override fun getContentPosition(): Long = currentPosition

    override fun getBufferedPosition(): Long =
        baseMs + super.getBufferedPosition().coerceAtLeast(0)

    override fun getContentBufferedPosition(): Long = bufferedPosition

    override fun getTotalBufferedDuration(): Long = super.getTotalBufferedDuration()

    /** Every seek is a fresh encode, so it is clamped and handed to the server. */
    override fun seekTo(positionMs: Long) = restartAt(clamp(positionMs))

    override fun seekTo(mediaItemIndex: Int, positionMs: Long) = restartAt(clamp(positionMs))

    override fun seekForward() = restartAt(clamp(currentPosition + seekForwardIncrement))

    override fun seekBack() = restartAt(clamp(currentPosition - seekBackIncrement))

    override fun seekToNext() = seekForward()

    override fun seekToPrevious() = seekBack()

    override fun seekToNextMediaItem() = seekForward()

    override fun seekToPreviousMediaItem() = seekBack()

    private fun clamp(ms: Long): Long =
        ms.coerceIn(0, if (totalMs > 0) totalMs - 5_000 else Long.MAX_VALUE)

    /**
     * Media3 asks this before it will let anybody drag the bar - and it is not the
     * same question as "is the seek command available".
     *
     * The answer comes from the timeline window underneath, and a live encode has no
     * window worth the name: not seekable, no duration. So the bar was drawn, showed
     * the right numbers, and refused to move. That is exactly what a film played with
     * headphones on looks like, since headphones mean an encode rather than the file.
     */
    override fun isCurrentMediaItemSeekable(): Boolean = true

    /** And it is not live, whatever the stream underneath looks like. */
    override fun isCurrentMediaItemLive(): Boolean = false

    /** The controls grey themselves out unless the player says seeking is possible. */
    override fun isCommandAvailable(command: Int): Boolean = when (command) {
        Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM,
        Player.COMMAND_SEEK_BACK,
        Player.COMMAND_SEEK_FORWARD,
        Player.COMMAND_SEEK_TO_NEXT,
        Player.COMMAND_SEEK_TO_PREVIOUS,
        Player.COMMAND_GET_CURRENT_MEDIA_ITEM -> true
        else -> super.isCommandAvailable(command)
    }

    override fun getAvailableCommands(): Player.Commands =
        super.getAvailableCommands().buildUpon()
            .addAll(
                Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM,
                Player.COMMAND_SEEK_BACK,
                Player.COMMAND_SEEK_FORWARD,
            )
            .build()
}
