# stdlib
import logging
from collections import namedtuple
from itertools import product, starmap
from typing import Dict

# third party
from dbt.adapters.factory import Adapter

# first party
from dbt_dynamic_models.utils import get_results_from_sql

logger = logging.getLogger(__name__)


class Param:
    def __init__(
        self,
        dynamic_model: Dict,
        adapter: Adapter,
        config=None,
        manifest=None,
    ):
        self.dynamic_model = dynamic_model
        self.adapter = adapter
        self.config = config
        self.manifest = manifest
        self.strategy = self.dynamic_model.get("strategy", "product")

    STRATEGY_FUNCTION = {
        "product": product,
        "row": zip,
    }

    def _render_jinja_query(self, query: str) -> str:
        """Render Jinja templates in param queries using dbt context.

        This allows queries to use dbt functions like:
        - {{ ref('model_name') }}
        - {{ env_var('VAR_NAME', 'default') }}
        - {{ source('schema', 'table') }}
        - {{ var('variable_name') }}
        """
        # If config and manifest aren't provided, return query as-is for backwards compatibility
        if not self.config or not self.manifest:
            logger.debug(
                "Config/manifest not provided, skipping Jinja rendering for query"
            )
            return query

        # Check if query contains Jinja syntax
        if "{{" not in query and "{%" not in query:
            return query

        try:
            from dbt_common.clients.jinja import get_template, render_template
            from dbt.context.providers import generate_runtime_model_context

            # We need a minimal node to generate context
            # Use an existing SQL node from manifest if available, or create a minimal one
            sql_nodes = [
                node
                for node in self.manifest.nodes.values()
                if hasattr(node, "resource_type") and node.resource_type == "model"
            ]

            if sql_nodes:
                # Use first available model node as context base
                context_node = sql_nodes[0]
            else:
                # Fallback: create a minimal context without a specific node
                # This is less ideal but works for basic functions
                from dbt.context.providers import generate_runtime_macro_context

                context = generate_runtime_macro_context(
                    macro=None,
                    config=self.config,
                    manifest=self.manifest,
                    package_name=self.config.project_name,
                )
                template = get_template(query, ctx=context)
                return render_template(template, context)

            # Generate full runtime context with ref, source, env_var, etc.
            context = generate_runtime_model_context(
                model=context_node,
                config=self.config,
                manifest=self.manifest,
            )

            # Render the query with dbt context
            template = get_template(query, ctx=context)
            rendered = render_template(template, context, node=context_node)

            logger.debug(f"Rendered query: {rendered}")
            return rendered

        except Exception as e:
            logger.warning(f"Failed to render Jinja in query: {e}")
            logger.warning(f"Falling back to raw query: {query}")
            return query

    def _validate_object_values(self, param_name: str, values: list):
        """Validate that object values have consistent keys across all items.

        Args:
            param_name: Name of the parameter for error messages
            values: List of dict objects to validate

        Raises:
            ValueError: If objects have inconsistent keys
        """
        if not values or not isinstance(values[0], dict):
            return  # Not object values, skip validation

        # Get keys from first object as reference
        reference_keys = set(values[0].keys())

        # Check all subsequent objects have the same keys
        for i, obj in enumerate(values[1:], start=1):
            if not isinstance(obj, dict):
                raise ValueError(
                    f"Parameter '{param_name}' has mixed types: "
                    f"item 0 is dict, but item {i} is {type(obj).__name__}"
                )

            obj_keys = set(obj.keys())

            # Check for missing keys
            missing_keys = reference_keys - obj_keys
            if missing_keys:
                logger.warning(
                    f"Parameter '{param_name}' item {i} is missing keys: {missing_keys}. "
                    f"Expected keys: {reference_keys}"
                )

            # Check for extra keys
            extra_keys = obj_keys - reference_keys
            if extra_keys:
                logger.warning(
                    f"Parameter '{param_name}' item {i} has extra keys: {extra_keys}. "
                    f"Expected keys: {reference_keys}"
                )

    def _format_params(self):
        params = {}
        for param in self.dynamic_model["params"]:
            if "values" in param:
                values = param["values"]

                # Validate object values have consistent keys
                if values and isinstance(values[0], dict):
                    self._validate_object_values(param["name"], values)

                params[param["name"]] = values
            elif "query" in param:
                # Render Jinja templates in the query (ref, env_var, source, etc.)
                rendered_query = self._render_jinja_query(param["query"])
                response, table = get_results_from_sql(
                    self.adapter, rendered_query, fetch=True
                )

                # Log response for debugging
                logger.debug(
                    f"Query response code: {response.code}, message: {response._message}"
                )

                # Check for actual error codes instead of expecting specific success codes
                # Different adapters return different success codes (SELECT, INSERT, SUCCESS, OK, etc.)
                if response.code in ["ERROR", "FAIL", "FATAL"]:
                    raise ValueError(f"Query failed: {response}")

                # Validate that we got usable data back
                if table is None or len(table.columns) == 0:
                    raise ValueError(f"Query returned no data. Response: {response}")

                params.update(
                    **{col.name.lower(): col.values() for col in table.columns}
                )
            else:
                raise NotImplementedError
        return params

    def product_check(self, params):
        pass

    def row_check(self, params: Dict):
        iterables = [v for k, v in params.items()]
        length = len(iterables[0])
        if any(len(ls) != length for ls in iterables):
            logger.warning("The parameters are of unequal lengths!")

    def get_iterable(self):
        params = self._format_params()

        # Check params
        strategy_check = f"{self.strategy}_check"
        getattr(self, strategy_check)(params)

        # Hard error for undefined strategies
        func = self.STRATEGY_FUNCTION[self.strategy]

        # Get appropriate iterable based on strategy
        Iterable = namedtuple("Iterable", params.keys())
        named_tuples = starmap(Iterable, func(*params.values()))
        return [named_tuple._asdict() for named_tuple in named_tuples]
