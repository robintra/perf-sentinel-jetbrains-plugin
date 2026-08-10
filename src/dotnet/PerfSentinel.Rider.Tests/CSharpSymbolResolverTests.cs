using System.IO;
using JetBrains.Lifetimes;
using JetBrains.ProjectModel;
using JetBrains.ReSharper.TestFramework;
using NUnit.Framework;

namespace PerfSentinel.Rider.Tests;

[TestFixture]
[TestFileExtension(".cs")]
public class CSharpSymbolResolverTests : BaseTestWithSingleProject
{
    private string _namespaceName = "";
    private string _functionName = "";
    private string? _expectedText;

    protected override string RelativeTestDataPath => "";

    [TestCase("PerfSentinel.RiderTests.Symbols", "SlowPath", "SlowPath")]
    [TestCase("PerfSentinel.RiderTests.Symbols", "PerfSentinel.RiderTests.Symbols.SlowPath", "SlowPath")]
    [TestCase("PerfSentinel.RiderTests.Symbols", ".ctor", "Symbols")]
    [TestCase("PerfSentinel.RiderTests.Symbols", ".cctor", "Symbols")]
    [TestCase("PerfSentinel.RiderTests.Symbols", "Count", "Count")]
    [TestCase("PerfSentinel.RiderTests.Symbols", "get_Count", "get")]
    [TestCase("PerfSentinel.RiderTests.Symbols", "set_Count", "set")]
    [TestCase("PerfSentinel.RiderTests.Symbols", "<Outer>g__LocalWork|0_0", "LocalWork")]
    public void ResolvesUniqueDeclaration(string namespaceName, string functionName, string expectedText)
    {
        _namespaceName = namespaceName;
        _functionName = functionName;
        _expectedText = expectedText;
        DoTestSolution("Symbols.cs");
    }

    [TestCase("PerfSentinel.RiderTests.Overloaded", "Run")]
    [TestCase("PerfSentinel.RiderTests.Missing", "Run")]
    [TestCase("System.String", "ToString")]
    public void RejectsAmbiguousMissingAndExternalSymbols(string namespaceName, string functionName)
    {
        _namespaceName = namespaceName;
        _functionName = functionName;
        _expectedText = null;
        DoTestSolution("Symbols.cs");
    }

    protected override void DoTest(Lifetime lifetime, IProject project)
    {
        var anchor = CSharpSymbolResolver.Resolve(Solution, _namespaceName, _functionName);
        if (_expectedText == null)
        {
            Assert.That(anchor, Is.Null);
            return;
        }

        Assert.That(anchor, Is.Not.Null);
        Assert.That(anchor!.Path, Does.EndWith("Symbols.cs"));
        var source = File.ReadAllText(anchor.Path);
        Assert.That(anchor.Offset, Is.InRange(0, source.Length - _expectedText.Length));
        Assert.That(source.Substring(anchor.Offset, _expectedText.Length), Is.EqualTo(_expectedText));
    }
}
