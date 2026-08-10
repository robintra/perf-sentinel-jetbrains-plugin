package io.github.robintra.perfsentinel.settings

import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage
import com.intellij.openapi.components.StoragePathMacros
import com.intellij.util.xmlb.XmlSerializerUtil
import io.github.robintra.perfsentinel.core.DEFAULT_ENDPOINT
import io.github.robintra.perfsentinel.core.normalizeEndpoints

@Service(Service.Level.PROJECT)
@State(name = "PerfSentinelSettings", storages = [Storage(StoragePathMacros.WORKSPACE_FILE)])
class PerfSentinelSettings : PersistentStateComponent<PerfSentinelSettings.SettingsState> {
    private var settings = SettingsState()

    override fun getState(): SettingsState = settings

    override fun loadState(state: SettingsState) {
        XmlSerializerUtil.copyBean(state, settings)
    }

    // Persisted state is never re-validated as a unit: one bad entry in the workspace file would
    // otherwise throw out of both refresh() and the settings page that is the only way to fix it.
    // Invalid entries are dropped; an empty result falls back to the default endpoint.
    fun snapshot(): Snapshot =
        Snapshot(normalizeEndpoints(settings.endpoints.filter(::isValid)), settings.serviceOverride.trim())

    private fun isValid(endpoint: String): Boolean = runCatching { normalizeEndpoints(listOf(endpoint)) }.isSuccess

    fun update(endpoints: List<String>, serviceOverride: String) {
        settings.endpoints = normalizeEndpoints(endpoints).toMutableList()
        settings.serviceOverride = serviceOverride.trim()
    }

    class SettingsState {
        var endpoints: MutableList<String> = mutableListOf(DEFAULT_ENDPOINT)
        var serviceOverride: String = ""
    }

    data class Snapshot(val endpoints: List<String>, val serviceOverride: String)
}
