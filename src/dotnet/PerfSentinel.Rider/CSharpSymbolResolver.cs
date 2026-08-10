using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using JetBrains.Metadata.Reader.Impl;
using JetBrains.ProjectModel;
using JetBrains.ReSharper.Psi;
using JetBrains.ReSharper.Psi.Caches;
using JetBrains.ReSharper.Psi.CSharp.Tree;
using JetBrains.ReSharper.Psi.ExtensionsAPI.Tree;
using JetBrains.ReSharper.Psi.Modules;
using JetBrains.ReSharper.Psi.Tree;
using JetBrains.ReSharper.Resources.Shell;
using PerfSentinel.Rider.Model;

namespace PerfSentinel.Rider;

public static class CSharpSymbolResolver
{
    private static readonly Regex LocalFunctionPattern = new(
        @"^<(?<parent>[^>]+)>g__(?<name>[^|]+)\|",
        RegexOptions.CultureInvariant);

    public static SourceAnchor? Resolve(ISolution solution, string namespaceName, string functionName)
    {
        var symbol = SymbolName.Parse(namespaceName, functionName);
        if (symbol == null)
            return null;

        using (ReadLockCookie.Create())
        {
            var symbolCache = solution.GetComponent<ISymbolCache>();
            var anchors = new Dictionary<(string Path, int Offset), SourceAnchor>();
            foreach (var project in solution.GetAllProjects())
            foreach (var module in project.GetPsiModules())
            {
                var type = symbolCache
                    .GetSymbolScope(module, withReferences: false, caseSensitive: true)
                    .GetTypeElementByCLRName(new ClrTypeName(symbol.Owner));
                if (type == null)
                    continue;

                foreach (var typeDeclaration in type.GetDeclarations().OfType<IClassLikeDeclaration>())
                foreach (var declaration in typeDeclaration.Descendants<ICSharpDeclaration>())
                {
                    if (!Matches(declaration, symbol))
                        continue;

                    var anchor = ToAnchor(declaration);
                    if (anchor == null)
                        continue;

                    anchors[(anchor.Path, anchor.Offset)] = anchor;
                    if (anchors.Count > 1)
                        return null;
                }
            }

            return anchors.Count == 1 ? anchors.Values.Single() : null;
        }
    }

    private static bool Matches(ICSharpDeclaration declaration, SymbolName symbol) => symbol.Kind switch
    {
        SymbolKind.InstanceConstructor => declaration is IConstructorDeclaration { IsStatic: false },
        SymbolKind.StaticConstructor => declaration is IConstructorDeclaration { IsStatic: true },
        SymbolKind.Property => declaration is IPropertyDeclaration property &&
                               property.DeclaredName == symbol.Member,
        SymbolKind.Getter => declaration is IAccessorDeclaration { Kind: AccessorKind.GETTER } accessor &&
                             accessor.GetContainingNode<IPropertyDeclaration>()?.DeclaredName == symbol.Member,
        SymbolKind.Setter => declaration is IAccessorDeclaration { Kind: AccessorKind.SETTER } accessor &&
                             accessor.GetContainingNode<IPropertyDeclaration>()?.DeclaredName == symbol.Member,
        SymbolKind.LocalFunction => declaration is ILocalFunctionDeclaration local &&
                                    local.DeclaredName == symbol.Member &&
                                    local.GetContainingNode<IMethodDeclaration>()?.DeclaredName == symbol.Parent,
        _ => declaration is IMethodDeclaration method && method.DeclaredName == symbol.Member ||
             declaration is IPropertyDeclaration property && property.DeclaredName == symbol.Member,
    };

    private static SourceAnchor? ToAnchor(ICSharpDeclaration declaration)
    {
        var sourceFile = declaration.GetSourceFile();
        if (sourceFile?.ToProjectFile() == null)
            return null;

        var range = declaration.GetNameDocumentRange();
        return range.IsValid()
            ? new SourceAnchor(sourceFile.GetLocation().FullPath, range.TextRange.StartOffset)
            : null;
    }

    private enum SymbolKind
    {
        Member,
        InstanceConstructor,
        StaticConstructor,
        Property,
        Getter,
        Setter,
        LocalFunction,
    }

    private sealed class SymbolName
    {
        private SymbolName(string owner, string member, SymbolKind kind, string? parent = null)
        {
            Owner = owner;
            Member = member;
            Kind = kind;
            Parent = parent;
        }

        public string Owner { get; }
        public string Member { get; }
        public SymbolKind Kind { get; }
        public string? Parent { get; }

        public static SymbolName? Parse(string namespaceName, string functionName)
        {
            var owner = namespaceName.Trim().TrimEnd('.');
            var function = functionName.Trim();
            if (string.IsNullOrEmpty(owner) || string.IsNullOrEmpty(function))
                return null;

            var argumentStart = function.IndexOf('(');
            if (argumentStart >= 0)
                function = function.Substring(0, argumentStart).TrimEnd();

            var prefix = owner + ".";
            var member = function.StartsWith(prefix, StringComparison.Ordinal)
                ? function.Substring(prefix.Length)
                : function;

            if (member == ".ctor")
                return new SymbolName(owner, member, SymbolKind.InstanceConstructor);
            if (member == ".cctor")
                return new SymbolName(owner, member, SymbolKind.StaticConstructor);

            var local = LocalFunctionPattern.Match(member);
            if (local.Success)
                return new SymbolName(
                    owner,
                    local.Groups["name"].Value,
                    SymbolKind.LocalFunction,
                    local.Groups["parent"].Value);

            if (member.StartsWith("get_", StringComparison.Ordinal) && member.Length > 4)
                return new SymbolName(owner, member.Substring(4), SymbolKind.Getter);
            if (member.StartsWith("set_", StringComparison.Ordinal) && member.Length > 4)
                return new SymbolName(owner, member.Substring(4), SymbolKind.Setter);

            return new SymbolName(owner, member, SymbolKind.Member);
        }
    }
}
