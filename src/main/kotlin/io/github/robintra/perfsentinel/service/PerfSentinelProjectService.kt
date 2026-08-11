package io.github.robintra.perfsentinel.service

import com.intellij.openapi.application.EDT
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import com.intellij.util.messages.Topic
import io.github.robintra.perfsentinel.core.DaemonClient
import io.github.robintra.perfsentinel.core.EndpointSnapshot
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.core.RefreshState
import io.github.robintra.perfsentinel.core.forService
import io.github.robintra.perfsentinel.core.resolveServiceName
import io.github.robintra.perfsentinel.core.updated
import io.github.robintra.perfsentinel.settings.PerfSentinelSettings
import io.github.robintra.perfsentinel.navigation.AnchorNavigator
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

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
                settings.endpoints
                    .map { endpoint -> async { endpoint to runCatching { client.fetch(endpoint, service) } } }
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

    private suspend fun publish(newState: RefreshState) = withContext(Dispatchers.EDT) {
        state = newState
        project.messageBus.syncPublisher(FINDINGS_TOPIC).stateChanged(newState)
    }
}
