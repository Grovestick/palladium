package se.palladium.tv

import android.content.Context
import android.os.Build
import java.io.PrintWriter
import java.io.StringWriter
import java.net.HttpURLConnection
import java.net.URL

/**
 * Send crashes to the server.
 *
 * There is no store, no Play Console and no cable in the way here, so a crash on the sofa
 * is otherwise invisible: the app dies and nobody learns why. This posts the stack trace
 * to the same server the app already talks to, then lets Android carry on killing the
 * process as it normally would.
 */
object Crash {

    fun install(ctx: Context) {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            // The crash usually happens on the main thread, and Android forbids network
            // calls there - reporting inline threw NetworkOnMainThreadException and the
            // report was silently lost. Hand it to a worker and wait briefly for it.
            val worker = Thread { runCatching { report(ctx, thread.name, error) } }
            worker.start()
            runCatching { worker.join(3000) }
            previous?.uncaughtException(thread, error)
        }
    }

    /**
     * A fault the app caught and carried on from.
     *
     * Worth reporting precisely because nothing else marks it: the screen recovers,
     * the person shrugs, and the same thing happens again next week. Sent on a worker
     * so it can be called from anywhere, and silent about its own failures.
     */
    fun survived(ctx: Context, where: String, error: Throwable) {
        Thread { runCatching { report(ctx, "caught in " + where, error) } }.start()
    }

    private fun report(ctx: Context, thread: String, error: Throwable) {
        val trace = StringWriter().also { error.printStackTrace(PrintWriter(it)) }.toString()
        val body = buildString {
            append("version=").append(BuildConfig.VERSION_NAME)
                .append(" (").append(BuildConfig.VERSION_CODE).append(")\n")
            append("device=").append(Build.MANUFACTURER).append(" ").append(Build.MODEL)
                .append("  android=").append(Build.VERSION.RELEASE)
                .append(" sdk=").append(Build.VERSION.SDK_INT).append("\n")
            append("thread=").append(thread).append("\n\n")
            append(trace)
        }
        val base = Api.base.ifEmpty {
            ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)
                .getString("server", "") ?: ""
        }
        if (base.isEmpty()) return
        // a crash report must not hang the dying process, hence the short timeouts
        val conn = URL("$base/applog").openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        // the key if there is one: away from home the door asks for it, and a crash
        // on somebody else's sofa is the one hardest to learn about otherwise
        runCatching {
            if (Api.token.isNotEmpty()) conn.setRequestProperty("X-Plex-Token", Api.token)
        }
        conn.connectTimeout = 2500
        conn.readTimeout = 2500
        conn.doOutput = true
        conn.outputStream.use { it.write(body.toByteArray()) }
        conn.responseCode
    }
}
