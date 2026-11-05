// jarvis-setup.exe: the installer window. It runs setup.ps1 (built into the exe) hidden, with -Gui, and turns its
// "@@kind|text" lines into the step list. Built by tools/make_setup.py with the C# compiler that comes with Windows
// (.NET Framework 4, so C# 5: no $"" strings, no ?.), so nothing extra is needed to build it.
//   jarvis-setup.exe --check            only look, change nothing
//   jarvis-setup.exe --folder D:\Jarvis  install there (otherwise it picks a folder, which you can change)
//   jarvis-setup.exe --go                start right away (for testing)
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SetupForm(args));
    }
}

class Hit { public string Id; public RectangleF R; public Action Click; }
class StepInfo { public string Title; public string Sub = ""; public int State; }

class SetupForm : Form
{
    const int PENDING = 0, RUNNING = 1, DONE = 2, WAITING = 3, FAILED = 4;
    static Color C(string hex) { return ColorTranslator.FromHtml("#" + hex); }
    static readonly Color Bg = C("0A101C"), Card = C("121A2B"), Edge = C("1E2A40"), Hi = C("17223A"), HoverCard = C("1A2640"),
        Ink = C("EAF2FF"), Sub = C("8FA0BA"), Dim = C("56647D"), Accent = C("5FE3FF"), AccentHover = C("8BEBFF"),
        OnAccent = C("03141C"), Green = C("4ADE80"), Red = C("F87171"), Amber = C("FBBF24"), Off = C("2A3550");
    static readonly string[] Titles = { "Git", "Python 3.13", "Claude Code", "Download Jarvis", "Python packages",
        "Tunnel for your phone", "Settings", "Log in to Claude", "Extras", "Start Jarvis" };
    // the lines worth showing under a step while it works (pip, git)
    static readonly Regex Live = new Regex(@"^\s*(Collecting|Downloading|Installing collected|Building|Successfully installed|" +
        @"Requirement already satisfied|Receiving objects|Resolving deltas|Cloning|Updating|Fast-forward|Already up to date)",
        RegexOptions.IgnoreCase);

    float S = 1;
    string page = "welcome", folder, hover, failText;
    bool updating, folderBad, check, nvidia, finished, exited, cancelled, showLog;
    bool optKeys = true, optStartup = true, optShortcut = true;
    double freeGb, needGb;
    int cur;
    float spin;
    readonly StepInfo[] steps = new StepInfo[10];
    readonly List<Hit> hits = new List<Hit>();
    Process proc;
    readonly TextBox log;
    readonly Timer anim;
    readonly Image logo;
    readonly Font fTitle, fHead, fBody, fBold, fSmall;

    public SetupForm(string[] args)
    {
        check = Array.IndexOf(args, "--check") >= 0;
        int fi = Array.IndexOf(args, "--folder");
        using (Graphics g = CreateGraphics()) S = g.DpiX / 96f;
        Text = "Jarvis setup";
        BackColor = Bg; ForeColor = Ink;
        FormBorderStyle = FormBorderStyle.FixedSingle; MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.None;
        ClientSize = new Size(D(520), D(700));
        KeyPreview = true;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, true);
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        Stream ls = Assembly.GetExecutingAssembly().GetManifestResourceStream("logo.png");
        if (ls != null) logo = Image.FromStream(ls);
        fTitle = new Font("Segoe UI Semibold", 26 * S, GraphicsUnit.Pixel);
        fHead = new Font("Segoe UI Semibold", 21 * S, GraphicsUnit.Pixel);
        fBody = new Font("Segoe UI", 14 * S, GraphicsUnit.Pixel);
        fBold = new Font("Segoe UI Semibold", 14 * S, GraphicsUnit.Pixel);
        fSmall = new Font("Segoe UI", 13 * S, GraphicsUnit.Pixel);
        for (int i = 0; i < 10; i++) steps[i] = new StepInfo { Title = Titles[i] };

        log = new TextBox();
        log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.Vertical; log.WordWrap = true;
        log.BorderStyle = BorderStyle.None; log.BackColor = Card; log.ForeColor = Sub;
        log.Font = new Font("Consolas", 12 * S, GraphicsUnit.Pixel);
        log.Bounds = Rectangle.Round(R(48, 126, 424, 404));
        log.Visible = false;
        Controls.Add(log);

        anim = new Timer();
        anim.Interval = 30;
        anim.Tick += delegate { if (page == "run" && !exited) { spin += 0.12f; Invalidate(); } };
        anim.Start();

        nvidia = HasNvidia();
        SetFolder(fi >= 0 && fi + 1 < args.Length ? args[fi + 1] : DefaultFolder());
        if (Array.IndexOf(args, "--go") >= 0) Shown += delegate { StartInstall(); };
        int pv = Array.IndexOf(args, "--preview");   // the last page or a failure, without running anything (for testing)
        if (pv >= 0 && pv + 1 < args.Length)
        {
            if (args[pv + 1] == "done") page = "done";
            else
            {
                page = "run"; exited = true; cur = 5;
                for (int i = 0; i < 4; i++) { steps[i].State = DONE; steps[i].Sub = "Already installed"; }
                failText = "Installing the packages failed. Details shows why.";
                steps[4].State = FAILED; steps[4].Sub = failText;
            }
        }
    }

    // ------------------------------------------------------------------ where Jarvis goes

    static double Free(string path)
    {
        try { return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(path))).AvailableFreeSpace / 1e9; } catch { return -1; }
    }

    static string DefaultFolder()
    {
        string home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Jarvis");
        if (Directory.Exists(Path.Combine(home, ".git"))) return home;   // already here: update it
        DriveInfo best = null;
        try
        {
            foreach (DriveInfo d in DriveInfo.GetDrives())
            {
                if (d.DriveType != DriveType.Fixed || !d.IsReady) continue;
                string there = Path.Combine(d.RootDirectory.FullName, "Jarvis");
                if (Directory.Exists(Path.Combine(there, ".git"))) return there;
                if (best == null || d.AvailableFreeSpace > best.AvailableFreeSpace) best = d;
            }
        }
        catch { }
        // a nearly full system drive: another drive with clearly more room
        if (Free(home) < 8 && best != null && best.AvailableFreeSpace / 1e9 > Free(home) + 2)
            return Path.Combine(best.RootDirectory.FullName, "Jarvis");
        return home;
    }

    void SetFolder(string f)
    {
        folder = f.Trim().Trim('"').TrimEnd('\\', '/');
        if (folder.Length == 2 && folder[1] == ':') folder = Path.Combine(folder + "\\", "Jarvis");
        updating = Directory.Exists(Path.Combine(folder, ".git"));
        folderBad = !updating && Directory.Exists(folder) && Directory.GetFileSystemEntries(folder).Length > 0;
        freeGb = Free(folder);
        needGb = updating ? 0.5 : (nvidia ? 5 : 3);
        Invalidate();
    }

    static bool HasNvidia()
    {
        try
        {
            using (ManagementObjectSearcher s = new ManagementObjectSearcher("SELECT Name FROM Win32_VideoController"))
                foreach (ManagementObject o in s.Get())
                    if (Convert.ToString(o["Name"]).IndexOf("NVIDIA", StringComparison.OrdinalIgnoreCase) >= 0) return true;
        }
        catch { }
        return false;
    }

    void ChangeFolder()
    {
        using (FolderBrowserDialog d = new FolderBrowserDialog())
        {
            d.Description = "Pick where Jarvis goes. A Jarvis folder is made inside the one you pick.";
            try { d.SelectedPath = Path.GetDirectoryName(folder); } catch { }
            if (d.ShowDialog(this) != DialogResult.OK) return;
            string f = d.SelectedPath.TrimEnd('\\');
            SetFolder(Path.GetFileName(f).Equals("Jarvis", StringComparison.OrdinalIgnoreCase) ? f : Path.Combine(f + "\\", "Jarvis"));
        }
    }

    // ------------------------------------------------------------------ running setup.ps1

    void StartInstall()
    {
        page = "run"; failText = null; finished = exited = cancelled = false; cur = 0;
        foreach (StepInfo s in steps) { s.State = PENDING; s.Sub = ""; }
        log.Clear();
        string script = Path.Combine(Path.GetTempPath(), "jarvis-setup.ps1");
        using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("setup.ps1"))
        using (FileStream f = File.Create(script))
            s.CopyTo(f);
        StringBuilder a = new StringBuilder();
        // -ExecutionPolicy Bypass applies to this one PowerShell only; Windows' own setting stays as it is
        a.Append("-NoProfile -ExecutionPolicy Bypass -File \"").Append(script).Append("\" -Gui -Folder \"").Append(folder).Append("\"");
        if (check) a.Append(" -Check");
        if (optKeys) a.Append(" -Keys");
        if (optStartup) a.Append(" -Startup");
        if (optShortcut) a.Append(" -Shortcut");
        ProcessStartInfo psi = new ProcessStartInfo("powershell.exe", a.ToString());
        psi.UseShellExecute = false; psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
        psi.StandardOutputEncoding = Encoding.UTF8; psi.StandardErrorEncoding = Encoding.UTF8;
        Process p = new Process();
        p.StartInfo = psi; p.EnableRaisingEvents = true;
        p.OutputDataReceived += OnData;
        p.ErrorDataReceived += OnData;
        p.Exited += delegate
        {
            System.Threading.Thread.Sleep(700);   // let the last lines arrive first
            try { BeginInvoke(new Action(delegate { OnExit(p); })); } catch { }
        };
        proc = p;
        try { p.Start(); }
        catch (Exception ex) { failText = "Couldn't start PowerShell: " + ex.Message; exited = true; Invalidate(); return; }
        p.BeginOutputReadLine(); p.BeginErrorReadLine();
        Invalidate();
    }

    void OnData(object sender, DataReceivedEventArgs e)
    {
        if (e.Data == null) return;
        string line = e.Data;
        try { BeginInvoke(new Action(delegate { OnLine(line); })); } catch { }
    }

    void OnLine(string line)
    {
        line = line.TrimStart('\uFEFF');
        if (line.StartsWith("@@"))
        {
            string[] p = line.Substring(2).Split(new char[] { '|' }, 2);
            string kind = p[0], text = p.Length > 1 ? p[1] : "";
            if (kind == "step")
            {
                string[] q = text.Split(new char[] { '|' }, 2);
                int n;
                if (int.TryParse(q[0], out n) && n >= 1 && n <= 10)
                {
                    if (cur > 0 && (steps[cur - 1].State == RUNNING || steps[cur - 1].State == WAITING)) steps[cur - 1].State = DONE;
                    cur = n;
                    steps[n - 1].State = RUNNING; steps[n - 1].Sub = "";
                    if (q.Length > 1) steps[n - 1].Title = q[1];
                    AppendLog("");
                    AppendLog("== " + steps[n - 1].Title);
                }
            }
            else if (kind == "done") { finished = true; if (exited) Finish(); }
            else if (cur > 0)
            {
                StepInfo s = steps[cur - 1];
                if (kind == "ok") { s.State = DONE; s.Sub = text; }
                else if (kind == "info") { if (s.State != FAILED) s.State = RUNNING; s.Sub = text; }
                else if (kind == "wait") { s.State = WAITING; s.Sub = text; }
                else if (kind == "fail") { s.State = FAILED; s.Sub = text; failText = text; }
                AppendLog("   " + text);
            }
        }
        else
        {
            AppendLog(line);
            if (cur > 0 && steps[cur - 1].State == RUNNING && Live.IsMatch(line))
                steps[cur - 1].Sub = Regex.Replace(line.Trim(), @"\s*\(from .*$", "");
        }
        Invalidate();
    }

    void OnExit(Process p)
    {
        exited = true;
        int code = -1;
        try { code = p.ExitCode; } catch { }
        if (cancelled) return;
        if (code == 0)
        {
            foreach (StepInfo s in steps) if (s.State == RUNNING || s.State == WAITING) s.State = DONE;
            if (finished) Finish();
        }
        else if (failText == null)
        {
            failText = "Setup stopped unexpectedly. Details shows what happened.";
            if (cur > 0) { steps[cur - 1].State = FAILED; steps[cur - 1].Sub = failText; }
        }
        Invalidate();
    }

    void Finish()
    {
        page = "done"; showLog = false; log.Visible = false;
        Invalidate();
        Activate();
    }

    void AppendLog(string s)
    {
        if (log.TextLength > 400000) log.Text = log.Text.Substring(200000);
        log.AppendText(s + Environment.NewLine);
    }

    void KillTree()
    {
        try
        {
            if (proc == null || proc.HasExited) return;
            ProcessStartInfo k = new ProcessStartInfo("taskkill", "/T /F /PID " + proc.Id);
            k.CreateNoWindow = true; k.UseShellExecute = false;
            Process.Start(k).WaitForExit(5000);
        }
        catch { }
    }

    bool ConfirmStop()
    {
        return MessageBox.Show(this, "Stop setting up Jarvis?\n\nRun jarvis-setup.exe again later: it carries on where it stopped.",
            "Jarvis setup", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes;
    }

    void CancelInstall()
    {
        if (!ConfirmStop()) return;
        cancelled = true; KillTree(); Close();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (page == "run" && proc != null && !exited && !cancelled)
        {
            if (!ConfirmStop()) { e.Cancel = true; return; }
            cancelled = true; KillTree();
        }
        base.OnFormClosing(e);
    }

    void ShowQr()
    {
        try
        {
            ProcessStartInfo q = new ProcessStartInfo(Path.Combine(folder, ".venv\\Scripts\\pythonw.exe"), "-m jarvis.pairing");
            q.WorkingDirectory = folder; q.UseShellExecute = false;
            Process.Start(q);
        }
        catch (Exception ex) { MessageBox.Show(this, "Couldn't open it: " + ex.Message, "Jarvis setup"); }
    }

    // ------------------------------------------------------------------ drawing

    int D(float v) { return (int)Math.Round(v * S); }
    RectangleF R(float x, float y, float w, float h) { return new RectangleF(x * S, y * S, w * S, h * S); }

    static GraphicsPath Round(RectangleF r, float rad)
    {
        GraphicsPath p = new GraphicsPath();
        float d = Math.Min(rad * 2, Math.Min(r.Width, r.Height));
        if (d <= 0) { p.AddRectangle(r); return p; }
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }

    void Fill(Graphics g, RectangleF r, float rad, Color c)
    {
        using (GraphicsPath p = Round(r, rad * S)) using (SolidBrush b = new SolidBrush(c)) g.FillPath(b, p);
    }

    void Panel(Graphics g, float x, float y, float w, float h)
    {
        RectangleF r = R(x, y, w, h);
        Fill(g, r, 16, Card);
        using (GraphicsPath p = Round(r, 16 * S)) using (Pen pen = new Pen(Edge, Math.Max(1, S))) g.DrawPath(pen, p);
    }

    void Str(Graphics g, string s, Font f, Color c, float x, float y)
    {
        using (SolidBrush b = new SolidBrush(c)) g.DrawString(s, f, b, x * S, y * S);
    }

    void StrFit(Graphics g, string s, Font f, Color c, float x, float y, float w)
    {
        using (SolidBrush b = new SolidBrush(c))
        using (StringFormat sf = new StringFormat(StringFormatFlags.NoWrap))
        {
            sf.Trimming = StringTrimming.EllipsisCharacter;
            g.DrawString(s, f, b, new RectangleF(x * S, y * S, w * S, f.GetHeight(g) * 1.3f), sf);
        }
    }

    void StrBox(Graphics g, string s, Font f, Color c, RectangleF r, StringAlignment h, StringAlignment v)
    {
        using (SolidBrush b = new SolidBrush(c))
        using (StringFormat sf = new StringFormat())
        {
            sf.Alignment = h; sf.LineAlignment = v;
            g.DrawString(s, f, b, r, sf);
        }
    }

    void Btn(Graphics g, string id, string text, bool primary, bool enabled, float x, float y, float w, float h, Action click)
    {
        RectangleF r = R(x, y, w, h);
        bool hv = enabled && hover == id;
        Color fill = primary ? (enabled ? (hv ? AccentHover : Accent) : C("23414B")) : (hv ? HoverCard : Card);
        Fill(g, r, 12, fill);
        if (!primary) using (GraphicsPath p = Round(r, 12 * S)) using (Pen pen = new Pen(Edge, Math.Max(1, S))) g.DrawPath(pen, p);
        StrBox(g, text, fBold, primary ? (enabled ? OnAccent : Dim) : (enabled ? Ink : Dim), r, StringAlignment.Center, StringAlignment.Center);
        if (enabled) hits.Add(new Hit { Id = id, R = r, Click = click });
    }

    void Switch(Graphics g, float x, float y, bool on)
    {
        Fill(g, R(x, y, 44, 26), 13, on ? Accent : Off);
        using (SolidBrush b = new SolidBrush(Color.White)) g.FillEllipse(b, R(x + (on ? 21 : 3), y + 3, 20, 20));
    }

    void ToggleRow(Graphics g, int i, string id, string title, string sub, bool on, Action toggle)
    {
        float y = 246 + i * 64;
        if (i > 0) using (Pen pen = new Pen(Edge, Math.Max(1, S))) g.DrawLine(pen, 52 * S, y * S, 468 * S, y * S);
        Str(g, title, fBold, Ink, 52, y + 12);
        StrFit(g, sub, fSmall, Sub, 52, y + 34, 350);
        Switch(g, 424, y + 19, on);
        hits.Add(new Hit { Id = id, R = R(32, y, 456, 64), Click = toggle });
    }

    void StepIcon(Graphics g, float cx, float cy, int state)
    {
        float r = 10 * S, x = cx * S, y = cy * S, w = Math.Max(1.6f, 2.2f * S);
        RectangleF c = new RectangleF(x - r, y - r, 2 * r, 2 * r);
        if (state == PENDING) using (Pen p = new Pen(Dim, Math.Max(1.2f, 1.5f * S))) g.DrawEllipse(p, c);
        else if (state == RUNNING)
        {
            using (Pen p = new Pen(Edge, w)) g.DrawEllipse(p, c);
            using (Pen p = new Pen(Accent, w)) { p.StartCap = p.EndCap = LineCap.Round; g.DrawArc(p, c, (spin * 120) % 360, 100); }
        }
        else if (state == WAITING)
        {
            int a = (int)(140 + 115 * Math.Sin(spin * 1.6));
            using (Pen p = new Pen(Color.FromArgb(a, Amber), w)) g.DrawEllipse(p, c);
            using (SolidBrush b = new SolidBrush(Amber)) g.FillEllipse(b, x - 3 * S, y - 3 * S, 6 * S, 6 * S);
        }
        else if (state == DONE)
        {
            using (SolidBrush b = new SolidBrush(Green)) g.FillEllipse(b, c);
            using (Pen p = new Pen(OnAccent, w)) { p.StartCap = p.EndCap = LineCap.Round; p.LineJoin = LineJoin.Round;
                g.DrawLines(p, new PointF[] { new PointF(x - 4.5f * S, y + 0.3f * S), new PointF(x - 1.3f * S, y + 3.6f * S), new PointF(x + 4.6f * S, y - 3.4f * S) }); }
        }
        else
        {
            using (SolidBrush b = new SolidBrush(Red)) g.FillEllipse(b, c);
            using (Pen p = new Pen(OnAccent, w)) { p.StartCap = p.EndCap = LineCap.Round;
                g.DrawLine(p, x - 3.5f * S, y - 3.5f * S, x + 3.5f * S, y + 3.5f * S); g.DrawLine(p, x + 3.5f * S, y - 3.5f * S, x - 3.5f * S, y + 3.5f * S); }
        }
    }

    string Subtitle()
    {
        if (page == "welcome") return check ? "Check what this computer has (nothing is changed)" : updating ? "Already on this computer. This updates him." : "Set up Jarvis on this computer";
        if (page == "done") return "All set";
        if (failText != null) return "Setup stopped";
        if (exited) return check ? "Check finished" : "Finished";
        return check ? "Checking..." : updating ? "Updating..." : "Installing...";
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        g.Clear(Bg);
        hits.Clear();
        if (logo != null) g.DrawImage(logo, R(32, 30, 54, 54));
        Str(g, "Jarvis", fTitle, Ink, 100, 27);
        Str(g, Subtitle(), fBody, Sub, 102, 62);
        if (page == "welcome") Welcome(g);
        else if (page == "run") Running(g);
        else Done(g);
    }

    void Welcome(Graphics g)
    {
        Panel(g, 32, 112, 456, 118);
        Str(g, updating ? "Jarvis is in" : "Install to", fSmall, Sub, 52, 128);
        StrFit(g, folder, fBold, Ink, 52, 150, 316);
        string space; Color sc = Sub;
        if (folderBad) { space = "That folder already has other files in it. Pick another."; sc = Red; }
        else if (updating) space = "Your settings, keys and chats stay as they are.";
        else
        {
            space = string.Format("{0:0.#} GB free on {1}   ·   needs about {2} GB", Math.Max(0, freeGb), Path.GetPathRoot(folder).TrimEnd('\\'), needGb);
            if (freeGb >= 0 && freeGb < needGb + 1) sc = Red;
        }
        StrFit(g, space, fSmall, sc, 52, 182, 416);
        Btn(g, "change", "Change", false, true, 380, 136, 88, 36, ChangeFolder);

        Panel(g, 32, 246, 456, 192);
        ToggleRow(g, 0, "keys", "Add free AI keys", "Gemini and OpenRouter, pasted in a window that opens", optKeys, delegate { optKeys = !optKeys; });
        ToggleRow(g, 1, "startup", "Start when I log in", "Jarvis starts by himself, hidden in the tray", optStartup, delegate { optStartup = !optStartup; });
        ToggleRow(g, 2, "shortcut", "Desktop shortcut", "Jarvis on the Desktop and in the Start menu", optShortcut, delegate { optShortcut = !optShortcut; });

        StrBox(g, (nvidia ? "NVIDIA graphics found: speech runs on it. " : "No NVIDIA graphics: speech runs on the processor. ") +
            "Takes 5 to 15 minutes. Windows may ask you to allow Git, Python and Claude Code, and a Claude window opens for you to log in.",
            fSmall, Sub, R(34, 456, 452, 80), StringAlignment.Near, StringAlignment.Near);

        Btn(g, "cancel", "Cancel", false, true, 216, 624, 120, 44, Close);
        Btn(g, "go", check ? "Check" : updating ? "Update" : "Install", true, !folderBad, 348, 624, 140, 44, StartInstall);
    }

    void Running(Graphics g)
    {
        if (!showLog)
        {
            Panel(g, 32, 112, 456, 432);
            for (int i = 0; i < 10; i++)
            {
                float y = 118 + i * 42;
                StepInfo s = steps[i];
                if (s.State == RUNNING || s.State == WAITING || (s.State == FAILED && failText != null)) Fill(g, R(38, y, 444, 42), 10, Hi);
                StepIcon(g, 60, y + 21, s.State);
                if (s.Sub == "") Str(g, s.Title, fBold, s.State == PENDING ? Dim : Ink, 84, y + 11);
                else
                {
                    Str(g, s.Title, fBold, s.State == PENDING ? Dim : Ink, 84, y + 3);
                    StrFit(g, s.Sub, fSmall, s.State == WAITING ? Amber : s.State == FAILED ? Red : Sub, 84, y + 21, 388);
                }
            }
        }
        else Panel(g, 32, 112, 456, 432);
        if (log.Visible != showLog) log.Visible = showLog;

        int done = 0; bool busy = false;
        foreach (StepInfo s in steps) { if (s.State == DONE) done++; if (s.State == RUNNING || s.State == WAITING) busy = true; }
        float frac = Math.Min(1f, (done + (busy ? 0.5f : 0f)) / 10f);
        string label; Color lc = Sub;
        if (failText != null) { label = "Stopped at step " + cur + ". Fix that, then Try again: it carries on from there."; lc = Red; }
        else if (exited) label = check ? "Check finished. Nothing was changed." : "Finished.";
        else if (cur > 0) label = "Step " + cur + " of 10   ·   " + steps[cur - 1].Title;
        else label = "Starting...";
        StrFit(g, label, fSmall, lc, 32, 558, 400);
        StrBox(g, (int)(frac * 100) + "%", fSmall, Sub, R(408, 558, 80, 20), StringAlignment.Far, StringAlignment.Near);
        Fill(g, R(32, 586, 456, 6), 3, Edge);
        if (frac > 0) Fill(g, R(32, 586, Math.Max(6, 456 * frac), 6), 3, failText != null ? Red : Accent);

        Btn(g, "log", showLog ? "Steps" : "Details", false, true, 32, 624, 120, 44, delegate { showLog = !showLog; if (showLog) { log.SelectionStart = log.TextLength; log.ScrollToCaret(); } });
        if (!exited) Btn(g, "cancel", "Cancel", false, true, 368, 624, 120, 44, CancelInstall);
        else if (failText != null)
        {
            Btn(g, "close", "Close", false, true, 216, 624, 120, 44, Close);
            Btn(g, "again", "Try again", true, true, 348, 624, 140, 44, StartInstall);
        }
        else Btn(g, "close", "Close", true, true, 368, 624, 120, 44, Close);
    }

    void Done(Graphics g)
    {
        RectangleF c = R(228, 116, 64, 64);
        using (SolidBrush b = new SolidBrush(Green)) g.FillEllipse(b, c);
        using (Pen p = new Pen(OnAccent, 5 * S))
        {
            p.StartCap = p.EndCap = LineCap.Round; p.LineJoin = LineJoin.Round;
            float x = c.X + c.Width / 2, y = c.Y + c.Height / 2;
            g.DrawLines(p, new PointF[] { new PointF(x - 13 * S, y + 1 * S), new PointF(x - 4 * S, y + 10 * S), new PointF(x + 14 * S, y - 9 * S) });
        }
        StrBox(g, "Jarvis is running", fHead, Ink, R(32, 196, 456, 32), StringAlignment.Center, StringAlignment.Near);
        StrBox(g, "His icon is under the ^ arrow in the taskbar.", fBody, Sub, R(32, 230, 456, 24), StringAlignment.Center, StringAlignment.Near);

        Panel(g, 32, 280, 456, 262);
        Str(g, "Connect your phone", fBold, Ink, 52, 298);
        string[] lines = {
            "A window with a QR code just opened on this computer.",
            "In Jarvis on your iPhone: Settings, Your computers, Add a Computer, then Scan Its QR Code and point it at that code.",
            "Pick which computer you're talking to at the top of Control Center (the computer tab).",
        };
        float[] ys = { 334, 390, 462 };
        for (int i = 0; i < 3; i++)
        {
            RectangleF badge = R(52, ys[i], 24, 24);
            using (SolidBrush b = new SolidBrush(Accent)) g.FillEllipse(b, badge);
            StrBox(g, (i + 1).ToString(), fBold, OnAccent, badge, StringAlignment.Center, StringAlignment.Center);
            StrBox(g, lines[i], fBody, Sub, R(88, ys[i] + 2, 384, 84), StringAlignment.Near, StringAlignment.Near);
        }

        Btn(g, "qr", "Show QR code", false, true, 32, 624, 160, 44, ShowQr);
        Btn(g, "finish", "Finish", true, true, 368, 624, 120, 44, Close);
    }

    // ------------------------------------------------------------------ mouse, keys, title bar

    protected override void OnMouseMove(MouseEventArgs e)
    {
        string h = null;
        foreach (Hit x in hits) if (x.R.Contains(e.Location)) h = x.Id;
        if (h != hover) { hover = h; Cursor = h != null ? Cursors.Hand : Cursors.Default; Invalidate(); }
        base.OnMouseMove(e);
    }

    protected override void OnMouseLeave(EventArgs e) { if (hover != null) { hover = null; Invalidate(); } base.OnMouseLeave(e); }

    protected override void OnMouseClick(MouseEventArgs e)
    {
        if (e.Button == MouseButtons.Left)
            foreach (Hit x in new List<Hit>(hits))
                if (x.R.Contains(e.Location)) { x.Click(); Invalidate(); break; }
        base.OnMouseClick(e);
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (e.KeyCode == Keys.Enter && page == "welcome" && !folderBad) StartInstall();
        else if (e.KeyCode == Keys.Enter && page == "done") Close();
        else if (e.KeyCode == Keys.Escape) Close();
        base.OnKeyDown(e);
    }

    [DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        try
        {
            int on = 1, caption = Bg.B << 16 | Bg.G << 8 | Bg.R;
            DwmSetWindowAttribute(Handle, 20, ref on, 4);        // dark title bar
            DwmSetWindowAttribute(Handle, 35, ref caption, 4);   // the same colour as the window (Windows 11)
        }
        catch { }
    }
}
