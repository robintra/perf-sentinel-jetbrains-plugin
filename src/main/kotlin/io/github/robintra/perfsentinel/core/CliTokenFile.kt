package io.github.robintra.perfsentinel.core

import com.google.gson.JsonParser
import io.github.robintra.perfsentinel.PerfSentinelBundle
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.NoSuchFileException
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.attribute.PosixFileAttributeView
import java.nio.file.attribute.PosixFilePermission
import java.nio.file.attribute.PosixFilePermissions

/**
 * The token `perf-sentinel login --hub <url>` stored, for one Hub at a time. Not a data class, so its
 * string form cannot print the token.
 */
class CliToken internal constructor(
    private val path: Path,
    private val hubUrl: String,
    private val token: String,
    private val expiresAtMs: Long,
    // The permissions, when users other than the owner hold any.
    private val openMode: String?,
) {
    /**
     * The token when [endpoint] is the Hub that issued it, otherwise null. Refused, with the command
     * that fixes it, when the file is open to other users or the token expired, as the CLI refuses it.
     */
    fun forEndpoint(endpoint: String, nowMs: Long): String? {
        val hub = runCatching { normalizeEndpoints(listOf(hubUrl)).single() }.getOrNull()
        if (hub != endpoint) return null
        if (openMode != null) {
            throw CredentialRefusedException(PerfSentinelBundle.message("error.cli.open", path, openMode, endpoint))
        }
        if (expiresAtMs <= nowMs) throw CredentialRefusedException(PerfSentinelBundle.message("error.cli.expired", endpoint))
        return token
    }

    override fun toString() = "CliToken(hubUrl=$hubUrl, expiresAtMs=$expiresAtMs)"
}

/** The file `perf-sentinel login` writes, read with the checks the CLI applies before using it. */
object CliTokenFile {
    private const val MAX_BYTES = 64 * 1024
    private val OWNER_ONLY = setOf(
        PosixFilePermission.OWNER_READ,
        PosixFilePermission.OWNER_WRITE,
        PosixFilePermission.OWNER_EXECUTE,
    )

    /** `$XDG_CONFIG_HOME` when absolute, else `$HOME/.config`, and `%APPDATA%` on Windows, as the CLI resolves it. */
    fun locate(env: (String) -> String?, windows: Boolean): Path? {
        fun set(name: String) = env(name)?.takeIf(String::isNotEmpty)
        val root = if (windows) {
            set("APPDATA")?.let(Path::of)
        } else {
            set("XDG_CONFIG_HOME")?.let(Path::of)?.takeIf(Path::isAbsolute) ?: set("HOME")?.let { Path.of(it, ".config") }
        }
        return root?.resolve("perf-sentinel")?.resolve("hub-token.json")
    }

    /** Null when there is no file. A link, anything but a regular file, or content that does not parse is refused. */
    fun read(path: Path): CliToken? {
        val attributes = try {
            Files.readAttributes(path, BasicFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
        } catch (_: NoSuchFileException) {
            return null
        }
        if (!attributes.isRegularFile) throw CredentialRefusedException(PerfSentinelBundle.message("error.cli.link", path))
        val openMode = Files.getFileAttributeView(path, PosixFileAttributeView::class.java, LinkOption.NOFOLLOW_LINKS)
            ?.readAttributes()?.permissions()
            ?.takeUnless(OWNER_ONLY::containsAll)
            ?.let(PosixFilePermissions::toString)
        // NOFOLLOW again at open: a link swapped in after the check is still refused.
        val bytes = Files.newInputStream(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)
            .use { it.readNBytes(MAX_BYTES) }
        // The parser quotes what it fails on, which could be the token: keep neither its message nor its cause.
        return runCatching {
            val json = JsonParser.parseString(String(bytes, StandardCharsets.UTF_8)).asJsonObject
            // Blank is refused as the CLI refuses it: normalizing an empty hub_url falls back to the default endpoint.
            val hubUrl = json["hub_url"].asString.also { require(it.isNotBlank()) }
            val token = json["token"].asString.also { require(it.isNotBlank()) }
            CliToken(path, hubUrl, token, json["expires_at_ms"].asLong, openMode)
        }.getOrElse { throw CredentialRefusedException(PerfSentinelBundle.message("error.cli.invalid", path)) }
    }
}
