// jarvis-setup.exe: runs setup.ps1 (built into the exe) in this console window. Built by tools/make_setup.py with
// the C# compiler that comes with Windows, so nothing extra is needed to build it.
//   jarvis-setup.exe --check   only look, change nothing
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

class JarvisSetup
{
    static int Main(string[] args)
    {
        string script = Path.Combine(Path.GetTempPath(), "jarvis-setup.ps1");
        using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("setup.ps1"))
        using (FileStream f = File.Create(script))
            s.CopyTo(f);
        string extra = Array.IndexOf(args, "--check") >= 0 ? " -Check" : "";
        // -ExecutionPolicy Bypass applies to this one PowerShell only; Windows' own setting stays as it is
        var psi = new ProcessStartInfo("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"" + extra);
        psi.UseShellExecute = false;   // same window
        using (Process p = Process.Start(psi))
        {
            p.WaitForExit();
            return p.ExitCode;
        }
    }
}
