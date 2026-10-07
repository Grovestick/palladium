package se.palladium.tv

import android.content.Context
import java.net.InetAddress
import java.net.UnknownHostException

/**
 * The addresses a server's name last resolved to, kept on the device and used when a
 * lookup fails. A guest wifi that could not resolve the main server's name half the
 * time left the library loading by the stored IP and half the posters blank, since the
 * pictures were only ever asked for by name.
 */
object KnownHosts : okhttp3.Dns {
    private val known = java.util.concurrent.ConcurrentHashMap<String, List<String>>()
    @Volatile private var ctx: Context? = null

    fun load(context: Context) {
        ctx = context.applicationContext
        val p = context.getSharedPreferences("palladium", Context.MODE_PRIVATE)
        p.all.forEach { (k, v) ->
            if (k.startsWith("host:") && v is String && v.isNotEmpty())
                known[k.removePrefix("host:")] = v.split(",")
        }
    }

    private fun keep(host: String, found: List<InetAddress>) {
        val ips = found.mapNotNull { it.hostAddress }.filter { it.isNotEmpty() }
        if (ips.isEmpty() || known[host] == ips) return
        known[host] = ips
        ctx?.getSharedPreferences("palladium", Context.MODE_PRIVATE)?.edit()
            ?.putString("host:$host", ips.joinToString(","))?.apply()
    }

    private fun isName(host: String) =
        host.isNotEmpty() && !host.all { it.isDigit() || it == '.' || it == ':' }

    override fun lookup(hostname: String): List<InetAddress> {
        return try {
            InetAddress.getAllByName(hostname).toList().also { keep(hostname, it) }
        } catch (e: UnknownHostException) {
            val kept = known[hostname] ?: throw e
            kept.mapNotNull { runCatching { InetAddress.getByName(it) }.getOrNull() }
                .ifEmpty { throw e }
        }
    }

    private val failedAt = java.util.concurrent.ConcurrentHashMap<String, Long>()

    /**
     * The address to connect to: itself where its name resolves, and the same by its
     * last known number where it does not. Every connection is opened through this.
     * The library was asked for by number when the name failed and so kept working;
     * the check for a newer build, the build itself and the look at which servers
     * answer were not, and on a network that could not resolve the name the app could
     * browse but never update.
     */
    fun reachable(url: String): String {
        val host = Regex("""^https?://([^/:]+)""").find(url)?.groupValues?.get(1) ?: return url
        if (!isName(host)) return url
        val kept = known[host]?.firstOrNull().orEmpty()
        val now = System.currentTimeMillis()
        if (kept.isNotEmpty() && Route.nameStillFailing(failedAt[host] ?: 0L, now))
            return Route.numbered(url, kept)
        return try {
            keep(host, InetAddress.getAllByName(host).toList())
            failedAt.remove(host)
            url
        } catch (e: UnknownHostException) {
            if (kept.isEmpty()) url
            else {
                failedAt[host] = now
                Route.numbered(url, kept)
            }
        }
    }

    /** The same address with its name swapped for the last known IP, or null. */
    fun byAddress(url: String): String? {
        val m = Regex("""^(https?://)([^/:]+)(.*)$""").find(url) ?: return null
        val host = m.groupValues[2]
        if (!isName(host)) return null
        val ip = known[host]?.firstOrNull() ?: return null
        return m.groupValues[1] + ip + m.groupValues[3]
    }

    /** Whether an address to fall back on is kept for this one's name, or it has none. */
    fun covered(url: String): Boolean {
        val host = Regex("""^https?://([^/:]+)""").find(url)?.groupValues?.get(1) ?: return true
        return !isName(host) || known.containsKey(host)
    }

    /** Look the name up now, so there is an address to fall back on later. */
    fun learn(url: String) {
        val host = Regex("""^https?://([^/:]+)""").find(url)?.groupValues?.get(1) ?: return
        if (!isName(host)) return
        runCatching { keep(host, InetAddress.getAllByName(host).toList()) }
    }
}
