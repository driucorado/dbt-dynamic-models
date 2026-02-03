# third party
import agate


def get_results_from_sql(
    adapter, sql: str, auto_begin: bool = False, fetch: bool = False
):
    _, cursor = adapter.add_query(sql, auto_begin)
    response = adapter.connections.get_response(cursor)
    if fetch:
        table = adapter.connections.get_result_from_cursor(cursor)
    else:
        table = agate.Table.from_object([])
    return response, table
