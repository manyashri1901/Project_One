package com.jalbhoomi.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Ring gauge for soil moisture (0-100 %). Shows "--" when the value is unknown. */
@Composable
fun CircularMoistureGauge(percent: Double?, modifier: Modifier = Modifier) {
    val track = MaterialTheme.colorScheme.surfaceVariant
    val colour = when {
        percent == null -> Color.Gray
        percent < 30 -> MaterialTheme.colorScheme.error
        percent > 60 -> MaterialTheme.colorScheme.secondary
        else -> MaterialTheme.colorScheme.primary
    }
    Box(modifier.size(160.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize().padding(10.dp)) {
            val stroke = Stroke(width = 18f, cap = StrokeCap.Round)
            drawArc(track, 135f, 270f, false, style = stroke)
            if (percent != null) drawArc(colour, 135f, 270f * (percent.coerceIn(0.0, 100.0) / 100.0).toFloat(), false, style = stroke)
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(if (percent == null) "--" else "${percent.toInt()}%", fontSize = 34.sp, fontWeight = FontWeight.Bold)
            Text("soil moisture", style = MaterialTheme.typography.labelMedium)
        }
    }
}

@Composable
fun StatCard(label: String, value: String, modifier: Modifier = Modifier, hint: String? = null) {
    Card(modifier, shape = RoundedCornerShape(14.dp)) {
        Column(Modifier.padding(14.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium)
            Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            if (hint != null) Text(hint, style = MaterialTheme.typography.bodySmall)
        }
    }
}

enum class Level { INFO, WARN, FAULT }

@Composable
fun StatusBanner(text: String, level: Level, modifier: Modifier = Modifier, onDismiss: (() -> Unit)? = null) {
    val bg = when (level) {
        Level.FAULT -> MaterialTheme.colorScheme.errorContainer
        Level.WARN -> Color(0xFFFFE0B2)
        Level.INFO -> MaterialTheme.colorScheme.secondaryContainer
    }
    Card(modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = bg)) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(text, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
            if (onDismiss != null) TextButton(onClick = onDismiss) { Text("Dismiss") }
        }
    }
}

/** Simple line chart; null points break the line. No data -> an honest empty-state message. */
@Composable
fun LineChart(values: List<Double?>, modifier: Modifier = Modifier, yMin: Double? = null, yMax: Double? = null) {
    val pts = values.filterNotNull()
    if (pts.size < 2) { Box(modifier, contentAlignment = Alignment.Center) { Text("Not enough data yet") }; return }
    val lo = yMin ?: pts.min()
    val hi = (yMax ?: pts.max()).let { if (it <= lo) lo + 1 else it }
    val colour = MaterialTheme.colorScheme.primary
    val grid = MaterialTheme.colorScheme.outlineVariant
    Canvas(modifier) {
        drawLine(grid, Offset(0f, size.height), Offset(size.width, size.height))
        drawLine(grid, Offset(0f, 0f), Offset(0f, size.height))
        val stepX = size.width / (values.size - 1).coerceAtLeast(1)
        var path = Path(); var open = false
        values.forEachIndexed { i, v ->
            if (v == null) { if (open) { drawPath(path, colour, style = Stroke(4f)); path = Path(); open = false }; return@forEachIndexed }
            val x = i * stepX
            val y = (size.height * (1 - (v - lo) / (hi - lo))).toFloat()
            if (!open) { path.moveTo(x, y); open = true } else path.lineTo(x, y)
        }
        if (open) drawPath(path, colour, style = Stroke(4f))
    }
}

/** Bar chart (e.g. litres per day). */
@Composable
fun BarChart(values: List<Double>, modifier: Modifier = Modifier) {
    if (values.isEmpty()) { Box(modifier, contentAlignment = Alignment.Center) { Text("No irrigation recorded yet") }; return }
    val colour = MaterialTheme.colorScheme.secondary
    val hi = (values.max()).let { if (it <= 0) 1.0 else it }
    Canvas(modifier) {
        val slot = size.width / values.size
        values.forEachIndexed { i, v ->
            val h = (size.height * (v / hi)).toFloat()
            drawRect(colour, Offset(i * slot + slot * 0.15f, size.height - h), Size(slot * 0.7f, h))
        }
    }
}
