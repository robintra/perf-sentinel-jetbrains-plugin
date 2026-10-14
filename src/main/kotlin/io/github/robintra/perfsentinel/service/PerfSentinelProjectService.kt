package io.github.robintra.perfsentinel.service

import com.intellij.openapi.application.EDT
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.SystemInfo
import com.intellij.util.EnvironmentUtil
import com.intellij.util.messages.Topic
import io.github.robintra.perfsentinel.core.*
import io.github.robintra.perfsentinel.navigation.AnchorNavigator
import io.github.robintra.perfsentinel.settings.EndpointCredentials
import io.github.robintra.perfsentinel.settings.PerfSentinelSettings
import kotlinx.coroutines.*
import org.jetbrains.annotations.VisibleForTesting
import java.io.IOException
import java.nio.file.Path

private val LOG = logger<PerfSentinelProjectService>()

fun interface FindingsListener {
    fun stateChanged(state: RefreshState)
}

val FINDINGS_TOPIC: Topic<FindingsListener> = Topic.create("Perf Sentinel findings", FindingsListener::class.java)

@Service(Service.Level.PROJECT)
class PerfSentinelProjectService(
    private val project: Project,
    private val coroutineScope: CoroutineScope,
) {
    @Volatile
    var state: RefreshState = RefreshState()
        private set

    private val client = DaemonClient()

    // Where `perf-sentinel login` writes its token; a test points it elsewhere. EnvironmentUtil gives the
    // login-shell environment on macOS, so an XDG_CONFIG_HOME set in a shell rc file is seen from a Dock launch.
    // It can wait for that environment to load, hence resolved at refresh, off the EDT.
    @VisibleForTesting
    internal var cliTokenPath: () -> Path? = { CliTokenFile.locate(EnvironmentUtil::getValue, SystemInfo.isWindows) }

    private var refreshJob: Job? = null

    fun refresh() {
        refreshJob?.cancel()
        refreshJob = coroutineScope.launch {
            val settings = project.service<PerfSentinelSettings>().snapshot()
            val service = resolveServiceName(project.name, project.basePath, settings.serviceOverride)
            val previous = state.endpoints.associateBy(EndpointSnapshot::endpoint)
            val current = settings.endpoints.associateWith { endpoint ->
                previous[endpoint]?.forService(service) ?: EndpointSnapshot(endpoint, service = service)
            }
            publish(
                RefreshState(
                    refreshing = true,
                    endpoints = current.values.toList(),
                ),
            )

            // Concurrently: sequential fetches make refresh latency the sum of every endpoint's
            // timeout, so three unreachable daemons freeze the tool window for three timeouts.
            val results = withContext(Dispatchers.IO) {
                // Read at every refresh, so a new `perf-sentinel login` applies without restarting the IDE.
                val cliToken = readCliToken()
                val startedAt = System.currentTimeMillis()
                settings.endpoints
                    .map { endpoint ->
                        async {
                            endpoint to runCatching {
                                // The endpoint's own credential wins: it was set for this endpoint on purpose.
                                val secret = EndpointCredentials.get(endpoint)
                                val login = if (secret == null) cliToken?.forEndpoint(endpoint, startedAt) else null
                                client.fetch(endpoint, service, secret ?: login, loginHint = secret == null)
                            }
                        }
                    }
                    .awaitAll()
            }
            val now = System.currentTimeMillis()
            publish(
                RefreshState(
                    endpoints = results.map { (endpoint, result) ->
                        current.getValue(endpoint).updated(result, now)
                    },
                ),
            )
        }
    }

    fun navigate(finding: Finding) {
        coroutineScope.launch {
            // A resolver that throws must remain a silent no-op, not an IDE internal-error balloon.
            // Cancellation is rethrown, so closing the project still tears the coroutine down.
            val target = try {
                AnchorNavigator.resolve(project, finding)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: Exception) {
                null
            } ?: return@launch
            withContext(Dispatchers.EDT) {
                if (target.canNavigate()) target.navigate(true)
            }
        }
    }

    // A file no endpoint can be matched against, a link or one that does not parse, is logged and skipped.
    private fun readCliToken(): CliToken? = runCatching(cliTokenPath).getOrNull()?.let { path ->
        try {
            CliTokenFile.read(path)
        } catch (error: IOException) {
            LOG.warn(error.message)
            null
        }
    }

    private suspend fun publish(newState: RefreshState) = withContext(Dispatchers.EDT) {
        state = newState
        project.messageBus.syncPublisher(FINDINGS_TOPIC).stateChanged(newState)
    }
}
