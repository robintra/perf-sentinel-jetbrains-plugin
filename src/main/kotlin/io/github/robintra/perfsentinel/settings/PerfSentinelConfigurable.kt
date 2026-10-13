package io.github.robintra.perfsentinel.settings

import com.intellij.openapi.components.service
import com.intellij.openapi.options.Configurable
import com.intellij.openapi.options.ConfigurationException
import com.intellij.openapi.project.Project
import com.intellij.platform.ide.progress.runWithModalProgressBlocking
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBPasswordField
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.components.JBTextField
import com.intellij.util.ui.FormBuilder
import io.github.robintra.perfsentinel.PerfSentinelBundle
import io.github.robintra.perfsentinel.core.credentialRefusal
import io.github.robintra.perfsentinel.core.normalizeEndpoints
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.awt.BorderLayout
import javax.swing.JComponent
import javax.swing.JPanel

class PerfSentinelConfigurable(private val project: Project) : Configurable {
    private var panel: JPanel? = null
    private var endpointsField: JBTextArea? = null
    private var serviceField: JBTextField? = null
    private val credentialsPanel = JPanel(BorderLayout())

    // One field per saved endpoint, against the secret the password safe held when it was built.
    private var storedSecrets: Map<String, String?> = emptyMap()
    private val secretFields = mutableMapOf<String, JBPasswordField>()

    override fun getDisplayName(): String = PerfSentinelBundle.message("settings.title")

    override fun createComponent(): JComponent {
        endpointsField = JBTextArea(5, 48).apply {
            lineWrap = false
            accessibleContext.accessibleName = PerfSentinelBundle.message("settings.endpoints")
        }
        serviceField = JBTextField().apply {
            emptyText.text = PerfSentinelBundle.message("settings.service.placeholder")
            accessibleContext.accessibleName = PerfSentinelBundle.message("settings.service")
        }
        panel = FormBuilder.createFormBuilder()
            .addLabeledComponent(
                JBLabel(PerfSentinelBundle.message("settings.endpoints")),
                JBScrollPane(endpointsField),
            )
            .addComponent(JBLabel(PerfSentinelBundle.message("settings.endpoints.help")))
            .addLabeledComponent(JBLabel(PerfSentinelBundle.message("settings.service")), serviceField!!)
            .addLabeledComponent(JBLabel(PerfSentinelBundle.message("settings.credentials")), credentialsPanel, true)
            .addComponent(JBLabel(PerfSentinelBundle.message("settings.credentials.help")))
            .addComponentFillVertically(JPanel(), 0)
            .panel
        reset()
        return panel!!
    }

    override fun isModified(): Boolean {
        val current = project.service<PerfSentinelSettings>().snapshot()
        // Normalized against normalized: apply() stores the normalized form, so comparing raw lines
        // leaves the dialog dirty forever after a trailing newline or any other rewritten input.
        // Unparseable text counts as modified — apply() is where the user gets the error.
        val edited = runCatching { normalizeEndpoints(endpointLines()) }.getOrNull()
        return edited != current.endpoints || serviceField?.text.orEmpty().trim() != current.serviceOverride ||
            editedSecrets().isNotEmpty()
    }

    override fun apply() {
        val endpoints = try {
            normalizeEndpoints(endpointLines())
        } catch (error: IllegalArgumentException) {
            throw ConfigurationException(error.message ?: PerfSentinelBundle.message("settings.invalid.endpoint"))
        }
        val secrets = editedSecrets()
        // Before anything is saved: a secret must not vanish with its endpoint, nor sit in the safe unsendable.
        secrets.filterValues(String::isNotEmpty).forEach { (endpoint, secret) ->
            val problem = if (endpoint in endpoints) {
                credentialRefusal(endpoint, secret)
            } else {
                PerfSentinelBundle.message("settings.credentials.unlisted")
            }
            if (problem != null) {
                throw ConfigurationException(PerfSentinelBundle.message("settings.credentials.refused", endpoint, problem))
            }
        }
        project.service<PerfSentinelSettings>().update(endpoints, serviceField?.text.orEmpty())
        syncCredentials(endpoints, secrets)
    }

    override fun reset() {
        val current = project.service<PerfSentinelSettings>().snapshot()
        endpointsField?.text = current.endpoints.joinToString("\n")
        serviceField?.text = current.serviceOverride
        syncCredentials(current.endpoints, emptyMap())
    }

    override fun disposeUIResources() {
        panel = null
        endpointsField = null
        serviceField = null
        storedSecrets = emptyMap()
        secretFields.clear()
        credentialsPanel.removeAll()
    }

    private fun endpointLines(): List<String> = endpointsField?.text.orEmpty().lineSequence().toList()

    // An emptied field is an edit too: it clears the stored secret.
    private fun editedSecrets(): Map<String, String> = secretFields
        .mapValues { (_, field) -> String(field.password).trim() }
        .filter { (endpoint, secret) -> secret != storedSecrets[endpoint].orEmpty() }

    // Writes the edited secrets, then rebuilds one field per endpoint from what the safe now holds.
    // The safe may unlock a keychain or a KeePass file, which the EDT must not wait on.
    private fun syncCredentials(endpoints: List<String>, writes: Map<String, String>) {
        val title = PerfSentinelBundle.message("settings.credentials.progress")
        storedSecrets = runWithModalProgressBlocking(project, title) {
            withContext(Dispatchers.IO) {
                writes.forEach { (endpoint, secret) -> EndpointCredentials.set(endpoint, secret) }
                endpoints.associateWith(EndpointCredentials::get)
            }
        }
        secretFields.clear()
        val form = FormBuilder.createFormBuilder()
        storedSecrets.forEach { (endpoint, secret) ->
            val field = JBPasswordField().apply { text = secret.orEmpty() }
            secretFields[endpoint] = field
            form.addLabeledComponent(JBLabel(endpoint), field)
        }
        credentialsPanel.removeAll()
        credentialsPanel.add(form.panel)
        credentialsPanel.revalidate()
        credentialsPanel.repaint()
    }
}
