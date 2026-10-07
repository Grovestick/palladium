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
        // whatever could not be sent last time - the machine refused it or was off -
        // goes now, to whichever server this start opens
        Thread { runCatching { sendKept(ctx) } }.start()
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
        // kept first, so a report the server refuses or never receives is not lost
        keep(ctx, body)
        if (post(ctx, body)) forget(ctx, body)
    }

    private fun post(ctx: Context, body: String): Boolean {
        val base = Api.base.ifEmpty {
            ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)
                .getString("server", "") ?: ""
        }
        if (base.isEmpty()) return false
        // a house address away from home: kept for the next start rather than waited on
        if (!Api.homeHere(base)) return false
        // The key the way the server reads it: in the address and in its own header.
        // Sent as X-Plex-Token it was never seen, and every crash away from home was
        // refused as having no key.
        val key = runCatching { Api.token }.getOrDefault("")
        // a crash report must not hang the dying process, hence the short timeouts
        val conn = Net.open("$base/applog" + (if (key.isEmpty()) "" else "?t=" + key),
                            2500, 2500, key, "POST")
        Net.send(conn, body)
        return conn.responseCode in 200..299
    }

    private fun prefs(ctx: Context) =
        ctx.getSharedPreferences("palladium-crashes", Context.MODE_PRIVATE)

    /** Up to five reports waiting to be sent, newest last. */
    private fun keep(ctx: Context, body: String) {
        val had = prefs(ctx).getStringSet("waiting", emptySet()).orEmpty().toMutableSet()
        had.add(System.currentTimeMillis().toString() + "\n" + body)
        val five = had.sorted().takeLast(5).toSet()
        prefs(ctx).edit().putStringSet("waiting", five).commit()
    }

    private fun forget(ctx: Context, body: String) {
        val had = prefs(ctx).getStringSet("waiting", emptySet()).orEmpty()
        prefs(ctx).edit()
            .putStringSet("waiting", had.filterNot { it.substringAfter("\n") == body }.toSet())
            .commit()
    }

    private fun sendKept(ctx: Context) {
        // the server is known once the app has loaded it; a moment is enough
        Thread.sleep(8000)
        for (one in prefs(ctx).getStringSet("waiting", emptySet()).orEmpty().sorted()) {
            val body = one.substringAfter("\n")
            if (runCatching { post(ctx, body) }.getOrDefault(false)) forget(ctx, body)
        }
    }
}
