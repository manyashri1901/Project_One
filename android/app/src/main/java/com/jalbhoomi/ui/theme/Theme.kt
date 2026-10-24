package com.jalbhoomi.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val Green = Color(0xFF2E7D32)
private val Scheme = lightColorScheme(
    primary = Green,
    secondary = Color(0xFF0277BD),
    tertiary = Color(0xFFEF6C00),
    error = Color(0xFFC62828),
)

@Composable
fun JalBhoomiTheme(content: @Composable () -> Unit) = MaterialTheme(colorScheme = Scheme, content = content)
