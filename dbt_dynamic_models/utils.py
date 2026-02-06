# stdlib
import logging
import re

# third party
import agate

logger = logging.getLogger(__name__)


def get_results_from_sql(
    adapter, sql: str, auto_begin: bool = False, fetch: bool = False
):
    _, cursor = adapter.add_query(sql, auto_begin)
    response = adapter.connections.get_response(cursor)
    if fetch:
        table = adapter.connections.get_result_from_cursor(cursor, limit=100)
    else:
        table = agate.Table.from_object([])
    return response, table


def validate_escape_sequences(template: str):
    """Validate that escape sequences are properly matched.

    Checks for:
    - Unmatched {{{{ without }}}}
    - Unmatched {{{# without #}}}
    - Unmatched {{% without %}}
    - Common mistakes like {{{{{ (too many braces)

    Args:
        template: SQL template string

    Raises:
        ValueError: If escape sequences are malformed
    """
    # Check for unmatched {{{{
    opening_vars = len(re.findall(r"\{\{\{\{", template))
    closing_vars = len(re.findall(r"\}\}\}\}", template))
    if opening_vars != closing_vars:
        raise ValueError(
            f"Unmatched escape sequences: found {opening_vars} '{{{{{{{{' but "
            f"{closing_vars} '}}}}}}}}'. Each '{{{{{{{{' must have a matching '}}}}}}}}'"
        )

    # Check for unmatched {{{#
    opening_comments = len(re.findall(r"\{\{\{#", template))
    closing_comments = len(re.findall(r"#\}\}\}", template))
    if opening_comments != closing_comments:
        raise ValueError(
            f"Unmatched escape sequences: found {opening_comments} '{{{{{{#' but "
            f"{closing_comments} '#}}}}}}'. Each '{{{{{{#' must have a matching '#}}}}}}'"
        )

    # Check for unmatched {{% (with space)
    opening_blocks = len(re.findall(r"\{\{% ", template))
    closing_blocks = len(re.findall(r" %\}\}", template))
    if opening_blocks != closing_blocks:
        raise ValueError(
            f"Unmatched escape sequences: found {opening_blocks} '{{{{%' but "
            f"{closing_blocks} '%}}}}'. Each '{{{{%' must have a matching '%}}}}'"
        )

    # Check for common mistakes (too many braces)
    if "{{{{{" in template or "}}}}}" in template:
        logger.warning(
            "Found 5 or more consecutive braces ('{{{{{' or '}}}}}'). "
            "Did you mean 4 braces for escape sequences? ({{{{ or }}}})"
        )

    # Check for nested escape sequences (unsupported)
    if "{{{{{{" in template:
        raise ValueError(
            "Nested escape sequences (6+ consecutive braces) are not supported. "
            "Use temporary variables or separate the logic."
        )
