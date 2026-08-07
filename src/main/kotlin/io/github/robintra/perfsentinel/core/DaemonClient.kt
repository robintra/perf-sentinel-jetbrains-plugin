package io.github.robintra.perfsentinel.core

import com.intellij.util.io.HttpRequests
import com.intellij.util.concurrency.AppExecutorUtil
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException
import java.net.HttpURLConnection
import java.nio.charset.StandardCharsets
import java.util.concurrent.atomic.AtomicReference
import kotlin.time.Duration.Companion.milliseconds

const val MAX_RESPONSE_BYTES = 5 * 1024 * 1024

class DaemonRequestException(statusCode: Int) : IOException("Perf Sentinel daemon returned HTTP $statusCode")
class ResponseTooLargeException : IOException("Perf Sentinel daemon response exceeds 5 MiB")
class DaemonTimeoutException(timeoutMillis: Long) : IOException("Perf Sentinel daemon request exceeded $timeoutMillis ms")

class DaemonClient(private val timeoutMillis: Long = 5_000) {
    init {
        require(timeoutMillis > 0)
    }

    suspend fun fetch(endpoint: String, service: String): List<FindingResponse> =
        withTimeoutOrNull(timeoutMillis.milliseconds) { execute(endpoint, service) }
            ?: throw DaemonTimeoutException(timeoutMillis)

    private suspend fun execute(endpoint: String, service: String): List<FindingResponse> =
        suspendCancellableCoroutine { continuation ->
            val connection = AtomicReference<HttpURLConnection>()
            val task = AppExecutorUtil.getAppExecutorService().submit {
                try {
                    val socketTimeout = timeoutMillis.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
                    val findings = HttpRequests.request(findingsUri(endpoint, service).toString())
                        .connectTimeout(socketTimeout)
                        .readTimeout(socketTimeout)
                        .throwStatusCodeException(false)
                        .connect { request ->
                            val http = request.connection as HttpURLConnection
                            connection.set(http)
                            if (!continuation.isActive) {
                                http.disconnect()
                                throw CancellationException()
                            }

                            val status = http.responseCode
                            if (status !in 200..299) throw DaemonRequestException(status)

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
