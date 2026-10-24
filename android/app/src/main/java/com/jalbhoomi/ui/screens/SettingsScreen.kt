package com.jalbhoomi.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.google.firebase.auth.FirebaseAuth
import com.jalbhoomi.ui.viewmodel.UiState

@Composable
fun SettingsScreen(s: UiState, onUrl: (String) -> Unit, onPush: (Boolean) -> Unit, onSignOut: () -> Unit) {
    var url by remember(s.backendUrl) { mutableStateOf(s.backendUrl) }
    val email = try { FirebaseAuth.getInstance().currentUser?.email } catch (_: Exception) { null }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Settings", style = MaterialTheme.typography.headlineSmall)
        Text("Signed in as: ${email ?: "unknown"}")
        OutlinedTextField(url, { url = it }, label = { Text("Backend address") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Button(enabled = url.startsWith("http"), onClick = { onUrl(url) }) { Text("Save address") }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Push notifications", Modifier.weight(1f))
            Switch(s.pushEnabled, onPush)
        }
        OutlinedButton(onClick = onSignOut) { Text("Sign out") }
        Text("Language: English only. Other languages are planned but not implemented.", style = MaterialTheme.typography.bodySmall)
    }
}
