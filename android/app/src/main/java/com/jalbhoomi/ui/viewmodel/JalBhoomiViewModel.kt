package com.jalbhoomi.ui.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.google.firebase.messaging.FirebaseMessaging
import com.jalbhoomi.data.*
import com.jalbhoomi.live.LiveReading
import com.jalbhoomi.live.LiveStatus
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import retrofit2.HttpException

data class UiState(
    val zones: List<ZoneDto> = emptyList(),
    val selectedId: String? = null,
    val summaries: List<SummaryDto> = emptyList(),
    val daily: List<DailyWaterDto> = emptyList(),
    val total: WaterTotalDto? = null,
    val saved: WaterSavedDto? = null,
    val alerts: List<AlertDto> = emptyList(),
    val advice: AdviceDto? = null,
    val thresholds: ThresholdsDto = ThresholdsDto(),
    val commands: List<CommandDto> = emptyList(),
    val live: LiveReading? = null,
    val loading: Boolean = false,
    val error: String? = null,
    val message: String? = null,
    val backendUrl: String = "",
    val pushEnabled: Boolean = true,
) {
    val selected: ZoneDto? get() = zones.firstOrNull { it.deviceId == selectedId }
}

/** Longest run the ESP32 firmware allows for one manual start (MAX_PUMP_RUN_MS). */
const val MAX_MANUAL_SECONDS = 30

class JalBhoomiViewModel(app: Application) : AndroidViewModel(app) {
    private val settings = SettingsStore(app)
    private var api: BackendApi = ApiClient.create(settings.backendUrl)

    private val _state = MutableStateFlow(UiState(backendUrl = settings.backendUrl, pushEnabled = settings.pushEnabled))
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var pollJob: Job? = null
    private var liveJob: Job? = null

    fun start() {
        if (pollJob?.isActive == true) return
        pollJob = viewModelScope.launch {
            while (true) {
                refresh()
                delay(15_000)
            }
        }
    }

    fun stop() { pollJob?.cancel(); liveJob?.cancel() }

    private fun describe(e: Throwable): String = when (e) {
        is HttpException -> when (e.code()) {
            401 -> "Not authorised. Your account must be on the server's allow-list."
            404 -> "Not found on the server."
            503 -> "Server cannot reach the device right now (MQTT not connected)."
            else -> "Server error ${e.code()}."
        }
        else -> "Cannot reach the backend (${e.javaClass.simpleName}). Check the address in Settings."
    }

    private suspend fun <T> safe(block: suspend () -> T): T? =
        try { block() } catch (e: Exception) { _state.update { it.copy(error = describe(e)) }; null }

    fun refresh() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            val zones = safe { api.zones() } ?: run { _state.update { it.copy(loading = false) }; return@launch }
            val sel = _state.value.selectedId?.takeIf { id -> zones.any { it.deviceId == id } } ?: zones.firstOrNull()?.deviceId
            _state.update { it.copy(zones = zones, selectedId = sel) }
            if (sel != null) loadZone(sel)
            _state.update { it.copy(loading = false) }
        }
    }

    private suspend fun loadZone(id: String) {
        val s = safe { api.summaries(id) }
        val d = safe { api.waterDaily(id) }
        val t = safe { api.waterTotal(id) }
        val sv = safe { api.waterSaved(id) }
        val a = safe { api.alerts(id) }
        val adv = safe { api.advice(id) }
        val th = safe { api.thresholds(id) }
        val c = safe { api.commands(id) }
        _state.update {
            it.copy(
                summaries = s ?: it.summaries, daily = d ?: it.daily, total = t ?: it.total, saved = sv ?: it.saved,
                alerts = a ?: it.alerts, advice = adv ?: it.advice, thresholds = th ?: it.thresholds,
                commands = c ?: it.commands,
            )
        }
        watchLive(id)
        if (settings.pushEnabled) subscribePush(id)
    }

    private fun watchLive(id: String) {
        liveJob?.cancel()
        liveJob = viewModelScope.launch {
            try {
                LiveStatus.latest(id).collect { r -> _state.update { it.copy(live = r) } }
            } catch (_: Exception) { /* Realtime Database not configured: REST data still works */ }
        }
    }

    private fun subscribePush(id: String) {
        viewModelScope.launch {
            try { FirebaseMessaging.getInstance().subscribeToTopic("zone_$id").await() } catch (_: Exception) {}
        }
    }

    fun selectZone(id: String) {
        _state.update { it.copy(selectedId = id, live = null) }
        viewModelScope.launch { loadZone(id) }
    }

    // ---- zone control (all commands are ADVISORY: the ESP32 may refuse them) ----
    private fun send(body: CommandBody, doneText: String) {
        val id = _state.value.selectedId ?: return
        viewModelScope.launch {
            val r = safe { api.sendCommand(id, body) }
            if (r != null) {
                _state.update { it.copy(message = doneText) }
                delay(2_500)                                  // give the ESP32 time to answer
                safe { api.commands(id) }?.let { c -> _state.update { it.copy(commands = c) } }
                safe { api.zones() }?.let { z -> _state.update { it.copy(zones = z) } }
            }
        }
    }

    fun startManual(seconds: Int) =
        send(CommandBody("override_on", durationS = seconds.coerceIn(1, MAX_MANUAL_SECONDS)), "Start request sent. The controller decides.")

    fun stopIrrigation() = send(CommandBody("override_off"), "Stop request sent.")
    fun setAuto(enabled: Boolean) =
        send(CommandBody("set_auto", enabled = enabled), if (enabled) "Automatic mode requested." else "Manual mode requested.")

    fun saveThresholds(dry: Int, wet: Int, rainProb: Double) {
        val id = _state.value.selectedId ?: return
        viewModelScope.launch {
            val r = safe { api.putThresholds(id, ThresholdsDto(dry, wet, rainProb)) }
            if (r != null) _state.update {
                it.copy(thresholds = ThresholdsDto(dry, wet, rainProb),
                    message = if (r.pushedToDevice) "Saved and sent to the controller." else "Saved. The controller was not reachable.")
            }
        }
    }

    fun ackAlert(alertId: Int) {
        viewModelScope.launch {
            if (safe { api.ackAlert(alertId) } != null) _state.update { s -> s.copy(alerts = s.alerts.filter { it.id != alertId }) }
        }
    }

    fun renameZone(name: String) {
        val id = _state.value.selectedId ?: return
        viewModelScope.launch { if (safe { api.rename(id, RenameBody(name)) } != null) refresh() }
    }

    fun setBackendUrl(url: String) {
        settings.backendUrl = url.trim()
        api = ApiClient.create(settings.backendUrl)
        _state.update { it.copy(backendUrl = settings.backendUrl) }
        refresh()
    }

    fun setPush(enabled: Boolean) {
        settings.pushEnabled = enabled
        _state.update { it.copy(pushEnabled = enabled) }
        val id = _state.value.selectedId ?: return
        viewModelScope.launch {
            try {
                val fm = FirebaseMessaging.getInstance()
                if (enabled) fm.subscribeToTopic("zone_$id").await() else fm.unsubscribeFromTopic("zone_$id").await()
            } catch (_: Exception) {}
        }
    }

    fun clearMessage() = _state.update { it.copy(message = null, error = null) }
}
