from one_c_autoresearch.stable_diff_ids import build_diff_id_map_rows, stable_diff_id_for_row, validate_diff_id_map_rows


def test_stable_diff_id_ignores_current_row_id() -> None:
    row = {
        "diff_id": "V8D-00001",
        "source": "v8unpack-refinement",
        "change_type": "M",
        "path": "./Report\\Demo\\Report.obj.bsl",
    }
    moved = dict(row, diff_id="V8D-99999")

    assert stable_diff_id_for_row(row) == stable_diff_id_for_row(moved)


def test_diff_id_map_preserves_removed_rows_as_inactive() -> None:
    current = [
        {
            "diff_id": "V8D-00002",
            "source": "v8unpack-refinement",
            "change_type": "M",
            "path": "Report/Demo/Report.obj.bsl",
        }
    ]
    removed = {
        "stable_diff_id": "DIF-REMOVED",
        "current_diff_id": "V8D-00001",
        "source": "v8unpack-refinement",
        "change_type": "M",
        "path": "Report/Removed/Report.obj.bsl",
        "active": "true",
    }

    rows = build_diff_id_map_rows(current, previous_rows=[removed])

    inactive = [row for row in rows if row["stable_diff_id"] == "DIF-REMOVED"][0]
    assert inactive["active"] == "false"
    assert inactive["current_diff_id"] == ""
    assert validate_diff_id_map_rows(rows) == []


def test_validate_diff_id_map_rejects_active_duplicates() -> None:
    rows = [
        {"stable_diff_id": "DIF-DUP", "path": "a", "active": "true"},
        {"stable_diff_id": "DIF-DUP", "path": "b", "active": "true"},
    ]

    assert "duplicate active stable_diff_id DIF-DUP" in validate_diff_id_map_rows(rows)[0]
