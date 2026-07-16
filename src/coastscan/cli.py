"""Typer command-line interface."""

import json

import typer
from rich.console import Console

from coastscan.acquire import acquire_region_data
from coastscan.exceptions import CoastScanError
from coastscan.pipeline.build_bathymetry import build_bathymetry, inspect_bathymetry
from coastscan.pipeline.build_region import build_region, inspect_region_inputs

app = typer.Typer(no_args_is_help=True, help="CoastScan coastline morphology pipeline")
console = Console()


@app.command("acquire-region-data")
def acquire_region_data_command(
    region: str = typer.Option(..., "--region", help="Region ID with an acquisition plan"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Download and validate only the authoritative files planned for a region."""
    try:
        manifest = acquire_region_data(region)
        console.print_json(json.dumps(manifest.model_dump(mode="json")))
    except CoastScanError as exc:
        console.print(f"[red]{exc}[/red]")
        if verbose:
            raise
        raise typer.Exit(code=2) from None


@app.command("inspect-inputs")
def inspect_inputs_command(
    region: str = typer.Option(..., "--region", help="Region ID or YAML path"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Validate configured files and source metadata."""
    try:
        result = inspect_region_inputs(region)
        console.print_json(json.dumps(result))
    except CoastScanError as exc:
        console.print(f"[red]{exc}[/red]")
        if verbose:
            raise
        raise typer.Exit(code=2) from None


@app.command("build-region")
def build_region_command(
    region: str = typer.Option(..., "--region", help="Region ID or YAML path"),
    force: bool = typer.Option(False, "--force", help="Rebuild cached terrain rasters"),
    write_samples: bool = typer.Option(False, "--write-samples"),
    skip_qa_map: bool = typer.Option(False, "--skip-qa-map"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Run the complete Phase 1 regional pipeline."""
    try:
        manifest = build_region(
            region,
            force=force,
            write_samples=write_samples,
            skip_qa_map=skip_qa_map,
            verbose=verbose,
        )
        console.print(f"[green]Build complete[/green]: {manifest.run_id}")
    except CoastScanError as exc:
        console.print(f"[red]{exc}[/red]")
        if verbose:
            raise
        raise typer.Exit(code=2) from None


@app.command("inspect-bathymetry")
def inspect_bathymetry_command(
    region: str = typer.Option(..., "--region", help="Region ID or YAML path"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Inspect Phase 2 source, resolution, variables, coverage and upstream contracts."""
    try:
        result = inspect_bathymetry(region)
        console.print_json(json.dumps(result))
    except CoastScanError as exc:
        console.print(f"[red]{exc}[/red]")
        if verbose:
            raise
        raise typer.Exit(code=2) from None


@app.command("build-bathymetry")
def build_bathymetry_command(
    region: str = typer.Option(..., "--region", help="Region ID or YAML path"),
    force: bool = typer.Option(False, "--force", help="Rebuild cached bathymetry rasters"),
    write_samples: bool = typer.Option(False, "--write-samples"),
    skip_qa_map: bool = typer.Option(False, "--skip-qa-map"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Run the independent Phase 2 regional bathymetry pipeline."""
    try:
        manifest = build_bathymetry(
            region,
            force=force,
            write_samples=write_samples,
            skip_qa_map=skip_qa_map,
            verbose=verbose,
        )
        console.print(f"[green]Bathymetry build complete[/green]: {manifest.run_id}")
    except CoastScanError as exc:
        console.print(f"[red]{exc}[/red]")
        if verbose:
            raise
        raise typer.Exit(code=2) from None


if __name__ == "__main__":
    app()
