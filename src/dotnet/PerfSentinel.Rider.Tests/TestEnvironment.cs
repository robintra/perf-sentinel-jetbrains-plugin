using System.Threading;
using JetBrains.Application.BuildScript.Application.Zones;
using JetBrains.ReSharper.Feature.Services;
using JetBrains.ReSharper.Psi.CSharp;
using JetBrains.ReSharper.TestFramework;
using JetBrains.TestFramework;
using JetBrains.TestFramework.Application.Zones;
using NUnit.Framework;

[assembly: Apartment(ApartmentState.STA)]

namespace PerfSentinel.Rider.Tests;

[ZoneDefinition]
public class PerfSentinelTestEnvironmentZone : ITestsEnvZone, IRequire<PsiFeatureTestZone>,
    IRequire<IPerfSentinelZone>;

[ZoneMarker]
public class ZoneMarker : IRequire<ICodeEditingZone>, IRequire<ILanguageCSharpZone>,
    IRequire<PerfSentinelTestEnvironmentZone>;

[SetUpFixture]
public class TestEnvironment : ExtensionTestEnvironmentAssembly<PerfSentinelTestEnvironmentZone>;
