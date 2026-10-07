package se.palladium.tv

import java.net.HttpURLConnection
import java.net.URL

/**
 * Every connection to a server is opened here.
 *
 * One place that knows the rules: a house address off the house network is refused at
 * once rather than waited on, a name that will not resolve is asked for by its last
 * known number, and every request carries the app's name and the key the way the
 * server reads them. Which address to ask is not decided here - that is the
 * background check's ([Reach]), handed out by [Api.addressOf]. Each caller reads its
 * own answer.
 */
object Net {
    fun open(url: String, connect: Int, read: Int, token: String = "",
             method: String = "GET"): HttpURLConnection {
        if (!Api.homeHere(url))
            throw java.net.ConnectException("a house address, not this network")
        // by number where the name will not resolve on this network
        val conn = URL(KnownHosts.reachable(url)).openConnection() as HttpURLConnection
        // from outside the house a connection is a radio waking and maybe a packet
        // sent again at one second and at three: never less than four
        conn.connectTimeout = if (Servers.athome(url)) connect else maxOf(connect, 4000)
        conn.readTimeout = read
        conn.setRequestProperty("X-Palladium-App", Api.appName())
        if (token.isNotEmpty()) conn.setRequestProperty("X-Palladium-Token", token)
        if (method != "GET") {
            conn.requestMethod = method
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
        }
        return conn
    }

    /** The body of a POST, written. */
    fun send(conn: HttpURLConnection, body: String) {
        conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
    }

    /** The answer, as text. */
    fun text(conn: HttpURLConnection): String =
        conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
}
