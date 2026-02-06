# stdlib
from pathlib import Path
import logging

# third party
from dbt.adapters.factory import Adapter
from dbt.config.runtime import RuntimeConfig
from dbt.contracts.graph.manifest import Manifest

# first party
from dbt_dynamic_models.params import Param
from dbt_dynamic_models.utils import validate_escape_sequences


logger = logging.getLogger(__name__)


class DynamicModel:
    def __init__(
        self,
        config: RuntimeConfig,
        manifest: Manifest,
        adapter: Adapter,
        test_sql: bool = False,
    ):
        self.config = config
        self.manifest = manifest
        self.adapter = adapter
        self.test_sql = test_sql
        self.project_root = config.project_root
        self.model_path = Path(f"{self.project_root}/{config.model_paths[0]}")

    def _parse_manifest_for_dynamic_models(self):
        """Return only parts of the manifest that contain a dynamic models key"""
        return {
            k: v
            for k, v in self.manifest.to_dict()["files"].items()
            if v["parse_file_type"] == "schema" and "dynamic_models" in v["dfy"].keys()
            # check for project root, allow user to define
            # what projects to look in (default is all,
            # 'root' should be an option, as well as list of projects)
        }

    def _get_operation_node(self, sql, model):
        # third party
        from dbt.parser.manifest import process_node
        from dbt.parser.sql import SqlBlockParser

        block_parser = SqlBlockParser(
            project=self.config,
            manifest=self.manifest,
            root_project=self.config,
        )

        sql_node = block_parser.parse_remote(sql, model)
        process_node(self.config, self.manifest, sql_node)
        return sql_node

    def _execute_sql(self, sql, model):
        # third party
        from dbt.task.sql import SqlExecuteRunner

        node = self._get_operation_node(sql, model)
        runner = SqlExecuteRunner(self.config, self.adapter, node, 1, 1)
        return runner.safe_run(self.manifest)

    def _compile_and_run(self, sql: str, model: str):
        sql += " limit 1"
        results = self._execute_sql(sql, model)
        if len(results.timing) != 2:
            raise RuntimeError("Bad result")

    def _extract_scalars(self, context: dict) -> dict:
        """Extract scalars from single-item lists for cleaner templates.

        This allows {% if whitelist_count > 0 %} instead of {% if whitelist_count[0] > 0 %}

        Args:
            context: Dict with potentially list-wrapped values

        Returns:
            Dict with single-item lists converted to scalars
        """
        clean_context = {}
        for key, value in context.items():
            if isinstance(value, list) and len(value) == 1:
                clean_context[key] = value[0]
            else:
                clean_context[key] = value
        return clean_context

    def _flatten_context(self, context: dict) -> dict:
        """Flatten nested dict objects for backwards compatibility (deprecated).

        Note: This method is kept for backwards compatibility but is no longer needed
        since we now use Jinja for all template rendering (name, location, sql).

        Args:
            context: Dict with potentially nested dict values

        Returns:
            Same dict (no flattening needed with Jinja)
        """
        # With Jinja everywhere, no flattening needed - Jinja handles nested dicts natively
        return context

    def _render_template(self, template: str, context: dict) -> str:
        """Render a template string with Jinja.

        Supports both simple variables and nested object access:
        - {{ model }} - simple variable
        - {{ config.model }} - nested object access

        Args:
            template: Template string (for name, location, etc.)
            context: Dict of values

        Returns:
            Rendered string
        """
        from jinja2 import Template

        jinja_template = Template(template)
        return jinja_template.render(**context)

    def _escape_dbt_jinja(self, template: str) -> str:
        """Replace dbt Jinja escape sequences with temporary markers.

        Strategy: Replace the OUTER braces only, leaving inner content for Jinja to evaluate.
        - {{{{ expr }}}} → ___ESCAPE_VAR_START___ expr ___ESCAPE_VAR_END___
        - {{{# comment #}}} → ___ESCAPE_COMMENT_START___ comment ___ESCAPE_COMMENT_END___
        - {{% block %}} → ___ESCAPE_BLOCK_START___ block ___ESCAPE_BLOCK_END___

        This allows inner {{ model }} to be evaluated by Jinja, while preserving
        the outer escape markers for later conversion to dbt Jinja syntax.

        Args:
            template: SQL template with escape sequences

        Returns:
            Template with escape markers
        """
        # Replace outer braces only, leaving inner content intact
        result = template

        # Replace {{{{ with opening marker
        result = result.replace("{{{{", "___ESCAPE_VAR_START___")
        # Replace }}}} with closing marker
        result = result.replace("}}}}", "___ESCAPE_VAR_END___")

        # Replace {{{# with opening comment marker
        result = result.replace("{{{#", "___ESCAPE_COMMENT_START___")
        # Replace #}}} with closing comment marker
        result = result.replace("#}}}", "___ESCAPE_COMMENT_END___")

        # Replace {{% with opening block marker (note space after)
        result = result.replace("{{% ", "___ESCAPE_BLOCK_START___ ")
        # Replace %}} with closing block marker (note space before)
        result = result.replace(" %}}", " ___ESCAPE_BLOCK_END___")

        return result

    def _unescape_dbt_jinja(self, rendered: str) -> str:
        """Restore dbt Jinja syntax from temporary markers.

        After Jinja has evaluated inner expressions, convert markers back to dbt Jinja syntax.

        Args:
            rendered: Template rendered by Jinja with escape markers

        Returns:
            Final SQL with dbt Jinja syntax restored
        """
        result = rendered

        # Convert markers to dbt Jinja syntax
        result = result.replace("___ESCAPE_VAR_START___", "{{")
        result = result.replace("___ESCAPE_VAR_END___", "}}")

        result = result.replace("___ESCAPE_COMMENT_START___", "{#")
        result = result.replace("___ESCAPE_COMMENT_END___", "#}")

        result = result.replace("___ESCAPE_BLOCK_START___", "{%")
        result = result.replace("___ESCAPE_BLOCK_END___", "%}")

        return result

    def _render_sql_with_jinja(self, template: str, context: dict) -> str:
        """Render SQL template with Jinja, supporting escape sequences for dbt Jinja.

        Two-stage rendering process:
        1. dbtgen evaluates: {% if %}, {{ param }} (this method)
        2. dbt evaluates: {{ macro() }} in generated .sql file (later)

        Escape sequences allow outputting dbt Jinja syntax in generated files:
        - {{{{ expr }}}} → {{ expr }} (dbt variables/macros)
        - {{{# comment #}}} → {# comment #} (dbt comments)
        - {{% block %}} → {% block %} (dbt blocks, rare)

        Regular Jinja syntax is evaluated by dbtgen:
        - {{ param }} → replaced with param value
        - {% if %} → conditional logic evaluated
        - {# comment #} → removed

        Example:
            Input YAML:
                {% if whitelist_count > 0 %}
                {{{{ create_sink('{{ model }}') }}}}
                {% endif %}

            Output .sql (if whitelist_count=5, model='dim_company'):
                {{ create_sink('dim_company') }}

        Args:
            template: SQL template string
            context: Dict of param values to use as Jinja context

        Returns:
            Rendered SQL string with dbt Jinja syntax preserved
        """
        # Step 1: Extract scalars from single-item lists
        clean_context = self._extract_scalars(context)

        # print(f"Rendering SQL with context: {clean_context}")

        # Step 2: Validate escape sequences
        validate_escape_sequences(template)

        # Step 3: Replace escape sequences with placeholders
        template_with_placeholders = self._escape_dbt_jinja(template)

        # Step 4: Render with Jinja (evaluates dbtgen logic)
        from jinja2 import Template

        jinja_template = Template(template_with_placeholders)
        rendered = jinja_template.render(**clean_context)

        # Step 5: Restore dbt Jinja syntax from placeholders
        final_sql = self._unescape_dbt_jinja(rendered)

        logger.debug(f"Final rendered SQL: {final_sql[:200]}...")

        return final_sql

    def _write(self, model: str, location: str, sql: str):
        if model[-4:] != ".sql":
            model += ".sql"
        path = self.model_path / location
        path.mkdir(parents=True, exist_ok=True)
        filepath = path / model
        with filepath.open("w", encoding="utf-8") as f:
            f.writelines(sql)

    def execute(self):
        """Entrypoint to this class"""
        schema_dict = self._parse_manifest_for_dynamic_models()
        if not schema_dict:
            raise RuntimeError("No dynamic models found in your project")

        for _, dct in schema_dict.items():
            dynamic_models = dct["dfy"]["dynamic_models"]
            for dynamic_model in dynamic_models:
                # Iterate each model
                iterable = Param(
                    dynamic_model,
                    self.adapter,
                    config=self.config,
                    manifest=self.manifest,
                ).get_iterable()
                for item in iterable:
                    # Use Jinja for all templates (name, location, sql)
                    # This supports both simple vars ({{ model }}) and nested objects ({{ config.model }})
                    model = self._render_template(dynamic_model["name"], item)
                    location = self._render_template(dynamic_model["location"], item)
                    sql = self._render_sql_with_jinja(dynamic_model["sql"], item)

                    if self.test_sql:
                        self._compile_and_run(sql, model)
                    self._write(model, location, sql)
