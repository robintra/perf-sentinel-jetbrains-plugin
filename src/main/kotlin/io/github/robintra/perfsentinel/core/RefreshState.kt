package io.github.robintra.perfsentinel.core

data class EndpointSnapshot(
    val endpoint: String,
    val findings: List<FindingResponse> = emptyList(),
    val lastSuccessAtMillis: Long? = null,
    val error: String? = null,
    val service: String? = null,
)

fun EndpointSnapshot.forService(service: String): EndpointSnapshot =
    if (this.service == service) this else EndpointSnapshot(endpoint = endpoint, service = service)

fun EndpointSnapshot.updated(result: Result<List<FindingResponse>>, nowMillis: Long): EndpointSnapshot =
    result.fold(
        onSuccess = { copy(findings = it, lastSuccessAtMillis = nowMillis, error = null) },
        onFailure = { copy(error = it.message ?: it.javaClass.simpleName) },
    )

data class RefreshState(
    val refreshing: Boolean = false,
    val endpoints: List<EndpointSnapshot> = emptyList(),
) {
    val findings: List<FindingResponse> get() = endpoints.flatMap(EndpointSnapshot::findings)
}
