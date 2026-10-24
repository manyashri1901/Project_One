package com.jalbhoomi.data

import com.google.android.gms.tasks.Tasks
import com.google.firebase.auth.FirebaseAuth
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import java.util.concurrent.TimeUnit

object ApiClient {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    /** Adds the signed-in user's Firebase ID token to every request. */
    private val authInterceptor = okhttp3.Interceptor { chain ->
        val user = FirebaseAuth.getInstance().currentUser
        val token = try {
            if (user != null) Tasks.await(user.getIdToken(false), 10, TimeUnit.SECONDS).token else null
        } catch (e: Exception) { null }
        val req = chain.request().newBuilder().apply {
            if (!token.isNullOrEmpty()) header("Authorization", "Bearer $token")
        }.build()
        chain.proceed(req)
    }

    fun create(baseUrl: String): BackendApi {
        val client = OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .build()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(BackendApi::class.java)
    }
}
