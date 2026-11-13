package se.palladium.tv

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.mutableStateOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Android notifications for the Watchlist's blinks, a setting of the viewer's kept by the
 * server and off unless turned on. The server's notification handler lights the blink and
 * queues the same step for the app; this posts what it is handed: a cinema release as the
 * name with cam or telesync after it, a real release by its name alone, one that has come
 * in as here. Asked while the app is up and every fifteen minutes by the system while it is
 * not; a press opens the title. A television never asks: the queue is the phone's.
 */
object Notes {
    val on = mutableStateOf(false)
    private const val CHANNEL = "titles"
    private const val JOB = 72

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("palladium", Context.MODE_PRIVATE)

    private fun posts(): Boolean = Api.device != "tv"

    fun load(ctx: Context) {
        on.value = prefs(ctx).getBoolean("notesOn", false)
        if (on.value && posts()) schedule(ctx)
    }

    /** The server's word on it, kept here for the system's look. */
    suspend fun learn(ctx: Context) {
        val said = Api.notify() ?: return
        keep(ctx, said == "on")
    }

    suspend fun setOn(ctx: Context, yes: Boolean) {
        keep(ctx, yes)
        Api.setNotify(if (yes) "on" else "off")?.let { keep(ctx, it == "on") }
    }

    private fun keep(ctx: Context, yes: Boolean) {
        on.value = yes
        prefs(ctx).edit().putBoolean("notesOn", yes).apply()
        if (yes && posts()) schedule(ctx) else runCatching {
            (ctx.getSystemService(Context.JOB_SCHEDULER_SERVICE) as android.app.job.JobScheduler).cancel(JOB)
        }
    }

    private fun schedule(ctx: Context) {
        val js = ctx.getSystemService(Context.JOB_SCHEDULER_SERVICE) as android.app.job.JobScheduler
        if (js.getPendingJob(JOB) != null) return
        runCatching {
            js.schedule(android.app.job.JobInfo.Builder(
                JOB, android.content.ComponentName(ctx, NotesJob::class.java))
                .setPeriodic(15 * 60_000L)
                .setRequiredNetworkType(android.app.job.JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .build())
        }
    }

    /** What a blink says: the name, with the kind of cinema release or that it is here. */
    fun line(title: String, how: String, kind: String): String = when (how) {
        "cam" -> title + "  ·  " + kind.ifEmpty { "cam" }
        "here", "fetched" -> title + "  ·  here"
        else -> title
    }

    /** One notification, opening `key` when pressed. */
    private fun post(ctx: Context, n: Api.WatchNews) {
        val nm = ctx.getSystemService(android.app.NotificationManager::class.java)
        if (android.os.Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(
            android.app.NotificationChannel(CHANNEL, "Watchlist",
                                            android.app.NotificationManager.IMPORTANCE_DEFAULT))
        val id = (n.key + ":" + n.how).hashCode()
        val open = android.app.PendingIntent.getActivity(
            ctx, id,
            Intent(ctx, MainActivity::class.java).putExtra("openKey", n.key)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT)
        val made = androidx.core.app.NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_more)
            .setContentTitle(line(n.title, n.how, n.kind))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        runCatching { nm.notify(id, made) }
    }

    /** What the server has queued for this viewer's app, posted. */
    suspend fun check(ctx: Context) {
        if (!on.value || !posts()) return
        for (n in Api.notes()) if (n.title.isNotEmpty()) post(ctx, n)
    }

    /** The system's look while the app is not up. */
    suspend fun look(ctx: Context) {
        Api.learnDevice(ctx)
        if (Api.base.isEmpty()) Api.loadServer(ctx)
        Reach.start(ctx)
        check(ctx)
    }
}

/** The system's fifteen-minute look for notifications. */
class NotesJob : android.app.job.JobService() {
    override fun onStartJob(params: android.app.job.JobParameters?): Boolean {
        Notes.load(applicationContext)
        @OptIn(kotlinx.coroutines.DelicateCoroutinesApi::class)
        kotlinx.coroutines.GlobalScope.launch(Dispatchers.IO) {
            runCatching { Notes.look(applicationContext) }
            jobFinished(params, false)
        }
        return true
    }

    override fun onStopJob(params: android.app.job.JobParameters?): Boolean = true
}
