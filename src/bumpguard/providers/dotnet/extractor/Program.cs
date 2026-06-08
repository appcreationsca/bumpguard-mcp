using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace BumpGuard.Extractor;

/// <summary>
/// Reads a NuGet assembly's PUBLIC API surface using reflection-only metadata
/// loading (System.Reflection.MetadataLoadContext). No assembly code is ever
/// executed. Emits the surface as JSON on stdout. Diagnostics go to stderr.
/// </summary>
internal static class Program
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private static int Main(string[] args)
    {
        try
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine("usage: <surface|usage> <path> [resolverDir...]");
                return 2;
            }

            return args[0] switch
            {
                "surface" => RunSurface(args[1], args.Skip(2).ToArray()),
                "usage" => UsageScanner.Run(args[1]),
                _ => Fail("unknown command: " + args[0]),
            };
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            Console.WriteLine(JsonSerializer.Serialize(new { error = ex.Message }, JsonOpts));
            return 1;
        }
    }

    private static int Fail(string msg)
    {
        Console.Error.WriteLine(msg);
        Console.WriteLine(JsonSerializer.Serialize(new { error = msg }, JsonOpts));
        return 2;
    }

    private static int RunSurface(string targetDir, string[] resolverDirs)
    {
        if (!Directory.Exists(targetDir))
            return Fail("target directory not found: " + targetDir);

        // Target assemblies = the DLLs directly inside the target dir.
        var targetDlls = Directory
            .GetFiles(targetDir, "*.dll", SearchOption.TopDirectoryOnly)
            .Where(p => !IsResourceAssembly(p))
            .ToList();
        if (targetDlls.Count == 0)
            return Fail("no managed assemblies found in: " + targetDir);

        // Resolver paths: the runtime's reference assemblies + the package's own
        // DLLs + any extra dependency dirs. MetadataLoadContext needs these to
        // resolve base/parameter/return types referenced by the public API.
        var resolverPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var p in Directory.GetFiles(RuntimeEnvironment.GetRuntimeDirectory(), "*.dll"))
            resolverPaths.Add(p);
        foreach (var p in targetDlls)
            resolverPaths.Add(p);
        foreach (var dir in resolverDirs.Where(Directory.Exists))
            foreach (var p in Directory.GetFiles(dir, "*.dll", SearchOption.AllDirectories))
                resolverPaths.Add(p);

        var symbols = new List<SymbolDto>();
        bool partial = false;

        using var mlc = new MetadataLoadContext(new PathAssemblyResolver(resolverPaths));
        foreach (var dll in targetDlls)
        {
            Assembly asm;
            try { asm = mlc.LoadFromAssemblyPath(dll); }
            catch (Exception ex) { Console.Error.WriteLine($"skip {dll}: {ex.Message}"); partial = true; continue; }

            Type[] types;
            try { types = asm.GetTypes(); }
            catch (ReflectionTypeLoadException ex) { types = ex.Types.Where(t => t != null).ToArray(); partial = true; }
            catch (Exception ex) { Console.Error.WriteLine($"types {dll}: {ex.Message}"); partial = true; continue; }

            foreach (var type in types)
            {
                if (type == null) continue;
                try
                {
                    if (!IsPublicApiType(type)) continue;
                    ExtractType(type, symbols);
                }
                catch (Exception ex) { Console.Error.WriteLine($"type {type}: {ex.Message}"); partial = true; }
            }
        }

        Console.WriteLine(JsonSerializer.Serialize(new SurfaceDto { Partial = partial, Symbols = symbols }, JsonOpts));
        return 0;
    }

    private static bool IsResourceAssembly(string path) =>
        path.EndsWith(".resources.dll", StringComparison.OrdinalIgnoreCase);

    private static bool IsPublicApiType(Type t)
    {
        // Public top-level types and public nested types form the surface.
        if (t.IsNested) return t.IsNestedPublic || t.IsNestedFamily;
        return t.IsPublic;
    }

    private static void ExtractType(Type type, List<SymbolDto> symbols)
    {
        var typePath = CleanName(type);

        // Constructors make the type "callable". A single public ctor is
        // diff-able; multiple ctors are marked overloaded (presence only).
        var ctors = SafeGet(() => type.GetConstructors(BindingFlags.Public | BindingFlags.Instance))
            ?? Array.Empty<ConstructorInfo>();
        var publicCtors = ctors.Where(c => c.IsPublic).ToList();
        var typeSym = new SymbolDto { Path = typePath, Kind = "class" };
        if (publicCtors.Count == 1)
            FillParams(typeSym, publicCtors[0].GetParameters());
        else if (publicCtors.Count > 1)
            typeSym.Overloaded = true;
        symbols.Add(typeSym);

        // Methods (excluding property/event/operator accessors), grouped by name
        // so overloads can be marked.
        var methods = SafeGet(() => type.GetMethods(
            BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
            ?? Array.Empty<MethodInfo>();
        foreach (var grp in methods
                     .Where(m => (m.IsPublic || m.IsFamily) && !m.IsSpecialName)
                     .GroupBy(m => m.Name))
        {
            var sym = new SymbolDto { Path = typePath + "." + grp.Key, Kind = "method" };
            var overloads = grp.ToList();
            if (overloads.Count == 1)
                FillParams(sym, overloads[0].GetParameters());
            else
                sym.Overloaded = true;
            symbols.Add(sym);
        }

        foreach (var prop in SafeGet(() => type.GetProperties(
                     BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
                     ?? Array.Empty<PropertyInfo>())
            symbols.Add(new SymbolDto { Path = typePath + "." + prop.Name, Kind = "attribute" });

        foreach (var field in SafeGet(() => type.GetFields(
                     BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
                     ?? Array.Empty<FieldInfo>())
            if (field.IsPublic || field.IsFamily)
                symbols.Add(new SymbolDto { Path = typePath + "." + field.Name, Kind = "attribute" });

        foreach (var evt in SafeGet(() => type.GetEvents(
                     BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
                     ?? Array.Empty<EventInfo>())
            symbols.Add(new SymbolDto { Path = typePath + "." + evt.Name, Kind = "attribute" });
    }

    private static void FillParams(SymbolDto sym, ParameterInfo[] ps)
    {
        sym.Params = new List<ParamDto>();
        foreach (var p in ps)
        {
            bool isParams = p.GetCustomAttributesData()
                .Any(a => a.AttributeType.Name == "ParamArrayAttribute");
            if (isParams) { sym.AcceptsVarargs = true; continue; }
            sym.Params.Add(new ParamDto { Name = p.Name ?? "", HasDefault = p.HasDefaultValue });
        }
    }

    private static T SafeGet<T>(Func<T> f) where T : class
    {
        try { return f(); } catch { return null; }
    }

    private static readonly Regex Arity = new(@"`\d+", RegexOptions.Compiled);

    /// <summary>Normalise a metadata type name to how C# source references it:
    /// nested `Outer+Inner` -> `Outer.Inner`, generic `List`1` -> `List`.</summary>
    private static string CleanName(Type t)
    {
        var full = t.FullName ?? ((t.Namespace is { Length: > 0 } ns) ? ns + "." + t.Name : t.Name);
        full = full.Replace('+', '.');
        full = Arity.Replace(full, "");
        return full;
    }
}

internal sealed class SurfaceDto
{
    public bool Partial { get; set; }
    public List<SymbolDto> Symbols { get; set; } = new();
}

internal sealed class SymbolDto
{
    public string Path { get; set; }
    public string Kind { get; set; }
    public bool Overloaded { get; set; }
    public bool AcceptsVarargs { get; set; }
    public List<ParamDto> Params { get; set; }
}

internal sealed class ParamDto
{
    public string Name { get; set; }
    public bool HasDefault { get; set; }
}
