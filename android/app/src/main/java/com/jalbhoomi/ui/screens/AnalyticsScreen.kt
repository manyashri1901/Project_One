package com.jalbhoomi.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jalbhoomi.ui.components.*
import com.jalbhoomi.ui.viewmodel.UiState

@Composable
fun AnalyticsScreen(s: UiState) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Analytics", style = MaterialTheme.typography.headlineSmall)

        Text("Soil moisture (last ${s.summaries.size} min-summaries)", style = MaterialTheme.typography.titleSmall)
        LineChart(s.summaries.map { it.soilPct }, Modifier.fillMaxWidth().height(160.dp), yMin = 0.0, yMax = 100.0)

        Text("Water used per day (litres)", style = MaterialTheme.typography.titleSmall)
        BarChart(s.daily.map { it.litres }, Modifier.fillMaxWidth().height(140.dp))
        s.daily.lastOrNull()?.let { Text("Latest day ${it.day}: ${"%.2f".format(it.litres)} L", style = MaterialTheme.typography.bodySmall) }

        s.total?.let {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard("Total measured", "%.1f L".format(it.totalLitres), Modifier.weight(1f))
                StatCard("Per cycle (avg)", it.avgPerCycle?.let { v -> "%.2f L".format(v) } ?: "--", Modifier.weight(1f), hint = "${it.cycles} cycles")
            }
            if (it.note.isNotBlank()) Text(it.note, style = MaterialTheme.typography.bodySmall)
        }

        s.saved?.let {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Text("Water not used (estimate)", style = MaterialTheme.typography.labelMedium)
                    Text(it.litresAvoided?.let { v -> "≈ %.1f L".format(v) } ?: "Not enough measured cycles yet",
                        style = MaterialTheme.typography.titleLarge)
                    Text("${it.skipEpisodes} skip episode(s), ${it.cyclesMeasured} measured cycle(s)", style = MaterialTheme.typography.bodySmall)
                    Text(it.basis, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}
