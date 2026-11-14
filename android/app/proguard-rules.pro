# Names stay: a crash report from a television should read as the source does.
-dontobfuscate

# Named in the manifest's meta-data, so nothing in the code refers to it.
-keep class se.palladium.tv.CastOptionsProvider { *; }

# the progress bar held longer than Media3's two seconds (PlayerActivity.holdTheBar)
-keepclassmembers class androidx.media3.ui.PlayerControlView {
    androidx.media3.ui.PlayerControlViewLayoutManager controlViewLayoutManager;
}
-keepclassmembers class androidx.media3.ui.PlayerControlViewLayoutManager {
    int uxState;
    java.lang.Runnable hideProgressBarRunnable;
}

# the knob on the time bar drawn as a pause sign while paused (PlayerActivity.knobShowsPause)
-keepclassmembers class androidx.media3.ui.DefaultTimeBar {
    android.graphics.drawable.Drawable scrubberDrawable;
}
