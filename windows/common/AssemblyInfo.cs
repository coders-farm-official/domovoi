// Assembly attributes for both exes. The TargetFramework attribute matters:
// without it the runtime treats the exe as built for .NET 4.0 and HTTPS
// falls back to TLS 1.0 defaults; with it, TLS follows the OS (1.2/1.3).

using System.Reflection;
using System.Runtime.Versioning;

[assembly: TargetFramework(".NETFramework,Version=v4.8.1", FrameworkDisplayName = ".NET Framework 4.8.1")]
[assembly: AssemblyCompany("Coders Farm")]
[assembly: AssemblyProduct("Domovoi")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Coders Farm. MIT License.")]
[assembly: AssemblyVersion("0.1.0.0")]
[assembly: AssemblyFileVersion("0.1.0.0")]
[assembly: AssemblyInformationalVersion("0.1.0-test")]
