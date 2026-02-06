# stdlib
import os
from argparse import Namespace
from multiprocessing import get_context
from pathlib import Path

# third party
import dbt.tracking
import typer
import yaml
from dbt.adapters.factory import get_adapter, register_adapter
from dbt.config.runtime import RuntimeConfig
from dbt.flags import set_from_args
from dbt.parser.manifest import ManifestLoader
from dbt_common.context import set_invocation_context

# first party
from dbt_dynamic_models.base import DynamicModel

app = typer.Typer()


@app.command()
def models(
    profiles_dir: str = typer.Option(
        None, envvar="DBT_PROFILES_DIR", help="Location of your profiles.yml file"
    ),
    project_dir: Path = typer.Option(
        Path.cwd(), help="Location of your dbt_project.yml file"
    ),
    profile: str = typer.Option(
        None,
        "--profile",
        help="Which profile to load. Overrides setting in dbt_project.yml.",
    ),
    target: str = typer.Option(
        None, "--target", "-t", help="Which target to load for the given profile"
    ),
    test_sql: bool = typer.Option(
        False, help="Test the generated SQL for each model prior to saving."
    ),
):
    if profiles_dir is not None:
        os.environ["DBT_PROFILES_DIR"] = profiles_dir

    # Create args namespace for dbt
    args = Namespace(
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        profile=profile,
        target=target,
        threads=1,
        vars={},
        log_format="default",
        write_json=False,
        partial_parse=True,
        use_colors=True,
        printer_width=80,
        warn_error=False,
        warn_error_options={"include": [], "exclude": []},
        debug=False,
        log_level="info",
        log_path="logs",
        version_check=True,
        fail_fast=False,
        send_anonymous_usage_stats=True,
        quiet=False,
        no_print=False,
        cache_selected_only=False,
        introspect=True,
        static_parser=True,
        REQUIRE_RESOURCE_NAMES_WITHOUT_SPACES=False,
    )

    # Set flags from args
    set_from_args(args, None)
    # Initialize DBT

    # Initialize invocation context with environment variables
    # This is required for env_var() to work in profiles.yml in dbt-core >= 1.11
    set_invocation_context(os.environ)

    # Initialize tracking
    dbt.tracking.initialize_from_flags(
        send_anonymous_usage_stats=False, profiles_dir=profiles_dir
    )
    # Get config to pass to an adapter
    config = RuntimeConfig.from_args(args)

    # Register the adapter before trying to get it
    # This is required for custom adapters like RisingWave to be available
    mp_context = get_context("spawn")
    register_adapter(config, mp_context)

    # Get your current adapter
    adapter = get_adapter(config)
    adapter.acquire_connection()

    # Parse manifest
    manifest = ManifestLoader.get_full_manifest(config)

    DynamicModel(config, manifest, adapter, test_sql).execute()


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def profile(
    ctx: typer.Context,
    profile_name: str = typer.Option(
        ..., "--profile-name", help="Name of profile from dbt_project.yml"
    ),
    target_name: str = typer.Option(
        "default", "--target-name", help="Name of the active target"
    ),
):
    if len(ctx.args) % 2 != 0:
        raise RuntimeError("Invalid number of arguments given")

    target_config = {}
    key = None
    for i, extra_arg in enumerate(ctx.args):
        if i % 2 == 0:
            key = extra_arg.lstrip("-")
        else:
            target_config[key] = extra_arg

    profile_config = {
        profile_name: {"outputs": {target_name: target_config}, "target": target_name}
    }
    typer.echo(yaml.dump(profile_config))


def main():
    app()

if __name__ == "__main__":
    main()
