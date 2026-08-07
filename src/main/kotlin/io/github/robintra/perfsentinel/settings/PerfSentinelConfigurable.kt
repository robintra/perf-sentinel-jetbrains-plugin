package io.github.robintra.perfsentinel.settings

import com.intellij.openapi.components.service
import com.intellij.openapi.options.Configurable
import com.intellij.openapi.options.ConfigurationException
import com.intellij.openapi.project.Project
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.components.JBTextField
import com.intellij.util.ui.FormBuilder
import io.github.robintra.perfsentinel.PerfSentinelBundle
import io.github.robintra.perfsentinel.core.normalizeEndpoints
import javax.swing.JComponent
import javax.swing.JPanel

class PerfSentinelConfigurable(private val project: Project) : Configurable {
    private var panel: JPanel? = null
    private var endpointsField: JBTextArea? = null
    private var serviceField: JBTextField? = null

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
            .addComponentFillVertically(JPanel(), 0)
            .panel
        reset()
        return panel!!
    }

    override fun isModified(): Boolean {
        val current = project.service<PerfSentinelSettings>().snapshot()
        return endpointLines() != current.endpoints || serviceField?.text.orEmpty().trim() != current.serviceOverride
    }

    override fun apply() {
        val endpoints = try {
            normalizeEndpoints(endpointLines())
        } catch (error: IllegalArgumentException) {
            throw ConfigurationException(error.message ?: PerfSentinelBundle.message("settings.invalid.endpoint"))
        }
        project.service<PerfSentinelSettings>().update(endpoints, serviceField?.text.orEmpty())
    }

    override fun reset() {
        val current = project.service<PerfSentinelSettings>().snapshot()
        endpointsField?.text = current.endpoints.joinToString("\n")
        serviceField?.text = current.serviceOverride
    }

    override fun disposeUIResources() {
        panel = null
        endpointsField = null
        serviceField = null
    }

    private fun endpointLines(): List<String> = endpointsField?.text.orEmpty().lineSequence().toList()
}
