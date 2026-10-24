package com.jalbhoomi.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Field names mirror the FastAPI backend's JSON (snake_case).

@Serializable
data class ZoneDto(
    @SerialName("device_id") val deviceId: String,
    val name: String? = null,
    @SerialName("auto_mode") val autoMode: Boolean = true,
    @SerialName("last_seen") val lastSeen: String? = null,
    @SerialName("soil_pct") val soilPct: Double? = null,
    @SerialName("temp_c") val tempC: Double? = null,
    @SerialName("hum_pct") val humPct: Double? = null,
    @SerialName("pump_on_recently") val pumpOnRecently: Boolean = false,
    @SerialName("open_faults") val openFaults: Int = 0,
) {
    val title: String get() = name?.takeIf { it.isNotBlank() } ?: deviceId
}

@Serializable
data class SummaryDto(
    val ts: String,
    @SerialName("soil_pct_mean") val soilPct: Double? = null,
    @SerialName("soil_raw_mean") val soilRaw: Double? = null,
    @SerialName("temp_c_mean") val tempC: Double? = null,
    @SerialName("hum_pct_mean") val humPct: Double? = null,
    @SerialName("flow_l") val flowL: Double? = null,
    @SerialName("pump_on_s") val pumpOnS: Int? = null,
    val faults: Int? = null,
    val rain: Int? = null,
)

@Serializable
data class DailyWaterDto(val day: String, val litres: Double, @SerialName("minutes_reported") val minutes: Int)

@Serializable
data class WaterTotalDto(
    @SerialName("total_litres") val totalLitres: Double,
    @SerialName("irrigation_cycles") val cycles: Int,
    @SerialName("avg_litres_per_cycle") val avgPerCycle: Double? = null,
    val note: String = "",
)

@Serializable
data class WaterSavedDto(
    val estimate: Boolean = true,
    @SerialName("skip_episodes") val skipEpisodes: Int,
    @SerialName("cycles_measured") val cyclesMeasured: Int,
    @SerialName("litres_avoided_estimate") val litresAvoided: Double? = null,
    val basis: String = "",
)

@Serializable
data class AlertDto(
    val id: Int,
    @SerialName("device_id") val deviceId: String,
    val ts: String,
    val type: String,
    val severity: String,
    val detail: String? = null,
    val acknowledged: Int = 0,
)

@Serializable
data class CommandDto(
    @SerialName("cmd_id") val cmdId: String,
    val cmd: String,
    val status: String,
    val reason: String? = null,
    @SerialName("created_at") val createdAt: String,
)

/** Body for POST /devices/{id}/commands. Only the fields a command needs are sent. */
@Serializable
data class CommandBody(
    val cmd: String,
    @SerialName("duration_s") val durationS: Int? = null,
    val enabled: Boolean? = null,
    val action: String? = null,
    val hours: Int? = null,
)

@Serializable
data class ThresholdsDto(
    @SerialName("soil_dry_pct") val soilDryPct: Int = 30,
    @SerialName("soil_wet_pct") val soilWetPct: Int = 60,
    @SerialName("rain_probability") val rainProbability: Double = 0.4,
)

@Serializable
data class ThresholdPutDto(
    @SerialName("soil_dry_pct") val soilDryPct: Int,
    @SerialName("soil_wet_pct") val soilWetPct: Int,
    @SerialName("rain_probability") val rainProbability: Double,
    @SerialName("pushed_to_device") val pushedToDevice: Boolean = false,
)

@Serializable
data class RenameBody(val name: String)

@Serializable
data class AdviceDto(
    @SerialName("model_loaded") val modelLoaded: Boolean = false,
    val ready: Boolean = false,
    val reason: String? = null,
    @SerialName("horizon_min") val horizonMin: Int? = null,
    @SerialName("probability_dry_within_horizon") val probabilityDry: Double? = null,
    val note: String? = null,
)
