# Android app (Kotlin, Jetpack Compose, Firebase)

Stack per the report: native Android with Kotlin and Jetpack Compose, Firebase Authentication and Realtime Database.
The app talks to the FastAPI backend over REST and shows live readings from the Realtime Database mirror.

**Status: source written, syntax-checked only. It has never been built or run on a device.**
Expect to fix a few compile errors on the first Gradle sync; they are small and local.

## Screens
Login (Firebase email/password) · Home (soil gauge, temperature, humidity, pump, water total, alerts, prediction) ·
Zones (select, rename) · Control (auto on/off, manual start/stop, thresholds) · Analytics (soil trend,
litres per day, total, estimated water not used) · Settings (backend address, push on/off, sign out).
Every control sends a REQUEST: the ESP32 arbitrates it and may refuse; the refusal reason is shown.
Languages: English only (multi-language is not implemented).

## Run it
1. Install Android Studio. Open this `android/` folder.
2. Create a Firebase project; enable Authentication (email/password), Realtime Database and Cloud Messaging.
   Add an Android app with package `com.jalbhoomi`, download `google-services.json` into `android/app/`
   (it is git-ignored; never commit it). Create one user in the console.
3. Backend: set `FIREBASE_ENABLED=true`, `FIREBASE_CREDENTIALS`, `FIREBASE_DB_URL`, and put that user's UID in
   `ALLOWED_FIREBASE_UIDS` (otherwise writes are refused and the app shows "Not authorised").
4. Backend address: emulator default `http://10.0.2.2:8000/`; for a real phone set your PC's LAN address in
   Settings (cleartext HTTP is allowed for development only; use HTTPS when deployed).
5. Run. Without `google-services.json` the project still compiles but sign-in shows "Firebase is not configured".

Source layout: `data/` (API models, Retrofit, settings), `live/` (Realtime Database + push), `ui/` (theme,
components, screens, view model), `MainActivity.kt`.
