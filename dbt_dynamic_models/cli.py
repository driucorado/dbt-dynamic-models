# stdlib
import os
from argparse import Namespace
from pathlib import Path

# third party
import dbt.tracking
import typer
import yaml
from dbt.adapters.factory import get_adapter
from dbt.config.runtime import RuntimeConfig
from dbt.flags import set_from_args
from dbt.parser.manifest import ManifestLoader

# first party
from dbt_dynamic_models.base import DynamicModel

app = typer.Typer()


@app.command()
def models(
    profiles_dir: str = typer.Option(
        None, envvar='DBT_PROFILES_DIR', help='Location of your profiles.yml file'
    ),
    project_dir: Path = typer.Option(
        Path.cwd(), help='Location of your dbt_project.yml file'
    ),
    test_sql: bool = typer.Option(
        False, help='Test the generated SQL for each model prior to saving.'
    ),
):
    if profiles_dir is not None:
        os.environ['DBT_PROFILES_DIR'] = profiles_dir

    # Create args namespace for dbt
    args = Namespace(
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        profile=None,
        target=None,
        threads=1,
        vars='{}',
        log_format='default',
        write_json=False,
        partial_parse=True,
        use_colors=True,
        printer_width=80,
        warn_error=False,
        warn_error_options={'include': [], 'exclude': []},
        debug=False,
        log_level='info',
        log_path='logs',
        version_check=True,
        fail_fast=False,
        send_anonymous_usage_stats=True,
        quiet=False,
        no_print=False,
        cache_selected_only=False,
        introspect=True,
        static_parser=True,
    )

    # Set flags from args
    set_from_args(args, None)

    # Initialize tracking
    dbt.tracking.initialize_from_flags(send_anonymous_usage_stats=False,
                                       profiles_dir=profiles_dir)

    # Get config to pass to an adapter
    config = RuntimeConfig.from_args(args)

    # Get your current adapter
    adapter = get_adapter(config)
    adapter.acquire_connection()

    # Parse manifest
    manifest = ManifestLoader.get_full_manifest(config)

    DynamicModel(config, manifest, adapter, test_sql).execute()


@app.command(
    context_settings={'allow_extra_args': True, 'ignore_unknown_options': True}
)
def profile(
    ctx: typer.Context,
    profile_name: str = typer.Option(..., '--profile-name', help='Name of profile from dbt_project.yml'),
    target_name: str = typer.Option('default', '--target-name', help='Name of the active target'),
):
    if len(ctx.args) % 2 != 0:
        raise RuntimeError('Invalid number of arguments given')

    target_config = {}
    key = None
    for i, extra_arg in enumerate(ctx.args):
        if i % 2 == 0:
            key = extra_arg.lstrip('-')
        else:
            target_config[key] = extra_arg

    profile_config = {
        profile_name: {'outputs': {target_name: target_config}, 'target': target_name}
    }
    typer.echo(yaml.dump(profile_config))


def main():
    app()
