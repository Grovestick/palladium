package se.palladium.tv

import android.content.Context
import android.os.Looper
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.text.TextOutput
import androidx.media3.exoplayer.text.TextRenderer
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * The sound made even between one release and the next.
 *
 * Every release is mixed at whatever level whoever made it felt like. One episode of a
 * programme arrives eight decibels under the film before it, and the remote is used to
 * put that right - which then has to be put back for the next thing. The server
 * measures how loud a file is, in LUFS, and says how far off it is; this turns that
 * into gain on the way to the speakers.
 *
 * Nothing is done to a sound that is already near enough, and nothing can be done to
 * one that is being passed through untouched to an amplifier - a Dolby bitstream is not
 * ours to change. It is 16-bit sound that goes through here, which is what a decoded
 * track is.
 *
 * The level can change while a film is playing: a file measured after it was opened is
 * corrected from the next progress report. The change is eased in rather than applied
 * at once - eight decibels arriving between two buffers is a jolt, and the reason the
 * correction exists is that jolts in level are unpleasant.
 */
class Gain : BaseAudioProcessor() {

    /** How much to add, in decibels. Nought leaves everything exactly as it was. */
    @Volatile
    var decibels: Float = 0f
        set(value) {
            field = value
            from = value
            toward = value
            framesLeft = 0
            factor = dbToFactor(value)
        }

    //: where the ease began, where it is going, and how much of it is left in frames
    @Volatile private var from = 0f
    @Volatile private var toward = 0f
    @Volatile private var framesLeft = 0L
    @Volatile private var framesWhole = 0L
    @Volatile private var factor = 1f
    private var rate = 48000
    private var channels = 2

    /**
     * Whether this can do anything to the sound at all.
     *
     * False until the sink configures it with 16-bit sound, and false again if it is
     * configured with anything else. A Dolby bitstream on its way to an amplifier does
     * not go through the processor chain, so this is never called and the answer stays
     * false - which is the difference between a correction that is being applied and
     * one that is only being held.
     */
    @Volatile var working = false
        private set

    private fun dbToFactor(db: Float) = Math.pow(10.0, (db / 20.0)).toFloat()

    /**
     * Move to a new level over this many seconds, from wherever it is now.
     *
     * Asking for the level it is already going to changes nothing, so a report every
     * five seconds saying the same number does not restart the ease each time.
     */
    fun easeTo(db: Float, seconds: Float = 30f) {
        if (db == toward) return
        from = nowDb()
        toward = db
        framesWhole = (rate.toLong() * seconds.toLong()).coerceAtLeast(1L)
        framesLeft = framesWhole
    }

    /** The level in force this instant, part way through an ease or at rest. */
    fun nowDb(): Float {
        val left = framesLeft
        if (left <= 0L || framesWhole <= 0L) return toward
        val gone = (framesWhole - left).toFloat() / framesWhole.toFloat()
        return from + (toward - from) * gone
    }

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat):
        AudioProcessor.AudioFormat =
        if (inputAudioFormat.encoding == androidx.media3.common.C.ENCODING_PCM_16BIT) {
            rate = inputAudioFormat.sampleRate
            channels = inputAudioFormat.channelCount.coerceAtLeast(1)
            working = true
            inputAudioFormat
        } else {
            // anything else - a Dolby bitstream, or float output - is left to itself
            working = false
            AudioProcessor.AudioFormat.NOT_SET
        }

    /**
     * Only when there is something to do.
     *
     * Reported active for every decoded film - so that a level arriving later could
     * reach one that started at nought - this sat in the audio chain of every direct
     * play. A phone decodes to PCM and went through it; a television passing Dolby to
     * an amplifier does not, which is why the television played and the phone threw
     * ERROR_CODE_FAILED_RUNTIME_CHECK at nought seconds.
     *
     * The cost is that a file measured while it is playing corrects itself only if it
     * opened with a correction already: a chain is built when the film opens, and a
     * processor left out of it then cannot be put in later.
     */
    override fun isActive(): Boolean =
        super.isActive() && (factor != 1f || framesLeft > 0L)

    override fun queueInput(input: ByteBuffer) {
        val out = replaceOutputBuffer(input.remaining())
        out.order(ByteOrder.nativeOrder())
        val from16 = input.order(ByteOrder.nativeOrder())
        if (framesLeft <= 0L && factor == 1f) {
            // nothing to do: hand the bytes on rather than multiplying each by one
            out.put(from16)
            input.position(input.limit())
            out.flip()
            return
        }
        // One level for the whole buffer. A buffer is tens of milliseconds, so a
        // thirty-second ease moves it by hundredths of a decibel from one to the next
        // - below anything anybody can hear, and without a pow() per sample.
        val take = if (framesLeft > 0L) dbToFactor(nowDb()).also { factor = it } else factor
        var frames = 0L
        while (from16.remaining() >= 2) {
            val sample = from16.short.toInt()
            var made = (sample * take).toInt()
            // A sample that would run past the end of the scale is held at it rather
            // than allowed to wrap, which is what turns a loud passage into a crackle.
            if (made > Short.MAX_VALUE.toInt()) made = Short.MAX_VALUE.toInt()
            if (made < Short.MIN_VALUE.toInt()) made = Short.MIN_VALUE.toInt()
            out.putShort(made.toShort())
            frames++
        }
        // frames, not samples: a stereo buffer holds half as many of them as it does
        // shorts, and the ease is counted in time
        if (framesLeft > 0L) {
            framesLeft = (framesLeft - frames / channels).coerceAtLeast(0L)
            if (framesLeft == 0L) {
                decibels = toward       // arrived: settle, and stop interpolating
            }
        }
        input.position(input.limit())
        out.flip()
    }
}

/**
 * The renderers this app plays with: sound that can be evened out, and subtitles the
 * framework would otherwise refuse.
 *
 * The subtitle part is not a nicety. Media3 turned off what it calls legacy decoding,
 * and a DVD's own subtitles - bitmaps, not words - arrive as exactly that: picking one
 * threw "can't handle application/vobsub samples" out of the renderer and the episode
 * stopped dead. It is one switch, on the renderer that draws them.
 */
class Renderers(context: Context, val gain: Gain) : DefaultRenderersFactory(context) {

    override fun buildTextRenderers(
        context: Context,
        output: TextOutput,
        outputLooper: Looper,
        extensionRendererMode: Int,
        out: ArrayList<Renderer>,
    ) {
        out.add(TextRenderer(output, outputLooper).apply {
            experimentalSetLegacyDecodingEnabled(true)
        })
    }

    override fun buildAudioSink(
        context: Context,
        enableFloatOutput: Boolean,
        enableAudioTrackPlaybackParams: Boolean,
    ): AudioSink =
        DefaultAudioSink.Builder(context)
            .setAudioProcessorChain(DefaultAudioSink.DefaultAudioProcessorChain(gain))
            // Float output would take the sound past this, where the gain is applied
            // on whole numbers; a track that is being passed through is untouched
            // either way.
            .setEnableFloatOutput(false)
            .setEnableAudioTrackPlaybackParams(enableAudioTrackPlaybackParams)
            .build()
}
