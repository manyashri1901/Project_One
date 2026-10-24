package com.jalbhoomi

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.google.firebase.auth.FirebaseAuth
import com.jalbhoomi.ui.screens.*
import com.jalbhoomi.ui.theme.JalBhoomiTheme
import com.jalbhoomi.ui.viewmodel.JalBhoomiViewModel

private enum class Tab(val label: String) { Home("Home"), Zones("Zones"), Control("Control"), Analytics("Analytics"), Settings("Settings") }

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { JalBhoomiTheme { Root() } }
    }
}

private fun signedIn(): Boolean = try { FirebaseAuth.getInstance().currentUser != null } catch (_: Exception) { false }

@Composable
private fun Root() {
    var loggedIn by remember { mutableStateOf(signedIn()) }
    val notifPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33) notifPerm.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
    if (!loggedIn) { LoginScreen { loggedIn = true }; return }

    val vm: JalBhoomiViewModel = viewModel()
    val s by vm.state.collectAsStateWithLifecycle()
    var tab by remember { mutableStateOf(Tab.Home) }
    DisposableEffect(Unit) { vm.start(); onDispose { vm.stop() } }

    Scaffold(bottomBar = {
        NavigationBar {
            Tab.values().forEach { t ->
                NavigationBarItem(selected = tab == t, onClick = { tab = t },
                    icon = { Text(t.label.take(1)) }, label = { Text(t.label) })
            }
        }
    }) { pad ->
        val m = Modifier.padding(pad)
        Surface(m) {
            when (tab) {
                Tab.Home -> HomeScreen(s, vm::ackAlert, vm::refresh)
                Tab.Zones -> ZonesScreen(s, { vm.selectZone(it); tab = Tab.Home }, vm::renameZone)
                Tab.Control -> ZoneControlScreen(s, vm::startManual, vm::stopIrrigation, vm::setAuto, vm::saveThresholds, vm::clearMessage)
                Tab.Analytics -> AnalyticsScreen(s)
                Tab.Settings -> SettingsScreen(s, vm::setBackendUrl, vm::setPush) {
                    try { FirebaseAuth.getInstance().signOut() } catch (_: Exception) {}
                    loggedIn = false
                }
            }
        }
    }
}
