package se.palladium.tv

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** The watchlist button pressed: where the title stands, Add or Remove as it stands, a
 *  favorite or not, and the watchlist itself. */
@Composable
fun WatchlistPicker(listed: Boolean, favorite: Boolean, onClose: () -> Unit,
                    onList: (Boolean) -> Unit, onFavorite: (Boolean) -> Unit,
                    onGoTo: () -> Unit,
                    /* what the Offline button says, with offline viewing on and a title
                       that can be kept; null leaves it out */
                    offline: String? = null, offlineOn: Boolean = false,
                    onOffline: () -> Unit = {}) {
    val on = listed || favorite
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = { Text("Watchlist", color = Skin.Fg) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(Route.watchlistSaid(listed, favorite), color = Skin.Dim, fontSize = 14.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Pill(if (on) "Remove from watchlist" else "Add to watchlist",
                         primary = !on) { onList(!on) }
                    Pill(if (favorite) "Not a favorite" else "\u2665 Favorite") { onFavorite(!favorite) }
                }
                // kept on this device: its quality, how far, or let go - in its own panel
                offline?.let { said ->
                    Pill(said, active = offlineOn) { onOffline() }
                }
            }
        },
        confirmButton = { Pill("Go to watchlist") { onClose(); onGoTo() } },
        dismissButton = { Pill("Done") { onClose() } })
}
