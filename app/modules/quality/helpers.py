# helpers.py
import sqlite3
from app.core.database import get_db_connection


def get_all_quality_definitions():
    """
    Simple helper to fetch all definitions (no joins).
    """
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            DefinitionID,
            Code,
            Name,
            Category,
            DefinitionType,
            IsCurrentVersion,
            IsActive,
            SequenceNumber
        FROM t_QualityOperationMaster
        ORDER BY SequenceNumber IS NULL, SequenceNumber, Code;
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_definition_with_latest_assignment(definition_id: int):
    """
    Fetch a single definition and its latest assignment (if any).
    """
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM t_QualityOperationMaster WHERE DefinitionID = ?",
        (definition_id,),
    )
    definition = cur.fetchone()
    if not definition:
        conn.close()
        return None

    cur.execute(
        """
        SELECT *
        FROM t_QualityOperationAssignment
        WHERE DefinitionID = ?
        ORDER BY VersionNumber DESC, AssignmentID DESC
        LIMIT 1;
        """,
        (definition_id,),
    )
    assignment = cur.fetchone()
    conn.close()

    return {
        "definition": dict(definition),
        "assignment": dict(assignment) if assignment else None,
    }


def ensure_quality_tables_exist():
    # Placeholder so the app can start
    pass

def start_quality_plan_workflow(plan_id, user_id):
    # same logic as in api.py – you can move it here
    pass