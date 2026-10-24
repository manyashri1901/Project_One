package com.jalbhoomi.data

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

/** The FastAPI backend (backend/app/main.py). Write calls need a Firebase login on the server's allow-list. */
interface BackendApi {
    @GET("zones") suspend fun zones(): List<ZoneDto>

    @PUT("devices/{id}") suspend fun rename(@Path("id") id: String, @Body body: RenameBody): Map<String, String>

    @GET("devices/{id}/summaries")
    suspend fun summaries(@Path("id") id: String, @Query("limit") limit: Int = 240): List<SummaryDto>

    @GET("devices/{id}/water/daily") suspend fun waterDaily(@Path("id") id: String): List<DailyWaterDto>
    @GET("devices/{id}/water/total") suspend fun waterTotal(@Path("id") id: String): WaterTotalDto
    @GET("devices/{id}/water/saved") suspend fun waterSaved(@Path("id") id: String): WaterSavedDto

    @GET("devices/{id}/alerts")
    suspend fun alerts(@Path("id") id: String, @Query("unacknowledged") unack: Boolean = true): List<AlertDto>

    @POST("alerts/{alertId}/ack") suspend fun ackAlert(@Path("alertId") alertId: Int): AlertDto

    @POST("devices/{id}/commands") suspend fun sendCommand(@Path("id") id: String, @Body body: CommandBody): CommandDto
    @GET("devices/{id}/commands") suspend fun commands(@Path("id") id: String, @Query("limit") limit: Int = 5): List<CommandDto>

    @GET("devices/{id}/thresholds") suspend fun thresholds(@Path("id") id: String): ThresholdsDto
    @PUT("devices/{id}/thresholds") suspend fun putThresholds(@Path("id") id: String, @Body body: ThresholdsDto): ThresholdPutDto

    @GET("devices/{id}/advice") suspend fun advice(@Path("id") id: String): AdviceDto
}
