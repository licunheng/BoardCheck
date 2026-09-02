using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Diagnostics;

// 板检数据核验工具 - 自解压安装器
// 用法:把 package.zip 附加到本程序末尾(copy /b stub.exe + package.zip = 安装包.exe)
// 运行时:从自身尾部定位 zip -> 解压到 %LOCALAPPDATA%\板检数据核验工具 -> 建桌面快捷方式 -> 启动
class Installer
{
    static readonly string APP_NAME = "\u677f\u68c0\u6570\u636e\u6838\u9a8c\u5de5\u5177"; // 板检数据核验工具
    static readonly string EXE_NAME = "\u677f\u68c0\u6570\u636e\u6838\u9a8c\u5de5\u5177.exe"; // 板检数据核验工具.exe
    static readonly string SHORTCUT = "\u677f\u68c0\u6570\u636e\u6838\u9a8c\u5de5\u5177.lnk"; // 板检数据核验工具.lnk

    [STAThread]
    static int Main(string[] args)
    {
        try
        {
            string selfPath = Process.GetCurrentProcess().MainModule.FileName;
            byte[] all = File.ReadAllBytes(selfPath);

            // 1. 定位内嵌 zip(找 EOCD 签名 PK\x05\x06)
            int zipStart = FindZipStart(all);
            if (zipStart < 0)
            {
                Console.WriteLine("错误:未找到安装数据(package.zip 未附加)");
                Pause();
                return 1;
            }

            // 2. 默认安装目录:%LOCALAPPDATA%\板检数据核验工具,支持 /d=<path>
            string dest = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                APP_NAME);
            foreach (string a in args)
            {
                if (a.StartsWith("/d=", StringComparison.OrdinalIgnoreCase))
                {
                    string p = a.Substring(3).Trim('"');
                    if (p.Length > 0) dest = Path.GetFullPath(p);
                }
            }

            // 3. 解压
            using (var ms = new MemoryStream(all, zipStart, all.Length - zipStart))
            using (var zip = new ZipArchive(ms, ZipArchiveMode.Read))
            {
                foreach (var entry in zip.Entries)
                {
                    if (string.IsNullOrEmpty(entry.Name)) continue; // 目录项
                    string target = Path.Combine(dest, entry.FullName.Replace('/', Path.DirectorySeparatorChar));
                    string dir = Path.GetDirectoryName(target);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    using (var src = entry.Open())
                    using (var dst = File.Create(target))
                        src.CopyTo(dst);
                }
            }

            // 4. 桌面快捷方式
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            CreateShortcut(Path.Combine(desktop, SHORTCUT),
                Path.Combine(dest, EXE_NAME), dest);

            // 5. 启动主程序
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = Path.Combine(dest, EXE_NAME),
                    WorkingDirectory = dest,
                    UseShellExecute = true
                });
            }
            catch { }

            string msg = "安装完成!\n\n已安装到:\n" + dest +
                "\n\n桌面已创建快捷方式,程序正在启动。\n浏览器将自动打开 http://127.0.0.1:5050";
            System.Windows.Forms.MessageBox.Show(msg, "板检数据核验工具",
                System.Windows.Forms.MessageBoxButtons.OK,
                System.Windows.Forms.MessageBoxIcon.Information);
            return 0;
        }
        catch (Exception ex)
        {
            System.Windows.Forms.MessageBox.Show("安装失败:\n" + ex.Message, "板检数据核验工具",
                System.Windows.Forms.MessageBoxButtons.OK,
                System.Windows.Forms.MessageBoxIcon.Error);
            return 1;
        }
    }

    // 定位 zip 起始偏移:
    // 文件末尾 = ... [central directory][EOCD][comment]
    // EOCD 记录:offset+12=cdSize, offset+16=cdOffset(相对 zip 起点)
    // zipStart = EOCD绝对位置 - cdSize - cdOffset
    static int FindZipStart(byte[] all)
    {
        for (int i = all.Length - 22; i >= 0; i--)
        {
            if (all[i] == 0x50 && all[i + 1] == 0x4B &&
                all[i + 2] == 0x05 && all[i + 3] == 0x06)
            {
                int cdSize = BitConverter.ToInt32(all, i + 12);
                int cdOffset = BitConverter.ToInt32(all, i + 16);
                int zipStart = (i - cdSize) - cdOffset;
                if (zipStart >= 0 && zipStart < all.Length)
                    return zipStart;
                return -1;
            }
        }
        return -1;
    }

    static void CreateShortcut(string lnkPath, string target, string workDir)
    {
        try
        {
            Type t = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(t);
            dynamic lnk = shell.CreateShortcut(lnkPath);
            lnk.TargetPath = target;
            lnk.WorkingDirectory = workDir;
            lnk.Description = APP_NAME;
            lnk.Save();
        }
        catch { /* 快捷方式失败不阻断安装 */ }
    }

    static void Pause()
    {
        try { Console.ReadKey(); } catch { }
    }
}
