package io.github.robintra.perfsentinel.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.nio.file.FileSystems
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions

class CliTokenFileTest {
    @get:Rule
    val folder = TemporaryFolder()

    private val posix = "posix" in FileSystems.getDefault().supportedFileAttributeViews()

    @Test
    fun `locates the file where perf-sentinel login writes it`() {
        // Absolute on every platform, so the test also holds on Windows.
        val root = folder.root.toPath()
        val xdg = root.resolve("xdg")
        val home = root.resolve("home")
        val expectedHome = home.resolve(".config").resolve("perf-sentinel").resolve("hub-token.json")

        val withXdg = mapOf("XDG_CONFIG_HOME" to xdg.toString(), "HOME" to home.toString())
        assertEquals(xdg.resolve("perf-sentinel").resolve("hub-token.json"), CliTokenFile.locate(withXdg::get, windows = false))

        // The XDG spec ignores a relative value, and an empty one is unset.
        val relative = mapOf("XDG_CONFIG_HOME" to "config", "HOME" to home.toString())
        assertEquals(expectedHome, CliTokenFile.locate(relative::get, windows = false))
        val empty = mapOf("XDG_CONFIG_HOME" to "", "HOME" to home.toString())
        assertEquals(expectedHome, CliTokenFile.locate(empty::get, windows = false))

        val appData = mapOf("APPDATA" to root.toString(), "XDG_CONFIG_HOME" to xdg.toString())
        assertEquals(root.resolve("perf-sentinel").resolve("hub-token.json"), CliTokenFile.locate(appData::get, windows = true))

        assertNull(CliTokenFile.locate(emptyMap<String, String>()::get, windows = false))
    }

    @Test
    fun `no file reads as no sign-in`() {
        assertNull(CliTokenFile.read(folder.root.toPath().resolve("hub-token.json")))
    }

    @Test
    fun `the token goes only to the Hub that issued it`() {
        val token = CliTokenFile.read(tokenFile(HUB))!!

        assertEquals(TOKEN, token.forEndpoint(HUB, NOW))
        assertNull(token.forEndpoint("https://other-hub.example.test", NOW))
        assertNull(token.forEndpoint("http://127.0.0.1:4318", NOW))
    }

    @Test
    fun `matches the endpoint as the settings normalize it`() {
        // The CLI keeps the URL as typed, less its trailing slash.
        val token = CliTokenFile.read(tokenFile("HTTPS://Hub.Example.Test:443/"))!!

        assertEquals(TOKEN, token.forEndpoint(HUB, NOW))
    }

    @Test
    fun `an expired token is refused with the sign-in command`() {
        val token = CliTokenFile.read(tokenFile(HUB, expiresAtMs = NOW))!!

        val error = assertThrows(CredentialRefusedException::class.java) { token.forEndpoint(HUB, NOW) }

        assertEquals("The perf-sentinel sign-in to $HUB expired: run `perf-sentinel login --hub $HUB`.", error.message)
        // An expired sign-in elsewhere does not stand in the way of another endpoint.
        assertNull(token.forEndpoint("http://127.0.0.1:4318", NOW))
    }

    @Test
    fun `a file other users can read is refused for its Hub`() {
        assumeTrue(posix)
        val path = tokenFile(HUB)
        Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-r--r--"))
        val token = CliTokenFile.read(path)!!

        val error = assertThrows(CredentialRefusedException::class.java) { token.forEndpoint(HUB, NOW) }

        assertEquals(
            "The token file $path is open to other users (rw-r--r--): run `perf-sentinel logout`, " +
                "then `perf-sentinel login --hub $HUB`.",
            error.message,
        )
        assertNull(token.forEndpoint("http://127.0.0.1:4318", NOW))
    }

    @Test
    fun `a symlink is refused, even to a valid file`() {
        assumeTrue(posix)
        val link = folder.root.toPath().resolve("link.json")
        Files.createSymbolicLink(link, tokenFile(HUB))

        val error = assertThrows(CredentialRefusedException::class.java) { CliTokenFile.read(link) }

        assertEquals("The token file $link is not a regular file, so it is not read.", error.message)
    }

    @Test
    fun `a file that does not parse is refused without quoting it`() {
        val path = writeOwnerOnly("""{"hub_url":"$HUB","token":"$TOKEN"""")

        val error = assertThrows(CredentialRefusedException::class.java) { CliTokenFile.read(path) }

        assertEquals("The token file $path is not valid: run `perf-sentinel login` again.", error.message)
        assertNull(error.cause)
    }

    @Test
    fun `a blank hub_url or token is refused, so the token never goes to the default endpoint`() {
        for (content in listOf(
            """{"hub_url":"","token":"$TOKEN","expires_at_ms":${NOW + 1}}""",
            """{"hub_url":"  ","token":"$TOKEN","expires_at_ms":${NOW + 1}}""",
            """{"hub_url":"$HUB","token":" ","expires_at_ms":${NOW + 1}}""",
        )) {
            val path = writeOwnerOnly(content)

            val error = assertThrows(CredentialRefusedException::class.java) { CliTokenFile.read(path) }

            assertEquals("The token file $path is not valid: run `perf-sentinel login` again.", error.message)
        }
    }

    @Test
    fun `the token never shows in its string form`() {
        val token = CliTokenFile.read(tokenFile(HUB))!!

        assertFalse(token.toString(), TOKEN in token.toString())
        assertTrue(token.toString(), HUB in token.toString())
    }

    private fun tokenFile(hubUrl: String, expiresAtMs: Long = NOW + 3_600_000): Path =
        writeOwnerOnly("""{"hub_url":"$hubUrl","token":"$TOKEN","expires_at_ms":$expiresAtMs}""")

    private fun writeOwnerOnly(content: String): Path {
        val path = folder.root.toPath().resolve("hub-token.json")
        Files.writeString(path, content)
        if (posix) Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"))
        return path
    }

    private companion object {
        const val HUB = "https://hub.example.test"
        const val TOKEN = "pshub_t_7Qw2xL"
        const val NOW = 1_800_000_000_000L
    }
}
