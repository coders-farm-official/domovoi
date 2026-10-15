// The Hyper-V firewall rules Domovoi owns (PLAN 4.10). WSL's VMs sit
// behind the Hyper-V firewall, whose default inbound action is Block; each
// rule allows one Domovoi port range in, for WSL's VM creator, from the
// household's subnet only. New-NetFirewallHyperVRule takes no LocalSubnet
// keyword, so the subnet is spelled out ([verified] PLAN 4.10; MS Learn
// New-NetFirewallHyperVRule). Never flip DefaultInboundAction.
//
// The rules match WSL's VM creator, so they apply to every WSL VM on this
// PC, not only Domovoi's: a household distro in mirrored mode listening on
// one of these ports would be reachable from the LAN too.

using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public sealed class FirewallRule
    {
        public string Name;
        public string DisplayName;
        public string LocalPorts;
        public string RemoteAddress;
    }

    public static class Firewall
    {
        // WSL's VM creator id (BRIEF.md; Get-NetFirewallHyperVVMCreator names it "WSL").
        public const string WslVmCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}";
        public const string RulePrefix = "Domovoi-";

        static readonly Regex SafeName = new Regex(@"^Domovoi-[A-Za-z]+-[0-9]+(-[0-9]+)?\z");
        static readonly Regex SafePorts = new Regex(@"^[0-9]{1,5}(-[0-9]{1,5})?\z");

        public static List<FirewallRule> Plan(string subnetCidr)
        {
            if (!Lan.IsCidr(subnetCidr)) throw new ArgumentException("not a subnet: " + subnetCidr);
            string net = subnetCidr.Substring(0, subnetCidr.IndexOf('/'));
            if (!Lan.IsPrivateIPv4(net)) throw new ArgumentException("not a private (home network) subnet: " + subnetCidr);
            var rules = new List<FirewallRule>();
            foreach (var s in DomovoiPorts.All)
            {
                if (!s.Lan) continue;
                var r = new FirewallRule();
                r.Name = s.RuleName;
                r.DisplayName = "Domovoi: " + s.What + " (TCP " + s.Ports + ", from " + subnetCidr + ")";
                r.LocalPorts = s.Ports;
                r.RemoteAddress = subnetCidr;
                rules.Add(r);
            }
            return rules;
        }

        static string Q(string s)
        {
            // PowerShell single-quoted string: only ' needs doubling.
            return "'" + s.Replace("'", "''") + "'";
        }

        // A PowerShell 5.1 script that removes every Domovoi- rule for WSL's
        // VM creator and adds these. Idempotent: run it again with the same
        // or a new subnet and the result is the same set of rules.
        public static string AddScript(List<FirewallRule> rules)
        {
            var sb = new StringBuilder();
            sb.Append("$ErrorActionPreference = 'Stop'\n");
            sb.Append(RemoveBody());
            foreach (var r in rules)
            {
                if (!SafeName.IsMatch(r.Name) || !SafePorts.IsMatch(r.LocalPorts) || !Lan.IsCidr(r.RemoteAddress))
                    throw new ArgumentException("refusing an unexpected rule: " + r.Name);
                sb.Append("New-NetFirewallHyperVRule -Name ").Append(Q(r.Name))
                  .Append(" -DisplayName ").Append(Q(r.DisplayName))
                  .Append(" -Direction Inbound -VMCreatorId ").Append(Q(WslVmCreatorId))
                  .Append(" -Protocol TCP -LocalPorts ").Append(Q(r.LocalPorts))
                  .Append(" -RemoteAddresses ").Append(Q(r.RemoteAddress))
                  .Append(" -Action Allow -Enabled True | Out-Null\n");
                sb.Append("Write-Output ").Append(Q("added " + r.Name)).Append("\n");
            }
            return sb.ToString();
        }

        public static string RemoveScript()
        {
            return "$ErrorActionPreference = 'Stop'\n" + RemoveBody();
        }

        static string RemoveBody()
        {
            // Get- then Remove- by exact name, so nothing but our own rules
            // (Name starts with Domovoi-) can go, whatever the cmdlet does
            // with wildcards.
            // VMCreatorId reads back as {40E0AC32-...}; braces dropped on
            // both sides so either spelling matches (-eq ignores case).
            return "$domovoiRules = @(Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "
                + Q(RulePrefix + "*") + " -and ([string]$_.VMCreatorId -replace '[{}]', '') -eq "
                + Q(WslVmCreatorId.Trim('{', '}')) + " })\n"
                + "foreach ($r in $domovoiRules) { Remove-NetFirewallHyperVRule -Name $r.Name; Write-Output ('removed ' + $r.Name) }\n";
        }

        public static string ListScript()
        {
            return "$ErrorActionPreference = 'Stop'\n"
                + "Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "
                + Q(RulePrefix + "*") + " } | ForEach-Object { Write-Output ('rule ' + $_.Name) }\n";
        }
    }
}
