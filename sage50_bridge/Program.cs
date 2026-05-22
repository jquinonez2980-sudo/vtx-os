using System;
using System.Collections.Generic;
using System.Data;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json;
using SimplySDK;
using SimplySDK.GeneralModule;
using SimplySDK.Support;
using Simply.Domain.Utility;

// Sage50Bridge — read/write a Sage 50 company file via the SDK.
//
// Usage:
//   Sage50Bridge.exe --sai <file.sai> --user <user> --password <pass> --table <table>
//                    [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
//
// Tables: gl, ar, ap, coa, customers, vendors, tables
//
// PREREQUISITES:
//   1. Sage 50 must be running with the company file open.
//   2. The SDK app code "SASDK" must be authorized in Sage 50:
//      Setup -> System Settings -> Security -> Third-Party Applications
//      (or it may prompt automatically on first connect)

namespace Sage50Bridge
{
    class Program
    {
        private static readonly string SdkDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
            "Sage 50 Accounting SDK", "SDK");

        // SDK DLLs live in two places: root SDK dir + ConnectionManager\4.0\
        private static readonly string[] AsmSearchDirs;

        static Program()
        {
            AsmSearchDirs = new[]
            {
                SdkDir,
                Path.Combine(SdkDir, "ConnectionManager", "4.0"),
            };

            AppDomain.CurrentDomain.AssemblyResolve += (s, a) =>
            {
                string name = new AssemblyName(a.Name).Name;
                foreach (string dir in AsmSearchDirs)
                {
                    string path = Path.Combine(dir, name + ".dll");
                    if (File.Exists(path)) return Assembly.LoadFrom(path);
                }
                return null;
            };
        }

        static int Main(string[] args)
        {
            Options opts = ParseArgs(args);
            if (opts == null)
            {
                Console.Error.WriteLine(
                    "Usage (read):  Sage50Bridge --sai <file> --user <u> [--password <p>] --table <t>");
                Console.Error.WriteLine(
                    "Usage (write): Sage50Bridge --sai <file> --user <u> [--password <p>] --mode write  (JSON on stdin)");
                Console.Error.WriteLine(
                    "Tables: tables, gl, ar, ap, coa, customers, vendors, reflect");
                return 1;
            }

            Console.OutputEncoding = Encoding.UTF8;

            try
            {
                SDKInstanceManager.Instance.SetAlertImplementation(new SilentAlert());

                // Version check (works without credentials — reads SAI header only).
                string dbVer, dbRel;
                SDKInstanceManager.Instance.GetDatabaseVersion(opts.SaiFile, out dbVer, out dbRel);
                Console.Error.WriteLine("DB version=" + dbVer + " release=" + dbRel);

                Console.Error.WriteLine(
                    "Opening: " + opts.SaiFile +
                    "  user=" + opts.User +
                    "  TPAppCode=SASDK  multiUser=true");

                SDKInstanceManager.SDKResult sdkResult;
                bool opened = SDKInstanceManager.Instance.OpenDatabase(
                    opts.SaiFile,
                    opts.User,
                    opts.Password,
                    true,           // multiUser — allow connection alongside active Sage 50 session
                    "Sage50Bridge", // TPAppName
                    "SASDK",        // TPAppCode — Sage 50 SDK registered code (matches SDK sample)
                    1,              // TPAppVer
                    out sdkResult);

                Console.Error.WriteLine(
                    "OpenDatabase: " + sdkResult + " (opened=" + opened + ")");

                if (!opened)
                {
                    WriteError("Cannot open database: " + sdkResult + GetHint(sdkResult));
                    return 1;
                }

                try
                {
                    if (opts.Mode == "write")
                    {
                        string jsonIn = Console.In.ReadToEnd();
                        Console.WriteLine(new DataWriter().PostJournalEntries(jsonIn));
                    }
                    else
                    {
                        Console.WriteLine(
                            new DataExporter(opts.StartDate, opts.EndDate).Export(opts.Table));
                    }
                    return 0;
                }
                finally
                {
                    SDKInstanceManager.Instance.CloseDatabase();
                }
            }
            catch (Exception ex)
            {
                WriteError(ex.Message);
                return 1;
            }
        }

        // ── Arg parsing ───────────────────────────────────────────────────────────

        static Options ParseArgs(string[] args)
        {
            var opts = new Options();
            for (int i = 0; i + 1 < args.Length; i++)
            {
                switch (args[i].ToLower())
                {
                    case "--sai":        opts.SaiFile  = args[++i]; break;
                    case "--user":       opts.User     = args[++i]; break;
                    case "--password":   opts.Password = args[++i]; break;
                    case "--table":      opts.Table    = args[++i]; break;
                    case "--mode":       opts.Mode     = args[++i]; break;
                    case "--start-date": opts.StartDate = args[++i]; break;
                    case "--end-date":   opts.EndDate   = args[++i]; break;
                    // MySQL-mode flags — accepted but ignored.
                    case "--host":
                    case "--port":
                    case "--db":
                    case "--mysql-user":
                    case "--mysql-pass": i++; break;
                }
            }
            bool isWrite = opts.Mode == "write";
            if (string.IsNullOrEmpty(opts.SaiFile) ||
                (!isWrite && string.IsNullOrEmpty(opts.Table)))
                return null;
            return opts;
        }

        // ── Error hints ───────────────────────────────────────────────────────────

        static string GetHint(SDKInstanceManager.SDKResult r)
        {
            switch (r)
            {
                case SDKInstanceManager.SDKResult.FAIL_USER_LOGON_FAILED:
                    return "\n  -> Wrong username/password, OR the user lacks third-party access." +
                           "\n     In Sage 50: Setup -> Manage Users -> select user -> enable 'Allow Third-Party Access'.";
                case SDKInstanceManager.SDKResult.FAIL_CONNECTIONMGR_NONE:
                    return "\n  -> Connection Manager is not running. " +
                           "Make sure Sage 50 is open with the company file loaded.";
                case SDKInstanceManager.SDKResult.FAIL_MYSQL_NOTRUNNING:
                    return "\n  -> Sage 50 MySQL is not running. " +
                           "Open Sage 50 and load the company file first.";
                case SDKInstanceManager.SDKResult.FAIL_PATH_NOT_EXIST:
                    return "\n  -> SAI file not found at the specified path.";
                case SDKInstanceManager.SDKResult.FAIL_INVALID_SECURITY_DETAILS:
                    return "\n  -> Security settings error. " +
                           "In Sage 50: Setup -> System Settings -> Security, " +
                           "authorize third-party app code 'SASDK'.";
                case SDKInstanceManager.SDKResult.FAIL:
                    return "\n  -> Unspecified failure. Check:\n" +
                           "     1. Sage 50 is open with this company file loaded.\n" +
                           "     2. App 'SASDK' is authorized in Sage 50:\n" +
                           "        Setup -> System Settings -> Security\n" +
                           "        OR watch for an authorization dialog in Sage 50.";
                default:
                    return "";
            }
        }

        static void WriteError(string message)
        {
            Console.Error.WriteLine("ERROR: " + message);
            Console.WriteLine(JsonConvert.SerializeObject(new { error = message }));
        }
    }

    // ── Options ───────────────────────────────────────────────────────────────────

    class Options
    {
        public string SaiFile;
        public string User     = "sysadmin";
        public string Password = "";
        public string Table;
        public string Mode      = "read";   // "read" | "write"
        public string StartDate;
        public string EndDate;
    }

    // ── Silent alert handler ──────────────────────────────────────────────────────

    class SilentAlert : SDKAlert
    {
        public override AlertResult AskAlert(SimplyMessage m) { Log(m); return AlertResult.YES; }
        public override AlertResult AskSaveAlert()            { return AlertResult.NO; }
        public override AlertResult YNCAlert(SimplyMessage m) { Log(m); return AlertResult.NO; }
        public override void StopAlert(SimplyMessage m)       { Log(m); }
        public override bool StopAlertNotShow(SimplyMessage m){ Log(m); return false; }
        private static void Log(SimplyMessage m) { Console.Error.WriteLine("SDK alert: " + m.Message); }
    }

    // ── Data export ───────────────────────────────────────────────────────────────

    class DataExporter
    {
        private readonly string _start;
        private readonly string _end;

        public DataExporter(string startDate, string endDate)
        {
            _start = startDate;
            _end   = endDate;
        }

        public string Export(string table)
        {
            // Ad-hoc discovery modes (not in switch — C# 5 switch doesn't support patterns).
            if (table.StartsWith("desc:"))
                return QueryJson("DESCRIBE " + table.Substring(5));
            if (table.StartsWith("raw:"))
                return QueryJson("SELECT * FROM " + table.Substring(4) + " LIMIT 3");

            switch (table)
            {
                case "tables":    return QueryJson("SHOW TABLES");
                case "coa":       return QueryJson("SELECT * FROM taccount ORDER BY lId");
                case "vendors":   return QueryJson("SELECT * FROM tvendor ORDER BY sName");
                case "customers": return QueryJson("SELECT * FROM tcustomr ORDER BY sName");
                case "gl": return ExportGl();
                case "ar": return ExportAr();
                case "ap": return ExportAp();
                case "reflect":   return ReflectSdk();
                default:
                    return Error("Unknown table: " + table +
                        ". Valid: tables, gl, ar, ap, coa, customers, vendors, reflect");
            }
        }

        private string ExportGl()
        {
            // tjeh01 = journal entry header: lId (PK, FK for tjentact.lJEntId),
            //   dtASDate = transaction date, sSource = source code, sComment = description
            string baseSelect =
                "SELECT h.lId AS lJEntID, h.dtASDate AS txnDate, " +
                "h.sSource, h.sComment AS hdrComment, " +
                "jl.nLineNum, jl.lAcctId, jl.dAmount, jl.szComment " +
                "FROM tjeh01 h " +
                "JOIN tjentact jl ON jl.lJEntId = h.lId ";

            string df  = DateFilter("h.dtASDate");
            string sql = baseSelect + "WHERE " + df + " ORDER BY h.dtASDate";
            try
            {
                string result = QueryJson(sql);
                // Zero rows with a date filter means entries may have been stored by Sage 50
                // with fiscal-adjusted dates outside the requested range (e.g. "date precedes
                // Fiscal Start" causes entries to land on the prior fiscal year-end date).
                // Retry without the date filter so the caller still sees the entries.
                if (HasDates() && result == "[]")
                {
                    Console.Error.WriteLine(
                        "GL date-filtered query returned 0 rows — retrying without date filter. " +
                        "Check Sage 50 fiscal year start date (Setup > Company Information).");
                    try { return QueryJson(baseSelect + "ORDER BY h.dtASDate"); }
                    catch { }
                }
                return result;
            }
            catch { }

            // Last-resort fallback: raw GL lines (no header join, no dates)
            try { return QueryJson("SELECT * FROM tjentact"); }
            catch { }

            return Error("GL: JOIN with tjeh01 failed.");
        }

        private string ExportAr()
        {
            // trcsal (AR header, has lCusId + date) JOIN trcsall (lines, has amounts)
            string[] dateCols = new[] { "dtLastPost", "dtDate", "dtTrDate", "dtInvDate", "dtTransDate" };
            foreach (string dc in dateCols)
            {
                string df  = DateFilter("h." + dc);
                string ord = HasDates() ? " ORDER BY h." + dc : "";
                string sql =
                    "SELECT h.lId AS invoiceId, h." + dc + " AS txnDate, h.lCusId, " +
                    "l.nLineNum, l.dAmount, l.dPrice, l.dQuantity, l.lAcctId, l.dTaxAmt, l.sDesc " +
                    "FROM trcsal h JOIN trcsall l ON l.lRCSalId = h.lId " +
                    "WHERE " + df + ord;
                try { return QueryJson(sql); }
                catch { }
            }
            // Fallback: header only
            foreach (string dc in dateCols)
            {
                string df = DateFilter(dc);
                try { return QueryJson("SELECT * FROM trcsal WHERE " + df); }
                catch { }
            }
            return Error("AR: no working query found.");
        }

        private string ExportAp()
        {
            // trcpur (AP header, has lVenId + date) JOIN trcpurl (lines, has amounts)
            string[] dateCols = new[] { "dtLastPost", "dtDate", "dtTrDate", "dtInvDate", "dtTransDate" };
            foreach (string dc in dateCols)
            {
                string df  = DateFilter("h." + dc);
                string ord = HasDates() ? " ORDER BY h." + dc : "";
                string sql =
                    "SELECT h.lId AS invoiceId, h." + dc + " AS txnDate, h.lVenId, " +
                    "l.nLineNum, l.dAmount, l.dPrice, l.dQuantity, l.lAcctId, l.dTaxAmt, l.sDesc " +
                    "FROM trcpur h JOIN trcpurl l ON l.lRCPurId = h.lId " +
                    "WHERE " + df + ord;
                try { return QueryJson(sql); }
                catch { }
            }
            // Fallback: header only
            foreach (string dc in dateCols)
            {
                string df = DateFilter(dc);
                try { return QueryJson("SELECT * FROM trcpur WHERE " + df); }
                catch { }
            }
            return Error("AP: no working query found.");
        }

        private bool HasDates()
        {
            return !string.IsNullOrEmpty(_start) || !string.IsNullOrEmpty(_end);
        }

        private string DateFilter(string col)
        {
            bool s = !string.IsNullOrEmpty(_start);
            bool e = !string.IsNullOrEmpty(_end);
            if (s && e) return col + " BETWEEN '" + _start + "' AND '" + _end + "'";
            if (s)      return col + " >= '" + _start + "'";
            if (e)      return col + " <= '" + _end + "'";
            return "1=1";
        }

        private string TryQueries(string[] queries)
        {
            var tried = new List<string>();
            foreach (string sql in queries)
            {
                try { return QueryJson(sql); }
                catch (Exception ex)
                {
                    tried.Add(sql.Substring(0, Math.Min(80, sql.Length)) + " => " + ex.Message);
                }
            }
            return JsonConvert.SerializeObject(new { error = "All queries failed", tried = tried });
        }

        private string QueryJson(string sql)
        {
            var util = new SDKDatabaseUtility();
            util.RunSelectQuery(sql);
            DataSet ds = util.GetDataSetFromLastSelectQuery();
            return DataSetToJson(ds);
        }

        private static string DataSetToJson(DataSet ds)
        {
            if (ds == null || ds.Tables.Count == 0)
                return "[]";
            DataTable dt = ds.Tables[0];
            var rows = new List<Dictionary<string, object>>();
            foreach (DataRow row in dt.Rows)
            {
                var dict = new Dictionary<string, object>();
                foreach (DataColumn col in dt.Columns)
                {
                    object val = row[col];
                    if (val is DBNull || val == null)
                        dict[col.ColumnName] = null;
                    else if (val is DateTime)
                        dict[col.ColumnName] = ((DateTime)val).ToString("yyyy-MM-dd");
                    else
                        dict[col.ColumnName] = val;
                }
                rows.Add(dict);
            }
            return JsonConvert.SerializeObject(rows, Formatting.None);
        }

        private static string Error(string msg)
        {
            return JsonConvert.SerializeObject(new { error = msg });
        }

        // ── SDK reflection probe ──────────────────────────────────────────────────
        // Usage: --table reflect
        // Writes type/method info to stderr; returns {"ok":true} to stdout.
        private string ReflectSdk()
        {
            var sdkAsm = typeof(SDKInstanceManager).Assembly;

            // 1. Well-known types we want full method lists for
            string[] wantTypes =
            {
                "SimplySDK.Support.SDKDatabaseUtility",
                "SimplySDK.SDKInstanceManager",
                "SimplySDK.GeneralJournal",
                "SimplySDK.GeneralJournalEntry",
                "SimplySDK.GenericModel",
            };
            foreach (var tn in wantTypes)
            {
                var t = sdkAsm.GetType(tn);
                if (t == null) { Console.Error.WriteLine("\n=== " + tn + ": NOT FOUND ==="); continue; }
                PrintReflectType(t);
            }

            // 2. Anything with "journal" or "entry" in the name across ALL loaded assemblies
            Console.Error.WriteLine("\n=== Journal/Entry types across all loaded assemblies ===");
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types;
                try { types = asm.GetExportedTypes(); } catch { continue; }
                foreach (var t in types.Where(t2 =>
                    t2.Name.IndexOf("journal", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    t2.Name.IndexOf("entry",   StringComparison.OrdinalIgnoreCase) >= 0))
                {
                    Console.Error.WriteLine("  " + t.FullName + "  [" + asm.GetName().Name + "]");
                    PrintReflectType(t);
                }
            }

            // 3. DESCRIBE the header and line tables so we know required columns
            try { Console.Error.WriteLine("\n=== DESCRIBE tjeh01 ===\n"   + QueryJson("DESCRIBE tjeh01"));   } catch (Exception e) { Console.Error.WriteLine("tjeh01: " + e.Message); }
            try { Console.Error.WriteLine("\n=== DESCRIBE tjentact ===\n" + QueryJson("DESCRIBE tjentact")); } catch (Exception e) { Console.Error.WriteLine("tjentact: " + e.Message); }

            // 4. Sample row from each table so we know the actual data shape
            try { Console.Error.WriteLine("\n=== SAMPLE tjeh01 (1 row) ===\n"   + QueryJson("SELECT * FROM tjeh01 LIMIT 1"));   } catch { }
            try { Console.Error.WriteLine("\n=== SAMPLE tjentact (3 rows) ===\n" + QueryJson("SELECT * FROM tjentact LIMIT 3")); } catch { }

            return JsonConvert.SerializeObject(new { ok = true });
        }

        private void PrintReflectType(Type t)
        {
            Console.Error.WriteLine("\n=== " + t.FullName + " ===");
            foreach (var m in t.GetMethods(
                    System.Reflection.BindingFlags.Public |
                    System.Reflection.BindingFlags.Instance |
                    System.Reflection.BindingFlags.Static)
                .OrderBy(m2 => m2.Name))
            {
                var parms = string.Join(", ", m.GetParameters()
                    .Select(p => p.ParameterType.Name + " " + p.Name));
                Console.Error.WriteLine("  " + m.ReturnType.Name + " " + m.Name + "(" + parms + ")");
            }
            foreach (var p in t.GetProperties(
                    System.Reflection.BindingFlags.Public |
                    System.Reflection.BindingFlags.Instance)
                .OrderBy(p2 => p2.Name))
                Console.Error.WriteLine("  [prop] " + p.PropertyType.Name + " " + p.Name);
        }
    }

    // ── Journal entry writer ──────────────────────────────────────────────────────
    // Reads a JSON array of JournalEntryInput from stdin and posts each as a
    // General Journal entry via the SDK.  Returns JSON result to stdout.
    //
    // Entry format:
    //   { "date": "YYYY-MM-DD", "source": "BNK", "comment": "...",
    //     "lines": [{"account_id":"1060","debit":1000.0,"credit":0.0,"comment":"..."},
    //               {"account_id":"4100","debit":0.0,"credit":1000.0,"comment":"..."}] }
    //
    // Constraint: entries MUST be balanced (sum(debit) == sum(credit)) and have >= 2 lines.
    // Sage 50 comment fields: source <= 12 chars, entry/line comment <= 39 chars.

    class DataWriter
    {
        public string PostJournalEntries(string jsonInput)
        {
            if (!SDKInstanceManager.Instance.CanOpenGeneralJournal())
                return Err("Cannot open General Journal — check Sage 50 user permissions or close the journal if already open.");

            List<JournalEntryInput> entries;
            try { entries = JsonConvert.DeserializeObject<List<JournalEntryInput>>(jsonInput); }
            catch (Exception ex) { return Err("Invalid JSON input: " + ex.Message); }

            if (entries == null || entries.Count == 0)
                return JsonConvert.SerializeObject(new { posted = 0, total = 0, errors = 0, results = new object[0] });

            GeneralJournal gj = SDKInstanceManager.Instance.OpenGeneralJournal();
            var results = new List<object>();
            int postedCount = 0;

            foreach (var entry in entries)
            {
                var row = new Dictionary<string, object>();
                row["date"]    = entry.date ?? "";
                row["comment"] = entry.comment ?? "";

                try
                {
                    gj.SetJournalDate(IsoToSageDate(entry.date));
                    gj.Source  = Truncate(entry.source  ?? "BNK", 12);
                    gj.Comment = Truncate(entry.comment ?? "",     39);

                    for (int i = 0; i < entry.lines.Count; i++)
                    {
                        int n    = i + 1;
                        var line = entry.lines[i];
                        gj.SetAccount(line.account_id, n);
                        if (line.debit  > 0) gj.SetDebit (line.debit,  n);
                        if (line.credit > 0) gj.SetCredit(line.credit, n);
                        gj.SetComment(Truncate(line.comment ?? "", 39), n);
                    }

                    if (gj.Post())
                    {
                        row["posted"]     = true;
                        row["journal_no"] = gj.GetLastJournalNumber();
                        postedCount++;
                    }
                    else
                    {
                        row["posted"] = false;
                        row["error"]  = "Post() returned false — check Sage 50 for validation errors";
                        Console.Error.WriteLine("Post() false: " + entry.date + " " + entry.comment);
                        try { gj.Undo(); } catch { }
                    }
                }
                catch (Exception ex)
                {
                    row["posted"] = false;
                    row["error"]  = ex.Message;
                    Console.Error.WriteLine("Exception posting " + entry.date + ": " + ex.Message);
                    try { gj.Undo(); } catch { }
                }

                results.Add(row);
            }

            SDKInstanceManager.Instance.CloseGeneralJournal();

            return JsonConvert.SerializeObject(new
            {
                posted  = postedCount,
                total   = entries.Count,
                errors  = entries.Count - postedCount,
                results = results,
            });
        }

        // "YYYY-MM-DD" → "MM/DD/YYYY"
        private static string IsoToSageDate(string iso)
        {
            if (!string.IsNullOrEmpty(iso) && iso.Length == 10 && iso[4] == '-')
            {
                var p = iso.Split('-');
                return p[1] + "/" + p[2] + "/" + p[0];
            }
            return iso ?? "";
        }

        private static string Truncate(string s, int max)
        {
            return s.Length <= max ? s : s.Substring(0, max);
        }

        private static string Err(string msg)
        {
            Console.Error.WriteLine("ERROR: " + msg);
            return JsonConvert.SerializeObject(new { error = msg });
        }
    }

    class JournalEntryInput
    {
        [JsonProperty("date")]    public string date;
        [JsonProperty("source")]  public string source;
        [JsonProperty("comment")] public string comment;
        [JsonProperty("lines")]   public List<JournalLineInput> lines;
    }

    class JournalLineInput
    {
        [JsonProperty("account_id")] public string account_id;
        [JsonProperty("debit")]      public double debit;
        [JsonProperty("credit")]     public double credit;
        [JsonProperty("comment")]    public string comment;
    }
}
