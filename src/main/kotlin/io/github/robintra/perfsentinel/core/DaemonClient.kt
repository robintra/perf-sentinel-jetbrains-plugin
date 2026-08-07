package io.github.robintra.perfsentinel.core

import com.intellij.util.io.HttpRequests
import java.io.IOException
import java.net.HttpURLConnection
import java.nio.charset.StandardCharsets

const val MAX_RESPONSE_BYTES = 5 * 1024 * 1024

class DaemonRequestException(statusCode: Int) : IOException("Perf Sentinel daemon returned HTTP $statusCode")
class ResponseTooLargeException : IOException("Perf Sentinel daemon response exceeds 5 MiB")

class DaemonClient {
    fun fetch(endpoint: String, service: String): List<FindingResponse> =
        HttpRequests.request(findingsUri(endpoint, service).toString())
            .connectTimeout(5_000)
            .readTimeout(5_000)
            .throwStatusCodeException(false)
            .connect { request ->
                val status = (request.connection as HttpURLConnection).responseCode
                if (status !in 200..299) throw DaemonRequestException(status)

                val bytes = request.inputStream.readNBytes(MAX_RESPONSE_BYTES + 1)
                if (bytes.size > MAX_RESPONSE_BYTES) throw ResponseTooLargeException()
                parseFindings(String(bytes, StandardCharsets.UTF_8)).map { it.copy(source = endpoint) }
            }
}
