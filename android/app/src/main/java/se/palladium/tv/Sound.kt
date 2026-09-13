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
 */
class Gain : BaseAudioProcessor() {

    /** How much to add, in decibels. Nought leaves everything exactly as it was. */
    @Volatile
    var decibels: Float = 0f
        set(value) {
            field = value
            factor = Math.pow(10.0, (value / 20.0)).toFloat()
        }

    @Volatile
    private var factor = 1f

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat):
        AudioProcessor.AudioFormat =
        if (inputAudioFormat.encoding == androidx.media3.common.C.ENCODING_PCM_16BIT)
            inputAudioFormat
        else
            // anything else - a Dolby bitstream, or float output - is left to itself
            AudioProcessor.AudioFormat.NOT_SET

    override fun isActive(): Boolean = super.isActive() && factor != 1f

    override fun queueInput(input: ByteBuffer) {
        val take = factor
        val out = replaceOutputBuffer(input.remaining())
        out.order(ByteOrder.nativeOrder())
        val from = input.order(ByteOrder.nativeOrder())
        while (from.remaining() >= 2) {
            val sample = from.short.toInt()
            var made = (sample * take).toInt()
            // A sample that would run past the end of the scale is held at it rather
            // than allowed to wrap, which is what turns a loud passage into a crackle.
            if (made > Short.MAX_VALUE.toInt()) made = Short.MAX_VALUE.toInt()
            if (made < Short.MIN_VALUE.toInt()) made = Short.MIN_VALUE.toInt()
            out.putShort(made.toShort())
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
