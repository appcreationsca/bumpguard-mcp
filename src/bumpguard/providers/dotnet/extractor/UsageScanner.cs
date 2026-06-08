using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace BumpGuard.Extractor;

/// <summary>
/// Parses C# source with Roslyn (syntax only — no compilation, no execution)
/// and emits the `using` directives, local variable types, and references
/// (object creations, invocations, member accesses). The Python side resolves
/// these to fully-qualified candidate symbols against a package surface.
/// </summary>
internal static class UsageScanner
{
    private static readonly JsonSerializerOptions Opts = new()
    {
        WriteIndented = false,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    public static int Run(string sourcePath)
    {
        string code = File.Exists(sourcePath) ? File.ReadAllText(sourcePath) : sourcePath;
        var root = CSharpSyntaxTree.ParseText(code).GetRoot();

        var usings = new List<UsingDto>();
        foreach (var u in root.DescendantNodes().OfType<UsingDirectiveSyntax>())
        {
            var name = u.Name?.ToString();
            if (name == null) continue;
            usings.Add(new UsingDto
            {
                Name = name,
                Alias = u.Alias?.Name.Identifier.Text,
                IsStatic = u.StaticKeyword.IsKind(SyntaxKind.StaticKeyword),
                IsGlobal = u.GlobalKeyword.IsKind(SyntaxKind.GlobalKeyword),
            });
        }

        var locals = new List<LocalDto>();
        foreach (var decl in root.DescendantNodes().OfType<VariableDeclarationSyntax>())
        {
            string declaredType = decl.Type.IsVar ? null : StripGeneric(decl.Type);
            foreach (var v in decl.Variables)
            {
                var t = declaredType;
                if (t == null && v.Initializer?.Value is ObjectCreationExpressionSyntax oce)
                    t = StripGeneric(oce.Type);
                if (!string.IsNullOrEmpty(t))
                    locals.Add(new LocalDto { Var = v.Identifier.Text, Type = t });
            }
        }

        var refs = new List<RefDto>();
        foreach (var node in root.DescendantNodes())
        {
            switch (node)
            {
                case ObjectCreationExpressionSyntax oce:
                    refs.Add(MakeRef(StripGeneric(oce.Type), true, oce.ArgumentList, Line(oce)));
                    break;
                case InvocationExpressionSyntax inv:
                    var iname = ExprName(inv.Expression);
                    if (iname != null)
                        refs.Add(MakeRef(iname, true, inv.ArgumentList, Line(inv)));
                    break;
                case MemberAccessExpressionSyntax mae when mae.Parent is not InvocationExpressionSyntax:
                    var mname = ExprName(mae);
                    if (mname != null)
                        refs.Add(new RefDto { Name = mname, IsCall = false, Line = Line(mae) });
                    break;
            }
        }

        Console.WriteLine(JsonSerializer.Serialize(
            new UsageDto { Usings = usings, Locals = locals, Refs = refs }, Opts));
        return 0;
    }

    private static RefDto MakeRef(string name, bool isCall, BaseArgumentListSyntax argList, int line)
    {
        var args = argList?.Arguments ?? default;
        int positional = 0;
        var kwargs = new List<string>();
        if (argList != null)
            foreach (var a in args)
            {
                if (a.NameColon != null) kwargs.Add(a.NameColon.Name.Identifier.Text);
                else positional++;
            }
        return new RefDto
        {
            Name = name,
            IsCall = isCall,
            PositionalCount = positional,
            Kwargs = kwargs,
            Line = line,
        };
    }

    private static int Line(SyntaxNode n) => n.GetLocation().GetLineSpan().StartLinePosition.Line + 1;

    /// <summary>Dotted identifier path of an expression, generics stripped, or
    /// null if the root isn't a plain name (e.g. a literal or nested call).</summary>
    private static string ExprName(ExpressionSyntax expr)
    {
        switch (expr)
        {
            case IdentifierNameSyntax id:
                return id.Identifier.Text;
            case GenericNameSyntax g:
                return g.Identifier.Text;
            case MemberAccessExpressionSyntax mae:
                var left = ExprName(mae.Expression);
                if (left == null) return null;
                if (left is "this" or "base") left = null;
                var right = mae.Name.Identifier.Text;
                return left == null ? right : left + "." + right;
            default:
                return null;
        }
    }

    private static string StripGeneric(TypeSyntax t)
    {
        switch (t)
        {
            case IdentifierNameSyntax id:
                return id.Identifier.Text;
            case GenericNameSyntax g:
                return g.Identifier.Text;
            case QualifiedNameSyntax q:
                return StripGeneric(q.Left) + "." + StripGeneric(q.Right);
            case AliasQualifiedNameSyntax a:
                return StripGeneric(a.Name);
            case ArrayTypeSyntax arr:
                return StripGeneric(arr.ElementType);
            case NullableTypeSyntax nl:
                return StripGeneric(nl.ElementType);
            default:
                return t.ToString();
        }
    }
}

internal sealed class UsageDto
{
    public List<UsingDto> Usings { get; set; } = new();
    public List<LocalDto> Locals { get; set; } = new();
    public List<RefDto> Refs { get; set; } = new();
}

internal sealed class UsingDto
{
    public string Name { get; set; }
    public string Alias { get; set; }
    public bool IsStatic { get; set; }
    public bool IsGlobal { get; set; }
}

internal sealed class LocalDto
{
    public string Var { get; set; }
    public string Type { get; set; }
}

internal sealed class RefDto
{
    public string Name { get; set; }
    public bool IsCall { get; set; }
    public int PositionalCount { get; set; }
    public List<string> Kwargs { get; set; } = new();
    public int Line { get; set; }
}
