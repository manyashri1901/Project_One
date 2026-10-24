package com.jalbhoomi.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jalbhoomi.ui.components.Level
import com.jalbhoomi.ui.components.StatusBanner
import com.jalbhoomi.ui.viewmodel.MAX_MANUAL_SECONDS
import com.jalbhoomi.ui.viewmodel.UiState

/**
 * Manual control. Every button sends a REQUEST; the ESP32 arbitrates it against its safety
 * interlocks and may refuse. The result is shown from the device's acknowledgement.
 */
@Composable
fun ZoneControlScreen(
    s: UiState,
    onStart: (Int) -> Unit, onStop: () -> Unit, onAuto: (Boolean) -> Unit,
    onSaveThresholds: (Int, Int, Double) -> Unit, onDismiss: () -> Unit,
) {
    val z = s.selected
    if (z == null) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Select a zone first.") }; return }

    var seconds by remember { mutableFloatStateOf(10f) }
    var dry by remember(s.thresholds) { mutableFloatStateOf(s.thresholds.soilDryPct.toFloat()) }
    var wet by remember(s.thresholds) { mutableFloatStateOf(s.thresholds.soilWetPct.toFloat()) }
    var rain by remember(s.thresholds) { mutableFloatStateOf(s.thresholds.rainProbability.toFloat()) }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Control: ${z.title}", style = MaterialTheme.typography.headlineSmall)
        s.message?.let { StatusBanner(it, Level.INFO, onDismiss = onDismiss) }
        s.error?.let { StatusBanner(it, Level.WARN, onDismiss = onDismiss) }

        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Automatic irrigation", style = MaterialTheme.typography.titleMedium)
                Text("When off, the controller never starts the pump by itself.", style = MaterialTheme.typography.bodySmall)
            }
            Switch(checked = z.autoMode, onCheckedChange = onAuto)
        }
        HorizontalDivider()

        Text("Manual run: ${seconds.toInt()} s", style = MaterialTheme.typography.titleMedium)
        Slider(seconds, { seconds = it }, valueRange = 1f..MAX_MANUAL_SECONDS.toFloat())
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = { onStart(seconds.toInt()) }) { Text("Start") }
            OutlinedButton(onClick = onStop) { Text("Stop") }
        }
        Text("The controller refuses starts during an experiment, after a fault, or when the soil is already wet.",
            style = MaterialTheme.typography.bodySmall)

        if (s.commands.isNotEmpty()) {
            Text("Recent requests", style = MaterialTheme.typography.titleSmall)
            s.commands.forEach { c ->
                Text("${c.cmd}: ${c.status}" + (c.reason?.takeIf { it.isNotBlank() }?.let { " ($it)" } ?: ""),
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        HorizontalDivider()

        Text("Thresholds", style = MaterialTheme.typography.titleMedium)
        Text("Dry below ${dry.toInt()}%")
        Slider(dry, { dry = it }, valueRange = 5f..60f)
        Text("Wet above ${wet.toInt()}%")
        Slider(wet, { wet = it }, valueRange = 30f..95f)
        Text("Skip if rain probability ≥ ${"%.2f".format(rain)}")
        Slider(rain, { rain = it }, valueRange = 0f..1f)
        Button(enabled = dry.toInt() < wet.toInt(), onClick = { onSaveThresholds(dry.toInt(), wet.toInt(), rain.toDouble()) }) { Text("Save thresholds") }
        if (dry.toInt() >= wet.toInt()) Text("Dry must be below wet.", color = MaterialTheme.colorScheme.error)
    }
}
