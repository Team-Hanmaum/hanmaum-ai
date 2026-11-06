"""Temporarily allow only this runner's IPv4 /32; never replace the firewall."""

import argparse
import ipaddress
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def aws(region, action, **arguments):
    result = subprocess.run(
        [
            "aws",
            "lightsail",
            action,
            "--region",
            region,
            "--output",
            "json",
            "--no-cli-pager",
            "--cli-input-json",
            json.dumps(arguments),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError(f"AWS Lightsail {action} failed; check the role permissions and region")
    return json.loads(result.stdout)


def ssh_rules(region, instance):
    states = aws(region, "get-instance-port-states", instanceName=instance)["portStates"]
    return [
        rule
        for rule in states
        if rule.get("state") == "open"
        and (
            rule["protocol"] == "all"
            or (rule["protocol"] == "tcp" and rule["fromPort"] <= 22 <= rule["toPort"])
        )
    ]


def port_info(cidr):
    network = ipaddress.ip_network(cidr)
    if network.version != 4 or network.prefixlen != 32:
        raise ValueError("Refusing a firewall change other than one IPv4 /32")
    return {"fromPort": 22, "toPort": 22, "protocol": "tcp", "cidrs": [str(network)]}


def has_exact_rule(rules, cidr):
    return any(
        str(ipaddress.ip_network(value, strict=False)) == cidr
        for rule in rules
        for value in rule.get("cidrs", [])
    )


def wait_for_rule(region, instance, cidr, present):
    for _ in range(20):
        if has_exact_rule(ssh_rules(region, instance), cidr) == present:
            return
        time.sleep(3)
    raise RuntimeError("Timed out verifying the temporary SSH rule; inspect Lightsail Networking")


def open_rule(state_file, region, instance, runner_ip):
    address = ipaddress.ip_address(runner_ip.strip())
    if address.version != 4 or not address.is_global:
        raise ValueError("A public IPv4 runner address is required")
    cidr = str(ipaddress.ip_network(f"{address}/32"))
    rules = ssh_rules(region, instance)
    for rule in rules:
        for value in rule.get("cidrs", []) + rule.get("ipv6Cidrs", []):
            if ipaddress.ip_network(value, strict=False).prefixlen == 0:
                raise ValueError("SSH is open to everyone; restrict it before enabling deployment")
    owned = not has_exact_rule(rules, cidr)
    # Save ownership BEFORE the API call: cleanup can run even if the response is lost.
    with state_file.open("x", encoding="utf-8") as stream:
        json.dump({"region": region, "instance": instance, "cidr": cidr, "owned": owned}, stream)
    if owned:
        aws(region, "open-instance-public-ports", instanceName=instance, portInfo=port_info(cidr))
        wait_for_rule(region, instance, cidr, True)
    print(f"Deployment SSH source: {cidr}; temporary rule added: {owned}")


def close_rule(state_file):
    if not state_file.exists():
        return
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if state["owned"]:
        aws(
            state["region"],
            "close-instance-public-ports",
            instanceName=state["instance"],
            portInfo=port_info(state["cidr"]),
        )
        wait_for_rule(state["region"], state["instance"], state["cidr"], False)
        print(f"Removed deployment SSH source: {state['cidr']}")
    state_file.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("open", "close"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--region")
    parser.add_argument("--instance")
    args = parser.parse_args()
    try:
        if args.command == "open":
            if not args.region or not args.instance:
                raise ValueError("--region and --instance are required")
            with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=15) as response:
                runner_ip = response.read(128).decode("ascii")
            open_rule(args.state, args.region, args.instance, runner_ip)
        else:
            close_rule(args.state)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Firewall operation failed: {error}\n")
