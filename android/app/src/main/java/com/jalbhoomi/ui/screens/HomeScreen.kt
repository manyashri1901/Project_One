package com.jalbhoomi.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jalbhoomi.ui.components.*
import com.jalbhoomi.ui.viewmodel.UiState

private fun alertText(type: String) = when (type) {
    "predictive_skip" -> "Irrigation skipped: rain is forecast."
    "advisory_skip" -> "Irrigation skipped on the server's advice."
    "fault_max_time" -> "FAULT: pump hit the maximum run time and was stopped."
    "fault_no_flow" -> "FAULT: pump on but no water flow detected."
    "fault_valve_stuck_open" -> "FAULT: flow detected while the pump is off (valve stuck open?)."
    "command_rejected" -> "A request from the app was refused by the controller."
    else -> type.replace('_', ' ')
}

@Composable
fun HomeScreen(s: UiState, onAck: (Int) -> Unit, onRefresh: () -> Unit) {
    val z = s.selected
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        s.error?.let { StatusBanner(it, Level.WARN) }
        if (z == null) {
            Text("No device has reported yet.", style = MaterialTheme.typography.titleMedium)
            Text("Power the ESP32, make sure it reaches the MQTT broker and wait for its first 60-second summary.")
            Button(onClick = onRefresh) { Text("Refresh") }
            return@Column
        }
        Text(z.title, style = MaterialTheme.typography.headlineSmall)
        Text(if (z.autoMode) "Automatic mode" else "Manual mode (automatic starts are off)", style = MaterialTheme.typography.bodyMedium)

        s.alerts.forEach { a ->
            StatusBanner(alertText(a.type), if (a.severity == "fault") Level.FAULT else Level.INFO, onDismiss = { onAck(a.id) })
        }

        val soil = s.live?.soilPct ?: z.soilPct
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) { CircularMoistureGauge(soil) }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            StatCard("Temperature", (s.live?.tempC ?: z.tempC)?.let { "%.1f °C".format(it) } ?: "--", Modifier.weight(1f))
            StatCard("Humidity", (s.live?.humPct ?: z.humPct)?.let { "%.0f %%".format(it) } ?: "--", Modifier.weight(1f))
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            StatCard("Pump", if (z.pumpOnRecently) "Ran recently" else "Idle", Modifier.weight(1f))
            StatCard("Water (all time)", s.total?.let { "%.1f L".format(it.totalLitres) } ?: "--", Modifier.weight(1f),
                hint = s.total?.let { "${it.cycles} cycles" })
        }
        s.advice?.let { adv ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Text("Prediction", style = MaterialTheme.typography.labelMedium)
                    if (adv.modelLoaded && adv.ready && adv.probabilityDry != null)
                        Text("Chance of drying out within ${adv.horizonMin} min: ${(adv.probabilityDry * 100).toInt()}%",
                            style = MaterialTheme.typography.titleMedium)
                    else Text("No trained model active yet." + (adv.reason?.let { " ($it)" } ?: ""))
                    adv.note?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                }
            }
        }
        z.lastSeen?.let { Text("Last report: $it", style = MaterialTheme.typography.bodySmall) }
    }
}
