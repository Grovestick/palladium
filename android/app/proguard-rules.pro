# Names stay: a crash report from a television should read as the source does.
-dontobfuscate

# Named in the manifest's meta-data, so nothing in the code refers to it.
-keep class se.palladium.tv.CastOptionsProvider { *; }
