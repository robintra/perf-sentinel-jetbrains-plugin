package io.github.robintra.perfsentinel.core

import com.intellij.util.concurrency.AppExecutorUtil
import com.intellij.util.io.HttpRequests
import io.github.robintra.perfsentinel.PerfSentinelBundle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException
import java.net.HttpURLConnection
import java.nio.charset.StandardCharsets
import java.util.concurrent.atomic.AtomicReference
import java.util.logging.Level
import java.util.logging.Logger
import kotlin.time.Duration.Companion.milliseconds

const val MAX_RESPONSE_BYTES = 5 * 1024 * 1024

class DaemonRequestException(statusCode: Int, endpoint: String, loginHint: Boolean = false) : IOException(
    when (statusCode) {
        401 -> PerfSentinelBundle.message(if (loginHint) "error.credential.login" else "error.credential.unauthorized", endpoint)
        403 -> PerfSentinelBundle.message("error.credential.forbidden")
        else -> "Perf Sentinel daemon returned HTTP $statusCode"
    },
)

class ResponseTooLargeException : IOException("Perf Sentinel daemon response exceeds 5 MiB")
class DaemonTimeoutException(timeoutMillis: Long) :
    IOException("Perf Sentinel daemon request exceeded $timeoutMillis ms")

class CredentialRefusedException(message: String) : IOException(message)

class DaemonClient(private val timeoutMillis: Long = 5_000) {
    init {
        require(timeoutMillis > 0)
    }

    /**
     * [secret], when given, goes out as `Authorization: Bearer`, which both the Hub and the daemon read.
     * [loginHint] makes a 401 point at `perf-sentinel login`: true unless [secret] is the endpoint's own credential.
     */
    suspend fun fetch(
        endpoint: String,
        service: String,
        secret: String? = null,
        loginHint: Boolean = secret == null,
    ): List<FindingResponse> {
        if (secret != null) {
            credentialRefusal(endpoint, secret)?.let { throw CredentialRefusedException(it) }
            // At FINE, HttpURLConnection logs every request header, Authorization included, and nothing can mask one.
            if (Logger.getLogger("sun.net.www.protocol.http.HttpURLConnection").isLoggable(Level.FINE)) {
                throw CredentialRefusedException(PerfSentinelBundle.message("error.credential.debuglog"))
            }
        }
        return withTimeoutOrNull(timeoutMillis.milliseconds) { execute(endpoint, service, secret, loginHint) }
            ?: throw DaemonTimeoutException(timeoutMillis)
    }

    private suspend fun execute(endpoint: String, service: String, secret: String?, loginHint: Boolean): List<FindingResponse> =
        suspendCancellableCoroutine { continuation ->
            val connection = AtomicReference<HttpURLConnection>()
            val task = AppExecutorUtil.getAppExecutorService().submit {
                try {
                    val socketTimeout = timeoutMillis.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
                    val findings = HttpRequests.request(findingsUri(endpoint, service).toString())
                        .connectTimeout(socketTimeout)
                        .readTimeout(socketTimeout)
                        // HttpRequests re-tunes every redirect hop, so following one would hand the secret to its target.
                        .followRedirects(secret == null)
                        .tuner { if (secret != null) it.setRequestProperty("Authorization", "Bearer $secret") }
                        .throwStatusCodeException(false)
                        .connect { request ->
                            val http = request.connection as HttpURLConnection
                            connection.set(http)
                            if (!continuation.isActive) {
                                http.disconnect()
                                throw CancellationException()
                            }

                            val status = http.responseCode
                            if (status !in 200..299) throw DaemonRequestException(status, endpoint, loginHint)

                            val bytes = request.inputStream.readNBytes(MAX_RESPONSE_BYTES + 1)
                            if (bytes.size > MAX_RESPONSE_BYTES) throw ResponseTooLargeException()
                            parseFindings(String(bytes, StandardCharsets.UTF_8)).map { it.copy(source = endpoint) }
                        }
                    if (continuation.isActive) continuation.resumeWith(Result.success(findings))
                } catch (error: Exception) {
                    if (continuation.isActive) continuation.resumeWith(Result.failure(error))
                }
            }
            continuation.invokeOnCancellation {
                connection.get()?.disconnect()
                task.cancel(true)
            }
        }
}
