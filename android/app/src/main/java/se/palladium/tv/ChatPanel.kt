package se.palladium.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * The watch party: the lobby, the party, and an invitation waiting to be answered.
 *
 * The same two rooms the browser has. The lobby is open to everyone on the server and
 * lists who is about and what they have on; the party exists only while one is
 * running. Reading is the half that matters on a television - what is said arrives
 * over the picture with no press - and this is where it can be answered.
 */
@Composable
fun ChatPanel(scope: CoroutineScope, onClose: () -> Unit) {
    var room by remember { mutableStateOf("lobby") }
    var said by remember { mutableStateOf<List<Api.Said>>(emptyList()) }
    var state by remember { mutableStateOf<Api.Party?>(null) }
    var line by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    val write = remember { FocusRequester() }

    // what is there, and then what arrives: the server holds the answer open, so this
    // is not a poll however much it reads like one
    LaunchedEffect(room) {
        said = Api.chat(0, 0, room)
        while (true) {
            val more = Api.chat(said.lastOrNull()?.id ?: 0, 45, room)
            if (more.isNotEmpty()) said = (said + more).takeLast(80)
        }
    }
    // who is about, what is on, and whether this screen has been asked to a party
    LaunchedEffect(Unit) {
        while (true) {
            state = Api.party()
            kotlinx.coroutines.delay(8_000)
        }
    }

    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Skin.Panel,
        title = {
            val now = state
            Text("Watch party" +
                 (if (now != null && now.on && now.title.isNotEmpty())
                      " · " + now.title else ""),
                 color = Skin.Fg, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
        },
        text = {
            Column {
                // Which room. The party is only there while one is on, and saying so
                // is better than a tab that answers nothing.
                Row(Modifier.padding(bottom = 8.dp)) {
                    Pill("Lobby", active = room == "lobby", small = true) {
                        room = "lobby"
                    }
                    Pill(if (state?.on == true) "Party" else "No party on",
                         active = room == "party", small = true,
                         dim = state?.on != true) {
                        if (state?.on == true) room = "party"
                    }
                }

                // Somebody has asked this screen to watch something.
                state?.let { now ->
                    if (now.invitedBy.isNotEmpty()) {
                        Row(Modifier.fillMaxWidth()
                                .clip(RoundedCornerShape(10.dp))
                                .background(Color(0xFF12324F))
                                .padding(horizontal = 12.dp, vertical = 9.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Text(now.invitedBy + " asks you to watch " + now.invitedTo,
                                 color = Color(0xFFDCE9F7), fontSize = 12.5.sp,
                                 modifier = Modifier.weight(1f))
                            Pill("Join", primary = true, small = true) {
                                scope.launch {
                                    Api.answerParty(true)
                                    state = Api.party()
                                    room = "party"
                                }
                            }
                            Pill("No", small = true) {
                                scope.launch {
                                    Api.answerParty(false)
                                    state = Api.party()
                                }
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                    }
                }

                // Who is about, and what they have on: this is what makes the lobby a
                // room rather than a list of lines.
                if (room == "lobby") {
                    state?.here?.takeIf { it.isNotEmpty() }?.let { about ->
                        Text("HERE NOW", color = Skin.Dim, fontSize = 11.sp,
                             fontWeight = FontWeight.SemiBold)
                        about.forEach { one ->
                            Row(Modifier.padding(top = 3.dp)) {
                                Text(one.name, color = Skin.Fg, fontSize = 12.5.sp,
                                     fontWeight = FontWeight.SemiBold,
                                     modifier = Modifier.padding(end = 8.dp))
                                Text(if (one.watching.isEmpty()) "in the library"
                                     else "watching " + one.watching,
                                     color = if (one.watching.isEmpty()) Skin.Dim
                                             else Skin.Accent,
                                     fontSize = 12.5.sp, maxLines = 1)
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                    }
                }

                if (said.isEmpty()) {
                    Text(if (room == "lobby") "Nothing said in the lobby yet."
                         else "Nothing said yet.",
                         color = Skin.Dim, fontSize = 13.sp)
                }
                Column(Modifier.heightIn(max = 220.dp)
                           .verticalScroll(rememberScrollState())) {
                    said.forEach { one ->
                        Row(Modifier.padding(vertical = 3.dp)) {
                            Text(one.who, color = Skin.Accent, fontSize = 12.5.sp,
                                 fontWeight = FontWeight.SemiBold,
                                 modifier = Modifier.padding(end = 8.dp))
                            Text(one.text, color = Skin.Fg, fontSize = 13.sp)
                        }
                    }
                }

                // A plain field: Compose's own, so a television's on-screen keyboard
                // opens on it and a phone's does too.
                Row(Modifier.padding(top = 10.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(8.dp))
                            .background(Skin.Panel2)
                            .border(1.dp, Skin.Line, RoundedCornerShape(8.dp))
                            .padding(horizontal = 10.dp, vertical = 9.dp)) {
                        if (line.isEmpty()) {
                            Text("Say something", color = Skin.Dim, fontSize = 13.sp)
                        }
                        BasicTextField(
                            value = line,
                            onValueChange = { line = it.take(300) },
                            singleLine = true,
                            textStyle = TextStyle(color = Skin.Fg, fontSize = 13.sp),
                            cursorBrush = SolidColor(Skin.Accent),
                            modifier = Modifier.fillMaxWidth().focusRequester(write))
                    }
                    Spacer(Modifier.width(8.dp))
                    Pill(if (sending) "…" else "Send", primary = true) {
                        val text = line.trim()
                        if (text.isNotEmpty() && !sending) {
                            sending = true
                            line = ""
                            scope.launch {
                                Api.say(text, from = Api.device.ifEmpty { "app" },
                                        room = room)
                                sending = false
                            }
                        }
                    }
                }
            }
        },
        confirmButton = { Pill("Close") { onClose() } },
    )

    LaunchedEffect(Unit) { runCatching { write.requestFocus() } }
}
