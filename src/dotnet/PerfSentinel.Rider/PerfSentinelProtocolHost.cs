using System;
using JetBrains.Application.Parts;
using JetBrains.ProjectModel;
using JetBrains.Rd.Tasks;
using JetBrains.ReSharper.Feature.Services.Protocol;
using PerfSentinel.Rider.Model;

namespace PerfSentinel.Rider;

[SolutionComponent(Instantiation.ContainerAsyncAnyThreadSafe)]
public sealed class PerfSentinelProtocolHost
{
    public PerfSentinelProtocolHost(ISolution solution)
    {
        // GetProtocolSolution is annotated non-null, HasProtocolSolution is the API's own guard.
        if (!solution.HasProtocolSolution())
            return;

        var protocolSolution = solution.GetProtocolSolution();
        protocolSolution.GetPerfSentinelModel().ResolveCSharpSymbol.SetAsync((lifetime, request) =>
        {
            if (!lifetime.IsAlive)
                return RdTask.Cancelled<SourceAnchor>();

            try
            {
                return RdTask.Successful(CSharpSymbolResolver.Resolve(
                    solution,
                    request.Namespace,
                    request.Function)!);
            }
            catch (OperationCanceledException)
            {
                return RdTask.Cancelled<SourceAnchor>();
            }
            catch (Exception)
            {
                return RdTask.Successful<SourceAnchor>(null!);
            }
        });
    }
}
