using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class TranslationEditorLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string directory = AppDomain.CurrentDomain.BaseDirectory;
            string root = Path.GetFullPath(Path.Combine(directory, ".."));
            string script = Path.Combine(root, "tools", "translation_editor.py");
            string runtime = Path.Combine(directory, @"runtime\python.exe");
            bool bundledProject = File.Exists(Path.Combine(directory, "Project", "tools", "translation_editor.py"));
            bool portable = File.Exists(runtime) && !bundledProject;
            if (bundledProject)
            {
                root = Path.Combine(directory, "Project");
                script = Path.Combine(root, "tools", "translation_editor.py");
                if (!File.Exists(runtime))
                    throw new FileNotFoundException("Keep the Runtime folder beside the executable.", runtime);
            }
            if (portable)
            {
                root = directory;
                script = Path.Combine(root, @"app\translation_editor.py");
            }
            if (!File.Exists(script))
                throw new FileNotFoundException("Keep this executable in the project build folder.", script);

            var launch = new ProcessStartInfo
            {
                FileName = portable || bundledProject ? runtime : "py.exe",
                Arguments = "-3.11 -B \"" + script + "\" --open-browser",
                WorkingDirectory = root,
                UseShellExecute = false
            };

            if (args.Length == 1 && args[0] == "--check")
                launch.Arguments = "-3.11 -B \"" + script + "\" --help";
            else if (args.Length != 0)
                throw new ArgumentException("Supported argument: --check");

            if (bundledProject)
            {
                string output = Path.Combine(directory, "Output");
                Directory.CreateDirectory(output);
                launch.EnvironmentVariables["KINDAICHI_EDITOR_OUTPUT"] = output;
                launch.EnvironmentVariables["KINDAICHI_EDITOR_FROZEN_INDEX"] = "1";
                string setup = Path.Combine(root, "tools", "setup_public_workbench.py");
                string database = Path.Combine(root, "extract", "translation", "master.sqlite");
                if (args.Length == 0 && File.Exists(setup) && !File.Exists(database))
                {
                    using (var picker = new OpenFileDialog())
                    {
                        picker.Title = "Select your clean Japanese SLPS-00601 disc";
                        picker.Filter = "PlayStation disc images (*.cue;*.bin)|*.cue;*.bin";
                        picker.CheckFileExists = true;
                        if (picker.ShowDialog() != DialogResult.OK)
                            return 0;
                        launch.Arguments = "-B \"" + setup + "\" \"" + picker.FileName + "\"";
                    }
                    using (var initialization = Process.Start(launch))
                    {
                        initialization.WaitForExit();
                        if (initialization.ExitCode != 0)
                            throw new InvalidOperationException("Setup failed. Check the disc path and try again.");
                    }
                }

                launch.Arguments = "-B \"" + script + "\"" +
                    (args.Length == 0 ? " --port 0 --open-browser" : " --help");
            }

            if (portable)
            {
                string database = File.Exists(Path.Combine(root, "master.sqlite"))
                    ? "master.sqlite" : "demo.sqlite";
                launch.Arguments = "-B \"" + script + "\" --portable --db " + database
                    + (args.Length == 0 ? " --port 0 --open-browser" : " --help");
            }

            using (var process = Process.Start(launch))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                    Console.Error.WriteLine("The editor stopped with exit code " + process.ExitCode + ".");
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            if (args.Length == 0 && !Console.IsInputRedirected)
            {
                Console.Error.WriteLine("Press Enter to close.");
                Console.ReadLine();
            }
            return 1;
        }
    }
}
