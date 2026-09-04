package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * "Next episode in 5", in the corner where it does not cover the credits.
 *
 * Deliberately not a full-screen takeover: the end of an episode is often the best part
 * of it, and something that blanks the picture to ask a question is an intrusion. It
 * counts down, it can be started at once, and it can be stopped - which is the whole
 * point of the five seconds.
 */
@Composable
fun NextEpisodeCard(next: Media, seconds: Int, onPlay: () -> Unit, onStop: () -> Unit) {
    Box(Modifier.fillMaxSize().padding(28.dp), contentAlignment = Alignment.BottomEnd) {
        Row(
            Modifier.background(Color(0xF01A2029), RoundedCornerShape(12.dp))
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.width(76.dp).height(114.dp).clip(RoundedCornerShape(8.dp))) {
                Art(Api.artUrl(next), next.title, Modifier.fillMaxSize(), mark = 28)
            }
            Column(Modifier.padding(start = 14.dp).widthIn(max = 300.dp)) {
                Text("NEXT EPISODE", color = Skin.Dim, fontSize = 10.5.sp,
                     letterSpacing = 1.2.sp, fontWeight = FontWeight.SemiBold)
                Text(next.title, color = Skin.Fg, fontSize = 17.sp,
                     fontWeight = FontWeight.SemiBold,
                     modifier = Modifier.padding(top = 3.dp))
                Text(next.subtitle, color = Skin.Dim, fontSize = 13.sp)
                Row(Modifier.padding(top = 10.dp)) {
                    Pill(if (seconds > 0) "Play now  ($seconds)" else "Play now",
                         primary = true) { onPlay() }
                    Pill("Stop") { onStop() }
                }
            }
        }
    }
}
