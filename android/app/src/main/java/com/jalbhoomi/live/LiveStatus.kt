package com.jalbhoomi.live

import com.google.firebase.database.DataSnapshot
import com.google.firebase.database.DatabaseError
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.database.ValueEventListener
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/** Latest reading mirrored by the backend into Firebase Realtime Database (zones/<id>/latest). */
data class LiveReading(
    val soilPct: Double?,
    val tempC: Double?,
    val humPct: Double?,
    val flowL: Double?,
    val pumpOnS: Double?,
    val faults: Double?,
    val ts: Long?,
)

object LiveStatus {
    private fun DataSnapshot.num(key: String): Double? = (child(key).value as? Number)?.toDouble()

    fun latest(deviceId: String): Flow<LiveReading?> = callbackFlow {
        val ref = FirebaseDatabase.getInstance().getReference("zones/$deviceId/latest")
        val listener = object : ValueEventListener {
            override fun onDataChange(snapshot: DataSnapshot) {
                trySend(
                    if (!snapshot.exists()) null
                    else LiveReading(
                        soilPct = snapshot.num("soil_pct"), tempC = snapshot.num("temp_c"),
                        humPct = snapshot.num("hum_pct"), flowL = snapshot.num("flow_l"),
                        pumpOnS = snapshot.num("pump_on_s"), faults = snapshot.num("faults"),
                        ts = (snapshot.child("ts").value as? Number)?.toLong(),
                    )
                )
            }

            override fun onCancelled(error: DatabaseError) { trySend(null) }
        }
        ref.addValueEventListener(listener)
        awaitClose { ref.removeEventListener(listener) }
    }
}
