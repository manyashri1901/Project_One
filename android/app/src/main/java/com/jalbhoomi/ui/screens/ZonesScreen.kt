package com.jalbhoomi.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jalbhoomi.ui.viewmodel.UiState

@Composable
fun ZonesScreen(s: UiState, onSelect: (String) -> Unit, onRename: (String) -> Unit) {
    var renaming by remember { mutableStateOf(false) }
    var text by remember { mutableStateOf("") }

    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Text("Zones", style = MaterialTheme.typography.headlineSmall) }
        if (s.zones.isEmpty()) item { Text("No zones yet.") }
        items(s.zones, key = { it.deviceId }) { z ->
            val sel = z.deviceId == s.selectedId
            Card(Modifier.fillMaxWidth().clickable { onSelect(z.deviceId) },
                colors = CardDefaults.cardColors(containerColor = if (sel) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant)) {
                Column(Modifier.padding(14.dp)) {
                    Text(z.title + if (sel) "  (selected)" else "", style = MaterialTheme.typography.titleMedium)
                    Text("Soil ${z.soilPct?.let { "%.0f%%".format(it) } ?: "--"}   ${if (z.autoMode) "Auto" else "Manual"}" +
                        if (z.openFaults > 0) "   ${z.openFaults} open fault(s)" else "")
                    Text(z.deviceId, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        item {
            val current = s.selected
            if (current != null) {
                if (!renaming) OutlinedButton(onClick = { text = current.title; renaming = true }) { Text("Rename selected zone") }
                else Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(text, { text = it }, singleLine = true, modifier = Modifier.weight(1f))
                    Button(enabled = text.isNotBlank(), onClick = { onRename(text.trim()); renaming = false }) { Text("Save") }
                }
            }
        }
    }
}
