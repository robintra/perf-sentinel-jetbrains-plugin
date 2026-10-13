package io.github.robintra.perfsentinel.settings

import com.intellij.credentialStore.CredentialAttributes
import com.intellij.ide.passwordSafe.PasswordSafe

/**
 * One optional secret per normalized endpoint, a Hub machine token or a daemon read key. It lives in
 * the IDE password safe, never in [PerfSentinelSettings], so the workspace file cannot carry it.
 *
 * Call off the EDT and outside a read action: the safe may unlock a keychain or a KeePass file.
 */
object EndpointCredentials {
    private fun attributes(endpoint: String) = CredentialAttributes("PerfSentinel", endpoint)

    fun get(endpoint: String): String? = PasswordSafe.instance.getPassword(attributes(endpoint))?.ifEmpty { null }

    fun set(endpoint: String, secret: String?) {
        PasswordSafe.instance.setPassword(attributes(endpoint), secret?.ifEmpty { null })
    }
}
