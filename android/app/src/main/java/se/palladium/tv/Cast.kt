package se.palladium.tv

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability

/**
 * Casting, guarded.
 *
 * Three things have to be true before the cast button is worth showing, and each one
 * used to fail loudly instead of quietly:
 *   * Google Play services present (a Google TV or a normal phone has them; an emulator
 *     or a de-Googled phone does not),
 *   * the cast framework built without throwing,
 *   * on Android 13 and up, permission to see devices on the local network - without it
 *     discovery finds nothing and the chooser sits empty, which reads as "does not work".
 */
object Cast {
    @Volatile private var ready = false

    fun warmUp(activity: Activity) {
        if (!playServices(activity)) return
        ready = runCatching { CastContext.getSharedInstance(activity); true }.getOrDefault(false)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(activity, Manifest.permission.NEARBY_WIFI_DEVICES)
            != PackageManager.PERMISSION_GRANTED) {
            runCatching {
                ActivityCompat.requestPermissions(
                    activity, arrayOf(Manifest.permission.NEARBY_WIFI_DEVICES), 42)
            }
        }
    }

    fun available(ctx: Context): Boolean = ready && playServices(ctx)

    private fun playServices(ctx: Context): Boolean =
        runCatching {
            GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(ctx) ==
                ConnectionResult.SUCCESS
        }.getOrDefault(false)
}
