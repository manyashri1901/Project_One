package com.jalbhoomi.data

import android.content.Context
import com.jalbhoomi.BuildConfig

/** Small per-phone settings (backend address, push on/off). */
class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("jalbhoomi", Context.MODE_PRIVATE)

    var backendUrl: String
        get() = prefs.getString("backend_url", BuildConfig.BACKEND_URL) ?: BuildConfig.BACKEND_URL
        set(v) { prefs.edit().putString("backend_url", if (v.endsWith("/")) v else "$v/").apply() }

    var pushEnabled: Boolean
        get() = prefs.getBoolean("push", true)
        set(v) { prefs.edit().putBoolean("push", v).apply() }
}
