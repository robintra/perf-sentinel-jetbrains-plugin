package io.github.robintra.perfsentinel.settings

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.service
import com.intellij.openapi.options.ConfigurationException
import com.intellij.openapi.util.JDOMUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.ui.components.JBPasswordField
import com.intellij.ui.components.JBTextArea
import com.intellij.util.ui.UIUtil
import com.intellij.util.xmlb.XmlSerializer
import io.github.robintra.perfsentinel.core.DEFAULT_ENDPOINT
import org.junit.Assert
import java.util.concurrent.Callable
import javax.swing.JComponent

class PerfSentinelConfigurableTest : BasePlatformTestCase() {
    override fun tearDown() {
        try {
            offEdt { listOf(DEFAULT_ENDPOINT, HUB, PLAIN_HTTP).forEach { EndpointCredentials.set(it, null) } }
            project.service<PerfSentinelSettings>().update(listOf(DEFAULT_ENDPOINT), "")
        } finally {
            super.tearDown()
        }
    }

    fun testSetsAndClearsAnEndpointSecretInThePasswordSafeOnly() {
        val configurable = PerfSentinelConfigurable(project)
        try {
            val panel = configurable.createComponent()
            secretField(panel).text = SECRET
            assertTrue(configurable.isModified)

            configurable.apply()

            assertEquals(SECRET, offEdt { EndpointCredentials.get(DEFAULT_ENDPOINT) })
            assertFalse(configurable.isModified)
            val workspace = JDOMUtil.write(XmlSerializer.serialize(project.service<PerfSentinelSettings>().state))
            assertFalse(workspace, workspace.contains(SECRET))

            secretField(panel).text = ""
            configurable.apply()

            assertNull(offEdt { EndpointCredentials.get(DEFAULT_ENDPOINT) })
        } finally {
            configurable.disposeUIResources()
        }
    }

    fun testShowsTheStoredSecretMaskedAndUnmodified() {
        offEdt { EndpointCredentials.set(DEFAULT_ENDPOINT, SECRET) }
        val configurable = PerfSentinelConfigurable(project)
        try {
            val field = secretField(configurable.createComponent())

            assertEquals(SECRET, String(field.password))
            assertFalse(configurable.isModified)
        } finally {
            configurable.disposeUIResources()
        }
    }

    fun testRefusesASecretTypedForAnEndpointTheSameApplyRemoves() {
        val configurable = PerfSentinelConfigurable(project)
        try {
            val panel = configurable.createComponent()
            secretField(panel).text = SECRET
            endpointsField(panel).text = HUB

            val error = Assert.assertThrows(ConfigurationException::class.java) { configurable.apply() }

            assertEquals(
                "$DEFAULT_ENDPOINT: This change removes the endpoint. Apply the endpoint list first, then set its credential.",
                error.message,
            )
            assertNull(offEdt { EndpointCredentials.get(DEFAULT_ENDPOINT) })
            assertNull(offEdt { EndpointCredentials.get(HUB) })
            assertEquals(listOf(DEFAULT_ENDPOINT), project.service<PerfSentinelSettings>().snapshot().endpoints)
        } finally {
            configurable.disposeUIResources()
        }
    }

    fun testClearsTheSecretOfAnEndpointEmptiedInTheApplyThatRemovesIt() {
        offEdt { EndpointCredentials.set(DEFAULT_ENDPOINT, SECRET) }
        val configurable = PerfSentinelConfigurable(project)
        try {
            val panel = configurable.createComponent()
            secretField(panel).text = ""
            endpointsField(panel).text = HUB

            configurable.apply()

            assertNull(offEdt { EndpointCredentials.get(DEFAULT_ENDPOINT) })
            assertEquals(listOf(HUB), project.service<PerfSentinelSettings>().snapshot().endpoints)
        } finally {
            configurable.disposeUIResources()
        }
    }

    fun testRefusesToStoreASecretTheClientWouldNeverSend() {
        project.service<PerfSentinelSettings>().update(listOf(DEFAULT_ENDPOINT, PLAIN_HTTP), "")
        val configurable = PerfSentinelConfigurable(project)
        try {
            val fields = UIUtil.findComponentsOfType(configurable.createComponent(), JBPasswordField::class.java)
            fields[1].text = SECRET

            val transport = Assert.assertThrows(ConfigurationException::class.java) { configurable.apply() }

            assertEquals("$PLAIN_HTTP: A credential is sent only over HTTPS or to this machine.", transport.message)

            fields[1].text = ""
            fields[0].text = "psh\tmachine"

            val control = Assert.assertThrows(ConfigurationException::class.java) { configurable.apply() }

            assertEquals(
                "$DEFAULT_ENDPOINT: The credential holds a control character, so it is not sent.",
                control.message,
            )
            assertNull(offEdt { EndpointCredentials.get(PLAIN_HTTP) })
            assertNull(offEdt { EndpointCredentials.get(DEFAULT_ENDPOINT) })
        } finally {
            configurable.disposeUIResources()
        }
    }

    private fun endpointsField(panel: JComponent): JBTextArea =
        checkNotNull(UIUtil.findComponentOfType(panel, JBTextArea::class.java)) { "no endpoints field" }

    private fun secretField(panel: JComponent): JBPasswordField =
        checkNotNull(UIUtil.findComponentOfType(panel, JBPasswordField::class.java)) { "no credential field" }

    // The password safe refuses slow work on the EDT, where these tests run.
    private fun <T> offEdt(action: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread(Callable { action() }).get()

    private companion object {
        const val SECRET = "psh_machine_4Lr8wQ"
        const val HUB = "https://hub.example.test"
        const val PLAIN_HTTP = "http://hub.example.test"
    }
}
