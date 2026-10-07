package se.palladium.tv

import android.content.Context
import android.content.Intent
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Keeping the app current.
 *
 * The server publishes the version of the APK it is serving; the app compares that with
 * its own and offers the download. Android installs it over the top - same package, same
 * signing key - so settings and progress survive. There is no store in the loop, which is
 * the whole point of a sideloaded app on a home network.
 */
object Updates {

    data class Available(val versionName: String, val versionCode: Int, val sizeMb: Double)

    /**
     * Where new versions come from.
     *
     * The app is built and published in one place, so that is where it looks first -
     * baked in at build time. A friend running their own Palladium serves whatever APK
     * they happened to download, which could be older than what is on the phone, so
     * their server is only the fallback for when home cannot be reached.
     */
    private fun homes(ctx: Context?): List<Pair<String, String>> {
        val known = if (ctx == null) emptyList() else Servers.all(ctx)
        val places = ArrayList<Pair<String, String>>()
        // the address each server answers on now, from the background check: away from
        // home that is the outside one, which nothing below would otherwise ask
        known.forEach { row -> Reach.doorOf(row)?.let { places.add(Pair(it, row.token)) } }
        // the server this app is actually using answers from wherever the phone is,
        // which the address baked in at build time does not
        if (Api.base.isNotEmpty()) places.add(Pair(Api.base, Api.token))
        known.forEach {
            places.add(Pair(it.base, it.token))
            places.add(Pair(it.outside, it.token))
        }
        if (BuildConfig.UPDATE_HOME.isNotEmpty()) {
            // a token for it if this phone happens to know one
            val tok = known.firstOrNull { it.base == BuildConfig.UPDATE_HOME }?.token ?: ""
            places.add(Pair(BuildConfig.UPDATE_HOME, tok))
        }
        // not a house address off the house network, nor one the check found off:
        // away from home every one of them was four seconds for nothing
        return places.map { Pair(it.first.trimEnd('/'), it.second) }
            .distinctBy { it.first }
            .filter { it.first.isNotEmpty() && Api.homeHere(it.first) &&
                      Reach.upAt(it.first) != false }
    }

    @Volatile private var from: String = ""      // where the offer on screen came from
    @Volatile private var fromToken: String = ""     // and what opens it

    /** A version the user asked not to be told about again. */
    fun skippedVersion(ctx: Context): Int =
        ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE).getInt("skipUpdate", 0)

    fun setSkipped(ctx: Context, code: Int) {
        ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)
            .edit().putInt("skipUpdate", code).apply()
    }

    @Volatile var skip: Int = 0                  // loaded once, so check() stays cheap

    suspend fun check(ignoreSkip: Boolean = false, ctx: Context? = null): Available? =
        withContext(Dispatchers.IO) {
        // which servers answer, and where: asked at the very start, this ran before the
        // check had said, and away from home it knew only the house addresses
        Reach.awaitFirst(4000)
        answered = false
        val places = homes(ctx)
        var why = ""
        // twice round: asked once each, one lost connection on a poor network was
        // every server failing, and that was reported as being up to date
        repeat(2) { round ->
            for ((host, tok) in places) {
                try {
                    val ask = "$host/app/version" + (if (tok.isEmpty()) "" else "?t=$tok")
                    val body = Net.text(Net.open(ask, 4000, 6000, tok))
                    val o = JSONObject(body)
                    val code = o.optInt("versionCode")
                    answered = true
                    said("update: " + Servers.hostOf(host) + " offers " + code + ", this is " +
                         BuildConfig.VERSION_CODE + (if (code == skip && !ignoreSkip)
                                                         " (skipped)" else ""))
                    if (code > BuildConfig.VERSION_CODE && (ignoreSkip || code != skip)) {
                        from = host
                        fromToken = tok
                        return@withContext Available(o.optString("versionName"), code,
                                                     o.optDouble("sizeMb"))
                    }
                    return@withContext null      // reachable and current: nothing to do
                } catch (e: Exception) {
                    why = Servers.hostOf(host) + " " + e.javaClass.simpleName + ": " +
                        (e.message ?: "").take(60)
                }
            }
            if (round == 0) kotlinx.coroutines.delay(1500)
        }
        said("update: nobody answered of " + places.size + " - " + why)
        null                         // an update check is never worth an error on screen
    }

    /** Whether the last check was answered by any server: unanswered is not up to date. */
    @Volatile var answered: Boolean = false

    @Volatile private var lastSaid = ""

    /** What a check found, once, to the server: why a phone somewhere else was not
     *  offered a build cannot be seen from the house. */
    private suspend fun said(what: String) {
        android.util.Log.i("Palladium", what)
        if (what == lastSaid) return
        lastSaid = what
        runCatching { Api.trace(what) }
    }

    /**
     * Fetch the APK and hand it to Android's installer.
     *
     * Deliberately not a browser's job. Chrome refuses to download an APK over plain
     * http, and a television may have no browser at all - both of which look to the
     * user like "download failed" with nothing to act on. Downloading it here means
     * the only thing that can fail is the network, and it can be said plainly.
     */
    /**
     * The copy already fetched, if it is whole and is the version being offered.
     *
     * By its version, not by its size. Four builds in a row came to 16.3 MB, so a
     * file kept from an earlier one passed a size check and was installed again -
     * the update appeared to work and the version never moved.
     */
    fun waiting(ctx: Context, offered: Available?): java.io.File? {
        if (offered == null) return null
        val out = java.io.File(java.io.File(ctx.cacheDir, "updates"), "palladium.apk")
        if (!out.exists() || out.length() < 1_000_000) return null
        val held = ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)
            .getInt("apkCode", 0)
        return if (held == offered.versionCode) out else null
    }

    suspend fun download(ctx: Context, offered: Available? = null,
                         progress: ((Int) -> Unit)? = null): java.io.File? =
        withContext(Dispatchers.IO) {
        val places = homes(ctx)
        val host = from.ifEmpty { places.firstOrNull()?.first ?: Api.base }
        val tok = if (from.isNotEmpty()) fromToken
                  else places.firstOrNull()?.second ?: Api.token
        val dir = java.io.File(ctx.cacheDir, "updates").apply { mkdirs() }
        val out = java.io.File(dir, "palladium.apk")
        try {
            // the invitation travels with it: from outside the house there is no other
            // way to be recognised, and the file is refused without one
            val url = "$host/palladium.apk" + (if (tok.isEmpty()) "" else "?t=$tok")
            val conn = Net.open(url, 8000, 60000, tok)
            val total = conn.contentLengthLong
            conn.inputStream.use { input ->
                out.outputStream().use { file ->
                    val buffer = ByteArray(128 * 1024)
                    var got = 0L
                    var said = -1
                    while (true) {
                        val n = input.read(buffer)
                        if (n < 0) break
                        file.write(buffer, 0, n)
                        got += n
                        if (progress != null && total > 0) {
                            val pct = ((got * 100) / total).toInt().coerceIn(0, 100)
                            // only when the number itself changes: this crosses to
                            // the main thread and there are a hundred of them, not
                            // one per block of a sixteen-megabyte file
                            if (pct != said) { said = pct; progress(pct) }
                        }
                    }
                }
            }
            if (out.length() <= 1_000_000) return@withContext null   // truncated: no use
            // which version this file is, so it is never mistaken for a later one
            ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE).edit()
                .putInt("apkCode", offered?.versionCode ?: 0).apply()
            out
        } catch (e: Exception) {
            null
        }
    }

    /**
     * Open the system installer on a file we already hold.
     *
     * Two ways of asking, because a television is not a phone: ACTION_VIEW on the
     * package mime type is what a phone answers, and some television builds have
     * nothing registered for it at all - the press then appears to do nothing, and
     * the natural response is to press again. ACTION_INSTALL_PACKAGE is deprecated
     * and still answered there.
     */
    fun installFile(ctx: Context, apk: java.io.File): Boolean {
        val uri = androidx.core.content.FileProvider.getUriForFile(
            ctx, ctx.packageName + ".files", apk)
        // CLEAR_TASK: the installer's task from the last update is still there, and
        // without it the press was delivered to that old task and only closed it
        val view = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or
                      Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        if (runCatching { ctx.startActivity(view); true }.getOrDefault(false)) return true
        @Suppress("DEPRECATION")
        val old = Intent(Intent.ACTION_INSTALL_PACKAGE)
            .setData(uri)
            .putExtra(Intent.EXTRA_NOT_UNKNOWN_SOURCE, true)
            .putExtra(Intent.EXTRA_RETURN_RESULT, false)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or
                      Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        return runCatching { ctx.startActivity(old); true }.getOrDefault(false)
    }

    /**
     * Whether this device will let us install anything at all.
     *
     * Android asks the user to allow it per app, once; sending them straight to that
     * screen is kinder than an installer that refuses without explanation.
     */
    fun mayInstall(ctx: Context): Boolean =
        android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.O ||
            ctx.packageManager.canRequestPackageInstalls()

    fun askForPermission(ctx: Context) {
        ctx.startActivity(
            Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                   Uri.parse("package:" + ctx.packageName))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
