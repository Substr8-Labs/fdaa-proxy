"""
RunProof Protocol CLI Commands

Covers:
- Proof verification
- Proof graphs
- State proofs
- Policy binding
- External anchoring
"""

import click
import json
import urllib.request
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

RUNPROOF_URL = "http://localhost:8097"


def api_call(method: str, path: str, data: dict = None) -> dict:
    """Make API call to RunProof Builder."""
    url = f"{RUNPROOF_URL}{path}"
    
    if data:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method=method
        )
    else:
        req = urllib.request.Request(url, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            return {"error": json.loads(error_body).get("detail", error_body)}
        except:
            return {"error": error_body}
    except Exception as e:
        return {"error": str(e)}


@click.group()
def proof():
    """RunProof Protocol commands."""
    pass


# ============ Graph Commands ============

@proof.group()
def graph():
    """Proof graph commands (DAG composition)."""
    pass


@graph.command("link")
@click.argument("child_id")
@click.argument("parent_id")
@click.option("--relation", "-r", 
              type=click.Choice(["delegation", "retry", "branch", "approval", "dependency", "merge"]),
              default="delegation",
              help="Relationship type")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def graph_link(child_id: str, parent_id: str, relation: str, as_json: bool):
    """Link two proofs in a graph.
    
    Example:
        substr8 proof graph link run-child run-parent
        substr8 proof graph link run-retry run-original --relation retry
    """
    result = api_call("POST", "/v1/proof-graph/link", {
        "child_proof_id": child_id,
        "parent_proof_id": parent_id,
        "relation": relation
    })
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print(f"[green]✓[/green] Linked {child_id} → {parent_id} ({relation})")


@graph.command("show")
@click.argument("root_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def graph_show(root_id: str, as_json: bool):
    """Show proof graph from root.
    
    Example:
        substr8 proof graph show run-root
    """
    result = api_call("GET", f"/v1/proof-graph/{root_id}")
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print()
    console.print(f"[bold]Proof Graph: {root_id}[/bold]")
    console.print(f"  Nodes: {result.get('node_count', 0)}")
    console.print(f"  Edges: {result.get('edge_count', 0)}")
    console.print(f"  Hash:  {result.get('graph_hash', 'N/A')[:40]}...")
    console.print()
    
    if result.get("edges"):
        table = Table(title="Edges", box=box.ROUNDED)
        table.add_column("Parent", style="cyan")
        table.add_column("→")
        table.add_column("Child", style="green")
        table.add_column("Relation", style="dim")
        
        for edge in result["edges"]:
            table.add_row(
                edge["parent_proof_id"][:20] + "...",
                "→",
                edge["child_proof_id"][:20] + "...",
                edge["relation"]
            )
        
        console.print(table)


@graph.command("ancestry")
@click.argument("proof_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def graph_ancestry(proof_id: str, as_json: bool):
    """Show lineage to root.
    
    Example:
        substr8 proof graph ancestry run-child
    """
    result = api_call("GET", f"/v1/runproof/{proof_id}/ancestry")
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print()
    console.print(f"[bold]Ancestry of {proof_id}[/bold]")
    
    for i, ancestor in enumerate(result.get("ancestry", [])):
        indent = "  " * i
        console.print(f"{indent}└─ {ancestor['proof_id']} ({ancestor.get('relation', 'root')})")


@graph.command("descendants")
@click.argument("proof_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def graph_descendants(proof_id: str, as_json: bool):
    """Show all children of a proof.
    
    Example:
        substr8 proof graph descendants run-parent
    """
    result = api_call("GET", f"/v1/runproof/{proof_id}/descendants")
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print()
    console.print(f"[bold]Descendants of {proof_id}[/bold]")
    console.print(f"  Total: {result.get('count', 0)}")
    
    for desc in result.get("descendants", []):
        console.print(f"  └─ {desc['proof_id']} ({desc.get('relation', '?')})")


# ============ Anchor Commands ============

@proof.group()
def anchor():
    """External anchoring commands (blockchain/notary)."""
    pass


@anchor.command("submit")
@click.argument("proof_id")
@click.option("--type", "anchor_type", 
              type=click.Choice(["bitcoin", "ethereum", "solana", "notary"]),
              default="ethereum",
              help="Anchor type")
@click.option("--network", default="mainnet", help="Network (mainnet/testnet)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def anchor_submit(proof_id: str, anchor_type: str, network: str, as_json: bool):
    """Submit proof for external anchoring.
    
    Example:
        substr8 proof anchor submit run-abc123 --type ethereum
        substr8 proof anchor submit run-abc123 --type bitcoin --network testnet
    """
    result = api_call("POST", "/v1/anchor", {
        "proof_id": proof_id,
        "proof_type": "run",
        "anchor_type": anchor_type,
        "anchor_network": network
    })
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print(f"[green]✓[/green] Submitted for {anchor_type} anchoring")
    console.print(f"  Anchor ID: {result.get('id')}")
    console.print(f"  Status: {result.get('status')}")


@anchor.command("status")
@click.argument("anchor_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def anchor_status(anchor_id: str, as_json: bool):
    """Check anchor status.
    
    Example:
        substr8 proof anchor status anc-abc123
    """
    result = api_call("GET", f"/v1/anchor/{anchor_id}")
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    status = result.get("status", "unknown")
    status_color = {"pending": "yellow", "confirmed": "green", "failed": "red"}.get(status, "dim")
    
    console.print()
    console.print(f"[bold]Anchor {anchor_id}[/bold]")
    console.print(f"  Status: [{status_color}]{status}[/{status_color}]")
    console.print(f"  Type: {result.get('anchor_type')}")
    console.print(f"  Proof: {result.get('proof_id')}")
    
    if result.get("anchor_tx_id"):
        console.print(f"  TX: {result.get('anchor_tx_id')}")
    if result.get("anchor_block"):
        console.print(f"  Block: {result.get('anchor_block')}")


@anchor.command("confirm")
@click.argument("anchor_id")
@click.option("--tx", "tx_id", required=True, help="Transaction ID")
@click.option("--block", help="Block number/hash")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def anchor_confirm(anchor_id: str, tx_id: str, block: str, as_json: bool):
    """Confirm anchor with transaction details.
    
    Example:
        substr8 proof anchor confirm anc-abc123 --tx 0x123... --block 18500000
    """
    data = {"anchor_tx_id": tx_id}
    if block:
        data["anchor_block"] = block
    
    result = api_call("POST", f"/v1/anchor/{anchor_id}/confirm", data)
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print(f"[green]✓[/green] Anchor confirmed")


@anchor.command("pending")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def anchor_pending(as_json: bool):
    """List pending anchors.
    
    Example:
        substr8 proof anchor pending
    """
    result = api_call("GET", "/v1/anchors/pending")
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    pending = result.get("pending", [])
    
    if not pending:
        console.print("[dim]No pending anchors[/dim]")
        return
    
    table = Table(title=f"Pending Anchors ({len(pending)})", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("Proof")
    table.add_column("Type")
    table.add_column("Created")
    
    for a in pending:
        table.add_row(
            a["id"],
            a["proof_id"][:20] + "...",
            a["anchor_type"],
            a.get("created_at", "")[:19]
        )
    
    console.print(table)


# ============ State Commands ============

@proof.group()
def state():
    """State proof commands."""
    pass


@state.command("create")
@click.argument("run_id")
@click.option("--type", "state_type",
              type=click.Choice(["memory", "session", "workflow", "agent"]),
              required=True,
              help="State type")
@click.option("--prev", "prev_hash", help="Previous state hash")
@click.option("--next", "next_hash", required=True, help="Next state hash")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def state_create(run_id: str, state_type: str, prev_hash: str, next_hash: str, as_json: bool):
    """Create a state proof.
    
    Example:
        substr8 proof state create run-abc123 --type memory --next sha256:newstate
    """
    result = api_call("POST", "/v1/state-proof", {
        "run_id": run_id,
        "state_type": state_type,
        "prev_state_hash": prev_hash,
        "next_state_hash": next_hash
    })
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print(f"[green]✓[/green] State proof created")
    console.print(f"  ID: {result.get('id')}")


@state.command("chain")
@click.argument("state_type")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def state_chain(state_type: str, as_json: bool):
    """Verify state chain integrity.
    
    Example:
        substr8 proof state chain memory
    """
    result = api_call("GET", f"/v1/state-chain/{state_type}/verify")
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    valid = result.get("chain_valid", False)
    
    if valid:
        console.print(f"[green]✓[/green] State chain valid ({result.get('entries', 0)} entries)")
    else:
        console.print(f"[red]✗[/red] State chain broken")
        if result.get("errors"):
            for err in result["errors"]:
                console.print(f"  • {err}")


# ============ Policy Commands ============

@proof.group()
def policy():
    """Policy binding commands."""
    pass


@policy.command("bind")
@click.argument("run_id")
@click.option("--type", "policy_type",
              type=click.Choice(["acc_token", "governance_rule", "capability_grant", "constraint"]),
              required=True,
              help="Policy type")
@click.option("--id", "policy_id", required=True, help="Policy ID")
@click.option("--hash", "policy_hash", required=True, help="Policy hash")
@click.option("--status", "binding_status",
              type=click.Choice(["applied", "violated", "bypassed"]),
              default="applied",
              help="Binding status")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def policy_bind(run_id: str, policy_type: str, policy_id: str, policy_hash: str, 
                binding_status: str, as_json: bool):
    """Bind a policy to a run.
    
    Example:
        substr8 proof policy bind run-abc123 --type acc_token --id acc_xyz --hash sha256:...
    """
    result = api_call("POST", "/v1/policy-binding", {
        "run_id": run_id,
        "policy_type": policy_type,
        "policy_id": policy_id,
        "policy_hash": policy_hash,
        "binding_status": binding_status
    })
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    console.print(f"[green]✓[/green] Policy bound to run")


@policy.command("list")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def policy_list(run_id: str, as_json: bool):
    """List policies for a run.
    
    Example:
        substr8 proof policy list run-abc123
    """
    result = api_call("GET", f"/v1/runproof/{run_id}/policies")
    
    if as_json:
        console.print_json(json.dumps(result))
        return
    
    if "error" in result:
        console.print(f"[red]✗ Error:[/red] {result['error']}")
        raise SystemExit(1)
    
    policies = result.get("policies", [])
    
    if not policies:
        console.print("[dim]No policies bound[/dim]")
        return
    
    table = Table(title=f"Policies for {run_id}", box=box.ROUNDED)
    table.add_column("Type", style="cyan")
    table.add_column("Policy ID")
    table.add_column("Status")
    
    for p in policies:
        status = p.get("binding_status", "?")
        status_style = {"applied": "green", "violated": "red", "bypassed": "yellow"}.get(status, "dim")
        table.add_row(
            p["policy_type"],
            p["policy_id"],
            f"[{status_style}]{status}[/{status_style}]"
        )
    
    console.print(table)
