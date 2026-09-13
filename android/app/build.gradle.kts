plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "se.palladium.tv"
    compileSdk = 35

    defaultConfig {
        applicationId = "se.palladium.tv"
        minSdk = 24                      // Android 7: covers the Streamer and old phones
        targetSdk = 35
        // major.minor.patch, and we are pre-1.0: the third number carries fixes and
        // small additions, the second is reserved for a real feature release, the first
        // for calling it done. versionCode is separate and only ever counts up, because
        // that is the number Android compares when deciding an APK is an update.
        versionCode = 774
        // A film's page says what else is like it, in its own years.
        versionName = "0.18.56"     // one number for the app and the server it ships with

        // Where the app looks for its own updates, whatever server it happens to be
        // browsing. A friend's Palladium serves whatever APK they downloaded, which
        // may be older than what is already installed.
        //
        // Where a debug build looks for a newer app when it knows no server yet.
        // Empty unless it is given one: put updateHome=http://<address>:8765 in your
        // own ~/.gradle/gradle.properties, or pass -PupdateHome= on the command line.
        // A LAN address is usually the right answer - a connection behind the ISP's
        // own NAT cannot be reached from outside, and the public address only produced
        // a four-second timeout on every start.
        buildConfigField("String", "UPDATE_HOME",
                         "\"" + (project.findProperty("updateHome") ?: "") + "\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")   // sideloading, not Play
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    kotlinOptions { jvmTarget = "11" }
    buildFeatures {
        compose = true
        buildConfig = true   // the app compares its own version with the server's
    }
}

dependencies {
    // the rules that decide how a film is read, tried here rather than on a television
    testImplementation("junit:junit:4.13.2")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("io.coil-kt:coil-compose:2.7.0")
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-ui:1.4.1")
    implementation("androidx.media3:media3-datasource-okhttp:1.4.1")
    // casting from the phone to the Google TV Streamer
    implementation("androidx.media3:media3-cast:1.4.1")
    // a media session, so the button on a pair of headphones reaches the player
    implementation("androidx.media3:media3-session:1.4.1")
    implementation("com.google.android.gms:play-services-cast-framework:21.5.0")
    implementation("androidx.mediarouter:mediarouter:1.7.0")
    // MediaRouteButton inflates against an AppCompat theme and crashes without one
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
